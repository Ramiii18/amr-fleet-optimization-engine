"""Measure what a finer cell size costs: one layout at 1.0 m and the same hall at 0.5 m (D40).

    .venv\\Scripts\\python.exe scripts\\measure_cell_size.py [--seeds 5]

The 0.5 m version is made by refining the 1.0 m grid: every cell becomes 2 x 2 cells. Floor stays
floor (a 1 m one-way aisle becomes two 0.5 m lanes in the same direction, with lateral moves
between them), blocked stays blocked, and each endpoint keeps exactly one of its four cells, the
first in reading order that touches the floor. So both versions have the same storage locations,
stations, chargers and parking spots, and generated orders draw the same indices: only the
resolution of the floor changes. A layout drawn at 0.5 m by hand would also have twice the storage
cells along each rack face; that effect is not measured here.

Prints, for each cell size: nodes, edges, and per policy the mean completion time, the decision
latency (wall time per dispatch decision, pooled over seeds: mean, P95, max) and the wall time per
run. Wall times depend on the machine; they were measured on one laptop.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.layout import Layout, compile_layout, load_layout, require_well_formed
from amr_optimizer.layout.model import BLOCKED, is_floor
from amr_optimizer.metrics import compute_kpis
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "layouts" / "examples" / "l_hall.layout.json"
SPEC = ScenarioSpec(robots=8, orders_per_hour=200.0, duration_s=1800.0)
POLICY_NAMES = ("greedy", "cpsat-fair")


def refine(layout: Layout) -> Layout:
    """The same hall at half the cell size (see the module docstring)."""
    rows = [
        [layout.grid[r // 2][c // 2] for c in range(2 * layout.columns)]
        for r in range(2 * layout.rows)
    ]
    for r in range(layout.rows):
        for c in range(layout.columns):
            char = layout.grid[r][c]
            if char == BLOCKED or is_floor(char):
                continue
            kept = False
            for fr, fc in (
                (2 * r, 2 * c),
                (2 * r, 2 * c + 1),
                (2 * r + 1, 2 * c),
                (2 * r + 1, 2 * c + 1),
            ):
                touches = any(
                    0 <= fr + dr < len(rows)
                    and 0 <= fc + dc < len(rows[0])
                    and is_floor(rows[fr + dr][fc + dc])
                    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
                )
                if touches and not kept:
                    kept = True
                else:
                    rows[fr][fc] = BLOCKED
    return Layout(tuple("".join(row) for row in rows), layout.cell_m / 2, layout.description)


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))]


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args()
    coarse = load_layout(LAYOUT)
    print(
        f"{LAYOUT.name}: {SPEC.robots} robots, {SPEC.orders_per_hour:g} orders/h for "
        f"{SPEC.duration_s:g} s, traffic on, seeds 0-{args.seeds - 1}"
    )
    print(
        "| cell | nodes | edges | policy | completion mean (s) | decision ms mean / P95 / max "
        "| wall s per run |"
    )
    print("|---|---:|---:|---|---:|---|---:|")
    for layout in (coarse, refine(coarse)):
        graph = compile_layout(layout)
        require_well_formed(graph, robots=SPEC.robots)
        for policy in POLICY_NAMES:
            completion, latency, walls = [], [], []
            for seed in range(args.seeds):
                scenario = generate_scenario(graph, SPEC, seed=seed)
                started = time.perf_counter()
                result = simulate(scenario, POLICIES[policy](), traffic=True)
                walls.append(time.perf_counter() - started)
                kpis = compute_kpis(result)
                if not result.all_delivered or kpis.conflicts:
                    print(f"seed {seed}: not all delivered or conflicts", file=sys.stderr)
                    return 1
                completion.append(kpis.completion_mean_s)
                latency += [s * 1000 for s in result.decision_seconds]
            print(
                f"| {layout.cell_m:g} m | {len(graph)} | {graph.num_edges} | {policy} "
                f"| {statistics.fmean(completion):.1f} | {statistics.fmean(latency):.1f} / "
                f"{percentile(latency, 0.95):.1f} / {max(latency):.1f} "
                f"| {statistics.fmean(walls):.2f} |"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
