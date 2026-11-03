"""Tests for A* and its heuristics."""

import itertools
import math

import pytest

from amr_optimizer.routing import (
    Heuristic,
    astar,
    euclidean,
    manhattan,
    shortest_path,
    validate_route,
    zero,
)
from amr_optimizer.warehouse import Edge, Node, NodeId, UnknownNodeError, WarehouseGraph

FIXTURE_GRAPHS = [
    "line_graph",
    "diamond_graph",
    "one_way_detour_graph",
    "disconnected_graph",
    "tie_graph",
]


def grid_graph(width: int, height: int, spacing: float = 1.0) -> WarehouseGraph:
    """A two-way 4-connected lattice; node ids are 'x,y'."""

    def node_id(x: int, y: int) -> NodeId:
        return NodeId(f"{x},{y}")

    nodes = [
        Node(node_id(x, y), x * spacing, y * spacing) for y in range(height) for x in range(width)
    ]
    edges = []
    for x, y in itertools.product(range(width), range(height)):
        for dx, dy in ((1, 0), (0, 1)):
            if x + dx < width and y + dy < height:
                a, b = node_id(x, y), node_id(x + dx, y + dy)
                edges += [Edge(a, b, spacing), Edge(b, a, spacing)]
    return WarehouseGraph(nodes, edges)


# --- heuristics ---------------------------------------------------------------------------


def test_heuristic_values() -> None:
    node, goal = Node(NodeId("A"), 0.0, 0.0), Node(NodeId("B"), 3.0, 4.0)

    assert zero(node, goal) == 0.0
    assert euclidean(node, goal) == 5.0
    assert manhattan(node, goal) == 7.0
    assert euclidean(goal, goal) == manhattan(goal, goal) == 0.0


# --- A* agrees with Dijkstra on every pair of every hand-made graph -----------------------


@pytest.mark.parametrize("graph_name", FIXTURE_GRAPHS)
@pytest.mark.parametrize("heuristic", [zero, euclidean], ids=["zero", "euclidean"])
def test_astar_finds_the_same_length_as_dijkstra(
    graph_name: str, heuristic: Heuristic, request: pytest.FixtureRequest
) -> None:
    graph: WarehouseGraph = request.getfixturevalue(graph_name)

    for source, target in itertools.product([node.id for node in graph], repeat=2):
        ours = astar(graph, source, target, heuristic)
        reference = shortest_path(graph, source, target)
        if reference.route is None:
            assert ours.route is None
            continue
        assert ours.route is not None
        assert ours.route.length_m == pytest.approx(reference.route.length_m)
        validate_route(graph, ours.route)


@pytest.mark.parametrize("graph_name", FIXTURE_GRAPHS)
def test_zero_heuristic_expands_exactly_like_dijkstra(
    graph_name: str, request: pytest.FixtureRequest
) -> None:
    graph: WarehouseGraph = request.getfixturevalue(graph_name)

    for source, target in itertools.product([node.id for node in graph], repeat=2):
        ours = astar(graph, source, target, zero)
        reference = shortest_path(graph, source, target)
        assert ours.expanded == reference.expanded
        assert ours.route == reference.route


# --- contract shared with Dijkstra --------------------------------------------------------


def test_unreachable_target_returns_no_route(disconnected_graph: WarehouseGraph) -> None:
    assert astar(disconnected_graph, NodeId("A"), NodeId("C")).route is None


@pytest.mark.parametrize(("source", "target"), [("Z", "A"), ("A", "Z")])
def test_unknown_node_raises(line_graph: WarehouseGraph, source: str, target: str) -> None:
    with pytest.raises(UnknownNodeError, match="'Z'"):
        astar(line_graph, NodeId(source), NodeId(target))


def test_blocked_edge_forces_a_detour(diamond_graph: WarehouseGraph) -> None:
    blocked = {(NodeId("U"), NodeId("T"))}

    result = astar(diamond_graph, NodeId("S"), NodeId("T"), blocked_edges=blocked)

    assert result.route is not None
    assert result.route.nodes == (NodeId("S"), NodeId("D"), NodeId("T"))
    validate_route(diamond_graph, result.route, blocked_edges=blocked)


# --- why the heuristic matters ------------------------------------------------------------


def test_goal_directed_search_expands_fewer_nodes_on_a_grid() -> None:
    graph = grid_graph(20, 20)
    source, target = NodeId("0,10"), NodeId("19,10")  # straight across the middle

    dijkstra_result = shortest_path(graph, source, target)
    euclid_result = astar(graph, source, target, euclidean)
    manhattan_result = astar(graph, source, target, manhattan)

    assert dijkstra_result.route is not None
    for result in (euclid_result, manhattan_result):
        assert result.route is not None
        assert result.route.length_m == dijkstra_result.route.length_m == 19.0
    # Dijkstra settles every node closer than 19 m; A* stays near the straight line.
    assert euclid_result.expanded < dijkstra_result.expanded / 4
    assert manhattan_result.expanded <= euclid_result.expanded


def test_inflated_heuristic_can_return_a_longer_route() -> None:
    """Documents why A* needs a heuristic that does not overestimate.

    Via A: 2 + 6 = 8 m (A lies on the straight line, but A -> T is a long curve).
    Via B: 2 * sqrt(13) = 7.21 m (the true shortest route).
    Tripling the straight-line estimate makes B look far worse than it is.
    """
    s, a, b, t = NodeId("S"), NodeId("A"), NodeId("B"), NodeId("T")
    graph = WarehouseGraph(
        [Node(s, 0, 0), Node(a, 2, 0), Node(b, 2, 3), Node(t, 4, 0)],
        [Edge(s, a, 2.0), Edge(a, t, 6.0), Edge(s, b, math.sqrt(13)), Edge(b, t, math.sqrt(13))],
    )

    def inflated(node: Node, goal: Node) -> float:
        return 3.0 * euclidean(node, goal)

    optimal = astar(graph, s, t, euclidean)
    greedy = astar(graph, s, t, inflated)

    assert optimal.route is not None
    assert optimal.route.nodes == (s, b, t)
    assert greedy.route is not None
    assert greedy.route.nodes == (s, a, t)
    assert greedy.route.length_m > optimal.route.length_m
