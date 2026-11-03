"""Tests for the warehouse model: Node, Edge and WarehouseGraph validation and queries."""

import dataclasses
import math

import pytest

from amr_optimizer.warehouse import (
    Edge,
    MapValidationError,
    Node,
    NodeId,
    NodeKind,
    UnknownNodeError,
    WarehouseGraph,
)

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")


# --- Node ---------------------------------------------------------------------------------


def test_node_stores_its_fields_and_defaults_to_intersection() -> None:
    node = Node(NodeId("A1"), x=2.0, y=3.5)

    assert node.id == "A1"
    assert (node.x, node.y) == (2.0, 3.5)
    assert node.kind is NodeKind.INTERSECTION


def test_node_is_immutable() -> None:
    node = Node(NodeId("A1"), x=0.0, y=0.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        node.x = 5.0  # type: ignore[misc]  # mypy already rejects this; we check runtime too


def test_equal_nodes_compare_equal_and_hash_the_same() -> None:
    first = Node(A, 1.0, 2.0, NodeKind.CHARGER)
    second = Node(A, 1.0, 2.0, NodeKind.CHARGER)

    assert first == second
    assert len({first, second}) == 1


def test_node_rejects_empty_id() -> None:
    with pytest.raises(MapValidationError, match="non-empty"):
        Node(NodeId(""), 0.0, 0.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_node_rejects_non_finite_coordinates(bad: float) -> None:
    with pytest.raises(MapValidationError, match="finite"):
        Node(A, bad, 0.0)
    with pytest.raises(MapValidationError, match="finite"):
        Node(A, 0.0, bad)


def test_node_distance_is_euclidean() -> None:
    origin = Node(A, 0.0, 0.0)
    other = Node(B, 3.0, 4.0)

    assert origin.distance_to(other) == 5.0
    assert other.distance_to(origin) == 5.0
    assert origin.distance_to(origin) == 0.0


# --- Edge ---------------------------------------------------------------------------------


def test_edge_stores_its_fields_and_key() -> None:
    edge = Edge(A, B, 2.5)

    assert (edge.source, edge.target, edge.length_m) == (A, B, 2.5)
    assert edge.key == (A, B)


def test_edges_are_directed() -> None:
    assert Edge(A, B, 1.0) != Edge(B, A, 1.0)


def test_edge_rejects_self_loop() -> None:
    with pytest.raises(MapValidationError, match="self-loop"):
        Edge(A, A, 1.0)


@pytest.mark.parametrize("bad_length", [0.0, -1.0, math.nan, math.inf])
def test_edge_rejects_invalid_length(bad_length: float) -> None:
    with pytest.raises(MapValidationError, match="length"):
        Edge(A, B, bad_length)


def test_map_validation_error_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        Edge(A, A, 1.0)


# --- WarehouseGraph: construction and validation -----------------------------------------


def test_graph_reports_size_membership_and_keeps_input_order(line_graph: WarehouseGraph) -> None:
    assert len(line_graph) == 3
    assert line_graph.num_edges == 4
    assert A in line_graph
    assert NodeId("Z") not in line_graph
    assert [node.id for node in line_graph] == [A, B, C]
    assert [edge.key for edge in line_graph.edges] == [(A, B), (B, A), (B, C), (C, B)]


def test_graph_accepts_one_shot_iterables() -> None:
    nodes = (Node(NodeId(name), float(i), 0.0) for i, name in enumerate("AB"))
    edges = iter([Edge(A, B, 1.0)])

    graph = WarehouseGraph(nodes, edges)

    assert len(graph) == 2
    assert graph.num_edges == 1


def test_empty_graph_is_valid() -> None:
    graph = WarehouseGraph([], [])

    assert len(graph) == 0
    assert graph.edges == ()


def test_graph_rejects_duplicate_node_ids() -> None:
    with pytest.raises(MapValidationError, match="duplicate node id 'A'"):
        WarehouseGraph([Node(A, 0, 0), Node(A, 1, 1)], [])


@pytest.mark.parametrize(("source", "target"), [("A", "Z"), ("Z", "A")])
def test_graph_rejects_edge_to_unknown_node(source: str, target: str) -> None:
    with pytest.raises(MapValidationError, match="unknown node 'Z'"):
        WarehouseGraph([Node(A, 0, 0)], [Edge(NodeId(source), NodeId(target), 1.0)])


def test_graph_rejects_duplicate_edges() -> None:
    nodes = [Node(A, 0, 0), Node(B, 1, 0)]

    with pytest.raises(MapValidationError, match="duplicate edge 'A'->'B'"):
        WarehouseGraph(nodes, [Edge(A, B, 1.0), Edge(A, B, 2.0)])


def test_graph_rejects_edge_shorter_than_straight_line_distance() -> None:
    nodes = [Node(A, 0, 0), Node(B, 3, 4)]  # 5 m apart

    with pytest.raises(MapValidationError, match="shorter than the straight-line distance"):
        WarehouseGraph(nodes, [Edge(A, B, 4.9)])


def test_graph_accepts_edges_equal_to_or_longer_than_straight_line_distance() -> None:
    nodes = [Node(A, 0, 0), Node(B, 1, 1), Node(C, 2, 0)]
    diagonal = math.sqrt(2.0)  # computed differently from math.hypot: may differ in last bits

    graph = WarehouseGraph(nodes, [Edge(A, B, diagonal), Edge(B, C, 10.0)])

    assert graph.num_edges == 2


# --- WarehouseGraph: queries --------------------------------------------------------------


def test_node_lookup(line_graph: WarehouseGraph) -> None:
    assert line_graph.node(B) == Node(B, 1.0, 0.0)


def test_unknown_node_lookup_raises_with_the_offending_id(line_graph: WarehouseGraph) -> None:
    with pytest.raises(UnknownNodeError, match="unknown node 'Z'") as error:
        line_graph.node(NodeId("Z"))

    assert error.value.node_id == "Z"


def test_out_edges_in_input_order(line_graph: WarehouseGraph) -> None:
    assert [edge.target for edge in line_graph.out_edges(B)] == [A, C]


def test_node_without_outgoing_edges_has_empty_tuple(disconnected_graph: WarehouseGraph) -> None:
    assert disconnected_graph.out_edges(C) == ()


def test_out_edges_of_unknown_node_raises(line_graph: WarehouseGraph) -> None:
    with pytest.raises(UnknownNodeError):
        line_graph.out_edges(NodeId("Z"))


def test_get_edge(one_way_detour_graph: WarehouseGraph) -> None:
    edge = one_way_detour_graph.get_edge(B, A)

    assert edge is not None
    assert edge.length_m == 2.0
    assert one_way_detour_graph.get_edge(A, B) is None  # one-way: only B -> A exists


def test_get_edge_with_unknown_node_raises(line_graph: WarehouseGraph) -> None:
    with pytest.raises(UnknownNodeError):
        line_graph.get_edge(A, NodeId("Z"))


def test_nodes_of_kind() -> None:
    graph = WarehouseGraph(
        [Node(A, 0, 0, NodeKind.CHARGER), Node(B, 1, 0), Node(C, 2, 0, NodeKind.CHARGER)], []
    )

    assert [node.id for node in graph.nodes_of_kind(NodeKind.CHARGER)] == [A, C]
    assert graph.nodes_of_kind(NodeKind.STATION) == ()
