"""Floor plans as layout files compiled into warehouse graphs, and the check that a map is fit
for the traffic planner (Phase 3, D37 to D39)."""

from amr_optimizer.layout.compiler import cell_node_id, compile_layout
from amr_optimizer.layout.io import (
    layout_from_dict,
    layout_to_dict,
    load_layout,
    load_map_file,
    map_from_dict,
    save_layout,
)
from amr_optimizer.layout.model import Cell, Layout, LayoutFormatError
from amr_optimizer.layout.wellformed import (
    NotWellFormedError,
    Violation,
    WellFormedReport,
    check_well_formed,
    holding_kinds,
    holding_spots_needed,
    require_well_formed,
)

__all__ = [
    "Cell",
    "Layout",
    "LayoutFormatError",
    "NotWellFormedError",
    "Violation",
    "WellFormedReport",
    "check_well_formed",
    "cell_node_id",
    "compile_layout",
    "holding_kinds",
    "holding_spots_needed",
    "layout_from_dict",
    "layout_to_dict",
    "load_layout",
    "load_map_file",
    "map_from_dict",
    "require_well_formed",
    "save_layout",
]
