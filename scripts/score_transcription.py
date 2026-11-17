"""Score a layout transcribed from a plan against the layout the plan was drawn from (D42).

    .venv\\Scripts\\python.exe scripts\\score_transcription.py ^
        TRUTH.layout.json TRANSCRIBED.layout.json --robots 6 --orders-per-hour 150

Measures, in this order:

1. **Scale**: the transcription's cell size and building size against the truth's.
2. **Cells**, after alignment: every truth cell's centre is looked up in the transcription (in
   metres from the top-left corner, so different cell sizes still compare), trying every shift of up
   to 3 transcription cells each way and keeping the one with most agreement. Per class (blocked,
   floor, storage, station, charger, parking, candidate slot): precision and recall. For cells both
   call floor: whether the one-way direction agrees.
3. **Whether it is usable**: it compiles, and passes the well-formedness checker for the fleet.
4. **What the errors cost**: both layouts run on seeds 0-9 (greedy, traffic, no disturbances), and
   the mean completion times are compared per seed. The seeds give the same orders only if both
   layouts have the same storage cells and station count; the output says whether they do.

Prints Markdown. Exit code 0 even when the transcription is poor: the numbers are the result.
"""

import argparse
import math
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.lab import paired_contrast
from amr_optimizer.layout import (
    Layout,
    LayoutFormatError,
    check_well_formed,
    compile_layout,
    load_layout,
)
from amr_optimizer.layout.model import ARROWS, is_floor
from amr_optimizer.metrics import compute_kpis
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate

CLASSES = {
    "#": "blocked",
    "S": "storage",
    "T": "station",
    "C": "charger",
    "P": "parking",
    "?": "slot",
}
SHIFT = 3
SEEDS = range(10)


def cell_class(char: str) -> str:
    return "floor" if is_floor(char) else CLASSES[char]


def lookup(layout: Layout, x_m: float, y_m: float, shift: tuple[int, int]) -> str:
    """The character of ``layout`` at a point in metres from its top-left corner (x right, y
    down), with the grid shifted by ``shift`` cells; outside the grid counts as blocked."""
    r = math.floor(y_m / layout.cell_m) + shift[0]
    c = math.floor(x_m / layout.cell_m) + shift[1]
    if 0 <= r < layout.rows and 0 <= c < layout.columns:
        return layout.grid[r][c]
    return "#"


def pairs(truth: Layout, other: Layout, shift: tuple[int, int]) -> list[tuple[str, str]]:
    cell = truth.cell_m
    return [
        (truth.grid[r][c], lookup(other, (c + 0.5) * cell, (r + 0.5) * cell, shift))
        for r in range(truth.rows)
        for c in range(truth.columns)
    ]


def best_shift(truth: Layout, other: Layout) -> tuple[int, int]:
    shifts = [(dr, dc) for dr in range(-SHIFT, SHIFT + 1) for dc in range(-SHIFT, SHIFT + 1)]

    def agreement(shift: tuple[int, int]) -> tuple[int, int]:
        same = sum(cell_class(a) == cell_class(b) for a, b in pairs(truth, other, shift))
        return same, -abs(shift[0]) - abs(shift[1])  # ties: the smallest shift

    return max(shifts, key=agreement)


def mean_completion(layout: Layout, robots: int, rate: float) -> list[float]:
    graph = compile_layout(layout)
    spec = ScenarioSpec(robots=robots, orders_per_hour=rate, duration_s=1800.0)
    values = []
    for seed in SEEDS:
        result = simulate(
            generate_scenario(graph, spec, seed=seed), POLICIES["greedy"](), traffic=True
        )
        kpis = compute_kpis(result)
        if not result.all_delivered or kpis.conflicts:
            raise RuntimeError(f"seed {seed}: undelivered orders or conflicts")
        values.append(kpis.completion_mean_s)
    return values


