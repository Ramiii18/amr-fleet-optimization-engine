"""Tests for scenario files: your own map, robots and orders (D32)."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.metrics import compute_kpis
from amr_optimizer.scenario import (
    ScenarioFormatError,
    ScenarioValidationError,
    load_scenario,
    scenario_from_dict,
)
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, MapFormatError, generate_grid_warehouse, save_graph
from amr_optimizer.warehouse.io import graph_to_dict

EXAMPLES = Path(__file__).resolve().parents[3] / "scenarios" / "examples"


def valid_scenario() -> dict[str, Any]:
    return {
        "format": "amr-scenario",
        "version": 1,
        "map": "small",
        "robots": [{"id": "R1", "node": "PK00"}, {"id": "R2", "node": "PK01"}],
        "orders": [
            {"id": "O1", "pickup": "S00-01", "dropoff": "ST00", "release_s": 0},
            {"id": "O2", "pickup": "S01-02", "dropoff": "ST01", "release_s": 2.5},
        ],
    }


def test_a_minimal_file_gets_the_defaults_of_generated_scenarios() -> None:
    scenario = scenario_from_dict(valid_scenario())

    assert len(scenario.graph) == 45  # the small preset
    assert scenario.time_model.tick_s == 0.1
    assert (scenario.speed_mps, scenario.load_ticks, scenario.unload_ticks) == (1.0, 100, 100)
    assert [(r.robot_id, r.node) for r in scenario.robots] == [("R1", "PK00"), ("R2", "PK01")]
    assert all(r.is_idle and r.since_tick == 0 for r in scenario.robots)
    assert [(o.id, o.pickup, o.dropoff, o.release_tick) for o in scenario.orders] == [
        ("O1", "S00-01", "ST00", 0),
        ("O2", "S01-02", "ST01", 25),
    ]


def test_optional_settings_are_applied() -> None:
    data = valid_scenario() | {
        "description": "a test",
        "tick_s": 0.2,
        "speed_mps": 1.5,
        "load_s": 8,
        "unload_s": 0,
    }

    scenario = scenario_from_dict(data)

    assert scenario.time_model.tick_s == 0.2
    assert (scenario.speed_mps, scenario.load_ticks, scenario.unload_ticks) == (1.5, 40, 0)
    assert scenario.orders[1].release_tick == 13  # 2.5 s / 0.2 s = 12.5 ticks, rounded up


def test_orders_are_sorted_by_release_keeping_the_file_order_among_ties() -> None:
    data = valid_scenario()
    data["orders"] = [
        {"id": "late", "pickup": "S00-01", "dropoff": "ST00", "release_s": 30},
        {"id": "first", "pickup": "S00-02", "dropoff": "ST00", "release_s": 0},
        {"id": "second", "pickup": "S00-03", "dropoff": "ST01", "release_s": 0},
    ]

    scenario = scenario_from_dict(data)

    assert [o.id for o in scenario.orders] == ["first", "second", "late"]


def test_the_map_can_be_a_file_next_to_the_scenario_or_inline(tmp_path: Path) -> None:
    graph = generate_grid_warehouse(PRESETS["small"])
    (tmp_path / "maps").mkdir()
    save_graph(graph, tmp_path / "maps" / "mine.json")

    by_relative_path = valid_scenario() | {"map": "maps/mine.json"}
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(by_relative_path), encoding="utf-8")
    from_file = load_scenario(path)

    by_absolute_path = valid_scenario() | {"map": str(tmp_path / "maps" / "mine.json")}
    inline = valid_scenario() | {"map": graph_to_dict(graph)}

    for scenario in (from_file, scenario_from_dict(by_absolute_path), scenario_from_dict(inline)):
        assert scenario.graph.nodes == graph.nodes
        assert scenario.graph.edges == graph.edges


def test_an_unknown_map_names_the_presets() -> None:
    with pytest.raises(ScenarioFormatError, match=r"neither a preset \(small, medium, large\)"):
        scenario_from_dict(valid_scenario() | {"map": "nowhere.json"})


def test_an_invalid_inline_map_raises_the_map_error() -> None:
    bad_map = graph_to_dict(generate_grid_warehouse(PRESETS["small"])) | {"format": "other"}

    with pytest.raises(MapFormatError, match="format"):
        scenario_from_dict(valid_scenario() | {"map": bad_map})


def _mutate(path: str, value: object) -> dict[str, Any]:
    """A valid scenario with one field replaced; ``value=...`` deletes it."""
    data = copy.deepcopy(valid_scenario())
    *parents, last = (int(key) if key.isdigit() else key for key in path.split("."))
    target: Any = data
    for key in parents:
        target = target[key]
    if value is ...:
        del target[last]
    else:
        target[last] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("format", "amr-warehouse-map", "scenario.format: must be 'amr-scenario'"),
        ("version", 2, "unsupported version 2"),
        ("version", True, "unsupported version True"),
        ("robots", ..., r"missing field\(s\) \['robots'\]"),
        ("colour", "blue", r"unknown field\(s\) \['colour'\]"),
        ("description", 7, "scenario.description: expected a string"),
        ("map", 3, "expected a preset name, a map or layout file path, or a map object"),
        ("robots", {"id": "R1"}, "scenario.robots: expected a list"),
        ("robots.0", "R1", r"robots\[0\]: expected an object"),
        ("robots.0.node", 5, r"robots\[0\].node: expected a non-empty string"),
        ("robots.1.id", "", r"robots\[1\].id: expected a non-empty string"),
        ("orders.1.release_s", "soon", r"orders\[1\].release_s: expected a number"),
        ("orders.1.release_s", -1, r"orders\[1\].release_s: must be non-negative"),
        ("orders.0.priority", 1, r"orders\[0\]: unknown field\(s\) \['priority'\]"),
        ("speed_mps", 0, "scenario.speed_mps: must be positive"),
        ("tick_s", float("nan"), "scenario.tick_s: expected a number"),
        ("load_s", -5, "scenario.load_s: must be non-negative"),
    ],
)
def test_format_errors_say_where(path: str, value: object, message: str) -> None:
    with pytest.raises(ScenarioFormatError, match=message):
        scenario_from_dict(_mutate(path, value))


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("robots.0.node", "NOWHERE", "robot 'R1': unknown node 'NOWHERE'"),
        ("robots.1.id", "R1", r"duplicate robot ids: \['R1'\]"),
        ("robots.1.node", "PK00", r"several robots start on the same node: \['PK00'\]"),
        ("orders.1.id", "O1", r"duplicate order ids: \['O1'\]"),
        ("orders.0.dropoff", "S00-01", r"orders\[0\]: "),  # pickup and drop-off are the same
        ("orders.1.pickup", "NOWHERE", "order 'O2' pickup: unknown node 'NOWHERE'"),
    ],
)
def test_scenarios_that_do_not_fit_together_are_rejected(
    path: str, value: object, message: str
) -> None:
    with pytest.raises(ScenarioValidationError, match=message):
        scenario_from_dict(_mutate(path, value))


def test_a_file_that_is_not_json_is_a_format_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"format": "amr-scenario",', encoding="utf-8")

    with pytest.raises(ScenarioFormatError, match="not valid JSON"):
        load_scenario(path)


@pytest.mark.parametrize("example", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.stem)
def test_every_example_runs_to_completion_without_conflicts(example: Path) -> None:
    scenario = load_scenario(example)

    for policy in ("greedy", "cpsat-fair"):
        kpis = compute_kpis(simulate(scenario, POLICIES[policy](), traffic=True))
        assert kpis.delivered == kpis.orders == len(scenario.orders) > 0
        assert kpis.conflicts == 0


def test_the_examples_directory_is_not_empty() -> None:
    assert list(EXAMPLES.glob("*.json"))
