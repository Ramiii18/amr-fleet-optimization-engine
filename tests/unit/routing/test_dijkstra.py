"""Hand-computed cases for Dijkstra (shortest_path and shortest_path_lengths)."""

import math

import pytest

from amr_optimizer.routing import shortest_path, shortest_path_lengths, validate_route
from amr_optimizer.warehouse import NodeId, UnknownNodeError, WarehouseGraph

A, B, C, D = NodeId("A"), NodeId("B"), NodeId("C"), NodeId("D")
S, T, U = NodeId("S"), NodeId("T"), NodeId("U")


def test_straight_line(line_graph: WarehouseGraph) -> None:
    result = shortest_path(line_graph, A, C)

    assert result.route is not None
    assert result.route.nodes == (A, B, C)
    assert result.route.length_m == 2.0
    validate_route(line_graph, result.route)


def test_source_equals_target_is_a_zero_length_route(line_graph: WarehouseGraph) -> None:
    result = shortest_path(line_graph, B, B)

    assert result.route is not None
    assert result.route.nodes == (B,)
    assert result.route.length_m == 0.0
    assert result.expanded == 1


def test_picks_the_shorter_branch(diamond_graph: WarehouseGraph) -> None:
    result = shortest_path(diamond_graph, S, T)

    assert result.route is not None
    assert result.route.nodes == (S, U, T)
    assert result.route.length_m == pytest.approx(2 * math.sqrt(2))


def test_respects_one_way_edges(one_way_detour_graph: WarehouseGraph) -> None:
    forward = shortest_path(one_way_detour_graph, A, B)
    backward = shortest_path(one_way_detour_graph, B, A)

    assert forward.route is not None
    assert forward.route.nodes == (A, D, C, B)
    assert forward.route.length_m == 6.0
    assert backward.route is not None
    assert backward.route.nodes == (B, A)
    assert backward.route.length_m == 2.0


def test_unreachable_target_returns_no_route(disconnected_graph: WarehouseGraph) -> None:
    assert shortest_path(disconnected_graph, A, C).route is None  # no edge at all
    assert shortest_path(disconnected_graph, B, A).route is None  # only A -> B exists


def test_unreachable_search_reports_what_it_explored(disconnected_graph: WarehouseGraph) -> None:
    result = shortest_path(disconnected_graph, A, C)

    assert result.expanded == 2  # A and B settled, then the queue ran empty


def test_stops_as_soon_as_target_is_settled(line_graph: WarehouseGraph) -> None:
    assert shortest_path(line_graph, A, B).expanded == 2  # A, B; C never settled


@pytest.mark.parametrize(("source", "target"), [("Z", "A"), ("A", "Z")])
def test_unknown_node_raises(line_graph: WarehouseGraph, source: str, target: str) -> None:
    with pytest.raises(UnknownNodeError, match="'Z'"):
        shortest_path(line_graph, NodeId(source), NodeId(target))


def test_equal_length_ties_resolve_deterministically(tie_graph: WarehouseGraph) -> None:
    results = [shortest_path(tie_graph, S, T) for _ in range(5)]

    # S -> U is listed before S -> D, so U is reached first; a later equal-length
    # alternative never replaces an existing parent (strict improvement only).
    assert all(result.route is not None for result in results)
    assert {result.route.nodes for result in results if result.route} == {(S, U, T)}


def test_blocked_edge_forces_a_detour(diamond_graph: WarehouseGraph) -> None:
    result = shortest_path(diamond_graph, S, T, blocked_edges={(U, T)})

    assert result.route is not None
    assert result.route.nodes == (S, NodeId("D"), T)
    assert result.route.length_m == 4.0
    validate_route(diamond_graph, result.route, blocked_edges={(U, T)})


def test_blocking_every_way_makes_target_unreachable(line_graph: WarehouseGraph) -> None:
    assert shortest_path(line_graph, A, C, blocked_edges={(B, C)}).route is None


def test_blocking_a_non_existent_edge_has_no_effect(line_graph: WarehouseGraph) -> None:
    result = shortest_path(line_graph, A, C, blocked_edges={(A, C)})

    assert result.route is not None
    assert result.route.nodes == (A, B, C)


def test_one_to_all_distances(one_way_detour_graph: WarehouseGraph) -> None:
    assert shortest_path_lengths(one_way_detour_graph, A) == {A: 0.0, D: 2.0, C: 4.0, B: 6.0}


def test_one_to_all_omits_unreachable_nodes(disconnected_graph: WarehouseGraph) -> None:
    assert shortest_path_lengths(disconnected_graph, A) == {A: 0.0, B: 1.0}


def test_one_to_all_respects_blocked_edges(line_graph: WarehouseGraph) -> None:
    assert shortest_path_lengths(line_graph, A, blocked_edges={(B, C)}) == {A: 0.0, B: 1.0}


def test_one_to_all_unknown_source_raises(line_graph: WarehouseGraph) -> None:
    with pytest.raises(UnknownNodeError):
        shortest_path_lengths(line_graph, NodeId("Z"))
