"""Tests for TravelPlanner."""

from amr_optimizer.domain import TimeModel, TravelTimes
from amr_optimizer.sim import TravelPlanner
from amr_optimizer.warehouse import NodeId, WarehouseGraph

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")


def _planner(graph: WarehouseGraph) -> TravelPlanner:
    return TravelPlanner(TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))


def test_route_and_ticks(line_graph: WarehouseGraph) -> None:
    planner = _planner(line_graph)

    route = planner.route(A, C)
    assert route is not None
    assert (route.nodes, route.length_m) == ((A, B, C), 2.0)
    assert planner.ticks(A, C) == 20


def test_same_node_is_a_zero_tick_route(line_graph: WarehouseGraph) -> None:
    planner = _planner(line_graph)

    route = planner.route(B, B)
    assert route is not None and route.nodes == (B,)
    assert planner.ticks(B, B) == 0


def test_unreachable_target_gives_none(disconnected_graph: WarehouseGraph) -> None:
    planner = _planner(disconnected_graph)

    assert planner.route(B, A) is None  # A -> B is one-way
    assert planner.ticks(A, C) is None


def test_follows_one_way_edges(one_way_detour_graph: WarehouseGraph) -> None:
    planner = _planner(one_way_detour_graph)

    assert planner.ticks(B, A) == 20
    assert planner.ticks(A, B) == 60  # around the loop


def test_answers_are_cached(line_graph: WarehouseGraph) -> None:
    planner = _planner(line_graph)

    assert planner.route(A, C) is planner.route(A, C)
    assert planner.travel_times.speed_mps == 1.0
