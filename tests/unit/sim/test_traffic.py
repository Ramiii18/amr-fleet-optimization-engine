"""Tests for the simulator with traffic (D23, D24): hand cases and whole generated runs.

Hand map (well formed: every endpoint is a dead-end spur)::

            A       B          storage, 1 m spurs
            |       |
    P1 --   W ===== E   -- P2  parking, 1 m spurs; W = E is a 2 m two-way corridor
            |       |
            S1      S2         stations, 1 m spurs

At 1 m/s and 0.1 s ticks, a spur takes 10 ticks and the corridor 20. Loading and unloading
take 5 ticks each.
"""

from collections import defaultdict
from collections.abc import Sequence

import pytest

from amr_optimizer.dispatch import GreedyDispatch, InsertionDispatch, Objective
from amr_optimizer.domain import (
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import (
    ORDER_LIFECYCLE,
    Assignment,
    DispatchPolicy,
    DispatchSnapshot,
    EventKind,
    SimulationResult,
    simulate,
)
from amr_optimizer.traffic import TimedPath, Visit, path_conflicts, validate_timed_path
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

A, B, W, E, S1, S2, P1, P2 = (NodeId(n) for n in ("A", "B", "W", "E", "S1", "S2", "P1", "P2"))
R1, R2 = RobotId("R1"), RobotId("R2")
O1, O2 = OrderId("O1"), OrderId("O2")


def _hand_map() -> WarehouseGraph:
    nodes = [
        Node(W, 0, 0),
        Node(E, 2, 0),
        Node(A, 0, 1, NodeKind.STORAGE),
        Node(B, 2, 1, NodeKind.STORAGE),
        Node(S1, 0, -1, NodeKind.STATION),
        Node(S2, 2, -1, NodeKind.STATION),
        Node(P1, -1, 0, NodeKind.PARKING),
        Node(P2, 3, 0, NodeKind.PARKING),
    ]
    links = [(W, E, 2.0), (W, A, 1.0), (W, S1, 1.0), (W, P1, 1.0)]
    links += [(E, B, 1.0), (E, S2, 1.0), (E, P2, 1.0)]
    edges = [Edge(u, v, length) for a, b, length in links for u, v in ((a, b), (b, a))]
    return WarehouseGraph(nodes, edges)


def _scenario(robots: dict[RobotId, NodeId], orders: list[TransportOrder]) -> Scenario:
    return Scenario(
        graph=_hand_map(),
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=5,
        unload_ticks=5,
        robots=tuple(RobotState.idle(rid, node) for rid, node in robots.items()),
        orders=tuple(orders),
    )


class FirstThenGreedy:
    """Test policy: a fixed decision at the first call, greedy dispatch afterwards."""

    name = "fixed-test"

    def __init__(self, first: list[Assignment]) -> None:
        self.first: list[Assignment] | None = first
        self.greedy = GreedyDispatch()

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        if self.first is not None:
            first, self.first = self.first, None
            return first
        return self.greedy.assign(snapshot)


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


def _delivered(result: SimulationResult) -> dict[OrderId, int]:
    return {e.order_id: e.tick for e in result.log if e.kind is EventKind.DELIVERED}


def _conflicts(result: SimulationResult) -> list[str]:
    return [c.kind for c in path_conflicts({r: [p] for r, p in result.trajectories.items()})]


# --- hand cases --------------------------------------------------------------------------------

# R1 at P1 takes O1 from B to S1; R2 at P2 takes O2 from A to S2. Both start at tick 0, so in
# free flow they meet head-on in the corridor during (10, 30).
CROSSING = (
    {R1: P1, R2: P2},
    [TransportOrder(O1, B, S1, 0), TransportOrder(O2, A, S2, 0)],
    [Assignment(R1, O1), Assignment(R2, O2)],
)


def test_without_traffic_the_robots_meet_head_on() -> None:
    robots, orders, decision = CROSSING
    result = simulate(_scenario(robots, orders), FirstThenGreedy(decision))

    assert "head-on" in _conflicts(result)


def test_with_traffic_the_second_robot_waits_until_the_corridor_is_clear() -> None:
    # R1 is planned first and drives in free flow. Its holding spot after delivery is P1:
    # the nearest free endpoint to S1 (A is as near, but it is a pending pickup).
    # R2 cannot be in the corridor before R1 leaves it at tick 30, nor at E at tick 30. So it
    # waits at P2 until 21, reaches E at 31, and goes on. At W it arrives at 76, one tick after
    # R1 has passed. Its holding spot is P2 (B is reserved by R1's pickup at the time).
    robots, orders, decision = CROSSING
    result = simulate(_scenario(robots, orders), FirstThenGreedy(decision), traffic=True)

    assert result.trajectories[R1] == _path(
        (P1, 0, 0),
        (W, 10, 10),
        (E, 30, 30),
        (B, 40, 45),
        (E, 55, 55),
        (W, 75, 75),
        (S1, 85, 90),
        (W, 100, 100),
        (P1, 110, 131),
    )
    assert result.trajectories[R2] == _path(
        (P2, 0, 21),
        (E, 31, 31),
        (W, 51, 51),
        (A, 61, 66),
        (W, 76, 76),
        (E, 96, 96),
        (S2, 106, 111),
        (E, 121, 121),
        (P2, 131, 131),
    )
    assert _delivered(result) == {O1: 90, O2: 111}
    assert _conflicts(result) == []
    assert [r.node for r in result.robots] == [P1, P2] and all(r.is_idle for r in result.robots)


def test_two_robots_delivering_to_one_station_take_turns() -> None:
    # R1 stands on A and R2 on B; both orders go to station S1. R1 is planned first: it loads
    # at A, unloads at S1 during [25, 30] and leaves through W at tick 40 for P1. R2 loads at B,
    # waits at E until 21 so that it reaches W at 41, one tick after R1 has passed, and
    # unloads at S1 during [51, 56]. Its holding spot is P2: A and P1 are taken, B is a
    # pending pickup at the time of planning.
    orders = [TransportOrder(O1, A, S1, 0), TransportOrder(O2, B, S1, 0)]
    scenario = _scenario({R1: A, R2: B}, orders)
    decision = [Assignment(R1, O1), Assignment(R2, O2)]

    free = simulate(scenario, FirstThenGreedy(decision))
    result = simulate(scenario, FirstThenGreedy(decision), traffic=True)

    assert "vertex" in _conflicts(free)  # in free flow both robots end up idle on S1
    assert result.trajectories[R1] == _path(
        (A, 0, 5), (W, 15, 15), (S1, 25, 30), (W, 40, 40), (P1, 50, 96)
    )
    assert result.trajectories[R2] == _path(
        (B, 0, 5),
        (E, 15, 21),
        (W, 41, 41),
        (S1, 51, 56),
        (W, 66, 66),
        (E, 86, 86),
        (P2, 96, 96),
    )
    assert _delivered(result) == {O1: 30, O2: 56}
    assert _conflicts(result) == []


def test_an_idle_robot_on_an_assigned_pickup_moves_out_of_the_way() -> None:
    # R1 stands idle on A. R2 is given O1, which picks up at A: R1 first moves to the nearest
    # free endpoint (P1; S1 is a station and P2 is held by R2), then R2 is planned around it.
    scenario = _scenario({R1: A, R2: P2}, [TransportOrder(O1, A, S2, 0)])

    result = simulate(scenario, FirstThenGreedy([Assignment(R2, O1)]), traffic=True)

    assert result.moves_out_of_the_way == 1 and result.deferred_assignments == 0
    assert result.trajectories[R1] == _path((A, 0, 0), (W, 10, 10), (P1, 20, 110))
    assert result.trajectories[R2] == _path(
        (P2, 0, 0),
        (E, 10, 10),
        (W, 30, 30),
        (A, 40, 45),
        (W, 55, 55),
        (E, 75, 75),
        (S2, 85, 90),
        (E, 100, 100),
        (B, 110, 110),
    )
    assert _delivered(result) == {O1: 90}
    assert _conflicts(result) == []


def test_two_robots_on_each_others_pickups_are_untangled() -> None:
    # R1 stands on A and is given O1 (pickup B); R2 stands on B and is given O2 (pickup A).
    # Each would have to wait for the other to leave first. One assignment is postponed and
    # that robot moves out of the way; its order is assigned again later.
    scenario = _scenario(
        {R1: A, R2: B}, [TransportOrder(O1, B, S1, 0), TransportOrder(O2, A, S2, 0)]
    )
    decision = [Assignment(R1, O1), Assignment(R2, O2)]

    result = simulate(scenario, FirstThenGreedy(decision), traffic=True)

    assert (result.deferred_assignments, result.moves_out_of_the_way) == (1, 1)
    assert result.all_delivered
    assert _conflicts(result) == []
    assigned = {e.order_id: e.robot_id for e in result.log if e.kind is EventKind.ASSIGNED}
    assert assigned[O2] == R2


# --- whole generated runs -------------------------------------------------------------------------

MAPS = {name: generate_grid_warehouse(PRESETS[name]) for name in ("small", "medium")}
POLICY_NAMES = ("greedy", "insertion-fair")


def _policy(name: str) -> DispatchPolicy:
    if name == "greedy":
        return GreedyDispatch()
    return InsertionDispatch(objective=Objective.WORST_FIRST)


def _position(path: TimedPath, tick: int) -> NodeId | None:
    for visit in path.visits:
        if visit.arrive <= tick <= visit.depart:
            return visit.node
    return None


CASES = [(m, r, s, p) for m in MAPS for r in (1, 3, 6) for s in range(3) for p in POLICY_NAMES]


@pytest.mark.parametrize(("map_name", "robots", "seed", "policy"), CASES)
def test_generated_runs_with_traffic_are_conflict_free_and_complete(
    map_name: str, robots: int, seed: int, policy: str
) -> None:
    spec = ScenarioSpec(robots=robots, orders_per_hour=150, duration_s=1200)
    scenario = generate_scenario(MAPS[map_name], spec, seed=seed)
    times = TravelTimes(
        scenario.graph, speed_mps=scenario.speed_mps, time_model=scenario.time_model
    )

    result = simulate(scenario, _policy(policy), traffic=True)
    again = simulate(scenario, _policy(policy), traffic=True)

    assert result.all_delivered
    assert _conflicts(result) == []
    assert (result.log, result.trajectories) == (again.log, again.trajectories)  # reproducible
    for path in result.trajectories.values():
        validate_timed_path(path, times)
    by_order: dict[OrderId, list[EventKind]] = defaultdict(list)
    for entry in result.log:
        by_order[entry.order_id].append(entry.kind)
        if entry.robot_id is not None:  # the log and the trajectories agree on positions
            assert _position(result.trajectories[entry.robot_id], entry.tick) == entry.node
    assert all(tuple(kinds) == ORDER_LIFECYCLE for kinds in by_order.values())
    assert all(robot.is_idle for robot in result.robots)


@pytest.mark.parametrize("map_name", list(MAPS))
def test_free_flow_trajectories_agree_with_the_log(map_name: str) -> None:
    spec = ScenarioSpec(robots=6, orders_per_hour=150, duration_s=1200)
    scenario = generate_scenario(MAPS[map_name], spec, seed=0)
    times = TravelTimes(
        scenario.graph, speed_mps=scenario.speed_mps, time_model=scenario.time_model
    )

    result = simulate(scenario, GreedyDispatch())

    assert not result.traffic
    for path in result.trajectories.values():
        validate_timed_path(path, times)
    for entry in result.log:
        if entry.robot_id is not None:
            assert _position(result.trajectories[entry.robot_id], entry.tick) == entry.node
