"""Static figures and an animation, drawn with matplotlib (M5).

Visual conventions (the reference palette of the project's data-visualization guidance):
- the data is the only loud ink: maps and axes use recessive grays, hairline gridlines;
- three categorical colours, in fixed order and never cycled: the first three palette slots,
  which are validated to stay distinguishable for every pair, in colour-vision deficiency
  too, in light and in dark mode;
- colour follows meaning: in the map and the animation it is what a robot is doing, in the
  scaling chart it is the policy;
- an ordered set of series (the recovery policies, from none to the most) is one blue ramp,
  light to dark, instead of more categorical colours; its lightest step still stands out from
  the surface;
- text is always in ink colours, never in a series colour; identity comes from the mark.
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")  # files only: never open a window

from matplotlib import pyplot as plt  # noqa: E402 - must follow the backend choice
from matplotlib.animation import FuncAnimation  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402
from matplotlib.collections import PathCollection  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, PathPatch  # noqa: E402
from matplotlib.path import Path  # noqa: E402

from amr_optimizer.sim import SimulationResult  # noqa: E402
from amr_optimizer.viz.motion import Activity, ActivityTimeline, position_at  # noqa: E402
from amr_optimizer.warehouse import NodeKind, WarehouseGraph  # noqa: E402


@dataclass(frozen=True, slots=True)
class Theme:
    """Surface, ink and series colours for one display mode."""

    surface: str
    primary: str
    secondary: str
    muted: str
    grid: str
    axis: str
    series: tuple[str, str, str]
    ramp: tuple[str, str, str]  # an ordinal blue ramp, weakest first


LIGHT = Theme(
    surface="#fcfcfb",
    primary="#0b0b0b",
    secondary="#52514e",
    muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a"),
    ramp=("#86b6ef", "#2a78d6", "#104281"),  # blue 250, 450, 650: no lighter than 250 on light
)
DARK = Theme(
    surface="#1a1a19",
    primary="#f0efec",
    secondary="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    series=("#3987e5", "#d95926", "#199e70"),
    ramp=("#184f95", "#3987e5", "#9ec5f4"),  # blue 600, 400, 200: no darker than 600 on dark
)

ACTIVITY_SLOT = {Activity.TO_PICKUP: 0, Activity.LOADED: 1}  # free robots are drawn in ink
ACTIVITY_LABEL = {
    Activity.TO_PICKUP: "driving to a pickup",
    Activity.LOADED: "carrying a load",
    Activity.FREE: "free (idle or parking)",
}
ENDPOINTS = (  # kind, marker, label
    (NodeKind.STATION, "s", "station"),
    (NodeKind.CHARGER, "^", "charger"),
    (NodeKind.PARKING, "D", "parking"),
)


def _fonts() -> AbstractContextManager[None]:
    """The system sans, with matplotlib's own as the fallback where it is missing."""
    return plt.rc_context(
        {"font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans"]}
    )


def _style(ax: Axes, theme: Theme) -> None:
    ax.set_facecolor(theme.surface)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=theme.muted, labelcolor=theme.secondary, length=0)


def plot_map(ax: Axes, graph: WarehouseGraph, theme: Theme) -> None:
    """The warehouse: corridors as hairlines, storage as small dots, other endpoints by shape."""
    _style(ax, theme)
    drawn: set[frozenset[str]] = set()
    for edge in graph.edges:
        key = frozenset((edge.source, edge.target))
        if key in drawn:
            continue
        drawn.add(key)
        a, b = graph.node(edge.source), graph.node(edge.target)
        ax.plot([a.x, b.x], [a.y, b.y], color=theme.axis, linewidth=0.8, zorder=1)
    storage = graph.nodes_of_kind(NodeKind.STORAGE)
    ax.scatter(
        [n.x for n in storage],
        [n.y for n in storage],
        s=4,
        color=theme.muted,
        zorder=2,
        label="storage",
    )
    for kind, marker, label in ENDPOINTS:
        nodes = graph.nodes_of_kind(kind)
        ax.scatter(
            [n.x for n in nodes],
            [n.y for n in nodes],
            s=36,
            marker=marker,
            color=theme.secondary,
            zorder=2,
            label=label,
        )
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def plot_robots(
    ax: Axes,
    result: SimulationResult,
    tick: float,
    theme: Theme,
    timeline: ActivityTimeline | None = None,
) -> PathCollection:
    """Every robot at ``tick``: a dot at its position, coloured by what it is doing."""
    timeline = timeline or ActivityTimeline(result.log)
    xs, ys, colors = _robot_frame(result, tick, theme, timeline)
    return ax.scatter(xs, ys, s=70, c=colors, edgecolors=theme.surface, linewidths=1.5, zorder=4)


