"""Measure what recording robot events and battery levels costs (D35).

    git worktree add ..\\phase1 feature/amr-digital-twin-phase1
    .venv\\Scripts\\python.exe scripts\\measure_observability.py --baseline-src ..\\phase1\\src

Two questions, on small, medium and large scenarios:

- **Simulation time.** The same runs are timed with this checkout and with a checkout from
  before the events existed (``--baseline-src``). Each run is repeated and the fastest time is
  kept, the usual way to measure code and not the machine's other work. The two versions are
  timed in separate processes, alternating, so that neither is favoured by a warm cache.
- **Trace size.** The version 2 trace of each run against its version 1 trace (the same trace
  without the additions), as JSON and compressed.

Wall times depend on the machine. The counts and sizes do not.
"""

import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (name, map, robots, orders per hour, order window in s, with batteries/delays/breakdowns)
CASES = [
    ("small, plain traffic", "small", 3, 120.0, 1800.0, False),
    ("small, disturbed", "small", 3, 120.0, 1800.0, True),
    ("medium, plain traffic", "medium", 8, 320.0, 1800.0, False),
    ("medium, disturbed", "medium", 8, 320.0, 1800.0, True),
    ("large, plain traffic", "large", 25, 750.0, 1800.0, False),
    ("large, disturbed", "large", 25, 750.0, 1800.0, True),
]


def _run(case: tuple[str, str, int, float, float, bool]):  # type: ignore[no-untyped-def]
    """One run of ``case``. Imports are local: this also runs against an older checkout."""
    from amr_optimizer.dispatch import POLICIES
    from amr_optimizer.domain import BatterySpec, ChargingPolicy
    from amr_optimizer.scenario import ScenarioSpec, generate_scenario
    from amr_optimizer.sim import simulate
    from amr_optimizer.traffic import NO_DELAYS, DelayModel, Recovery
    from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

    _, map_name, robots, rate, duration, disturbed = case
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=duration)
    scenario = generate_scenario(generate_grid_warehouse(PRESETS[map_name]), spec, seed=0)
    return simulate(
        scenario,
        POLICIES["greedy"](),
        traffic=True,
        delays=DelayModel(0.1, 10, 40) if disturbed else NO_DELAYS,
        battery=BatterySpec() if disturbed else None,
        charging=ChargingPolicy.OPPORTUNISTIC if disturbed else ChargingPolicy.WHEN_LOW,
        breakdowns=DelayModel(0.01, 200, 900) if disturbed else NO_DELAYS,
        recovery=Recovery.REPLAN if disturbed else Recovery.WAIT,
    )


def worker(repeats: int) -> None:
    """Time every case and print the fastest time of each, as JSON."""
    times = {}
    for case in CASES:
        best = float("inf")
        for _ in range(repeats):
            started = time.perf_counter()
            result = _run(case)
            best = min(best, time.perf_counter() - started)
        times[case[0]] = {"seconds": best, "end_tick": result.end_tick, "events": len(result.log)}
    print(json.dumps(times))


def _timed(src: Path, repeats: int) -> dict[str, dict[str, float]]:
    env = dict(os.environ, PYTHONPATH=str(src))
    output = subprocess.run(
        [sys.executable, __file__, "--worker", "--repeats", str(repeats)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data: dict[str, dict[str, float]] = json.loads(output)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--baseline-src", type=Path, help="src/ of a checkout without robot events")
    parser.add_argument("--repeats", type=int, default=5, help="runs per case; the fastest counts")
    parser.add_argument("--rounds", type=int, default=3, help="alternating rounds per version")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.repeats)
        return 0

    from amr_optimizer.replay import build_trace, downgrade_to_v1, trace_to_json

    print("Trace size: version 2 against version 1 (the same trace without the additions)\n")
    print(
        "| Run | Visits | Robot events | v1 JSON | v2 JSON | Change | v1 gzip | v2 gzip | Change |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for case in CASES:
        result = _run(case)
        v2 = trace_to_json(build_trace(result))
        v1 = json.dumps(downgrade_to_v1(json.loads(v2)), separators=(",", ":"))
        z1, z2 = (len(gzip.compress(text.encode(), mtime=0)) for text in (v1, v2))
        visits = sum(len(path.visits) for path in result.trajectories.values())
        print(
            f"| {case[0]} | {visits:,} | {len(result.robot_events):,} | {len(v1) / 1e3:,.0f} kB "
            f"| {len(v2) / 1e3:,.0f} kB | {len(v2) / len(v1) - 1:+.1%} | {z1 / 1e3:,.0f} kB "
            f"| {z2 / 1e3:,.0f} kB | {z2 / z1 - 1:+.1%} |"
        )

    if args.baseline_src is None:
        print("\nNo --baseline-src given: simulation time was not compared.")
        return 0
    baseline_src = args.baseline_src.resolve()
    if not (baseline_src / "amr_optimizer").is_dir():
        parser.error(f"{baseline_src} has no amr_optimizer package")
    best: dict[str, dict[str, dict[str, float]]] = {"before": {}, "now": {}}
    for _ in range(args.rounds):  # alternate, so that neither version always runs first
        for label, src in (("before", baseline_src), ("now", ROOT / "src")):
            for name, measured in _timed(src, args.repeats).items():
                kept = best[label].setdefault(name, measured)
                if measured["seconds"] < kept["seconds"]:
                    best[label][name] = measured
    print(
        f"\nSimulation time: fastest of {args.rounds * args.repeats} runs per version "
        f"({args.rounds} alternating rounds of {args.repeats})\n"
    )
    print("| Run | Before | Now | Change | Same run |")
    print("|---|---:|---:|---:|---|")
    for case in CASES:
        before, now = best["before"][case[0]], best["now"][case[0]]
        same = (before["end_tick"], before["events"]) == (now["end_tick"], now["events"])
        print(
            f"| {case[0]} | {before['seconds'] * 1e3:,.0f} ms | {now['seconds'] * 1e3:,.0f} ms "
            f"| {now['seconds'] / before['seconds'] - 1:+.1%} "
            f"| {'yes' if same else 'NO: the runs differ'} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
