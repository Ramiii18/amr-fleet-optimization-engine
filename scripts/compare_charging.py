"""Compare charging policies: dispatch policies x charging policies, with batteries (P2).

For every configuration and seed, one scenario is generated on the large map, with traffic.
Every dispatch policy runs on it:
- without a battery model (the M4 and M5 setting);
- with the default synthetic battery (D26) under each charging policy (D27):
  - `when-low`: robots charge only below the low threshold, up to the high threshold;
  - `opportunistic`: as when-low, and idle robots top up when no order is waiting;
  - `opportunistic-early`: as opportunistic, but low robots return to service at the low
    threshold.

Start levels come from a stream seeded by the scenario seed, so every charging policy starts
from the same levels.

Reported as means over seeds:
- service: waiting time, completion time (mean, P95, maximum) and conflicts per run (checked to
  be zero);
- charging: trips when low, top-ups, the share of robot time out of service, robot time waiting
  for a charger, charger occupancy, and the lowest battery level (the minimum over seeds).

Then paired differences, with 95% t confidence intervals:
- **cost of batteries**: the mean completion time under this charging policy minus without a
  battery model, for the same dispatch policy;
- **vs when-low**: an opportunistic policy minus when-low, for the mean and for the maximum
  completion time;
- **vs greedy**: the mean completion time of the policy minus greedy, under the same charging
  policy.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\compare_charging.py [--seeds 20] [--jobs 8]
"""

import argparse
import functools
import math
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
from amr_optimizer.domain import BatterySpec, ChargingPolicy
from amr_optimizer.metrics import (
    ChargingKpis,
    RunKpis,
    compute_charging_kpis,
    compute_kpis,
    paired_difference,
)
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, NodeKind, WarehouseGraph, generate_grid_warehouse

MAP = "large"
CONFIGS = [(10, 200.0), (10, 300.0)]  # (robots, orders per hour)
POLICY_NAMES = ["greedy", "cpsat-fair"]
NONE = "no battery"
CHARGING = {NONE: None} | {policy.value: policy for policy in ChargingPolicy}
WHEN_LOW = ChargingPolicy.WHEN_LOW.value
BATTERY = BatterySpec()

Key = tuple[str, str]  # dispatch policy, charging
Kpis = tuple[RunKpis, ChargingKpis | None]


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


def run_seed(robots: int, rate: float, duration_s: float, seed: int) -> dict[Key, Kpis]:
    """Every dispatch and charging policy on one scenario. Runs in a worker process."""
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration_s)
    scenario = generate_scenario(_graph(), spec, seed=seed)
    results: dict[Key, Kpis] = {}
    for name in POLICY_NAMES:
        for label, charging in CHARGING.items():
            result = simulate(
                scenario,
                POLICIES[name](),
                traffic=True,
                battery=BATTERY if charging else None,
                battery_seed=seed,
                charging=charging or ChargingPolicy.WHEN_LOW,
            )
            charging_kpis = compute_charging_kpis(result) if charging else None
            results[(name, label)] = (compute_kpis(result), charging_kpis)
    return results


def _mean(values: list[float]) -> float:
    return statistics.fmean(values)


def service_rows(robots: int, rate: float, results: list[dict[Key, Kpis]]) -> list[str]:
    def completion(name: str, label: str) -> list[float]:
        return [r[(name, label)][0].completion_mean_s for r in results]

    def worst(name: str, label: str) -> list[float]:
        return [r[(name, label)][0].completion_max_s for r in results]

    lines = []
    for name in POLICY_NAMES:
        for label in CHARGING:
            kpis = [r[(name, label)][0] for r in results]
            cost = (
                ""
                if label == NONE
                else paired_difference(completion(name, label), completion(name, NONE)).format()
            )
            opportunistic = label not in (NONE, WHEN_LOW)
            vs_when_low = (
                paired_difference(completion(name, label), completion(name, WHEN_LOW)).format()
                if opportunistic
                else ""
            )
            worst_vs_when_low = (
                paired_difference(worst(name, label), worst(name, WHEN_LOW)).format()
                if opportunistic
                else ""
            )
            vs_greedy = (
                "baseline"
                if name == "greedy"
                else paired_difference(
                    completion(name, label), completion("greedy", label)
                ).format()
            )
            lines.append(
                f"| {robots} | {rate:g} | {name} | {label} "
                f"| {_mean([k.waiting_mean_s for k in kpis]):.1f} "
                f"| {_mean([k.completion_mean_s for k in kpis]):.1f} "
                f"| {_mean([k.completion_p95_s for k in kpis]):.1f} "
                f"| {_mean([k.completion_max_s for k in kpis]):.1f} "
                f"| {_mean([float(k.conflicts) for k in kpis]):.1f} | {cost} | {vs_when_low} "
                f"| {worst_vs_when_low} | {vs_greedy} |"
            )
    return lines


