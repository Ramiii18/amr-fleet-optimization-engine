"""A replay trace: everything a viewer needs to play back one finished run (D33).

A trace is a read model of a `SimulationResult`. It holds the map, every robot's recorded
trajectory, the orders, the event log, the KPIs and the conflicts found by the independent
checker. A viewer computes none of these: it only interpolates positions between two recorded
visits (`viz.motion.position_at` is the reference) and reads activities from the events.

The simulator does not know this module, so building a trace cannot change a run.

JSON format (``schema_version`` 1), in this key order::

    {
      "format": "amr-replay-trace",
      "schema_version": 1,
      "source": "Synthetic simulation output ...",
      "run": {...},                       the settings that produced the run, as given by the caller
      "policy": "greedy", "traffic": true, "battery": false,
      "tick_s": 0.1, "end_tick": 6538, "speed_mps": 1.0, "load_ticks": 100, "unload_ticks": 100,
      "map": {"nodes": [{"id", "x", "y", "kind"}], "edges": [{"source", "target", "length_m"}]},
      "robots": [{"id": "R1", "visits": [["PK00", 0, 12], ["X00-00", 32, 32], ...]}],
      "orders": [{"id", "pickup", "dropoff", "release_tick"}],
      "events": [{"tick", "kind", "order", "node", "robot", "distance_m"}],
      "outcome": {"delivered": [...], "in_progress": [...], "pending": [...], "unreleased": [...]},
      "counters": {"breakdowns", "breakdown_ticks", "replans", "moves_out_of_the_way",
                   "deferred_assignments"},
      "kpis": {...}, "charging": {...} or null,
      "conflicts": {"total": 0, "listed": [{"kind", "resource", "robots", "start", "end"}]}
    }

Rules:

- Every time is an integer tick (D6); ``tick_s`` converts ticks to seconds. Only KPIs are in
  seconds, and their names end in ``_s``.
- A visit is ``[node, arrive, depart]``: the robot stands at the node from ``arrive`` to
  ``depart``, both included, and drives to the next visit's node in between. A robot's first
  visit starts at tick 0, at the node where it starts.
- A hold, a last visit with no planned departure, has ``depart`` null. Inside the engine it is
  `FOREVER` (2^62), which never reaches a file.
- A run plays from tick 0 to ``end_tick``. A run that was cut short (``max_tick``) can record
  visits after ``end_tick``: a free-flow drive is recorded when it starts. A viewer stops at
  ``end_tick``.
- A KPI that is undefined (nothing was delivered) is null: JSON has no NaN.
- Nothing in a trace depends on the machine or the clock, so the same run gives the same JSON,
  byte for byte. Wall-clock measurements are deliberately left out.

Parsing is strict, like map and scenario files (D32): unknown, missing and mistyped fields are
rejected with their location. A trace is then checked as a whole: see `ReplayTrace`.

**Version 2** (D35) adds what happens to robots besides orders. Everything above is unchanged,
and three things are added::

      "robots": [{"id", "visits": [...], "levels": [19200, 18200, ...] or null}],
      "battery_model": {"capacity", "charge_per_tick", "low", "high", "back_in_service",
                        "units_per_metre"} or null,
      "robot_events": [{"tick", "kind", "robot", "node", "target", "ticks", "level", "reason"}]

- ``levels`` has one entry per visit: the robot's energy, in units, on arrival at that visit.
  These are the moments the simulator updates a level while driving. Between two of them the
  trace says nothing about the level: a reader shows the last one, and does not interpolate.
- ``battery_model`` holds the numbers of the simulator's charging formula, so that a reader can
  repeat it while a robot stands on a charger: ``min(capacity, level + ticks * charge_per_tick)``.
  A value computed that way is a calculation by the model's formula, not a recorded reading.
- ``robot_events`` are defined in `sim.log.RobotEventKind`. A field that a kind does not have is
  null. Runs without a battery model have null ``levels`` and ``battery_model``.

A version 1 trace is still read: it has no robot events and no levels. `downgrade_to_v1` removes
the additions from version 2 data, which gives exactly the version 1 trace of the same run.
"""

import bisect
import gzip
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from amr_optimizer.domain import OrderId, OrderValidationError, RobotId, TransportOrder
from amr_optimizer.metrics import RunKpis, compute_charging_kpis, compute_kpis
from amr_optimizer.sim import (
    BatteryRecord,
    EventKind,
    LogEntry,
    RobotEvent,
    RobotEventKind,
    SimulationResult,
)
from amr_optimizer.traffic import (
    FOREVER,
    Conflict,
    Corridor,
    InvalidTimedPathError,
    TimedPath,
    Visit,
    path_conflicts,
)
from amr_optimizer.warehouse import (
    Edge,
    MapValidationError,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
)

FORMAT_NAME = "amr-replay-trace"
SCHEMA_VERSION = 2
"""The version this package writes."""
READABLE_VERSIONS = (1, 2)
"""The versions this package reads."""
SOURCE = (
    "Synthetic simulation output of the AMR Fleet Optimization Engine. "
    "No real robot was controlled or observed."
)
MAX_LISTED_CONFLICTS = 1000
"""How many conflicts a trace lists by default. The total is always exact."""

