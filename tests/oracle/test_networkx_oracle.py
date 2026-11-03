"""Oracle tests: our routing must agree with NetworkX on many seeded random graphs.

NetworkX is an independent, widely used implementation, so agreement on hundreds of
random queries (including unreachable targets and blocked edges) is strong evidence of
correctness. NetworkX is used here only; `src/` never imports it (D2).
"""

import math
import random

import networkx as nx
import pytest

from amr_optimizer.routing import shortest_path, shortest_path_lengths, validate_route
from amr_optimizer.warehouse import Edge, EdgeKey, Node, NodeId, WarehouseGraph

SEEDS = range(60)
QUERIES_PER_GRAPH = 12


def random_geometric_digraph(seed: int) -> WarehouseGraph:
    """A random directed graph with coordinates, valid under D5.

    Edge lengths are the straight-line distance times a factor in [1, 1.5] (curved paths).
    The edge count varies from none to fairly dense, so some targets are unreachable.
    """
    rng = random.Random(seed)
    count = rng.randint(2, 40)
    nodes = [Node(NodeId(f"n{i}"), rng.uniform(0, 100), rng.uniform(0, 100)) for i in range(count)]
    edges: dict[EdgeKey, Edge] = {}
    for _ in range(rng.randint(0, 4 * count)):
        a, b = rng.sample(nodes, 2)
        length = max(a.distance_to(b) * rng.uniform(1.0, 1.5), 1e-3)
        edges.setdefault((a.id, b.id), Edge(a.id, b.id, length))
    return WarehouseGraph(nodes, edges.values())


def to_networkx(graph: WarehouseGraph, blocked: set[EdgeKey]) -> "nx.DiGraph[NodeId]":
    reference: nx.DiGraph[NodeId] = nx.DiGraph()
    reference.add_nodes_from(node.id for node in graph)
    for edge in graph.edges:
        if edge.key not in blocked:
            reference.add_edge(edge.source, edge.target, weight=edge.length_m)
    return reference


def random_blocked_edges(graph: WarehouseGraph, rng: random.Random) -> set[EdgeKey]:
    return {edge.key for edge in graph.edges if rng.random() < 0.2}


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("with_blocked_edges", [False, True])
def test_point_to_point_matches_networkx(seed: int, with_blocked_edges: bool) -> None:
    graph = random_geometric_digraph(seed)
    rng = random.Random(1000 + seed)
    blocked = random_blocked_edges(graph, rng) if with_blocked_edges else set()
    reference = to_networkx(graph, blocked)
    ids = [node.id for node in graph]

    for _ in range(QUERIES_PER_GRAPH):
        source, target = rng.choice(ids), rng.choice(ids)
        result = shortest_path(graph, source, target, blocked_edges=blocked)
        try:
            expected = nx.dijkstra_path_length(reference, source, target)
        except nx.NetworkXNoPath:
            assert result.route is None, f"{source}->{target} should be unreachable"
            continue
        assert result.route is not None, f"{source}->{target} should be reachable"
        assert math.isclose(result.route.length_m, expected, rel_tol=1e-9, abs_tol=1e-9)
        validate_route(graph, result.route, blocked_edges=blocked)


@pytest.mark.parametrize("seed", SEEDS)
def test_one_to_all_matches_networkx(seed: int) -> None:
    graph = random_geometric_digraph(seed)
    reference = to_networkx(graph, set())
    source = random.Random(2000 + seed).choice([node.id for node in graph])

    ours = shortest_path_lengths(graph, source)
    expected = nx.single_source_dijkstra_path_length(reference, source)

    assert ours.keys() == expected.keys()
    for node_id, distance in ours.items():
        assert math.isclose(distance, expected[node_id], rel_tol=1e-9, abs_tol=1e-9)


def test_random_graphs_cover_both_reachable_and_unreachable_queries() -> None:
    """Guards the oracle tests themselves: they must exercise both outcomes."""
    reachable = unreachable = 0
    for seed in SEEDS:
        graph = random_geometric_digraph(seed)
        reference = to_networkx(graph, set())
        rng = random.Random(1000 + seed)
        ids = [node.id for node in graph]
        for _ in range(QUERIES_PER_GRAPH):
            if nx.has_path(reference, rng.choice(ids), rng.choice(ids)):
                reachable += 1
            else:
                unreachable += 1

    assert reachable > 100
    assert unreachable > 50
