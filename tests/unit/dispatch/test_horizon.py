"""Tests for the planning model: availability, problems, plan evaluation and commitment.

``_line()``: N0 <-> N1 <-> ... <-> N4, 1 m apart, so 10 ticks per edge at 1 m/s and 0.1 s
ticks. Unless stated otherwise, loading takes 5 ticks and unloading 7.
"""

import dataclasses
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from amr_optimizer.dispatch import (
    GreedyDispatch,
    HorizonProblem,
    InvalidPlanError,
    Objective,
    Plan,
    PlanningProblemError,
    RobotAvailability,
    availability,
    build_problem,
    completion_offsets,
    completion_upper_bound,
    first_jobs,
    plan_cost,
    total_completion,
    worst_lead,
    worst_weight,
)
from amr_optimizer.domain import (
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.routing import Route
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import (
    Assignment,
    DispatchSnapshot,
    EventKind,
    TravelPlanner,
    simulate,
)
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    WarehouseGraph,
    generate_grid_warehouse,
)

N = [NodeId(f"N{i}") for i in range(5)]
R1, R2 = RobotId("R1"), RobotId("R2")
O1, O2, O3, O4 = OrderId("O1"), OrderId("O2"), OrderId("O3"), OrderId("O4")
LOAD, UNLOAD = 5, 7

BruteForce = Callable[[HorizonProblem], tuple[int, Plan]]
RandomProblem = Callable[..., HorizonProblem]


def _line() -> WarehouseGraph:
    nodes = [Node(N[i], float(i), 0.0) for i in range(5)]
    edges = [Edge(N[i], N[j], 1.0) for i in range(4) for i, j in ((i, i + 1), (i + 1, i))]
    return WarehouseGraph(nodes, edges)


def _snapshot(
    tick: int,
    robots: Sequence[RobotState],
    orders: Sequence[TransportOrder],
    graph: WarehouseGraph | None = None,
) -> DispatchSnapshot:
    travel_times = TravelTimes(graph or _line(), speed_mps=1.0, time_model=TimeModel(0.1))
    return DispatchSnapshot(
        tick,
        tuple(robots),
        tuple(orders),
        TravelPlanner(travel_times),
        load_ticks=LOAD,
        unload_ticks=UNLOAD,
    )


# --- availability ------------------------------------------------------------------------------

# R2 carries O1 from N1 to N3 (a 20-tick loaded trip), starting from N0 at tick 0.
JOB = TransportOrder(O1, N[1], N[3], 0)
TO_PICKUP = RobotState.idle(R2, N[0]).assign(JOB, Route((N[0], N[1]), 1.0), tick=0, until_tick=10)
LOADING = TO_PICKUP.arrive(tick=10, until_tick=10 + LOAD)
TO_DROPOFF = LOADING.finish_loading(Route((N[1], N[2], N[3]), 2.0), tick=15, until_tick=35)
UNLOADING = TO_DROPOFF.arrive(tick=35, until_tick=35 + UNLOAD)


@pytest.mark.parametrize(
    ("state", "tick", "ready"),
    [
        (TO_PICKUP, 4, 6 + LOAD + 20 + UNLOAD),  # rest of the trip, load, loaded trip, unload
        (LOADING, 12, 3 + 20 + UNLOAD),
        (TO_DROPOFF, 20, 15 + UNLOAD),
        (UNLOADING, 40, 2),
    ],
    ids=["to-pickup", "loading", "to-dropoff", "unloading"],
)
def test_a_busy_robot_is_ready_when_it_delivers(state: RobotState, tick: int, ready: int) -> None:
    result = availability(state, _snapshot(tick, [state], []))

    assert result == RobotAvailability(R2, N[3], ready, idle=False)


def test_an_idle_robot_is_ready_now_where_it_stands() -> None:
    robot = RobotState.idle(R1, N[2], tick=3)

    assert availability(robot, _snapshot(9, [robot], [])) == RobotAvailability(R1, N[2], 0, True)


