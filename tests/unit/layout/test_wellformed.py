"""The well-formedness checker (D39): every rule broken on purpose, and the maps that pass."""

from pathlib import Path

import pytest

from amr_optimizer.domain import (
    BatterySpec,
    EnergyModel,
    RobotId,
    RobotState,
    TimeModel,
    TravelTimes,
    safe_low_m,
)
from amr_optimizer.layout import (
    Layout,
    NotWellFormedError,
    WellFormedReport,
    check_well_formed,
    compile_layout,
    holding_kinds,
    holding_spots_needed,
    load_map_file,
    require_well_formed,
)
from amr_optimizer.scenario import Scenario
from amr_optimizer.sim.traffic_control import TrafficControl, TrafficPlanningError
from amr_optimizer.traffic import FOREVER, TimedPath, Visit
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((ROOT / "layouts" / "examples").glob("*.layout.json"))


def rules(report: WellFormedReport) -> list[str]:
    return [violation.rule for violation in report.violations]


def only(report: WellFormedReport, rule: str) -> tuple[NodeId, ...]:
    """The nodes of the single violation of ``rule``, checking that no other rule fired."""
    assert rules(report) == [rule], report.describe()
    return report.violations[0].nodes


# --- maps that pass -----------------------------------------------------------------------------

# The largest fleet each preset guarantees a free holding spot for: 3N - 1 <= holding spots.
PRESET_FLEETS = {"small": (19, 6), "medium": (79, 26), "large": (215, 72)}


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_is_well_formed(name: str) -> None:
    graph = generate_grid_warehouse(PRESETS[name])
    spots, fleet = PRESET_FLEETS[name]
    assert sum(node.kind in holding_kinds(batteries=False) for node in graph) == spots
    assert check_well_formed(graph).ok
    assert check_well_formed(graph, robots=fleet).ok
    assert rules(check_well_formed(graph, robots=fleet + 1)) == ["holding-spots"]
    assert check_well_formed(graph, robots=5, battery=BatterySpec()).ok


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.name)
def test_every_example_layout_is_well_formed(path: Path) -> None:
    assert require_well_formed(load_map_file(path), robots=2).ok


def test_the_robots_used_in_published_experiments_fit_their_maps() -> None:
    """The lab's committed families: 8 robots on medium, 10 on large."""
    assert check_well_formed(generate_grid_warehouse(PRESETS["medium"]), robots=8).ok
    assert check_well_formed(generate_grid_warehouse(PRESETS["large"]), robots=10).ok


# --- each rule, broken on purpose ---------------------------------------------------------------


def test_an_endpoint_between_two_floor_cells_is_named() -> None:
    graph = compile_layout(Layout(("....", "#S#T", "#..#"), 1.0))
    nodes = only(check_well_formed(graph), "leaf-endpoints")
    assert nodes[0] == "S-r001-c001"
    assert set(nodes[1:]) == {"X-r000-c001", "X-r002-c001"}
    message = check_well_formed(graph).violations[0].message
    assert "endpoint S-r001-c001 (storage) has 2 neighbours (X-r000-c001, X-r002-c001)" in message


def test_an_endpoint_with_no_connection_is_named() -> None:
    graph = compile_layout(Layout(("S#T", "#..", "...")))
    report = check_well_formed(graph)
    assert "leaf-endpoints" in rules(report)
    (leaf,) = [v for v in report.violations if v.rule == "leaf-endpoints"]
    assert leaf.nodes == ("S-r000-c000",)
    assert "has no connection" in leaf.message


def hand_graph(*spur: Edge) -> WarehouseGraph:
    """A two-way floor pair X0-X1 with a station spur on X1, plus the storage spur given."""
    nodes = [
        Node(NodeId("X0"), 0.0, 0.0),
        Node(NodeId("X1"), 1.0, 0.0),
        Node(NodeId("T"), 1.0, -1.0, NodeKind.STATION),
        Node(NodeId("S"), 0.0, 1.0, NodeKind.STORAGE),
        Node(NodeId("P"), 0.0, 2.0, NodeKind.PARKING),
    ]
    floor = [Edge(NodeId("X0"), NodeId("X1"), 1.0), Edge(NodeId("X1"), NodeId("X0"), 1.0)]
    station = [Edge(NodeId("T"), NodeId("X1"), 1.0), Edge(NodeId("X1"), NodeId("T"), 1.0)]
    parking = [Edge(NodeId("P"), NodeId("S"), 1.0), Edge(NodeId("S"), NodeId("P"), 1.0)]
    return WarehouseGraph(nodes, [*floor, *station, *parking, *spur])


