"""Command line: generate or read a scenario, simulate it under one policy, print the KPIs.

Examples (PowerShell, from the repository root):

    .venv\\Scripts\\python.exe -m amr_optimizer --map medium --robots 5 --policy hungarian
    .venv\\Scripts\\python.exe -m amr_optimizer --scenario scenarios\\examples\\morning_wave.json
"""

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import asdict

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import BatterySpec, ChargingPolicy
from amr_optimizer.metrics import ChargingKpis, RunKpis, compute_charging_kpis, compute_kpis
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario, load_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.traffic import NO_DELAYS, DelayModel, ExecutionPolicy
from amr_optimizer.warehouse import PRESETS, WarehouseGraph, generate_grid_warehouse, load_graph

# Options that describe a generated scenario, with their defaults. A scenario file replaces them.
GENERATED = {"map": "medium", "robots": 5, "orders_per_hour": 60.0, "duration_s": 3600.0}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amr-sim",
        description="Simulate a synthetic AMR fleet on a generated scenario, or on a scenario "
        "file with your own map, robots and orders, and report KPIs.",
    )
    parser.add_argument(
        "--scenario",
        metavar="FILE",
        help="a scenario file: map, robots and orders (see scenarios/examples/); it replaces "
        "--map, --robots, --orders-per-hour and --duration-s",
    )
    parser.add_argument(
        "--map", help=f"a preset ({', '.join(PRESETS)}) or a JSON map file (default medium)"
    )
    parser.add_argument("--robots", type=int, help="default 5")
    parser.add_argument("--orders-per-hour", type=float, help="default 60")
    parser.add_argument("--duration-s", type=float, help="order release window (default 3600)")
    parser.add_argument(
        "--seed", type=int, default=1, help="seeds generated orders, delays and batteries"
    )
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
    parser.add_argument(
        "--delay-share",
        type=float,
        default=0.0,
        help="with --traffic: the share of edge traversals that are delayed (0 to 1)",
    )
    parser.add_argument(
        "--delay-s",
        type=float,
        nargs=2,
        default=(1.0, 5.0),
        metavar=("MIN", "MAX"),
        help="with --delay-share: the range of extra seconds of a delayed edge (default 1 5)",
    )
    parser.add_argument(
        "--naive",
        action="store_true",
        help="with --traffic: robots keep their own schedules under delays (conflicts possible)",
    )
    parser.add_argument(
        "--charging",
        choices=[policy.value for policy in ChargingPolicy],
        help="with --traffic: robots run on the default synthetic battery (D26) and charge "
        "by this policy (D27); default is no battery model",
    )
    parser.add_argument("--json", action="store_true", help="print the KPIs as JSON")
    return parser


def _load_map(name: str) -> WarehouseGraph:
    if name in PRESETS:
        return generate_grid_warehouse(PRESETS[name])
    return load_graph(name)


def _scenario(args: argparse.Namespace) -> tuple[Scenario, str]:
    """The scenario to run, and the line that describes it in the report."""
    given = [name for name in GENERATED if getattr(args, name) is not None]
    if args.scenario is not None:
        if given:
            options = ", ".join("--" + name.replace("_", "-") for name in given)
            _parser().error(f"--scenario replaces {options}; give one or the other")
        try:
            scenario = load_scenario(args.scenario)
        except (OSError, ValueError) as error:
            _parser().error(f"--scenario {args.scenario}: {error}")
        return scenario, (
            f"Scenario: file {args.scenario} ({len(scenario.graph)} nodes), "
            f"{len(scenario.robots)} robots, {len(scenario.orders)} orders"
        )
    for name, default in GENERATED.items():
        if getattr(args, name) is None:
            setattr(args, name, default)
    spec = ScenarioSpec(
        robots=args.robots, orders_per_hour=args.orders_per_hour, duration_s=args.duration_s
    )
    graph = _load_map(args.map)
    scenario = generate_scenario(graph, spec, seed=args.seed)
    return scenario, (
        f"Scenario: map {args.map} ({len(graph)} nodes), {args.robots} robots, "
        f"{args.orders_per_hour:g} orders/h for {args.duration_s:g} s, seed {args.seed}: "
        f"{len(scenario.orders)} orders"
    )


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
        f"  conflicts          {kpis.conflicts:8d} (robots in the same place at the same time)",
    ]


def _charging_report(kpis: ChargingKpis) -> list[str]:
    return [
        f"  charging           {kpis.charging_trips:8d} trips when low  "
        f"{kpis.top_up_trips:8d} top-ups",
        f"  out of service     {kpis.out_of_service_share:8.1%} of robot time  "
        f"{kpis.charger_wait_s:8.1f} s waiting for a charger",
        f"  chargers           {kpis.charger_occupancy:8.1%} occupied  "
        f"lowest battery {kpis.lowest_energy_share:.0%}",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.round_s < 0:
        _parser().error("--round-s must be non-negative")
    if (args.delay_share or args.naive or args.charging) and not args.traffic:
        _parser().error("--delay-share, --naive and --charging need --traffic")
    if not 0 <= args.delay_share <= 1:
        _parser().error("--delay-share must be between 0 and 1")
    scenario, description = _scenario(args)
    interval = scenario.time_model.to_ticks(args.round_s) or None
    low, high = (scenario.time_model.to_ticks(s) for s in args.delay_s)
    if not 1 <= low <= high:
        _parser().error("--delay-s needs 0 < MIN <= MAX")
    delays = DelayModel(args.delay_share, low, high) if args.delay_share else NO_DELAYS
    execution = ExecutionPolicy.NAIVE if args.naive else ExecutionPolicy.ORDERED
    charging = ChargingPolicy(args.charging) if args.charging else ChargingPolicy.WHEN_LOW

    started = time.perf_counter()
    result = simulate(
        scenario,
        POLICIES[args.policy](),
        decision_interval_ticks=interval,
        traffic=args.traffic,
        delays=delays,
        delay_seed=args.seed,
        execution=execution,
        battery=BatterySpec() if args.charging else None,
        battery_seed=args.seed,
        charging=charging,
    )
    elapsed = time.perf_counter() - started
    kpis = compute_kpis(result)
    charging_kpis = compute_charging_kpis(result) if result.battery else None

    if args.json:
        output = asdict(kpis) | {"empty_share": kpis.empty_share}
        if charging_kpis is not None:
            output["charging"] = asdict(charging_kpis)
        print(json.dumps(output, indent=2))
        return 0
    rounds = f"decision rounds every {args.round_s:g} s" if interval else "decides at every event"
    print(description)
    mode = "with traffic (reservations)" if args.traffic else "free flow (no traffic)"
    if args.delay_share:
        kind = "naive" if args.naive else "ordered"
        mode += f", {args.delay_share:.0%} of edges delayed, {kind} execution"
    if args.charging:
        mode += f", batteries ({args.charging} charging)"
    print(f"Policy:   {kpis.policy}, {rounds}, {mode}")
    print(
        f"Delivered {kpis.delivered} of {kpis.orders} orders in {kpis.duration_s:.1f} s of "
        f"simulated time (computed in {elapsed:.2f} s)"
    )
    if kpis.undelivered:
        print(f"WARNING: {kpis.undelivered} orders were not delivered")
    print("\n".join(_report(kpis)))
    if charging_kpis is not None:
        print("\n".join(_charging_report(charging_kpis)))
    return 0
