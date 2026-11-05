"""Command line: generate a scenario, simulate it under one policy, and print the KPIs.

Example (PowerShell, from the repository root):

    .venv\\Scripts\\python.exe -m amr_optimizer --map medium --robots 5 --policy hungarian
"""

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import asdict

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import RunKpis, compute_kpis
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse, load_graph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amr-sim",
        description="Simulate a synthetic AMR fleet on a generated scenario and report KPIs.",
    )
    parser.add_argument(
        "--map", default="medium", help=f"a preset ({', '.join(PRESETS)}) or a JSON map file"
    )
    parser.add_argument("--robots", type=int, default=5)
    parser.add_argument("--orders-per-hour", type=float, default=60.0)
    parser.add_argument("--duration-s", type=float, default=3600.0, help="order release window")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--policy", choices=sorted(POLICIES), default="greedy")
    parser.add_argument(
        "--round-s",
        type=float,
        default=0.0,
        help="decision rounds every N seconds; 0 (default) decides at every event",
    )
    parser.add_argument(
        "--traffic",
        action="store_true",
        help="robots plan around each other's reservations (M4); default is free flow",
    )
    parser.add_argument("--json", action="store_true", help="print the KPIs as JSON")
    return parser


def _load_map(name: str) -> WarehouseGraph:
    if name in PRESETS:
        return generate_grid_warehouse(PRESETS[name])
    return load_graph(name)


def _report(kpis: RunKpis) -> list[str]:
    return [
        f"  throughput         {kpis.throughput_per_h:8.1f} orders/h",
        f"  waiting time       {kpis.waiting_mean_s:8.1f} s mean  {kpis.waiting_p95_s:8.1f} s P95",
        f"  completion time    {kpis.completion_mean_s:8.1f} s mean  "
        f"{kpis.completion_p95_s:8.1f} s P95  {kpis.completion_max_s:8.1f} s max",
        f"  assignment delay   {kpis.assignment_delay_mean_s:8.1f} s mean",
        f"  utilization        {kpis.utilization:8.1%}",
        f"  distance           {kpis.empty_distance_m:8.0f} m empty  "
        f"{kpis.loaded_distance_m:8.0f} m loaded  ({kpis.empty_share:.1%} empty)",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.round_s < 0:
        _parser().error("--round-s must be non-negative")
    spec = ScenarioSpec(
        robots=args.robots, orders_per_hour=args.orders_per_hour, duration_s=args.duration_s
    )
    graph = _load_map(args.map)
    scenario = generate_scenario(graph, spec, seed=args.seed)
    interval = scenario.time_model.to_ticks(args.round_s) or None

    started = time.perf_counter()
    result = simulate(
        scenario, POLICIES[args.policy](), decision_interval_ticks=interval, traffic=args.traffic
    )
    elapsed = time.perf_counter() - started
    kpis = compute_kpis(result)

    if args.json:
        print(json.dumps(asdict(kpis) | {"empty_share": kpis.empty_share}, indent=2))
        return 0
    rounds = f"decision rounds every {args.round_s:g} s" if interval else "decides at every event"
    print(
        f"Scenario: map {args.map} ({len(graph)} nodes), {args.robots} robots, "
        f"{args.orders_per_hour:g} orders/h for {args.duration_s:g} s, seed {args.seed}: "
        f"{kpis.orders} orders"
    )
    mode = "with traffic (reservations)" if args.traffic else "free flow (no traffic)"
    print(f"Policy:   {kpis.policy}, {rounds}, {mode}")
    print(
        f"Delivered {kpis.delivered} of {kpis.orders} orders in {kpis.duration_s:.1f} s of "
        f"simulated time (computed in {elapsed:.2f} s)"
    )
    if kpis.undelivered:
        print(f"WARNING: {kpis.undelivered} orders were not delivered")
    print("\n".join(_report(kpis)))
    return 0
