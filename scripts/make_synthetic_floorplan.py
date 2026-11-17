"""Draw a synthetic floor plan image from a layout file, to test transcription (D42).

    .venv\\Scripts\\python.exe scripts\\make_synthetic_floorplan.py ^
        layouts\\ground_truth\\mixed_hall.layout.json

Writes ``<name>.plan.png`` next to the layout, and ``<name>.plan.json`` with what the picture does
not state outright: its scale in pixels per metre and the pixel of the layout's top-left corner. The
JSON is for scoring only and is never shown to a transcriber.

The drawing imitates an architectural plan, not the grid: solid areas (walls, rack ends, anything
not drivable) in dark grey; rack bays as light rectangles, one per storage location; free-standing
columns as small black squares; stations, chargers, parking spots and candidate positions as
symbols explained in a legend; one-way traffic as arrows painted every few metres along an aisle;
a scale bar. There are no grid lines and no labels on cells. Every committed plan is synthetic.

The output is deterministic: the same layout gives the same bytes with the same matplotlib.
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, FancyArrow, Patch, Rectangle  # noqa: E402

from amr_optimizer.layout import Layout, load_layout  # noqa: E402
from amr_optimizer.layout.model import ARROWS, BLOCKED, is_floor  # noqa: E402

PX_PER_M = 24  # pixels per metre in the image
MARGIN_M = 2.0  # white border around the building, in metres
LEGEND_M = 4.5  # band below the building for the legend and the scale bar
ARROW_EVERY = 3  # one painted arrow per this many cells of a one-way run

SOLID, RACK, BAY_LINE, COLUMN = "#4b4f54", "#e7d9b8", "#b19a66", "#111111"
STATION, CHARGER, PARKING, CANDIDATE, ARROW = "#1f5fa8", "#2e8b57", "#d9822b", "#8a8a8a", "#5b7083"


def _neighbours(layout: Layout, r: int, c: int) -> list[str]:
    cells = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        rr, cc = r + dr, c + dc
        inside = 0 <= rr < layout.rows and 0 <= cc < layout.columns
        cells.append(layout.grid[rr][cc] if inside else BLOCKED)
    return cells


def _is_column(layout: Layout, r: int, c: int) -> bool:
    """A blocked cell with no blocked neighbour: a free-standing column."""
    return layout.grid[r][c] == BLOCKED and BLOCKED not in _neighbours(layout, r, c)


def _arrow_cells(layout: Layout) -> list[tuple[int, int, str]]:
    """The cells where an arrow is painted: every few cells along each run of one arrow."""
    painted = []
    for char, (dr, dc) in ARROWS.items():
        seen: set[tuple[int, int]] = set()
        for r in range(layout.rows):
            for c in range(layout.columns):
                if layout.grid[r][c] != char or (r, c) in seen:
                    continue
                run = []  # walk the run along the arrow's axis, from its upstream end
                rr, cc = r, c
                while (
                    0 <= rr < layout.rows
                    and 0 <= cc < layout.columns
                    and layout.grid[rr][cc] == char
                ):
                    run.append((rr, cc))
                    rr, cc = rr + abs(dr), cc + abs(dc)
                seen.update(run)
                for i in range(len(run) // 2 % ARROW_EVERY, len(run), ARROW_EVERY):
                    painted.append((*run[i], char))
    return painted


def draw(
    layout: Layout, out: Path, px_per_m: float = PX_PER_M, dimensions: bool = False
) -> dict[str, object]:
    """Draw the plan of ``layout`` into ``out``. With ``dimensions``, the building's width and
    depth are also written along its outline, as on an architect's drawing."""
    rows, cols, cell = layout.rows, layout.columns, layout.cell_m
    width_m, height_m = cols * cell + 2 * MARGIN_M, rows * cell + 2 * MARGIN_M + LEGEND_M
    fig = plt.figure(figsize=(width_m * px_per_m / 100, height_m * px_per_m / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(-MARGIN_M, cols * cell + MARGIN_M)
    ax.set_ylim(-(rows * cell + MARGIN_M + LEGEND_M), MARGIN_M)  # y down: plan coordinates
    ax.set_aspect("equal")
    ax.axis("off")

    def box(r: int, c: int, inset: float = 0.0) -> tuple[float, float, float]:
        return c * cell + inset, -(r + 1) * cell + inset, cell - 2 * inset

    for r, row in enumerate(layout.grid):
        for c, char in enumerate(row):
            x, y, size = box(r, c)
            if char == BLOCKED and _is_column(layout, r, c):
                ax.add_patch(Rectangle(box(r, c, 0.2)[:2], 0.6 * cell, 0.6 * cell, color=COLUMN))
            elif char == BLOCKED:
                ax.add_patch(Rectangle((x, y), size, size, color=SOLID, linewidth=0))
            elif char == "S":
                ax.add_patch(
                    Rectangle((x, y), size, size, facecolor=RACK, edgecolor=BAY_LINE, linewidth=0.8)
                )
            elif char == "T":
                ax.add_patch(
                    Rectangle(box(r, c, 0.12)[:2], 0.76 * cell, 0.76 * cell, color=STATION)
                )
                ax.add_patch(
                    Rectangle(box(r, c, 0.32)[:2], 0.36 * cell, 0.36 * cell, color="white")
                )
            elif char == "C":
                ax.add_patch(Circle((x + cell / 2, y + cell / 2), 0.38 * cell, color=CHARGER))
            elif char == "P":
                ax.add_patch(
                    Rectangle(
                        box(r, c, 0.14)[:2],
                        0.72 * cell,
                        0.72 * cell,
                        fill=False,
                        edgecolor=PARKING,
                        linewidth=1.6,
                    )
                )
            elif char == "?":
                ax.add_patch(
                    Rectangle(
                        box(r, c, 0.14)[:2],
                        0.72 * cell,
                        0.72 * cell,
                        fill=False,
                        edgecolor=CANDIDATE,
                        linewidth=1.0,
                        linestyle=(0, (2, 2)),
                    )
                )
            else:
                assert is_floor(char), char
    for r, c, char in _arrow_cells(layout):
        dr, dc = ARROWS[char]
        cx, cy = c * cell + cell / 2, -(r * cell + cell / 2)
        ax.add_patch(
            FancyArrow(
                cx - 0.3 * dc * cell,
                cy + 0.3 * dr * cell,
                0.6 * dc * cell,
                -0.6 * dr * cell,
                width=0.06 * cell,
                head_width=0.32 * cell,
                head_length=0.22 * cell,
                length_includes_head=True,
                color=ARROW,
            )
        )

    # Legend and scale bar, below the building.
    top = -(rows * cell + 1.2)
    handles = [
        Patch(facecolor=SOLID, label="Wall, rack end or no access"),
        Patch(facecolor=RACK, edgecolor=BAY_LINE, label="Rack bay (one storage location)"),
        Patch(facecolor=COLUMN, label="Column"),
        Patch(facecolor=STATION, label="Pick/drop station"),
        Line2D(
            [], [], marker="o", color="none", markerfacecolor=CHARGER, markersize=9, label="Charger"
        ),
        Patch(facecolor="none", edgecolor=PARKING, linewidth=1.6, label="Parking spot"),
        Patch(
            facecolor="none",
            edgecolor=CANDIDATE,
            linestyle=(0, (2, 2)),
            label="Candidate position (empty)",
        ),
        Line2D(
            [],
            [],
            marker=">",
            color="none",
            markerfacecolor=ARROW,
            markersize=9,
            label="One-way traffic",
        ),
    ]
    ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.01, 1 - (rows * cell + MARGIN_M + 1.0) / height_m),
        ncol=2,
        fontsize=7,
        frameon=False,
        handlelength=1.4,
    )
    bar = 5.0
    x0, y0 = cols * cell - bar, top - LEGEND_M + 1.6
    for i in range(int(bar)):
        ax.add_patch(
            Rectangle(
                (x0 + i, y0), 1.0, 0.25, color="black" if i % 2 == 0 else "white", linewidth=0
            )
        )
    ax.add_patch(Rectangle((x0, y0), bar, 0.25, fill=False, edgecolor="black", linewidth=0.8))
    for i in (0, int(bar)):
        ax.text(x0 + i, y0 - 0.2, f"{i}", ha="center", va="top", fontsize=7)
    ax.text(x0 + bar / 2, y0 - 0.2, "metres", ha="center", va="top", fontsize=7)
    ax.text(
        0,
        MARGIN_M - (0.15 if dimensions else 0.6),
        "SYNTHETIC FLOOR PLAN - invented for testing, not a real site",
        ha="left",
        va="top",
        fontsize=8,
        color="#333333",
    )

    if dimensions:
        w, h = cols * cell, rows * cell
        style = {"color": "#555555", "linewidth": 0.6}
        ax.annotate("", (0, 0.6), (w, 0.6), arrowprops={"arrowstyle": "<->", **style})
        ax.text(w / 2, 0.75, f"{w:.2f} m", ha="center", va="bottom", fontsize=7, color="#555555")
        ax.annotate("", (-0.6, 0), (-0.6, -h), arrowprops={"arrowstyle": "<->", **style})
        ax.text(
            -0.75,
            -h / 2,
            f"{h:.2f} m",
            ha="right",
            va="center",
            rotation=90,
            fontsize=7,
            color="#555555",
        )

    fig.savefig(out, dpi=100, metadata={"Software": None}, facecolor="white")
    plt.close(fig)
    # The layout's top-left corner is at (0, 0) in metres; in pixels it is one margin in.
    return {
        "image": out.name,
        "px_per_m": px_per_m,
        "origin_px": [MARGIN_M * px_per_m, MARGIN_M * px_per_m],
        "cell_m": cell,
        "rows": rows,
        "columns": cols,
    }


