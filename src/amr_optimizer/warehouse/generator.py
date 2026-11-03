"""Synthetic grid-warehouse generator.

Layout, with distances in metres (two aisles and four rows shown)::

              CH00                          chargers: spurs above the top row
              X00-03 ============ X01-03    top cross aisle (two-way)
                ^                   |
      PK01 -- X00-02 -- S00-02    X01-02 -- S01-02
                ^                   v       one-way aisles, alternating direction
      PK00 -- X00-01 -- S00-01    X01-01 -- S01-01
                ^                   v       storage: spurs east of inner aisle nodes
              X00-00 ============ X01-00    bottom cross aisle (two-way)
              ST00                ST01      stations: spurs below the bottom row

    (parking spurs sit west of aisle 0; spurs are two-way dead ends)

- Aisle ``c`` runs along ``x = c * aisle_pitch_m``; row ``r`` lies at ``y = r * row_spacing_m``.
- With one-way aisles, even aisles run up (+y) and odd aisles run down. The bottom and
  top rows are always two-way cross aisles, so every node can reach every other node.
- Every storage location, station, charger and parking spot is a **dead-end spur** off an
  aisle. A robot standing at one never blocks through traffic. This is the
  "well-formed infrastructure" property that multi-robot planning relies on (M4).
- All edges are axis-aligned, so the Manhattan heuristic is consistent on these maps.

The generator is a pure function of its spec: same spec, same graph (D9). It produces
synthetic layouts only; it is not modelled on any real site.
"""

import math
from dataclasses import dataclass

from amr_optimizer.warehouse.model import (
    Edge,
    MapValidationError,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
)


