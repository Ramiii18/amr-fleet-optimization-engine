"""Tests for executing plans under delays (D25).

Hand cases use a line N0 - N1 - N2 - N3 of two-way 1 m corridors: 10 ticks each at 1 m/s and
0.1 s ticks.
"""

import random

import pytest

from amr_optimizer.domain import RobotId, TimeModel, TravelTimes
from amr_optimizer.traffic import (
    FOREVER,
    DelayModel,
    ExecutionPolicy,
    ItineraryPlanner,
    ReservationTable,
    Stop,
    TimedPath,
    Visit,
    execute_plans,
    path_conflicts,
)
from amr_optimizer.warehouse import Edge, Node, NodeId, WarehouseGraph

N = [NodeId(f"N{i}") for i in range(4)]
R1, R2 = RobotId("R1"), RobotId("R2")
ORDERED, NAIVE = ExecutionPolicy.ORDERED, ExecutionPolicy.NAIVE


def _times(graph: WarehouseGraph) -> TravelTimes:
    return TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1))


def _line() -> WarehouseGraph:
    nodes = [Node(n, float(i), 0.0) for i, n in enumerate(N)]
    edges = [Edge(N[i], N[j], 1.0) for i in range(3) for i, j in ((i, i + 1), (i + 1, i))]
    return WarehouseGraph(nodes, edges)


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


def _conflicts(paths: dict[RobotId, TimedPath]) -> list[str]:
    return [c.kind for c in path_conflicts({r: [p] for r, p in paths.items()})]


# --- by hand -------------------------------------------------------------------------------------

# R1 drives N1 -> N2 -> N3 from tick 0. R2 follows one corridor behind: N0 -> N1 -> N2.
FOLLOW = {
    R1: _path((N[1], 0, 0), (N[2], 10, 10), (N[3], 20, FOREVER)),
    R2: _path((N[0], 0, 0), (N[1], 10, 10), (N[2], 20, FOREVER)),
}


def test_without_delays_both_policies_reproduce_the_plan() -> None:
    times = _times(_line())

    assert execute_plans(FOLLOW, times, policy=ORDERED) == FOLLOW
    assert execute_plans(FOLLOW, times, policy=NAIVE) == FOLLOW


def test_a_delayed_leader_makes_its_follower_wait() -> None:
    # R1's first edge takes 15 ticks longer: it reaches N2 at 25 and leaves at once. Ordered,
    # R2 may enter the corridor N1 - N2 only when R1 has left it (25), and N2 only one tick
    # after R1 has left it (26); it reaches N2 at 35.
    times = _times(_line())
    delays = {R1: [15, 0]}

    ordered = execute_plans(FOLLOW, times, policy=ORDERED, delays=delays)
    naive = execute_plans(FOLLOW, times, policy=NAIVE, delays=delays)

    assert ordered[R1] == _path((N[1], 0, 0), (N[2], 25, 25), (N[3], 35, FOREVER))
    assert ordered[R2] == _path((N[0], 0, 0), (N[1], 10, 25), (N[2], 35, FOREVER))
    assert _conflicts(ordered) == []
    assert set(_conflicts(naive)) == {"following", "vertex"}  # R2 drives into R1 and its node


def test_a_follower_waits_at_the_end_of_its_corridor() -> None:
    # The plan lets R2 drive N1 -> N2 during (3, 13) because R1 leaves N2 at 12. R1's service
    # at N2 takes 30 ticks instead, so R1 leaves at 30. R2 still departs at 3 (its corridor is
    # free) but waits at the end of the corridor and enters N2 at 31.
    plans = {
        R1: _path((N[2], 0, 12), (N[3], 22, FOREVER)),
        R2: _path((N[1], 0, 3), (N[2], 13, FOREVER)),
    }

    result = execute_plans(plans, _times(_line()), policy=ORDERED, min_dwell={R1: [30, 0]})

    assert result[R1] == _path((N[2], 0, 30), (N[3], 40, FOREVER))
    assert result[R2] == _path((N[1], 0, 3), (N[2], 31, FOREVER))
    assert _conflicts(result) == []


def test_ordered_execution_needs_a_conflict_free_plan() -> None:
    clash = {R1: _path((N[1], 0, 5)), R2: _path((N[1], 3, 9))}

    with pytest.raises(ValueError, match="conflict-free"):
        execute_plans(clash, _times(_line()), policy=ORDERED)
    assert execute_plans(clash, _times(_line()), policy=NAIVE) == clash  # naive doesn't care


def test_the_delay_model_is_validated_and_uses_its_stream_evenly() -> None:
    with pytest.raises(ValueError, match="probability"):
        DelayModel(1.5)
    with pytest.raises(ValueError, match="min_ticks"):
        DelayModel(0.5, min_ticks=5, max_ticks=2)
    never, always = random.Random(1), random.Random(1)

    assert DelayModel(0.0, 5, 9).draw(never) == 0
    assert 5 <= DelayModel(1.0, 5, 9).draw(always) <= 9
    assert never.random() == always.random()  # both draws consumed the same amount


# --- random fleets ------------------------------------------------------------------------------


