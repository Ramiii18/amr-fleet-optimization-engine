"""Compile a layout into a warehouse graph: one node per cell, edges between neighbours (D38).

Rules:

- **Nodes.** Every floor cell and every endpoint becomes a node; blocked cells and unassigned
  slots do not. The cell at text row ``r`` and column ``c`` has its centre at
  ``x = c * cell_m`` and ``y = (rows - 1 - r) * cell_m`` (metres, y up, as in the map model).
- **Ids** name the kind and the cell: ``X-r003-c012`` for floor, ``S-``, ``ST-``, ``CH-`` and
  ``PK-`` for storage, stations, chargers and parking. An id never depends on any other cell, so a
  floor cell keeps its id when slots change, and runs on two candidate layouts can be paired.
- **Edges** join 4-adjacent cells only, with length ``cell_m``. They are axis-aligned, so the
  Manhattan heuristic stays consistent (D5, D10).
- **Floor to floor.** Two-way, unless a cell of the pair is an arrow on the pair's axis: then only
  the arrow's direction. Arrows on that axis that point against each other are an error. An arrow
  constrains its own axis only, so crossing a one-way aisle and leaving it sideways stay two-way.
- **Endpoint to floor.** Always two-way, also beside an arrow: a spur is a dead end that a robot
  enters and leaves.
- **Endpoint to endpoint.** Never joined. Side-by-side storage cells on a rack face are separate
  dead ends, each reached from the floor beside it.

Whether the result is fit for traffic (every endpoint a leaf, everything reachable from
everywhere) is not the compiler's question: the well-formedness checker answers it for any map.

Cost: one pass over the grid, O(rows x columns) time and memory. Nodes come in reading order, and
each cell's edges in a fixed order (right, then down), so the same layout gives the same graph.
"""

from amr_optimizer.layout.model import ARROWS, ENDPOINTS, Cell, Layout, LayoutFormatError, is_floor
from amr_optimizer.warehouse import Edge, Node, NodeId, NodeKind, WarehouseGraph

ID_PREFIXES = {
    NodeKind.INTERSECTION: "X",
    NodeKind.STORAGE: "S",
    NodeKind.STATION: "ST",
    NodeKind.CHARGER: "CH",
    NodeKind.PARKING: "PK",
}


def cell_node_id(kind: NodeKind, cell: Cell) -> NodeId:
    """The id of the node at ``cell``, e.g. ``X-r003-c012``."""
    row, column = cell
    return NodeId(f"{ID_PREFIXES[kind]}-r{row:03d}-c{column:03d}")


def _kind(char: str) -> NodeKind | None:
    if is_floor(char):
        return NodeKind.INTERSECTION
    return ENDPOINTS.get(char)


def _directions(layout: Layout, a: Cell, b: Cell) -> tuple[bool, bool]:
    """Whether a floor-to-floor edge runs a -> b and b -> a. ``b`` is right of or below ``a``."""
    step = (b[0] - a[0], b[1] - a[1])
    forward = backward = True
    for cell in (a, b):
        arrow = ARROWS.get(layout.at(cell))
        if arrow is None or arrow[0] * step[0] + arrow[1] * step[1] == 0:
            continue  # not an arrow, or an arrow across this axis
        if arrow == step:
            backward = False
        else:
            forward = False
    if not (forward or backward):
        raise LayoutFormatError(
            f"layout.grid[{a[0]}][{a[1]}] {layout.at(a)!r} and layout.grid[{b[0]}][{b[1]}] "
            f"{layout.at(b)!r}: arrows on one axis point against each other, so no robot "
            "could pass between these cells"
        )
    return forward, backward


def compile_layout(layout: Layout) -> WarehouseGraph:
    """The warehouse graph of ``layout``. Raises `LayoutFormatError` for opposing arrows."""
    kinds: dict[Cell, NodeKind] = {}
    nodes: list[Node] = []
    top = layout.rows - 1
    for r, row in enumerate(layout.grid):
        for c, char in enumerate(row):
            kind = _kind(char)
            if kind is not None:
                kinds[r, c] = kind
                nodes.append(
                    Node(
                        cell_node_id(kind, (r, c)),
                        c * layout.cell_m,
                        (top - r) * layout.cell_m,
                        kind,
                    )
                )

    edges: list[Edge] = []
    for (r, c), kind in kinds.items():
        a = (r, c)
        for b in ((r, c + 1), (r + 1, c)):
            other = kinds.get(b)
            if other is None:
                continue
            a_floor, b_floor = kind is NodeKind.INTERSECTION, other is NodeKind.INTERSECTION
            if not (a_floor or b_floor):
                continue  # two endpoints are never joined
            forward = backward = True
            if a_floor and b_floor:
                forward, backward = _directions(layout, a, b)
            a_id, b_id = cell_node_id(kind, a), cell_node_id(other, b)
            if forward:
                edges.append(Edge(a_id, b_id, layout.cell_m))
            if backward:
                edges.append(Edge(b_id, a_id, layout.cell_m))
    return WarehouseGraph(nodes, edges)
