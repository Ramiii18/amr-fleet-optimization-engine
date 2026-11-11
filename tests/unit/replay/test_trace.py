"""Tests for replay traces (D33): a trace says exactly what the run did, and nothing else."""

import copy
import dataclasses
import gzip
import json
import math
import random
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import jsonschema
import pytest

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import BatterySpec, ChargingPolicy
from amr_optimizer.metrics import compute_charging_kpis, compute_kpis
from amr_optimizer.replay import (
    FORMAT_NAME,
    SCHEMA_VERSION,
    TRACE_JSON_SCHEMA,
    TraceValidationError,
    build_trace,
    scalar_values,
    trace_from_dict,
    trace_from_json,
    trace_to_dict,
    trace_to_gzip,
    trace_to_json,
)
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import SimulationResult, simulate
from amr_optimizer.traffic import FOREVER, DelayModel, Recovery, TimedPath, Visit
from amr_optimizer.viz.motion import position_at
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

GRAPH = generate_grid_warehouse(PRESETS["small"])


def _free_flow(max_tick: int | None = None) -> SimulationResult:
    spec = ScenarioSpec(robots=4, orders_per_hour=240, duration_s=600)
    scenario = generate_scenario(GRAPH, spec, seed=2)
    return simulate(scenario, POLICIES["greedy"](), max_tick=max_tick)


def _disturbed() -> SimulationResult:
    """Everything at once: traffic, delays, batteries, breakdowns and replanning."""
    spec = ScenarioSpec(robots=3, orders_per_hour=120, duration_s=900)
    return simulate(
        generate_scenario(GRAPH, spec, seed=4),
        POLICIES["insertion-fair"](),
        traffic=True,
        delays=DelayModel(0.1, 10, 30),
        delay_seed=4,
        battery=BatterySpec(),
        battery_seed=4,
        charging=ChargingPolicy.OPPORTUNISTIC,
        breakdowns=DelayModel(0.02, 100, 600),
        breakdown_seed=4,
        recovery=Recovery.REPLAN,
    )


RUNS: dict[str, Callable[[], SimulationResult]] = {
    "free flow": _free_flow,
    "cut short": lambda: _free_flow(max_tick=2500),
    "disturbed": _disturbed,
}


@pytest.fixture(scope="module", params=list(RUNS))
def result(request: pytest.FixtureRequest) -> SimulationResult:
    return RUNS[request.param]()


@pytest.fixture(scope="module")
def free_flow() -> SimulationResult:
    return _free_flow()


def test_a_trace_holds_the_run_unchanged(result: SimulationResult) -> None:
    trace = build_trace(result, run={"seed": 2})
    scenario = result.scenario
    kpis = compute_kpis(result)

    assert trace.paths() == dict(result.trajectories)
    assert list(trace.paths()) == [robot.robot_id for robot in scenario.robots]
    assert trace.events == result.log
    assert trace.orders == scenario.orders
    assert (trace.nodes, trace.edges) == (scenario.graph.nodes, scenario.graph.edges)
    assert trace.kpis == scalar_values(asdict(kpis) | {"empty_share": kpis.empty_share})
    assert trace.outcome.delivered == result.delivered
    assert trace.outcome.in_progress == result.in_progress
    assert trace.outcome.pending == result.pending
    assert trace.outcome.unreleased == result.unreleased
    assert (trace.policy, trace.traffic, trace.battery) == (
        result.policy_name,
        result.traffic,
        result.battery,
    )
    assert (trace.tick_s, trace.end_tick) == (scenario.time_model.tick_s, result.end_tick)
    assert trace.counters.breakdowns == result.breakdowns
    assert trace.counters.replans == result.replans
    assert trace.run == {"seed": 2}


def test_the_runs_cover_what_they_are_meant_to() -> None:
    cut, disturbed = RUNS["cut short"](), _disturbed()

    assert cut.in_progress and cut.unreleased  # a run stopped in the middle of its work
    assert disturbed.breakdowns > 0 and disturbed.battery
    assert build_trace(disturbed).charging == scalar_values(
        asdict(compute_charging_kpis(disturbed))
    )
    assert build_trace(cut).charging is None


