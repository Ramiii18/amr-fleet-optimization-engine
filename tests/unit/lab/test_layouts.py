"""Comparing layouts in the lab (D41): pairing across layouts, version 2 files, batteries."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from amr_optimizer.lab import (
    ExperimentValidationError,
    UnpairedRunsError,
    analyse,
    experiment_from_dict,
    load_experiment,
    report,
    results_to_dict,
    run_experiment,
)
from amr_optimizer.layout import Layout, compile_layout, load_layout, save_layout
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.traffic import DisturbanceSpec, generate_disturbances
from amr_optimizer.warehouse import NodeId, NodeKind, WarehouseGraph

ROOT = Path(__file__).resolve().parents[3]
HALL = load_layout(ROOT / "layouts" / "examples" / "small_hall.layout.json")


def edited(layout: Layout, cells: dict[tuple[int, int], str]) -> Layout:
    rows = [list(row) for row in layout.grid]
    for (r, c), char in cells.items():
        rows[r][c] = char
    return Layout(tuple("".join(row) for row in rows), layout.cell_m, layout.description)


# The baseline has stations in columns 2, 6, 9 of row 12 and chargers in columns 2, 8 of row 1.
# "moved" puts the first station and the first charger one slot over: same storage, same counts.
MOVED = edited(HALL, {(12, 2): "?", (12, 3): "T", (1, 2): "?", (1, 4): "C"})
# "fewer" loses one storage cell: its orders cannot be the same by index.
FEWER = edited(HALL, {(4, 2): "#"})


def rank(graph: WarehouseGraph, node: NodeId, kind: NodeKind) -> int:
    return sorted(n.id for n in graph.nodes_of_kind(kind)).index(node)


@pytest.mark.parametrize("seed", range(5))
def test_same_storage_and_station_count_give_the_same_orders_by_index(seed: int) -> None:
    base, moved = compile_layout(HALL), compile_layout(MOVED)
    spec = ScenarioSpec(robots=4, orders_per_hour=300.0, duration_s=600.0)
    a, b = generate_scenario(base, spec, seed=seed), generate_scenario(moved, spec, seed=seed)
    assert len(a.orders) == len(b.orders) > 20
    for x, y in zip(a.orders, b.orders, strict=True):
        assert (x.id, x.release_tick, x.pickup) == (y.id, y.release_tick, y.pickup)
        assert rank(base, x.dropoff, NodeKind.STATION) == rank(moved, y.dropoff, NodeKind.STATION)
    starts = [(r.robot_id, r.node) for r in a.robots], [(r.robot_id, r.node) for r in b.robots]
    assert starts[0] != starts[1], "the third robot starts on the first charger, which moved"
    for (_, here), (_, there) in zip(*starts, strict=True):
        kind = base.node(here).kind
        assert moved.node(there).kind is kind
        if kind in (NodeKind.CHARGER, NodeKind.PARKING):
            assert rank(base, here, kind) == rank(moved, there, kind)


def test_shared_corridors_meet_the_same_episodes_and_robots_the_same_breakdowns() -> None:
    base, moved = compile_layout(HALL), compile_layout(MOVED)
    spec = DisturbanceSpec(
        horizon_ticks=36_000,
        delay_share=0.2,
        episode_min_ticks=100,
        episode_max_ticks=400,
        breakdown_gap_ticks=3000,
        repair_min_ticks=100,
        repair_max_ticks=300,
    )
    robots = ["R01", "R02", "R03"]
    a = generate_disturbances(base, robots, spec, 3)  # type: ignore[arg-type]
    b = generate_disturbances(moved, robots, spec, 3)  # type: ignore[arg-type]
    assert a.breakdowns == b.breakdowns
    shared = {e.corridor for e in a.episodes} & {e.corridor for e in b.episodes}
    assert len(shared) > 100
    assert [e for e in a.episodes if e.corridor in shared] == [
        e for e in b.episodes if e.corridor in shared
    ]
    assert {e.corridor for e in a.episodes} != {e.corridor for e in b.episodes}


# --- a layout experiment ------------------------------------------------------------------------


def layout_experiment(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": "amr-experiment",
        "version": 2,
        "name": "stations",
        "families": [
            {"name": "hall-4", "robots": 4, "orders_per_hour": 150.0, "duration_s": 300.0}
        ],
        "layouts": [
            {"name": "baseline", "map": "baseline.layout.json"},
            {"name": "moved", "map": "moved.layout.json"},
        ],
        "policies": ["greedy"],
        "baseline": "baseline",
        "seeds": [0, 1, 2],
        "disturbances": {
            "model": "schedule",
            "delay_share": 0.1,
            "delay_max_s": 3.0,
            "breakdown_gap_s": 150.0,
            "repair_min_s": 10.0,
            "repair_max_s": 40.0,
            "recovery": "replan",
        },
        "kpis": ["completion_max_s"],
        "margin": 5.0,
        "resamples": 200,
    }
    data.update(changes)
    return data


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    save_layout(HALL, tmp_path / "baseline.layout.json")
    save_layout(MOVED, tmp_path / "moved.layout.json")
    save_layout(FEWER, tmp_path / "fewer.layout.json")
    return tmp_path


def test_layouts_are_compared_per_policy_paired_by_seed(folder: Path) -> None:
    path = folder / "experiment.json"
    path.write_text(json.dumps(layout_experiment()), encoding="utf-8")
    experiment = load_experiment(path)
    assert experiment.compares_layouts and experiment.version == 2
    written = experiment.to_dict()
    assert experiment_from_dict(written, base_dir=folder) == experiment
    assert written["layouts"] == layout_experiment()["layouts"]
    assert "map" not in written["families"][0]  # type: ignore[index]

    rows = run_experiment(experiment)
    assert [(r["layout"], r["seed"]) for r in rows] == [
        (layout, seed) for layout in ("baseline", "moved") for seed in (0, 1, 2)
    ]
    assert all(r["all_delivered"] for r in rows)
    for seed in (0, 1, 2):
        base, moved = (r for r in rows if r["seed"] == seed)
        assert base["pairing"] == moved["pairing"]
        assert base["fingerprint"] != moved["fingerprint"]  # different station and charger ids
        assert base["map_hash"] != moved["map_hash"]

    primary, other = analyse(experiment, rows)
    assert (primary.kpi, primary.variant, primary.baseline) == (
        "completion_mean_s",
        "hall-4/greedy/moved",
        "baseline",
    )
    assert primary.holm_p == pytest.approx(primary.p_value)
    moved_values = [float(str(r["completion_mean_s"])) for r in rows if r["layout"] == "moved"]
    base_values = [float(str(r["completion_mean_s"])) for r in rows if r["layout"] == "baseline"]
    assert primary.difference == pytest.approx(
        sum(m - b for m, b in zip(moved_values, base_values, strict=True)) / 3
    )
    assert other.kpi == "completion_max_s"
    results = results_to_dict(experiment, rows, [primary, other], commit=None)
    assert results["version"] == 2
    assert "| Family / policy / layout | KPI | Layout | Baseline |" in report(
        experiment, rows, [primary, other]
    )


def test_layouts_that_cannot_be_paired_are_not_compared(folder: Path) -> None:
    data = layout_experiment(
        layouts=[
            {"name": "baseline", "map": "baseline.layout.json"},
            {"name": "fewer", "map": "fewer.layout.json"},
        ],
        seeds=[0],
    )
    experiment = experiment_from_dict(data, base_dir=folder)
    rows = run_experiment(experiment)
    assert rows[0]["pairing"] != rows[1]["pairing"]
    with pytest.raises(UnpairedRunsError, match="the layouts faced different conditions"):
        analyse(experiment, rows)


def test_a_missing_run_is_named(folder: Path) -> None:
    experiment = experiment_from_dict(layout_experiment(seeds=[0]), base_dir=folder)
    rows = run_experiment(experiment)
    with pytest.raises(UnpairedRunsError, match="no run for"):
        analyse(experiment, rows[:-1])


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"baseline": "greedy"}, "the baseline 'greedy' is not among the layouts"),
        (
            {"layouts": [{"name": "baseline", "map": "baseline.layout.json"}]},
            "at least two layouts",
        ),
        (
            {"layouts": [{"name": "a", "map": "baseline.layout.json"}] * 2, "baseline": "a"},
            "distinct names",
        ),
        (
            {
                "layouts": [
                    {"name": "baseline", "map": "baseline.layout.json"},
                    {"name": "x", "map": "no.json"},
                ]
            },
            "layout 'x': unknown map 'no.json'",
        ),
        (
            {
                "families": [
                    {
                        "name": "f",
                        "map": "small",
                        "robots": 4,
                        "orders_per_hour": 9.0,
                        "duration_s": 9.0,
                    }
                ]
            },
            r"families\[0\]: unknown field\(s\) \['map'\]",
        ),
        ({"policies": []}, "at least one distinct"),
        ({"layouts": {}}, "experiment.layouts: expected a list"),
    ],
)
def test_a_broken_layout_experiment_is_refused(
    folder: Path, changes: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ExperimentValidationError, match=reason):
        experiment_from_dict(layout_experiment(**changes), base_dir=folder)


# --- version 1 is unchanged; batteries --------------------------------------------------------


@pytest.mark.parametrize("name", ["dispatch_disturbed.json", "dispatch_disturbed_moderate.json"])
def test_committed_version_1_files_read_and_write_back_unchanged(name: str) -> None:
    path = ROOT / "experiments" / name
    experiment = load_experiment(path)
    assert experiment.version == 1 and not experiment.compares_layouts
    assert experiment.battery is None
    assert experiment.to_dict() == json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("field", ["layouts", "battery"])
def test_version_1_does_not_take_the_new_parts(field: str) -> None:
    data = json.loads((ROOT / "experiments" / "dispatch_disturbed.json").read_text("utf-8"))
    data[field] = {} if field == "battery" else []
    with pytest.raises(ExperimentValidationError, match=rf"unknown field\(s\) \['{field}'\]"):
        experiment_from_dict(data)


def battery_experiment(**battery: Any) -> dict[str, Any]:
    return {
        "format": "amr-experiment",
        "version": 2,
        "name": "batteries",
        "families": [
            {
                "name": "small-3",
                "map": "small",
                "robots": 3,
                "orders_per_hour": 200.0,
                "duration_s": 300.0,
            }
        ],
        "policies": ["greedy", "insertion-fair"],
        "baseline": "greedy",
        "seeds": [0, 1],
        "disturbances": {"model": "none"},
        "kpis": ["charger_wait_s"],
        "battery": {"charging": "opportunistic", "start_min_share": 0.26, "start_max_share": 0.3}
        | battery,
        "resamples": 200,
    }


def test_robots_run_on_batteries_and_charging_kpis_can_be_compared() -> None:
    experiment = experiment_from_dict(battery_experiment())
    assert experiment.version == 2 and not experiment.compares_layouts
    written = experiment.to_dict()
    assert experiment_from_dict(written) == experiment
    assert written["battery"]["start_max_share"] == 0.3  # type: ignore[index]
    rows = run_experiment(experiment)
    assert all(r["all_delivered"] for r in rows)
    assert sum(int(str(r["charging_trips"])) for r in rows) > 0, "robots start nearly low"
    assert {"out_of_service_share", "charger_occupancy", "lowest_energy_share"} <= rows[0].keys()
    primary, charger = analyse(experiment, rows)
    assert charger.kpi == "charger_wait_s"
    plain = copy.deepcopy(battery_experiment())
    del plain["battery"]
    plain["kpis"] = []
    without = run_experiment(experiment_from_dict(plain))
    assert rows[0]["fingerprint"] != without[0]["fingerprint"], "the battery is a condition"


@pytest.mark.parametrize(
    ("battery", "reason"),
    [
        ({"charging": "sometimes"}, "unknown charging policy 'sometimes'"),
        ({"low_share": 0.95}, "battery: thresholds need"),
        ({"capacity_m": "a lot"}, "battery.capacity_m: expected float"),
        ({"colour": 1}, r"battery: unknown field\(s\) \['colour'\]"),
    ],
)
def test_a_broken_battery_is_refused(battery: dict[str, Any], reason: str) -> None:
    with pytest.raises(ExperimentValidationError, match=reason):
        experiment_from_dict(battery_experiment(**battery))


def test_charging_kpis_need_a_battery() -> None:
    data = battery_experiment()
    del data["battery"]
    with pytest.raises(ExperimentValidationError, match="unknown KPI 'charger_wait_s'"):
        experiment_from_dict(data)


# --- named primary contrasts (L7) ---------------------------------------------------------------

OTHER = edited(HALL, {(12, 9): "?", (12, 8): "T"})


def test_named_contrasts_are_the_only_primary_tests(folder: Path) -> None:
    save_layout(OTHER, folder / "other.layout.json")
    data = layout_experiment(
        layouts=[
            {"name": "baseline", "map": "baseline.layout.json"},
            {"name": "moved", "map": "moved.layout.json"},
            {"name": "other", "map": "other.layout.json"},
        ],
        contrasts=[["moved", "other"], ["moved", "baseline"]],
        seeds=[0, 1, 2],
    )
    experiment = experiment_from_dict(data, base_dir=folder)
    assert experiment.contrasts == (("moved", "other"), ("moved", "baseline"))
    assert experiment.to_dict()["contrasts"] == [["moved", "other"], ["moved", "baseline"]]
    contrasts = analyse(experiment, run_experiment(experiment))
    rows = [(c.variant, c.baseline, c.kpi, c.holm_p is not None) for c in contrasts]
    assert rows == [
        ("hall-4/greedy/moved", "other", "completion_mean_s", True),
        ("hall-4/greedy/moved", "other", "completion_max_s", False),
        ("hall-4/greedy/moved", "baseline", "completion_mean_s", True),
        ("hall-4/greedy/moved", "baseline", "completion_max_s", False),
        ("hall-4/greedy/other", "baseline", "completion_mean_s", False),  # exploratory
        ("hall-4/greedy/other", "baseline", "completion_max_s", False),
    ]
    primary = [contrasts[0], contrasts[2]]
    from amr_optimizer.lab import holm

    expected = holm([c.p_value for c in primary])
    assert [c.holm_p for c in primary] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("contrasts", "reason"),
    [
        ([["moved", "nowhere"]], "needs two different layouts"),
        ([["moved", "moved"]], "needs two different layouts"),
        ([["moved", "baseline"], ["moved", "baseline"]], "listed once"),
        ([["moved"]], r"contrasts\[0\]: expected \[variant, reference\]"),
        ("moved", "expected a list of pairs"),
    ],
)
def test_broken_contrasts_are_refused(folder: Path, contrasts: object, reason: str) -> None:
    with pytest.raises(ExperimentValidationError, match=reason):
        experiment_from_dict(layout_experiment(contrasts=contrasts), base_dir=folder)


def test_contrasts_need_a_layout_comparison() -> None:
    data = battery_experiment()
    data["contrasts"] = [["a", "b"]]
    with pytest.raises(ExperimentValidationError, match="need a layout comparison"):
        experiment_from_dict(data)
