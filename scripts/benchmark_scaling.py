"""Scaling benchmark with traffic: 3, 5, 10 and 25 robots, two loads, three policies (M5).

For every fleet size, the order rate is set per robot: 20 orders per robot per hour (moderate
load) and 30 (high load). For every configuration and seed, one scenario is generated on the
large map, and every policy runs on it with traffic (D23, D24): greedy (the M2 baseline) and
the worst-first planners insertion-fair and cpsat-fair (D22).

Reported per configuration and policy, as means over seeds: utilization, waiting time,
completion time (mean, P95, maximum), the empty share of driving, conflicts per run (always
zero with traffic; checked), and the paired difference in mean completion time against
greedy, with a 95% t confidence interval.

**Decision latency**: the wall time of each dispatch decision, from building the snapshot to
the end of executing it, including the planning of every itinerary. Pooled over all decisions
of all seeds: mean, P95 and maximum, in milliseconds. It is measured while the worker
processes share the CPU, so a decision taken alone would be faster.

With ``--csv``, one row per run is also written to a CSV file, for `scripts/make_figures.py`.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\benchmark_scaling.py [--seeds 20] [--jobs 8]
        [--csv docs/results/scaling.csv]
"""

import argparse
import csv
import functools
import os
import platform
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from importlib.metadata import version
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import RunKpis, compute_kpis, paired_difference, percentile
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse

MAP = "large"
FLEETS = [3, 5, 10, 25]
ORDERS_PER_ROBOT_HOUR = [20.0, 30.0]
VARIANTS = ["greedy", "insertion-fair", "cpsat-fair"]


@dataclass(frozen=True, slots=True)
class Run:
    kpis: RunKpis
    decision_seconds: tuple[float, ...]


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


def run_task(robots: int, rate: float, policy: str, duration_s: float, seed: int) -> Run:
    """One policy on the scenario of one configuration and seed, with traffic."""
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
    scenario = generate_scenario(_graph(), spec, seed=seed)
    result = simulate(scenario, POLICIES[policy](), traffic=True)
    return Run(compute_kpis(result), result.decision_seconds)


def table_rows(robots: int, rate: float, runs: dict[str, list[Run]]) -> list[str]:
    def mean(values: list[float]) -> float:
        return statistics.fmean(values)

    completion = {name: [r.kpis.completion_mean_s for r in runs[name]] for name in VARIANTS}
    lines = []
    for name in VARIANTS:
        kpis = [r.kpis for r in runs[name]]
        latency_ms = [s * 1000 for r in runs[name] for s in r.decision_seconds]
        vs_greedy = (
            "baseline"
            if name == "greedy"
            else paired_difference(completion[name], completion["greedy"]).format()
        )
        lines.append(
            f"| {robots} | {rate:g} | {name} "
            f"| {mean([k.utilization for k in kpis]):.1%} "
            f"| {mean([k.waiting_mean_s for k in kpis]):.1f} "
            f"| {mean(completion[name]):.1f} "
            f"| {mean([k.completion_p95_s for k in kpis]):.1f} "
            f"| {mean([k.completion_max_s for k in kpis]):.1f} "
            f"| {mean([k.empty_share for k in kpis]):.1%} "
            f"| {sum(k.conflicts for k in kpis)} | {vs_greedy} "
            f"| {statistics.fmean(latency_ms):.1f} / {percentile(latency_ms, 95):.1f} "
            f"/ {max(latency_ms):.1f} |"
        )
    return lines


def write_csv(path: Path, results: dict[tuple[int, float, str, int], Run]) -> None:
    """One row per run: configuration, seed, every KPI, and the run's decision latency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(next(iter(results.values())).kpis))  # starts with policy, robots
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["orders_per_hour", "seed", *fields, "decisions", "decision_mean_ms"])
        for (robots, rate, policy, seed), run in sorted(results.items()):
            values = asdict(run.kpis)
            assert (values["policy"], values["robots"]) == (policy, robots)
            seconds = run.decision_seconds
            latency_ms = statistics.fmean(seconds) * 1000 if seconds else 0.0
            writer.writerow(
                [
                    f"{rate:g}",
                    seed,
                    *(f"{v:.6g}" if isinstance(v, float) else v for v in values.values()),
                    len(seconds),
                    f"{latency_ms:.3f}",
                ]
            )


def run(seeds: int, duration_s: float, jobs: int, csv_path: Path | None) -> int:
    print(f"Scaling benchmark with traffic, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"ortools {version('ortools')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()}), {jobs} worker processes"
    )
    print(
        f"Settings: map {MAP}, orders released for {duration_s:g} s, seeds 0-{seeds - 1}, "
        f"1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading, traffic on; "
        f"{', '.join(f'{r:g}' for r in ORDERS_PER_ROBOT_HOUR)} orders per robot per hour"
    )

    configs = [
        (robots, robots * per_robot) for robots in FLEETS for per_robot in ORDERS_PER_ROBOT_HOUR
    ]
    tasks = [(r, rate, p, s) for r, rate in configs for p in VARIANTS for s in range(seeds)]
    # The slowest tasks first, so that the pool is not left waiting for them at the end.
    tasks.sort(key=lambda t: (-t[0], -t[1], t[2] != "cpsat-fair", t[3]))
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {t: pool.submit(run_task, t[0], t[1], t[2], duration_s, t[3]) for t in tasks}
        results = {task: future.result() for task, future in futures.items()}
    elapsed = time.perf_counter() - started

    print()
    print(
        "| Robots | Orders/h | Policy | Utilization | Waiting s | Completion s "
        "| Completion P95 s | Completion max s | Empty share | Conflicts (all seeds) "
        "| Completion diff. vs greedy (95% CI) | Decision ms mean / P95 / max |"
    )
    print("|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for robots, rate in configs:
        runs = {p: [results[(robots, rate, p, s)] for s in range(seeds)] for p in VARIANTS}
        for line in table_rows(robots, rate, runs):
            print(line)

    if csv_path is not None:
        write_csv(csv_path, results)
        print()
        print(f"Per-run results written to {csv_path.as_posix()}.")

    undelivered = sum(r.kpis.undelivered for r in results.values())
    conflicts = sum(r.kpis.conflicts for r in results.values())
    print()
    print(f"{len(results)} runs in {elapsed:.1f} s.")
    if undelivered or conflicts:
        print(f"WARNING: {undelivered} undelivered orders and {conflicts} conflicts.")
        return 1
    print("Every order was delivered in every run, with no conflicts.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--duration-s", type=float, default=3600.0)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--csv", type=Path, default=None, help="also write one row per run")
    args = parser.parse_args()
    sys.exit(run(args.seeds, args.duration_s, args.jobs, args.csv))


if __name__ == "__main__":
    main()