def test_a_one_way_spur_and_an_endpoint_on_an_endpoint_are_named() -> None:
    one_way_in = hand_graph(Edge(NodeId("X0"), NodeId("S"), 1.0))
    report = check_well_formed(one_way_in)
    messages = {str(v.nodes[0]): v.message for v in report.violations if v.rule == "leaf-endpoints"}
    assert "has 2 neighbours (P, X0)" in messages["S"]
    assert "is joined to another endpoint, S" in messages["P"]

    lone = WarehouseGraph(
        [Node(NodeId("X0"), 0.0, 0.0), Node(NodeId("S"), 0.0, 1.0, NodeKind.STORAGE)],
        [Edge(NodeId("X0"), NodeId("S"), 1.0)],
    )
    (leaf,) = [v for v in check_well_formed(lone).violations if v.rule == "leaf-endpoints"]
    assert leaf.nodes == ("S", "X0")
    assert "joined to X0 one way only: robots could not get out" in leaf.message


def test_a_one_way_dead_end_splits_the_map() -> None:
    graph = compile_layout(Layout(("S#T", ".>.")))
    nodes = only(check_well_formed(graph), "strongly-connected")
    assert "X-r001-c001" in nodes
    message = check_well_formed(graph).violations[0].message
    assert "splits into 3 parts" in message
    assert "X-r001-c001" in message


@pytest.mark.parametrize(
    ("grid", "missing"),
    [
        (("S", "."), "no station"),
        (("T", "."), "no storage"),
        (("C", "."), "no storage and no station"),
    ],
)
def test_a_map_without_storage_or_stations_is_refused(grid: tuple[str, ...], missing: str) -> None:
    report = check_well_formed(compile_layout(Layout(grid)))
    assert rules(report) == ["storage-and-stations"]
    assert missing in report.violations[0].message


def test_too_few_holding_spots_for_the_fleet() -> None:
    graph = load_map_file(ROOT / "layouts" / "examples" / "two_aisles.layout.json")
    # Holding spots: 4 storage, 1 charger, 1 parking = 6; with batteries the charger does not count.
    assert check_well_formed(graph, robots=2).ok  # needs 5
    report = check_well_formed(graph, robots=3)  # needs 8
    assert rules(report) == ["holding-spots"]
    assert report.violations[0].message == (
        "3 robots need at least 8 holding spots (charger, parking, storage) so that one is "
        "always free; the map has 6"
    )
    assert check_well_formed(graph, robots=2, dropoffs_hold=True).ok  # needs 6
    assert check_well_formed(graph, robots=2, battery=BatterySpec()).ok  # 5 spots, needs 5
    with_batteries = check_well_formed(graph, robots=2, battery=BatterySpec(), dropoffs_hold=True)
    assert rules(with_batteries) == ["holding-spots"]
    assert "(parking, storage)" in with_batteries.violations[0].message


def test_batteries_need_a_charger() -> None:
    graph = compile_layout(Layout(("S#T", "...")))
    assert check_well_formed(graph).ok
    assert rules(check_well_formed(graph, battery=BatterySpec())) == ["chargers"]


def test_a_map_too_long_for_the_low_threshold_names_its_farthest_endpoints() -> None:
    """301 m between storage and station: four of them are 1,204 m, above the default 900 m."""
    graph = compile_layout(Layout(("S" + "#" * 298 + "T", "." * 300, "C" + "#" * 299)))
    report = check_well_formed(graph, battery=BatterySpec())
    nodes = only(report, "battery-threshold")
    assert nodes == ("S-r000-c000", "ST-r000-c299")
    assert "(900 m of driving)" in report.violations[0].message
    assert "(1204 m;" in report.violations[0].message