def test_an_idle_robot_with_a_planned_return_is_ready_then() -> None:
    # A robot charging out of service is idle, but it is planned from its return to service.
    robot = RobotState.idle(R1, N[4], tick=3)
    snapshot = dataclasses.replace(_snapshot(9, [robot], []), planned_ready={R1: (40, N[4])})

    assert availability(robot, snapshot) == RobotAvailability(R1, N[4], 31, idle=False)


class _Recorder:
    """Greedy dispatch that records, at every decision, where and when each busy robot is
    predicted to deliver its order."""

    name = "recorder"

    def __init__(self) -> None:
        self.greedy = GreedyDispatch()
        self.predicted: dict[OrderId, set[tuple[int, NodeId]]] = defaultdict(set)

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        for robot in snapshot.robots:
            if robot.order is not None:
                ready = availability(robot, snapshot)
                self.predicted[robot.order.id].add((snapshot.tick + ready.ready, ready.node))
        return self.greedy.assign(snapshot)


def test_predicted_availability_matches_the_simulation() -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    spec = ScenarioSpec(robots=4, orders_per_hour=150, duration_s=1800)
    recorder = _Recorder()

    result = simulate(generate_scenario(graph, spec, seed=3), recorder)

    delivered = {e.order_id: (e.tick, e.node) for e in result.log if e.kind is EventKind.DELIVERED}
    assert len(recorder.predicted) > 20  # many predictions, made in every busy phase
    for order_id, predictions in recorder.predicted.items():
        assert predictions == {delivered[order_id]}


# --- the planning problem ----------------------------------------------------------------------

# At tick 20: R1 is idle at N0. R2 is driving O1 to N3 and will be ready there at tick 42.
# Pending: O2 (N4 -> N0, older) and O3 (N2 -> N4).
EXAMPLE_ORDERS = [TransportOrder(O2, N[4], N[0], 1), TransportOrder(O3, N[2], N[4], 2)]


def _example() -> HorizonProblem:
    snapshot = _snapshot(20, [RobotState.idle(R1, N[0]), TO_DROPOFF], EXAMPLE_ORDERS)
    return build_problem(snapshot, max_orders=10)


def test_build_problem_computes_every_time_from_the_map() -> None:
    problem = _example()

    assert problem.tick == 20
    assert problem.robots == (
        RobotAvailability(R1, N[0], 0, idle=True),
        RobotAvailability(R2, N[3], 22, idle=False),
    )
    assert [o.id for o in problem.orders] == [O2, O3]
    assert problem.job_ticks == (LOAD + 40 + UNLOAD, LOAD + 20 + UNLOAD)
    assert problem.start_ticks == ((40, 20), (10, 10))  # from N0 and from N3, to N4 and N2
    assert problem.setup_ticks == ((0, 20), (0, 0))  # N0 -> N2 = 20; N4 -> N4 = 0


def test_build_problem_records_how_long_each_order_has_waited() -> None:
    snapshot = _snapshot(20, [RobotState.idle(R1, N[0]), TO_DROPOFF], EXAMPLE_ORDERS)

    problem = build_problem(snapshot, max_orders=10)
    fair = build_problem(snapshot, max_orders=10, objective=Objective.WORST_FIRST)

    assert problem.waited == (19, 18)  # released at ticks 1 and 2, planned at tick 20
    assert (problem.objective, fair.objective) == (Objective.TOTAL, Objective.WORST_FIRST)


def test_build_problem_keeps_the_oldest_orders() -> None:
    snapshot = _snapshot(20, [RobotState.idle(R1, N[0]), TO_DROPOFF], EXAMPLE_ORDERS)

    assert [o.id for o in build_problem(snapshot, max_orders=1).orders] == [O2]
    with pytest.raises(ValueError, match="max_orders"):
        build_problem(snapshot, max_orders=0)


