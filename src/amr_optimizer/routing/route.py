"""Routes on the warehouse graph, search results, and a check that a route is executable."""

import itertools
import math
from collections.abc import Mapping, Set
from dataclasses import dataclass

from amr_optimizer.warehouse import EdgeKey, NodeId, WarehouseGraph

NO_BLOCKED_EDGES: frozenset[EdgeKey] = frozenset()
"""Default for ``blocked_edges`` parameters: nothing is blocked."""


class InvalidRouteError(ValueError):
    """A route cannot be driven on the graph as stated."""


@dataclass(frozen=True, slots=True)
class Route:
    """A sequence of nodes from ``nodes[0]`` to ``nodes[-1]`` and its total length.

    A single-node route means "already at the target" and has length 0.
    """

    nodes: tuple[NodeId, ...]
    length_m: float

    def __post_init__(self) -> None:
        if not self.nodes:
            raise InvalidRouteError("a route must contain at least one node")

    @property
    def source(self) -> NodeId:
        return self.nodes[0]

    @property
    def target(self) -> NodeId:
        return self.nodes[-1]


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Outcome of a single source-target search.

    ``route`` is ``None`` when the target is unreachable: a normal outcome, e.g. behind a
    blocked aisle (D4). ``expanded`` counts the nodes the search settled (took from the
    priority queue as final). It measures search effort independently of hardware.
    """

    route: Route | None
    expanded: int


def reconstruct_path(
    parents: Mapping[NodeId, NodeId], source: NodeId, target: NodeId
) -> tuple[NodeId, ...]:
    """Follow parent links back from ``target`` to ``source`` and return the forward path."""
    path = [target]
    while path[-1] != source:
        path.append(parents[path[-1]])
    path.reverse()
    return tuple(path)


def validate_route(
    graph: WarehouseGraph,
    route: Route,
    *,
    blocked_edges: Set[EdgeKey] = NO_BLOCKED_EDGES,
) -> None:
    """Raise `InvalidRouteError` unless ``route`` can be driven on ``graph`` as stated.

    Checks that every node exists, every consecutive pair is joined by an edge in the
    direction of travel, no blocked edge is used, and ``length_m`` equals the sum of the
    edge lengths. The simulator will reuse this as an invariant check.
    """
    for node_id in route.nodes:
        if node_id not in graph:
            raise InvalidRouteError(f"route uses unknown node {node_id!r}")

    total_m = 0.0
    for source, target in itertools.pairwise(route.nodes):
        edge = graph.get_edge(source, target)
        if edge is None:
            raise InvalidRouteError(f"route uses non-existent edge {source!r}->{target!r}")
        if edge.key in blocked_edges:
            raise InvalidRouteError(f"route uses blocked edge {source!r}->{target!r}")
        total_m += edge.length_m

    if not math.isclose(total_m, route.length_m, rel_tol=1e-9, abs_tol=1e-9):
        raise InvalidRouteError(
            f"route length {route.length_m} m does not match the sum of its edges {total_m} m"
        )
