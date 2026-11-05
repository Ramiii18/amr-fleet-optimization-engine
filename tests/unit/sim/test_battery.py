"""Tests for batteries and charging in the simulator (D26, Phase 2).

Hand map (well formed; every endpoint a dead-end spur; 0.1 s ticks at 1 m/s)::

             A       B          storage, 1 m spurs
             |       |
     P1 --   W ===== E   -- CH  parking and charger, 1 m spurs; W = E is 2 m
             |       |
             S1      S2         stations, 1 m spurs

The hand battery holds 64 m of driving (64,000 units) and charges fully in 64 s (100 units per
tick). The low threshold, 25% = 16 m, is exactly the map's safety bound (4 x 4 m); the high
threshold is 90% = 57,600 units.
"""

from collections.abc import Sequence

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
from amr_optimizer.sim import Assignment, DispatchSnapshot, SimulationResult, simulate
from amr_optimizer.traffic import TimedPath, Visit, path_conflicts
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

A, B, W, E, S1, S2, P1, CH = (NodeId(n) for n in ("A", "B", "W", "E", "S1", "S2", "P1", "CH"))
R1, R2 = RobotId("R1"), RobotId("R2")


def _hand_map() -> WarehouseGraph:
    nodes = [
        Node(W, 0, 0),
        Node(E, 2, 0),
        Node(A, 0, 1, NodeKind.STORAGE),
        Node(B, 2, 1, NodeKind.STORAGE),
        Node(S1, 0, -1, NodeKind.STATION),
        Node(S2, 2, -1, NodeKind.STATION),
        Node(P1, -1, 0, NodeKind.PARKING),
        Node(CH, 3, 0, NodeKind.CHARGER),
    ]
    links = [(W, E, 2.0), (W, A, 1.0), (W, S1, 1.0), (W, P1, 1.0)]
    links += [(E, B, 1.0), (E, S2, 1.0), (E, CH, 1.0)]
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


def _battery(start_share: float) -> BatterySpec:
    return BatterySpec(
        capacity_m=64.0,
        full_charge_s=64.0,
        start_min_share=start_share,
        start_max_share=start_share,
    )


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


def _conflicts(result: SimulationResult) -> int:
    return len(path_conflicts({r: [p] for r, p in result.trajectories.items()}))


def test_a_robot_that_ends_a_job_low_goes_to_charge_and_returns_to_service() -> None:
    # R1 starts at 30% (19,200 units) and does O1: P1 -> W -> A (load) -> W -> S1 (unload)
    # -> W -> P1, 6 m. It is idle at P1 at tick 70 with 13,200 units, below the low threshold
    # (16,000): out of service. It drives P1 -> W -> E -> CH (4 m) and arrives at tick 110 with
    # 9,200 units, the lowest level of the run. O2 is released meanwhile and waits: charging
    # the 48,400 units to 57,600 takes 484 ticks, so R1 is back in service and takes O2 at 594.
    orders = [TransportOrder(OrderId("O1"), A, S1, 0), TransportOrder(OrderId("O2"), B, S2, 300)]
    scenario = _scenario({R1: P1}, orders)

    result = simulate(scenario, GreedyDispatch(), traffic=True, battery=_battery(0.30))

    assert (
        result.trajectories[R1].visits[:12]
        == _path(
            (P1, 0, 0),
            (W, 10, 10),
            (A, 20, 25),
            (W, 35, 35),
            (S1, 45, 50),
            (W, 60, 60),
            (P1, 70, 70),
            (W, 80, 80),
            (E, 100, 100),
            (CH, 110, 594),
            (E, 604, 604),
            (B, 614, 619),
        ).visits
    )
    assert result.charging_trips == 1
    assert result.lowest_energy_share == pytest.approx(9_200 / 64_000)
    assert result.out_of_service_ticks == 594 - 70
    assert result.charger_wait_ticks == 0
    assert result.all_delivered and _conflicts(result) == 0


def test_the_run_ends_with_the_last_job_not_with_the_last_charge() -> None:
    # As above without O2: nothing is left to do once R1 reaches the charger, so the run ends
    # there (tick 110) instead of at its return to service (594), and throughput and utilization
    # are not diluted by the charging. The time out of service is counted until the end.
    scenario = _scenario({R1: P1}, [TransportOrder(OrderId("O1"), A, S1, 0)])

    result = simulate(scenario, GreedyDispatch(), traffic=True, battery=_battery(0.30))

    assert result.end_tick == 110
    assert result.out_of_service_ticks == 110 - 70


