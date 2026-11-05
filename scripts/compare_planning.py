"""Compare the planning policies with greedy dispatch in simulation, on paired seeds (M3).

Variants, all deciding at every event:
- greedy: oldest order first, nearest idle robot (the M2 baseline);
- insertion: receding-horizon plan of every robot and the oldest 10 orders, by cheapest
  insertion, minimising total completion time (D18, D19);
- cpsat: the same plan improved by CP-SAT within its deterministic limit (D20);
- insertion-fair and cpsat-fair: the same two planners with the worst-first objective, which
  minimises the longest lead time first and the total completion time second (D22).

For every configuration and seed, one scenario is generated and every variant runs on exactly
that scenario (D14), so differences are paired over seeds.

Reported per variant, as means over seeds: utilization, waiting time, completion time (mean,
P95 and maximum, the last to expose starvation) and the empty share of driving. Then paired
differences with 95% t confidence intervals: of the mean completion time against greedy and,
for each CP-SAT variant, against insertion with the same objective (the D19 criterion); and of
the maximum completion time against greedy.

Solver statistics per configuration and CP-SAT variant, over all decisions: how many needed a
search, how many of those were proven optimal, stopped at the limit or fell back to the
insertion plan, how often CP-SAT improved on insertion, the mean remaining gap of stopped solves
(relative to the plan cost of D22 for the fair variant), and solve times.

Runs execute in parallel worker processes (``--jobs``). Each solve uses one thread and a
deterministic limit, so the KPIs do not depend on the parallelism. The solve times do: the
processes share the CPU, so they are slower than solves run one at a time.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_planning.py [--seeds 20] [--jobs 8]
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

from amr_optimizer.dispatch import (
    DEFAULT_MAX_ORDERS,
    POLICIES,
    CpSatDispatch,
    SolverSettings,
    SolveStatus,
)
from amr_optimizer.metrics import RunKpis, compute_kpis, paired_difference, percentile
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse

MAP = "large"
CONFIGS = [(5, 100.0), (5, 150.0), (10, 200.0), (10, 300.0)]  # (robots, orders per hour)
VARIANTS = ["greedy", "insertion", "cpsat", "insertion-fair", "cpsat-fair"]
SAME_MODEL = {"cpsat": "insertion", "cpsat-fair": "insertion-fair"}  # the D19 comparison


@dataclass(frozen=True, slots=True)
class Solve:
    """One CP-SAT decision, reduced to what the statistics need."""

    status: SolveStatus
    objective: int
    hint_objective: int
    bound: int
    wall_s: float


@dataclass(frozen=True, slots=True)
class SeedResult:
    kpis: dict[str, RunKpis]
    solves: dict[str, list[Solve]]  # per CP-SAT variant


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


def run_seed(robots: int, rate: float, duration_s: float, seed: int) -> SeedResult:
    """Every variant on the scenario of one configuration and seed. Runs in a worker."""
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
    scenario = generate_scenario(_graph(), spec, seed=seed)
    kpis: dict[str, RunKpis] = {}
    solves: dict[str, list[Solve]] = {}
    for name in VARIANTS:
        policy = POLICIES[name]()
        kpis[name] = compute_kpis(simulate(scenario, policy))
        if isinstance(policy, CpSatDispatch):
            solves[name] = [
                Solve(o.status, o.objective, o.hint_objective, o.bound, o.wall_s)
                for o in policy.outcomes
            ]
    return SeedResult(kpis, solves)


def kpi_rows(robots: int, rate: float, results: list[SeedResult]) -> list[str]:
    def mean(values: list[float]) -> float:
        return statistics.fmean(values)

    completion = {name: [r.kpis[name].completion_mean_s for r in results] for name in VARIANTS}
    worst = {name: [r.kpis[name].completion_max_s for r in results] for name in VARIANTS}
    rows = []
    for name in VARIANTS:
        runs = [r.kpis[name] for r in results]
        if name == "greedy":
            vs_greedy = max_vs_greedy = "baseline"
        else:
            vs_greedy = paired_difference(completion[name], completion["greedy"]).format()
            max_vs_greedy = paired_difference(worst[name], worst["greedy"]).format()
        same_model = SAME_MODEL.get(name)
        vs_same_model = (
            paired_difference(completion[name], completion[same_model]).format()
            if same_model
            else ""
        )
        rows.append(
            f"| {robots} | {rate:g} | {name} "
            f"| {mean([k.utilization for k in runs]):.1%} "
            f"| {mean([k.waiting_mean_s for k in runs]):.1f} "
            f"| {mean(completion[name]):.1f} "
            f"| {mean([k.completion_p95_s for k in runs]):.1f} "
            f"| {mean(worst[name]):.1f} "
            f"| {mean([k.empty_share for k in runs]):.1%} | {vs_greedy} | {vs_same_model} "
            f"| {max_vs_greedy} |"
        )
    return rows


def solver_row(robots: int, rate: float, name: str, results: list[SeedResult]) -> str:
    solves = [s for r in results for s in r.solves[name]]
    searched = [s for s in solves if s.status is not SolveStatus.TRIVIAL]

    def count(status: SolveStatus) -> str:
        n = sum(s.status is status for s in searched)
        return f"{n} ({n / len(searched):.1%})" if searched else "0"

    stopped = [s for s in searched if s.status is SolveStatus.LIMIT]
    gap = (
        f"{statistics.fmean((s.objective - s.bound) / s.objective for s in stopped):.1%}"
        if stopped
        else "-"
    )
    improved = sum(s.objective < s.hint_objective for s in searched)
    walls = [s.wall_s for s in searched]
    times = (
        f"{statistics.fmean(walls):.3f} / {percentile(walls, 95):.3f} / {max(walls):.3f}"
        if walls
        else "-"
    )
    return (
        f"| {robots} | {rate:g} | {name} | {len(solves)} | {len(searched)} "
        f"| {count(SolveStatus.OPTIMAL)} "
        f"| {count(SolveStatus.LIMIT)} | {count(SolveStatus.FALLBACK)} | {improved} | {gap} "
        f"| {times} |"
    )


def run(seeds: int, duration_s: float, jobs: int) -> int:
    settings = SolverSettings()
    print(f"Planning comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"ortools {version('ortools')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()}), {jobs} worker processes"
    )
    print(
        f"Settings: map {MAP}, orders released for {duration_s:g} s, seeds 0-{seeds - 1}, "
        f"1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading; planning window "
        f"{DEFAULT_MAX_ORDERS} orders; CP-SAT: 1 worker, deterministic limit "
        f"{settings.max_deterministic_time:g}, seed {settings.random_seed}"
    )

    started = time.perf_counter()
    tasks = [(robots, rate, seed) for robots, rate in CONFIGS for seed in range(seeds)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {
            task: pool.submit(run_seed, task[0], task[1], duration_s, task[2]) for task in tasks
        }
        results = {task: future.result() for task, future in futures.items()}
    elapsed = time.perf_counter() - started

    print()
    print(
        "| Robots | Orders/h | Policy | Utilization | Waiting s | Completion s | Completion P95 s "
        "| Completion max s | Empty share | Completion diff. vs greedy (95% CI) "
        "| vs insertion, same objective (95% CI) | Max diff. vs greedy (95% CI) |"
    )
    print("|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|")
    by_config = {
        (robots, rate): [results[(robots, rate, seed)] for seed in range(seeds)]
        for robots, rate in CONFIGS
    }
    for (robots, rate), config_results in by_config.items():
        for row in kpi_rows(robots, rate, config_results):
            print(row)

    print()
    print("CP-SAT solver statistics, over all decisions of all seeds:")
    print()
    print(
        "| Robots | Orders/h | Policy | Decisions | Searched | Proven optimal "
        "| Stopped at limit | Fallback | Improved on insertion | Mean gap when stopped "
        "| Solve s mean / P95 / max |"
    )
    print("|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for (robots, rate), config_results in by_config.items():
        for name in SAME_MODEL:
            print(solver_row(robots, rate, name, config_results))

    undelivered = sum(k.undelivered for r in results.values() for k in r.kpis.values())
    print()
    print(f"{len(tasks) * len(VARIANTS)} runs in {elapsed:.1f} s.")
    if undelivered:
        print(f"WARNING: {undelivered} orders were not delivered across all runs.")
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
