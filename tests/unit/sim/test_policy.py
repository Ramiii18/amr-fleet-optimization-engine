"""Tests for the dispatch interface types."""

import pytest

from amr_optimizer.domain import RobotId, RobotState, TimeModel, TravelTimes
from amr_optimizer.sim import DispatchSnapshot, TravelPlanner
from amr_optimizer.warehouse import NodeId, WarehouseGraph


@pytest.mark.parametrize(("load", "unload"), [(-1, 0), (0, -1)])
def test_snapshot_rejects_negative_handling_times(
    line_graph: WarehouseGraph, load: int, unload: int
) -> None:
    planner = TravelPlanner(TravelTimes(line_graph, speed_mps=1.0, time_model=TimeModel(0.1)))

    with pytest.raises(ValueError, match="non-negative"):
        DispatchSnapshot(0, (), (), planner, load_ticks=load, unload_ticks=unload)


def test_robots_out_of_service_are_not_offered(line_graph: WarehouseGraph) -> None:
    planner = TravelPlanner(TravelTimes(line_graph, speed_mps=1.0, time_model=TimeModel(0.1)))
    r1, r2 = RobotId("R1"), RobotId("R2")
    robots = (RobotState.idle(r1, NodeId("A")), RobotState.idle(r2, NodeId("C")))

    snapshot = DispatchSnapshot(
        0, robots, (), planner, load_ticks=0, unload_ticks=0, unavailable=frozenset({r1})
    )

    assert [robot.robot_id for robot in snapshot.idle_robots] == [r2]
