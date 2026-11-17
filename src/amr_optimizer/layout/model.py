"""A floor plan as a grid of cells: the layout file's content (D37, D38).

Each cell is one character. Text row 0 is the top of the plan; every row has the same length.

=====  =====================================================================================
Char   Meaning
=====  =====================================================================================
``#``  blocked: wall, rack body, column, anything a robot cannot drive on
``.``  two-way floor
``^``  one-way floor: along its vertical axis, robots may move up only
``v``  one-way floor: along its vertical axis, down only
``<``  one-way floor: along its horizontal axis, left only
``>``  one-way floor: along its horizontal axis, right only
``S``  storage location (endpoint)
``T``  station: a pickup or drop-off point (endpoint)
``C``  charger (endpoint)
``P``  parking spot (endpoint)
``?``  candidate slot: screening may make it a station, charger or parking spot. A slot left
       unassigned is not part of the compiled map.
=====  =====================================================================================

Floor cells are the drivable cells (``.`` and the arrows). Endpoints are where robots stop. How
cells become nodes and edges is the compiler's business (`amr_optimizer.layout.compiler`).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

from amr_optimizer.warehouse import MapValidationError, NodeKind

BLOCKED = "#"
FLOOR = "."
SLOT = "?"
ARROWS: Mapping[str, tuple[int, int]] = {"^": (-1, 0), "v": (1, 0), "<": (0, -1), ">": (0, 1)}
"""Each arrow's direction as (row step, column step): text rows grow downwards."""
ENDPOINTS: Mapping[str, NodeKind] = {
    "S": NodeKind.STORAGE,
    "T": NodeKind.STATION,
    "C": NodeKind.CHARGER,
    "P": NodeKind.PARKING,
}
SLOT_KINDS: Mapping[NodeKind, str] = {
    NodeKind.STATION: "T",
    NodeKind.CHARGER: "C",
    NodeKind.PARKING: "P",
}
"""What a candidate slot may become, and the character it is then written with."""
LEGEND = frozenset({BLOCKED, FLOOR, SLOT, *ARROWS, *ENDPOINTS})

Cell = tuple[int, int]
"""A cell as (row, column), row 0 at the top."""


class LayoutFormatError(MapValidationError):
    """A layout is malformed: the message says where (e.g. ``layout.grid[3][7]``)."""


def is_floor(char: str) -> bool:
    return char == FLOOR or char in ARROWS


@dataclass(frozen=True, slots=True)
class Layout:
    """A validated grid of cells, ``cell_m`` metres on a side."""

    grid: tuple[str, ...]
    cell_m: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        if not (math.isfinite(self.cell_m) and self.cell_m > 0):
            raise LayoutFormatError(f"layout.cell_m: must be a positive number, got {self.cell_m}")
        if not self.grid:
            raise LayoutFormatError("layout.grid: needs at least one row")
        width = len(self.grid[0])
        for r, row in enumerate(self.grid):
            if not row:
                raise LayoutFormatError(f"layout.grid[{r}]: a row needs at least one cell")
            if len(row) != width:
                raise LayoutFormatError(
                    f"layout.grid[{r}]: has {len(row)} cells, but row 0 has {width}; "
                    "every row must have the same length"
                )
            for c, char in enumerate(row):
                if char not in LEGEND:
                    raise LayoutFormatError(
                        f"layout.grid[{r}][{c}]: unknown cell {char!r}; "
                        f"the legend is {''.join(sorted(LEGEND))}"
                    )

    @property
    def rows(self) -> int:
        return len(self.grid)

    @property
    def columns(self) -> int:
        return len(self.grid[0])

    def at(self, cell: Cell) -> str:
        row, column = cell
        return self.grid[row][column]

    def slots(self) -> tuple[Cell, ...]:
        """The candidate slots, in reading order (row by row, left to right)."""
        return tuple(
            (r, c) for r, row in enumerate(self.grid) for c, char in enumerate(row) if char == SLOT
        )

    def assign(self, assignment: Mapping[Cell, NodeKind]) -> "Layout":
        """A copy in which the given slots become stations, chargers or parking spots.

        Slots not named stay candidate slots. Naming a cell that is not a slot, or a kind a slot
        cannot become, raises `ValueError`.
        """
        rows = [list(row) for row in self.grid]
        for (r, c), kind in assignment.items():
            if not (0 <= r < self.rows and 0 <= c < self.columns) or rows[r][c] != SLOT:
                raise ValueError(f"cell ({r}, {c}) is not a candidate slot")
            if kind not in SLOT_KINDS:
                allowed = ", ".join(k.value for k in SLOT_KINDS)
                raise ValueError(f"a slot can become a {allowed}, not a {kind.value}")
            rows[r][c] = SLOT_KINDS[kind]
        return Layout(tuple("".join(row) for row in rows), self.cell_m, self.description)
