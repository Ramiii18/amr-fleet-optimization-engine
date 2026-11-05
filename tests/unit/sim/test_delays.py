"""Tests for executing plans under delays in the fleet simulator (Phase 2, D25)."""

from collections import defaultdict

import pytest

from amr_optimizer.dispatch import GreedyDispatch, InsertionDispatch, Objective
from amr_optimizer.domain import (
    InvalidTransitionError,
    OrderId,
    RobotId,
    RobotState,
    RobotStateError,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.routing import Route
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import ORDER_LIFECYCLE, EventKind, SimulationResult, simulate
from amr_optimizer.traffic import (
    FOREVER,
    NO_DELAYS,
    DelayModel,
    ExecutionPolicy,
    ResourceOrder,
    TimedPath,
    Visit,
    path_conflicts,
)
from amr_optimizer.warehouse import PRESETS, NodeId, generate_grid_warehouse

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
R1, R2 = RobotId("R1"), RobotId("R2")
MAPS = {name: generate_grid_warehouse(PRESETS[name]) for name in ("small", "medium")}
DELAYS = DelayModel(0.2, min_ticks=5, max_ticks=40)


# --- building blocks -----------------------------------------------------------------------------


def test_only_a_travelling_robot_can_be_postponed_and_only_to_later() -> None:
    order = TransportOrder(OrderId("O1"), B, C, 0)
    driving = RobotState.idle(R1, A).assign(order, Route((A, B), 1.0), tick=0, until_tick=10)

    assert driving.postpone(25).until_tick == 25
    assert driving.postpone(25).phase is driving.phase
    with pytest.raises(RobotStateError, match="cannot move the end"):
        driving.postpone(5)
    loading = driving.arrive(tick=10, until_tick=15)
    with pytest.raises(InvalidTransitionError, match="only a travelling robot"):
        loading.postpone(20)


def test_the_resource_order_lets_robots_in_by_planned_time() -> None:
    order = ResourceOrder()
    order.add(R1, TimedPath((Visit(A, 0, FOREVER),)), include_start=True)
    order.add(R2, TimedPath((Visit(C, 0, 3), Visit(B, 13, FOREVER))), include_start=True)
    order.add(R1, TimedPath((Visit(A, 0, 20), Visit(B, 30, FOREVER))), include_start=False)

    # B: R2 is planned there first (13), then R1 (30).
    assert order.earliest_arrival(B, R1) is None
    assert order.may_enter_corridor(A, B, R1)  # nobody else uses that corridor
    assert order.leave(C, R2, 3) is None
    assert order.earliest_arrival(B, R2) == 0  # nobody was at B before
    with pytest.raises(RuntimeError, match="out of the planned order"):
        order.leave(B, R1, 40)


# --- whole runs ------------------------------------------------------------------------------


def _run(
    map_name: str, robots: int, seed: int, policy: str, execution: ExecutionPolicy
) -> SimulationResult:
    spec = ScenarioSpec(robots=robots, orders_per_hour=150, duration_s=1200)
    scenario = generate_scenario(MAPS[map_name], spec, seed=seed)
    dispatch = (
        GreedyDispatch()
        if policy == "greedy"
        else InsertionDispatch(objective=Objective.WORST_FIRST)
    )
    return simulate(
        scenario, dispatch, traffic=True, delays=DELAYS, delay_seed=seed, execution=execution
    )


def _conflicts(result: SimulationResult) -> int:
    return len(path_conflicts({r: [p] for r, p in result.trajectories.items()}))


def _position(path: TimedPath, tick: int) -> NodeId | None:
    for visit in path.visits:
        if visit.arrive <= tick <= visit.depart:
            return visit.node
    return None


CASES = [(m, r, s, p) for m in MAPS for r in (3, 6) for s in range(3) for p in ("greedy", "fair")]


@pytest.mark.parametrize(("map_name", "robots", "seed", "policy"), CASES)
def test_ordered_execution_under_delays_is_complete_and_conflict_free(
    map_name: str, robots: int, seed: int, policy: str
) -> None:
    result = _run(map_name, robots, seed, policy, ExecutionPolicy.ORDERED)
    again = _run(map_name, robots, seed, policy, ExecutionPolicy.ORDERED)
    graph = result.scenario.graph
    times = TravelTimes(graph, speed_mps=result.scenario.speed_mps, time_model=TimeModel(0.1))

    assert result.all_delivered
    assert _conflicts(result) == 0
    assert (result.log, result.trajectories) == (again.log, again.trajectories)
    by_order: dict[OrderId, list[EventKind]] = defaultdict(list)
    for entry in result.log:
        by_order[entry.order_id].append(entry.kind)
        if entry.robot_id is not None:
            assert _position(result.trajectories[entry.robot_id], entry.tick) == entry.node
    assert all(tuple(kinds) == ORDER_LIFECYCLE for kinds in by_order.values())
    for path in result.trajectories.values():  # real edges, never faster than free flow
        for before, after in zip(path.visits, path.visits[1:], strict=False):
            assert after.arrive - before.depart >= times.edge_ticks(before.node, after.node)


def test_naive_execution_under_delays_causes_conflicts() -> None:
    total = sum(
        _conflicts(_run(m, r, s, p, ExecutionPolicy.NAIVE)) for m, r, s, p in CASES if r == 6
    )

    assert total > 0


def test_delays_slow_the_fleet_down() -> None:
    spec = ScenarioSpec(robots=6, orders_per_hour=150, duration_s=1200)
    scenario = generate_scenario(MAPS["medium"], spec, seed=1)

    plain = simulate(scenario, GreedyDispatch(), traffic=True)
    delayed = simulate(scenario, GreedyDispatch(), traffic=True, delays=DELAYS, delay_seed=1)

    def mean_completion(result: SimulationResult) -> float:
        release = {e.order_id: e.tick for e in result.log if e.kind is EventKind.RELEASED}
        done = [e.tick - release[e.order_id] for e in result.log if e.kind is EventKind.DELIVERED]
        return sum(done) / len(done)

    assert delayed.all_delivered
    assert mean_completion(delayed) > mean_completion(plain)


def test_delays_and_execution_policies_need_traffic() -> None:
    scenario = generate_scenario(MAPS["small"], ScenarioSpec(robots=2, duration_s=300), seed=0)

    with pytest.raises(ValueError, match="need traffic"):
        simulate(scenario, GreedyDispatch(), delays=DELAYS)
    with pytest.raises(ValueError, match="need traffic"):
        simulate(scenario, GreedyDispatch(), execution=ExecutionPolicy.NAIVE)
    assert simulate(scenario, GreedyDispatch(), delays=NO_DELAYS).all_delivered
