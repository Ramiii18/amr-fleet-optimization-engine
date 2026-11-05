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


def test_delays_can_be_switched_on_with_traffic(capsys: pytest.CaptureFixture[str]) -> None:
    run = [*SMALL_RUN, "--traffic", "--delay-share", "0.3"]

    assert main(run) == 0
    out = capsys.readouterr().out
    assert "30% of edges delayed, ordered execution" in out
    assert "conflicts                 0" in out

    assert main([*run, "--naive", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["delivered"] > 0


@pytest.mark.parametrize(
    "bad",
    [
        ["--delay-share", "0.2"],  # needs --traffic
        ["--charging", "when-low"],  # needs --traffic
        ["--traffic", "--charging", "sometimes"],
        ["--traffic", "--delay-share", "1.5"],
        ["--traffic", "--delay-share", "0.2", "--delay-s", "5", "1"],
    ],
)
def test_bad_delay_options_are_rejected(bad: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([*SMALL_RUN, *bad])

    assert exit_info.value.code == 2


def test_batteries_can_be_switched_on_with_traffic(capsys: pytest.CaptureFixture[str]) -> None:
    run = [*SMALL_RUN, "--traffic", "--charging", "opportunistic"]

    assert main(run) == 0
    out = capsys.readouterr().out
    assert "with traffic (reservations), batteries charged opportunistic" in out
    assert "top-ups" in out and "lowest battery" in out
    assert "WARNING" not in out

    assert main([*run, "--json"]) == 0
    charging = json.loads(capsys.readouterr().out)["charging"]
    assert 0 < charging["lowest_energy_share"] <= 1