def test_a_trace_survives_json_exactly(result: SimulationResult) -> None:
    trace = build_trace(result, run={"map": "small", "delays": {"share": 0.1}})

    text = trace_to_json(trace)
    again = trace_from_json(text)

    assert again == trace
    assert trace_to_json(again) == text
    assert trace_from_json(text.encode()) == trace
    assert text.isascii() and "\n" not in text


def test_positions_from_a_parsed_trace_equal_positions_from_the_run(
    result: SimulationResult,
) -> None:
    trace = trace_from_json(trace_to_json(build_trace(result)))
    graph, paths = trace.graph(), trace.paths()
    rng = random.Random(7)
    ticks = [0, 0.5, result.end_tick, result.end_tick + 50.25]
    ticks += [rng.uniform(0, result.end_tick) for _ in range(300)]

    for robot_id, original in result.trajectories.items():
        ticks_here = ticks + [t for v in original.visits for t in (v.arrive, v.depart + 0.5)]
        for tick in ticks_here:
            assert position_at(paths[robot_id], tick, graph) == position_at(
                original, tick, result.scenario.graph
            )


def test_building_a_trace_does_not_change_the_run() -> None:
    before = _disturbed()
    log, trajectories, robots = before.log, dict(before.trajectories), before.robots

    first = trace_to_json(build_trace(before))

    assert (before.log, dict(before.trajectories), before.robots) == (log, trajectories, robots)
    # The same run, made again without ever building a trace in between, gives the same text.
    assert trace_to_json(build_trace(_disturbed())) == first


def test_real_traces_fit_the_json_schema(result: SimulationResult) -> None:
    jsonschema.Draft202012Validator.check_schema(TRACE_JSON_SCHEMA)

    data = json.loads(trace_to_json(build_trace(result, run={"seed": 2})))

    jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA).validate(data)
    assert list(data)[:3] == ["format", "schema_version", "source"]
    assert (data["format"], data["schema_version"]) == (FORMAT_NAME, SCHEMA_VERSION)
    assert "Synthetic" in data["source"]


def test_a_hold_is_null_in_json_and_never_a_huge_tick(free_flow: SimulationResult) -> None:
    robot_id, path = next(iter(free_flow.trajectories.items()))
    last = path.visits[-1]
    held = TimedPath((*path.visits[:-1], Visit(last.node, last.arrive, FOREVER)))
    result = dataclasses.replace(
        free_flow, trajectories=dict(free_flow.trajectories) | {robot_id: held}
    )

    trace = build_trace(result)
    text = trace_to_json(trace)
    data = json.loads(text)

    assert data["robots"][0]["visits"][-1] == [last.node, last.arrive, None]
    assert str(FOREVER) not in text
    assert all(c["end"] <= max(trace.end_tick, c["start"]) for c in data["conflicts"]["listed"])
    assert trace_from_json(text).paths()[robot_id].holds
    jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA).validate(data)


def test_finished_runs_have_no_hold(result: SimulationResult) -> None:
    assert not any(path.holds for path in result.trajectories.values())


def test_conflicts_come_from_the_independent_checker(free_flow: SimulationResult) -> None:
    trace = build_trace(free_flow)
    listed = trace.conflicts

    assert trace.conflicts_total == trace.kpis["conflicts"] == len(listed) > 0
    assert [c.start for c in listed] == sorted(c.start for c in listed)
    paths = trace.paths()
    for conflict in listed:
        # Both robots really are on the resource during the reported ticks.
        for robot in conflict.robots:
            nodes_then = {
                v.node
                for v in paths[robot].visits
                if v.arrive <= conflict.end + 1 and v.depart >= conflict.start - 1
            }
            if conflict.kind == "vertex":
                assert set(conflict.resource) <= nodes_then
            else:
                steps = set(zip(paths[robot].nodes, paths[robot].nodes[1:], strict=False))
                a, b = conflict.resource
                assert (a, b) in steps or (b, a) in steps


