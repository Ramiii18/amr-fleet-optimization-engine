"""Tests for version 2 of the trace and for the reference functions of `replay.state` (D35).

- Version 2 only adds to version 1: removing the additions gives the version 1 trace, and the
  committed version 1 fixtures are exactly that, byte for byte.
- Position, status and battery level at any tick are checked by hand on a charging cycle and a
  breakdown, and on the committed version 2 fixtures from the trace file alone.
"""

import copy
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from amr_optimizer.dispatch import GreedyDispatch
from amr_optimizer.domain import (
    BatterySpec,
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
)
from amr_optimizer.replay import (
    TRACE_JSON_SCHEMA,
    TRACE_JSON_SCHEMA_V1,
    ReplayTrace,
    TraceValidationError,
    build_trace,
    downgrade_to_v1,
    trace_from_dict,
    trace_from_json,
    trace_to_dict,
    trace_to_json,
)
from amr_optimizer.replay.state import (
    Level,
    LevelSource,
    Status,
    corridor_arrivals,
    events_by_robot,
    level_at,
    position_at,
    status_at,
)
from amr_optimizer.scenario import Scenario
from amr_optimizer.sim import RobotEventKind as K
from amr_optimizer.sim import SimulationResult, simulate
from amr_optimizer.traffic import DelayModel
from amr_optimizer.viz.motion import ActivityTimeline
from amr_optimizer.viz.motion import position_at as plain_position_at
from amr_optimizer.warehouse import Edge, Node, NodeId, NodeKind, WarehouseGraph

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "data" / "replay"
V1 = ("free_flow", "cut_short", "disturbed")
V2 = (*V1, "charging")
R1, R2 = RobotId("R1"), RobotId("R2")
READING, FORMULA = LevelSource.READING, LevelSource.CHARGING_FORMULA


def _v2(name: str) -> ReplayTrace:
    return trace_from_json((FIXTURES / "v2" / f"{name}.trace.json").read_bytes())


def _v2_data(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "v2" / f"{name}.trace.json").read_bytes())
    return data


# --- version 2 only adds to version 1 ------------------------------------------------------------


@pytest.mark.parametrize("name", V1)
def test_without_its_additions_a_v2_trace_is_the_committed_v1_trace_byte_for_byte(
    name: str,
) -> None:
    v1_file = (FIXTURES / f"{name}.trace.json").read_bytes()

    stripped = downgrade_to_v1(_v2_data(name))

    text = json.dumps(stripped, separators=(",", ":"), allow_nan=False) + "\n"
    assert text.encode("ascii") == v1_file
    jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA_V1).validate(stripped)


@pytest.mark.parametrize("name", V1)
def test_a_v1_trace_is_still_read_and_has_no_additions(name: str) -> None:
    older = trace_from_json((FIXTURES / f"{name}.trace.json").read_bytes())
    newer = _v2(name)

    assert older.robot_events == () and older.battery_model is None
    assert all(robot.levels is None for robot in older.robots)
    # Everything a version 1 trace has is the same in the version 2 trace of the same run.
    assert older.paths() == newer.paths()
    for field in ("events", "orders", "nodes", "edges", "kpis", "charging", "counters", "outcome"):
        assert getattr(older, field) == getattr(newer, field), field
    assert (older.conflicts_total, older.conflicts) == (newer.conflicts_total, newer.conflicts)
    assert (older.run, older.end_tick, older.battery) == (newer.run, newer.end_tick, newer.battery)


@pytest.mark.parametrize("name", V2)
def test_v2_fixtures_fit_the_schema_and_survive_json_exactly(name: str) -> None:
    data = _v2_data(name)

    jsonschema.Draft202012Validator.check_schema(TRACE_JSON_SCHEMA)
    jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA).validate(data)
    trace = trace_from_dict(copy.deepcopy(data))
    assert trace_to_dict(trace) == data
    assert data["schema_version"] == 2
    assert list(data)[-2:] == ["battery_model", "robot_events"]  # additions come last


def test_downgrading_needs_version_2_data() -> None:
    with pytest.raises(TraceValidationError, match="only version 2"):
        downgrade_to_v1(json.loads((FIXTURES / "free_flow.trace.json").read_bytes()))


def _set(path: list[Any], value: Any) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        target: Any = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


