"""Shared tiny graphs whose shortest paths can be computed by hand.

Coordinates are in metres. Every graph respects D5 (edge length >= straight-line distance).
"""

import math

import pytest

from amr_optimizer.warehouse import Edge, Node, NodeId, WarehouseGraph

SQRT2 = math.sqrt(2.0)


def _graph(
    nodes: list[tuple[str, float, float]], edges: list[tuple[str, str, float]]
) -> WarehouseGraph:
    return WarehouseGraph(
        (Node(NodeId(node_id), x, y) for node_id, x, y in nodes),
        (Edge(NodeId(source), NodeId(target), length) for source, target, length in edges),
    )


@pytest.fixture
def line_graph() -> WarehouseGraph:
    """A <-> B <-> C on a straight line, 1 m apart (two-way corridor)."""
    return _graph(
        [("A", 0, 0), ("B", 1, 0), ("C", 2, 0)],
        [("A", "B", 1), ("B", "A", 1), ("B", "C", 1), ("C", "B", 1)],
    )


@pytest.fixture
def diamond_graph() -> WarehouseGraph:
    """S -> T via U (2*sqrt2 = 2.83 m, shortest) or via D (2 + 2 = 4 m, a curved detour).

        U
      /   \\
    S       T
      \\   /
        D
    """
    return _graph(
        [("S", 0, 0), ("U", 1, 1), ("D", 1, -1), ("T", 2, 0)],
        [("S", "U", SQRT2), ("U", "T", SQRT2), ("S", "D", 2.0), ("D", "T", 2.0)],
    )


@pytest.fixture
def one_way_detour_graph() -> WarehouseGraph:
    """A square loop of one-way edges: B -> A -> D -> C -> B, each 2 m.

    A -> B is 6 m (around the loop), although B -> A is a direct 2 m edge.
    """
    return _graph(
        [("A", 0, 0), ("B", 2, 0), ("C", 2, 2), ("D", 0, 2)],
        [("B", "A", 2), ("A", "D", 2), ("D", "C", 2), ("C", "B", 2)],
    )


@pytest.fixture
def disconnected_graph() -> WarehouseGraph:
    """A -> B is a one-way edge; C has no edges at all."""
    return _graph([("A", 0, 0), ("B", 1, 0), ("C", 5, 5)], [("A", "B", 1)])


@pytest.fixture
def tie_graph() -> WarehouseGraph:
    """S -> T via U or via D, both exactly 2*sqrt2 m: tests deterministic tie-breaking."""
    return _graph(
        [("S", 0, 0), ("U", 1, 1), ("D", 1, -1), ("T", 2, 0)],
        [("S", "U", SQRT2), ("S", "D", SQRT2), ("U", "T", SQRT2), ("D", "T", SQRT2)],
    )
