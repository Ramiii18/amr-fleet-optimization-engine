"""Compare dispatch policies in free flow and with traffic, on paired seeds (M4).

For every configuration and seed, one scenario is generated, and every policy runs on it twice:
in free flow (robots pass through each other, as in M2 and M3) and with traffic (robots plan
around each other's reservations; D23, D24).

Policies: greedy (the M2 baseline), and the two worst-first planners of M3: insertion-fair
and cpsat-fair (D22).

Reported per policy and mode, as means over seeds: utilization, waiting time, completion time
(mean, P95, maximum), the empty share of driving, and conflicts per run (robots in the same
node or corridor at the same time, by the independent checker). Then paired differences of
the mean completion time with 95% t confidence intervals:
- **cost of traffic**: with traffic minus free flow, for the same policy;
- **vs greedy**: each policy minus greedy, in the same mode.

With traffic, the totals of robots moved out of another robot's way and of assignments
postponed to break a cycle are reported too.

Runs execute in parallel worker processes (``--jobs``). Each run is single-threaded and
deterministic, so the results do not depend on the parallelism.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_traffic.py [--seeds 20] [--jobs 8]
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
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse

MAP = "large"
CONFIGS = [(5, 100.0), (5, 150.0), (10, 200.0), (10, 300.0)]  # (robots, orders per hour)
VARIANTS = ["greedy", "insertion-fair", "cpsat-fair"]
MODES = [False, True]  # traffic off, on


@dataclass(frozen=True, slots=True)
class Run:
    kpis: RunKpis
    moves: int
    postponed: int


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


def run_seed(robots: int, rate: float, duration_s: float, seed: int) -> dict[tuple[str, bool], Run]:
    """Every policy in both modes on the scenario of one configuration and seed."""
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
    scenario = generate_scenario(_graph(), spec, seed=seed)
    runs = {}
    for name in VARIANTS:
        for traffic in MODES:
            result = simulate(scenario, POLICIES[name](), traffic=traffic)
            runs[(name, traffic)] = Run(
                compute_kpis(result), result.moves_out_of_the_way, result.deferred_assignments
            )
    return runs


def rows(robots: int, rate: float, results: list[dict[tuple[str, bool], Run]]) -> list[str]:
    def mean(values: list[float]) -> float:
        return statistics.fmean(values)

    def completion(name: str, traffic: bool) -> list[float]:
        return [r[(name, traffic)].kpis.completion_mean_s for r in results]

    lines = []
    for name in VARIANTS:
        for traffic in MODES:
            runs = [r[(name, traffic)].kpis for r in results]
            cost = (
                paired_difference(completion(name, True), completion(name, False)).format()
                if traffic
                else ""
            )
            vs_greedy = (
                "baseline"
                if name == "greedy"
                else paired_difference(
                    completion(name, traffic), completion("greedy", traffic)
                ).format()
            )
            lines.append(
                f"| {robots} | {rate:g} | {name} | {'traffic' if traffic else 'free flow'} "
                f"| {mean([k.utilization for k in runs]):.1%} "
                f"| {mean([k.waiting_mean_s for k in runs]):.1f} "
                f"| {mean([k.completion_mean_s for k in runs]):.1f} "
                f"| {mean([k.completion_p95_s for k in runs]):.1f} "
                f"| {mean([k.completion_max_s for k in runs]):.1f} "
                f"| {mean([k.empty_share for k in runs]):.1%} "
                f"| {mean([float(k.conflicts) for k in runs]):.1f} | {cost} | {vs_greedy} |"
            )
    return lines


def run(seeds: int, duration_s: float, jobs: int) -> int:
    print(f"Traffic comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"ortools {version('ortools')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()}), {jobs} worker processes"
    )
    print(
        f"Settings: map {MAP}, orders released for {duration_s:g} s, seeds 0-{seeds - 1}, "
        f"1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading"
    )

    started = time.perf_counter()
    tasks = [(robots, rate, seed) for robots, rate in CONFIGS for seed in range(seeds)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {t: pool.submit(run_seed, t[0], t[1], duration_s, t[2]) for t in tasks}
        results = {task: future.result() for task, future in futures.items()}
    elapsed = time.perf_counter() - started

    print()
    print(
        "| Robots | Orders/h | Policy | Mode | Utilization | Waiting s | Completion s "
        "| Completion P95 s | Completion max s | Empty share | Conflicts per run "
        "| Cost of traffic (95% CI) | vs greedy, same mode (95% CI) |"
    )
    print("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for robots, rate in CONFIGS:
        config_results = [results[(robots, rate, seed)] for seed in range(seeds)]
        for line in rows(robots, rate, config_results):
            print(line)

    all_runs = [run for r in results.values() for run in r.values()]
    traffic_runs = [run for r in results.values() for (_, t), run in r.items() if t]
    undelivered = sum(run.kpis.undelivered for run in all_runs)
    conflicts_with_traffic = sum(run.kpis.conflicts for run in traffic_runs)
    print()
    print(
        f"With traffic: {conflicts_with_traffic} conflicts in {len(traffic_runs)} runs; "
        f"{sum(run.moves for run in traffic_runs)} robots moved out of the way; "
        f"{sum(run.postponed for run in traffic_runs)} assignments postponed to break a cycle."
    )
    print(f"{len(all_runs)} runs in {elapsed:.1f} s.")
    failed = False
    if undelivered:
        print(f"WARNING: {undelivered} orders were not delivered across all runs.")
        failed = True
    if conflicts_with_traffic:
        print("WARNING: runs with traffic contain conflicts.")
        failed = True
    if not failed:
        print("Every order was delivered in every run.")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--duration-s", type=float, default=3600.0)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    sys.exit(run(args.seeds, args.duration_s, args.jobs))


if __name__ == "__main__":
    main()
