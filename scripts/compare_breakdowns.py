"""Compare recovery from breakdowns: breakdown levels x recovery policies x dispatch policies (P3).

For every configuration and seed, one scenario is generated on the large map, with traffic.
Every dispatch policy runs on it:
- without breakdowns (the M4, M5, P1 setting without delays);
- at each breakdown level, under each recovery policy (D28):
  - `wait`: robots behind a broken robot wait (ordered execution);
  - `retime`: every plan is re-timed at each breakdown, so new jobs are planned knowing about it;
  - `replan`: re-timed, then every delayed robot is replanned with SIPP.

A breakdown level gives the share of corridor entries in which a robot breaks down, and the
range of repair times. Each robot draws its breakdowns from its own stream, seeded by the
scenario seed, so every recovery policy faces the same breakdowns as far as the robots' paths
allow.

Reported as means over seeds: waiting time, completion time (mean, P95, maximum), conflicts per
run (checked to be zero), breakdowns and replans per run. Then paired differences, with 95% t
confidence intervals:
- **cost of breakdowns**: under `wait`, this breakdown level minus no breakdowns, for the same
  dispatch policy;
- **vs wait**: `retime` or `replan` minus `wait`, at the same breakdown level, for the mean and
  for the maximum completion time;
- **vs greedy**: the mean completion time of the policy minus greedy, under the same breakdowns
  and recovery.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_breakdowns.py [--seeds 20] [--jobs 8]
"""

import argparse
import functools
import os
import platform
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import RunKpis, compute_kpis, paired_difference
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.traffic import NO_DELAYS, DelayModel, Recovery
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse

MAP = "large"
CONFIGS = [(10, 200.0), (10, 300.0)]  # (robots, orders per hour)
POLICY_NAMES = ["greedy", "cpsat-fair"]
NONE = "none"
LEVELS = {  # name -> breakdown model: repairs of 30 s to 3 min (0.1 s ticks)
    NONE: NO_DELAYS,
    "0.1% of entries": DelayModel(0.001, min_ticks=300, max_ticks=1800),
    "0.5% of entries": DelayModel(0.005, min_ticks=300, max_ticks=1800),
}
WAIT = Recovery.WAIT

Key = tuple[str, str, Recovery]  # dispatch policy, breakdown level, recovery


@dataclass(frozen=True, slots=True)
class Run:
    kpis: RunKpis
    breakdowns: int
    replans: int


def git_commit() -> str:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return output.stdout.strip()


@functools.cache
def _graph() -> WarehouseGraph:
    return generate_grid_warehouse(PRESETS[MAP])


def _recoveries(level: str) -> list[Recovery]:
    return [WAIT] if level == NONE else list(Recovery)


def run_seed(robots: int, rate: float, duration_s: float, seed: int) -> dict[Key, Run]:
    """Every dispatch policy, breakdown level and recovery on one scenario. Runs in a worker
    process."""
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
    scenario = generate_scenario(_graph(), spec, seed=seed)
    results: dict[Key, Run] = {}
    for name in POLICY_NAMES:
        for level, model in LEVELS.items():
            for recovery in _recoveries(level):
                result = simulate(
                    scenario,
                    POLICIES[name](),
                    traffic=True,
                    breakdowns=model,
                    breakdown_seed=seed,
                    recovery=recovery,
                )
                results[(name, level, recovery)] = Run(
                    compute_kpis(result), result.breakdowns, result.replans
                )
    return results


def _mean(values: list[float]) -> float:
    return statistics.fmean(values)


def rows(robots: int, rate: float, results: list[dict[Key, Run]]) -> list[str]:
    def completion(name: str, level: str, recovery: Recovery) -> list[float]:
        return [r[(name, level, recovery)].kpis.completion_mean_s for r in results]

    def worst(name: str, level: str, recovery: Recovery) -> list[float]:
        return [r[(name, level, recovery)].kpis.completion_max_s for r in results]

    lines = []
    for name in POLICY_NAMES:
        for level in LEVELS:
            for recovery in _recoveries(level):
                runs = [r[(name, level, recovery)] for r in results]
                kpis = [run.kpis for run in runs]
                cost = (
                    paired_difference(
                        completion(name, level, WAIT), completion(name, NONE, WAIT)
                    ).format()
                    if level != NONE and recovery is WAIT
                    else ""
                )
                vs_wait = vs_wait_max = ""
                if recovery is not WAIT:
                    vs_wait = paired_difference(
                        completion(name, level, recovery), completion(name, level, WAIT)
                    ).format()
                    vs_wait_max = paired_difference(
                        worst(name, level, recovery), worst(name, level, WAIT)
                    ).format()
                vs_greedy = (
                    "baseline"
                    if name == "greedy"
                    else paired_difference(
                        completion(name, level, recovery), completion("greedy", level, recovery)
                    ).format()
                )
                label = "-" if level == NONE else recovery.value
                lines.append(
                    f"| {robots} | {rate:g} | {name} | {level} | {label} "
                    f"| {_mean([float(run.breakdowns) for run in runs]):.1f} "
                    f"| {_mean([float(run.replans) for run in runs]):.1f} "
                    f"| {_mean([k.waiting_mean_s for k in kpis]):.1f} "
                    f"| {_mean([k.completion_mean_s for k in kpis]):.1f} "
                    f"| {_mean([k.completion_p95_s for k in kpis]):.1f} "
                    f"| {_mean([k.completion_max_s for k in kpis]):.1f} "
                    f"| {_mean([float(k.conflicts) for k in kpis]):.1f} | {cost} | {vs_wait} "
                    f"| {vs_wait_max} | {vs_greedy} |"
                )
    return lines


def run(seeds: int, duration_s: float, jobs: int) -> int:
    print(f"Breakdown comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"ortools {version('ortools')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()}), {jobs} worker processes"
    )
    print(
        f"Settings: map {MAP}, orders released for {duration_s:g} s, seeds 0-{seeds - 1}, "
        f"1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading, traffic on, no delays; "
        f"a breakdown takes 30 to 180 s to repair"
    )

    started = time.perf_counter()
    tasks = [(robots, rate, seed) for robots, rate in CONFIGS for seed in range(seeds)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {t: pool.submit(run_seed, t[0], t[1], duration_s, t[2]) for t in tasks}
        results = {task: future.result() for task, future in futures.items()}
    elapsed = time.perf_counter() - started

    print()
    print(
        "| Robots | Orders/h | Policy | Breakdowns | Recovery | Breakdowns per run "
        "| Replans per run | Waiting s | Completion s | Completion P95 s | Completion max s "
        "| Conflicts per run | Cost of breakdowns (95% CI) | vs wait (95% CI) "
        "| Max: vs wait (95% CI) | vs greedy (95% CI) |"
    )
    print("|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|")
    for robots, rate in CONFIGS:
        config_results = [results[(robots, rate, seed)] for seed in range(seeds)]
        for line in rows(robots, rate, config_results):
            print(line)

    runs = [run for r in results.values() for run in r.values()]
    undelivered = sum(run.kpis.undelivered for run in runs)
    conflicts = sum(run.kpis.conflicts for run in runs)
    print()
    print(f"{conflicts} conflicts in {len(runs)} runs.")
    print(f"{len(runs)} runs in {elapsed:.1f} s.")
    if undelivered or conflicts:
        print(f"WARNING: {undelivered} undelivered orders, {conflicts} conflicts.")
        return 1
    print("Every order was delivered in every run.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--duration-s", type=float, default=3600.0)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    sys.exit(run(args.seeds, args.duration_s, args.jobs))


if __name__ == "__main__":
    main()
