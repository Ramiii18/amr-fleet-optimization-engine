"""Tests for robot events and battery readings (D35): observations of a run.

Three things are checked:

- **by hand:** a charging cycle and a breakdown, event by event, against timelines derived by
  hand in the battery and breakdown tests;
- **reconciliation:** on generated runs, the events add up to the totals the simulator has
  always counted on its own (charging time, time out of service, breakdowns, ...), and every
  traversal's duration is explained by its edge, its delay, its repair and its wait;
- **an energy oracle:** every recorded level follows from the previous one by the model's own
  rules, recomputed here from the events and the map.
"""

from collections import defaultdict
from collections.abc import Iterator

import pytest

from amr_optimizer.dispatch import POLICIES, GreedyDispatch
from amr_optimizer.domain import (
    BatterySpec,
    ChargingPolicy,
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import RobotEvent, SimulationResult, simulate
from amr_optimizer.sim import RobotEventKind as K
from amr_optimizer.traffic import NO_DELAYS, DelayModel, ExecutionPolicy, Recovery
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

R1, R2 = RobotId("R1"), RobotId("R2")

# Pairs of events that open and close an interval.
INTERVALS = {
    K.CHARGING_STARTED: K.CHARGING_STOPPED,
    K.OUT_OF_SERVICE: K.BACK_IN_SERVICE,
    K.CHARGER_WAIT_STARTED: K.CHARGER_WAIT_ENDED,
    K.CORRIDOR_WAIT_STARTED: K.CORRIDOR_WAIT_ENDED,
}


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


def _brief(events: tuple[RobotEvent, ...], robot: RobotId) -> list[tuple[object, ...]]:
    """A robot's events as (tick, kind, node, the fields that are set)."""
    return [
        (
            e.tick,
            e.kind,
            e.node,
            *(v for v in (e.target, e.ticks, e.level, e.reason) if v is not None),
        )
        for e in events
        if e.robot_id == robot
    ]


# --- by hand: a charging cycle -------------------------------------------------------------------
#
# The hand map and case of test_battery.py (0.1 s ticks at 1 m/s; a battery of 64,000 units that
# charges 100 units per tick; low threshold 16,000, high threshold 57,600):
#
#              A       B
#              |       |
#      P1 --   W ===== E   -- CH
#              |       |
#              S1      S2


def _battery_map() -> WarehouseGraph:
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
    return WarehouseGraph(
        nodes, [Edge(u, v, length) for x, y, length in links for u, v in ((x, y), (y, x))]
    )


def _charging_cycle() -> SimulationResult:
    a, b, s1, s2, p1 = (NodeId(n) for n in ("A", "B", "S1", "S2", "P1"))
    orders = [TransportOrder(OrderId("O1"), a, s1, 0), TransportOrder(OrderId("O2"), b, s2, 300)]
    battery = BatterySpec(
        capacity_m=64.0, full_charge_s=64.0, start_min_share=0.30, start_max_share=0.30
    )
    return simulate(
        _scenario(_battery_map(), {R1: p1}, orders), GreedyDispatch(), traffic=True, battery=battery
    )


def test_a_charging_cycle_is_recorded_event_by_event() -> None:
    # R1 starts with 19,200 units and does O1 (6 m). Idle at P1 at tick 70 with 13,200 units, it
    # is below the low threshold: out of service, and sent to the charger at once (no wait). It
    # arrives at tick 110 with 9,200 units and charges 100 units per tick. At tick 594 it has
    # 9,200 + 484 * 100 = 57,600, the high threshold: back in service, and it leaves for O2.
    result = _charging_cycle()
    p1, ch = NodeId("P1"), NodeId("CH")

    assert _brief(result.robot_events, R1) == [
        (70, K.OUT_OF_SERVICE, p1, 13_200),
        (70, K.CHARGER_TRIP_STARTED, p1, ch, 13_200, "low"),
        (110, K.CHARGING_STARTED, ch, 9_200),
        (594, K.BACK_IN_SERVICE, ch, 57_600),
        (594, K.CHARGING_STOPPED, ch, 57_600),
    ]
    # One level per visit, on arrival: a metre costs 1,000 units, and W = E is 2 m.
    assert result.energy_levels[R1][:12] == (
        19_200,  # P1, the start
        18_200,  # W
        17_200,  # A (loading costs nothing)
        16_200,  # W
        15_200,  # S1
        14_200,  # W
        13_200,  # P1: found low here
        12_200,  # W
        10_200,  # E
        9_200,  # CH: the lowest level of the run
        56_600,  # E, after charging to 57,600 and one metre
        55_600,  # B
    )
    assert len(result.energy_levels[R1]) == len(result.trajectories[R1].visits)
    record = result.battery_record
    assert record is not None
    assert (record.capacity, record.charge_per_tick, record.low, record.high) == (
        64_000,
        100,
        16_000,
        57_600,
    )
    assert (record.back_in_service, record.units_per_metre) == (57_600, 1_000)


def test_a_robot_that_starts_on_a_charger_charges_from_tick_0() -> None:
    ch, a, s1 = NodeId("CH"), NodeId("A"), NodeId("S1")
    battery = BatterySpec(
        capacity_m=64.0, full_charge_s=64.0, start_min_share=0.10, start_max_share=0.10
    )
    scenario = _scenario(_battery_map(), {R1: ch}, [TransportOrder(OrderId("O1"), a, s1, 0)])

    result = simulate(scenario, GreedyDispatch(), traffic=True, battery=battery)

    # 6,400 units at the start: charging from tick 0, out of service at the first decision, and
    # back at 57,600 after (57,600 - 6,400) / 100 = 512 ticks. Never sent anywhere: no trip.
    assert _brief(result.robot_events, R1)[:4] == [
        (0, K.CHARGING_STARTED, ch, 6_400),
        (0, K.OUT_OF_SERVICE, ch, 6_400),
        (512, K.BACK_IN_SERVICE, ch, 57_600),
        (512, K.CHARGING_STOPPED, ch, 57_600),
    ]
    assert result.charging_trips == 0 and result.out_of_service_ticks == 512


# --- by hand: a breakdown ------------------------------------------------------------------------
#
# The ring and case of test_breakdowns.py:
#
#     P1, P3 - A ===== B - X1, X3
#              |       |
#              C ===== D - S, X2


class _Scripted(DelayModel):
    """The k-th departure of the run draws the k-th value of the script."""

    def __init__(self, *script: int) -> None:
        super().__init__(1.0)
        object.__setattr__(self, "_script", list(script))

    def draw(self, rng: object) -> int:
        script: list[int] = self._script  # type: ignore[attr-defined]
        return script.pop(0) if script else 0


def _ring() -> WarehouseGraph:
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
    return WarehouseGraph(
        nodes, [Edge(u, v, length) for x, y, length in links for u, v in ((x, y), (y, x))]
    )


def _ring_run(recovery: Recovery, *, delays: DelayModel = NO_DELAYS) -> SimulationResult:
    orders = [
        TransportOrder(OrderId("O1"), NodeId("X1"), NodeId("S"), 0),
        TransportOrder(OrderId("O2"), NodeId("X3"), NodeId("S"), 15),
    ]
    scenario = _scenario(_ring(), {R1: NodeId("P1"), R2: NodeId("P3")}, orders)
    return simulate(
        scenario,
        GreedyDispatch(),
        traffic=True,
        delays=delays,
        breakdowns=_Scripted(0, 300),
        recovery=recovery,
    )


def test_a_breakdown_is_recorded_at_the_corridor_entry_with_its_repair_time() -> None:
    # R1's second departure (A -> B, at tick 10) breaks down for 300 ticks: it reaches B at
    # 10 + 20 + 300 = 330. The event is at tick 10, where the simulator draws it. It does not
    # say where in the corridor the robot stands still: the model has no such place.
    result = _ring_run(Recovery.WAIT)

    breakdowns = [e for e in result.robot_events if e.kind is K.BREAKDOWN]
    assert breakdowns == [RobotEvent(10, K.BREAKDOWN, R1, NodeId("A"), NodeId("B"), ticks=300)]
    assert (result.breakdowns, result.breakdown_ticks) == (1, 300)
    assert result.trajectories[R1].visits[1].depart == 10
    assert result.trajectories[R1].visits[2].arrive == 330
    assert not [e for e in result.robot_events if e.kind in (K.DELAY, K.REPLANNED)]


def test_a_delay_and_a_breakdown_on_one_traversal_are_two_events_with_their_own_ticks() -> None:
    # The same run, with every traversal also delayed by 4 ticks. A delay and a breakdown are
    # two causes, drawn from two streams: the traversal A -> B has one event of each, and takes
    # 20 (the edge) + 4 (the delay) + 300 (the repair) ticks.
    result = _ring_run(Recovery.WAIT, delays=DelayModel(1.0, 4, 4))
    a, b = NodeId("A"), NodeId("B")

    on_the_edge = [
        e for e in result.robot_events if e.robot_id == R1 and (e.node, e.target) == (a, b)
    ]
    depart = result.trajectories[R1].visits[1].depart
    assert [(e.tick, e.kind, e.ticks) for e in on_the_edge if e.kind in (K.DELAY, K.BREAKDOWN)] == [
        (depart, K.DELAY, 4),
        (depart, K.BREAKDOWN, 300),
    ]
    assert result.trajectories[R1].visits[2].arrive >= depart + 20 + 4 + 300
    assert (result.breakdowns, result.breakdown_ticks) == (1, 300)  # the delay is not a breakdown


def test_a_replan_is_recorded_when_a_new_route_is_adopted() -> None:
    orders = [
        TransportOrder(OrderId("O1"), NodeId("X1"), NodeId("S"), 0),
        TransportOrder(OrderId("O2"), NodeId("X3"), NodeId("S"), 0),
    ]
    scenario = _scenario(_ring(), {R1: NodeId("P1"), R2: NodeId("P3")}, orders)

    result = simulate(
        scenario,
        GreedyDispatch(),
        traffic=True,
        breakdowns=_Scripted(0, 300),
        recovery=Recovery.REPLAN,
    )

    replans = [e for e in result.robot_events if e.kind is K.REPLANNED]
    assert len(replans) == result.replans > 0
    # Recovery runs at the breakdown, so that is when a new route is adopted.
    broke_at = {e.tick for e in result.robot_events if e.kind is K.BREAKDOWN}
    assert {e.tick for e in replans} <= broke_at and len(broke_at) == 1


# --- reconciliation on generated runs ------------------------------------------------------------

MAPS = {name: generate_grid_warehouse(PRESETS[name]) for name in ("small", "medium")}
DELAYS = DelayModel(0.15, 5, 40)
BREAKDOWNS = DelayModel(0.03, 100, 900)


def _run(
    map_name: str,
    robots: int,
    seed: int,
    *,
    charging: ChargingPolicy | None,
    recovery: Recovery | None,
    delays: bool,
    max_tick: int | None = None,
    policy: str = "insertion-fair",
) -> SimulationResult:
    spec = ScenarioSpec(robots=robots, orders_per_hour=robots * 45.0, duration_s=900.0)
    scenario = generate_scenario(MAPS[map_name], spec, seed=seed)
    return simulate(
        scenario,
        POLICIES[policy](),
        max_tick=max_tick,
        traffic=True,
        delays=DELAYS if delays else NO_DELAYS,
        delay_seed=seed,
        battery=BatterySpec() if charging else None,
        battery_seed=seed,
        charging=charging or ChargingPolicy.WHEN_LOW,
        breakdowns=BREAKDOWNS if recovery else NO_DELAYS,
        breakdown_seed=seed,
        recovery=recovery or Recovery.WAIT,
    )


# (map, robots, seed, charging policy or None, recovery policy or None, with delays)
Setting = tuple[str, int, int, ChargingPolicy | None, Recovery | None, bool]
SETTINGS: list[Setting] = [
    ("small", 3, 1, ChargingPolicy.WHEN_LOW, Recovery.WAIT, True),
    ("small", 4, 2, ChargingPolicy.OPPORTUNISTIC, Recovery.RETIME, False),
    ("small", 4, 3, ChargingPolicy.OPPORTUNISTIC_EARLY, Recovery.REPLAN, True),
    ("medium", 6, 4, ChargingPolicy.WHEN_LOW, Recovery.REPLAN, True),
    ("medium", 8, 5, ChargingPolicy.OPPORTUNISTIC, Recovery.REPLAN, False),
    ("medium", 8, 6, ChargingPolicy.OPPORTUNISTIC_EARLY, Recovery.WAIT, True),
    ("medium", 6, 7, None, Recovery.REPLAN, True),
    ("small", 3, 8, ChargingPolicy.OPPORTUNISTIC, None, False),
    ("small", 4, 9, None, None, True),
]
CASES = [
    pytest.param(
        (m, r, s, c, v, d),
        id=f"{m}-{r}-seed{s}-{c.value if c else 'nobattery'}-"
        f"{v.value if v else 'nobreak'}-{'delays' if d else 'ontime'}",
    )
    for (m, r, s, c, v, d) in SETTINGS
]


@pytest.fixture(scope="module", params=CASES)
def result(request: pytest.FixtureRequest) -> SimulationResult:
    m, r, s, c, v, d = request.param
    return _run(m, r, s, charging=c, recovery=v, delays=d)


def _closed(result: SimulationResult, start: K) -> Iterator[tuple[RobotEvent, int]]:
    """Every interval opened by ``start``: its opening event and its end tick. An interval that
    is still open at the end of the run ends at the run's last tick."""
    end = INTERVALS[start]
    opened: dict[RobotId, RobotEvent] = {}
    for event in result.robot_events:
        if event.kind is start:
            assert event.robot_id not in opened, f"{start} twice in a row for {event.robot_id}"
            opened[event.robot_id] = event
        elif event.kind is end:
            first = opened.pop(event.robot_id)  # a KeyError here is an end without a start
            yield first, event.tick
    for first in opened.values():
        yield first, result.end_tick


def _total(result: SimulationResult, start: K) -> int:
    return sum(end - first.tick for first, end in _closed(result, start))


def test_events_are_in_time_order_and_within_the_run(result: SimulationResult) -> None:
    ticks = [event.tick for event in result.robot_events]
    assert ticks == sorted(ticks)
    assert all(0 <= tick <= result.end_tick for tick in ticks)
    robots = {robot.robot_id for robot in result.scenario.robots}
    graph = result.scenario.graph
    for event in result.robot_events:
        assert event.robot_id in robots and event.node in graph
        assert event.target is None or event.target in graph


def test_intervals_open_and_close_in_turn(result: SimulationResult) -> None:
    for start in INTERVALS:
        for first, end in _closed(result, start):  # asserts the alternation
            assert end >= first.tick
    # A corridor wait always ends: the run ends only when no robot is moving.
    starts = sum(e.kind is K.CORRIDOR_WAIT_STARTED for e in result.robot_events)
    ends = sum(e.kind is K.CORRIDOR_WAIT_ENDED for e in result.robot_events)
    assert starts == ends


def test_the_events_add_up_to_the_simulators_own_counters(result: SimulationResult) -> None:
    events = result.robot_events

    def count(kind: K, reason: str | None = None) -> int:
        return sum(e.kind is kind and (reason is None or e.reason == reason) for e in events)

    # Breakdowns: the counter counts events, and its ticks are theirs.
    assert count(K.BREAKDOWN) == result.breakdowns
    assert sum(e.ticks or 0 for e in events if e.kind is K.BREAKDOWN) == result.breakdown_ticks
    assert count(K.REPLANNED) == result.replans
    assert count(K.MOVED_OUT_OF_THE_WAY) == result.moves_out_of_the_way
    # Charging: each interval is counted once, by the counter of its own kind.
    assert _total(result, K.CHARGING_STARTED) == result.charger_ticks
    assert _total(result, K.OUT_OF_SERVICE) == result.out_of_service_ticks
    assert _total(result, K.CHARGER_WAIT_STARTED) == result.charger_wait_ticks
    assert count(K.CHARGER_TRIP_STARTED, "low") == result.charging_trips
    assert count(K.CHARGER_TRIP_STARTED, "top_up") == result.top_up_trips
    assert count(K.CHARGER_TRIP_STARTED) == result.charging_trips + result.top_up_trips
    if result.battery:
        assert result.battery_record is not None and result.lowest_energy_share is not None
        lowest = min(min(levels) for levels in result.energy_levels.values())
        assert lowest / result.battery_record.capacity == result.lowest_energy_share
    else:
        assert result.battery_record is None and not result.energy_levels
        battery_kinds = {K.CHARGING_STARTED, K.OUT_OF_SERVICE, K.CHARGER_WAIT_STARTED}
        assert not [e for e in events if e.kind in battery_kinds | {K.CHARGER_TRIP_STARTED}]


def test_the_cases_cover_every_kind_of_event() -> None:
    seen: set[K] = set()
    for m, r, s, c, v, d in SETTINGS:
        seen |= {
            event.kind for event in _run(m, r, s, charging=c, recovery=v, delays=d).robot_events
        }
    # Waiting for a charger needs every charger taken: the hand case below covers it.
    assert seen >= set(K) - {K.CHARGER_WAIT_STARTED, K.CHARGER_WAIT_ENDED}


def test_waiting_for_a_charger_is_recorded_and_adds_up() -> None:
    # Two robots that both start low, and one charger: the second waits for it.
    a, s1, p1, w = NodeId("A"), NodeId("S1"), NodeId("P1"), NodeId("W")
    battery = BatterySpec(
        capacity_m=64.0, full_charge_s=64.0, start_min_share=0.10, start_max_share=0.10
    )
    scenario = _scenario(
        _battery_map(), {R1: p1, R2: NodeId("B")}, [TransportOrder(OrderId("O1"), a, s1, 0)]
    )

    result = simulate(scenario, GreedyDispatch(), traffic=True, battery=battery)

    waits = list(_closed(result, K.CHARGER_WAIT_STARTED))
    assert len(waits) == 1 and waits[0][0].tick == 0
    assert _total(result, K.CHARGER_WAIT_STARTED) == result.charger_wait_ticks > 0
    assert _total(result, K.OUT_OF_SERVICE) == result.out_of_service_ticks
    assert _total(result, K.CHARGING_STARTED) == result.charger_ticks
    assert w in result.scenario.graph  # the map of the case above


def test_every_traversal_is_explained_by_its_edge_delay_repair_and_wait(
    result: SimulationResult,
) -> None:
    # arrive - depart = the edge's travel time + delay + repair + wait at the end of the corridor.
    # Nothing else can lengthen a traversal, and nothing is counted twice.
    scenario = result.scenario
    times = TravelTimes(
        scenario.graph, speed_mps=scenario.speed_mps, time_model=scenario.time_model
    )
    extra: dict[tuple[RobotId, int, K], int] = defaultdict(int)
    wait_start: dict[tuple[RobotId, NodeId, NodeId], int] = {}
    waited: dict[tuple[RobotId, int], tuple[int, int]] = {}
    for event in result.robot_events:
        if event.kind in (K.DELAY, K.BREAKDOWN):
            assert (event.robot_id, event.tick, event.kind) not in extra  # once per traversal
            assert event.ticks is not None and event.ticks > 0
            extra[(event.robot_id, event.tick, event.kind)] = event.ticks
        elif event.kind is K.CORRIDOR_WAIT_STARTED:
            assert event.target is not None
            wait_start[(event.robot_id, event.node, event.target)] = event.tick
        elif event.kind is K.CORRIDOR_WAIT_ENDED:
            assert event.target is not None
            started = wait_start.pop((event.robot_id, event.node, event.target))
            waited[(event.robot_id, event.tick)] = (started, event.tick)

    explained = delayed = waits = 0
    for robot_id, path in result.trajectories.items():
        for before, after in zip(path.visits, path.visits[1:], strict=False):
            edge = times.edge_ticks(before.node, after.node)
            delay = extra.pop((robot_id, before.depart, K.DELAY), 0)
            repair = extra.pop((robot_id, before.depart, K.BREAKDOWN), 0)
            started, ended = waited.pop((robot_id, after.arrive), (after.arrive, after.arrive))
            # The wait starts the moment the robot reaches the end of the corridor.
            assert started == before.depart + edge + delay + repair
            assert after.arrive - before.depart == edge + delay + repair + (ended - started)
            explained += 1
            delayed += bool(delay or repair)
            waits += ended > started
    assert not extra and not waited and not wait_start  # every event belongs to a traversal
    assert explained > 50
    if result.breakdowns:
        assert delayed > 0
    assert waits == sum(e.kind is K.CORRIDOR_WAIT_ENDED for e in result.robot_events)


def test_every_level_follows_from_the_previous_one_by_the_models_rules(
    result: SimulationResult,
) -> None:
    record = result.battery_record
    if record is None:
        pytest.skip("no battery model in this case")
    graph = result.scenario.graph
    charging = {
        (first.robot_id, first.tick): (first, end)
        for first, end in _closed(result, K.CHARGING_STARTED)
    }
    stopped = {(e.robot_id, e.tick): e for e in result.robot_events if e.kind is K.CHARGING_STOPPED}
    checked = charged = 0
    for robot_id, path in result.trajectories.items():
        levels = result.energy_levels[robot_id]
        assert len(levels) == len(path.visits)
        assert all(0 <= level <= record.capacity for level in levels)
        for i in range(1, len(levels)):
            before, after = path.visits[i - 1], path.visits[i]
            level = levels[i - 1]
            interval = charging.get((robot_id, before.arrive))
            if interval is not None and graph.node(before.node).kind is NodeKind.CHARGER:
                first, end = interval
                # Charging starts on arrival, at the level of that arrival, and runs until the
                # robot leaves: min(capacity, level + ticks * rate), the simulator's formula.
                assert first.level == level and end == before.depart
                level = min(record.capacity, level + (end - first.tick) * record.charge_per_tick)
                assert stopped[(robot_id, end)].level == level
                charged += 1
            edge = graph.get_edge(before.node, after.node)
            assert edge is not None
            # Driving is the only thing that costs energy, and it is charged on arrival.
            assert levels[i] == level - round(edge.length_m * record.units_per_metre)
            checked += 1
    assert checked > 50 and charged > 0


def test_event_levels_are_the_levels_the_model_gives_at_that_tick(result: SimulationResult) -> None:
    record = result.battery_record
    if record is None:
        pytest.skip("no battery model in this case")
    arrivals = {
        (robot_id, visit.arrive, visit.node): result.energy_levels[robot_id][i]
        for robot_id, path in result.trajectories.items()
        for i, visit in enumerate(path.visits)
    }
    charging_from: dict[RobotId, RobotEvent] = {}
    for event in result.robot_events:
        if event.kind is K.CHARGING_STARTED:
            assert event.level == arrivals[(event.robot_id, event.tick, event.node)]
            charging_from[event.robot_id] = event
        elif event.kind is K.CHARGING_STOPPED:
            charging_from.pop(event.robot_id)
        elif event.level is not None:
            start = charging_from.get(event.robot_id)
            if start is not None:  # on a charger: the formula, from the start of charging
                assert start.level is not None
                expected = min(
                    record.capacity,
                    start.level + (event.tick - start.tick) * record.charge_per_tick,
                )
                assert event.level == expected
            if event.kind is K.OUT_OF_SERVICE:
                assert event.level < record.low
            if event.kind is K.BACK_IN_SERVICE:
                assert event.level >= record.back_in_service


# --- observations change nothing -----------------------------------------------------------------


def test_the_same_run_records_the_same_events() -> None:
    first, second = (
        _run(
            "medium",
            6,
            11,
            charging=ChargingPolicy.OPPORTUNISTIC,
            recovery=Recovery.REPLAN,
            delays=True,
        )
        for _ in range(2)
    )

    assert first.robot_events == second.robot_events and len(first.robot_events) > 20
    assert dict(first.energy_levels) == dict(second.energy_levels)
    assert first.log == second.log and dict(first.trajectories) == dict(second.trajectories)


def test_a_run_cut_short_closes_nothing_and_still_adds_up() -> None:
    whole = _run(
        "medium", 8, 5, charging=ChargingPolicy.OPPORTUNISTIC, recovery=Recovery.REPLAN, delays=True
    )
    cut_at = whole.end_tick // 2

    cut = _run(
        "medium",
        8,
        5,
        charging=ChargingPolicy.OPPORTUNISTIC,
        recovery=Recovery.REPLAN,
        delays=True,
        max_tick=cut_at,
    )

    # Up to the cut, the two runs are the same run: the same events, in the same order.
    assert cut.robot_events == tuple(e for e in whole.robot_events if e.tick <= cut.end_tick)
    # Intervals still open are closed at the run's last tick, as the counters close them.
    assert _total(cut, K.CHARGING_STARTED) == cut.charger_ticks
    assert _total(cut, K.OUT_OF_SERVICE) == cut.out_of_service_ticks
    assert _total(cut, K.CHARGER_WAIT_STARTED) == cut.charger_wait_ticks
    for robot_id, path in cut.trajectories.items():
        assert len(cut.energy_levels[robot_id]) == len(path.visits)


def test_free_flow_and_naive_execution_record_what_they_have() -> None:
    spec = ScenarioSpec(robots=4, orders_per_hour=200.0, duration_s=600.0)
    scenario = generate_scenario(MAPS["small"], spec, seed=3)

    free_flow = simulate(scenario, GreedyDispatch())
    naive = simulate(
        scenario,
        GreedyDispatch(),
        traffic=True,
        delays=DELAYS,
        delay_seed=3,
        execution=ExecutionPolicy.NAIVE,
    )

    # Free flow has no batteries, no breakdowns and no waiting: nothing to record.
    assert free_flow.robot_events == () and not free_flow.energy_levels
    # Naive execution has delays, and never holds a robot at the end of a corridor.
    kinds = {event.kind for event in naive.robot_events}
    assert K.DELAY in kinds and K.CORRIDOR_WAIT_STARTED not in kinds
