"""Layout files, and one loader for every kind of map file (D37, D38).

Format (version 1)::

    {
      "format": "amr-layout",
      "version": 1,
      "description": "Synthetic hall: two one-way aisles between two cross aisles",
      "cell_m": 1.0,
      "grid": [
        "########",
        "#C####P#",
        ...
      ]
    }

``description`` is optional. It is free text and must never identify a real site: committed
layouts are synthetic (D37). Parsing is strict, as for maps and scenario files: missing fields,
unknown fields and wrong types raise a `LayoutFormatError` that says where the problem is
(``layout.grid[3][7]`` is row 3, column 7). Files are written with one grid row per line, so a
change to the plan shows as a change to the lines it touches.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from amr_optimizer.layout.compiler import compile_layout
from amr_optimizer.layout.model import Layout, LayoutFormatError
from amr_optimizer.warehouse import MapValidationError, WarehouseGraph
from amr_optimizer.warehouse.io import FORMAT_NAME as MAP_FORMAT_NAME
from amr_optimizer.warehouse.io import graph_from_dict

FORMAT_NAME = "amr-layout"
FORMAT_VERSION = 1

_REQUIRED = frozenset({"format", "version", "cell_m", "grid"})
_OPTIONAL = frozenset({"description"})


def layout_from_dict(data: object) -> Layout:
    """Build a layout from parsed JSON. Raises `LayoutFormatError`."""
    if not isinstance(data, dict):
        raise LayoutFormatError("layout: expected an object")
    missing = _REQUIRED - data.keys()
    if missing:
        raise LayoutFormatError(f"layout: missing field(s) {sorted(missing)}")
    unknown = data.keys() - _REQUIRED - _OPTIONAL
    if unknown:
        raise LayoutFormatError(f"layout: unknown field(s) {sorted(unknown)}")
    if data["format"] != FORMAT_NAME:
        raise LayoutFormatError(f"layout.format: must be {FORMAT_NAME!r}, got {data['format']!r}")
    version = data["version"]
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise LayoutFormatError(f"layout.version: unsupported version {version!r}")
    cell_m = data["cell_m"]
    if isinstance(cell_m, bool) or not isinstance(cell_m, int | float):
        raise LayoutFormatError(f"layout.cell_m: expected a number, got {cell_m!r}")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise LayoutFormatError("layout.description: expected a string")
    grid = data["grid"]
    if not isinstance(grid, list):
        raise LayoutFormatError("layout.grid: expected a list of strings, one per row")
    for r, row in enumerate(grid):
        if not isinstance(row, str):
            raise LayoutFormatError(f"layout.grid[{r}]: expected a string, got {row!r}")
    return Layout(tuple(grid), float(cell_m), description)


def layout_to_dict(layout: Layout) -> dict[str, object]:
    data: dict[str, object] = {"format": FORMAT_NAME, "version": FORMAT_VERSION}
    if layout.description:
        data["description"] = layout.description
    data["cell_m"] = layout.cell_m
    data["grid"] = list(layout.grid)
    return data


def save_layout(layout: Layout, path: Path | str) -> None:
    """Write ``layout`` as JSON, one grid row per line, with the same bytes on every OS."""
    lines = ["{", f'  "format": {json.dumps(FORMAT_NAME)},', f'  "version": {FORMAT_VERSION},']
    if layout.description:
        lines.append(f'  "description": {json.dumps(layout.description)},')
    lines += [
        f'  "cell_m": {json.dumps(layout.cell_m)},',
        '  "grid": [',
        ",\n".join(f"    {json.dumps(row)}" for row in layout.grid),
        "  ]",
        "}",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def load_layout(path: Path | str) -> Layout:
    return layout_from_dict(_read_json(path, LayoutFormatError))


def map_from_dict(data: object) -> WarehouseGraph:
    """A graph from parsed JSON in either format, told apart by its ``format`` field: a layout
    is compiled, a map is read as it is. Raises `MapValidationError` (or a subclass)."""
    if isinstance(data, Mapping) and data.get("format") == FORMAT_NAME:
        return compile_layout(layout_from_dict(dict(data)))
    if isinstance(data, Mapping) and "format" in data and data["format"] != MAP_FORMAT_NAME:
        raise MapValidationError(
            f"map: 'format' must be {MAP_FORMAT_NAME!r} or {FORMAT_NAME!r}, "
            f"got {data.get('format')!r}"
        )
    return graph_from_dict(data)


def load_map_file(path: Path | str) -> WarehouseGraph:
    """Read a map file or a layout file (compiled). Raises `MapValidationError` or a subclass."""
    return map_from_dict(_read_json(path, MapValidationError))


def _read_json(path: Path | str, error: type[MapValidationError]) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as problem:
        raise error(f"{path}: not valid JSON ({problem})") from problem