def test_the_conflict_list_is_capped_but_the_total_is_not(free_flow: SimulationResult) -> None:
    whole = build_trace(free_flow)

    capped = build_trace(free_flow, max_conflicts=3)

    assert capped.conflicts == whole.conflicts[:3]
    assert capped.conflicts_total == whole.conflicts_total > 3
    assert build_trace(free_flow, max_conflicts=0).conflicts == ()
    with pytest.raises(ValueError, match="non-negative"):
        build_trace(free_flow, max_conflicts=-1)


def test_a_run_with_traffic_has_no_conflicts() -> None:
    trace = build_trace(_disturbed())

    assert trace.traffic and trace.conflicts_total == 0 and trace.conflicts == ()


def test_gzip_is_the_same_json_and_has_no_timestamp(free_flow: SimulationResult) -> None:
    trace = build_trace(free_flow)

    packed = trace_to_gzip(trace)

    assert gzip.decompress(packed).decode("ascii") == trace_to_json(trace)
    assert packed == trace_to_gzip(build_trace(free_flow))
    assert packed[4:8] == b"\x00\x00\x00\x00"  # the header's modification time
    assert len(packed) < len(trace_to_json(trace)) / 2


def test_undefined_kpis_are_null_and_other_values_are_refused() -> None:
    assert scalar_values({"a": math.nan, "b": math.inf, "c": 1.5, "d": 2, "e": "x", "f": None}) == {
        "a": None,
        "b": None,
        "c": 1.5,
        "d": 2,
        "e": "x",
        "f": None,
    }
    with pytest.raises(TraceValidationError, match="g"):
        scalar_values({"g": [1]})


def test_run_settings_must_be_json_data(free_flow: SimulationResult) -> None:
    with pytest.raises(TraceValidationError, match="run cannot be written as JSON"):
        build_trace(free_flow, run={"when": object()})


# --- the strict parser ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def data(free_flow: SimulationResult) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(trace_to_json(build_trace(free_flow, run={"seed": 2})))
    return parsed


def _far_node(data: dict[str, Any], node: str) -> str:
    """A node with no edge from ``node``."""
    near = {e["target"] for e in data["map"]["edges"] if e["source"] == node} | {node}
    return str(next(n["id"] for n in data["map"]["nodes"] if n["id"] not in near))


def _set(path: list[Any], value: Any) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        target: Any = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


def _delete(path: list[Any]) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        target: Any = data
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return change


def _step_without_an_edge(data: dict[str, Any]) -> None:
    visits = data["robots"][0]["visits"]
    visits[1][0] = _far_node(data, visits[0][0])


def _swap_two_events(data: dict[str, Any]) -> None:
    events = data["events"]
    late = next(i for i, e in enumerate(events) if e["tick"] > events[0]["tick"])
    events[0], events[late] = events[late], events[0]


def _drop_an_order_from_the_outcome(data: dict[str, Any]) -> None:
    data["outcome"]["delivered"].pop()


def _list_an_order_twice(data: dict[str, Any]) -> None:
    data["outcome"]["pending"].append(data["outcome"]["delivered"][0])


