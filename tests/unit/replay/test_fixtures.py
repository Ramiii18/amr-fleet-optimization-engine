"""The committed golden fixtures of the replay viewer are current, and say what Python says.

The frontend tests reproduce these files from the trace alone (D34). They are only worth
that if they are exactly what the simulator and the reference functions produce today.
"""

import importlib.util
import json
import math
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from amr_optimizer.replay import ReplayTrace, trace_from_json
from amr_optimizer.viz.motion import ActivityTimeline, position_at

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "data" / "replay"
NAMES = ("free_flow", "cut_short", "disturbed")


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_replay_fixtures", ROOT / "scripts" / "make_replay_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trace(name: str) -> ReplayTrace:
    return trace_from_json((FIXTURES / f"{name}.trace.json").read_bytes())


def _samples(name: str) -> dict[str, Any]:
    samples: dict[str, Any] = json.loads((FIXTURES / f"{name}.samples.json").read_bytes())
    return samples


def test_the_committed_fixtures_are_what_the_script_writes_now() -> None:
    expected = _script().fixture_files()

    # Every file under the folder, by its path from it: the version 1 files at the top, and the
    # version 2 files in v2/.
    committed = {
        path.relative_to(FIXTURES).as_posix(): path.read_bytes().decode("ascii")
        for path in FIXTURES.rglob("*.json")
    }

    assert sorted(committed) == sorted(expected)
    for name, text in expected.items():
        assert committed[name] == text, f"{name} is out of date: run make_replay_fixtures.py"


@pytest.mark.parametrize("name", NAMES)
def test_the_samples_follow_from_the_trace_alone(name: str) -> None:
    # Nothing here uses the run: only the parsed trace file, as a viewer would.
    trace, samples = _trace(name), _samples(name)
    graph, paths = trace.graph(), trace.paths()
    activities = ActivityTimeline(trace.events)

    assert samples["trace"] == f"{name}.trace.json"
    assert samples["columns"] == ["tick", "x_m", "y_m", "activity"]
    assert list(samples["robots"]) == [robot.id for robot in trace.robots]
    for robot in trace.robots:
        rows = samples["robots"][robot.id]
        assert [row[0] for row in rows] == sorted({row[0] for row in rows})
        for tick, x, y, activity in rows:
            assert position_at(paths[robot.id], tick, graph) == (x, y)
            assert activities.at(robot.id, math.floor(tick)).value == activity


@pytest.mark.parametrize("name", NAMES)
def test_the_samples_reach_every_hard_case(name: str) -> None:
    trace, samples = _trace(name), _samples(name)
    nodes = {(node.x, node.y) for node in trace.nodes}

    for robot in trace.robots:
        rows = samples["robots"][robot.id]
        ticks = {row[0] for row in rows}
        visits = robot.path.visits
        # Every arrival and every departure within the run is sampled ...
        assert {float(v.arrive) for v in visits} <= ticks
        assert {float(v.depart) for v in visits if v.depart <= trace.end_tick} <= ticks
        # ... and so are tick 0, the end of the run and a moment after it.
        assert {0.0, float(trace.end_tick), trace.end_tick + 7.5} <= ticks
        between = [row for row in rows if (row[1], row[2]) not in nodes]
        assert len(between) >= len(visits) // 2  # really between two nodes
        assert any(row[0] != int(row[0]) for row in between)  # at a fraction of a tick
        assert {row[3] for row in rows} == {"free", "to pickup", "loaded"}


def test_each_fixture_is_the_kind_of_run_it_is_named_after() -> None:
    free_flow, cut_short, disturbed = (_trace(name) for name in NAMES)

    assert not free_flow.traffic and free_flow.conflicts_total > 0
    assert len(free_flow.outcome.delivered) == len(free_flow.orders)

    assert cut_short.run["max_tick"] == 1500
    assert cut_short.outcome.in_progress and cut_short.outcome.unreleased
    # A free-flow drive is recorded when it starts, so a run that is cut short knows where its
    # robots were going: visits after the end of the run.
    assert any(robot.path.end.arrive > cut_short.end_tick for robot in cut_short.robots)

    assert disturbed.traffic and disturbed.battery and disturbed.conflicts_total == 0
    assert disturbed.counters.breakdowns > 0 and disturbed.counters.replans > 0
    assert disturbed.charging is not None
    # Long waits: some visit lasts longer than a minute.
    longest = max(v.depart - v.arrive for robot in disturbed.robots for v in robot.path.visits)
    assert longest * disturbed.tick_s > 60
