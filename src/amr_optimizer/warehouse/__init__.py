"""Warehouse model: the graph of navigable locations and connections robots move on."""

from amr_optimizer.warehouse.generator import PRESETS, GridWarehouseSpec, generate_grid_warehouse
from amr_optimizer.warehouse.io import MapFormatError, load_graph, save_graph
from amr_optimizer.warehouse.model import (
    LENGTH_TOLERANCE_M,
    Edge,
    EdgeKey,
    MapValidationError,
    Node,
    NodeId,
    NodeKind,
    UnknownNodeError,
    WarehouseGraph,
)

# The public API of this package: what `from amr_optimizer.warehouse import ...` offers.
__all__ = [
    "LENGTH_TOLERANCE_M",
    "PRESETS",
    "Edge",
    "EdgeKey",
    "GridWarehouseSpec",
    "MapFormatError",
    "MapValidationError",
    "Node",
    "NodeId",
    "NodeKind",
    "UnknownNodeError",
    "WarehouseGraph",
    "generate_grid_warehouse",
    "load_graph",
    "save_graph",
]
