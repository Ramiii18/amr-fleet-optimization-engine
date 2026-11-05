"""Tests for cheapest-insertion planning and the insertion dispatch policy."""

import dataclasses
import random
from collections.abc import Callable, Sequence

import pytest

from amr_optimizer.dispatch import (
    POLICIES,
    GreedyDispatch,
    HorizonProblem,
    InsertionDispatch,
    Objective,
    Plan,
    build_problem,
    completion_offsets,
    first_jobs,
    insertion_plan,
    plan_cost,
    sequence_total,
    total_completion,
)
from amr_optimizer.domain import OrderId, RobotId
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import Assignment, DispatchSnapshot, EventKind, simulate
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

BruteForce = Callable[[HorizonProblem], tuple[int, Plan]]
RandomProblem = Callable[..., HorizonProblem]
R1, R2 = RobotId("R1"), RobotId("R2")
O2, O3 = OrderId("O2"), OrderId("O3")


# --- the plan ------------------------------------------------------------------------------------


def test_insertion_finds_the_look_ahead_plan(look_ahead_snapshot: DispatchSnapshot) -> None:
    problem = build_problem(look_ahead_snapshot, max_orders=10)

    plan = insertion_plan(problem)

    assert plan == ((1,), (0,))  # R1 takes O3; O2 waits for the busy R2
    assert total_completion(problem, plan) == 136
    assert list(InsertionDispatch().assign(look_ahead_snapshot)) == [Assignment(R1, O3)]
    assert list(GreedyDispatch().assign(look_ahead_snapshot)) == [Assignment(R1, O2)]


def test_insertion_is_myopic(myopic_problem: HorizonProblem, brute_force: BruteForce) -> None:
    # O0, the older order, is inserted first and takes R1 (20 < 21). O1 then either follows
    # it on R1 or goes to the distant R2: 100 in total either way, against 41 for the optimum.
    plan = insertion_plan(myopic_problem)

    assert plan == ((1, 0), ())  # a tie between O1 first and O1 second: the earlier position
    assert total_completion(myopic_problem, plan) == 100
    assert brute_force(myopic_problem) == (41, ((1,), (0,)))


def test_sequence_total_agrees_with_the_evaluator(
    make_problem: RandomProblem, enumerate_plans: Callable[[HorizonProblem], list[Plan]]
) -> None:
    feasible = infeasible = 0
    for seed in range(30):
        problem = make_problem(random.Random(seed), 3, 4)
        for plan in enumerate_plans(problem):
            totals = [sequence_total(problem, r, sequence) for r, sequence in enumerate(plan)]
            if None in totals:
                infeasible += 1
                continue
            feasible += 1
            assert sum(t for t in totals if t is not None) == total_completion(problem, plan)

    assert feasible > 0 and infeasible > 0


def test_insertion_returns_a_valid_plan_that_never_beats_the_optimum(
    make_problem: RandomProblem, brute_force: BruteForce
) -> None:
    optimal = 0
    for seed in range(300):
        rng = random.Random(seed)
        problem = make_problem(rng, rng.randint(1, 3), rng.randint(0, 5))

        total = total_completion(problem, insertion_plan(problem))  # raises if invalid
        best, _ = brute_force(problem)

        assert total >= best
        optimal += total == best

    assert 0 < optimal < 300  # both outcomes occur, so the comparison is not vacuous


def test_insertion_follows_the_objective(fairness_problem: HorizonProblem) -> None:
    fair = dataclasses.replace(fairness_problem, objective=Objective.WORST_FIRST)

    assert insertion_plan(fairness_problem) == ((1, 0),)  # the near order first: total 80
    assert insertion_plan(fair) == ((0, 1),)  # the order that has waited first: lead 160


def test_worst_first_insertion_never_beats_the_optimum(
    make_problem: RandomProblem, brute_force: BruteForce
) -> None:
    optimal = 0
    for seed in range(200):
        rng = random.Random(seed)
        problem = make_problem(
            rng, rng.randint(1, 3), rng.randint(0, 5), objective=Objective.WORST_FIRST
        )

        cost = plan_cost(problem, insertion_plan(problem))
        best, _ = brute_force(problem)

        assert cost >= best
        optimal += cost == best

    assert 0 < optimal < 200


# --- the policy ----------------------------------------------------------------------------------


def test_no_idle_robot_or_no_pending_order_means_no_assignments(
    look_ahead_snapshot: DispatchSnapshot,
) -> None:
    busy_only = DispatchSnapshot(
        look_ahead_snapshot.tick,
        look_ahead_snapshot.robots[1:],
        look_ahead_snapshot.pending_orders,
        look_ahead_snapshot.planner,
        load_ticks=5,
        unload_ticks=7,
    )
    nothing_pending = DispatchSnapshot(
        look_ahead_snapshot.tick,
        look_ahead_snapshot.robots,
        (),
        look_ahead_snapshot.planner,
        load_ticks=5,
        unload_ticks=7,
    )

    assert list(InsertionDispatch().assign(busy_only)) == []
    assert list(InsertionDispatch().assign(nothing_pending)) == []


def test_the_window_limits_the_planned_orders(look_ahead_snapshot: DispatchSnapshot) -> None:
    # With only the oldest order in the window, the busy R2 is still the better choice for
    # it (84 < 92 ticks), so the idle R1 waits.
    assert list(InsertionDispatch(max_orders=1).assign(look_ahead_snapshot)) == []
    with pytest.raises(ValueError, match="max_orders"):
        InsertionDispatch(max_orders=0)


def test_the_policies_are_registered_by_objective() -> None:
    plain, fair = POLICIES["insertion"](), POLICIES["insertion-fair"]()

    assert isinstance(plain, InsertionDispatch) and isinstance(fair, InsertionDispatch)
    assert (plain.name, plain.objective) == ("insertion", Objective.TOTAL)
    assert (fair.name, fair.objective) == ("insertion-fair", Objective.WORST_FIRST)


class _Predicting:
    """Insertion dispatch that records the planned delivery tick of every committed job."""

    name = "predicting"

    def __init__(self) -> None:
        self.predicted: dict[OrderId, int] = {}

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        if not snapshot.idle_robots or not snapshot.pending_orders:
            return []
        problem = build_problem(snapshot, max_orders=10)
        plan = insertion_plan(problem)
        offsets = completion_offsets(problem, plan)
        jobs = first_jobs(problem, plan)
        index = {order.id: j for j, order in enumerate(problem.orders)}
        for job in jobs:
            self.predicted[job.order_id] = snapshot.tick + offsets[index[job.order_id]]
        return jobs


def test_committed_jobs_are_delivered_exactly_when_planned() -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=4, orders_per_hour=150), seed=5)
    policy = _Predicting()

    result = simulate(scenario, policy)

    delivered = {e.order_id: e.tick for e in result.log if e.kind is EventKind.DELIVERED}
    assert result.all_delivered
    assert policy.predicted == delivered  # every order, at exactly the planned tick


@pytest.mark.parametrize("objective", list(Objective))
@pytest.mark.parametrize("interval", [None, 50])
def test_every_order_of_a_generated_scenario_is_delivered(
    interval: int | None, objective: Objective
) -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=5, duration_s=1800), seed=2)

    first = simulate(
        scenario, InsertionDispatch(objective=objective), decision_interval_ticks=interval
    )
    second = simulate(
        scenario, InsertionDispatch(objective=objective), decision_interval_ticks=interval
    )

    assert first.all_delivered
    assert first.log == second.log  # deterministic
