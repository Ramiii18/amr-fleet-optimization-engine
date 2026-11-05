"""Tests for breakdowns and recovery in the simulator (D28, Phase 2).

Hand map: a ring with spurs (0.1 s ticks at 1 m/s; A = B and C = D are 2 m, the rest 1 m)::

    P1, P3 - A ===== B - X1, X3      parking spurs at A, storage spurs at B
             |       |
             C ===== D - S, X2       station and storage spurs at D

Breakdowns in the hand cases are scripted: the k-th robot departure in the run draws the k-th
repair time of the script.
"""

from dataclasses import dataclass, field

import pytest

from amr_optimizer.dispatch import GreedyDispatch, InsertionDispatch, Objective
from amr_optimizer.domain import (
    BatterySpec,
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
)
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import SimulationResult, simulate
from amr_optimizer.traffic import DelayModel, ExecutionPolicy, Recovery, path_conflicts
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

A, B, C, D = (NodeId(n) for n in "ABCD")
P1, P3, X1, X2, X3, S = (NodeId(n) for n in ("P1", "P3", "X1", "X2", "X3", "S"))
R1, R2 = RobotId("R1"), RobotId("R2")
WAIT, RETIME = Recovery.WAIT, Recovery.RETIME


@dataclass(frozen=True, slots=True)
class _Scripted(DelayModel):
    """Breakdowns in the order of departures: the k-th departure draws ``script[k]``."""

    script: list[int] = field(default_factory=list)

    def draw(self, rng: object) -> int:
        return self.script.pop(0) if self.script else 0


def _scripted(*repairs: int) -> _Scripted:
    return _Scripted(1.0, script=list(repairs))


def _ring() -> WarehouseGraph:
    nodes = [
        Node(A, 0, 1),
        Node(B, 2, 1),
        Node(C, 0, 0),
        Node(D, 2, 0),
        Node(P1, -1, 1, NodeKind.PARKING),
        Node(P3, 0, 2, NodeKind.PARKING),
        Node(X1, 3, 1, NodeKind.STORAGE),
        Node(X3, 2, 2, NodeKind.STORAGE),
        Node(X2, 2, -1, NodeKind.STORAGE),
        Node(S, 3, 0, NodeKind.STATION),
    ]
    links = [(A, B, 2.0), (C, D, 2.0), (A, C, 1.0), (B, D, 1.0)]
    links += [(A, P1, 1.0), (A, P3, 1.0), (B, X1, 1.0), (B, X3, 1.0), (D, S, 1.0), (D, X2, 1.0)]
    edges = [Edge(u, v, length) for a, b, length in links for u, v in ((a, b), (b, a))]
    return WarehouseGraph(nodes, edges)


def _scenario(robots: dict[RobotId, NodeId], orders: list[TransportOrder]) -> Scenario:
    return Scenario(
        graph=_ring(),
        time_model=TimeModel(0.1),
        speed_mps=1.0,
        load_ticks=5,
        unload_ticks=5,
        robots=tuple(RobotState.idle(rid, node) for rid, node in robots.items()),
        orders=tuple(orders),
    )


def _conflicts(result: SimulationResult) -> int:
    return len(path_conflicts({r: [p] for r, p in result.trajectories.items()}))


def _pickup_reached(result: SimulationResult, order: str) -> int:
    return next(
        e.tick
        for e in result.log
        if e.order_id == OrderId(order) and e.kind.name == "PICKUP_REACHED"
    )


# --- by hand -------------------------------------------------------------------------------------

# R1 takes O1 at tick 0: P1 -> A -> B -> X1. Its second departure (A -> B, at tick 10) breaks
# down for 300 ticks, so it reaches B at 10 + 20 + 300 = 330. R2, at P3, is assigned O2 (pickup
# X3, at B) when it is released at tick 15. The top corridor is the short way (4 m against 6 m).
ORDERS = [TransportOrder(OrderId("O1"), X1, S, 0), TransportOrder(OrderId("O2"), X3, S, 15)]


def _ring_run(recovery: Recovery) -> SimulationResult:
    scenario = _scenario({R1: P1, R2: P3}, ORDERS)
    return simulate(
        scenario,
        GreedyDispatch(),
        traffic=True,
        breakdowns=_scripted(0, 300),
        recovery=recovery,
    )


def test_a_broken_robot_holds_its_corridor_and_followers_wait() -> None:
    # Without recovery, R2's plan uses the top corridor, as if R1 had left it on time (tick 30):
    # it waits at A until R1 reaches B (tick 330), then drives A -> B -> X3.
    result = _ring_run(WAIT)

    assert (result.breakdowns, result.breakdown_ticks) == (1, 300)
    assert result.trajectories[R1].visits[2].arrive == 330  # at B
    assert _pickup_reached(result, "O2") == 360
    assert result.all_delivered and _conflicts(result) == 0


