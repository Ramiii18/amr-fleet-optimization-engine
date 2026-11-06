"""Run the engine's plans on mock robots through VDA 5050 3.0.0 messages (P5, D30).

For each seed, one scenario is generated on the large map. Every robot gets one job at tick 0,
planned with SIPP as the simulator plans it. Fleet control then sends the jobs as orders and
releases each robot's base under the planned order; mock robots drive and report their state;
fleet control follows them only through those messages.

Reported, over all seeds: messages by topic; conflicts in the robots' trajectories (checked to be
zero); and how much later than planned each job was delivered (its drop action reported
finished). Robots may stop only at nodes, so a robot whose next node is still occupied waits at
the node before, not at the end of its corridor as in the simulator.

With ``--log``, every message of the first seed is written as JSON lines: tick, topic, payload.

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\vda5050_demo.py [--robots 10] [--seeds 20]
        [--log vda5050-messages.jsonl]
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from amr_optimizer.domain import TimeModel, TravelTimes
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim.traffic_control import TrafficControl
from amr_optimizer.traffic import path_conflicts
from amr_optimizer.vda5050 import VERSION, first_jobs, run_jobs
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

MAP = "large"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--robots", type=int, nargs="+", default=[5, 10, 25])
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--log", type=Path, default=None, help="write the first run's messages")
    args = parser.parse_args()

    graph = generate_grid_warehouse(PRESETS[MAP])
    print(f"VDA 5050 {VERSION} demo: map {MAP}, one job per robot, seeds 0-{args.seeds - 1}")
    print()
    print(
        "| Robots | Jobs | Messages per run | Conflicts | Late: mean s | Late: P95 s | Late: max s "
        "| Run time per run s |"
    )
    print("|---:|---:|---:|---:|---:|---:|---:|---:|")
    failures = 0
    all_messages = Counter[str]()
    for robots in args.robots:
        jobs_total, conflicts, messages = 0, 0, Counter[str]()
        late: list[float] = []
        started = time.perf_counter()
        for seed in range(args.seeds):
            spec = ScenarioSpec(robots=robots, orders_per_hour=600)
            scenario = generate_scenario(graph, spec, seed=seed)
            travel = TravelTimes(graph, speed_mps=scenario.speed_mps, time_model=TimeModel(0.1))
            jobs = first_jobs(scenario, TrafficControl(scenario, travel))
            run = run_jobs(scenario, jobs, map_id=MAP)
            jobs_total += len(jobs)
            conflicts += len(path_conflicts({r: [p] for r, p in run.trajectories.items()}))
            messages.update(m.topic.rsplit("/", 1)[1] for m in run.log)
            missing = set(run.planned_at) - set(run.delivered_at)
            failures += len(missing)
            late += [
                (run.delivered_at[o] - run.planned_at[o]) * scenario.time_model.tick_s
                for o in run.planned_at
                if o in run.delivered_at
            ]
            if args.log is not None and robots == args.robots[0] and seed == 0:
                with args.log.open("w", encoding="utf-8") as file:
                    for m in run.log:
                        payload = json.loads(m.payload)
                        file.write(
                            json.dumps({"tick": m.tick, "topic": m.topic, "payload": payload})
                        )
                        file.write("\n")
        elapsed = (time.perf_counter() - started) / args.seeds
        ordered = sorted(late)
        p95 = ordered[max(0, -(-95 * len(ordered) // 100) - 1)]
        per_run = sum(messages.values()) / args.seeds
        all_messages.update(messages)
        print(
            f"| {robots} | {jobs_total} | {per_run:.0f} | {conflicts} | "
            f"{statistics.fmean(late):.1f} | {p95:.1f} | {max(late):.1f} | {elapsed:.2f} |"
        )
    print()
    print(
        "Messages by topic, all runs: "
        + ", ".join(f"{k} {v}" for k, v in sorted(all_messages.items()))
    )
    if args.log is not None:
        print(f"Messages of the first run written to {args.log.as_posix()}.")
    if failures:
        print(f"WARNING: {failures} jobs were not reported delivered.")
        return 1
    print("Every job was reported delivered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