def test_a_charged_robot_on_the_charger_makes_way_for_a_low_one() -> None:
    # R2 starts on the charger, in service. R1 ends its job low (as above) and needs that
    # charger, so R2 is moved away first.
    scenario = _scenario({R1: P1, R2: CH}, [TransportOrder(OrderId("O1"), A, S1, 0)])

    result = simulate(scenario, _OnlyR1(), traffic=True, battery=_battery(0.30))

    assert result.moves_out_of_the_way == 1
    assert result.charging_trips == 1
    assert result.trajectories[R1].end.node == CH
    assert result.trajectories[R2].end.node != CH
    assert _conflicts(result) == 0


class _OnlyR1:
    """Test policy: gives every order to R1, and records when it was asked and which robots it
    was offered."""

    name = "only-r1"

    def __init__(self) -> None:
        self.offered: list[tuple[int, tuple[RobotId, ...]]] = []

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        self.offered.append((snapshot.tick, tuple(r.robot_id for r in snapshot.idle_robots)))
        idle = {r.robot_id for r in snapshot.idle_robots}
        if R1 in idle and snapshot.pending_orders:
            return [Assignment(R1, snapshot.pending_orders[0].id)]
        return []


def test_a_robot_that_starts_low_is_hidden_from_the_policy_until_charged() -> None:
    # R1 starts below the threshold (20% = 12,800 units): it leaves for the charger at tick 0
    # and arrives at tick 40 with 8,800. The order is released at tick 5, but the policy is
    # first asked at tick 528, when R1 has charged the 48,800 units to the high threshold.
    scenario = _scenario({R1: P1}, [TransportOrder(OrderId("O1"), A, S1, 5)])
    policy = _OnlyR1()

    result = simulate(scenario, policy, traffic=True, battery=_battery(0.20))

    assert policy.offered[0] == (528, (R1,))
    assert result.trajectories[R1].visits[:4] == (
        Visit(P1, 0, 0),
        Visit(W, 10, 10),
        Visit(E, 30, 30),
        Visit(CH, 40, 528),
    )
    assert result.out_of_service_ticks == 528
    assert result.all_delivered


def test_a_robot_that_starts_low_on_a_charger_returns_to_service_when_charged() -> None:
    # R1 starts on the charger at 20% and charges the 44,800 units to the high threshold in
    # 448 ticks. Its return to service wakes the simulation: nothing else happens at that tick.
    scenario = _scenario({R1: CH}, [TransportOrder(OrderId("O1"), A, S1, 5)])
    policy = _OnlyR1()

    result = simulate(scenario, policy, traffic=True, battery=_battery(0.20))

    assert policy.offered[0] == (448, (R1,))
    assert result.charging_trips == 0
    assert result.out_of_service_ticks == 448


def test_batteries_need_traffic() -> None:
    scenario = _scenario({R1: P1}, [])

    with pytest.raises(ValueError, match="batteries need traffic"):
        simulate(scenario, GreedyDispatch(), battery=BatterySpec())


# --- whole generated runs ---------------------------------------------------------------------

MAPS = {name: generate_grid_warehouse(PRESETS[name]) for name in ("small", "medium")}
CASES = [(m, r, s) for m in MAPS for r in (3, 6) for s in range(3)]
# Small enough that robots charge within half an hour; the low threshold (300 m) stays above both
# maps' safety bounds.
SMALL_BATTERY = BatterySpec(capacity_m=1200.0)


@pytest.mark.parametrize(("map_name", "robots", "seed"), CASES)
def test_generated_runs_with_batteries_never_run_empty(
    map_name: str, robots: int, seed: int
) -> None:
    spec = ScenarioSpec(robots=robots, orders_per_hour=150, duration_s=1800)
    scenario = generate_scenario(MAPS[map_name], spec, seed=seed)
    policy = InsertionDispatch(objective=Objective.WORST_FIRST)
    battery = SMALL_BATTERY

    result = simulate(scenario, policy, traffic=True, battery=battery, battery_seed=seed)
    again = simulate(
        scenario,
        InsertionDispatch(objective=Objective.WORST_FIRST),
        traffic=True,
        battery=battery,
        battery_seed=seed,
    )

    assert result.all_delivered
    assert _conflicts(result) == 0
    assert result.lowest_energy_share is not None and result.lowest_energy_share > 0
    assert (result.log, result.trajectories) == (again.log, again.trajectories)


def test_robots_do_charge_in_the_generated_runs() -> None:
    trips = 0
    for map_name, robots, seed in CASES:
        spec = ScenarioSpec(robots=robots, orders_per_hour=150, duration_s=1800)
        scenario = generate_scenario(MAPS[map_name], spec, seed=seed)
        result = simulate(
            scenario, GreedyDispatch(), traffic=True, battery=SMALL_BATTERY, battery_seed=seed
        )
        trips += result.charging_trips

    assert trips > len(CASES)  # the tests above exercise charging, not just its absence