def _robot_frame(
    result: SimulationResult, tick: float, theme: Theme, timeline: ActivityTimeline
) -> tuple[list[float], list[float], list[str]]:
    graph = result.scenario.graph
    xs, ys, colors = [], [], []
    for robot, path in result.trajectories.items():
        x, y = position_at(path, tick, graph)
        activity = timeline.at(robot, int(tick))
        slot = ACTIVITY_SLOT.get(activity)
        xs.append(x)
        ys.append(y)
        colors.append(theme.primary if slot is None else theme.series[slot])
    return xs, ys, colors


def _activity_legend(ax: Axes, theme: Theme) -> None:
    handles = [
        Line2D(
            [],
            [],
            linestyle="",
            marker="o",
            markersize=8,
            markeredgecolor=theme.surface,
            markerfacecolor=theme.primary
            if ACTIVITY_SLOT.get(a) is None
            else theme.series[ACTIVITY_SLOT[a]],
            label=ACTIVITY_LABEL[a],
        )
        for a in (Activity.TO_PICKUP, Activity.LOADED, Activity.FREE)
    ]
    infrastructure = [
        Line2D([], [], linestyle="", marker="o", markersize=3, color=theme.muted, label="storage"),
        *(
            Line2D(
                [],
                [],
                linestyle="",
                marker=marker,
                markersize=6,
                color=theme.secondary,
                label=label,
            )
            for _, marker, label in ENDPOINTS
        ),
    ]
    legend = ax.legend(
        handles=handles + infrastructure,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.01),
        ncols=4,
        frameon=False,
        fontsize=8,
        labelcolor=theme.secondary,
    )
    legend.set_zorder(5)


def map_figure(result: SimulationResult, tick: float, theme: Theme, title: str) -> Figure:
    """The map with every robot at ``tick``, and a legend."""
    with _fonts():
        figure, ax = plt.subplots(figsize=(7.2, 6.4), facecolor=theme.surface)
        plot_map(ax, result.scenario.graph, theme)
        plot_robots(ax, result, tick, theme)
        ax.set_title(title, color=theme.primary, fontsize=11, loc="left")
        _activity_legend(ax, theme)
        figure.tight_layout()
    return figure


def animate_run(
    result: SimulationResult,
    *,
    start_tick: int,
    end_tick: int,
    step_ticks: int,
    theme: Theme,
    title: str,
) -> tuple[Figure, FuncAnimation]:
    """An animation of the run from ``start_tick`` to ``end_tick``, one frame every
    ``step_ticks``. Save it with ``animation.save(path, writer="pillow", fps=...)``."""
    timeline = ActivityTimeline(result.log)
    tick_s = result.scenario.time_model.tick_s
    frames = list(range(start_tick, end_tick + 1, step_ticks))
    with _fonts():
        figure, ax = plt.subplots(figsize=(6.4, 5.8), facecolor=theme.surface)
        plot_map(ax, result.scenario.graph, theme)
        dots = plot_robots(ax, result, frames[0], theme, timeline)
        heading = ax.set_title("", color=theme.primary, fontsize=10, loc="left")
        _activity_legend(ax, theme)
        figure.tight_layout()

    def draw(tick: int) -> tuple[PathCollection]:
        xs, ys, colors = _robot_frame(result, tick, theme, timeline)
        dots.set_offsets(list(zip(xs, ys, strict=True)))
        dots.set_facecolor(colors)
        minutes, seconds = divmod(round(tick * tick_s), 60)
        heading.set_text(f"{title}   t = {minutes:02d}:{seconds:02d}")
        return (dots,)

    animation = FuncAnimation(figure, draw, frames=frames, blit=False)
    return figure, animation


SCALING_POLICIES = ("greedy", "insertion-fair", "cpsat-fair")


