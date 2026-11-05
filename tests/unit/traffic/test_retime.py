"""Tests for re-timing plans mid-run (D28).

Hand cases use a line N0 - N1 - N2 - N3 of two-way 1 m corridors: 10 ticks each at 1 m/s and
0.1 s ticks. The oracle cuts an ordered execution of random SIPP-planned fleets at a tick, and
checks that re-timing the rest from the state at that tick reproduces it.
"""

import random

import pytest

from amr_optimizer.domain import RobotId, TimeModel, TravelTimes
from amr_optimizer.traffic import (
    FOREVER,
    Corridor,
    DelayModel,
    ExecutionPolicy,
    ItineraryPlanner,
    Progress,
    ReservationTable,
    Resource,
    Stop,
    TimedPath,
    Visit,
    execute_plans,
    path_conflicts,
    retime,
)
from amr_optimizer.warehouse import Edge, Node, NodeId, WarehouseGraph

N = [NodeId(f"N{i}") for i in range(4)]
R1, R2 = RobotId("R1"), RobotId("R2")


def _times(graph: WarehouseGraph) -> TravelTimes:
    return TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1))


def _line() -> WarehouseGraph:
    nodes = [Node(n, float(i), 0.0) for i, n in enumerate(N)]
    edges = [Edge(N[i], N[j], 1.0) for i in range(3) for i, j in ((i, i + 1), (i + 1, i))]
    return WarehouseGraph(nodes, edges)


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


# --- by hand -------------------------------------------------------------------------------------

# R1 drives N1 -> N2 -> N3 from tick 0. R2 follows one corridor behind: N0 -> N1 -> N2.
FOLLOW = {
    R1: _path((N[1], 0, 0), (N[2], 10, 10), (N[3], 20, FOREVER)),
    R2: _path((N[0], 0, 0), (N[1], 10, 10), (N[2], 20, FOREVER)),
}


def test_on_schedule_nothing_changes() -> None:
    progress = {R1: Progress(0, 0, ready=10), R2: Progress(0, 0, ready=10)}

    result = retime(FOLLOW, progress, _times(_line()), now=5, left_at={N[1]: 0, N[0]: 0})

    assert result == FOLLOW


def test_a_breakdown_ahead_makes_the_follower_wait_for_the_repair() -> None:
    # At tick 5 both robots are in their first corridor. R1 has broken down in N1 -> N2 and will
    # reach N2 only at tick 60. R2 reaches N1 on time, but may enter N1 -> N2 only once R1 has
    # left it (tick 60), and N2 only one tick after R1 has left it (R1 leaves at 60).
    progress = {R1: Progress(0, 0, ready=60), R2: Progress(0, 0, ready=10)}

    result = retime(FOLLOW, progress, _times(_line()), now=5, left_at={N[1]: 0, N[0]: 0})

    assert result[R1] == _path((N[1], 0, 0), (N[2], 60, 60), (N[3], 70, FOREVER))
    assert result[R2] == _path((N[0], 0, 0), (N[1], 10, 60), (N[2], 70, FOREVER))


def test_service_and_now_hold_robots_standing_at_a_node() -> None:
    # R2 stands at N1, arrived at tick 3 and serving until tick 25; nothing happens before
    # tick 12. A later service of 4 ticks at N2 delays its departure from there.
    plans = {R2: _path((N[1], 3, 10), (N[2], 20, 20), (N[3], 30, FOREVER))}
    progress = {R2: Progress(3, ready=25)}

    result = retime(plans, progress, _times(_line()), now=12, min_dwell={R2: {1: 4}})

    assert result[R2] == _path((N[1], 3, 25), (N[2], 35, 39), (N[3], 49, FOREVER))


def test_a_node_needs_a_tick_of_clearance_after_the_last_robot_left() -> None:
    plans = {R2: _path((N[0], 0, 0), (N[1], 10, FOREVER))}
    progress = {R2: Progress(0, 0, ready=10)}

    result = retime(plans, progress, _times(_line()), now=5, left_at={N[1]: 12})

    assert result[R2] == _path((N[0], 0, 0), (N[1], 13, FOREVER))