def _first(data: dict[str, Any], kind: str) -> int:
    return next(i for i, e in enumerate(data["robot_events"]) if e["kind"] == kind)


def _event(kind: str, field: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        data["robot_events"][_first(data, kind)][field] = value

    return change


def _drop_levels_of_one_robot(data: dict[str, Any]) -> None:
    data["robots"][0]["levels"] = None


def _swap_two_robot_events(data: dict[str, Any]) -> None:
    events = data["robot_events"]
    late = next(i for i, e in enumerate(events) if e["tick"] > events[0]["tick"])
    events[0], events[late] = events[late], events[0]


# (name, change, the parser's reason, whether the JSON Schema must reject it too)
BROKEN: list[tuple[str, Callable[[dict[str, Any]], None], str, bool]] = [
    ("a later version", _set(["schema_version"], 3), "unsupported version 3", True),
    (
        "a v2 trace labelled as v1",
        _set(["schema_version"], 1),
        r"unknown field\(s\) \['battery_model', 'robot_events'\]",
        False,  # the version 2 schema refuses the label; version 1 would refuse the fields
    ),
    ("no robot events field", lambda d: d.pop("robot_events"), "missing field", True),
    ("a robot without its levels field", lambda d: d["robots"][0].pop("levels"), "missing", True),
    ("levels for one robot only", _drop_levels_of_one_robot, "exactly with a battery model", False),
    ("one level too few", lambda d: d["robots"][0]["levels"].pop(), "one level per visit", False),
    ("a negative level", _set(["robots", 0, "levels", 0], -1), "outside 0 to", True),
    ("a level above the capacity", _set(["robots", 0, "levels", 0], 10**9), "outside 0 to", False),
    ("a battery model of capacity zero", _set(["battery_model", "capacity"], 0), "positive", True),
    (
        "a battery model with an unknown field",
        _set(["battery_model", "voltage"], 48),
        "battery_model: unknown field",
        True,
    ),
    (
        "a return level that is neither threshold",
        _set(["battery_model", "back_in_service"], 123),
        "must be the low or the high threshold",
        False,
    ),
    ("no battery model but levels", _set(["battery_model"], None), "levels are given", False),
    ("an unknown kind of robot event", _event("delay", "kind", "teleport"), "kind", True),
    ("a delay without its ticks", _event("delay", "ticks", None), "a delay event has", False),
    ("a delay of zero ticks", _event("delay", "ticks", 0), "ticks", True),
    ("a delay with a level", _event("delay", "level", 5), "a delay event has", False),
    (
        "charging without a level",
        _event("charging_started", "level", None),
        "a charging_started event has the fields",
        False,
    ),
    (
        "a trip for an unknown reason",
        _event("charger_trip_started", "reason", "bored"),
        "reason must be one of",
        True,
    ),
    ("an event of an unknown robot", _event("delay", "robot", "R99"), "unknown robot", False),
    ("an event at an unknown node", _event("delay", "target", "NOWHERE"), "unknown node", False),
    ("robot events out of time order", _swap_two_robot_events, "out of time order", False),
    (
        "a robot event after the end",
        lambda d: d["robot_events"][-1].update(tick=10**9),
        "outside the run",
        False,
    ),
    (
        "an event with an unknown field",
        lambda d: d["robot_events"][0].update(colour="red"),
        r"robot_events\[0\]: unknown field",
        True,
    ),
]


@pytest.mark.parametrize(
    ("change", "reason", "schema_too"), [b[1:] for b in BROKEN], ids=[b[0] for b in BROKEN]
)
def test_a_broken_v2_trace_is_refused_with_the_reason(
    change: Callable[[dict[str, Any]], None], reason: str, schema_too: bool
) -> None:
    broken = _v2_data("charging")
    change(broken)

    with pytest.raises(TraceValidationError, match=reason):
        trace_from_dict(broken)

    fits_schema = jsonschema.Draft202012Validator(TRACE_JSON_SCHEMA).is_valid(broken)
    if schema_too:
        assert not fits_schema
    elif broken["schema_version"] == 2:
        assert fits_schema  # structure is fine: only the parser's consistency checks object


# --- by hand: a charging cycle -------------------------------------------------------------------
#
# The hand case of test_battery.py and test_robot_events.py (64,000 units; 100 units per tick;
# low threshold 16,000, high threshold 57,600; a metre costs 1,000 units):
#
#              A       B
#              |       |
#      P1 --   W ===== E   -- CH
#              |       |
#              S1      S2
#
# R1: P1 (0) -> W (10) -> A (20-25) -> W (35) -> S1 (45-50) -> W (60) -> P1 (70) -> W (80)
#     -> E (100) -> CH (110-594) -> E (604) -> B (614) ...


def _scenario(
    graph: WarehouseGraph, robots: dict[RobotId, NodeId], orders: list[TransportOrder]
) -> Scenario:
    return Scenario(
        graph=graph,
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=5,
        unload_ticks=5,
        robots=tuple(RobotState.idle(rid, node) for rid, node in robots.items()),
        orders=tuple(orders),
    )


def _charging_cycle() -> SimulationResult:
    a, b, w, e, s1, s2, p1, ch = (NodeId(n) for n in ("A", "B", "W", "E", "S1", "S2", "P1", "CH"))
    nodes = [
        Node(w, 0, 0),
        Node(e, 2, 0),
        Node(a, 0, 1, NodeKind.STORAGE),
        Node(b, 2, 1, NodeKind.STORAGE),
        Node(s1, 0, -1, NodeKind.STATION),
        Node(s2, 2, -1, NodeKind.STATION),
        Node(p1, -1, 0, NodeKind.PARKING),
        Node(ch, 3, 0, NodeKind.CHARGER),
    ]
    links = [(w, e, 2.0), (w, a, 1.0), (w, s1, 1.0), (w, p1, 1.0)]
    links += [(e, b, 1.0), (e, s2, 1.0), (e, ch, 1.0)]
    graph = WarehouseGraph(
        nodes, [Edge(u, v, length) for x, y, length in links for u, v in ((x, y), (y, x))]
    )
    orders = [TransportOrder(OrderId("O1"), a, s1, 0), TransportOrder(OrderId("O2"), b, s2, 300)]
    battery = BatterySpec(
        capacity_m=64.0, full_charge_s=64.0, start_min_share=0.30, start_max_share=0.30
    )
    return simulate(
        _scenario(graph, {R1: p1}, orders), GreedyDispatch(), traffic=True, battery=battery
    )


@pytest.fixture(scope="module")
def cycle() -> SimulationResult:
    return _charging_cycle()


@pytest.mark.parametrize(
    ("tick", "expected"),
    [
        (0, Level(19_200, 0, READING)),  # the start level
        (5.5, Level(19_200, 0, READING)),  # on the first edge: nothing recorded since the start
        (10, Level(18_200, 10, READING)),  # the edge is paid on arrival
        (22, Level(17_200, 20, READING)),  # loading at A costs nothing and records nothing
        (65, Level(14_200, 60, READING)),
        (70, Level(13_200, 70, READING)),  # found low here; the event has the same level
        (99.9, Level(12_200, 80, READING)),  # on the 2 m edge W -> E: still the level at W
        (100, Level(10_200, 100, READING)),
        (110, Level(9_200, 110, READING)),  # arrival at the charger: a reading
        (110.9, Level(9_200, 110, READING)),  # the same whole tick: nothing charged yet
        (111, Level(9_300, 111, FORMULA)),  # one tick of charging, by the model's formula
        (300.5, Level(28_200, 300, FORMULA)),  # 9,200 + 190 * 100
        (593, Level(57_500, 593, FORMULA)),
        (594, Level(57_600, 594, READING)),  # back in service: the simulator computed it
        (598, Level(57_600, 594, READING)),  # driving away: what it charged to, as of leaving
        (604, Level(56_600, 604, READING)),
    ],
)
def test_the_level_at_a_tick_and_where_it_comes_from(
    cycle: SimulationResult, tick: float, expected: Level
) -> None:
    record = cycle.battery_record
    assert record is not None
    events = [e for e in cycle.robot_events if e.robot_id == R1]

    level = level_at(cycle.trajectories[R1], cycle.energy_levels[R1], events, record, tick)

    assert level == expected


def test_the_charging_formula_stops_at_the_capacity() -> None:
    # A robot that stays on the charger: its level rises to the capacity and no further.
    result = _charging_cycle()
    record = result.battery_record
    assert record is not None
    path, levels = result.trajectories[R1], result.energy_levels[R1]
    events = [e for e in result.robot_events if e.robot_id == R1]
    never_leaves = [e for e in events if e.kind is not K.CHARGING_STOPPED and e.tick <= 594]

    far = level_at(path.__class__(path.visits[:10]), levels[:10], never_leaves, record, 5_000)

    assert far == Level(record.capacity, 5_000, FORMULA)


@pytest.mark.parametrize(
    ("tick", "expected"),
    [
        (69.5, ()),
        (70, (Status.OUT_OF_SERVICE,)),  # an interval includes its first tick ...
        (109.5, (Status.OUT_OF_SERVICE,)),  # driving to the charger: not charging yet
        (110, (Status.CHARGING, Status.OUT_OF_SERVICE)),
        (593.5, (Status.CHARGING, Status.OUT_OF_SERVICE)),
        (594, ()),  # ... and not its last: back in service and off the charger at 594
    ],
)
def test_the_status_at_a_tick(
    cycle: SimulationResult, tick: float, expected: tuple[Status, ...]
) -> None:
    events = [e for e in cycle.robot_events if e.robot_id == R1]

    assert status_at(cycle.trajectories[R1], events, tick) == expected


# --- by hand: a breakdown, and a wait at the end of a corridor -----------------------------------
#
#     P1, P3 - A ===== B - X1, X3
#              |       |
#              C ===== D - S, X2


class _Scripted(DelayModel):
    def __init__(self, *script: int) -> None:
        super().__init__(1.0)
        object.__setattr__(self, "_script", list(script))

    def draw(self, rng: object) -> int:
        script: list[int] = self._script  # type: ignore[attr-defined]
        return script.pop(0) if script else 0


def _ring_run() -> SimulationResult:
    a, b, c, d = (NodeId(n) for n in "ABCD")
    p1, p3, x1, x2, x3, s = (NodeId(n) for n in ("P1", "P3", "X1", "X2", "X3", "S"))
    nodes = [
        Node(a, 0, 1),
        Node(b, 2, 1),
        Node(c, 0, 0),
        Node(d, 2, 0),
        Node(p1, -1, 1, NodeKind.PARKING),
        Node(p3, 0, 2, NodeKind.PARKING),
        Node(x1, 3, 1, NodeKind.STORAGE),
        Node(x3, 2, 2, NodeKind.STORAGE),
        Node(x2, 2, -1, NodeKind.STORAGE),
        Node(s, 3, 0, NodeKind.STATION),
    ]
    links = [(a, b, 2.0), (c, d, 2.0), (a, c, 1.0), (b, d, 1.0)]
    links += [(a, p1, 1.0), (a, p3, 1.0), (b, x1, 1.0), (b, x3, 1.0), (d, s, 1.0), (d, x2, 1.0)]
    graph = WarehouseGraph(
        nodes, [Edge(u, v, length) for x, y, length in links for u, v in ((x, y), (y, x))]
    )
    orders = [TransportOrder(OrderId("O1"), x1, s, 0), TransportOrder(OrderId("O2"), x3, s, 15)]
    return simulate(
        _scenario(graph, {R1: p1, R2: p3}, orders),
        GreedyDispatch(),
        traffic=True,
        breakdowns=_Scripted(0, 300),
    )


def test_a_breakdown_is_a_status_of_the_whole_traversal_and_not_a_place() -> None:
    # R1 leaves A for B at tick 10 with a repair of 300 ticks, and enters B at 330.
    result = _ring_run()
    path = result.trajectories[R1]
    events = [e for e in result.robot_events if e.robot_id == R1]

    assert status_at(path, events, 9.5) == ()
    for tick in (10, 150, 329.9):
        assert Status.BREAKDOWN in status_at(path, events, tick)
    assert Status.BREAKDOWN not in status_at(path, events, 330)
    # The position on that edge is the convention of even movement: at half the time, half way.
    # The status above is what says that this is not a measured place.
    graph = result.scenario.graph
    assert position_at(path, events, 170, graph) == (1.0, 1.0)
    assert position_at(path, events, 170, graph) == plain_position_at(path, 170, graph)


@pytest.mark.parametrize("name", V2)
def test_a_robot_that_waits_stands_at_the_end_of_its_corridor(name: str) -> None:
    trace = _v2(name)
    graph, events = trace.graph(), events_by_robot(trace.robot_events)
    waits = 0
    for robot in trace.robots:
        own = events.get(robot.id, [])
        reached = corridor_arrivals(robot.path, own)
        for index, tick in reached.items():
            before, after = robot.path.visits[index], robot.path.visits[index + 1]
            assert before.depart < tick < after.arrive
            end = graph.node(after.node)
            # From the moment it reaches the end of the corridor until it enters the node, the
            # robot is at the end, and its status says that it is waiting there.
            for moment in (tick, (tick + after.arrive) / 2, after.arrive - 0.25):
                assert position_at(robot.path, own, moment, graph) == (end.x, end.y)
                assert Status.CORRIDOR_WAIT in status_at(robot.path, own, moment)
            assert Status.CORRIDOR_WAIT not in status_at(robot.path, own, after.arrive)
            # Just before, it is still on its way; the plain interpolation would have it
            # further back, spreading the wait over the whole edge.
            almost = position_at(robot.path, own, tick - 0.5, graph)
            assert almost != (end.x, end.y)
            assert almost != plain_position_at(robot.path, tick - 0.5, graph)
            waits += 1
        if not reached:  # no wait: the two position functions are the same function
            for tick in range(0, trace.end_tick + 20, 37):
                moment = tick + 0.3
                assert position_at(robot.path, own, moment, graph) == plain_position_at(
                    robot.path, moment, graph
                )
    assert waits == sum(e.kind is K.CORRIDOR_WAIT_STARTED for e in trace.robot_events)
    if name in ("disturbed", "charging"):
        assert waits > 0


# --- the committed version 2 samples -------------------------------------------------------------


@pytest.mark.parametrize("name", V2)
def test_the_v2_samples_follow_from_the_trace_alone(name: str) -> None:
    # Nothing here uses the run: only the parsed trace file, as a viewer would.
    trace = _v2(name)
    samples = json.loads((FIXTURES / "v2" / f"{name}.samples.json").read_bytes())
    graph, events = trace.graph(), events_by_robot(trace.robot_events)
    activities = ActivityTimeline(trace.events)

    assert (samples["format"], samples["version"]) == ("amr-replay-samples", 2)
    assert samples["columns"] == [
        "tick",
        "x_m",
        "y_m",
        "activity",
        "status",
        "level_units",
        "level_as_of_tick",
        "level_source",
    ]
    assert list(samples["robots"]) == [robot.id for robot in trace.robots]
    seen_status: set[str] = set()
    seen_source: set[str | None] = set()
    for robot in trace.robots:
        own = events.get(robot.id, [])
        for tick, x, y, activity, status, units, as_of, source in samples["robots"][robot.id]:
            assert position_at(robot.path, own, tick, graph) == (x, y)
            assert activities.at(robot.id, math.floor(tick)).value == activity
            assert [s.value for s in status_at(robot.path, own, tick)] == status
            seen_status.update(status)
            seen_source.add(source)
            if trace.battery_model is None or robot.levels is None:
                assert (units, as_of, source) == (None, None, None)
                continue
            level = level_at(robot.path, robot.levels, own, trace.battery_model, tick)
            assert (level.units, level.as_of, level.source.value) == (units, as_of, source)
            assert as_of <= tick  # a value is never dated after the moment it is shown at

    if name == "charging":
        assert seen_status >= {"charging", "out_of_service", "delay", "corridor_wait"}
        assert seen_source == {"reading", "charging_formula"}
    if name == "disturbed":
        assert seen_status >= {"charging", "breakdown", "delay", "corridor_wait"}
    if name in ("free_flow", "cut_short"):
        assert seen_status == set() and seen_source == {None}


def test_a_trace_built_from_a_run_carries_its_events_and_levels(cycle: SimulationResult) -> None:
    trace = build_trace(cycle, run={"case": "charging cycle"})

    assert trace.robot_events == cycle.robot_events
    assert trace.robots[0].levels == cycle.energy_levels[R1]
    assert trace.battery_model == cycle.battery_record
    assert trace_from_json(trace_to_json(trace)) == trace
