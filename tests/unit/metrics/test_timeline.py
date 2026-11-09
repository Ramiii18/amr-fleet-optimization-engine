"""Tests for per-order timelines: hand-computed runs, and agreement with the KPIs.

``line_graph``: A - B - C, 1 m apart. At 1 m/s and 0.1 s ticks an edge takes 1 s; loading
and unloading take 5 ticks (0.5 s) each.
"""

import statistics
from collections.abc import Sequence

import pytest

from amr_optimizer.dispatch import POLICIES, GreedyDispatch
from amr_optimizer.domain import OrderId, RobotId, RobotState, TimeModel, TransportOrder
from amr_optimizer.metrics import compute_kpis, order_timelines
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import Assignment, DispatchSnapshot, simulate
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
R1 = RobotId("R1")
O1, O2 = OrderId("O1"), OrderId("O2")


def _scenario(graph: WarehouseGraph, orders: list[TransportOrder]) -> Scenario:
    return Scenario(
        graph=graph,
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=5,
        unload_ticks=5,
        robots=(RobotState.idle(R1, A),),
        orders=tuple(orders),
    )


class Lazy:
    name = "lazy-test"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        return []


def test_two_queued_orders_step_by_step(line_graph: WarehouseGraph) -> None:
    # O1 (B -> C): R1 drives A -> B, loads, drives to C, unloads: delivered at 3.0 s.
    # O2 (C -> A), released at 0, waits for R1, which is already at C: assigned and at the
    # pickup at 3.0 s, loaded at 3.5 s, at A at 5.5 s, delivered at 6.0 s.
    orders = [TransportOrder(O1, B, C, 0), TransportOrder(O2, C, A, 0)]
    first, second = order_timelines(simulate(_scenario(line_graph, orders), GreedyDispatch()))

    assert (first.order_id, first.robot_id, first.pickup, first.dropoff) == (O1, R1, B, C)
    assert [
        first.released_s,
        first.assigned_s,
        first.pickup_reached_s,
        first.loaded_s,
        first.dropoff_reached_s,
        first.delivered_s,
    ] == pytest.approx([0.0, 0.0, 1.0, 1.5, 2.5, 3.0])
    assert (first.waiting_s, first.completion_s) == pytest.approx((1.0, 3.0))
    assert (first.empty_m, first.loaded_m) == (1.0, 1.0)

    assert second.robot_id == R1
    assert [
        second.released_s,
        second.assigned_s,
        second.pickup_reached_s,
        second.loaded_s,
        second.dropoff_reached_s,
        second.delivered_s,
    ] == pytest.approx([0.0, 3.0, 3.0, 3.5, 5.5, 6.0])
    assert (second.waiting_s, second.completion_s) == pytest.approx((3.0, 6.0))
    assert (second.empty_m, second.loaded_m) == (0.0, 2.0)


def test_steps_that_did_not_happen_are_none(line_graph: WarehouseGraph) -> None:
    orders = [TransportOrder(O1, B, C, 0), TransportOrder(O2, A, C, 4)]
    rows = order_timelines(simulate(_scenario(line_graph, orders), Lazy()))

    assert [row.released_s for row in rows] == pytest.approx([0.0, 0.4])
    for row in rows:
        assert row.robot_id is None
        assert row.assigned_s is row.delivered_s is row.waiting_s is row.completion_s is None
        assert (row.empty_m, row.loaded_m) == (0.0, 0.0)


def test_a_job_cut_off_by_max_tick_has_no_delivery(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, [TransportOrder(O1, B, C, 0)])
    (row,) = order_timelines(simulate(scenario, GreedyDispatch(), max_tick=12))

    assert row.robot_id == R1
    assert row.pickup_reached_s == pytest.approx(1.0)
    assert row.loaded_s is row.delivered_s is row.completion_s is None


@pytest.mark.parametrize("policy", ["greedy", "cpsat-fair"])
def test_rows_agree_with_the_kpis_on_a_run_with_traffic(policy: str) -> None:
    graph = generate_grid_warehouse(PRESETS["small"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=3, duration_s=900), seed=5)
    result = simulate(scenario, POLICIES[policy](), traffic=True)

    rows = order_timelines(result)
    kpis = compute_kpis(result)

    assert [row.order_id for row in rows] == [order.id for order in scenario.orders]
    delivered = [row for row in rows if row.delivered_s is not None]
    assert len(delivered) == kpis.delivered > 0
    for row in delivered:
        steps = [
            row.released_s,
            row.assigned_s,
            row.pickup_reached_s,
            row.loaded_s,
            row.dropoff_reached_s,
            row.delivered_s,
        ]
        present = [step for step in steps if step is not None]
        assert row.robot_id is not None
        assert len(present) == len(steps)  # every step happened
        assert present == sorted(present)  # in order
    completion = [row.completion_s for row in delivered if row.completion_s is not None]
    waiting = [row.waiting_s for row in delivered if row.waiting_s is not None]
    assert len(completion) == len(waiting) == len(delivered)
    assert statistics.fmean(completion) == pytest.approx(kpis.completion_mean_s, rel=1e-12)
    assert statistics.fmean(waiting) == pytest.approx(kpis.waiting_mean_s, rel=1e-12)
    assert sum(row.empty_m for row in rows) == pytest.approx(kpis.empty_distance_m)
    assert sum(row.loaded_m for row in rows) == pytest.approx(kpis.loaded_distance_m)
