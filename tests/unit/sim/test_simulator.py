"""Tests for the discrete-event simulator, on hand-checkable scenarios.

Unless stated otherwise: 0.1 s ticks and 1 m/s, so each 1 m edge of ``line_graph`` takes
10 ticks; loading and unloading take 5 ticks each.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from amr_optimizer.dispatch import GreedyDispatch
from amr_optimizer.domain import (
    OrderId,
    RobotId,
    RobotPhase,
    RobotState,
    TimeModel,
    TransportOrder,
)
from amr_optimizer.routing import Route
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import (
    Assignment,
    DispatchSnapshot,
    EventKind,
    InvalidAssignmentError,
    LogEntry,
    SimulationInvariantError,
    simulate,
)
from amr_optimizer.sim.simulator import _Simulation
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
R1, R2 = RobotId("R1"), RobotId("R2")
O1, O2, O3 = OrderId("O1"), OrderId("O2"), OrderId("O3")


def _order(order_id: OrderId, pickup: NodeId, dropoff: NodeId, release: int) -> TransportOrder:
    return TransportOrder(order_id, pickup, dropoff, release)


def _scenario(
    graph: WarehouseGraph,
    robots: dict[RobotId, NodeId],
    orders: Sequence[TransportOrder],
    *,
    load_ticks: int = 5,
    unload_ticks: int = 5,
) -> Scenario:
    return Scenario(
        graph=graph,
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=load_ticks,
        unload_ticks=unload_ticks,
        robots=tuple(RobotState.idle(rid, node) for rid, node in robots.items()),
        orders=tuple(orders),
    )


class FirstComeFirstServed:
    """Test policy: the oldest pending orders go to idle robots in scenario order."""

    name = "fcfs-test"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        return [
            Assignment(robot.robot_id, order.id)
            for robot, order in zip(snapshot.idle_robots, snapshot.pending_orders, strict=False)
        ]


@dataclass
class Scripted:
    """Test policy that returns fixed assignments, and records every snapshot it sees."""

    assignments: list[Assignment]
    name: str = "scripted-test"
    seen: list[DispatchSnapshot] = field(default_factory=list)

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        self.seen.append(snapshot)
        return self.assignments


def _steps(log: Sequence[LogEntry]) -> list[tuple[int, str, str, str | None, float]]:
    return [(e.tick, e.kind.name, e.node, e.robot_id, e.distance_m) for e in log]


# --- hand-computed timelines ---------------------------------------------------------------


def test_single_job_timeline(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, {R1: A}, [_order(O1, B, C, 0)])

    result = simulate(scenario, FirstComeFirstServed())

    assert _steps(result.log) == [
        (0, "RELEASED", "B", None, 0.0),
        (0, "ASSIGNED", "A", "R1", 1.0),  # drive A -> B: 10 ticks
        (10, "PICKUP_REACHED", "B", "R1", 0.0),  # load: 5 ticks
        (15, "LOADED", "B", "R1", 1.0),  # drive B -> C: 10 ticks
        (25, "DROPOFF_REACHED", "C", "R1", 0.0),  # unload: 5 ticks
        (30, "DELIVERED", "C", "R1", 0.0),
    ]
    assert result.end_tick == 30
    assert result.delivered == (O1,)
    assert result.all_delivered
    assert (result.in_progress, result.pending, result.unreleased) == ((), (), ())
    assert result.robots == (RobotState.idle(R1, C, tick=30),)
    assert result.policy_name == "fcfs-test"


def test_robot_at_the_pickup_starts_loading_immediately(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, {R1: B}, [_order(O1, B, C, 3)])

    result = simulate(scenario, FirstComeFirstServed())

    assert [(e.tick, e.kind) for e in result.log] == [
        (3, EventKind.RELEASED),
        (3, EventKind.ASSIGNED),
        (3, EventKind.PICKUP_REACHED),
        (8, EventKind.LOADED),
        (18, EventKind.DROPOFF_REACHED),
        (23, EventKind.DELIVERED),
    ]
    assert result.log[1].distance_m == 0.0


def test_zero_handling_times_chain_events_within_one_tick(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, {R1: B}, [_order(O1, B, C, 0)], load_ticks=0, unload_ticks=0)

    result = simulate(scenario, FirstComeFirstServed())

    assert [(e.tick, e.kind.name) for e in result.log] == [
        (0, "RELEASED"),
        (0, "ASSIGNED"),
        (0, "PICKUP_REACHED"),
        (0, "LOADED"),
        (10, "DROPOFF_REACHED"),
        (10, "DELIVERED"),
    ]


def test_orders_queue_while_the_only_robot_is_busy(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, C, A, 0)]
    result = simulate(_scenario(line_graph, {R1: A}, orders), FirstComeFirstServed())

    o2 = [(e.tick, e.kind.name) for e in result.log if e.order_id == O2]
    assert o2 == [
        (0, "RELEASED"),
        (30, "ASSIGNED"),  # R1 delivers O1 at C at tick 30 and is already at O2's pickup
        (30, "PICKUP_REACHED"),
        (35, "LOADED"),
        (55, "DROPOFF_REACHED"),  # C -> A is 2 m: 20 ticks
        (60, "DELIVERED"),
    ]
    assert result.delivered == (O1, O2)


def test_two_robots_work_in_parallel(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, B, A, 0)]
    result = simulate(_scenario(line_graph, {R1: A, R2: C}, orders), FirstComeFirstServed())

    assigned = {e.order_id: e.robot_id for e in result.log if e.kind is EventKind.ASSIGNED}
    assert assigned == {O1: R1, O2: R2}
    assert result.end_tick == 30  # both jobs take 30 ticks and run at the same time
    assert set(result.delivered) == {O1, O2}


def test_runs_are_deterministic(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, B, A, 0), _order(O3, A, C, 7)]
    scenario = _scenario(line_graph, {R1: A, R2: C}, orders)

    assert simulate(scenario, FirstComeFirstServed()).log == (
        simulate(scenario, FirstComeFirstServed()).log
    )


def test_policy_sees_current_robots_and_pending_orders(line_graph: WarehouseGraph) -> None:
    policy = Scripted([])
    orders = [_order(O1, B, C, 0), _order(O2, A, C, 4)]
    simulate(_scenario(line_graph, {R1: A, R2: B}, orders), policy)

    assert [(s.tick, [o.id for o in s.pending_orders]) for s in policy.seen] == [
        (0, [O1]),
        (4, [O1, O2]),
    ]
    assert [r.robot_id for r in policy.seen[0].idle_robots] == [R1, R2]


def test_unassigned_orders_are_reported_not_dropped(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, A, C, 4)]
    result = simulate(_scenario(line_graph, {R1: A}, orders), Scripted([]))

    assert result.pending == (O1, O2)
    assert result.delivered == ()
    assert not result.all_delivered
    assert result.end_tick == 4


def test_max_tick_stops_the_run_and_reports_unfinished_orders(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, A, C, 20)]
    result = simulate(_scenario(line_graph, {R1: A}, orders), FirstComeFirstServed(), max_tick=12)

    assert result.end_tick == 10  # the last event handled: arrival at the pickup
    assert result.in_progress == (O1,)
    assert result.unreleased == (O2,)
    assert result.robots[0].phase is RobotPhase.LOADING


def test_negative_max_tick_is_rejected(line_graph: WarehouseGraph) -> None:
    with pytest.raises(ValueError, match="max_tick"):
        simulate(_scenario(line_graph, {R1: A}, []), FirstComeFirstServed(), max_tick=-1)


def test_scenario_without_orders_ends_at_tick_zero(line_graph: WarehouseGraph) -> None:
    result = simulate(_scenario(line_graph, {R1: A}, []), FirstComeFirstServed())

    assert (result.end_tick, result.log, result.all_delivered) == (0, (), True)


# --- invalid decisions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("assignments", "message"),
    [
        ([Assignment(RobotId("R9"), O1)], "robot 'R9' is not an idle robot"),
        ([Assignment(R1, O1), Assignment(R1, O2)], "robot 'R1' is not an idle robot"),
        ([Assignment(R1, O3)], "order 'O3' is not a pending order"),
        ([Assignment(R1, O1), Assignment(R2, O1)], "order 'O1' is not a pending order"),
    ],
)
def test_invalid_assignments_are_rejected(
    line_graph: WarehouseGraph, assignments: list[Assignment], message: str
) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, A, C, 0)]
    scenario = _scenario(line_graph, {R1: A, R2: C}, orders)

    with pytest.raises(InvalidAssignmentError, match=message):
        simulate(scenario, Scripted(assignments))


def test_busy_robot_cannot_take_a_second_order(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, A, C, 5)]
    always_r1 = Scripted([Assignment(R1, O1)])
    scenario = _scenario(line_graph, {R1: A, R2: C}, orders)

    with pytest.raises(InvalidAssignmentError, match="robot 'R1' is not an idle robot"):
        simulate(scenario, always_r1)  # at tick 5, R1 is still driving to O1's pickup


@pytest.mark.parametrize(
    ("order", "message"),
    [
        (_order(O1, C, B, 0), "cannot reach the pickup"),  # C is isolated
        (_order(O1, A, C, 0), "drop-off is unreachable"),
    ],
)
def test_unreachable_assignments_are_rejected(
    disconnected_graph: WarehouseGraph, order: TransportOrder, message: str
) -> None:
    scenario = _scenario(disconnected_graph, {R1: A}, [order])

    with pytest.raises(InvalidAssignmentError, match=message):
        simulate(scenario, FirstComeFirstServed())


# --- the invariant checks themselves ----------------------------------------------------------


def test_invariant_check_detects_a_robot_without_a_scheduled_event(
    line_graph: WarehouseGraph,
) -> None:
    order = _order(O1, B, C, 0)
    sim = _Simulation(_scenario(line_graph, {R1: A}, [order]), FirstComeFirstServed(), None)
    sim.released = 1
    sim.robots[R1] = RobotState.idle(R1, A).assign(order, Route((A, B), 1.0), tick=0, until_tick=10)

    with pytest.raises(SimulationInvariantError, match="no event scheduled"):
        sim._check_invariants()


def test_invariant_check_detects_a_lost_order(line_graph: WarehouseGraph) -> None:
    sim = _Simulation(
        _scenario(line_graph, {R1: A}, [_order(O1, B, C, 0)]), FirstComeFirstServed(), None
    )
    sim.released = 1  # released, but neither pending, carried nor delivered

    with pytest.raises(SimulationInvariantError, match="lost or counted twice"):
        sim._check_invariants()


def test_invariant_check_detects_a_wrong_phase_duration(line_graph: WarehouseGraph) -> None:
    order = _order(O1, B, C, 0)
    sim = _Simulation(_scenario(line_graph, {R1: A}, [order]), FirstComeFirstServed(), None)
    sim.released = 1
    sim._set(RobotState.idle(R1, A).assign(order, Route((A, B), 1.0), tick=0, until_tick=11))

    with pytest.raises(SimulationInvariantError, match="TO_PICKUP has the wrong duration"):
        sim._check_invariants()


def test_invariant_check_detects_an_order_carried_twice(line_graph: WarehouseGraph) -> None:
    order = _order(O1, B, C, 0)
    sim = _Simulation(_scenario(line_graph, {R1: A, R2: C}, [order]), FirstComeFirstServed(), None)
    sim.released = 1
    sim._set(RobotState.idle(R1, A).assign(order, Route((A, B), 1.0), tick=0, until_tick=10))
    sim._set(RobotState.idle(R2, C).assign(order, Route((C, B), 1.0), tick=0, until_tick=10))

    with pytest.raises(SimulationInvariantError, match="carried by two robots"):
        sim._check_invariants()


def test_invariant_check_detects_an_event_for_an_idle_robot(line_graph: WarehouseGraph) -> None:
    sim = _Simulation(_scenario(line_graph, {R1: A}, []), FirstComeFirstServed(), None)
    sim._scheduled[R1] = 10

    with pytest.raises(SimulationInvariantError, match="idle robot 'R1' has an event"):
        sim._check_invariants()


# --- decision rounds ---------------------------------------------------------------------------


def test_decision_rounds_delay_assignment_to_the_next_round(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, {R1: A}, [_order(O1, B, C, 3)])

    result = simulate(scenario, FirstComeFirstServed(), decision_interval_ticks=10)

    assert [(e.tick, e.kind.name) for e in result.log][:2] == [(3, "RELEASED"), (10, "ASSIGNED")]
    assert result.end_tick == 40  # the whole job shifts by the 7 ticks of waiting


def test_order_released_on_a_round_tick_is_assigned_at_once(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, {R1: A}, [_order(O1, B, C, 20)])

    result = simulate(scenario, FirstComeFirstServed(), decision_interval_ticks=10)

    assert result.log[1].tick == 20


def test_rounds_batch_orders_and_robots_into_one_decision(line_graph: WarehouseGraph) -> None:
    policy = Scripted([])
    orders = [_order(O1, B, C, 2), _order(O2, A, C, 5)]
    simulate(_scenario(line_graph, {R1: A, R2: B}, orders), policy, decision_interval_ticks=10)

    assert [(s.tick, len(s.pending_orders), len(s.idle_robots)) for s in policy.seen] == [
        (10, 2, 2)
    ]


def test_robot_freed_between_rounds_waits_for_the_next_round(line_graph: WarehouseGraph) -> None:
    orders = [_order(O1, B, C, 0), _order(O2, C, A, 0)]
    result = simulate(
        _scenario(line_graph, {R1: A}, orders), FirstComeFirstServed(), decision_interval_ticks=40
    )

    assigned = [(e.tick, e.order_id) for e in result.log if e.kind is EventKind.ASSIGNED]
    assert assigned == [(0, O1), (40, O2)]  # R1 is idle again at tick 30


def test_decision_interval_must_be_positive(line_graph: WarehouseGraph) -> None:
    with pytest.raises(ValueError, match="decision_interval_ticks"):
        simulate(
            _scenario(line_graph, {R1: A}, []), FirstComeFirstServed(), decision_interval_ticks=0
        )


# --- decision latency ---------------------------------------------------------------------


@pytest.mark.parametrize("traffic", [False, True])
def test_every_decision_is_timed_once(traffic: bool) -> None:
    class Counting:
        name = "counting-test"

        def __init__(self) -> None:
            self.calls = 0
            self.greedy = GreedyDispatch()

        def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
            self.calls += 1
            return self.greedy.assign(snapshot)

    graph = generate_grid_warehouse(PRESETS["small"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=3, duration_s=900), seed=1)
    policy = Counting()

    result = simulate(scenario, policy, traffic=traffic)

    assert policy.calls > 0
    assert len(result.decision_seconds) == policy.calls
    assert all(seconds >= 0 for seconds in result.decision_seconds)
