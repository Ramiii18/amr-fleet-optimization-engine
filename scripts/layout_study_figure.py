"""Figure of the layout study: the five layouts and their simulated mean completion times.

    .venv\\Scripts\\python.exe scripts\\layout_study_figure.py docs\\results\\layout_study.json ^
        experiments\\layout_study\\candidates\\screening.json

Writes ``docs/figures/layout_study-light.png`` and ``-dark.png``: one panel per layout (the map,
stations and chargers marked), titled with its station proxy and, per family, its mean completion
time under ``cpsat-fair`` over the seeds. Values are read from the results and screening files.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from amr_optimizer.layout import load_map_file  # noqa: E402
from amr_optimizer.viz.plots import DARK, LIGHT, Theme, plot_map  # noqa: E402
from amr_optimizer.warehouse import NodeKind  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
POLICY = "cpsat-fair"


def figure(results: dict, screening: dict, base: Path, theme: Theme, out: Path) -> None:  # type: ignore[type-arg]
    experiment = results["experiment"]
    proxy = {row["name"]: row["station_round_trip_m"] for row in screening["candidates"]}
    families = [f["name"] for f in experiment["families"]]
    layouts = experiment["layouts"]
    fig, axes = plt.subplots(
        1, len(layouts), figsize=(3.4 * len(layouts), 3.4), facecolor=theme.surface
    )
    for ax, layout in zip(axes, layouts, strict=True):
        graph = load_map_file(base / layout["map"])
        plot_map(ax, graph, theme)
        for kind, colour, size in (
            (NodeKind.STATION, theme.ramp[2], 40),
            (NodeKind.CHARGER, theme.ramp[0], 30),
        ):
            nodes = graph.nodes_of_kind(kind)
            ax.scatter(
                [n.x for n in nodes],
                [n.y for n in nodes],
                s=size,
                color=colour,
                zorder=5,
                marker="s" if kind is NodeKind.STATION else "o",
            )
        means = []
        for family in families:
            values = [
                float(r["completion_mean_s"])
                for r in results["rows"]
                if r["layout"] == layout["name"] and r["family"] == family and r["policy"] == POLICY
            ]
            means.append(f"{family} {statistics.fmean(values):.0f} s")
        ax.set_title(
            f"{layout['name']}: proxy {proxy[layout['name']]:.1f} m\n" + ", ".join(means),
            fontsize=8,
            color=theme.primary,
            loc="left",
        )
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(out, dpi=110, metadata={"Software": None}, facecolor=theme.surface)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("screening", type=Path)
    args = parser.parse_args()
    results = json.loads(args.results.read_text(encoding="utf-8"))
    screening = json.loads(args.screening.read_text(encoding="utf-8"))
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        out = ROOT / "docs" / "figures" / f"layout_study-{name}.png"
        figure(results, screening, args.screening.parent, theme, out)
        print(f"{out} written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