def scaling_figure(rows: Sequence[Mapping[str, str]], theme: Theme) -> Figure:
    """Mean completion time by fleet size, one panel per load, one line per policy.

    ``rows`` are the per-run rows written by ``scripts/benchmark_scaling.py``; each point is
    the mean over the seeds of a configuration.
    """
    loads = sorted({float(r["orders_per_hour"]) / int(r["robots"]) for r in rows})
    fleets = sorted({int(r["robots"]) for r in rows})
    with _fonts():
        figure, axes = plt.subplots(
            1, len(loads), figsize=(9.6, 4.2), facecolor=theme.surface, sharey=False
        )
        panels = list(axes) if len(loads) > 1 else [axes]
        for ax, per_robot in zip(panels, loads, strict=True):
            _style(ax, theme)
            ax.grid(axis="y", color=theme.grid, linewidth=0.8)
            ax.set_axisbelow(True)
            top = 0.0
            ends: list[float] = []
            for slot, policy in enumerate(SCALING_POLICIES):
                means = []
                for fleet in fleets:
                    values = [
                        float(r["completion_mean_s"])
                        for r in rows
                        if r["policy"] == policy
                        and int(r["robots"]) == fleet
                        and float(r["orders_per_hour"]) == per_robot * fleet
                    ]
                    means.append(sum(values) / len(values))
                top = max(top, *means)
                positions = list(range(len(fleets)))
                ax.plot(
                    positions,
                    means,
                    color=theme.series[slot],
                    linewidth=2,
                    solid_capstyle="round",
                    marker="o",
                    markersize=6,
                    markeredgecolor=theme.surface,
                    markeredgewidth=1.5,
                    label=policy,
                    zorder=3,
                )
                ends.append(means[-1])
            # Value labels at the line ends, only where they stay apart: converging labels
            # would collide, and then the legend and the benchmark table carry the values.
            gaps = [abs(a - b) for i, a in enumerate(ends) for b in ends[i + 1 :]]
            if min(gaps) >= 0.035 * top:  # about one line of label text at this figure size
                for value in ends:
                    ax.annotate(
                        f"{value:.0f} s",
                        (len(fleets) - 1, value),
                        xytext=(8, 0),
                        textcoords="offset points",
                        va="center",
                        fontsize=8,
                        color=theme.secondary,
                    )
            ax.set_xticks(range(len(fleets)), [str(f) for f in fleets])
            ax.set_xlim(-0.3, len(fleets) - 0.4)
            ax.set_ylim(0, top * 1.12)
            ax.set_xlabel("Robots", color=theme.secondary, fontsize=9)
            ax.set_title(
                f"{per_robot:g} orders per robot per hour",
                color=theme.primary,
                fontsize=10,
                loc="left",
            )
        panels[0].set_ylabel("Mean completion time (s)", color=theme.secondary, fontsize=9)
        panels[0].legend(frameon=False, fontsize=8, labelcolor=theme.secondary, loc="upper right")
        figure.suptitle(
            "Mean completion time with traffic, large map, 20 seeds per point",
            color=theme.primary,
            fontsize=11,
            x=0.01,
            ha="left",
        )
        figure.tight_layout()
    return figure


RECOVERY_ORDER = ("wait", "retime", "replan")
RECOVERY_POLICIES = ("greedy", "cpsat-fair")
RECOVERY_LEVELS = ("0.1% of entries", "0.5% of entries")


def _rounded_bar(ax: Axes, left: float, width: float, height: float, color: str) -> None:
    """A bar from the baseline with a 4 px rounded top and square bottom corners. Draw it once
    the axes' size and limits are final: the rounding is converted from pixels to data units."""
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    box = ax.get_window_extent()
    rx = min(4 * (x1 - x0) / box.width, width / 2)
    ry = min(4 * (y1 - y0) / box.height, height)
    k = 0.5523  # control-point factor of a quarter circle drawn as a cubic Bezier
    right, top = left + width, height
    vertices = [
        (left, 0.0),
        (left, top - ry),
        (left, top - ry + k * ry),
        (left + rx - k * rx, top),
        (left + rx, top),
        (right - rx, top),
        (right - rx + k * rx, top),
        (right, top - ry + k * ry),
        (right, top - ry),
        (right, 0.0),
        (left, 0.0),
    ]
    codes = [Path.MOVETO, Path.LINETO, *[Path.CURVE4] * 3, Path.LINETO, *[Path.CURVE4] * 3]
    codes += [Path.LINETO, Path.CLOSEPOLY]
    ax.add_patch(PathPatch(Path(vertices, codes), facecolor=color, edgecolor="none", zorder=2))


