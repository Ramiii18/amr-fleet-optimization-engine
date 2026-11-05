"""Tests for safe-interval itinerary planning, by hand and against a search over every tick.

``line_graph``: A - B - C, 1 m apart, two-way; at 1 m/s and 0.1 s ticks each edge takes 10
ticks.
"""

import contextlib
import heapq
import random
from collections.abc import Sequence

import pytest

from amr_optimizer.domain import RobotId, TimeModel, TravelTimes
from amr_optimizer.traffic import (
    FOREVER,
    Corridor,
    ItineraryPlanner,
    Occupancy,
    ReservationConflictError,
    ReservationTable,
    Stop,
    TimedPath,
    Visit,
    find_conflicts,
    validate_timed_path,
)
from amr_optimizer.warehouse import Edge, Node, NodeId, WarehouseGraph

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
OTHER = RobotId("other")


def _planner(graph: WarehouseGraph, speed_mps: float = 1.0) -> ItineraryPlanner:
    return ItineraryPlanner(TravelTimes(graph, speed_mps=speed_mps, time_model=TimeModel(0.1)))


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


# --- by hand ---------------------------------------------------------------------------------


def test_on_a_free_map_the_itinerary_is_the_free_flow_path(line_graph: WarehouseGraph) -> None:
    itinerary = _planner(line_graph).plan(ReservationTable(), A, 0, [Stop(C)])

    assert itinerary is not None
    assert itinerary.path == _path((A, 0, 0), (B, 10, 10), (C, 20, FOREVER))
    assert itinerary.stop_ticks == (20,)


def test_the_robot_waits_for_an_oncoming_robot_to_clear(line_graph: WarehouseGraph) -> None:
    # The other robot waits at C until tick 12, then drives C -> B during (12, 22) and stops at
    # B at tick 22. Free flow (B at 10, C at 20) would meet it head-on in the corridor. Waiting
    # at B is no use: the other robot needs B at tick 22. So the robot waits at A until 13,
    # reaches B at 23, the first tick B is free again, and C at 33.
    table = ReservationTable()
    table.reserve(OTHER, _path((C, 0, 12), (B, 22, 22)))

    itinerary = _planner(line_graph).plan(table, A, 0, [Stop(C)])

    assert itinerary is not None
    assert itinerary.path == _path((A, 0, 13), (B, 23, 23), (C, 33, FOREVER))


def test_services_are_spent_at_their_stops(line_graph: WarehouseGraph) -> None:
    planner = _planner(line_graph)

    loaded = planner.plan(ReservationTable(), A, 0, [Stop(B, 5), Stop(C)])
    at_start = planner.plan(ReservationTable(), A, 0, [Stop(A, 5), Stop(C)])
    same_node = planner.plan(ReservationTable(), A, 0, [Stop(C, 5), Stop(C)])

    assert loaded is not None and at_start is not None and same_node is not None
    assert loaded.path == _path((A, 0, 0), (B, 10, 15), (C, 25, FOREVER))
    assert loaded.stop_ticks == (10, 25)
    assert at_start.path == _path((A, 0, 5), (B, 15, 15), (C, 25, FOREVER))
    assert at_start.stop_ticks == (0, 25)
    assert same_node.path == _path((A, 0, 0), (B, 10, 10), (C, 20, FOREVER))
    assert same_node.stop_ticks == (20, 25)  # the hold begins when the service ends


def test_a_held_goal_cannot_be_reached(line_graph: WarehouseGraph) -> None:
    table = ReservationTable()
    table.reserve(OTHER, _path((C, 0, FOREVER)))

    assert _planner(line_graph).plan(table, A, 0, [Stop(C)]) is None


def test_bad_requests_are_rejected(line_graph: WarehouseGraph) -> None:
    table = ReservationTable()
    table.reserve(OTHER, _path((A, 0, 5)))
    planner = _planner(line_graph)

    with pytest.raises(ValueError, match="occupied at tick 3"):
        planner.plan(table, A, 3, [Stop(C)])
    with pytest.raises(ValueError, match="must be a hold"):
        planner.plan(table, C, 0, [Stop(B, 5)])
    with pytest.raises(ValueError, match="only the last stop"):
        planner.plan(table, C, 0, [Stop(B), Stop(A)])
    with pytest.raises(ValueError, match="service time"):
        Stop(B, -1)


# --- against a search over every tick ------------------------------------------------------------


def _grid(rng: random.Random) -> WarehouseGraph:
    """A 3 x 3 grid of two-way corridors, 1 m apart, each 1.0 or 1.5 m long."""
    nodes = [Node(NodeId(f"{x}{y}"), float(x), float(y)) for x in range(3) for y in range(3)]
    edges = []
    for x in range(3):
        for y in range(3):
            for dx, dy in ((1, 0), (0, 1)):
                if x + dx < 3 and y + dy < 3:
                    u, v = NodeId(f"{x}{y}"), NodeId(f"{x + dx}{y + dy}")
                    length = rng.choice([1.0, 1.5])
                    edges += [Edge(u, v, length), Edge(v, u, length)]
    return WarehouseGraph(nodes, edges)