def score(truth_path: Path, other_path: Path, robots: int, rate: float) -> list[str]:
    truth = load_layout(truth_path)
    out = [f"### {other_path.name} against {truth_path.name}", ""]
    try:
        other = load_layout(other_path)
    except (OSError, LayoutFormatError) as error:
        return [*out, f"The transcription cannot be read: {error}"]

    width = (truth.columns * truth.cell_m, other.columns * other.cell_m)
    height = (truth.rows * truth.cell_m, other.rows * other.cell_m)
    out += [
        "| Scale | Truth | Transcription | Error |",
        "|---|---:|---:|---:|",
        f"| cell size (m) | {truth.cell_m:g} | {other.cell_m:g} | "
        f"{other.cell_m / truth.cell_m - 1:+.1%} |",
        f"| building width (m) | {width[0]:g} | {width[1]:g} | {width[1] / width[0] - 1:+.1%} |",
        f"| building depth (m) | {height[0]:g} | {height[1]:g} | "
        f"{height[1] / height[0] - 1:+.1%} |",
        "",
    ]

    shift = best_shift(truth, other)
    matched = pairs(truth, other, shift)
    agree = sum(cell_class(a) == cell_class(b) for a, b in matched)
    out += [
        f"Alignment: shift of {shift[0]:+d} rows, {shift[1]:+d} columns (transcription cells). "
        f"Cells in the same class: {agree} of {len(matched)} ({agree / len(matched):.1%}).",
        "",
        "| Class | In truth | Precision | Recall |",
        "|---|---:|---:|---:|",
    ]
    truth_counts = Counter(cell_class(a) for a, _ in matched)
    said_counts = Counter(cell_class(b) for _, b in matched)
    hits = Counter(cell_class(a) for a, b in matched if cell_class(a) == cell_class(b))
    for name in ("blocked", "floor", "storage", "station", "charger", "parking", "slot"):
        if not truth_counts[name] and not said_counts[name]:
            continue
        precision = hits[name] / said_counts[name] if said_counts[name] else float("nan")
        recall = hits[name] / truth_counts[name] if truth_counts[name] else float("nan")
        out.append(f"| {name} | {truth_counts[name]} | {precision:.1%} | {recall:.1%} |")
    floors = [(a, b) for a, b in matched if is_floor(a) and is_floor(b)]
    one_way = [(a, b) for a, b in floors if a in ARROWS]
    same_way = sum(a == b for a, b in floors)
    out += [
        "",
        f"Floor cells called floor by both: {len(floors)}; their direction (two-way or the arrow) "
        f"agrees in {same_way} ({same_way / max(1, len(floors)):.1%}); "
        f"one-way cells of the truth with the right arrow: {sum(a == b for a, b in one_way)} of "
        f"{len(one_way)}.",
        "",
    ]

    try:
        graph = compile_layout(other)
    except LayoutFormatError as error:
        return [*out, f"**Does not compile:** {error}"]
    report = check_well_formed(graph, robots=robots)
    out.append(
        f"Compiled: {len(graph)} nodes, {graph.num_edges} edges (truth: "
        f"{len(compile_layout(truth))} nodes, {compile_layout(truth).num_edges} edges). "
        + ("Well formed for " + f"{robots} robots." if report.ok else "**Not well formed:**")
    )
    if not report.ok:
        out += [f"- [{v.rule}] {v.message}" for v in report.violations]
        return out

    kinds = ("S", "T")
    same_storage = sorted(
        ((r + 0.5) * truth.cell_m, (c + 0.5) * truth.cell_m)
        for r in range(truth.rows)
        for c in range(truth.columns)
        if truth.grid[r][c] == "S"
    ) == sorted(
        ((r - shift[0] + 0.5) * other.cell_m, (c - shift[1] + 0.5) * other.cell_m)
        for r in range(other.rows)
        for c in range(other.columns)
        if other.grid[r][c] == "S"
    )
    count = {
        k: (sum(row.count(k) for row in truth.grid), sum(row.count(k) for row in other.grid))
        for k in kinds
    }
    base = mean_completion(truth, robots, rate)
    values = mean_completion(other, robots, rate)
    contrast = paired_contrast(
        "completion_mean_s",
        other_path.name,
        truth_path.name,
        values,
        base,
        confidence=0.95,
        resamples=2000,
        rng=random.Random(0),
    )
    out += [
        "",
        f"Simulation: greedy, traffic, {robots} robots, {rate:g} orders/h for 1800 s, seeds 0-9. "
        f"Same storage cells: {'yes' if same_storage else 'no'}; stations "
        f"{count['T'][0]} in the truth, {count['T'][1]} in the transcription"
        + (
            "; so each seed gives the same orders by index."
            if same_storage and count["T"][0] == count["T"][1]
            else "; so the seeds do not give the same orders, and the difference is not paired."
        ),
        "",
        "| Mean completion (s) | Truth | Transcription | Difference [95% interval] |",
        "|---|---:|---:|---|",
        f"| mean over seeds | {statistics.fmean(base):.1f} | {statistics.fmean(values):.1f} | "
        f"{contrast.difference:+.1f} [{contrast.low:+.1f}, {contrast.high:+.1f}] |",
    ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("truth", type=Path)
    parser.add_argument("transcription", type=Path)
    parser.add_argument("--robots", type=int, default=6)
    parser.add_argument("--orders-per-hour", type=float, default=150.0)
    args = parser.parse_args()
    print("\n".join(score(args.truth, args.transcription, args.robots, args.orders_per_hour)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