# --- oracle: cutting an ordered execution and re-timing the rest ---------------------------------


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


def _cut(
    plans: dict[RobotId, TimedPath],
    actual: dict[RobotId, TimedPath],
    delays: dict[RobotId, list[int]],
    dwell: dict[RobotId, list[int]],
    times: TravelTimes,
    now: int,
) -> tuple[
    dict[RobotId, TimedPath],
    dict[RobotId, Progress],
    dict[Resource, int],
    dict[RobotId, dict[int, int]],
    dict[RobotId, int],
]:
    """The state of an ordered execution at ``now``: every event before ``now`` has happened."""
    remaining, progress, later_dwell, cuts = {}, {}, {}, {}
    left_at: dict[Resource, int] = {}
    for robot, path in actual.items():
        visits = path.visits
        c = max([i for i, v in enumerate(visits) if v.arrive < now], default=0)
        in_corridor = c + 1 < len(visits) and visits[c].depart < now
        if in_corridor:
            ticks = times.edge_ticks(visits[c].node, visits[c + 1].node)
            ready = visits[c].depart + ticks + delays[robot][c]
            progress[robot] = Progress(visits[c].arrive, visits[c].depart, ready)
        else:
            progress[robot] = Progress(visits[c].arrive, ready=visits[c].arrive + dwell[robot][c])
        remaining[robot] = TimedPath(plans[robot].visits[c:])
        later_dwell[robot] = {i - c: dwell[robot][i] for i in range(c + 1, len(visits))}
        cuts[robot] = c
        for i, visit in enumerate(visits):  # resources this robot is done with
            if visit.depart < now:
                left_at[visit.node] = max(left_at.get(visit.node, -1), visit.depart)
            if i + 1 < len(visits) and visits[i + 1].arrive < now:
                corridor: Resource = Corridor.between(visit.node, visits[i + 1].node)
                left_at[corridor] = max(left_at.get(corridor, -1), visits[i + 1].arrive)
    return remaining, progress, left_at, later_dwell, cuts


def _fleets() -> list[tuple[int, dict[RobotId, TimedPath], dict[RobotId, list[int]]]]:
    planner = ItineraryPlanner(_times(_grid()))
    fleets = []
    for seed in range(200):
        fleet = _random_fleet(random.Random(seed), planner)
        if fleet is not None:
            fleets.append((seed, *fleet))
    return fleets


FLEETS = _fleets()


@pytest.mark.parametrize("probability", [0.0, 0.3])
def test_re_timing_at_any_tick_reproduces_ordered_execution(probability: float) -> None:
    times = _times(_grid())
    model = DelayModel(probability, min_ticks=1, max_ticks=40) if probability else None
    checked = 0
    for seed, plans, dwell in FLEETS:
        rng = random.Random(seed)
        delays = {r: [model.draw(rng) if model else 0 for _ in p.visits] for r, p in plans.items()}
        actual = execute_plans(
            plans, times, policy=ExecutionPolicy.ORDERED, delays=delays, min_dwell=dwell
        )
        last = max(v.depart for p in actual.values() for v in p.visits if v.depart < FOREVER)
        for now in sorted(rng.sample(range(last + 5), 3)):
            # The future without further delays: only edges entered before the cut are late.
            known = {
                r: [d if p.visits[k].depart < now else 0 for k, d in enumerate(delays[r])]
                for r, p in actual.items()
            }
            expected = execute_plans(
                plans, times, policy=ExecutionPolicy.ORDERED, delays=known, min_dwell=dwell
            )
            remaining, progress, left_at, later, cuts = _cut(
                plans, actual, delays, dwell, times, now
            )

            result = retime(remaining, progress, times, now=now, left_at=left_at, min_dwell=later)

            for robot, path in result.items():
                assert path.visits == expected[robot].visits[cuts[robot] :], (
                    f"seed {seed}, tick {now}, robot {robot}"
                )
            assert path_conflicts({r: [p] for r, p in result.items()}) == []
            checked += 1
    assert checked > 450