def test_re_timing_lets_new_plans_avoid_the_broken_robot() -> None:
    # Re-timed at the breakdown, the table shows the top corridor taken until tick 330. R2's plan
    # at tick 15 goes the long way round instead: P3 -> A -> C -> D -> B -> X3, at X3 at tick 75.
    result = _ring_run(RETIME)

    assert [v.node for v in result.trajectories[R2].visits[:6]] == [P3, A, C, D, B, X3]
    assert _pickup_reached(result, "O2") == 75
    assert result.all_delivered and _conflicts(result) == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"breakdowns": DelayModel(0.1, 10, 20)}, "breakdowns need traffic"),
        (
            {
                "traffic": True,
                "breakdowns": DelayModel(0.1, 10, 20),
                "execution": ExecutionPolicy.NAIVE,
            },
            "ordered execution",
        ),
        ({"traffic": True, "recovery": RETIME}, "needs breakdowns"),
    ],
)
def test_breakdown_options_are_validated(kwargs: dict[str, object], message: str) -> None:
    scenario = _scenario({R1: P1}, [])

    with pytest.raises(ValueError, match=message):
        simulate(scenario, GreedyDispatch(), **kwargs)  # type: ignore[arg-type]


# --- generated runs ------------------------------------------------------------------------------

MAPS = {name: generate_grid_warehouse(PRESETS[name]) for name in ("small", "medium")}
CASES = [(m, r, s) for m in MAPS for r in (3, 6) for s in range(3)]
BREAKDOWNS = DelayModel(0.02, min_ticks=100, max_ticks=1200)  # 2% of edges, 10 s to 2 min


@pytest.mark.parametrize("recovery", [WAIT, RETIME])
@pytest.mark.parametrize(("map_name", "robots", "seed"), CASES)
def test_generated_runs_with_breakdowns_deliver_everything_without_conflicts(
    map_name: str, robots: int, seed: int, recovery: Recovery
) -> None:
    spec = ScenarioSpec(robots=robots, orders_per_hour=150, duration_s=1800)
    scenario = generate_scenario(MAPS[map_name], spec, seed=seed)

    def run() -> SimulationResult:
        return simulate(
            scenario,
            InsertionDispatch(objective=Objective.WORST_FIRST),
            traffic=True,
            breakdowns=BREAKDOWNS,
            breakdown_seed=seed,
            recovery=recovery,
        )

    result, again = run(), run()

    assert result.breakdowns > 0
    assert result.all_delivered
    assert _conflicts(result) == 0
    assert (result.log, result.trajectories) == (again.log, again.trajectories)


@pytest.mark.parametrize("seed", range(6))
def test_re_timing_alone_moves_no_robot(seed: int) -> None:
    # Every order is released at tick 0 with a pickup of its own, so every plan of the run is
    # made at tick 0, before any breakdown. Re-timing then changes no plan that anyone follows.
    graph = MAPS["medium"]
    base = generate_scenario(graph, ScenarioSpec(robots=6, orders_per_hour=600), seed=seed)
    orders: list[TransportOrder] = []
    pickups: set[NodeId] = set()
    for order in base.orders:
        if order.pickup not in pickups and len(orders) < 6:
            pickups.add(order.pickup)
            orders.append(TransportOrder(order.id, order.pickup, order.dropoff, 0))
    scenario = Scenario(
        graph=graph,
        time_model=base.time_model,
        speed_mps=base.speed_mps,
        load_ticks=base.load_ticks,
        unload_ticks=base.unload_ticks,
        robots=base.robots,
        orders=tuple(orders),
    )

    def run(recovery: Recovery) -> SimulationResult:
        return simulate(
            scenario,
            GreedyDispatch(),
            traffic=True,
            breakdowns=DelayModel(0.05, min_ticks=50, max_ticks=600),
            breakdown_seed=seed,
            recovery=recovery,
        )

    wait, retimed = run(WAIT), run(RETIME)

    assert wait.breakdowns > 0 and wait.deferred_assignments == 0
    assert retimed.trajectories == wait.trajectories
    assert retimed.log == wait.log


def test_breakdowns_combine_with_batteries() -> None:
    spec = ScenarioSpec(robots=6, orders_per_hour=150, duration_s=1800)
    scenario = generate_scenario(MAPS["medium"], spec, seed=4)

    result = simulate(
        scenario,
        GreedyDispatch(),
        traffic=True,
        battery=BatterySpec(capacity_m=1200.0),
        breakdowns=BREAKDOWNS,
        recovery=RETIME,
    )

    assert result.breakdowns > 0 and result.charging_trips > 0
    assert result.all_delivered and _conflicts(result) == 0
