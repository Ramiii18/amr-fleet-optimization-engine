"""Tests for the greedy and Hungarian dispatch policies."""

import random

import pytest

from amr_optimizer.dispatch import GreedyDispatch, HungarianDispatch
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
    DispatchPolicy,
    DispatchSnapshot,
    TravelPlanner,
    simulate,
)
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

POLICIES: list[DispatchPolicy] = [GreedyDispatch(), HungarianDispatch()]
N = [NodeId(f"N{i}") for i in range(5)]
R1, R2, R3 = RobotId("R1"), RobotId("R2"), RobotId("R3")
O1, O2, O3 = OrderId("O1"), OrderId("O2"), OrderId("O3")


def _line(length: int = 5) -> WarehouseGraph:
    """N0 <-> N1 <-> ... <-> N4, 1 m apart: 10 ticks per edge at 1 m/s and 0.1 s ticks."""
    nodes = [Node(N[i], float(i), 0.0) for i in range(length)]
    edges = [Edge(N[i], N[j], 1.0) for i in range(length - 1) for i, j in ((i, i + 1), (i + 1, i))]
    return WarehouseGraph(nodes, edges)


def _planner(graph: WarehouseGraph) -> TravelPlanner:
    return TravelPlanner(TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))


def _snapshot(
    graph: WarehouseGraph, robots: dict[RobotId, NodeId], orders: list[TransportOrder]
) -> DispatchSnapshot:
    states = tuple(RobotState.idle(rid, node) for rid, node in robots.items())
    return DispatchSnapshot(0, states, tuple(orders), _planner(graph), load_ticks=0, unload_ticks=0)


def _pickup_ticks(snapshot: DispatchSnapshot, assignments: list[Assignment]) -> int:
    robots = {r.robot_id: r for r in snapshot.robots}
    orders = {o.id: o for o in snapshot.pending_orders}
    total = 0
    for a in assignments:
        ticks = snapshot.planner.ticks(robots[a.robot_id].node, orders[a.order_id].pickup)
        assert ticks is not None
        total += ticks
    return total


# --- the case where greedy is myopic -------------------------------------------------------

# R1 at N1, R2 at N4. The older order O1 picks up at N2, the newer O2 at N0.
# Greedy gives O1 its nearest robot R1 (1 edge), so O2 gets R2 (4 edges): 5 edges in total.
# The optimal matching sends R2 to O1 (2 edges) and R1 to O2 (1 edge): 3 edges.
MYOPIC_ROBOTS = {R1: N[1], R2: N[4]}
MYOPIC_ORDERS = [TransportOrder(O1, N[2], N[3], 0), TransportOrder(O2, N[0], N[3], 0)]


def test_greedy_gives_the_oldest_order_its_nearest_robot() -> None:
    snapshot = _snapshot(_line(), MYOPIC_ROBOTS, MYOPIC_ORDERS)

    assignments = list(GreedyDispatch().assign(snapshot))

    assert assignments == [Assignment(R1, O1), Assignment(R2, O2)]
    assert _pickup_ticks(snapshot, assignments) == 50


def test_hungarian_minimises_total_travel_to_the_pickups() -> None:
    snapshot = _snapshot(_line(), MYOPIC_ROBOTS, MYOPIC_ORDERS)

    assignments = list(HungarianDispatch().assign(snapshot))

    assert assignments == [Assignment(R2, O1), Assignment(R1, O2)]  # listed oldest order first
    assert _pickup_ticks(snapshot, assignments) == 30


