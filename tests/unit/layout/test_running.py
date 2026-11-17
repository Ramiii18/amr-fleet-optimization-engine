"""Running on layouts (L3a): the examples under traffic, the layout command, the lab and traces."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from amr_optimizer.cli import main as amr_sim
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.lab import experiment_from_dict, load_experiment, run_experiment
from amr_optimizer.lab.runner import map_hash
from amr_optimizer.layout import compile_layout, load_layout, load_map_file
from amr_optimizer.layout.__main__ import main as layout_command
from amr_optimizer.metrics import compute_kpis
from amr_optimizer.scenario import ScenarioSpec, generate_scenario, load_scenario
from amr_optimizer.sim import simulate
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse, load_graph

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "layouts" / "examples"
TRACE = ROOT / "tests" / "data" / "layout" / "two_aisles.trace.json"
TRACE_RUN = [
    *("--robots", "2", "--orders-per-hour", "240", "--duration-s", "300", "--seed", "4"),
    *("--delay-share", "0.2"),
]

FLEETS = {"two_aisles": 2, "small_hall": 6, "l_hall": 8}


@pytest.mark.parametrize("policy", ["greedy", "cpsat-fair"])
@pytest.mark.parametrize("name", sorted(FLEETS))
def test_every_example_runs_with_traffic_without_conflicts(name: str, policy: str) -> None:
    graph = load_map_file(EXAMPLES / f"{name}.layout.json")
    spec = ScenarioSpec(robots=FLEETS[name], orders_per_hour=150.0, duration_s=600.0)
    scenario = generate_scenario(graph, spec, seed=7)
    result = simulate(scenario, POLICIES[policy](), traffic=True)
    kpis = compute_kpis(result)
    assert kpis.orders > 10
    assert result.all_delivered
    assert kpis.conflicts == 0


# --- the layout command -------------------------------------------------------------------------


def test_compile_writes_the_map_of_a_layout(tmp_path: Path) -> None:
    out = tmp_path / "hall.json"
    assert layout_command(["compile", str(EXAMPLES / "small_hall.layout.json"), str(out)]) == 0
    expected = tmp_path / "expected.json"
    from amr_optimizer.warehouse import save_graph

    save_graph(compile_layout(load_layout(EXAMPLES / "small_hall.layout.json")), expected)
    assert out.read_bytes() == expected.read_bytes()
    assert len(load_graph(out)) == 101


def test_check_reports_and_sets_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    two_aisles = str(EXAMPLES / "two_aisles.layout.json")
    assert layout_command(["check", two_aisles, "--robots", "2"]) == 0
    assert "the map is fit for traffic" in capsys.readouterr().out
    assert layout_command(["check", two_aisles, "--robots", "3"]) == 1
    assert "3 robots need at least 8 holding spots" in capsys.readouterr().out
    assert layout_command(["check", two_aisles, "--robots", "2", "--put-away"]) == 0
    assert layout_command(["check", two_aisles, "--robots", "2", "--batteries"]) == 0
    broken = tmp_path / "broken.layout.json"
    broken.write_text('{"format": "amr-layout"}', encoding="utf-8")
    assert layout_command(["check", str(broken)]) == 2
    assert "missing field(s)" in capsys.readouterr().err


def test_the_command_runs_as_a_module() -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "amr_optimizer.layout",
            "check",
            str(EXAMPLES / "l_hall.layout.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "273 nodes" in done.stdout


# --- amr-sim and scenario files -----------------------------------------------------------------


def test_amr_sim_runs_a_layout_file_with_traffic(capsys: pytest.CaptureFixture[str]) -> None:
    path = EXAMPLES / "small_hall.layout.json"
    assert amr_sim(["--map", str(path), "--robots", "4", "--duration-s", "300", "--traffic"]) == 0
    out = capsys.readouterr().out
    assert "(101 nodes)" in out
    assert "WARNING" not in out
    assert "conflicts                 0" in out


def test_a_scenario_file_can_name_a_layout_file(tmp_path: Path) -> None:
    (tmp_path / "hall.layout.json").write_bytes((EXAMPLES / "two_aisles.layout.json").read_bytes())
    scenario_file = tmp_path / "run.json"
    scenario_file.write_text(
        json.dumps(
            {
                "format": "amr-scenario",
                "version": 1,
                "map": "hall.layout.json",
                "robots": [{"id": "R1", "node": "PK-r001-c006"}],
                "orders": [
                    {"id": "O1", "pickup": "S-r004-c002", "dropoff": "ST-r009-c001", "release_s": 0}
                ],
            }
        ),
        encoding="utf-8",
    )
    scenario = load_scenario(scenario_file)
    assert len(scenario.graph) == 30
    assert amr_sim(["--scenario", str(scenario_file), "--traffic"]) == 0


# --- the lab ------------------------------------------------------------------------------------


def experiment(map_name: str) -> dict[str, Any]:
    return {
        "format": "amr-experiment",
        "version": 1,
        "name": "layout",
        "families": [
            {
                "name": "hall-4",
                "map": map_name,
                "robots": 4,
                "orders_per_hour": 150.0,
                "duration_s": 300.0,
            }
        ],
        "policies": ["greedy", "insertion-fair"],
        "baseline": "greedy",
        "seeds": [0, 1],
        "disturbances": {"model": "schedule", "delay_share": 0.1, "delay_max_s": 3.0},
        "resamples": 200,
    }


def test_the_lab_runs_a_layout_file_named_next_to_the_experiment(tmp_path: Path) -> None:
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "hall.layout.json").write_bytes(
        (EXAMPLES / "small_hall.layout.json").read_bytes()
    )
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(experiment("plans/hall.layout.json")), encoding="utf-8")
    loaded = load_experiment(path)
    assert loaded.to_dict()["families"] == experiment("plans/hall.layout.json")["families"]
    assert "base_dir" not in loaded.to_dict()
    rows = run_experiment(loaded)
    expected = map_hash(load_map_file(EXAMPLES / "small_hall.layout.json"))
    assert {row["map_hash"] for row in rows} == {expected}
    assert all(row["all_delivered"] for row in rows)
    for seed in (0, 1):
        greedy, fair = (row for row in rows if row["seed"] == seed)
        assert greedy["fingerprint"] == fair["fingerprint"]


def test_the_map_hash_tells_maps_apart_and_ignores_the_file_name() -> None:
    hashes = {name: map_hash(generate_grid_warehouse(PRESETS[name])) for name in PRESETS}
    assert len(set(hashes.values())) == 3
    layout = map_hash(load_map_file(EXAMPLES / "two_aisles.layout.json"))
    compiled = map_hash(compile_layout(load_layout(EXAMPLES / "two_aisles.layout.json")))
    assert layout == compiled
    assert layout not in hashes.values()


def test_an_unknown_map_path_is_refused_with_the_family() -> None:
    from amr_optimizer.lab import ExperimentValidationError

    with pytest.raises(ExperimentValidationError, match="family 'hall-4': unknown map 'nowhere"):
        experiment_from_dict(experiment("nowhere.layout.json"), base_dir=ROOT)


# --- a trace of a layout run, for the viewer ----------------------------------------------------


def test_the_layout_trace_fixture_is_reproduced(tmp_path: Path) -> None:
    """The viewer's tests read this trace; it must be what the engine writes today."""
    fresh = tmp_path / "trace.json"
    run = ["--map", str(EXAMPLES / "two_aisles.layout.json"), *TRACE_RUN, "--traffic"]
    assert amr_sim([*run, "--trace", str(fresh)]) == 0
    assert fresh.read_bytes() == TRACE.read_bytes(), "regenerate: see this test's command"
    trace = json.loads(TRACE.read_text(encoding="ascii"))
    assert trace["run"]["map"] == "two_aisles.layout.json"
    assert len(trace["map"]["nodes"]) == 30