def recovery_figure(rows: Sequence[Mapping[str, str]], theme: Theme) -> Figure:
    """Mean completion time under each recovery policy, one panel per load (P3).

    ``rows`` are the per-run rows written by ``scripts/compare_breakdowns.py --csv``. Each bar
    is the mean over the seeds of a configuration; the dashed mark over each group is the same
    configuration without breakdowns.
    """

    def mean(rate: float, policy: str, level: str, recovery: str) -> float:
        values = [
            float(r["completion_mean_s"])
            for r in rows
            if float(r["orders_per_hour"]) == rate
            and r["policy"] == policy
            and r["breakdowns_level"] == level
            and r["recovery"] == recovery
        ]
        return sum(values) / len(values)

    rates = sorted({float(r["orders_per_hour"]) for r in rows})
    groups = [(p, level) for p in RECOVERY_POLICIES for level in RECOVERY_LEVELS]
    width, gap_px = 0.22, 2
    with _fonts():
        figure, axes = plt.subplots(1, len(rates), figsize=(9.6, 4.4), facecolor=theme.surface)
        panels = list(axes) if len(rates) > 1 else [axes]
        heights: list[list[list[float]]] = []
        for ax, rate in zip(panels, rates, strict=True):
            _style(ax, theme)
            ax.grid(axis="y", color=theme.grid, linewidth=0.8)
            ax.set_axisbelow(True)
            panel = [[mean(rate, p, level, rec) for rec in RECOVERY_ORDER] for p, level in groups]
            heights.append(panel)
            top = max(max(values) for values in panel)
            ax.set_xlim(-0.6, len(groups) - 0.4)
            ax.set_ylim(0, top * 1.08)
            ax.set_xticks(
                range(len(groups)),
                [f"{level.split()[0]}\n{p}" for p, level in groups],
                fontsize=8,
            )
            ax.set_xlabel(
                "Breakdowns per corridor entry, and dispatch policy",
                color=theme.secondary,
                fontsize=9,
            )
            ax.set_title(
                f"10 robots, {rate:g} orders/h", color=theme.primary, fontsize=10, loc="left"
            )
            for g, (policy, _) in enumerate(groups):  # the same configuration, no breakdowns
                baseline = mean(rate, policy, "none", "-")
                ax.plot(
                    [g - 1.5 * width - 0.04, g + 1.5 * width + 0.04],
                    [baseline, baseline],
                    color=theme.secondary,
                    linewidth=1.5,
                    linestyle=(0, (2, 1.5)),
                    zorder=3,
                )
        panels[0].set_ylabel("Mean completion time (s)", color=theme.secondary, fontsize=9)
        handles: list[Patch | Line2D] = [
            Patch(facecolor=theme.ramp[i], label=name) for i, name in enumerate(RECOVERY_ORDER)
        ]
        handles.append(
            Line2D(
                [],
                [],
                color=theme.secondary,
                linewidth=1.5,
                linestyle=(0, (2, 1.5)),
                label="no breakdowns",
            )
        )
        figure.legend(
            handles=handles,
            loc="upper right",
            ncol=4,
            frameon=False,
            fontsize=8,
            labelcolor=theme.secondary,
            bbox_to_anchor=(0.995, 0.955),
        )
        figure.suptitle(
            "Recovering from breakdowns: mean completion time, large map, 20 seeds per bar",
            color=theme.primary,
            fontsize=11,
            x=0.01,
            ha="left",
        )
        figure.tight_layout(rect=(0, 0, 1, 0.94))
        for ax, panel in zip(panels, heights, strict=True):  # bars last: the layout is final
            gap = gap_px * (ax.get_xlim()[1] - ax.get_xlim()[0]) / ax.get_window_extent().width
            for g, values in enumerate(panel):
                for i, value in enumerate(values):
                    left = g + (i - 1.5) * width + gap / 2
                    _rounded_bar(ax, left, width - gap, value, theme.ramp[i])
    return figure
