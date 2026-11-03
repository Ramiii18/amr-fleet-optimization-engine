"""Read and write warehouse maps as JSON files.

Format (version 1)::

    {
      "format": "amr-warehouse-map",
      "version": 1,
      "nodes": [{"id": "X00-00", "x": 0.0, "y": 0.0, "kind": "intersection"}, ...],
      "edges": [{"source": "X00-00", "target": "X00-01", "length_m": 2.0}, ...]
    }

Parsing is strict. Missing fields, unknown fields, wrong types and unknown node kinds are
rejected with a `MapFormatError` that says where the problem is (e.g. ``nodes[3].x``).
After parsing, the usual graph validation applies. Files are written with one node or
edge per line, so map changes produce readable diffs.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from amr_optimizer.warehouse.model import (
    Edge,
    MapValidationError,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
)

FORMAT_NAME = "amr-warehouse-map"
FORMAT_VERSION = 1


class MapFormatError(MapValidationError):
    """A map file is not valid JSON in the expected structure."""


def graph_to_dict(graph: WarehouseGraph) -> dict[str, Any]:
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "nodes": [
            {"id": node.id, "x": node.x, "y": node.y, "kind": node.kind.value} for node in graph
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "length_m": edge.length_m}
            for edge in graph.edges
        ],
    }


def graph_from_dict(data: object) -> WarehouseGraph:
    """Build a graph from parsed JSON. Raises `MapFormatError` or `MapValidationError`."""
    top = _as_object(data, "map", {"format", "version", "nodes", "edges"})
    if top["format"] != FORMAT_NAME:
        raise MapFormatError(f"map: 'format' must be {FORMAT_NAME!r}, got {top['format']!r}")
    if top["version"] != FORMAT_VERSION or isinstance(top["version"], bool):
        raise MapFormatError(f"map: unsupported version {top['version']!r}")

    nodes = [_parse_node(item, f"nodes[{i}]") for i, item in enumerate(_as_list(top, "nodes"))]
    edges = [_parse_edge(item, f"edges[{i}]") for i, item in enumerate(_as_list(top, "edges"))]
    return WarehouseGraph(nodes, edges)


def save_graph(graph: WarehouseGraph, path: Path | str) -> None:
    """Write ``graph`` as JSON, one node or edge per line."""
    data = graph_to_dict(graph)
    lines = [
        "{",
        f'  "format": {json.dumps(data["format"])},',
        f'  "version": {json.dumps(data["version"])},',
        '  "nodes": [',
        ",\n".join(f"    {_one_line(node)}" for node in data["nodes"]),
        "  ],",
        '  "edges": [',
        ",\n".join(f"    {_one_line(edge)}" for edge in data["edges"]),
        "  ]",
        "}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _one_line(item: object) -> str:
    return json.dumps(item, separators=(", ", ": "))


def load_graph(path: Path | str) -> WarehouseGraph:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MapFormatError(f"{path}: not valid JSON ({error})") from error
    return graph_from_dict(data)


# --- strict field parsing -----------------------------------------------------------------


def _as_object(value: object, where: str, fields: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise MapFormatError(f"{where}: expected an object")
    missing = fields - value.keys()
    if missing:
        raise MapFormatError(f"{where}: missing field(s) {sorted(missing)}")
    unknown = value.keys() - fields
    if unknown:
        raise MapFormatError(f"{where}: unknown field(s) {sorted(unknown)}")
    return value


def _as_list(obj: Mapping[str, object], field: str) -> list[object]:
    value = obj[field]
    if not isinstance(value, list):
        raise MapFormatError(f"map.{field}: expected a list")
    return value


def _string(obj: Mapping[str, object], field: str, where: str) -> str:
    value = obj[field]
    if not isinstance(value, str):
        raise MapFormatError(f"{where}.{field}: expected a string, got {value!r}")
    return value


def _number(obj: Mapping[str, object], field: str, where: str) -> float:
    value = obj[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MapFormatError(f"{where}.{field}: expected a number, got {value!r}")
    return float(value)


def _parse_node(value: object, where: str) -> Node:
    obj = _as_object(value, where, {"id", "x", "y", "kind"})
    kind_text = _string(obj, "kind", where)
    try:
        kind = NodeKind(kind_text)
    except ValueError:
        allowed = [kind.value for kind in NodeKind]
        raise MapFormatError(f"{where}.kind: {kind_text!r} is not one of {allowed}") from None
    return Node(
        NodeId(_string(obj, "id", where)), _number(obj, "x", where), _number(obj, "y", where), kind
    )


def _parse_edge(value: object, where: str) -> Edge:
    obj = _as_object(value, where, {"source", "target", "length_m"})
    return Edge(
        NodeId(_string(obj, "source", where)),
        NodeId(_string(obj, "target", where)),
        _number(obj, "length_m", where),
    )
