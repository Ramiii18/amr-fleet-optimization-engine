"""Tests for Route, reconstruct_path and validate_route."""

import math

import pytest

from amr_optimizer.routing import InvalidRouteError, Route, reconstruct_path, validate_route
from amr_optimizer.warehouse import NodeId, WarehouseGraph

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")


def test_route_exposes_source_and_target() -> None:
    route = Route((A, B, C), 2.0)

    assert (route.source, route.target) == (A, C)


def test_single_node_route_is_allowed() -> None:
    route = Route((A,), 0.0)

    assert route.source == route.target == A


def test_empty_route_is_rejected() -> None:
    with pytest.raises(InvalidRouteError):
        Route((), 0.0)


def test_reconstruct_path_follows_parents_back_to_source() -> None:
    parents = {B: A, C: B}

    assert reconstruct_path(parents, A, C) == (A, B, C)
    assert reconstruct_path(parents, A, A) == (A,)


def test_valid_route_passes(line_graph: WarehouseGraph) -> None:
    validate_route(line_graph, Route((A, B, C), 2.0))
    validate_route(line_graph, Route((A,), 0.0))


def test_route_with_unknown_node_is_invalid(line_graph: WarehouseGraph) -> None:
    with pytest.raises(InvalidRouteError, match="unknown node 'Z'"):
        validate_route(line_graph, Route((A, NodeId("Z")), 1.0))


def test_route_against_edge_direction_is_invalid(one_way_detour_graph: WarehouseGraph) -> None:
    with pytest.raises(InvalidRouteError, match="non-existent edge 'A'->'B'"):
        validate_route(one_way_detour_graph, Route((A, B), 2.0))


def test_route_skipping_a_node_is_invalid(line_graph: WarehouseGraph) -> None:
    with pytest.raises(InvalidRouteError, match="non-existent edge 'A'->'C'"):
        validate_route(line_graph, Route((A, C), 2.0))


def test_route_using_blocked_edge_is_invalid(line_graph: WarehouseGraph) -> None:
    with pytest.raises(InvalidRouteError, match="blocked edge 'B'->'C'"):
        validate_route(line_graph, Route((A, B, C), 2.0), blocked_edges={(B, C)})


@pytest.mark.parametrize("wrong_length", [1.9, 2.1, 0.0, math.inf])
def test_route_with_wrong_length_is_invalid(
    line_graph: WarehouseGraph, wrong_length: float
) -> None:
    with pytest.raises(InvalidRouteError, match="does not match"):
        validate_route(line_graph, Route((A, B, C), wrong_length))
