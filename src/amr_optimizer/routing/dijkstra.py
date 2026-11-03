"""Dijkstra's shortest-path algorithm on the warehouse graph (distance in metres).

How it works: a priority queue holds candidate distances. Repeatedly take the node with the
smallest candidate distance, mark it *settled*, and try to improve its neighbours.

Correctness rests on one invariant: **the first time a node is taken from the queue, its
distance is final.** Any other route to it would have to leave the settled region through
some queued node whose distance is already at least as large, and edges can only add
length. That argument needs non-negative edge lengths, which the graph guarantees (D5).

Implementation choices:

- ``heapq`` binary heap with *lazy deletion*: instead of decreasing a key in place, a
  better candidate is pushed again and outdated entries are skipped when popped.
- Ties are broken by insertion order (a counter in each heap entry), so equal-length
  alternatives always resolve the same way (D7), and nodes are never compared directly.

Complexity: O((V + E) log V) time, O(V + E) memory (the heap can hold one entry per edge).
"""

import heapq
import itertools
import math
from collections.abc import Set

from amr_optimizer.routing.route import NO_BLOCKED_EDGES, Route, SearchResult, reconstruct_path
from amr_optimizer.warehouse import EdgeKey, NodeId, WarehouseGraph


def shortest_path(
    graph: WarehouseGraph,
    source: NodeId,
    target: NodeId,
    *,
    blocked_edges: Set[EdgeKey] = NO_BLOCKED_EDGES,
) -> SearchResult:
    """Shortest route from ``source`` to ``target``, never using an edge in ``blocked_edges``.

    The search stops as soon as ``target`` is settled. Returns a result whose ``route`` is
    ``None`` if ``target`` is unreachable. Raises `UnknownNodeError` for an unknown node id.
    Keys in ``blocked_edges`` that are not edges of the graph have no effect.
    """
    graph.node(target)  # fail fast on an unknown target id (D4)
    settled, parents = _settle(graph, source, blocked_edges, stop_at=target)
    if target not in settled:
        return SearchResult(route=None, expanded=len(settled))
    route = Route(reconstruct_path(parents, source, target), settled[target])
    return SearchResult(route=route, expanded=len(settled))


def shortest_path_lengths(
    graph: WarehouseGraph,
    source: NodeId,
    *,
    blocked_edges: Set[EdgeKey] = NO_BLOCKED_EDGES,
) -> dict[NodeId, float]:
    """Shortest distance from ``source`` to every reachable node (``source`` itself: 0).

    Unreachable nodes are absent from the result. This one-to-all variant is what dispatch
    needs to build robot-to-pickup distance tables.
    """
    settled, _ = _settle(graph, source, blocked_edges, stop_at=None)
    return settled


def _settle(
    graph: WarehouseGraph,
    source: NodeId,
    blocked_edges: Set[EdgeKey],
    *,
    stop_at: NodeId | None,
) -> tuple[dict[NodeId, float], dict[NodeId, NodeId]]:
    """Run Dijkstra from ``source``. Returns final distances (in settle order) and parents."""
    graph.node(source)  # fail fast on an unknown source id (D4)
    settled: dict[NodeId, float] = {}
    best: dict[NodeId, float] = {source: 0.0}  # best candidate distance found so far
    parents: dict[NodeId, NodeId] = {}
    tie_breaker = itertools.count()
    queue: list[tuple[float, int, NodeId]] = [(0.0, next(tie_breaker), source)]

    while queue:
        distance, _, node = heapq.heappop(queue)
        if node in settled:
            continue  # outdated entry: this node was already settled with a shorter distance
        settled[node] = distance
        if node == stop_at:
            break
        for edge in graph.out_edges(node):
            neighbour = edge.target
            if neighbour in settled or edge.key in blocked_edges:
                continue
            candidate = distance + edge.length_m
            if candidate < best.get(neighbour, math.inf):
                best[neighbour] = candidate
                parents[neighbour] = node
                heapq.heappush(queue, (candidate, next(tie_breaker), neighbour))

    return settled, parents
