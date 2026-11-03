"""Tests for reading and writing warehouse maps as JSON."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from amr_optimizer.warehouse import (
    PRESETS,
    MapFormatError,
    MapValidationError,
    WarehouseGraph,
    generate_grid_warehouse,
    load_graph,
    save_graph,
)
from amr_optimizer.warehouse.io import graph_from_dict, graph_to_dict

MAPS_DIR = Path(__file__).resolve().parents[3] / "scenarios" / "maps"


def valid_map() -> dict[str, Any]:
    return {
        "format": "amr-warehouse-map",
        "version": 1,
        "nodes": [
            {"id": "A", "x": 0, "y": 0, "kind": "station"},
            {"id": "B", "x": 1.5, "y": 0.0, "kind": "intersection"},
        ],
        "edges": [{"source": "A", "target": "B", "length_m": 1.5}],
    }


def assert_same_graph(first: WarehouseGraph, second: WarehouseGraph) -> None:
    assert first.nodes == second.nodes
    assert first.edges == second.edges


# --- round trips --------------------------------------------------------------------------


def test_save_then_load_returns_the_same_graph(tmp_path: Path) -> None:
    graph = generate_grid_warehouse(PRESETS["small"])
    path = tmp_path / "small.json"

    save_graph(graph, path)

    assert_same_graph(load_graph(path), graph)


def test_saved_file_has_one_node_or_edge_per_line(tmp_path: Path) -> None:
    graph = generate_grid_warehouse(PRESETS["small"])
    path = tmp_path / "small.json"

    save_graph(graph, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(graph) + graph.num_edges + 8
    json.loads(path.read_text(encoding="utf-8"))  # and it is still plain JSON
    assert b"\r\n" not in path.read_bytes()  # LF line endings on every OS


def test_empty_graph_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"

    save_graph(WarehouseGraph([], []), path)

    assert len(load_graph(path)) == 0


def test_dict_round_trip_accepts_integer_coordinates() -> None:
    graph = graph_from_dict(valid_map())

    assert graph.node(graph.nodes[0].id).x == 0.0
    assert graph_from_dict(graph_to_dict(graph)).nodes == graph.nodes


@pytest.mark.parametrize("name", PRESETS)
def test_committed_example_maps_match_the_generator(name: str) -> None:
    path = MAPS_DIR / f"{name}.json"
    assert path.exists(), f"{path} missing: run scripts/generate_maps.py"

    assert_same_graph(load_graph(path), generate_grid_warehouse(PRESETS[name]))


# --- strict parsing -----------------------------------------------------------------------


def broken(change: str) -> object:
    data = valid_map()
    match change:
        case "top-level list":
            return []
        case "wrong format":
            data["format"] = "something-else"
        case "wrong version":
            data["version"] = 2
        case "boolean version":
            data["version"] = True
        case "nodes not a list":
            data["nodes"] = {}
        case "missing field":
            del data["nodes"][1]["y"]
        case "unknown field":
            data["edges"][0]["speed"] = 1.0
        case "string number":
            data["nodes"][1]["x"] = "1.5"
        case "boolean number":
            data["edges"][0]["length_m"] = True
        case "unknown kind":
            data["nodes"][0]["kind"] = "elevator"
        case "numeric id":
            data["nodes"][0]["id"] = 7
    return data


@pytest.mark.parametrize(
    ("change", "location"),
    [
        ("top-level list", "map"),
        ("wrong format", "map"),
        ("wrong version", "map"),
        ("boolean version", "map"),
        ("nodes not a list", "map.nodes"),
        ("missing field", "nodes[1]"),
        ("unknown field", "edges[0]"),
        ("string number", "nodes[1].x"),
        ("boolean number", "edges[0].length_m"),
        ("unknown kind", "nodes[0].kind"),
        ("numeric id", "nodes[0].id"),
    ],
)
def test_malformed_maps_report_where_the_problem_is(change: str, location: str) -> None:
    with pytest.raises(MapFormatError, match="^" + re.escape(location)):
        graph_from_dict(broken(change))


def test_graph_rules_still_apply_after_parsing() -> None:
    data = valid_map()
    data["nodes"][1]["id"] = "A"  # duplicate id

    with pytest.raises(MapValidationError, match="duplicate node id"):
        graph_from_dict(data)


def test_invalid_json_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(MapFormatError, match="not valid JSON"):
        load_graph(path)
