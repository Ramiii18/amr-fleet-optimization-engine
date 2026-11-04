"""Tests for seeded scenario generation."""

import math
import random

import pytest

from amr_optimizer.domain import TimeModel, TransportOrder
from amr_optimizer.scenario import (
    ScenarioSpec,
    ScenarioValidationError,
    generate_orders,
    generate_scenario,
    place_robots,
)
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

SMALL = generate_grid_warehouse(PRESETS["small"])
LARGE = generate_grid_warehouse(PRESETS["large"])
TENTH = TimeModel(0.1)


def _ids(graph: WarehouseGraph, kind: NodeKind) -> set[NodeId]:
    return {node.id for node in graph.nodes_of_kind(kind)}


def test_pinned_scenario_is_reproduced_exactly() -> None:
    # Recorded from a run on Windows with Python 3.13. CI runs this test on Linux too, so
    # it checks that a seed gives the same scenario on both platforms. If the generator is
    # changed on purpose, these values must be re-recorded.
    spec = ScenarioSpec(robots=4, orders_per_hour=120, duration_s=600)
    scenario = generate_scenario(SMALL, spec, seed=7)

    assert len(scenario.orders) == 24
    assert [(o.id, o.pickup, o.dropoff, o.release_tick) for o in scenario.orders[:4]] == [
        ("O00001", "S01-02", "ST01", 675),
        ("O00002", "S01-03", "ST00", 815),
        ("O00003", "S01-02", "ST01", 937),
        ("O00004", "S02-02", "ST00", 958),
    ]
    assert [(r.robot_id, r.node) for r in scenario.robots] == [
        ("R01", "PK00"),
        ("R02", "PK01"),
        ("R03", "CH00"),
        ("R04", "S01-01"),
    ]
    assert (scenario.load_ticks, scenario.unload_ticks) == (100, 100)


def test_same_seed_same_scenario_and_different_seed_different_orders() -> None:
    spec = ScenarioSpec(robots=15, orders_per_hour=90, duration_s=1800)
    first = generate_scenario(LARGE, spec, seed=3)
    again = generate_scenario(LARGE, spec, seed=3)
    other = generate_scenario(LARGE, spec, seed=4)

    assert (first.orders, first.robots) == (again.orders, again.robots)
    assert first.orders != other.orders
    assert first.robots != other.robots  # the 4 robots beyond the 11 homes are drawn


def test_fleet_size_does_not_change_the_order_stream() -> None:
    one = generate_scenario(LARGE, ScenarioSpec(robots=1), seed=11)
    many = generate_scenario(LARGE, ScenarioSpec(robots=25), seed=11)

    assert one.orders == many.orders
    assert len(many.robots) == 25


def test_orders_are_retrievals_released_inside_the_horizon() -> None:
    duration_ticks = 18_000
    orders = generate_orders(
        LARGE,
        orders_per_hour=300,
        duration_ticks=duration_ticks,
        time_model=TENTH,
        rng=random.Random(5),
    )

    assert orders
    assert {o.pickup for o in orders} <= _ids(LARGE, NodeKind.STORAGE)
    assert {o.dropoff for o in orders} == _ids(LARGE, NodeKind.STATION)  # all 6 used
    assert all(0 <= o.release_tick < duration_ticks for o in orders)
    assert all(a.release_tick < b.release_tick for a, b in zip(orders, orders[1:], strict=False))
    assert [o.id for o in orders] == [f"O{i:05d}" for i in range(1, len(orders) + 1)]


def test_order_count_matches_the_arrival_rate() -> None:
    # 120 orders per hour for 30 minutes: 60 expected. Over 100 seeds the mean count must
    # lie within 3 standard errors of 60 (binomial variance n p (1 - p) per run).
    ticks, rate = 18_000, 120.0
    p = rate / 3600 * TENTH.tick_s
    counts = [
        len(
            generate_orders(
                SMALL,
                orders_per_hour=rate,
                duration_ticks=ticks,
                time_model=TENTH,
                rng=random.Random(seed),
            )
        )
        for seed in range(100)
    ]
    standard_error = math.sqrt(ticks * p * (1 - p) / len(counts))

    assert abs(sum(counts) / len(counts) - ticks * p) < 3 * standard_error


def test_more_than_one_order_per_tick_is_rejected() -> None:
    with pytest.raises(ScenarioValidationError, match="more than one order per tick"):
        generate_orders(
            SMALL,
            orders_per_hour=40_000,
            duration_ticks=10,
            time_model=TENTH,
            rng=random.Random(0),
        )


def test_map_without_stations_cannot_generate_orders() -> None:
    graph = WarehouseGraph(
        [Node(NodeId("A"), 0, 0, NodeKind.STORAGE), Node(NodeId("B"), 1, 0, NodeKind.STORAGE)],
        [Edge(NodeId("A"), NodeId("B"), 1.0)],
    )
    with pytest.raises(ScenarioValidationError, match="at least one storage location and station"):
        generate_orders(
            graph, orders_per_hour=60, duration_ticks=10, time_model=TENTH, rng=random.Random(0)
        )


def test_robots_fill_parking_then_chargers_then_random_storage() -> None:
    robots = place_robots(SMALL, 6, random.Random(0))

    assert [r.robot_id for r in robots] == ["R01", "R02", "R03", "R04", "R05", "R06"]
    assert [r.node for r in robots[:3]] == ["PK00", "PK01", "CH00"]
    assert {r.node for r in robots[3:]} <= _ids(SMALL, NodeKind.STORAGE)
    assert len({r.node for r in robots}) == 6
    assert all(r.is_idle and r.since_tick == 0 for r in robots)


def test_too_many_robots_for_the_map_are_rejected() -> None:
    # small: 2 parking + 1 charger + 16 storage locations = room for 19 robots
    assert len(place_robots(SMALL, 19, random.Random(0))) == 19
    with pytest.raises(ScenarioValidationError, match="room for 19 robots, not 20"):
        place_robots(SMALL, 20, random.Random(0))


@pytest.mark.parametrize(
    ("changes", "name"),
    [
        ({"robots": 0}, "robots"),
        ({"orders_per_hour": 0.0}, "orders_per_hour"),
        ({"duration_s": math.nan}, "duration_s"),
        ({"speed_mps": -1.0}, "speed_mps"),
        ({"tick_s": math.inf}, "tick_s"),
        ({"load_s": -1.0}, "load_s"),
        ({"unload_s": math.nan}, "unload_s"),
    ],
)
def test_invalid_specs_are_rejected(changes: dict[str, float], name: str) -> None:
    with pytest.raises(ScenarioValidationError, match=name):
        ScenarioSpec(**changes)  # type: ignore[arg-type]


def test_generated_scenarios_are_valid_on_every_preset() -> None:
    for preset in PRESETS.values():
        graph = generate_grid_warehouse(preset)
        scenario = generate_scenario(graph, ScenarioSpec(robots=3, duration_s=600), seed=1)
        assert all(isinstance(order, TransportOrder) for order in scenario.orders)
        assert scenario.time_model == TENTH
