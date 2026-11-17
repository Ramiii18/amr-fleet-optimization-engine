"""Draw a layout's compiled map beside the floor plan it was made from, at the same scale (D42).

    .venv\\Scripts\\python.exe scripts\\render_layout.py hall.layout.json --out compare.png ^
        --plan hall.png --px-per-m 24 --origin-px 48 48

Three panels share one frame in metres: the plan, the compiled map (``viz.plot_map``), and the map
drawn over the plan, half transparent, with the layout's cell borders. ``--px-per-m`` is the plan's
scale, read from its scale bar; ``--origin-px`` is the pixel (x, y) of the top-left corner of the
layout's first cell, usually the outer corner of the building. Without ``--plan`` only the map is
drawn. Needs the ``viz`` extra (matplotlib). The output is deterministic.

This is the check of step 5 of ``docs/LAYOUT_FROM_FLOOR_PLAN.md``: open the image and compare.
"""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from amr_optimizer.layout import compile_layout, load_layout  # noqa: E402
from amr_optimizer.viz.plots import LIGHT, plot_map  # noqa: E402

OUT_PX_PER_M = 22  # resolution of the comparison image
PAD_M = 1.0


def render(
    layout_path: Path,
    out: Path,
    plan: Path | None = None,
    px_per_m: float | None = None,
    origin_px: tuple[float, float] = (0.0, 0.0),
    rotate_deg: float = 0.0,
) -> None:
    layout = load_layout(layout_path)
    graph = compile_layout(layout)
    cell, rows, cols = layout.cell_m, layout.rows, layout.columns
    # Node (r, c) is at x = c * cell, y = (rows - 1 - r) * cell; its cell spans half a cell around.
    x0, x1 = -cell / 2 - PAD_M, (cols - 0.5) * cell + PAD_M
    y0, y1 = -cell / 2 - PAD_M, (rows - 0.5) * cell + PAD_M
    panels = 3 if plan is not None else 1
    width, height = (x1 - x0) * OUT_PX_PER_M / 100, (y1 - y0) * OUT_PX_PER_M / 100 + 0.4
    fig, axes = plt.subplots(1, panels, figsize=(width * panels, height), dpi=100, squeeze=False)
    image = extent = None
    if plan is not None:
        if px_per_m is None or px_per_m <= 0:
            raise ValueError("--plan needs a positive --px-per-m")
        image = plt.imread(plan)
        if rotate_deg:  # straighten a tilted plan first; --origin-px refers to the result
            from scipy import ndimage

            image = ndimage.rotate(image, rotate_deg, axes=(1, 0), reshape=True, order=1, cval=1.0)
            image = image.clip(0.0, 1.0)
        h, w = image.shape[:2]
        left = -cell / 2 - origin_px[0] / px_per_m
        top = (rows - 0.5) * cell + origin_px[1] / px_per_m
        extent = (left, left + w / px_per_m, top - h / px_per_m, top)
    titles = ["Plan", "Compiled map", "Map over plan, with the layout's cells"]
    for i, ax in enumerate(axes[0]):
        if image is not None and i != 1:
            ax.imshow(image, extent=extent, alpha=1.0 if i == 0 else 0.55, interpolation="nearest")
        if i != 0 or image is None:
            plot_map(ax, graph, LIGHT)
            if image is not None and i == 2:
                ax.set_facecolor("white")
        if i == 2:
            for k in range(cols + 1):
                ax.plot(
                    [(k - 0.5) * cell] * 2,
                    [-cell / 2, (rows - 0.5) * cell],
                    color="#c03030",
                    linewidth=0.3,
                    alpha=0.6,
                )
            for k in range(rows + 1):
                ax.plot(
                    [-cell / 2, (cols - 0.5) * cell],
                    [(k - 0.5) * cell] * 2,
                    color="#c03030",
                    linewidth=0.3,
                    alpha=0.6,
                )
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(titles[i] if image is not None else layout_path.name, fontsize=8, loc="left")
    fig.tight_layout(pad=0.3)
    fig.savefig(out, dpi=100, metadata={"Software": None}, facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("layout", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plan", type=Path, help="the floor plan image (PNG)")
    parser.add_argument("--px-per-m", type=float, help="the plan's pixels per metre")
    parser.add_argument(
        "--origin-px",
        type=float,
        nargs=2,
        default=(0.0, 0.0),
        metavar=("X", "Y"),
        help="the plan pixel of the top-left corner of the layout's first cell",
    )
    parser.add_argument(
        "--rotate-deg",
        type=float,
        default=0.0,
        help="rotate the plan counter-clockwise by this many degrees before drawing it, to "
        "straighten a tilted plan; --origin-px then refers to the straightened image",
    )
    args = parser.parse_args()
    try:
        render(
            args.layout, args.out, args.plan, args.px_per_m, tuple(args.origin_px), args.rotate_deg
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"{args.out} written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