def _grid() -> WarehouseGraph:
    """A 3 x 4 grid of two-way 1 m corridors."""
    nodes = [Node(NodeId(f"{x}{y}"), float(x), float(y)) for x in range(3) for y in range(4)]
    edges = []
    for x in range(3):
        for y in range(4):
            for dx, dy in ((1, 0), (0, 1)):
                if x + dx < 3 and y + dy < 4:
                    u, v = NodeId(f"{x}{y}"), NodeId(f"{x + dx}{y + dy}")
                    edges += [Edge(u, v, 1.0), Edge(v, u, 1.0)]
    return WarehouseGraph(nodes, edges)


def _random_fleet(
    rng: random.Random, planner: ItineraryPlanner
) -> tuple[dict[RobotId, TimedPath], dict[RobotId, list[int]]] | None:
    """Robots planned one after another with SIPP, each through one service stop to a hold."""
    nodes = sorted(n.id for n in planner.travel_times.graph.nodes)
    robots = rng.randint(2, 4)
    places = rng.sample(nodes, 2 * robots)
    starts, goals = places[:robots], places[robots:]
    table = ReservationTable()
    ids = [RobotId(f"R{k}") for k in range(robots)]
    for robot, start in zip(ids, starts, strict=True):
        table.reserve(robot, TimedPath((Visit(start, 0, FOREVER),)))
    plans, dwell = {}, {}
    for robot, start, goal in zip(ids, starts, goals, strict=True):
        tick = rng.randint(0, 20)
        table.end_hold(robot, tick)
        stop = Stop(rng.choice([n for n in nodes if n not in starts + goals]), rng.randint(0, 8))
        itinerary = planner.plan(table, start, tick, [stop, Stop(goal)])
        if itinerary is None:
            return None
        table.reserve(robot, itinerary.path)
        # The leading part of the robot's timeline: at its start from tick 0.
        visits = list(itinerary.path.visits)
        visits[0] = Visit(start, 0, visits[0].depart)
        plans[robot] = TimedPath(tuple(visits))
        service_at = next(
            i
            for i, v in enumerate(visits)
            if v.node == stop.node and v.arrive <= itinerary.stop_ticks[0] <= v.depart
        )
        dwell[robot] = [
            stop.service_ticks or 0 if i == service_at else 0 for i in range(len(visits))
        ]
    return plans, dwell


def _fleets() -> list[tuple[int, dict[RobotId, TimedPath], dict[RobotId, list[int]]]]:
    planner = ItineraryPlanner(_times(_grid()))
    fleets = []
    for seed in range(200):
        fleet = _random_fleet(random.Random(seed), planner)
        if fleet is not None:
            fleets.append((seed, *fleet))
    return fleets


FLEETS = _fleets()


def test_random_fleets_are_planned_conflict_free() -> None:
    assert len(FLEETS) > 150
    for seed, plans, _ in FLEETS:
        assert _conflicts(plans) == [], f"seed {seed}"


def test_without_delays_random_plans_are_reproduced_exactly() -> None:
    times = _times(_grid())
    for seed, plans, dwell in FLEETS:
        assert execute_plans(plans, times, policy=ORDERED, min_dwell=dwell) == plans, f"seed {seed}"


def test_with_delays_ordered_execution_is_conflict_free_and_naive_is_not() -> None:
    times = _times(_grid())
    model = DelayModel(0.3, min_ticks=1, max_ticks=15)
    naive_conflicts = 0
    for seed, plans, dwell in FLEETS:
        rng = random.Random(seed)
        delays = {r: [model.draw(rng) for _ in p.visits] for r, p in plans.items()}

        ordered = execute_plans(plans, times, policy=ORDERED, delays=delays, min_dwell=dwell)
        naive = execute_plans(plans, times, policy=NAIVE, delays=delays, min_dwell=dwell)

        assert _conflicts(ordered) == [], f"seed {seed}"
        naive_conflicts += len(_conflicts(naive))
        for robot, actual in ordered.items():
            planned = plans[robot]
            assert actual.nodes == planned.nodes  # same route
            for k, (a, p) in enumerate(zip(actual.visits, planned.visits, strict=True)):
                assert a.arrive >= p.arrive and a.depart >= p.depart  # never early
                assert a.depart >= a.arrive + dwell[robot][k]  # services are done
                if k:
                    ticks = times.edge_ticks(planned.visits[k - 1].node, p.node)
                    assert a.arrive >= actual.visits[k - 1].depart + ticks + delays[robot][k - 1]

    assert naive_conflicts > 0  # without the ordering, delays do cause conflicts


def test_more_delay_never_makes_anything_earlier() -> None:
    times = _times(_grid())
    for seed, plans, dwell in FLEETS[:60]:
        rng = random.Random(seed)
        delays = {r: [rng.choice([0, 0, 5]) for _ in p.visits] for r, p in plans.items()}
        robot = rng.choice(sorted(plans))
        more = {r: list(d) for r, d in delays.items()}
        more[robot][0] += 7

        base = execute_plans(plans, times, policy=ORDERED, delays=delays, min_dwell=dwell)
        later = execute_plans(plans, times, policy=ORDERED, delays=more, min_dwell=dwell)

        for r in plans:
            for a, b in zip(base[r].visits, later[r].visits, strict=True):
                assert b.arrive >= a.arrive and b.depart >= a.depart, f"seed {seed}"
