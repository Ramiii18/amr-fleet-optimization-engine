"""Compare execution under delays: delay levels x dispatch policies x execution policies (P1).

For every configuration and seed, one scenario is generated on the large map, with traffic.
Every dispatch policy runs on it:
- without delays (the M4 and M5 setting);
- at each delay level, with ordered execution (D25): every robot keeps the planned order of
  robots at every node and corridor;
- at each delay level, with naive execution: every robot keeps its own schedule, so conflicts are
  possible. Its KPIs come from runs with collisions, so only its conflict count is reported.

A delay level gives the share of edge traversals that are delayed and the range of the extra
time. Each robot draws its delays from its own stream, seeded by the scenario seed (D25).

Reported for ordered execution, as means over seeds: utilization, waiting time, completion time
(mean, P95, maximum) and conflicts per run (checked to be zero). Then paired differences of the
mean completion time, with 95% t confidence intervals:
- **cost of delays**: this delay level minus no delays, for the same dispatch policy;
- **vs greedy**: the policy minus greedy, at the same delay level.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_delays.py [--seeds 20] [--jobs 8]
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
from datetime import date
from importlib.metadata import version
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import RunKpis, compute_kpis, paired_difference
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.traffic import NO_DELAYS, DelayModel, ExecutionPolicy
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse

MAP = "large"
CONFIGS = [(10, 200.0), (10, 300.0)]  # (robots, orders per hour)
POLICY_NAMES = ["greedy", "cpsat-fair"]
LEVELS = {  # name -> delay model: 1 to 5 s of extra time on a share of edges (0.1 s ticks)
    "none": NO_DELAYS,
    "5% of edges": DelayModel(0.05, min_ticks=10, max_ticks=50),
    "20% of edges": DelayModel(0.20, min_ticks=10, max_ticks=50),
}
ORDERED, NAIVE = ExecutionPolicy.ORDERED, ExecutionPolicy.NAIVE

Key = tuple[str, str, ExecutionPolicy]  # policy, delay level, execution


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


def run_seed(robots: int, rate: float, duration_s: float, seed: int) -> dict[Key, RunKpis]:
    """Every policy, delay level and execution on one scenario. Runs in a worker process."""
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
    scenario = generate_scenario(_graph(), spec, seed=seed)
    results: dict[Key, RunKpis] = {}
    for name in POLICY_NAMES:
        for level, model in LEVELS.items():
            executions = [ORDERED] if level == "none" else [ORDERED, NAIVE]
            for execution in executions:
                result = simulate(
                    scenario,
                    POLICIES[name](),
                    traffic=True,
                    delays=model,
                    delay_seed=seed,
                    execution=execution,
                )
                results[(name, level, execution)] = compute_kpis(result)
    return results


def rows(robots: int, rate: float, results: list[dict[Key, RunKpis]]) -> list[str]:
    def mean(values: list[float]) -> float:
        return statistics.fmean(values)

    def completion(name: str, level: str) -> list[float]:
        return [r[(name, level, ORDERED)].completion_mean_s for r in results]

    lines = []
    for name in POLICY_NAMES:
        for level in LEVELS:
            kpis = [r[(name, level, ORDERED)] for r in results]
            cost = (
                ""
                if level == "none"
                else paired_difference(completion(name, level), completion(name, "none")).format()
            )
            vs_greedy = (
                "baseline"
                if name == "greedy"
                else paired_difference(
                    completion(name, level), completion("greedy", level)
                ).format()
            )
            naive = (
                "-"
                if level == "none"
                else f"{mean([float(r[(name, level, NAIVE)].conflicts) for r in results]):.1f}"
            )
            lines.append(
                f"| {robots} | {rate:g} | {name} | {level} "
                f"| {mean([k.utilization for k in kpis]):.1%} "
                f"| {mean([k.waiting_mean_s for k in kpis]):.1f} "
                f"| {mean([k.completion_mean_s for k in kpis]):.1f} "
                f"| {mean([k.completion_p95_s for k in kpis]):.1f} "
                f"| {mean([k.completion_max_s for k in kpis]):.1f} "
                f"| {mean([float(k.conflicts) for k in kpis]):.1f} | {naive} | {cost} "
                f"| {vs_greedy} |"
            )
    return lines


def run(seeds: int, duration_s: float, jobs: int) -> int:
    print(f"Delay comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"ortools {version('ortools')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()}), {jobs} worker processes"
    )
    print(
        f"Settings: map {MAP}, orders released for {duration_s:g} s, seeds 0-{seeds - 1}, "
        f"1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading, traffic on; "
        f"delayed edges take 1 to 5 s longer"
    )

    started = time.perf_counter()
    tasks = [(robots, rate, seed) for robots, rate in CONFIGS for seed in range(seeds)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {t: pool.submit(run_seed, t[0], t[1], duration_s, t[2]) for t in tasks}
        results = {task: future.result() for task, future in futures.items()}
    elapsed = time.perf_counter() - started

    print()
    print(
        "| Robots | Orders/h | Policy | Delays | Utilization | Waiting s | Completion s "
        "| Completion P95 s | Completion max s | Conflicts per run | Naive: conflicts per run "
        "| Cost of delays (95% CI) | vs greedy, same delays (95% CI) |"
    )
    print("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for robots, rate in CONFIGS:
        config_results = [results[(robots, rate, seed)] for seed in range(seeds)]
        for line in rows(robots, rate, config_results):
            print(line)

    ordered = [k for r in results.values() for key, k in r.items() if key[2] is ORDERED]
    undelivered = sum(k.undelivered for r in results.values() for k in r.values())
    conflicts = sum(k.conflicts for k in ordered)
    print()
    print(f"Ordered execution: {conflicts} conflicts in {len(ordered)} runs.")
    print(f"{sum(len(r) for r in results.values())} runs in {elapsed:.1f} s.")
    if undelivered or conflicts:
        print(f"WARNING: {undelivered} undelivered orders, {conflicts} conflicts when ordered.")
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