Scalar = float | int | str | None
"""A KPI value. An undefined KPI is ``None``."""


class TraceValidationError(ValueError):
    """A trace that is malformed, or whose parts do not fit together."""


@dataclass(frozen=True, slots=True)
class TraceRobot:
    """One robot and its whole movement over the run. The path starts at tick 0.

    With a battery model, ``levels`` holds the robot's energy on arrival at each visit, in
    units; otherwise it is None.
    """

    id: RobotId
    path: TimedPath
    levels: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class TraceOutcome:
    """Every order of the run, by its state when the run ended."""

    delivered: tuple[OrderId, ...]  # in delivery order
    in_progress: tuple[OrderId, ...]  # assigned, not delivered
    pending: tuple[OrderId, ...]  # released, never assigned
    unreleased: tuple[OrderId, ...]  # released after the run stopped


@dataclass(frozen=True, slots=True)
class TraceCounters:
    """Totals the simulator counts over a run. They have no events in the log."""

    breakdowns: int
    breakdown_ticks: int  # robot-ticks of repairs
    replans: int
    moves_out_of_the_way: int
    deferred_assignments: int


@dataclass(frozen=True, slots=True)
class TraceConflict:
    """Two robots in the same node or corridor at a common moment, as the checker found it.

    ``resource`` is one node, or the two ends of a corridor. ``start`` and ``end`` are the ticks
    in which both occupancies overlap, cut at the end of the run.
    """

    kind: str  # "vertex", "head-on" or "following"
    resource: tuple[NodeId, ...]
    robots: tuple[RobotId, RobotId]
    start: int
    end: int


CONFLICT_KINDS = ("vertex", "head-on", "following")

CHARGER_TRIP_REASONS = ("low", "top_up")

_K = RobotEventKind
ROBOT_EVENT_FIELDS: Mapping[RobotEventKind, frozenset[str]] = {
    _K.CHARGING_STARTED: frozenset({"level"}),
    _K.CHARGING_STOPPED: frozenset({"level"}),
    _K.OUT_OF_SERVICE: frozenset({"level"}),
    _K.BACK_IN_SERVICE: frozenset({"level"}),
    _K.CHARGER_WAIT_STARTED: frozenset(),
    _K.CHARGER_WAIT_ENDED: frozenset(),
    _K.CHARGER_TRIP_STARTED: frozenset({"target", "level", "reason"}),
    _K.DELAY: frozenset({"target", "ticks"}),
    _K.BREAKDOWN: frozenset({"target", "ticks"}),
    _K.CORRIDOR_WAIT_STARTED: frozenset({"target"}),
    _K.CORRIDOR_WAIT_ENDED: frozenset({"target"}),
    _K.REPLANNED: frozenset(),
    _K.MOVED_OUT_OF_THE_WAY: frozenset({"target"}),
}
"""Which of ``target``, ``ticks``, ``level`` and ``reason`` each kind of robot event has. A
trace is refused if an event lacks one of its fields or has one that its kind does not have."""


@dataclass(frozen=True, slots=True)
class ReplayTrace:
    """One finished run, ready to be played back.

    Checked on construction (`TraceValidationError`):

    - the map is a valid warehouse graph;
    - robot and order ids are unique; every robot starts at tick 0, stands only on nodes of the
      map and drives only along its edges;
    - events are in time order, within the run, and name known orders, nodes and robots; only a
      release has no robot;
    - the outcome lists every order exactly once;
    - no more conflicts are listed than were counted, and each names known robots and nodes;
    - ``run`` and the KPIs can be written as JSON;
    - battery levels exist exactly when there is a battery model: one per visit, within the
      capacity;
    - robot events are in time order, within the run, name known robots and nodes, and have
      exactly the fields of their kind (`ROBOT_EVENT_FIELDS`);
    - every robot event agrees with the robot's path: the robot is at the event's node at that
      tick, a delay or a breakdown is at a departure towards the event's target, a corridor
      wait lies within such a traversal, and charging happens on a charger.
    """

    run: Mapping[str, object]
    policy: str
    traffic: bool
    battery: bool
    tick_s: float
    end_tick: int
    speed_mps: float
    load_ticks: int
    unload_ticks: int
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    robots: tuple[TraceRobot, ...]
    orders: tuple[TransportOrder, ...]
    events: tuple[LogEntry, ...]
    outcome: TraceOutcome
    counters: TraceCounters
    kpis: Mapping[str, Scalar]
    charging: Mapping[str, Scalar] | None
    conflicts_total: int
    conflicts: tuple[TraceConflict, ...]
    battery_model: BatteryRecord | None = None
    robot_events: tuple[RobotEvent, ...] = ()

    def __post_init__(self) -> None:
        _check(self)

    def graph(self) -> WarehouseGraph:
        """The map of the run, with nodes and edges in their original order."""
        return WarehouseGraph(self.nodes, self.edges)

    def paths(self) -> dict[RobotId, TimedPath]:
        """Every robot's trajectory, as the engine's own type."""
        return {robot.id: robot.path for robot in self.robots}