# (name, change, the reason the parser gives, whether the JSON Schema must reject it too)
BROKEN: list[tuple[str, Callable[[dict[str, Any]], None], str, bool]] = [
    ("an unknown field", _set(["colour"], "red"), r"trace: unknown field\(s\) \['colour'\]", True),
    ("a missing field", _delete(["events"]), r"trace: missing field\(s\) \['events'\]", True),
    ("another format", _set(["format"], "amr-scenario"), "trace.format", True),
    ("a later version", _set(["schema_version"], 2), "unsupported version 2", True),
    ("a version that is true", _set(["schema_version"], True), "unsupported version", True),
    (
        "a tick with a fraction",
        _set(["end_tick"], 10.5),
        "trace.end_tick: expected an integer",
        True,
    ),
    (
        "a flag that is a number",
        _set(["traffic"], 1),
        "trace.traffic: expected true or false",
        True,
    ),
    ("a tick length of zero", _set(["tick_s"], 0), "tick_s must be a positive", True),
    (
        "an unknown node kind",
        _set(["map", "nodes", 0, "kind"], "lift"),
        r"map.nodes\[0\].kind",
        True,
    ),
    (
        "an unknown field in a node",
        _set(["map", "nodes", 0, "z"], 1.0),
        r"map.nodes\[0\]: unknown field",
        True,
    ),
    ("an edge of length zero", _set(["map", "edges", 0, "length_m"], 0), "length must be", True),
    ("no robots", _set(["robots"], []), "at least one robot", True),
    (
        "a visit without a departure field",
        _set(["robots", 0, "visits", 0], ["PK00", 0]),
        r"robots\[0\].visits\[0\]: expected \[node, arrive, depart\]",
        True,
    ),
    (
        "a hold in the middle of a path",
        _set(["robots", 0, "visits", 0, 2], None),
        r"robots\[0\].visits: only the last visit",
        False,
    ),
    (
        "a robot that starts late",
        _set(["robots", 0, "visits", 0, 1], 1),
        "the first visit must start at tick 0",
        False,
    ),
    (
        "a visit off the map",
        _set(["robots", 0, "visits", 0, 0], "NOWHERE"),
        "unknown node 'NOWHERE'",
        False,
    ),
    ("a step without an edge", _step_without_an_edge, "no edge", False),
    ("two robots with one id", _set(["robots", 1, "id"], "R01"), "duplicate robot id 'R01'", False),
    ("an unknown event kind", _set(["events", 0, "kind"], "lost"), r"events\[0\].kind", True),
    ("events out of time order", _swap_two_events, "out of time order", False),
    (
        "an event after the end",
        _set(["events", -1, "tick"], 10**9),
        r"events\[\d+\]: tick 1000000000 is out of time order or outside",
        False,
    ),
    ("an event of an unknown order", _set(["events", 0, "order"], "O?"), "unknown order", False),
    ("a release by a robot", _set(["events", 0, "robot"], "R01"), "except a release", False),
    (
        "an order with one place",
        _set(["orders", 0, "dropoff"], None),
        r"orders\[0\].dropoff: expected a string",
        True,
    ),
    ("an order missing from the outcome", _drop_an_order_from_the_outcome, "exactly once", False),
    ("an order listed twice", _list_an_order_twice, "listed twice", False),
    ("a negative counter", _set(["counters", "replans"], -1), "counters.replans", True),
    ("a KPI that is a list", _set(["kpis", "delivered"], [1]), "kpis.delivered", True),
    (
        "fewer conflicts counted than listed",
        _set(["conflicts", "total"], 0),
        "more are listed",
        False,
    ),
    (
        "a conflict of one robot with itself",
        _set(["conflicts", "listed", 0, "robots"], ["R01", "R01"]),
        "two different, known robots",
        False,
    ),
    (
        "a conflict of an unknown kind",
        _set(["conflicts", "listed", 0, "kind"], "near miss"),
        r"conflicts\[0\]: kind",
        True,
    ),
]


@pytest.mark.parametrize(
    ("change", "reason", "schema_too"), [b[1:] for b in BROKEN], ids=[b[0] for b in BROKEN]
)
def test_a_broken_trace_is_refused_with_the_reason(
    data: dict[str, Any], change: Callable[[dict[str, Any]], None], reason: str, schema_too: bool
) -> None:
    broken = copy.deepcopy(data)
    change(broken)

    with pytest.raises(TraceValidationError, match=reason):
        trace_from_dict(broken)

    fits_schema = jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA).is_valid(broken)
    assert fits_schema != schema_too


def test_the_unchanged_trace_is_accepted(data: dict[str, Any]) -> None:
    assert trace_to_dict(trace_from_dict(copy.deepcopy(data))) == data


@pytest.mark.parametrize("text", ["", "{", "[]", "null", '"trace"'])
def test_text_that_is_not_a_trace_is_refused(text: str) -> None:
    with pytest.raises(TraceValidationError, match="not valid JSON|expected an object"):
        trace_from_json(text)