def test_build_problem_skips_orders_that_cannot_be_planned(
    disconnected_graph: WarehouseGraph,
) -> None:
    a, b = NodeId("A"), NodeId("B")  # A -> B is the only edge
    orders = [
        TransportOrder(O1, b, a, 0),  # the drop-off cannot be reached from the pickup
        TransportOrder(O2, a, b, 0),  # plannable
        TransportOrder(O3, a, b, 0),  # no trip from O2's drop-off (B) to this pickup (A)
    ]
    at_a = build_problem(
        _snapshot(0, [RobotState.idle(R1, a)], orders, disconnected_graph), max_orders=5
    )
    at_b = build_problem(
        _snapshot(0, [RobotState.idle(R1, b)], orders, disconnected_graph), max_orders=5
    )

    assert [o.id for o in at_a.orders] == [O2]
    assert at_b.orders == ()  # no robot can reach pickup A


# --- evaluating plans --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "offsets"),
    [
        (((1,), (0,)), (22 + 10 + 52, 20 + 32)),  # the optimum: O2 waits for busy R2
        (((0,), (1,)), (40 + 52, 22 + 10 + 32)),  # what greedy does: idle R1 takes the oldest
        (((1, 0), ()), (52 + 0 + 52, 52)),
        (((0, 1), ()), (92, 92 + 20 + 32)),
        (((), (1, 0)), (64 + 0 + 52, 64)),
        (((), (0, 1)), (84, 84 + 20 + 32)),
    ],
)
def test_completion_times_follow_each_robots_sequence(plan: Plan, offsets: tuple[int, int]) -> None:
    problem = _example()

    assert completion_offsets(problem, plan) == offsets
    assert total_completion(problem, plan) == sum(offsets)


def test_the_brute_force_oracle_finds_the_look_ahead_optimum(brute_force: BruteForce) -> None:
    assert brute_force(_example()) == (136, ((1,), (0,)))


def test_the_oracle_enumerates_every_plan_once(
    enumerate_plans: Callable[[HorizonProblem], list[Plan]],
) -> None:
    plans = enumerate_plans(_example())

    assert len(plans) == len(set(plans)) == 2 * 3  # 2! orders x C(3, 1) ways to cut them


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (((1,),), "one sequence per robot"),
        (((1,), ()), "exactly once"),
        (((1, 0), (0,)), "exactly once"),
        (((2,), (0, 1)), "exactly once"),
    ],
)
def test_malformed_plans_are_rejected(plan: Plan, message: str) -> None:
    with pytest.raises(InvalidPlanError, match=message):
        completion_offsets(_example(), plan)


def test_a_plan_with_an_unreachable_first_job_is_rejected() -> None:
    problem = dataclasses.replace(_example(), start_ticks=((None, 20), (10, 10)))

    with pytest.raises(InvalidPlanError, match="cannot reach the pickup of 'O2'"):
        completion_offsets(problem, ((0,), (1,)))
    assert total_completion(problem, ((1,), (0,))) == 136  # the other way round still works


def test_lead_times_and_costs_under_both_objectives(fairness_problem: HorizonProblem) -> None:
    near_first, waited_first = ((1, 0),), ((0, 1),)  # B then A, or A then B
    fair = dataclasses.replace(fairness_problem, objective=Objective.WORST_FIRST)
    weight = 2 * 120 + 1  # n x upper bound + 1

    assert completion_upper_bound(fairness_problem) == 0 + 20 + 50 + 50  # ready, jobs, approaches
    assert (
        worst_lead(fairness_problem, near_first),
        worst_lead(fairness_problem, waited_first),
    ) == (
        100 + 70,
        max(100 + 60, 0 + 120),
    )
    assert plan_cost(fairness_problem, near_first) == 80  # total only
    assert plan_cost(fairness_problem, waited_first) == 180
    assert worst_weight(fairness_problem) == 0
    assert worst_weight(fair) == weight
    assert plan_cost(fair, near_first) == weight * 170 + 80
    assert plan_cost(fair, waited_first) == weight * 160 + 180  # cheaper: the worst comes first