# --- building a trace from a run ---------------------------------------------------------------


def scalar_values(values: Mapping[str, object]) -> dict[str, Scalar]:
    """``values`` for JSON: a number that is not finite (an undefined KPI) becomes ``None``."""
    safe: dict[str, Scalar] = {}
    for key, value in values.items():
        if isinstance(value, float) and not math.isfinite(value):
            safe[key] = None
        elif value is None or isinstance(value, float | int | str):
            safe[key] = value
        else:
            raise TraceValidationError(f"{key}: {value!r} is not a number or a string")
    return safe


def _trace_conflict(conflict: Conflict, end_tick: int) -> TraceConflict:
    (first_robot, a), (second_robot, b) = conflict.first, conflict.second
    resource = conflict.resource
    start = max(a.start, b.start)
    return TraceConflict(
        kind=conflict.kind,
        resource=(resource.a, resource.b) if isinstance(resource, Corridor) else (resource,),
        robots=(first_robot, second_robot),
        start=start,
        end=min(a.end, b.end, max(end_tick, start)),
    )


def build_trace(
    result: SimulationResult,
    *,
    run: Mapping[str, object] | None = None,
    kpis: RunKpis | None = None,
    max_conflicts: int = MAX_LISTED_CONFLICTS,
) -> ReplayTrace:
    """The trace of ``result``.

    ``run`` describes how the run was made (map name, seed, options): it is copied into the
    trace as provenance and must be JSON data. ``kpis`` saves computing them again when the
    caller already has them. At most ``max_conflicts`` conflicts are listed, the earliest first;
    the total counts all of them.
    """
    if max_conflicts < 0:
        raise ValueError(f"max_conflicts must be non-negative, got {max_conflicts}")
    scenario = result.scenario
    if kpis is None:
        kpis = compute_kpis(result)
    charging = compute_charging_kpis(result) if result.battery else None
    found = [
        _trace_conflict(conflict, result.end_tick)
        for conflict in path_conflicts({r: [p] for r, p in result.trajectories.items()})
    ]
    found.sort(key=lambda c: (c.start, c.end, c.resource, c.robots))
    return ReplayTrace(
        run=dict(run or {}),
        policy=result.policy_name,
        traffic=result.traffic,
        battery=result.battery,
        tick_s=scenario.time_model.tick_s,
        end_tick=result.end_tick,
        speed_mps=scenario.speed_mps,
        load_ticks=scenario.load_ticks,
        unload_ticks=scenario.unload_ticks,
        nodes=scenario.graph.nodes,
        edges=scenario.graph.edges,
        robots=tuple(
            TraceRobot(
                robot.robot_id,
                result.trajectories[robot.robot_id],
                result.energy_levels.get(robot.robot_id),
            )
            for robot in scenario.robots
        ),
        orders=scenario.orders,
        events=result.log,
        outcome=TraceOutcome(
            result.delivered, result.in_progress, result.pending, result.unreleased
        ),
        counters=TraceCounters(
            breakdowns=result.breakdowns,
            breakdown_ticks=result.breakdown_ticks,
            replans=result.replans,
            moves_out_of_the_way=result.moves_out_of_the_way,
            deferred_assignments=result.deferred_assignments,
        ),
        kpis=scalar_values(asdict(kpis) | {"empty_share": kpis.empty_share}),
        charging=scalar_values(asdict(charging)) if charging else None,
        conflicts_total=len(found),
        conflicts=tuple(found[:max_conflicts]),
        battery_model=result.battery_record,
        robot_events=result.robot_events,
    )


# --- checking a trace as a whole ---------------------------------------------------------------


def _fail(message: str) -> TraceValidationError:
    return TraceValidationError(f"trace: {message}")


