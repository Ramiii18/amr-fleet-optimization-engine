"""Orders from the engine's plans, and the rules an order must follow (VDA 5050 3.0.0, D30).

An itinerary planned by the engine (a `TimedPath`) becomes a route of alternating nodes and edges:
the first node has sequence id 0, the first edge 1, the second node 2, and so on, so the edge with
sequence id n connects the nodes n - 1 and n + 1. A prefix of the route is *released* (the base);
the rest is the horizon. An order update extends the base: it starts at the last node of the
previous base (the stitching node), with the next order update id.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from amr_optimizer.traffic import TimedPath
from amr_optimizer.vda5050.messages import Action, Edge, Header, Node, NodePosition, Order
from amr_optimizer.warehouse import WarehouseGraph


def edge_id(source: str, target: str) -> str:
    return f"{source}->{target}"


def route_of(
    path: TimedPath,
    graph: WarehouseGraph,
    map_id: str,
    actions: Mapping[int, Sequence[Action]] | None = None,
) -> tuple[tuple[Node, ...], tuple[Edge, ...]]:
    """The nodes and edges of ``path``, none released yet. ``actions[i]`` are the actions at the
    path's visit i, such as a pick at the pickup."""
    actions = actions or {}
    visits = path.visits
    nodes = tuple(
        Node(
            v.node,
            2 * i,
            False,
            NodePosition(graph.node(v.node).x, graph.node(v.node).y, map_id),
            tuple(actions.get(i, ())),
        )
        for i, v in enumerate(visits)
    )
    edges = []
    for i in range(len(visits) - 1):
        u, v = visits[i].node, visits[i + 1].node
        link = graph.get_edge(u, v)
        if link is None:
            raise ValueError(f"the path drives {u!r} -> {v!r}, which is not an edge of the map")
        edges.append(Edge(edge_id(u, v), 2 * i + 1, False, link.length_m))
    return nodes, tuple(edges)


def order_message(
    header: Header,
    order_id: str,
    order_update_id: int,
    route: tuple[tuple[Node, ...], tuple[Edge, ...]],
    *,
    start: int,
    base_end: int,
) -> Order:
    """The order (``start == 0``) or order update (``start`` is the stitching node) for ``route``,
    released up to and including node ``base_end``. Indices count nodes of the route."""
    nodes, edges = route
    if not 0 <= start <= base_end < len(nodes):
        raise ValueError(f"need 0 <= start <= base_end < {len(nodes)}, got {start}, {base_end}")
    return Order(
        header,
        order_id,
        order_update_id,
        tuple(replace(n, released=i <= base_end) for i, n in enumerate(nodes) if i >= start),
        tuple(replace(e, released=i < base_end) for i, e in enumerate(edges) if i >= start),
    )


def order_violations(order: Order) -> list[str]:
    """How ``order`` breaks the specification's rules for nodes, edges and the base, if it does."""
    problems = []
    nodes, edges = order.nodes, order.edges
    if not nodes:
        return ["an order needs at least one node"]
    if len(edges) != len(nodes) - 1:
        return [f"{len(edges)} edges for {len(nodes)} nodes: need one fewer edge"]
    first = nodes[0].sequence_id
    expected = [first + 2 * i for i in range(len(nodes))]
    if [n.sequence_id for n in nodes] != expected:
        problems.append("node sequence ids are not continuous")
    if [e.sequence_id for e in edges] != [s + 1 for s in expected[:-1]]:
        problems.append("edge sequence ids do not sit between their nodes")
    if not nodes[0].released:
        problems.append("the first node must be released")
    sequence = [
        x for pair in zip(nodes, (*edges, None), strict=True) for x in pair if x is not None
    ]
    released = [item.released for item in sequence]
    if released != sorted(released, reverse=True):
        problems.append("a released node or edge follows an unreleased one")
    for i, edge in enumerate(edges):
        if edge.released and not (nodes[i].released and nodes[i + 1].released):
            problems.append(f"edge {edge.edge_id!r} is released without both its nodes")
    return problems


def update_violations(previous: Order, update: Order) -> list[str]:
    """How ``update`` fails to continue ``previous``, if it does: same order, a higher order
    update id, and a first node equal to the last node of the previous base."""
    problems = order_violations(update)
    if update.order_id != previous.order_id:
        problems.append("an update belongs to the same order")
    if update.order_update_id <= previous.order_update_id:
        problems.append("the order update id must increase")
    last_base = [n for n in previous.nodes if n.released][-1]
    first = update.nodes[0] if update.nodes else None
    if first is None or (first.node_id, first.sequence_id) != (
        last_base.node_id,
        last_base.sequence_id,
    ):
        problems.append("an update must start at the last node of the previous base")
    return problems