def _random_reservations(rng: random.Random, planner: ItineraryPlanner) -> ReservationTable:
    """Random walks with waits, each reserved if it fits; some end in a hold."""
    graph, times = planner.travel_times.graph, planner.travel_times
    nodes = sorted(n.id for n in graph.nodes)
    table = ReservationTable()
    for index in range(rng.randint(1, 4)):
        node, tick = rng.choice(nodes), rng.randint(0, 40)
        visits = []
        for _ in range(rng.randint(1, 6)):
            depart = tick + rng.randint(0, 6)
            visits.append(Visit(node, tick, depart))
            following = rng.choice([e.target for e in graph.out_edges(node)])
            tick, node = depart + times.edge_ticks(node, following), following
        hold = rng.random() < 0.15
        visits.append(Visit(node, tick, FOREVER if hold else tick + rng.randint(0, 6)))
        with contextlib.suppress(ReservationConflictError):  # skip walks that don't fit
            table.reserve(RobotId(f"other{index}"), TimedPath(tuple(visits)))
    return table


def _earliest_by_ticks(
    table: ReservationTable,
    planner: ItineraryPlanner,
    start: NodeId,
    start_tick: int,
    stops: Sequence[Stop],
    horizon: int,
) -> int | None:
    """The earliest arrival at the last stop, by Dijkstra over (stage, node, tick) states."""
    graph, times = planner.travel_times.graph, planner.travel_times

    def node_free(node: NodeId, tick: int) -> bool:
        return all(not r.start <= tick <= r.end for r in table.node_reservations(node))

    def corridor_free(u: NodeId, v: NodeId, depart: int, ticks: int) -> bool:
        return all(
            not (depart < r.end and r.start < depart + ticks)
            for r in table.corridor_reservations(u, v)
        )

    last = len(stops) - 1
    heap = [(start_tick, 0, start)]
    seen: set[tuple[int, int, NodeId]] = set()
    while heap:
        tick, stage, node = heapq.heappop(heap)
        if (tick, stage, node) in seen or tick > horizon:
            continue
        seen.add((tick, stage, node))
        stop = stops[stage]
        if stage == last and node == stop.node and table.is_free_from(node, tick):
            return tick
        if stage < last and node == stop.node:
            service = stop.service_ticks
            assert service is not None
            if all(node_free(node, t) for t in range(tick, tick + service + 1)):
                heapq.heappush(heap, (tick + service, stage + 1, node))
        if node_free(node, tick + 1):
            heapq.heappush(heap, (tick + 1, stage, node))
        for edge in graph.out_edges(node):
            ticks = times.edge_ticks(node, edge.target)
            if corridor_free(node, edge.target, tick, ticks) and node_free(
                edge.target, tick + ticks
            ):
                heapq.heappush(heap, (tick + ticks, stage, edge.target))
    return None


def test_sipp_finds_the_earliest_itinerary_found_by_searching_every_tick() -> None:
    found = impossible = waited = 0
    for seed in range(300):
        rng = random.Random(seed)
        planner = _planner(_grid(rng), speed_mps=2.0)  # 5 or 8 ticks per corridor
        table = _random_reservations(rng, planner)
        nodes = sorted(n.id for n in planner.travel_times.graph.nodes)
        start_tick = rng.randint(0, 20)
        free = [
            n
            for n in nodes
            if (intervals := table.safe_intervals(n, start_tick)) and intervals[0][0] == start_tick
        ]
        if not free:
            continue
        start = rng.choice(free)
        stops = [Stop(rng.choice(nodes), rng.randint(0, 6)) for _ in range(rng.randint(0, 2))]
        stops.append(Stop(rng.choice(nodes)))
        latest = max(
            (r.end for n in nodes for r in table.node_reservations(n) if r.end < FOREVER),
            default=0,
        )

        itinerary = planner.plan(table, start, start_tick, stops)
        expected = _earliest_by_ticks(table, planner, start, start_tick, stops, latest + 400)

        if expected is None:
            assert itinerary is None, f"seed {seed}: planned an impossible itinerary"
            impossible += 1
            continue
        assert itinerary is not None, f"seed {seed}: missed a possible itinerary"
        assert itinerary.stop_ticks[-1] == expected, f"seed {seed}"
        path = itinerary.path
        validate_timed_path(path, planner.travel_times)
        assert (path.start.node, path.start.arrive, path.holds) == (start, start_tick, True)
        mine = [(RobotId("me"), o) for o in path.occupancies()]
        others = _all_reservations(table, planner.travel_times.graph)
        assert not find_conflicts(mine + others), f"seed {seed}: the path conflicts"
        found += 1
        waited += any(
            v.depart > v.arrive for v in path.visits[:-1] if v.node not in {s.node for s in stops}
        )

    assert found > 200 and impossible > 0 and waited > 0  # all three situations occur


def _all_reservations(
    table: ReservationTable, graph: WarehouseGraph
) -> list[tuple[RobotId, Occupancy]]:
    """Every reservation in ``table``, as occupancies of the robot that holds it."""
    result = []
    corridors = {
        Corridor.between(e.source, e.target) for n in graph.nodes for e in graph.out_edges(n.id)
    }
    for node in graph.nodes:
        result += [
            (r.robot, Occupancy(node.id, r.start, r.end)) for r in table.node_reservations(node.id)
        ]
    for corridor in sorted(corridors):
        result += [
            (r.robot, Occupancy(corridor, r.start, r.end))
            for r in table.corridor_reservations(corridor.a, corridor.b)
        ]
    return result
