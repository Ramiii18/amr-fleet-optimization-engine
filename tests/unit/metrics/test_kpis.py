"""Tests for the KPI definitions, on hand-computed runs.

``line_graph``: A - B - C, 1 m apart. At 1 m/s and 0.1 s ticks an edge takes 10 ticks
(1 s); loading and unloading take 5 ticks (0.5 s) each.
"""

import math
from collections.abc import Sequence

import pytest

from amr_optimizer.dispatch import GreedyDispatch
from amr_optimizer.domain import OrderId, RobotId, RobotState, TimeModel, TransportOrder
from amr_optimizer.metrics import compute_kpis, percentile
from amr_optimizer.scenario import Scenario
from amr_optimizer.sim import Assignment, DispatchSnapshot, simulate
from amr_optimizer.warehouse import NodeId, WarehouseGraph

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
R1, R2 = RobotId("R1"), RobotId("R2")
O1, O2 = OrderId("O1"), OrderId("O2")


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


class Lazy:
    name = "lazy-test"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        return []


# --- percentile ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        (list(range(1, 11)), 95, 10),  # rank ceil(9.5) = 10
        (list(range(1, 11)), 50, 5),  # rank ceil(5) = 5
        (list(range(1, 11)), 100, 10),
        (list(range(1, 21)), 95, 19),  # rank ceil(19) = 19
        ([7.0], 95, 7.0),
        ([3, 1, 2], 1, 1),  # input order does not matter
    ],
)
def test_nearest_rank_percentile(values: list[float], q: float, expected: float) -> None:
    assert percentile(values, q) == expected


def test_percentile_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="no values"):
        percentile([], 95)
    with pytest.raises(ValueError, match="q must be"):
        percentile([1.0], 0)
    with pytest.raises(ValueError, match="q must be"):
        percentile([1.0], 101)


# --- compute_kpis --------------------------------------------------------------------------------


def test_single_job(line_graph: WarehouseGraph) -> None:
    # Released and assigned at 0 s; pickup reached at 1.0 s; delivered at 3.0 s.
    result = simulate(
        _scenario(line_graph, {R1: A}, [TransportOrder(O1, B, C, 0)]), GreedyDispatch()
    )

    kpis = compute_kpis(result)

    assert (kpis.policy, kpis.robots, kpis.orders, kpis.delivered) == ("greedy", 1, 1, 1)
    assert kpis.undelivered == 0
    assert kpis.duration_s == pytest.approx(3.0)
    assert kpis.throughput_per_h == pytest.approx(1200.0)  # 1 order in 3 s
    assert kpis.waiting_mean_s == pytest.approx(1.0)
    assert kpis.waiting_p95_s == pytest.approx(1.0)
    assert kpis.assignment_delay_mean_s == 0.0
    assert kpis.completion_mean_s == pytest.approx(3.0)
    assert kpis.completion_max_s == pytest.approx(3.0)
    assert kpis.utilization == pytest.approx(1.0)  # busy from 0 to 3.0 s, of 3.0 s
    assert (kpis.empty_distance_m, kpis.loaded_distance_m) == (1.0, 1.0)
    assert kpis.empty_share == pytest.approx(0.5)
    assert kpis.conflicts == 0  # one robot cannot conflict


def test_queued_order_waits_for_the_busy_robot(line_graph: WarehouseGraph) -> None:
    # O1 as above. O2 (C -> A, released at 0) waits until R1 delivers O1 at C at 3.0 s,
    # is loaded straight away, and is delivered at 3.0 + 0.5 + 2.0 + 0.5 = 6.0 s.
    orders = [TransportOrder(O1, B, C, 0), TransportOrder(O2, C, A, 0)]
    kpis = compute_kpis(simulate(_scenario(line_graph, {R1: A}, orders), GreedyDispatch()))

    assert kpis.waiting_mean_s == pytest.approx((1.0 + 3.0) / 2)
    assert kpis.assignment_delay_mean_s == pytest.approx((0.0 + 3.0) / 2)
    assert kpis.completion_mean_s == pytest.approx((3.0 + 6.0) / 2)
    assert kpis.completion_p95_s == pytest.approx(6.0)
    assert kpis.completion_max_s == pytest.approx(6.0)
    assert kpis.utilization == pytest.approx(1.0)
    assert (kpis.empty_distance_m, kpis.loaded_distance_m) == (1.0, 3.0)


def test_utilization_counts_idle_robots(line_graph: WarehouseGraph) -> None:
    result = simulate(
        _scenario(line_graph, {R1: A, R2: C}, [TransportOrder(O1, B, C, 0)]), GreedyDispatch()
    )

    kpis = compute_kpis(result)

    assert kpis.utilization == pytest.approx(0.5)
    # In free flow, R1 delivers to C, where idle R2 stands: one vertex conflict.
    assert kpis.conflicts == 1


def test_undelivered_orders_are_counted_and_time_kpis_are_nan(line_graph: WarehouseGraph) -> None:
    orders = [TransportOrder(O1, B, C, 0), TransportOrder(O2, A, C, 4)]
    kpis = compute_kpis(simulate(_scenario(line_graph, {R1: A}, orders), Lazy()))

    assert (kpis.delivered, kpis.undelivered) == (0, 2)
    assert math.isnan(kpis.waiting_mean_s) and math.isnan(kpis.completion_p95_s)
    assert math.isnan(kpis.completion_max_s)
    assert kpis.utilization == 0.0
    assert math.isnan(kpis.empty_share)


def test_jobs_cut_off_by_max_tick_count_as_busy_until_the_end(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph, {R1: A}, [TransportOrder(O1, B, C, 0)])
    kpis = compute_kpis(simulate(scenario, GreedyDispatch(), max_tick=12))

    assert (kpis.delivered, kpis.undelivered) == (0, 1)
    assert kpis.utilization == pytest.approx(1.0)  # busy for the whole 1.0 s that was run
