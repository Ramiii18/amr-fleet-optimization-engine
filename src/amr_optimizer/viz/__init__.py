"""Figures and animations of maps, runs and benchmark results (M5). Requires matplotlib.

Install with the ``viz`` extra: ``pip install -e ".[viz]"``. Nothing else in the package
imports this one, so the engine itself never needs matplotlib.
"""

from amr_optimizer.viz.motion import Activity, ActivityTimeline, activity_at, position_at
from amr_optimizer.viz.plots import (
    DARK,
    LIGHT,
    Theme,
    animate_run,
    map_figure,
    plot_map,
    plot_robots,
    scaling_figure,
)

__all__ = [
    "DARK",
    "LIGHT",
    "Activity",
    "ActivityTimeline",
    "Theme",
    "activity_at",
    "animate_run",
    "map_figure",
    "plot_map",
    "plot_robots",
    "position_at",
    "scaling_figure",
]