def _duplicate(values: list[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _check(trace: ReplayTrace) -> None:
    for name in ("tick_s", "speed_mps"):
        value = getattr(trace, name)
        if not (math.isfinite(value) and value > 0):
            raise _fail(f"{name} must be a positive finite number, got {value!r}")
    for name in ("end_tick", "load_ticks", "unload_ticks", "conflicts_total"):
        if getattr(trace, name) < 0:
            raise _fail(f"{name} must be non-negative, got {getattr(trace, name)}")
    try:
        graph = trace.graph()
    except MapValidationError as error:
        raise _fail(f"map: {error}") from error
    robots = _check_robots(trace, graph)
    orders = _check_orders(trace, graph)
    _check_events(trace, graph, robots, orders)
    _check_outcome(trace, orders)
    _check_conflicts(trace, graph, robots)
    _check_battery(trace)
    _check_robot_events(trace, graph, robots)
    for name, values in (("run", trace.run), ("kpis", trace.kpis), ("charging", trace.charging)):
        try:
            json.dumps(values, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise _fail(f"{name} cannot be written as JSON: {error}") from error
    for counter, count in asdict(trace.counters).items():
        if count < 0:
            raise _fail(f"counters.{counter} must be non-negative, got {count}")


def _check_robots(trace: ReplayTrace, graph: WarehouseGraph) -> set[RobotId]:
    if not trace.robots:
        raise _fail("a trace needs at least one robot")
    if duplicate := _duplicate([robot.id for robot in trace.robots]):
        raise _fail(f"duplicate robot id {duplicate!r}")
    for robot in trace.robots:
        visits = robot.path.visits
        if not robot.id:
            raise _fail("robot id must be a non-empty string")
        if visits[0].arrive != 0:
            raise _fail(f"robot {robot.id!r}: the first visit must start at tick 0")
        for visit in visits:
            if visit.node not in graph:
                raise _fail(f"robot {robot.id!r}: unknown node {visit.node!r}")
        for before, after in zip(visits, visits[1:], strict=False):
            if graph.get_edge(before.node, after.node) is None:
                raise _fail(f"robot {robot.id!r}: no edge {before.node!r} -> {after.node!r}")
    return {robot.id for robot in trace.robots}


def _check_orders(trace: ReplayTrace, graph: WarehouseGraph) -> set[OrderId]:
    if duplicate := _duplicate([order.id for order in trace.orders]):
        raise _fail(f"duplicate order id {duplicate!r}")
    for order in trace.orders:
        for node in (order.pickup, order.dropoff):
            if node not in graph:
                raise _fail(f"order {order.id!r}: unknown node {node!r}")
    return {order.id for order in trace.orders}


def _check_events(
    trace: ReplayTrace, graph: WarehouseGraph, robots: set[RobotId], orders: set[OrderId]
) -> None:
    previous = 0
    for index, event in enumerate(trace.events):
        where = f"events[{index}]"
        if not previous <= event.tick <= trace.end_tick:
            raise _fail(
                f"{where}: tick {event.tick} is out of time order or outside the run "
                f"(0 to {trace.end_tick})"
            )
        previous = event.tick
        if event.order_id not in orders:
            raise _fail(f"{where}: unknown order {event.order_id!r}")
        if event.node not in graph:
            raise _fail(f"{where}: unknown node {event.node!r}")
        if (event.robot_id is None) != (event.kind is EventKind.RELEASED):
            raise _fail(f"{where}: every event except a release names a robot")
        if event.robot_id is not None and event.robot_id not in robots:
            raise _fail(f"{where}: unknown robot {event.robot_id!r}")
        if not (math.isfinite(event.distance_m) and event.distance_m >= 0):
            raise _fail(f"{where}: distance_m must be a non-negative finite number")


def _check_outcome(trace: ReplayTrace, orders: set[OrderId]) -> None:
    outcome = trace.outcome
    listed = [
        *outcome.delivered,
        *outcome.in_progress,
        *outcome.pending,
        *outcome.unreleased,
    ]
    if duplicate := _duplicate(list(listed)):
        raise _fail(f"outcome: order {duplicate!r} is listed twice")
    if set(listed) != orders:
        odd = sorted(set(listed) ^ orders)
        raise _fail(f"outcome: must list every order exactly once; check {odd[:5]}")


def _check_conflicts(trace: ReplayTrace, graph: WarehouseGraph, robots: set[RobotId]) -> None:
    if len(trace.conflicts) > trace.conflicts_total:
        raise _fail("conflicts: more are listed than counted")
    for index, conflict in enumerate(trace.conflicts):
        where = f"conflicts[{index}]"
        if conflict.kind not in CONFLICT_KINDS:
            raise _fail(f"{where}: kind {conflict.kind!r} is not one of {list(CONFLICT_KINDS)}")
        if len(conflict.resource) != (1 if conflict.kind == "vertex" else 2):
            raise _fail(f"{where}: a vertex conflict names one node, the others two")
        for node in conflict.resource:
            if node not in graph:
                raise _fail(f"{where}: unknown node {node!r}")
        first, second = conflict.robots
        if first == second or first not in robots or second not in robots:
            raise _fail(f"{where}: needs two different, known robots")
        if not 0 <= conflict.start <= conflict.end:
            raise _fail(f"{where}: needs 0 <= start <= end")


def _check_battery(trace: ReplayTrace) -> None:
    model = trace.battery_model
    if model is not None:
        if not trace.battery:
            raise _fail("battery_model: given for a run without a battery")
        for name, value in asdict(model).items():
            if value <= 0:
                raise _fail(f"battery_model.{name} must be positive, got {value}")
        if not model.low < model.high <= model.capacity:
            raise _fail("battery_model: needs low < high <= capacity")
        if model.back_in_service not in (model.low, model.high):
            raise _fail("battery_model.back_in_service: must be the low or the high threshold")
    for robot in trace.robots:
        if (robot.levels is None) != (model is None):
            raise _fail(f"robot {robot.id!r}: levels are given exactly with a battery model")
        if robot.levels is None or model is None:
            continue
        if len(robot.levels) != len(robot.path.visits):
            raise _fail(f"robot {robot.id!r}: needs one level per visit")
        if not all(0 <= level <= model.capacity for level in robot.levels):
            raise _fail(f"robot {robot.id!r}: a level is outside 0 to {model.capacity}")


def _check_robot_events(trace: ReplayTrace, graph: WarehouseGraph, robots: set[RobotId]) -> None:
    previous = 0
    for index, event in enumerate(trace.robot_events):
        where = f"robot_events[{index}]"
        if not previous <= event.tick <= trace.end_tick:
            raise _fail(
                f"{where}: tick {event.tick} is out of time order or outside the run "
                f"(0 to {trace.end_tick})"
            )
        previous = event.tick
        if event.robot_id not in robots:
            raise _fail(f"{where}: unknown robot {event.robot_id!r}")
        for node in (event.node, event.target):
            if node is not None and node not in graph:
                raise _fail(f"{where}: unknown node {node!r}")
        has = {
            name
            for name in ("target", "ticks", "level", "reason")
            if getattr(event, name) is not None
        }
        expected = ROBOT_EVENT_FIELDS[event.kind]
        if has != expected:
            raise _fail(
                f"{where}: a {event.kind.value} event has the fields {sorted(expected)}, "
                f"got {sorted(has)}"
            )
        if event.ticks is not None and event.ticks <= 0:
            raise _fail(f"{where}: ticks must be positive")
        if event.level is not None:
            if trace.battery_model is None:
                raise _fail(f"{where}: a level without a battery model")
            if not 0 <= event.level <= trace.battery_model.capacity:
                raise _fail(f"{where}: level is outside 0 to {trace.battery_model.capacity}")
        if event.reason is not None and event.reason not in CHARGER_TRIP_REASONS:
            raise _fail(f"{where}: reason must be one of {list(CHARGER_TRIP_REASONS)}")
    _check_event_places(trace, graph)


def _check_event_places(trace: ReplayTrace, graph: WarehouseGraph) -> None:
    """Every robot event must agree with the robot's recorded path.

    A reader derives statuses and corridor waits from the events and positions from the path;
    if the two disagreed, it would show a robot charging in an aisle or delayed while standing.
    """
    paths = {robot.id: robot.path.visits for robot in trace.robots}
    arrivals = {robot_id: [visit.arrive for visit in visits] for robot_id, visits in paths.items()}
    for index, event in enumerate(trace.robot_events):
        where = f"robot_events[{index}]"
        visits = paths[event.robot_id]
        # The visit the robot is in at that tick, or has last left.
        at = bisect.bisect_right(arrivals[event.robot_id], event.tick) - 1
        current = visits[at]
        following = visits[at + 1] if at + 1 < len(visits) else None
        # An event at the tick of an arrival may have been recorded just before the robot
        # entered the node: then it still names the node the robot came from.
        before = visits[at - 1] if at > 0 and current.arrive == event.tick else None
        kind = event.kind
        if kind is _K.CORRIDOR_WAIT_ENDED:
            if before is None or (before.node, current.node) != (event.node, event.target):
                raise _fail(
                    f"{where}: robot {event.robot_id!r} does not enter {event.target!r} from "
                    f"{event.node!r} at tick {event.tick}"
                )
            continue
        if event.node != current.node and (before is None or event.node != before.node):
            raise _fail(
                f"{where}: robot {event.robot_id!r} is not at {event.node!r} at tick {event.tick}"
            )
        if kind in (_K.DELAY, _K.BREAKDOWN, _K.CORRIDOR_WAIT_STARTED):
            # These belong to one traversal: the robot has left ``node`` for ``target``.
            assert event.target is not None  # by ROBOT_EVENT_FIELDS
            left = (
                current.depart < event.tick
                if kind is _K.CORRIDOR_WAIT_STARTED
                else current.depart == event.tick
            )
            towards = (
                following.node == event.target
                if following is not None
                else graph.get_edge(event.node, event.target) is not None
            )
            if event.node != current.node or not left or not towards:
                raise _fail(
                    f"{where}: robot {event.robot_id!r} is not on its way from {event.node!r} "
                    f"to {event.target!r} at tick {event.tick}"
                )
        elif kind in (_K.CHARGING_STARTED, _K.CHARGING_STOPPED):
            if graph.node(event.node).kind is not NodeKind.CHARGER:
                raise _fail(f"{where}: {event.node!r} is not a charger")
            if kind is _K.CHARGING_STOPPED and current.depart != event.tick:
                raise _fail(
                    f"{where}: robot {event.robot_id!r} does not leave {event.node!r} at tick "
                    f"{event.tick}"
                )
        elif kind is _K.CHARGER_TRIP_STARTED:
            assert event.target is not None
            if graph.node(event.target).kind is not NodeKind.CHARGER:
                raise _fail(f"{where}: {event.target!r} is not a charger")


# --- writing -----------------------------------------------------------------------------------


def trace_to_dict(trace: ReplayTrace) -> dict[str, object]:
    """``trace`` as JSON data, with the keys in the documented order.

    Every field that is a real number is written as a float, even where the engine holds a whole
    number (a distance of ``0``). The text then does not depend on how a value was computed, and
    a parsed trace writes the same text again.
    """
    return {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "run": dict(trace.run),
        "policy": trace.policy,
        "traffic": trace.traffic,
        "battery": trace.battery,
        "tick_s": float(trace.tick_s),
        "end_tick": trace.end_tick,
        "speed_mps": float(trace.speed_mps),
        "load_ticks": trace.load_ticks,
        "unload_ticks": trace.unload_ticks,
        "map": {
            "nodes": [
                {"id": n.id, "x": float(n.x), "y": float(n.y), "kind": n.kind.value}
                for n in trace.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target, "length_m": float(e.length_m)}
                for e in trace.edges
            ],
        },
        "robots": [
            {
                "id": robot.id,
                "visits": [
                    [v.node, v.arrive, None if v.depart == FOREVER else v.depart]
                    for v in robot.path.visits
                ],
                "levels": None if robot.levels is None else list(robot.levels),
            }
            for robot in trace.robots
        ],
        "orders": [
            {"id": o.id, "pickup": o.pickup, "dropoff": o.dropoff, "release_tick": o.release_tick}
            for o in trace.orders
        ],
        "events": [
            {
                "tick": e.tick,
                "kind": e.kind.value,
                "order": e.order_id,
                "node": e.node,
                "robot": e.robot_id,
                "distance_m": float(e.distance_m),
            }
            for e in trace.events
        ],
        "outcome": {key: list(ids) for key, ids in asdict(trace.outcome).items()},
        "counters": asdict(trace.counters),
        "kpis": dict(trace.kpis),
        "charging": None if trace.charging is None else dict(trace.charging),
        "conflicts": {
            "total": trace.conflicts_total,
            "listed": [
                {
                    "kind": c.kind,
                    "resource": list(c.resource),
                    "robots": list(c.robots),
                    "start": c.start,
                    "end": c.end,
                }
                for c in trace.conflicts
            ],
        },
        "battery_model": None if trace.battery_model is None else asdict(trace.battery_model),
        "robot_events": [
            {
                "tick": e.tick,
                "kind": e.kind.value,
                "robot": e.robot_id,
                "node": e.node,
                "target": e.target,
                "ticks": e.ticks,
                "level": e.level,
                "reason": e.reason,
            }
            for e in trace.robot_events
        ],
    }


def downgrade_to_v1(data: Mapping[str, object]) -> dict[str, object]:
    """Version 2 trace data without what version 2 added: the version 1 trace of the same run.

    Version 2 only adds to version 1 (D35), so this removes the additions and changes nothing
    else. The tests use it to show that the additions left every older field as it was.
    """
    if data.get("schema_version") != 2:
        raise TraceValidationError("trace.schema_version: only version 2 data can be downgraded")
    older: dict[str, object] = {
        key: value for key, value in data.items() if key not in ("battery_model", "robot_events")
    }
    older["schema_version"] = 1
    robots = data["robots"]
    assert isinstance(robots, list)
    older["robots"] = [{k: v for k, v in robot.items() if k != "levels"} for robot in robots]
    return older


def trace_to_json(trace: ReplayTrace) -> str:
    """``trace`` as compact JSON. The same trace always gives the same text, in ASCII."""
    return json.dumps(trace_to_dict(trace), separators=(",", ":"), allow_nan=False)


def trace_to_gzip(trace: ReplayTrace) -> bytes:
    """`trace_to_json`, compressed. The gzip header carries no timestamp, so on one machine
    the same trace gives the same bytes. (Another zlib build may compress differently; the
    JSON inside is always the same.)"""
    return gzip.compress(trace_to_json(trace).encode("ascii"), mtime=0)


# --- strict parsing ----------------------------------------------------------------------------


def trace_from_json(text: str | bytes) -> ReplayTrace:
    """Parse the output of `trace_to_json`. Raises `TraceValidationError`."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise TraceValidationError(f"trace: not valid JSON ({error})") from error
    return trace_from_dict(data)


_TOP = (
    "format",
    "schema_version",
    "source",
    "run",
    "policy",
    "traffic",
    "battery",
    "tick_s",
    "end_tick",
    "speed_mps",
    "load_ticks",
    "unload_ticks",
    "map",
    "robots",
    "orders",
    "events",
    "outcome",
    "counters",
    "kpis",
    "charging",
    "conflicts",
)


def trace_from_dict(data: object) -> ReplayTrace:
    """Build a trace from parsed JSON. Raises `TraceValidationError` with the location of the
    first problem."""
    head = _object(data, "trace", None)
    if head.get("format") != FORMAT_NAME:
        raise TraceValidationError(
            f"trace.format: must be {FORMAT_NAME!r}, got {head.get('format')!r}"
        )
    # The version is checked before anything else is read: a later version may differ anywhere.
    version = head.get("schema_version")
    if isinstance(version, bool) or version not in READABLE_VERSIONS:
        raise TraceValidationError(f"trace.schema_version: unsupported version {version!r}")
    v2 = version == 2
    top = _object(data, "trace", (*_TOP, "battery_model", "robot_events") if v2 else _TOP)
    _string(top, "source", "trace")
    map_ = _object(top["map"], "map", ("nodes", "edges"))
    outcome = _object(
        top["outcome"], "outcome", ("delivered", "in_progress", "pending", "unreleased")
    )
    counters = _object(
        top["counters"],
        "counters",
        (
            "breakdowns",
            "breakdown_ticks",
            "replans",
            "moves_out_of_the_way",
            "deferred_assignments",
        ),
    )
    conflicts = _object(top["conflicts"], "conflicts", ("total", "listed"))
    try:
        return ReplayTrace(
            run=dict(_object(top["run"], "run", None)),
            policy=_string(top, "policy", "trace"),
            traffic=_boolean(top, "traffic", "trace"),
            battery=_boolean(top, "battery", "trace"),
            tick_s=_number(top, "tick_s", "trace"),
            end_tick=_integer(top, "end_tick", "trace"),
            speed_mps=_number(top, "speed_mps", "trace"),
            load_ticks=_integer(top, "load_ticks", "trace"),
            unload_ticks=_integer(top, "unload_ticks", "trace"),
            nodes=tuple(
                _node(item, f"map.nodes[{i}]") for i, item in enumerate(_list(map_, "nodes", "map"))
            ),
            edges=tuple(
                _edge(item, f"map.edges[{i}]") for i, item in enumerate(_list(map_, "edges", "map"))
            ),
            robots=tuple(
                _robot(item, f"robots[{i}]", with_levels=v2)
                for i, item in enumerate(_list(top, "robots", "trace"))
            ),
            orders=tuple(
                _order(item, f"orders[{i}]") for i, item in enumerate(_list(top, "orders", "trace"))
            ),
            events=tuple(
                _event(item, f"events[{i}]") for i, item in enumerate(_list(top, "events", "trace"))
            ),
            outcome=TraceOutcome(
                *(
                    tuple(OrderId(_text(item, f"outcome.{key}[{i}]")) for i, item in enumerate(ids))
                    for key in ("delivered", "in_progress", "pending", "unreleased")
                    for ids in [_list(outcome, key, "outcome")]
                )
            ),
            counters=TraceCounters(
                **{key: _integer(counters, key, "counters") for key in counters}
            ),
            kpis=_scalars(top["kpis"], "kpis"),
            charging=None if top["charging"] is None else _scalars(top["charging"], "charging"),
            conflicts_total=_integer(conflicts, "total", "conflicts"),
            conflicts=tuple(
                _conflict(item, f"conflicts.listed[{i}]")
                for i, item in enumerate(_list(conflicts, "listed", "conflicts"))
            ),
            battery_model=_battery_model(top["battery_model"]) if v2 else None,
            robot_events=tuple(
                _robot_event(item, f"robot_events[{i}]")
                for i, item in enumerate(_list(top, "robot_events", "trace"))
            )
            if v2
            else (),
        )
    except (MapValidationError, OrderValidationError, InvalidTimedPathError) as error:
        raise TraceValidationError(f"trace: {error}") from error


def _object(value: object, where: str, fields: tuple[str, ...] | None) -> Mapping[str, object]:
    """``value`` as an object with exactly ``fields`` (any string keys if ``fields`` is None)."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TraceValidationError(f"{where}: expected an object")
    if fields is not None:
        if missing := set(fields) - value.keys():
            raise TraceValidationError(f"{where}: missing field(s) {sorted(missing)}")
        if unknown := value.keys() - set(fields):
            raise TraceValidationError(f"{where}: unknown field(s) {sorted(unknown)}")
    return value


def _list(obj: Mapping[str, object], field: str, where: str) -> list[object]:
    value = obj[field]
    if not isinstance(value, list):
        raise TraceValidationError(f"{where}.{field}: expected a list")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TraceValidationError(f"{where}: expected a string, got {value!r}")
    return value


def _whole(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceValidationError(f"{where}: expected an integer, got {value!r}")
    return value


def _string(obj: Mapping[str, object], field: str, where: str) -> str:
    return _text(obj[field], f"{where}.{field}")


def _integer(obj: Mapping[str, object], field: str, where: str) -> int:
    return _whole(obj[field], f"{where}.{field}")


def _boolean(obj: Mapping[str, object], field: str, where: str) -> bool:
    value = obj[field]
    if not isinstance(value, bool):
        raise TraceValidationError(f"{where}.{field}: expected true or false, got {value!r}")
    return value


def _number(obj: Mapping[str, object], field: str, where: str) -> float:
    value = obj[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TraceValidationError(f"{where}.{field}: expected a number, got {value!r}")
    return float(value)


def _scalars(value: object, where: str) -> dict[str, Scalar]:
    scalars: dict[str, Scalar] = {}
    for key, item in _object(value, where, None).items():
        if isinstance(item, bool) or not (item is None or isinstance(item, int | float | str)):
            raise TraceValidationError(f"{where}.{key}: expected a number, a string or null")
        scalars[key] = item
    return scalars


def _node(value: object, where: str) -> Node:
    obj = _object(value, where, ("id", "x", "y", "kind"))
    kind = _string(obj, "kind", where)
    if kind not in {k.value for k in NodeKind}:
        raise TraceValidationError(
            f"{where}.kind: {kind!r} is not one of {[k.value for k in NodeKind]}"
        )
    return Node(
        NodeId(_string(obj, "id", where)),
        _number(obj, "x", where),
        _number(obj, "y", where),
        NodeKind(kind),
    )


def _edge(value: object, where: str) -> Edge:
    obj = _object(value, where, ("source", "target", "length_m"))
    return Edge(
        NodeId(_string(obj, "source", where)),
        NodeId(_string(obj, "target", where)),
        _number(obj, "length_m", where),
    )


_BATTERY_FIELDS = (
    "capacity",
    "charge_per_tick",
    "low",
    "high",
    "back_in_service",
    "units_per_metre",
)


def _battery_model(value: object) -> BatteryRecord | None:
    if value is None:
        return None
    obj = _object(value, "battery_model", _BATTERY_FIELDS)
    return BatteryRecord(**{name: _integer(obj, name, "battery_model") for name in _BATTERY_FIELDS})


def _robot_event(value: object, where: str) -> RobotEvent:
    obj = _object(
        value, where, ("tick", "kind", "robot", "node", "target", "ticks", "level", "reason")
    )
    kind = _string(obj, "kind", where)
    if kind not in {k.value for k in RobotEventKind}:
        raise TraceValidationError(
            f"{where}.kind: {kind!r} is not one of {[k.value for k in RobotEventKind]}"
        )
    return RobotEvent(
        tick=_integer(obj, "tick", where),
        kind=RobotEventKind(kind),
        robot_id=RobotId(_string(obj, "robot", where)),
        node=NodeId(_string(obj, "node", where)),
        target=None if obj["target"] is None else NodeId(_string(obj, "target", where)),
        ticks=None if obj["ticks"] is None else _integer(obj, "ticks", where),
        level=None if obj["level"] is None else _integer(obj, "level", where),
        reason=None if obj["reason"] is None else _string(obj, "reason", where),
    )


def _robot(value: object, where: str, *, with_levels: bool) -> TraceRobot:
    obj = _object(value, where, ("id", "visits", "levels") if with_levels else ("id", "visits"))
    visits = []
    for i, item in enumerate(_list(obj, "visits", where)):
        at = f"{where}.visits[{i}]"
        if not isinstance(item, list) or len(item) != 3:
            raise TraceValidationError(f"{at}: expected [node, arrive, depart]")
        node, arrive, depart = item
        try:
            visits.append(
                Visit(
                    NodeId(_text(node, f"{at}[0]")),
                    _whole(arrive, f"{at}[1]"),
                    FOREVER if depart is None else _whole(depart, f"{at}[2]"),
                )
            )
        except InvalidTimedPathError as error:
            raise TraceValidationError(f"{at}: {error}") from error
    try:
        path = TimedPath(tuple(visits))
    except InvalidTimedPathError as error:
        raise TraceValidationError(f"{where}.visits: {error}") from error
    levels: tuple[int, ...] | None = None
    if with_levels and obj["levels"] is not None:
        levels = tuple(
            _whole(level, f"{where}.levels[{i}]")
            for i, level in enumerate(_list(obj, "levels", where))
        )
    return TraceRobot(RobotId(_string(obj, "id", where)), path, levels)


def _order(value: object, where: str) -> TransportOrder:
    obj = _object(value, where, ("id", "pickup", "dropoff", "release_tick"))
    try:
        return TransportOrder(
            OrderId(_string(obj, "id", where)),
            NodeId(_string(obj, "pickup", where)),
            NodeId(_string(obj, "dropoff", where)),
            _integer(obj, "release_tick", where),
        )
    except OrderValidationError as error:
        raise TraceValidationError(f"{where}: {error}") from error


def _event(value: object, where: str) -> LogEntry:
    obj = _object(value, where, ("tick", "kind", "order", "node", "robot", "distance_m"))
    kind = _string(obj, "kind", where)
    if kind not in {k.value for k in EventKind}:
        raise TraceValidationError(
            f"{where}.kind: {kind!r} is not one of {[k.value for k in EventKind]}"
        )
    return LogEntry(
        tick=_integer(obj, "tick", where),
        kind=EventKind(kind),
        order_id=OrderId(_string(obj, "order", where)),
        node=NodeId(_string(obj, "node", where)),
        robot_id=None if obj["robot"] is None else RobotId(_string(obj, "robot", where)),
        distance_m=_number(obj, "distance_m", where),
    )


def _conflict(value: object, where: str) -> TraceConflict:
    obj = _object(value, where, ("kind", "resource", "robots", "start", "end"))
    resource = _list(obj, "resource", where)
    robots = _list(obj, "robots", where)
    if len(robots) != 2:
        raise TraceValidationError(f"{where}.robots: expected two robots")
    return TraceConflict(
        kind=_string(obj, "kind", where),
        resource=tuple(NodeId(_text(n, f"{where}.resource[{i}]")) for i, n in enumerate(resource)),
        robots=(
            RobotId(_text(robots[0], f"{where}.robots[0]")),
            RobotId(_text(robots[1], f"{where}.robots[1]")),
        ),
        start=_integer(obj, "start", where),
        end=_integer(obj, "end", where),
    )
