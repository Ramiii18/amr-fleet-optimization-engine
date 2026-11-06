"""Tests for the figures: structure, not pixels (the matplotlib Agg backend draws offscreen)."""

from pathlib import Path

import numpy as np
import pytest
from matplotlib import pyplot as plt

from amr_optimizer.dispatch import GreedyDispatch
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import SimulationResult, simulate
from amr_optimizer.viz import DARK, LIGHT, Theme, animate_run, map_figure, plot_map, plot_robots
from amr_optimizer.viz.plots import recovery_figure, scaling_figure
from amr_optimizer.warehouse import PRESETS, NodeKind, generate_grid_warehouse

THEMES = [LIGHT, DARK]


@pytest.fixture(scope="module")
def run() -> SimulationResult:
    graph = generate_grid_warehouse(PRESETS["small"])
    spec = ScenarioSpec(robots=3, orders_per_hour=120, duration_s=300)
    return simulate(generate_scenario(graph, spec, seed=1), GreedyDispatch(), traffic=True)


@pytest.mark.parametrize("theme", THEMES, ids=["light", "dark"])
def test_the_map_draws_each_corridor_once_and_each_endpoint_kind(
    run: SimulationResult, theme: Theme
) -> None:
    graph = run.scenario.graph
    corridors = {frozenset((e.source, e.target)) for e in graph.edges}
    figure, ax = plt.subplots()

    plot_map(ax, graph, theme)
    robots = plot_robots(ax, run, 100, theme)

    assert len(ax.lines) == len(corridors)
    kinds = (NodeKind.STORAGE, NodeKind.STATION, NodeKind.CHARGER, NodeKind.PARKING)
    sizes = [len(np.asarray(c.get_offsets())) for c in ax.collections[:4]]
    assert sizes == [len(graph.nodes_of_kind(k)) for k in kinds]
    assert len(np.asarray(robots.get_offsets())) == len(run.trajectories)
    plt.close(figure)


def test_the_map_figure_has_a_title_and_a_legend(run: SimulationResult) -> None:
    figure = map_figure(run, 100, LIGHT, "Small map")

    ax = figure.axes[0]
    assert ax.get_title(loc="left") == "Small map"
    assert ax.get_legend() is not None
    plt.close(figure)


def test_the_scaling_figure_has_one_panel_per_load_and_one_line_per_policy() -> None:
    rows = [
        {
            "robots": str(robots),
            "orders_per_hour": str(robots * per_robot),
            "policy": policy,
            "seed": str(seed),
            "completion_mean_s": str(100 + robots + per_robot + seed + offset),
        }
        for robots in (3, 10)
        for per_robot in (20, 30)
        for offset, policy in enumerate(("greedy", "insertion-fair", "cpsat-fair"))
        for seed in range(2)
    ]

    figure = scaling_figure(rows, LIGHT)

    assert len(figure.axes) == 2
    for ax in figure.axes:
        assert len(ax.lines) == 3
        assert ax.get_ylim()[0] == 0
    legend = figure.axes[0].get_legend()
    assert legend is not None
    assert [t.get_text() for t in legend.get_texts()] == ["greedy", "insertion-fair", "cpsat-fair"]
    # Each point is the mean over seeds: 3 robots, 20 per robot, greedy: (123 + 124) / 2.
    assert np.asarray(figure.axes[0].lines[0].get_ydata())[0] == pytest.approx(123.5)
    plt.close(figure)


def test_an_animation_can_be_saved(run: SimulationResult, tmp_path: Path) -> None:
    figure, animation = animate_run(
        run, start_tick=0, end_tick=40, step_ticks=20, theme=LIGHT, title="Small map"
    )
    path = tmp_path / "run.gif"

    animation.save(path, writer="pillow", fps=2)

    assert path.stat().st_size > 0
    plt.close(figure)


@pytest.mark.parametrize("theme", THEMES, ids=["light", "dark"])
def test_the_recovery_figure_has_a_bar_per_policy_and_a_baseline_per_group(theme: Theme) -> None:
    rows = []
    for rate in (200, 300):
        for policy in ("greedy", "cpsat-fair"):
            for seed in range(2):
                base = {"orders_per_hour": str(rate), "policy": policy, "seed": str(seed)}
                rows.append(
                    base | {"breakdowns_level": "none", "recovery": "-", "completion_mean_s": "100"}
                )
                for level in ("0.1% of entries", "0.5% of entries"):
                    for i, recovery in enumerate(("wait", "retime", "replan")):
                        value = str(300 - 50 * i + seed)
                        rows.append(
                            base
                            | {
                                "breakdowns_level": level,
                                "recovery": recovery,
                                "completion_mean_s": value,
                            }
                        )

    figure = recovery_figure(rows, theme)

    assert len(figure.axes) == 2
    for ax in figure.axes:
        assert len(ax.patches) == 4 * 3  # 4 groups of 3 recovery policies
        assert len(ax.lines) == 4  # one "no breakdowns" mark per group
        assert ax.get_ylim()[0] == 0
        tops = sorted(
            round(float(np.asarray(patch.get_path().vertices)[:, 1].max()), 3)
            for patch in ax.patches
        )
        assert tops == [200.5] * 4 + [250.5] * 4 + [300.5] * 4  # means over the two seeds
        assert {round(float(np.asarray(line.get_ydata())[0]), 3) for line in ax.lines} == {100.0}
    legend_texts = [t.get_text() for t in figure.legends[0].get_texts()]
    assert legend_texts == ["wait", "retime", "replan", "no breakdowns"]
    plt.close(figure)
