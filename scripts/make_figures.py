"""Regenerate every figure in docs/figures from code and committed results (M5).

- ``scaling-{light,dark}.png``: mean completion time by fleet size, per load and policy, from
  the per-run results of ``scripts/benchmark_scaling.py`` (``docs/results/scaling.csv``).
- ``recovery-{light,dark}.png``: mean completion time under each recovery policy from
  breakdowns, from the per-run results of ``scripts/compare_breakdowns.py``
  (``docs/results/breakdowns.csv``).
- ``map-{light,dark}.png``: the large map during one run with traffic, every robot coloured by
  what it is doing.
- ``traffic.gif``: two minutes of that run, one frame per simulated second.

The run is fixed (10 robots, 300 orders per hour, seed 0, cpsat-fair, with traffic), so the
figures are the same every time. Requires the ``viz`` extra (matplotlib).

Usage (from the repository root):
    .venv\\Scripts\\python.exe scripts\\make_figures.py
"""

import csv
from pathlib import Path

from matplotlib import pyplot as plt

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.viz import DARK, LIGHT, animate_run, map_figure, recovery_figure, scaling_figure
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "docs" / "results" / "scaling.csv"
BREAKDOWNS = ROOT / "docs" / "results" / "breakdowns.csv"
FIGURES = ROOT / "docs" / "figures"
ROBOTS, ORDERS_PER_HOUR, SEED, POLICY = 10, 300.0, 0, "cpsat-fair"
SNAPSHOT_TICK = 9_000  # 15 minutes in: the fleet is busy
ANIMATION_TICKS = 1_200  # two minutes
DPI = 120


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)

    with RESULTS.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        figure = scaling_figure(rows, theme)
        figure.savefig(FIGURES / f"scaling-{name}.png", dpi=DPI, facecolor=theme.surface)
        plt.close(figure)

    with BREAKDOWNS.open(encoding="utf-8") as file:
        breakdown_rows = list(csv.DictReader(file))
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        figure = recovery_figure(breakdown_rows, theme)
        figure.savefig(FIGURES / f"recovery-{name}.png", dpi=DPI, facecolor=theme.surface)
        plt.close(figure)

    graph = generate_grid_warehouse(PRESETS["large"])
    spec = ScenarioSpec(robots=ROBOTS, orders_per_hour=ORDERS_PER_HOUR)
    result = simulate(generate_scenario(graph, spec, seed=SEED), POLICIES[POLICY](), traffic=True)
    title = f"{ROBOTS} robots, {ORDERS_PER_HOUR:g} orders/h, {POLICY}, with traffic"
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        figure = map_figure(result, SNAPSHOT_TICK, theme, f"{title}, t = 15:00")
        figure.savefig(FIGURES / f"map-{name}.png", dpi=DPI, facecolor=theme.surface)
        plt.close(figure)

    figure, animation = animate_run(
        result,
        start_tick=SNAPSHOT_TICK,
        end_tick=SNAPSHOT_TICK + ANIMATION_TICKS,
        step_ticks=10,
        theme=LIGHT,
        title=title,
    )
    animation.save(
        FIGURES / "traffic.gif",
        writer="pillow",
        fps=10,
        dpi=80,
        savefig_kwargs={"facecolor": LIGHT.surface},
    )
    plt.close(figure)

    for path in sorted(FIGURES.iterdir()):
        print(f"{path.relative_to(ROOT).as_posix()}: {path.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