@pytest.mark.parametrize("length", [100, 224, 225, 226, 300])
def test_the_battery_rule_agrees_with_the_energy_model(length: int) -> None:
    """The checker refuses exactly the maps `EnergyModel` refuses."""
    graph = compile_layout(
        Layout(("S" + "#" * (length - 2) + "T", "." * length, "C" + "#" * (length - 1)))
    )
    refused = "battery-threshold" in rules(check_well_formed(graph, battery=BatterySpec()))
    try:
        EnergyModel(BatterySpec(), graph, TimeModel(0.1))
    except ValueError:
        assert refused, safe_low_m(graph)
    else:
        assert not refused, safe_low_m(graph)


def test_every_violation_is_listed_and_raised() -> None:
    graph = compile_layout(Layout((".S.", "T##")))
    report = check_well_formed(graph, robots=1)
    assert rules(report) == ["leaf-endpoints", "holding-spots"]
    with pytest.raises(NotWellFormedError) as raised:
        require_well_formed(graph, robots=1)
    assert raised.value.report == report
    text = str(raised.value)
    assert text.startswith("the map is not fit for traffic (2 problem(s)):")
    assert "[leaf-endpoints] endpoint S-r000-c001" in text
    assert "[holding-spots] 1 robots need at least 2 holding spots" in text


# --- the holding-spot bound ---------------------------------------------------------------------


def test_the_bound_is_three_per_other_robot_plus_two() -> None:
    assert [holding_spots_needed(n) for n in (1, 2, 3, 10)] == [2, 5, 8, 29]
    assert [holding_spots_needed(n, dropoffs_hold=True) for n in (1, 2, 3)] == [2, 6, 10]
    with pytest.raises(ValueError, match="at least 1"):
        holding_spots_needed(0)


def blocked_state(storage: int) -> tuple[TrafficControl, RobotState]:
    """Two robots on a row of ``storage`` storage spurs above a two-way aisle, a station below.

    R02 stands on the second storage cell and has planned a job: it waits there until tick 50
    (its start), loads at the third storage cell from tick 80 (its pickup), unloads at the
    station, and ends holding the fourth storage cell. R01 stands on the first storage cell."""
    graph = compile_layout(Layout(("S" * storage, "." * storage, "T" + "#" * (storage - 1))))
    s = [NodeId(f"S-r000-c{c:03d}") for c in range(storage)]
    x = [NodeId(f"X-r001-c{c:03d}") for c in range(storage)]
    station = NodeId("ST-r002-c000")
    r1, r2 = RobotState.idle(RobotId("R01"), s[0]), RobotState.idle(RobotId("R02"), s[1])
    scenario = Scenario(graph, TimeModel(0.1), 1.0, 100, 100, (r1, r2), ())
    traffic = TrafficControl(scenario, TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1)))
    traffic.table.end_hold(r2.robot_id, 0)
    visits = [
        Visit(s[1], 0, 50),  # waiting at its start
        Visit(x[1], 60, 60),
        Visit(x[2], 70, 70),
        Visit(s[2], 80, 180),  # loading at its pickup
        Visit(x[2], 190, 190),
        Visit(x[1], 200, 200),
        Visit(x[0], 210, 210),
        Visit(station, 220, 320),  # unloading
        Visit(x[0], 330, 330),
        Visit(x[1], 340, 340),
        Visit(x[2], 350, 350),
        Visit(x[3], 360, 360),
        Visit(s[3], 370, FOREVER),  # its final hold
    ]
    traffic.table.reserve(r2.robot_id, TimedPath(tuple(visits)))
    return traffic, r1


def test_one_busy_robot_can_block_three_holding_spots() -> None:
    """With 2 robots, 3N - 2 = 4 holding spots can all be unavailable to a robot that must move:
    its own, and the other robot's start, pickup and final hold. With 3N - 1 = 5 it finds one.
    The state is built directly in the reservation table; that a simulation reaches it is
    plausible (SIPP plans waits at the start), not shown."""
    traffic, r1 = blocked_state(storage=4)
    with pytest.raises(TrafficPlanningError, match="no free holding spot"):
        traffic.plan_move(r1, 10, frozenset())
    traffic, r1 = blocked_state(storage=5)
    move = traffic.plan_move(r1, 10, frozenset())
    assert move.path.end.node == "S-r000-c004"
