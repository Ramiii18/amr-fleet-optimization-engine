"""Tests for mock robots and fleet control over VDA 5050 messages (D30).

Hand map: a line A - B - C - D of 1 m edges (10 ticks each at 1 m/s and 0.1 s ticks).
"""

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from amr_optimizer import warehouse
from amr_optimizer.domain import RobotId, RobotState, TimeModel, TravelTimes
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim.traffic_control import TrafficControl
from amr_optimizer.traffic import FOREVER, TimedPath, Visit, path_conflicts
from amr_optimizer.vda5050 import (
    FleetRun,
    Header,
    Job,
    MessageBus,
    MockVehicle,
    Order,
    OrderRejectedError,
    first_jobs,
    order_message,
    order_violations,
    route_of,
    run_jobs,
    update_violations,
)
from amr_optimizer.warehouse import PRESETS, NodeId, generate_grid_warehouse

Check = Callable[[str, dict[str, Any]], None]
A, B, C, D = (NodeId(n) for n in "ABCD")
X, Y = RobotId("X"), RobotId("Y")


def _line() -> warehouse.WarehouseGraph:
    nodes = [warehouse.Node(n, float(i), 0.0) for i, n in enumerate((A, B, C, D))]
    pairs = [(A, B), (B, C), (C, D)]
    edges = [warehouse.Edge(u, v, 1.0) for a, b in pairs for u, v in ((a, b), (b, a))]
    return warehouse.WarehouseGraph(nodes, edges)


def _conflicts(run: FleetRun) -> int:
    return len(path_conflicts({r: [p] for r, p in run.trajectories.items()}))


def _visits(run: FleetRun, robot: RobotId) -> list[tuple[str, int, int]]:
    return [(v.node, v.arrive, v.depart) for v in run.trajectories[robot].visits]


# --- by hand -------------------------------------------------------------------------------------


def test_a_robot_stops_at_its_decision_point_until_the_robot_ahead_has_left() -> None:
    # Y stands at C and picks for 15 ticks, then drives to D. X drives A -> B -> C, planned to reach
    # C at tick 20, after Y has left it. Y is first at C in the planned order, so X's base ends at
    # B: X stops there (tick 11). Y leaves C at tick 16; fleet control releases B -> C and C to X
    # in an order update at tick 16, and X leaves B at tick 17 and reaches C at tick 27.
    scenario = Scenario(
        graph=_line(),
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=15,
        unload_ticks=5,
        robots=(RobotState.idle(X, A), RobotState.idle(Y, C)),
        orders=(),
    )
    jobs = [
        Job(Y, "JY", TimedPath((Visit(C, 0, 15), Visit(D, 25, FOREVER))), pickup=0),
        Job(X, "JX", TimedPath((Visit(A, 0, 0), Visit(B, 10, 10), Visit(C, 20, FOREVER)))),
    ]

    run = run_jobs(scenario, jobs, map_id="line")

    assert _visits(run, X) == [("A", 0, 1), ("B", 11, 17), ("C", 27, 27)]
    assert _visits(run, Y) == [("C", 0, 16), ("D", 26, 27)]
    orders = [json.loads(m.payload) for m in run.log if m.topic.endswith("/X/order")]
    assert [
        (o["orderUpdateId"], [n["nodeId"] for n in o["nodes"] if n["released"]]) for o in orders
    ] == [
        (0, ["A", "B"]),
        (1, ["B", "C"]),
    ]
    assert [m.tick for m in run.log if m.topic.endswith("/X/order")] == [0, 16]
    assert _conflicts(run) == 0


def test_a_mock_robot_refuses_invalid_orders_and_updates() -> None:
    bus = MessageBus()
    vehicle = MockVehicle("R1", "A", bus, TimeModel(0.1))
    header = Header(0, datetime(2026, 1, 1, tzinfo=UTC), "synthetic", "R1")
    route = route_of(
        TimedPath((Visit(A, 0, 0), Visit(B, 10, 10), Visit(C, 20, FOREVER))), _line(), "line"
    )

    def send(order: Order) -> None:
        bus.publish(0, vehicle.order_topic, order.to_json())
        vehicle.receive(0)

    elsewhere = route_of(TimedPath((Visit(B, 0, 0), Visit(C, 10, FOREVER))), _line(), "line")
    with pytest.raises(OrderRejectedError, match="must start where the robot is"):
        send(order_message(header, "O1", 0, elsewhere, start=0, base_end=1))
    send(order_message(header, "O1", 0, route, start=0, base_end=1))
    with pytest.raises(OrderRejectedError, match="last node of the previous base"):
        send(order_message(header, "O1", 1, route, start=2, base_end=2))


# --- generated runs ------------------------------------------------------------------------------

GRAPH = generate_grid_warehouse(PRESETS["medium"])


def _run(seed: int) -> FleetRun:
    scenario = generate_scenario(GRAPH, ScenarioSpec(robots=8, orders_per_hour=600), seed=seed)
    traffic = TrafficControl(scenario, TravelTimes(GRAPH, speed_mps=1.0, time_model=TimeModel(0.1)))
    return run_jobs(scenario, first_jobs(scenario, traffic), map_id="medium")