@dataclass(frozen=True, slots=True)
class GridWarehouseSpec:
    """Parameters of a grid warehouse. See the module docstring for the layout."""

    aisles: int = 6
    rows: int = 10
    aisle_pitch_m: float = 4.0
    row_spacing_m: float = 2.0
    one_way_aisles: bool = True
    extra_cross_rows: tuple[int, ...] = ()  # inner rows that are also two-way cross aisles
    storage_every: int = 1  # storage spur on every k-th inner aisle node; 0 = no storage
    stations: int = 3
    chargers: int = 2
    parking: int = 2
    spur_length_m: float = 1.0

    def __post_init__(self) -> None:
        if self.aisles < 2:
            raise MapValidationError("a grid warehouse needs at least 2 aisles")
        if self.rows < 2:
            raise MapValidationError("a grid warehouse needs at least 2 rows")
        for name in ("aisle_pitch_m", "row_spacing_m", "spur_length_m"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise MapValidationError(f"{name} must be a finite number > 0, got {value}")
        if self.spur_length_m >= self.aisle_pitch_m / 2:
            raise MapValidationError("spur_length_m must be shorter than half the aisle pitch")
        if len(set(self.extra_cross_rows)) != len(self.extra_cross_rows):
            raise MapValidationError("extra_cross_rows contains duplicates")
        for row in self.extra_cross_rows:
            if not 0 < row < self.rows - 1:
                raise MapValidationError(
                    f"extra cross row {row} must be an inner row (1 to {self.rows - 2})"
                )
        if self.storage_every < 0:
            raise MapValidationError("storage_every must be >= 0")
        limits = (("stations", self.aisles), ("chargers", self.aisles), ("parking", self.rows - 2))
        for name, limit in limits:
            count = getattr(self, name)
            if not 0 <= count <= limit:
                raise MapValidationError(f"{name} must be between 0 and {limit}, got {count}")

    @property
    def cross_rows(self) -> frozenset[int]:
        """All rows with two-way horizontal edges: bottom, top and the extra ones."""
        return frozenset({0, self.rows - 1, *self.extra_cross_rows})


PRESETS: dict[str, GridWarehouseSpec] = {
    "small": GridWarehouseSpec(aisles=4, rows=6, stations=2, chargers=1, parking=2),
    "medium": GridWarehouseSpec(
        aisles=8, rows=12, extra_cross_rows=(6,), stations=4, chargers=3, parking=4
    ),
    "large": GridWarehouseSpec(
        aisles=12, rows=20, extra_cross_rows=(10,), stations=6, chargers=5, parking=6
    ),
}
"""Named layouts used by tests, scripts and the example maps in ``scenarios/maps``."""


def aisle_node_id(aisle: int, row: int) -> NodeId:
    return NodeId(f"X{aisle:02d}-{row:02d}")


def generate_grid_warehouse(spec: GridWarehouseSpec) -> WarehouseGraph:
    """Build the warehouse graph described by ``spec``."""
    nodes: list[Node] = []
    edges: list[Edge] = []

    def add_two_way(a: NodeId, b: NodeId, length_m: float) -> None:
        edges.extend((Edge(a, b, length_m), Edge(b, a, length_m)))

    def add_spur(anchor: Node, spur_id: str, dx: float, dy: float, kind: NodeKind) -> None:
        spur = Node(NodeId(spur_id), anchor.x + dx, anchor.y + dy, kind)
        nodes.append(spur)
        add_two_way(anchor.id, spur.id, spec.spur_length_m)

    aisle_nodes = {
        (c, r): Node(aisle_node_id(c, r), c * spec.aisle_pitch_m, r * spec.row_spacing_m)
        for c in range(spec.aisles)
        for r in range(spec.rows)
    }
    nodes.extend(aisle_nodes.values())

    # Vertical aisles.
    for c in range(spec.aisles):
        for r in range(spec.rows - 1):
            lower, upper = aisle_node_id(c, r), aisle_node_id(c, r + 1)
            if not spec.one_way_aisles:
                add_two_way(lower, upper, spec.row_spacing_m)
            elif c % 2 == 0:
                edges.append(Edge(lower, upper, spec.row_spacing_m))  # up
            else:
                edges.append(Edge(upper, lower, spec.row_spacing_m))  # down

    # Cross aisles.
    for r in sorted(spec.cross_rows):
        for c in range(spec.aisles - 1):
            add_two_way(aisle_node_id(c, r), aisle_node_id(c + 1, r), spec.aisle_pitch_m)

    # Storage spurs east of inner, non-cross aisle nodes.
    if spec.storage_every > 0:
        storage_rows = [
            r
            for r in range(1, spec.rows - 1)
            if r not in spec.cross_rows and (r - 1) % spec.storage_every == 0
        ]
        for c in range(spec.aisles):
            for r in storage_rows:
                add_spur(
                    aisle_nodes[c, r],
                    f"S{c:02d}-{r:02d}",
                    spec.spur_length_m,
                    0.0,
                    NodeKind.STORAGE,
                )

    # Stations below the bottom row, chargers above the top row, parking west of aisle 0
    # on inner rows (the side wall between the bottom and top cross aisles).
    top = spec.rows - 1
    for i, c in enumerate(spread(spec.stations, spec.aisles)):
        add_spur(aisle_nodes[c, 0], f"ST{i:02d}", 0.0, -spec.spur_length_m, NodeKind.STATION)
    for i, c in enumerate(spread(spec.chargers, spec.aisles)):
        add_spur(aisle_nodes[c, top], f"CH{i:02d}", 0.0, spec.spur_length_m, NodeKind.CHARGER)
    for i, inner_row in enumerate(spread(spec.parking, spec.rows - 2)):
        add_spur(
            aisle_nodes[0, inner_row + 1], f"PK{i:02d}", -spec.spur_length_m, 0.0, NodeKind.PARKING
        )

    return WarehouseGraph(nodes, edges)


def spread(count: int, size: int) -> list[int]:
    """``count`` distinct indices in ``range(size)``, as evenly spaced as possible.

    >>> spread(3, 7)
    [0, 3, 6]
    """
    if not 0 <= count <= size:
        raise ValueError(f"cannot pick {count} distinct indices from {size}")
    if count == 0:
        return []
    if count == 1:
        return [(size - 1) // 2]
    step = (size - 1) / (count - 1)  # >= 1, so rounded indices stay distinct
    return [round(i * step) for i in range(count)]