def degrade(
    clean: Path, out: Path, *, angle_deg: float, blur_px: float, noise: float, seed: int
) -> None:
    """A worse copy of a plan image: rotated by ``angle_deg`` (counter-clockwise, white fill),
    blurred (Gaussian, ``blur_px`` pixels), and with seeded Gaussian noise of standard deviation
    ``noise`` on a 0 to 1 scale. Deterministic for one seed."""
    import numpy as np
    from scipy import ndimage

    image = plt.imread(clean)[..., :3].astype(np.float64)
    image = ndimage.rotate(image, angle_deg, axes=(1, 0), reshape=True, order=1, cval=1.0)
    image = ndimage.gaussian_filter(image, sigma=(blur_px, blur_px, 0))
    image = image + np.random.default_rng(seed).normal(0.0, noise, image.shape)
    plt.imsave(out, np.clip(image, 0.0, 1.0), format="png", metadata={"Software": None})


DEGRADED_PX_PER_M = 21.7  # not a whole number of pixels per metre
BLUR_PX, NOISE, SEED = 0.8, 0.06, 7


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("layout", type=Path)
    parser.add_argument(
        "--degraded",
        type=float,
        metavar="ANGLE",
        help="instead, write <name>.degraded.png: drawn at 21.7 px per metre with dimension "
        "text, rotated by ANGLE degrees, blurred and with seeded noise",
    )
    args = parser.parse_args()
    layout = load_layout(args.layout)
    stem = args.layout.name.removesuffix(".layout.json")
    if args.degraded is None:
        image = args.layout.with_name(f"{stem}.plan.png")
        meta = draw(layout, image)
        side = args.layout.with_name(f"{stem}.plan.json")
    else:
        image = args.layout.with_name(f"{stem}.degraded.png")
        meta = draw(layout, image, DEGRADED_PX_PER_M, dimensions=True)
        degrade(image, image, angle_deg=args.degraded, blur_px=BLUR_PX, noise=NOISE, seed=SEED)
        meta |= {
            "image": image.name,
            "origin_px": "not meaningful after rotation",
            "rotation_deg": args.degraded,
            "blur_px": BLUR_PX,
            "noise_sd": NOISE,
            "noise_seed": SEED,
        }
        side = args.layout.with_name(f"{stem}.degraded.json")
    side.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{image}: {meta['columns']} x {meta['rows']} cells at {meta['px_per_m']} px per metre")
    return 0


if __name__ == "__main__":
    sys.exit(main())
