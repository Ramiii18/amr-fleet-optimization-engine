"""Tests for the command-line runner."""

import json
from pathlib import Path

import pytest

from amr_optimizer.cli import main
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse, save_graph

SMALL_RUN = ["--map", "small", "--robots", "2", "--duration-s", "600", "--seed", "3"]


def test_prints_a_kpi_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(SMALL_RUN) == 0

    out = capsys.readouterr().out
    assert "map small (45 nodes), 2 robots" in out
    assert "Policy:   greedy, decides at every event" in out
    assert "throughput" in out and "completion time" in out
    assert "WARNING" not in out


def test_json_output_has_every_kpi(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*SMALL_RUN, "--policy", "hungarian", "--json"]) == 0

    kpis = json.loads(capsys.readouterr().out)
    assert kpis["policy"] == "hungarian"
    assert kpis["delivered"] == kpis["orders"] > 0
    assert {"waiting_p95_s", "utilization", "empty_share"} <= kpis.keys()


def test_decision_rounds_are_reported(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*SMALL_RUN, "--round-s", "10"]) == 0

    assert "decision rounds every 10 s" in capsys.readouterr().out


def test_traffic_can_be_switched_on(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(SMALL_RUN) == 0
    assert "free flow (no traffic)" in capsys.readouterr().out

    assert main([*SMALL_RUN, "--traffic", "--policy", "cpsat-fair"]) == 0
    out = capsys.readouterr().out
    assert "cpsat-fair, decides at every event, with traffic (reservations)" in out
    assert "WARNING" not in out


def test_map_file_can_be_used(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "map.json"
    save_graph(generate_grid_warehouse(PRESETS["small"]), path)

    assert main(["--map", str(path), "--robots", "1", "--duration-s", "300"]) == 0
    assert "(45 nodes)" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [["--round-s", "-1"], ["--policy", "random"], ["--robots", "x"]])
def test_invalid_arguments_exit_with_usage_error(bad: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([*SMALL_RUN, *bad])
    assert exit_info.value.code == 2
