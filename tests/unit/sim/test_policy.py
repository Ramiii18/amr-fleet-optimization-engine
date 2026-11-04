"""Tests for the dispatch interface types."""

import pytest

from amr_optimizer.domain import TimeModel, TravelTimes
from amr_optimizer.sim import DispatchSnapshot, TravelPlanner
from amr_optimizer.warehouse import WarehouseGraph


@pytest.mark.parametrize(("load", "unload"), [(-1, 0), (0, -1)])
def test_snapshot_rejects_negative_handling_times(
    line_graph: WarehouseGraph, load: int, unload: int
) -> None:
    planner = TravelPlanner(TravelTimes(line_graph, speed_mps=1.0, time_model=TimeModel(0.1)))

    with pytest.raises(ValueError, match="non-negative"):
        DispatchSnapshot(0, (), (), planner, load_ticks=load, unload_ticks=unload)
