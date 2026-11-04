"""Whole-run properties of the simulator on generated scenarios.

Expected travel times are computed independently of the simulator: route length from
Dijkstra, converted with the time model. The generated maps need no rounding at 1 m/s and
0.1 s ticks (D12), so length / speed / tick is exactly the number of ticks.
"""

from collections import defaultdict
from collections.abc import Sequence

import pytest

from amr_optimizer.domain import RobotId
from amr_optimizer.routing import shortest_path
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import (
    ORDER_LIFECYCLE,
    Assignment,
    DispatchSnapshot,
    EventKind,
    LogEntry,
    SimulationResult,
    simulate,
)
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse

MAPS = {name: generate_grid_warehouse(PRESETS[name]) for name in ("small", "medium")}


class FirstComeFirstServed:
    """Test policy: the oldest pending orders go to idle robots in scenario order."""

    name = "fcfs-test"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        return [
            Assignment(robot.robot_id, order.id)
            for robot, order in zip(snapshot.idle_robots, snapshot.pending_orders, strict=False)
        ]


def _length(graph: WarehouseGraph, source: NodeId, target: NodeId) -> float:
    route = shortest_path(graph, source, target).route
    assert route is not None
    return route.length_m


def _run(map_name: str, robots: int, seed: int, interval: int | None) -> SimulationResult:
    spec = ScenarioSpec(robots=robots, orders_per_hour=150, duration_s=1200)
    scenario = generate_scenario(MAPS[map_name], spec, seed=seed)
    return simulate(scenario, FirstComeFirstServed(), decision_interval_ticks=interval)


# Every map, fleet size and seed, with immediate decisions and with 5 s decision rounds.
CASES = [(m, r, s, i) for m in MAPS for r in (1, 3, 6) for s in range(4) for i in (None, 50)]
PARAMS = ("map_name", "robots", "seed", "interval")


@pytest.mark.parametrize(PARAMS, CASES)
def test_every_order_goes_through_its_lifecycle_with_exact_durations(
    map_name: str, robots: int, seed: int, interval: int | None
) -> None:
    result = _run(map_name, robots, seed, interval)
    scenario, graph = result.scenario, result.scenario.graph
    tick_s, speed = scenario.time_model.tick_s, scenario.speed_mps

    def ticks(length_m: float) -> int:
        return round(length_m / speed / tick_s)

    assert result.all_delivered
    by_order: dict[str, list[LogEntry]] = defaultdict(list)
    for entry in result.log:
        by_order[entry.order_id].append(entry)

    for order in scenario.orders:
        steps = by_order[order.id]
        assert tuple(e.kind for e in steps) == ORDER_LIFECYCLE
        released, assigned, reached, loaded, at_drop, delivered = steps
        assert [e.tick for e in steps] == sorted(e.tick for e in steps)
        assert released.tick == order.release_tick
        assert len({e.robot_id for e in steps[1:]}) == 1

        empty_m = _length(graph, assigned.node, order.pickup)
        loaded_m = _length(graph, order.pickup, order.dropoff)
        assert assigned.distance_m == pytest.approx(empty_m)
        assert loaded.distance_m == pytest.approx(loaded_m)
        assert reached.tick - assigned.tick == ticks(empty_m)
        assert loaded.tick - reached.tick == scenario.load_ticks
        assert at_drop.tick - loaded.tick == ticks(loaded_m)
        assert delivered.tick - at_drop.tick == scenario.unload_ticks
        assert (reached.node, at_drop.node) == (order.pickup, order.dropoff)


@pytest.mark.parametrize(PARAMS, CASES)
def test_robots_do_one_job_at_a_time_and_never_teleport(
    map_name: str, robots: int, seed: int, interval: int | None
) -> None:
    result = _run(map_name, robots, seed, interval)
    position = {robot.robot_id: robot.node for robot in result.scenario.robots}
    free_from: dict[RobotId, int] = defaultdict(int)

    for entry in result.log:
        if entry.robot_id is None:
            continue
        if entry.kind is EventKind.ASSIGNED:
            assert entry.tick >= free_from[entry.robot_id]  # previous job finished
            assert entry.node == position[entry.robot_id]  # starts where it last stopped
        elif entry.kind is EventKind.DELIVERED:
            free_from[entry.robot_id] = entry.tick
            position[entry.robot_id] = entry.node

    assert {r.robot_id: r.node for r in result.robots} == position
    assert all(robot.is_idle for robot in result.robots)
