"""Warehouse graph model: the locations (nodes) and connections (edges) robots move on.

Coordinates are metres in one fixed warehouse frame (x to the right, y up).

Each class checks only the rules it can verify on its own. Rules that involve several
objects (unique ids, edges pointing at existing nodes, edge length vs. straight-line
distance) are checked by `WarehouseGraph`, which sees all of them at once.
"""

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import NewType

NodeId = NewType("NodeId", str)
"""Identifier of a node, e.g. ``NodeId("A3")``.

At runtime it is an ordinary ``str``. For mypy it is a separate type, so passing an
arbitrary string where a node id is expected is reported as an error.
"""

EdgeKey = tuple[NodeId, NodeId]
"""An edge identified by its ``(source, target)`` pair, e.g. for sets of blocked edges."""

# Edge lengths and straight-line distances come from floating-point arithmetic, so an
# edge whose length equals the distance between its endpoints may differ from it in the
# last bits. Shortfalls up to this tolerance are accepted (D5).
LENGTH_TOLERANCE_M = 1e-9


class MapValidationError(ValueError):
    """A node, edge or map violates one of the warehouse model's rules."""


class UnknownNodeError(LookupError):
    """A node id was used that does not exist in the graph."""

    def __init__(self, node_id: NodeId) -> None:
        super().__init__(f"unknown node {node_id!r}")
        self.node_id = node_id


class NodeKind(Enum):
    """What a location is used for.

    The kind describes purpose only; it does not restrict which robots may pass through.
    """

    INTERSECTION = "intersection"
    STATION = "station"  # pickup or drop-off point
    STORAGE = "storage"  # rack location where loads are stored
    CHARGER = "charger"
    PARKING = "parking"  # where idle robots wait without blocking aisles


@dataclass(frozen=True, slots=True)
class Node:
    """A navigable location in the warehouse."""

    id: NodeId
    x: float
    y: float
    kind: NodeKind = NodeKind.INTERSECTION

    def __post_init__(self) -> None:
        if not self.id:
            raise MapValidationError("node id must be a non-empty string")
        if not (math.isfinite(self.x) and math.isfinite(self.y)):
            raise MapValidationError(
                f"node {self.id!r}: coordinates must be finite numbers, got ({self.x}, {self.y})"
            )

    def distance_to(self, other: "Node") -> float:
        """Straight-line (Euclidean) distance to ``other``, in metres."""
        return math.hypot(other.x - self.x, other.y - self.y)


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed connection that robots can traverse from ``source`` to ``target``.

    A two-way corridor is represented by two edges, one per direction (D8).
    """

    source: NodeId
    target: NodeId
    length_m: float

    def __post_init__(self) -> None:
        if self.source == self.target:
            raise MapValidationError(
                f"edge {self.source!r}->{self.target!r}: self-loops are not allowed"
            )
        if not (math.isfinite(self.length_m) and self.length_m > 0):
            raise MapValidationError(
                f"edge {self.source!r}->{self.target!r}: length must be a finite number > 0, "
                f"got {self.length_m}"
            )

    @property
    def key(self) -> EdgeKey:
        """The ``(source, target)`` pair that identifies this edge."""
        return (self.source, self.target)


class WarehouseGraph:
    """Directed graph of warehouse locations, validated once on construction.

    Guaranteed invariants (the graph is read-only, so they hold for its whole lifetime):

    - node ids are unique;
    - every edge connects two existing nodes;
    - there is at most one edge per ordered ``(source, target)`` pair;
    - every edge is at least as long as the straight-line distance between its endpoints
      (D5), which keeps straight-line A* heuristics admissible.

    Storage is an adjacency list: for each node, the tuple of its outgoing edges. Warehouse
    graphs are sparse (a node has at most a handful of neighbours), so this uses O(V + E)
    memory, where an adjacency matrix would need O(V^2).

    Iteration order (nodes, edges, outgoing edges) is the order in which they were given,
    which makes every algorithm built on top of the graph deterministic (D7).
    """

    __slots__ = ("_edges", "_edges_by_key", "_nodes", "_out_edges")

    def __init__(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        node_map: dict[NodeId, Node] = {}
        for node in nodes:
            if node.id in node_map:
                raise MapValidationError(f"duplicate node id {node.id!r}")
            node_map[node.id] = node

        out_edges: dict[NodeId, list[Edge]] = {node_id: [] for node_id in node_map}
        edges_by_key: dict[EdgeKey, Edge] = {}
        for edge in edges:
            for endpoint in (edge.source, edge.target):
                if endpoint not in node_map:
                    raise MapValidationError(
                        f"edge {edge.source!r}->{edge.target!r}: unknown node {endpoint!r}"
                    )
            if edge.key in edges_by_key:
                raise MapValidationError(f"duplicate edge {edge.source!r}->{edge.target!r}")
            straight_m = node_map[edge.source].distance_to(node_map[edge.target])
            if edge.length_m < straight_m - LENGTH_TOLERANCE_M:
                raise MapValidationError(
                    f"edge {edge.source!r}->{edge.target!r}: length {edge.length_m} m is shorter "
                    f"than the straight-line distance {straight_m:.6f} m between its endpoints"
                )
            edges_by_key[edge.key] = edge
            out_edges[edge.source].append(edge)

        self._nodes = node_map
        self._edges_by_key = edges_by_key
        self._edges = tuple(edges_by_key.values())
        self._out_edges = {node_id: tuple(out) for node_id, out in out_edges.items()}

    # --- size and membership -------------------------------------------------------------

    def __len__(self) -> int:
        """Number of nodes."""
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    def __iter__(self) -> Iterator[Node]:
        return iter(self._nodes.values())

    def __repr__(self) -> str:
        return f"WarehouseGraph(nodes={len(self._nodes)}, edges={len(self._edges)})"

    @property
    def num_edges(self) -> int:
        return len(self._edges)

    # --- nodes ----------------------------------------------------------------------------

    @property
    def nodes(self) -> tuple[Node, ...]:
        return tuple(self._nodes.values())

    def node(self, node_id: NodeId) -> Node:
        """The node with this id. Raises `UnknownNodeError` if there is none."""
        try:
            return self._nodes[node_id]
        except KeyError:
            raise UnknownNodeError(node_id) from None

    def nodes_of_kind(self, kind: NodeKind) -> tuple[Node, ...]:
        return tuple(node for node in self._nodes.values() if node.kind is kind)

    # --- edges ----------------------------------------------------------------------------

    @property
    def edges(self) -> tuple[Edge, ...]:
        return self._edges

    def out_edges(self, node_id: NodeId) -> tuple[Edge, ...]:
        """Edges leaving ``node_id``. Raises `UnknownNodeError` for an unknown node."""
        try:
            return self._out_edges[node_id]
        except KeyError:
            raise UnknownNodeError(node_id) from None

    def get_edge(self, source: NodeId, target: NodeId) -> Edge | None:
        """The edge ``source -> target``, or ``None`` if the two nodes are not connected.

        Raises `UnknownNodeError` if either node does not exist (D4: a missing connection
        is a normal answer, an unknown id is an input error).
        """
        for node_id in (source, target):
            if node_id not in self._nodes:
                raise UnknownNodeError(node_id)
        return self._edges_by_key.get((source, target))