@pytest.mark.parametrize("seed", range(4))
def test_planned_jobs_run_through_messages_without_conflicts(
    seed: int, check_message: Check
) -> None:
    run = _run(seed)

    assert len(run.delivered_at) == len(run.planned_at) > 0  # every drop reported finished
    assert _conflicts(run) == 0
    # The robots stop only at nodes, so they may finish later than planned, never earlier
    # than one tick of message latency allows.
    assert all(run.delivered_at[o] >= run.planned_at[o] + 1 for o in run.planned_at)
    sent: dict[str, list[Order]] = defaultdict(list)
    for message in run.log:
        name = message.topic.rsplit("/", 1)[1]
        data = json.loads(message.payload)
        check_message(name, data)
        if name == "order":
            sent[message.topic].append(Order.from_json(data))
    for orders in sent.values():
        assert order_violations(orders[0]) == []
        for previous, update in zip(orders, orders[1:], strict=False):
            assert update_violations(previous, update) == []


def test_runs_are_reproducible() -> None:
    first, again = _run(1), _run(1)

    assert first.log == again.log and first.trajectories == again.trajectories


def test_a_later_use_of_an_intersection_is_released_only_after_the_earlier_one() -> None:
    # A regression test: in this run a robot goes into a pickup's spur and back out through the
    # same intersection, which another robot passes in between. Releasing the way back while the
    # robot still headed that intersection's queue let it in out of the planned order.
    run = _run(1)

    assert _conflicts(run) == 0


def test_jobs_sent_together_all_join_the_planned_order_before_any_release() -> None:
    # A regression test: on the large map, seed 10, robot R10's job is planned through an
    # intersection before R08's. When jobs were released one by one, R08 was released before
    # R10's job had joined the planned order, and passed the intersection first.
    graph = generate_grid_warehouse(PRESETS["large"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=10, orders_per_hour=600), seed=10)
    traffic = TrafficControl(scenario, TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))

    run = run_jobs(scenario, first_jobs(scenario, traffic), map_id="large")

    assert len(run.delivered_at) == len(run.planned_at) == 10
    assert _conflicts(run) == 0


def test_no_robot_parks_on_the_pickup_of_another_job() -> None:
    # A regression test: on the large map with 25 robots, seed 2, planning every job without
    # avoiding the other jobs' pickups sent one robot to park on another job's pickup, which then
    # had no itinerary.
    graph = generate_grid_warehouse(PRESETS["large"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=25, orders_per_hour=600), seed=2)
    traffic = TrafficControl(scenario, TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))

    jobs = first_jobs(scenario, traffic)

    assert len(jobs) == 25
    pickups = {job.path.visits[job.pickup].node for job in jobs if job.pickup is not None}
    assert pickups.isdisjoint(job.path.end.node for job in jobs)


def test_robots_waiting_on_each_other_around_a_ring_rotate_together() -> None:
    # The counterexample of an external review: a one-way ring C0 -> C1 -> C2 -> C3 -> C0 with a
    # start spur and a destination spur at every ring node. Robot i drives S_i -> C_i -> C_i+1 ->
    # D_i+1, planned with SIPP one after another: all four stand on the ring at tick 10, each
    # planned to leave as the robot behind arrives. Robots can stop only at nodes, so each waits
    # for the next robot's node, and without a joint release none ever moves (D31).
    from amr_optimizer.traffic import ItineraryPlanner, ReservationTable, Stop

    ring = [NodeId(f"C{i}") for i in range(4)]
    corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    starts_at = [(-1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (-1.0, 1.0)]
    ends_at = [(0.0, -1.0), (1.0, -1.0), (1.0, 2.0), (0.0, 2.0)]
    nodes, edges = [], []
    for i, c in enumerate(ring):
        s, d = NodeId(f"S{i}"), NodeId(f"D{i}")
        nodes += [
            warehouse.Node(c, *corners[i]),
            warehouse.Node(s, *starts_at[i], warehouse.NodeKind.PARKING),
            warehouse.Node(d, *ends_at[i], warehouse.NodeKind.PARKING),
        ]
        edges.append(warehouse.Edge(c, ring[(i + 1) % 4], 1.0))
        edges += [warehouse.Edge(u, v, 1.0) for a in (s, d) for u, v in ((a, c), (c, a))]
    graph = warehouse.WarehouseGraph(nodes, edges)
    robots = [RobotId(f"R{i}") for i in range(4)]
    planner = ItineraryPlanner(TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))
    table = ReservationTable()
    for i, robot in enumerate(robots):
        table.reserve(robot, TimedPath((Visit(NodeId(f"S{i}"), 0, FOREVER),)))
    jobs = []
    for i, robot in enumerate(robots):
        table.end_hold(robot, 0)
        itinerary = planner.plan(table, NodeId(f"S{i}"), 0, [Stop(NodeId(f"D{(i + 1) % 4}"))])
        assert itinerary is not None
        table.reserve(robot, itinerary.path)
        assert [v.node for v in itinerary.path.visits] == [
            f"S{i}",
            f"C{i}",
            f"C{(i + 1) % 4}",
            f"D{(i + 1) % 4}",
        ]
        jobs.append(Job(robot, f"J{i}", itinerary.path))
    scenario = Scenario(
        graph=graph,
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=0,
        unload_ticks=0,
        robots=tuple(RobotState.idle(r, NodeId(f"S{i}")) for i, r in enumerate(robots)),
        orders=(),
    )

    run = run_jobs(scenario, jobs, map_id="ring")

    for i, robot in enumerate(robots):
        assert run.trajectories[robot].end.node == f"D{(i + 1) % 4}"
    assert _conflicts(run) == 0