def charging_rows(robots: int, rate: float, results: list[dict[Key, Kpis]]) -> list[str]:
    lines = []
    for name in POLICY_NAMES:
        for label in CHARGING:
            if label == NONE:
                continue
            kpis = [k for r in results if (k := r[(name, label)][1]) is not None]
            lines.append(
                f"| {robots} | {rate:g} | {name} | {label} "
                f"| {_mean([float(k.charging_trips) for k in kpis]):.1f} "
                f"| {_mean([float(k.top_up_trips) for k in kpis]):.1f} "
                f"| {_mean([k.out_of_service_share for k in kpis]):.1%} "
                f"| {_mean([k.charger_wait_s for k in kpis]):.0f} "
                f"| {_mean([k.charger_occupancy for k in kpis]):.1%} "
                f"| {min(k.lowest_energy_share for k in kpis):.1%} |"
            )
    return lines


def run(seeds: int, duration_s: float, jobs: int) -> int:
    chargers = len(_graph().nodes_of_kind(NodeKind.CHARGER))
    print(f"Charging comparison, {date.today().isoformat()}")
    print(
        f"Environment: Python {platform.python_version()}, {platform.platform()}, "
        f"ortools {version('ortools')}, "
        f"amr-fleet-optimization-engine {version('amr-fleet-optimization-engine')} "
        f"(commit {git_commit()}), {jobs} worker processes"
    )
    print(
        f"Settings: map {MAP} ({chargers} chargers), orders released for {duration_s:g} s, "
        f"seeds 0-{seeds - 1}, 1 m/s, 0.1 s ticks, 10 s loading and 10 s unloading, traffic on"
    )
    print(
        f"Battery (synthetic, D26): {BATTERY.capacity_m:g} m of driving, full charge in "
        f"{BATTERY.full_charge_s:g} s, low {BATTERY.low_share:.0%}, high "
        f"{BATTERY.high_share:.0%}, start levels {BATTERY.start_min_share:.0%} to "
        f"{BATTERY.start_max_share:.0%} (seeded)"
    )

    started = time.perf_counter()
    tasks = [(robots, rate, seed) for robots, rate in CONFIGS for seed in range(seeds)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {t: pool.submit(run_seed, t[0], t[1], duration_s, t[2]) for t in tasks}
        results = {task: future.result() for task, future in futures.items()}
    elapsed = time.perf_counter() - started

    print()
    print("Service:")
    print()
    print(
        "| Robots | Orders/h | Policy | Charging | Waiting s | Completion s | Completion P95 s "
        "| Completion max s | Conflicts per run | Cost of batteries (95% CI) "
        "| vs when-low (95% CI) | Max: vs when-low (95% CI) | vs greedy, same charging (95% CI) |"
    )
    print("|---:|---:|---|---|---:|---:|---:|---:|---:|---|---|---|---|")
    for robots, rate in CONFIGS:
        config_results = [results[(robots, rate, seed)] for seed in range(seeds)]
        for line in service_rows(robots, rate, config_results):
            print(line)

    print()
    print("Charging (means over seeds; lowest level: the minimum over seeds):")
    print()
    print(
        "| Robots | Orders/h | Policy | Charging | Trips when low | Top-ups "
        "| Out of service | Charger wait, robot-s | Charger occupancy | Lowest level |"
    )
    print("|---:|---:|---|---|---:|---:|---:|---:|---:|---:|")
    for robots, rate in CONFIGS:
        config_results = [results[(robots, rate, seed)] for seed in range(seeds)]
        for line in charging_rows(robots, rate, config_results):
            print(line)

    runs = [kpis for r in results.values() for kpis in r.values()]
    undelivered = sum(k.undelivered for k, _ in runs)
    conflicts = sum(k.conflicts for k, _ in runs)
    lowest = min((c.lowest_energy_share for _, c in runs if c is not None), default=math.nan)
    print()
    print(f"{conflicts} conflicts in {len(runs)} runs; lowest battery level {lowest:.1%}.")
    print(f"{len(runs)} runs in {elapsed:.1f} s.")
    if undelivered or conflicts or not lowest > 0:
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
