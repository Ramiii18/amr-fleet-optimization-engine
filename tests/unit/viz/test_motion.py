"""Tests for positions and activities over time.

``line_graph``: A (0, 0) - B (1, 0) - C (2, 0), 1 m apart; 10 ticks per edge at 1 m/s and 0.1 s.
"""

import pytest

from amr_optimizer.dispatch import GreedyDispatch
from amr_optimizer.domain import OrderId, RobotId, RobotState, TimeModel, TransportOrder
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.traffic import TimedPath, Visit
from amr_optimizer.viz import Activity, ActivityTimeline, activity_at, position_at
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
R1 = RobotId("R1")


@pytest.mark.parametrize(
    ("tick", "expected"),
    [
        (-1, (0.0, 0.0)),  # before the path: at its first node
        (0, (0.0, 0.0)),
        (5, (0.0, 0.0)),  # waiting at A until 5
        (10, (0.5, 0.0)),  # halfway along A -> B (departed 5, arrives 15)
        (15, (1.0, 0.0)),
        (20, (1.0, 0.0)),  # at B until 20
        (27.5, (1.75, 0.0)),  # three quarters of B -> C, between whole ticks
        (35, (2.0, 0.0)),  # after the path: at its last node
    ],
)
def test_position_is_interpolated_along_edges(
    line_graph: WarehouseGraph, tick: float, expected: tuple[float, float]
) -> None:
    path = TimedPath((Visit(A, 0, 5), Visit(B, 15, 20), Visit(C, 30, 30)))

    assert position_at(path, tick, line_graph) == pytest.approx(expected)


def test_activity_follows_the_job(line_graph: WarehouseGraph) -> None:
    # R1 at A takes O1 from B to C at tick 0: assigned 0, loaded at 15, delivered at 30.
    scenario = Scenario(
        graph=line_graph,
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=5,
        unload_ticks=5,
        robots=(RobotState.idle(R1, A),),
        orders=(TransportOrder(OrderId("O1"), B, C, 0),),
    )
    log = simulate(scenario, GreedyDispatch()).log
    timeline = ActivityTimeline(log)

    expected = {0: Activity.TO_PICKUP, 14: Activity.TO_PICKUP, 15: Activity.LOADED}
    expected |= {29: Activity.LOADED, 30: Activity.FREE, 100: Activity.FREE}
    for tick, activity in expected.items():
        assert activity_at(log, R1, tick) is activity
        assert timeline.at(R1, tick) is activity
    assert timeline.at(RobotId("nobody"), 10) is Activity.FREE


@pytest.mark.parametrize("traffic", [False, True])
def test_the_timeline_agrees_with_the_reference_definition(traffic: bool) -> None:
    graph = generate_grid_warehouse(PRESETS["small"])
    spec = ScenarioSpec(robots=4, orders_per_hour=150, duration_s=600)
    result = simulate(generate_scenario(graph, spec, seed=4), GreedyDispatch(), traffic=traffic)
    timeline = ActivityTimeline(result.log)

    for robot in result.trajectories:
        for tick in range(0, result.end_tick + 1, 37):
            assert timeline.at(robot, tick) is activity_at(result.log, robot, tick)