def test_the_oracle_follows_the_objective(
    fairness_problem: HorizonProblem, brute_force: BruteForce
) -> None:
    fair = dataclasses.replace(fairness_problem, objective=Objective.WORST_FIRST)

    assert brute_force(fairness_problem) == (80, ((1, 0),))
    assert brute_force(fair) == (241 * 160 + 180, ((0, 1),))


def test_the_worst_weight_orders_plans_lexicographically(
    make_problem: RandomProblem, enumerate_plans: Callable[[HorizonProblem], list[Plan]]
) -> None:
    for seed in range(20):
        problem = make_problem(random.Random(seed), 2, 3, objective=Objective.WORST_FIRST)
        ranked = []
        for plan in enumerate_plans(problem):
            try:
                key = (worst_lead(problem, plan), total_completion(problem, plan))
            except InvalidPlanError:
                continue
            ranked.append((plan_cost(problem, plan), key))

        costs_in_key_order = [cost for cost, _ in sorted(ranked, key=lambda pair: pair[1])]
        assert costs_in_key_order == sorted(costs_in_key_order)  # same order as (worst, total)
        assert len({cost for cost, _ in ranked}) == len({key for _, key in ranked})


# --- committing --------------------------------------------------------------------------------


def test_only_idle_robots_first_jobs_are_committed() -> None:
    problem = _example()

    assert first_jobs(problem, ((1,), (0,))) == [Assignment(R1, O3)]  # R2 is busy
    assert first_jobs(problem, ((1, 0), ())) == [Assignment(R1, O3)]  # O2 is for later
    assert first_jobs(problem, ((), (1, 0))) == []  # the idle robot is planned to wait


def test_committed_jobs_are_listed_oldest_order_first() -> None:
    both_idle = (RobotAvailability(R1, N[0], 0, True), RobotAvailability(R2, N[3], 0, True))
    problem = dataclasses.replace(_example(), robots=both_idle)

    assert first_jobs(problem, ((1,), (0,))) == [Assignment(R2, O2), Assignment(R1, O3)]


# --- validation ----------------------------------------------------------------------------------


def _valid(make_problem: RandomProblem) -> HorizonProblem:
    return make_problem(random.Random(0), 2, 2)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: {"robots": ()}, "at least one robot"),
        (lambda p: {"robots": (p.robots[0], p.robots[0])}, "robot ids"),
        (lambda p: {"orders": (p.orders[0], p.orders[0])}, "order ids"),
        (lambda p: {"job_ticks": (1,)}, "must match"),
        (lambda p: {"start_ticks": p.start_ticks[:1]}, "must match"),
        (lambda p: {"setup_ticks": ((0, 1),)}, "must match"),
        (lambda p: {"job_ticks": (1, 0)}, "at least one tick"),
        (lambda p: {"setup_ticks": ((0, -1), (2, 0))}, "non-negative"),
        (lambda p: {"start_ticks": ((-1, 1), (1, 1))}, "non-negative"),
        (lambda p: {"start_ticks": ((None, 1), (None, 1))}, "'O0': no robot can reach"),
        (lambda p: {"waited": (0,)}, "waiting times"),
        (lambda p: {"waited": (0, -1)}, "waiting times"),
    ],
)
def test_inconsistent_problems_are_rejected(
    make_problem: RandomProblem, change: Callable[[HorizonProblem], dict[str, Any]], message: str
) -> None:
    problem = _valid(make_problem)

    with pytest.raises(PlanningProblemError, match=message):
        dataclasses.replace(problem, **change(problem))


def test_inconsistent_availability_is_rejected() -> None:
    with pytest.raises(PlanningProblemError, match="< 0"):
        RobotAvailability(R1, N[0], -1, idle=False)
    with pytest.raises(PlanningProblemError, match="ready now"):
        RobotAvailability(R1, N[0], 5, idle=True)
