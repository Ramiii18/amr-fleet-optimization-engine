"""A* search: Dijkstra guided towards the goal by a heuristic (distance in metres).

The queue is ordered by ``f = g + h``: ``g`` is the length travelled from the source and
``h`` the heuristic's estimate of the length still to go. With ``h = 0`` this is exactly
Dijkstra. A good ``h`` lets the search ignore nodes that lead away from the goal: same
optimal route, fewer nodes expanded.

Requirement: the heuristic must be **consistent** (see `heuristics`). This implementation
uses a closed set and never reopens a node, which is optimal only for consistent
heuristics. An inflated heuristic gives faster but possibly longer routes; that trade-off
("weighted A*") is deliberately not hidden behind a default.

Ties on ``f`` prefer the smaller ``h`` (the node closer to the goal), then insertion order.
Preferring progress towards the goal reduces expansions on grids, where many routes share
the same length. Worst-case complexity is the same as Dijkstra's: O((V + E) log V).
"""

import heapq
import itertools
import math
from collections.abc import Set

from amr_optimizer.routing.heuristics import Heuristic, euclidean
from amr_optimizer.routing.route import NO_BLOCKED_EDGES, Route, SearchResult, reconstruct_path
from amr_optimizer.warehouse import EdgeKey, NodeId, WarehouseGraph


def astar(
    graph: WarehouseGraph,
    source: NodeId,
    target: NodeId,
    heuristic: Heuristic = euclidean,
    *,
    blocked_edges: Set[EdgeKey] = NO_BLOCKED_EDGES,
) -> SearchResult:
    """Shortest route from ``source`` to ``target`` (for a consistent ``heuristic``).

    Same contract as `shortest_path`: ``route`` is ``None`` if the target is unreachable,
    `UnknownNodeError` for unknown ids, and edges in ``blocked_edges`` are never used.
    """
    goal = graph.node(target)
    start_h = heuristic(graph.node(source), goal)
    closed: set[NodeId] = set()
    best_g: dict[NodeId, float] = {source: 0.0}
    parents: dict[NodeId, NodeId] = {}
    tie_breaker = itertools.count()
    # Entries: (f, h, insertion counter, node).
    queue: list[tuple[float, float, int, NodeId]] = [(start_h, start_h, next(tie_breaker), source)]

    while queue:
        _, _, _, node = heapq.heappop(queue)
        if node in closed:
            continue  # outdated entry: this node was already expanded via a shorter route
        closed.add(node)
        if node == target:
            route = Route(reconstruct_path(parents, source, target), best_g[node])
            return SearchResult(route=route, expanded=len(closed))
        g = best_g[node]
        for edge in graph.out_edges(node):
            neighbour = edge.target
            if neighbour in closed or edge.key in blocked_edges:
                continue
            candidate = g + edge.length_m
            if candidate < best_g.get(neighbour, math.inf):
                best_g[neighbour] = candidate
                parents[neighbour] = node
                h = heuristic(graph.node(neighbour), goal)
                heapq.heappush(queue, (candidate + h, h, next(tie_breaker), neighbour))

    return SearchResult(route=None, expanded=len(closed))
