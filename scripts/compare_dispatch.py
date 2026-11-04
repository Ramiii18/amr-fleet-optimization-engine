"""Compare dispatch policies in simulation, on paired seeds.

For every configuration (fleet size and order rate on the large preset) and every seed, one
scenario is generated, and every policy variant runs on exactly that scenario. Differences
between variants are therefore paired: the order stream and start positions are the same
(D14), so seed-to-seed noise cancels out of the comparison.

Variants: greedy and Hungarian dispatch, each deciding at every event or in 10 s rounds.

Reported per variant, as means over seeds: utilization, waiting time, assignment delay,
completion time (mean and P95), and the share of driving that was empty. The last column
is the paired difference in mean completion time against greedy dispatch at every event,
with a 95% confidence interval from the t distribution over seeds. An interval that
contains 0 means no difference was detected at this sample size.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_dispatch.py [--seeds 20] [--duration-s 3600]
"""

import argparse
import platform
import statistics
import subprocess
import sys
import time
from datetime import date
from importlib.metadata import version
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import RunKpis, compute_kpis, paired_difference
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

MAP = "large"
CONFIGS = [(5, 100.0), (5, 150.0), (10, 200.0), (10, 300.0)]  # (robots, orders per hour)
VARIANTS = [("greedy", 0.0), ("hungarian", 0.0), ("greedy", 10.0), ("hungarian", 10.0)]
BASELINE = VARIANTS[0]


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


def run(seeds: int, duration_s: float) -> int:
    print(f"Dispatch comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"numpy {version('numpy')}, scipy {version('scipy')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()})"
    )
    print(
        f"Settings: map {MAP}, orders released for {duration_s:g} s, seeds 0-{seeds - 1}, "
        f"1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading"
    )
    print()
    print(
        "| Robots | Orders/h | Policy | Decisions | Utilization | Waiting s | Assign delay s "
        "| Completion s | Completion P95 s | Empty share "
        "| Completion diff. vs greedy at every event "
        "(95% CI) |"
    )
    print("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|")

    graph = generate_grid_warehouse(PRESETS[MAP])
    started = time.perf_counter()
    undelivered = 0
    for robots, rate in CONFIGS:
        spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
        kpis: dict[tuple[str, float], list[RunKpis]] = {variant: [] for variant in VARIANTS}
        for seed in range(seeds):
            scenario = generate_scenario(graph, spec, seed=seed)
            for policy, round_s in VARIANTS:
                interval = scenario.time_model.to_ticks(round_s) or None
                result = simulate(scenario, POLICIES[policy](), decision_interval_ticks=interval)
                kpis[(policy, round_s)].append(compute_kpis(result))

        baseline = [k.completion_mean_s for k in kpis[BASELINE]]
        for variant in VARIANTS:
            runs = kpis[variant]
            undelivered += sum(k.undelivered for k in runs)

            def mean(values: list[float]) -> float:
                return statistics.fmean(values)

            completion = [k.completion_mean_s for k in runs]
            delta = (
                "baseline"
                if variant == BASELINE
                else paired_difference(completion, baseline).format()
            )
            decisions = "every event" if variant[1] == 0 else f"{variant[1]:g} s rounds"
            print(
                f"| {robots} | {rate:g} | {variant[0]} | {decisions} "
                f"| {mean([k.utilization for k in runs]):.1%} "
                f"| {mean([k.waiting_mean_s for k in runs]):.1f} "
                f"| {mean([k.assignment_delay_mean_s for k in runs]):.1f} "
                f"| {mean(completion):.1f} "
                f"| {mean([k.completion_p95_s for k in runs]):.1f} "
                f"| {mean([k.empty_share for k in runs]):.1%} | {delta} |"
            )

    print()
    print(f"{len(CONFIGS) * len(VARIANTS) * seeds} runs in {time.perf_counter() - started:.1f} s.")
    if undelivered:
        print(f"WARNING: {undelivered} orders were not delivered across all runs.")
        return 1
    print("Every order was delivered in every run.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--duration-s", type=float, default=3600.0)
    args = parser.parse_args()
    sys.exit(run(args.seeds, args.duration_s))


if __name__ == "__main__":
    main()
