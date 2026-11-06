"""Tests for orders built from the engine's plans, and the specification's order rules (D30).

Hand map: a line A - B - C - D of 1 m edges (10 ticks each at 1 m/s and 0.1 s ticks).
"""

import dataclasses
import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from amr_optimizer import warehouse
from amr_optimizer.domain import TimeModel, TravelTimes
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim.traffic_control import TrafficControl
from amr_optimizer.traffic import FOREVER, TimedPath, Visit
from amr_optimizer.vda5050 import (
    Action,
    Edge,
    Header,
    Node,
    NodePosition,
    order_message,
    order_violations,
    route_of,
    update_violations,
)
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse

Check = Callable[[str, dict[str, Any]], None]
A, B, C, D = (NodeId(n) for n in "ABCD")
HEADER = Header(0, datetime(2026, 11, 6, 9, 0, tzinfo=UTC), "synthetic", "R1")
PICK = Action("pick", "O1-pick", "HARD")
DROP = Action("drop", "O1-drop", "HARD")


def _line() -> WarehouseGraph:
    nodes = [warehouse.Node(n, float(i), 0.0) for i, n in enumerate((A, B, C, D))]
    pairs = [(A, B), (B, C), (C, D)]
    return WarehouseGraph(
        nodes, [warehouse.Edge(u, v, 1.0) for a, b in pairs for u, v in ((a, b), (b, a))]
    )


PATH = TimedPath((Visit(A, 0, 0), Visit(B, 10, 15), Visit(C, 25, 25), Visit(D, 35, FOREVER)))
ROUTE = route_of(PATH, _line(), "line", {1: [PICK], 3: [DROP]})


def test_a_path_becomes_alternating_nodes_and_edges() -> None:
    nodes, edges = ROUTE

    assert [(n.node_id, n.sequence_id) for n in nodes] == [("A", 0), ("B", 2), ("C", 4), ("D", 6)]
    assert [(e.edge_id, e.sequence_id, e.length_m) for e in edges] == [
        ("A->B", 1, 1.0),
        ("B->C", 3, 1.0),
        ("C->D", 5, 1.0),
    ]
    assert nodes[2].position == NodePosition(2.0, 0.0, "line")
    assert [n.actions for n in nodes] == [(), (PICK,), (), (DROP,)]
    assert not any(n.released for n in nodes) and not any(e.released for e in edges)


def test_an_order_releases_a_prefix_as_its_base(check_message: Check) -> None:
    order = order_message(HEADER, "O1", 0, ROUTE, start=0, base_end=1)

    assert [n.released for n in order.nodes] == [True, True, False, False]
    assert [e.released for e in order.edges] == [True, False, False]
    assert order_violations(order) == []
    check_message("order", order.to_json())


def test_an_update_extends_the_base_from_the_stitching_node(check_message: Check) -> None:
    first = order_message(HEADER, "O1", 0, ROUTE, start=0, base_end=1)

    update = order_message(HEADER, "O1", 1, ROUTE, start=1, base_end=3)

    assert [(n.node_id, n.sequence_id, n.released) for n in update.nodes] == [
        ("B", 2, True),
        ("C", 4, True),
        ("D", 6, True),
    ]
    assert [e.edge_id for e in update.edges] == ["B->C", "C->D"]
    assert update_violations(first, update) == []
    check_message("order", update.to_json())


def _with(order_nodes: list[Node], order_edges: list[Edge]) -> Any:
    return dataclasses.replace(
        order_message(HEADER, "O1", 0, ROUTE, start=0, base_end=3),
        nodes=tuple(order_nodes),
        edges=tuple(order_edges),
    )


def test_broken_rules_are_detected() -> None:
    nodes, edges = ROUTE
    released = [dataclasses.replace(n, released=True) for n in nodes]
    released_edges = [dataclasses.replace(e, released=True) for e in edges]

    assert "need one fewer edge" in order_violations(_with(released, released_edges[:2]))[0]
    gap = [*released[:2], dataclasses.replace(released[2], sequence_id=6), released[3]]
    assert "not continuous" in " ".join(order_violations(_with(gap, released_edges)))
    after = [released[0], dataclasses.replace(released[1], released=False), *released[2:]]
    assert "follows an unreleased" in " ".join(order_violations(_with(after, released_edges)))
    edge_ahead = [*released[:3], dataclasses.replace(released[3], released=False)]
    assert "without both its nodes" in " ".join(order_violations(_with(edge_ahead, released_edges)))

    first = order_message(HEADER, "O1", 0, ROUTE, start=0, base_end=1)
    elsewhere = order_message(HEADER, "O1", 1, ROUTE, start=2, base_end=3)
    stale = order_message(HEADER, "O1", 0, ROUTE, start=1, base_end=3)
    assert "last node of the previous base" in " ".join(update_violations(first, elsewhere))
    assert "must increase" in " ".join(update_violations(first, stale))


def test_a_path_must_drive_edges_of_the_map() -> None:
    jump = TimedPath((Visit(A, 0, 0), Visit(C, 20, FOREVER)))

    with pytest.raises(ValueError, match="not an edge of the map"):
        route_of(jump, _line(), "line")


@pytest.mark.parametrize("seed", range(4))
def test_planned_jobs_become_valid_orders_and_updates(seed: int, check_message: Check) -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    scenario = generate_scenario(graph, ScenarioSpec(robots=4, orders_per_hour=600), seed=seed)
    traffic = TrafficControl(scenario, TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))
    rng = random.Random(seed)
    used: set[str] = set()
    for robot, order in zip(scenario.robots, scenario.orders, strict=False):
        if order.pickup in used:
            continue
        used.add(order.pickup)
        job = traffic.plan_job(robot, order, 0, frozenset())
        pickup, dropoff, _ = job.stop_visits
        route = route_of(
            job.path,
            graph,
            "medium",
            {
                pickup: [Action("pick", f"{order.id}-pick")],
                dropoff: [Action("drop", f"{order.id}-drop")],
            },
        )
        last = len(route[0]) - 1
        previous, start, base_end, update_id = None, 0, rng.randint(0, last), 0
        while True:
            message = order_message(
                HEADER, order.id, update_id, route, start=start, base_end=base_end
            )
            assert order_violations(message) == []
            if previous is not None:
                assert update_violations(previous, message) == []
            check_message("order", message.to_json())
            if base_end == last:
                break
            previous, start, update_id = message, base_end, update_id + 1
            base_end = rng.randint(base_end + 1, last)
