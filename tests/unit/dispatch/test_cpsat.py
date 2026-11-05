"""Tests for the CP-SAT plan model and the CP-SAT dispatch policy."""

import dataclasses
import random
from collections.abc import Callable

import pytest

from amr_optimizer.dispatch import (
    POLICIES,
    HorizonProblem,
    Objective,
    Plan,
    build_problem,
    insertion_plan,
    plan_cost,
    total_completion,
)
from amr_optimizer.dispatch.cpsat import CpSatDispatch, SolverSettings, SolveStatus, solve_plan
from amr_optimizer.domain import (
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import Assignment, DispatchSnapshot, TravelPlanner, simulate
from amr_optimizer.warehouse import PRESETS, NodeKind, generate_grid_warehouse

BruteForce = Callable[[HorizonProblem], tuple[int, Plan]]
RandomProblem = Callable[..., HorizonProblem]
MEDIUM = generate_grid_warehouse(PRESETS["medium"])


def _map_problem(rng: random.Random, robots: int, orders: int) -> HorizonProblem:
    """A planning problem with real travel times: idle robots at random endpoints of the
    medium map, retrieval orders from random storage locations to random stations."""
    endpoints = sorted(n.id for n in MEDIUM.nodes if n.kind is not NodeKind.INTERSECTION)
    storage = sorted(n.id for n in MEDIUM.nodes_of_kind(NodeKind.STORAGE))
    stations = sorted(n.id for n in MEDIUM.nodes_of_kind(NodeKind.STATION))
    snapshot = DispatchSnapshot(
        0,
        tuple(
            RobotState.idle(RobotId(f"R{i}"), n)
            for i, n in enumerate(rng.sample(endpoints, robots))
        ),
        tuple(
            TransportOrder(OrderId(f"O{j}"), rng.choice(storage), rng.choice(stations), 0)
            for j in range(orders)
        ),
        TravelPlanner(TravelTimes(MEDIUM, speed_mps=1.0, time_model=TimeModel(0.1))),
        load_ticks=100,
        unload_ticks=100,
    )
    return build_problem(snapshot, max_orders=orders)


# --- the model -----------------------------------------------------------------------------------


def test_cpsat_finds_the_optimum_where_insertion_is_myopic(myopic_problem: HorizonProblem) -> None:
    outcome = solve_plan(myopic_problem, insertion_plan(myopic_problem))

    assert outcome.status is SolveStatus.OPTIMAL
    assert outcome.plan == ((1,), (0,))
    assert (outcome.objective, outcome.hint_objective, outcome.bound) == (41, 100, 41)


def test_cpsat_matches_the_brute_force_optimum(
    make_problem: RandomProblem, brute_force: BruteForce
) -> None:
    improved = 0
    for seed in range(250):
        rng = random.Random(seed)
        problem = make_problem(rng, rng.randint(1, 3), rng.randint(2, 5))

        outcome = solve_plan(problem, insertion_plan(problem))
        best, _ = brute_force(problem)

        assert outcome.status is SolveStatus.OPTIMAL
        assert outcome.objective == best == outcome.bound
        assert total_completion(problem, outcome.plan) == best
        improved += outcome.objective < outcome.hint_objective

    assert improved > 0  # some problems where insertion is not optimal


def test_cpsat_matches_the_brute_force_optimum_on_map_problems(brute_force: BruteForce) -> None:
    for seed in range(50):
        rng = random.Random(seed)
        problem = _map_problem(rng, rng.randint(1, 3), rng.randint(2, 5))

        outcome = solve_plan(problem, insertion_plan(problem))

        assert outcome.status is SolveStatus.OPTIMAL
        assert outcome.objective == brute_force(problem)[0]


def test_cpsat_matches_the_brute_force_optimum_worst_first(
    make_problem: RandomProblem, brute_force: BruteForce
) -> None:
    for seed in range(150):
        rng = random.Random(seed)
        problem = make_problem(
            rng, rng.randint(1, 3), rng.randint(2, 5), objective=Objective.WORST_FIRST
        )

        outcome = solve_plan(problem, insertion_plan(problem))
        best, _ = brute_force(problem)

        assert outcome.status is SolveStatus.OPTIMAL
        assert outcome.objective == best == outcome.bound == plan_cost(problem, outcome.plan)


def test_cpsat_follows_the_objective(fairness_problem: HorizonProblem) -> None:
    fair = dataclasses.replace(fairness_problem, objective=Objective.WORST_FIRST)
    # Start from the other objective's plan, so that CP-SAT has to change it.
    total = solve_plan(fairness_problem, ((0, 1),))
    worst_first = solve_plan(fair, ((1, 0),))

    assert (total.status, total.plan, total.objective) == (SolveStatus.OPTIMAL, ((1, 0),), 80)
    assert (worst_first.status, worst_first.plan) == (SolveStatus.OPTIMAL, ((0, 1),))


@pytest.mark.parametrize("orders", [0, 1])
def test_one_order_or_none_needs_no_search(make_problem: RandomProblem, orders: int) -> None:
    problem = make_problem(random.Random(orders), 3, orders)
    hint = insertion_plan(problem)

    outcome = solve_plan(problem, hint)

    assert outcome.status is SolveStatus.TRIVIAL
    assert outcome.plan == hint
    assert outcome.objective == outcome.hint_objective == outcome.bound


def test_solves_are_reproducible(make_problem: RandomProblem) -> None:
    problem = make_problem(random.Random(7), 3, 5)
    hint = insertion_plan(problem)

    first, second = solve_plan(problem, hint), solve_plan(problem, hint)

    assert (first.plan, first.status, first.objective, first.bound) == (
        second.plan,
        second.status,
        second.objective,
        second.bound,
    )


def test_a_tiny_time_limit_still_gives_a_valid_plan_no_worse_than_insertion() -> None:
    problem = _map_problem(random.Random(3), 10, 10)
    hint = insertion_plan(problem)

    outcome = solve_plan(problem, hint, SolverSettings(max_deterministic_time=1e-9))

    assert outcome.status in (SolveStatus.LIMIT, SolveStatus.FALLBACK)
    assert total_completion(problem, outcome.plan) == outcome.objective
    assert outcome.bound <= outcome.objective <= outcome.hint_objective


def test_settings_are_validated() -> None:
    with pytest.raises(ValueError, match="max_deterministic_time"):
        SolverSettings(max_deterministic_time=0)
    with pytest.raises(ValueError, match="max_orders"):
        CpSatDispatch(max_orders=0)


# --- the policy ----------------------------------------------------------------------------------


def test_cpsat_policy_commits_the_look_ahead_choice(look_ahead_snapshot: DispatchSnapshot) -> None:
    policy = CpSatDispatch()

    assert list(policy.assign(look_ahead_snapshot)) == [Assignment(RobotId("R1"), OrderId("O3"))]
    assert [outcome.status for outcome in policy.outcomes] == [SolveStatus.OPTIMAL]
    assert policy.outcomes[0].objective == 136


def test_the_policies_are_registered_by_objective() -> None:
    plain, fair = POLICIES["cpsat"](), POLICIES["cpsat-fair"]()

    assert isinstance(plain, CpSatDispatch) and isinstance(fair, CpSatDispatch)
    assert (plain.name, plain.objective) == ("cpsat", Objective.TOTAL)
    assert (fair.name, fair.objective) == ("cpsat-fair", Objective.WORST_FIRST)


@pytest.mark.parametrize("objective", list(Objective))
@pytest.mark.parametrize("interval", [None, 50])
def test_every_order_of_a_generated_scenario_is_delivered(
    interval: int | None, objective: Objective
) -> None:
    spec = ScenarioSpec(robots=3, orders_per_hour=120, duration_s=1800)  # queues form
    scenario = generate_scenario(MEDIUM, spec, seed=2)
    first, second = CpSatDispatch(objective=objective), CpSatDispatch(objective=objective)

    result = simulate(scenario, first, decision_interval_ticks=interval)
    again = simulate(scenario, second, decision_interval_ticks=interval)

    assert result.all_delivered
    assert result.log == again.log  # reproducible, solver included
    assert any(o.status is not SolveStatus.TRIVIAL for o in first.outcomes)
    for outcome in first.outcomes:
        assert outcome.bound <= outcome.objective <= outcome.hint_objective
