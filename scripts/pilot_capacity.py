"""Pilot: how much load can a fleet carry on one layout? Sets the loads of a study (L7, D44).

    .venv\\Scripts\\python.exe scripts\\pilot_capacity.py layouts\\study\\baseline.layout.json ^
        --conditions experiments\\layout_study\\template.json --robots 10 ^
        --loads 100 200 300 400 500 600 --workers 8

Runs the layout under the conditions of a study (its ``disturbances`` and ``battery``, read from
the study's template) at each load, with traffic, for ``--policies`` on the **pilot seeds** (100 to
104 by default: never the study's seeds). Prints, per load and policy, means over the seeds.

**The rule that turns the pilot into loads, fixed before the pilot ran:** the capacity C is the mean
throughput of ``cpsat-fair`` at the highest load of the sweep, where the fleet is saturated; the
study's two families run at 0.60 C and 0.85 C, rounded to the nearest 10 orders per hour. The pilot
runs the baseline layout only, so it cannot favour a candidate.
"""

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from amr_optimizer.lab.experiment import Battery, Disturbances, Family
from amr_optimizer.lab.runner import run_one

SHARES = (0.60, 0.85)


def _run(
    job: tuple[Path, int, float, float, Disturbances, Battery | None, int, str],
) -> dict[str, object]:
    layout, robots, load, duration, d, battery, seed, policy = job
    family = Family(f"pilot-{load:g}", "", robots, load, duration)
    started = time.perf_counter()
    row = run_one(family, d, seed, policy, layout, battery=battery)
    return row | {"load": load, "wall_s": time.perf_counter() - started}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("layout", type=Path)
    parser.add_argument("--conditions", type=Path, required=True, help="the study's template")
    parser.add_argument("--robots", type=int, required=True)
    parser.add_argument("--loads", type=float, nargs="+", required=True)
    parser.add_argument("--duration-s", type=float, default=1800.0)
    parser.add_argument("--seeds", type=int, nargs="+", default=[100, 101, 102, 103, 104])
    parser.add_argument("--policies", nargs="+", default=["greedy", "cpsat-fair"])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, help="also write every run as JSON")
    args = parser.parse_args()
    template = json.loads(args.conditions.read_text(encoding="utf-8"))
    d = Disturbances(**template["disturbances"])
    battery = Battery(**template["battery"]) if "battery" in template else None
    jobs = [
        (args.layout.resolve(), args.robots, load, args.duration_s, d, battery, seed, policy)
        for load in args.loads
        for policy in args.policies
        for seed in args.seeds
    ]
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(_run, jobs))
    elapsed = time.perf_counter() - started

    print(
        f"{args.layout.name}: {args.robots} robots, {args.duration_s:g} s of orders, seeds "
        f"{args.seeds[0]}-{args.seeds[-1]}, disturbances {d.model}, battery "
        f"{'on' if battery else 'off'}; {len(rows)} runs in {elapsed:.0f} s "
        f"with {args.workers} workers"
    )
    print(
        "| Orders/h | Policy | Throughput /h | Completion mean s | P95 s | Run ends s "
        "| All delivered | Utilization | Out of service | Wall s per run |"
    )
    print("|---:|---|---:|---:|---:|---:|---|---:|---:|---:|")
    capacity = None
    for load in args.loads:
        for policy in args.policies:
            own = [r for r in rows if r["load"] == load and r["policy"] == policy]

            def mean(key: str, runs: list[dict[str, object]] = own) -> float:
                return statistics.fmean(float(str(r[key])) for r in runs)

            out_of_service = mean("out_of_service_share") if battery else float("nan")
            print(
                f"| {load:g} | {policy} | {mean('throughput_per_h'):.1f} "
                f"| {mean('completion_mean_s'):.1f} "
                f"| {mean('completion_p95_s'):.1f} | {mean('end_tick') / 10:.0f} "
                f"| {sum(bool(r['all_delivered']) for r in own)}/{len(own)} "
                f"| {mean('utilization'):.1%} "
                f"| {out_of_service:.1%} | {mean('wall_s'):.2f} |"
            )
            if policy == "cpsat-fair" and load == max(args.loads):
                capacity = mean("throughput_per_h")
    if capacity is not None:
        loads = [round(share * capacity / 10) * 10 for share in SHARES]
        print(
            f"\nCapacity C (cpsat-fair throughput at {max(args.loads):g} orders/h): {capacity:.1f} "
            f"orders/h; families at 0.60 C and 0.85 C: {loads[0]} and {loads[1]} orders/h"
        )
    if args.out is not None:
        args.out.write_text(json.dumps(rows, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