# --- behaviour shared by both policies -------------------------------------------------------


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_spare_robots_stay_idle(policy: DispatchPolicy) -> None:
    snapshot = _snapshot(_line(), {R1: N[0], R2: N[3], R3: N[4]}, [MYOPIC_ORDERS[0]])

    assert list(policy.assign(snapshot)) == [Assignment(R2, O1)]  # N3 is nearest to N2


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_oldest_orders_are_served_first(policy: DispatchPolicy) -> None:
    # One robot at N0, three orders: the oldest is farthest away, and is still served first.
    orders = [
        TransportOrder(O1, N[4], N[3], 0),
        TransportOrder(O2, N[1], N[2], 1),
        TransportOrder(O3, N[0], N[2], 2),
    ]
    snapshot = _snapshot(_line(), {R1: N[0]}, orders)

    assert list(policy.assign(snapshot)) == [Assignment(R1, O1)]


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_busy_robots_are_not_assigned(policy: DispatchPolicy) -> None:
    graph = _line()
    busy = RobotState.idle(R1, N[1]).assign(
        MYOPIC_ORDERS[0], Route((N[1], N[2]), 1.0), tick=0, until_tick=10
    )
    idle = RobotState.idle(R2, N[4])
    snapshot = DispatchSnapshot(
        0, (busy, idle), (MYOPIC_ORDERS[1],), _planner(graph), load_ticks=0, unload_ticks=0
    )

    assert list(policy.assign(snapshot)) == [Assignment(R2, O2)]


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_no_idle_robots_means_no_assignments(policy: DispatchPolicy) -> None:
    graph = _line()
    busy = RobotState.idle(R1, N[1]).assign(
        MYOPIC_ORDERS[0], Route((N[1], N[2]), 1.0), tick=0, until_tick=10
    )
    snapshot = DispatchSnapshot(
        0, (busy,), (MYOPIC_ORDERS[1],), _planner(graph), load_ticks=0, unload_ticks=0
    )

    assert list(policy.assign(snapshot)) == []


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_unreachable_orders_and_robots_are_skipped(
    policy: DispatchPolicy, disconnected_graph: WarehouseGraph
) -> None:
    a, b, c = NodeId("A"), NodeId("B"), NodeId("C")  # A -> B one-way; C isolated
    orders = [
        TransportOrder(O1, b, a, 0),  # drop-off unreachable from the pickup
        TransportOrder(O2, a, b, 0),  # serviceable, but only from A
    ]
    snapshot = _snapshot(disconnected_graph, {R1: c, R2: a}, orders)

    assert list(policy.assign(snapshot)) == [Assignment(R2, O2)]


# --- properties on random decisions ------------------------------------------------------------


def test_hungarian_never_needs_more_travel_than_greedy_for_the_same_orders() -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    endpoints = sorted(n.id for n in graph.nodes if n.kind is not NodeKind.INTERSECTION)
    storage = sorted(n.id for n in graph.nodes_of_kind(NodeKind.STORAGE))
    stations = sorted(n.id for n in graph.nodes_of_kind(NodeKind.STATION))
    planner = _planner(graph)
    strictly_better = 0

    for seed in range(200):
        rng = random.Random(seed)
        robots = tuple(
            RobotState.idle(RobotId(f"R{i}"), node)
            for i, node in enumerate(rng.sample(endpoints, rng.randint(1, 6)))
        )
        orders = tuple(
            TransportOrder(OrderId(f"O{i}"), rng.choice(storage), rng.choice(stations), 0)
            for i in range(rng.randint(1, 6))
        )
        snapshot = DispatchSnapshot(0, robots, orders, planner, load_ticks=0, unload_ticks=0)
        greedy = list(GreedyDispatch().assign(snapshot))
        hungarian = list(HungarianDispatch().assign(snapshot))

        assert {a.order_id for a in greedy} == {a.order_id for a in hungarian}
        assert _pickup_ticks(snapshot, hungarian) <= _pickup_ticks(snapshot, greedy)
        strictly_better += _pickup_ticks(snapshot, hungarian) < _pickup_ticks(snapshot, greedy)

    assert strictly_better > 0  # the comparison is not vacuous


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
@pytest.mark.parametrize("interval", [None, 50])
def test_policies_deliver_every_order_of_a_generated_scenario(
    policy: DispatchPolicy, interval: int | None
) -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=5, duration_s=1800), seed=2)

    result = simulate(scenario, policy, decision_interval_ticks=interval)

    assert result.all_delivered
    assert result.policy_name == policy.name
