"""Tests for the integer-tick time model and per-edge travel times."""

import dataclasses
import math
import random

import pytest

from amr_optimizer.domain import TimeModel, TravelTimes
from amr_optimizer.routing import InvalidRouteError, Route, shortest_path
from amr_optimizer.warehouse import (
    PRESETS,
    Edge,
    Node,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)

A, B, C, D = NodeId("A"), NodeId("B"), NodeId("C"), NodeId("D")
TENTH = TimeModel(tick_s=0.1)


# --- TimeModel -------------------------------------------------------------------------


@pytest.mark.parametrize("tick_s", [0.0, -0.1, math.nan, math.inf])
def test_tick_length_must_be_positive_and_finite(tick_s: float) -> None:
    with pytest.raises(ValueError, match="tick_s"):
        TimeModel(tick_s)


def test_time_model_is_immutable_and_compares_by_value() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        TENTH.tick_s = 1.0  # type: ignore[misc]
    assert TimeModel(0.1) == TENTH
    assert len({TimeModel(0.1), TENTH}) == 1


@pytest.mark.parametrize(
    ("seconds", "ticks"),
    [(0.0, 0), (2.0, 20), (0.05, 1), (0.1, 1), (0.11, 2), (2.01, 21)],
)
def test_durations_round_up_to_whole_ticks(seconds: float, ticks: int) -> None:
    assert TENTH.to_ticks(seconds) == ticks


def test_floating_point_noise_is_not_rounded_up() -> None:
    # 5.4 m at 1.2 m/s is exactly 4.5 s, i.e. 45 ticks. In floating point the division
    # gives 45.00000000000001, so a plain ceil would charge 46 ticks.
    assert 5.4 / 1.2 / 0.1 > 45
    assert math.ceil(5.4 / 1.2 / 0.1) == 46

    assert TENTH.travel_ticks(5.4, 1.2) == 45
    assert TENTH.to_ticks(0.1 + 0.2) == 3  # 0.30000000000000004 s


def test_every_decimal_duration_at_tick_resolution_is_exact() -> None:
    for k in range(10_001):
        assert TENTH.to_ticks(k / 10) == k


@pytest.mark.parametrize("tick_s", [0.1, 0.05, 0.25, 1 / 3, 0.001])
def test_ticks_to_seconds_and_back_is_the_identity(tick_s: float) -> None:
    model = TimeModel(tick_s)
    for n in range(5_001):
        assert model.to_ticks(model.to_seconds(n)) == n


@pytest.mark.parametrize("seconds", [-0.1, math.nan, math.inf])
def test_invalid_durations_are_rejected(seconds: float) -> None:
    with pytest.raises(ValueError, match="duration"):
        TENTH.to_ticks(seconds)


def test_negative_tick_counts_are_rejected() -> None:
    assert TENTH.to_seconds(20) == pytest.approx(2.0)
    with pytest.raises(ValueError, match="non-negative"):
        TENTH.to_seconds(-1)


@pytest.mark.parametrize(
    ("length_m", "speed_mps", "ticks"),
    [
        (1.0, 1.0, 10),  # exact
        (4.0, 2.0, 20),  # exact
        (1.0, 1.5, 7),  # 6.67 ticks, rounded up
        (1e-12, 1.0, 1),  # far below one tick, still one tick
    ],
)
def test_travel_ticks(length_m: float, speed_mps: float, ticks: int) -> None:
    assert TENTH.travel_ticks(length_m, speed_mps) == ticks


@pytest.mark.parametrize(
    ("length_m", "speed_mps", "name"),
    [
        (0.0, 1.0, "length_m"),
        (-1.0, 1.0, "length_m"),
        (math.nan, 1.0, "length_m"),
        (1.0, 0.0, "speed_mps"),
        (1.0, -1.0, "speed_mps"),
        (1.0, math.inf, "speed_mps"),
    ],
)
def test_travel_ticks_rejects_invalid_input(length_m: float, speed_mps: float, name: str) -> None:
    with pytest.raises(ValueError, match=name):
        TENTH.travel_ticks(length_m, speed_mps)


@pytest.mark.parametrize("seed", range(20))
def test_travel_ticks_never_underestimate_and_overestimate_by_less_than_one_tick(
    seed: int,
) -> None:
    rng = random.Random(seed)
    for _ in range(200):
        length_m = rng.uniform(0.01, 50.0)
        speed_mps = rng.uniform(0.2, 3.0)
        model = TimeModel(rng.choice([0.01, 0.05, 0.1, 0.2, 0.5]))
        ticks = model.travel_ticks(length_m, speed_mps)
        covered_per_tick_m = speed_mps * model.tick_s

        assert ticks >= 1
        assert ticks * covered_per_tick_m >= length_m * (1 - 1e-9)
        assert ticks == 1 or (ticks - 1) * covered_per_tick_m < length_m * (1 + 1e-9)


# --- TravelTimes -----------------------------------------------------------------------


def test_edge_and_route_ticks_on_a_line(line_graph: WarehouseGraph) -> None:
    times = TravelTimes(line_graph, speed_mps=1.0, time_model=TENTH)

    assert times.edge_ticks(A, B) == 10
    assert times.arrival_offsets(Route((A, B, C), 2.0)) == (0, 10, 20)
    assert times.route_ticks(Route((C, B, A), 2.0)) == 20
    assert times.is_exact
    assert (times.speed_mps, times.time_model) == (1.0, TENTH)


def test_single_node_route_takes_no_time(line_graph: WarehouseGraph) -> None:
    times = TravelTimes(line_graph, speed_mps=1.0, time_model=TENTH)

    assert times.arrival_offsets(Route((B,), 0.0)) == (0,)
    assert times.route_ticks(Route((B,), 0.0)) == 0


def test_rounding_accumulates_per_edge(line_graph: WarehouseGraph) -> None:
    # 1 m at 0.3 m/s is 33.3 ticks, rounded up to 34 for each edge. The 2 m route takes
    # 68 ticks, one more than its total length converted at once (66.7 -> 67).
    times = TravelTimes(line_graph, speed_mps=0.3, time_model=TENTH)

    assert times.edge_ticks(A, B) == 34
    assert times.route_ticks(Route((A, B, C), 2.0)) == 68
    assert TENTH.travel_ticks(2.0, 0.3) == 67
    assert not times.is_exact


def test_shortest_route_in_metres_can_be_slower_in_ticks() -> None:
    # A -> B -> D is 2.2 m but 2 + 2 = 4 ticks; the curved direct edge A -> D is 2.5 m but
    # 3 ticks. Routing by length picks the slower route. This is the D12 limitation, and
    # it only arises when some edge needs rounding.
    graph = WarehouseGraph(
        [Node(A, 0.0, 0.0), Node(B, 1.1, 0.0), Node(D, 2.2, 0.0)],
        [Edge(A, B, 1.1), Edge(B, D, 1.1), Edge(A, D, 2.5)],
    )
    times = TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(1.0))

    route = shortest_path(graph, A, D).route
    assert route is not None
    assert route.nodes == (A, B, D)
    assert times.route_ticks(route) == 4
    assert times.edge_ticks(A, D) == 3
    assert not times.is_exact


def test_missing_edges_and_unknown_nodes_are_errors(
    one_way_detour_graph: WarehouseGraph,
) -> None:
    times = TravelTimes(one_way_detour_graph, speed_mps=1.0, time_model=TENTH)

    with pytest.raises(LookupError, match="no edge 'A'->'B'"):
        times.edge_ticks(A, B)
    with pytest.raises(InvalidRouteError, match="non-existent edge 'A'->'B'"):
        times.route_ticks(Route((A, B), 2.0))
    with pytest.raises(InvalidRouteError, match="unknown node 'Z'"):
        times.route_ticks(Route((NodeId("Z"),), 0.0))


@pytest.mark.parametrize("speed_mps", [0.0, -1.0, math.nan])
def test_travel_times_reject_invalid_speed(line_graph: WarehouseGraph, speed_mps: float) -> None:
    with pytest.raises(ValueError, match="speed_mps"):
        TravelTimes(line_graph, speed_mps=speed_mps, time_model=TENTH)


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_generated_maps_are_exact_at_one_metre_per_second(preset: str) -> None:
    # All generated edge lengths are whole decimetres, so at 1 m/s and 0.1 s per tick no
    # edge is rounded, and routing by metres is the same as routing by ticks.
    graph = generate_grid_warehouse(PRESETS[preset])
    times = TravelTimes(graph, speed_mps=1.0, time_model=TENTH)
    assert times.is_exact

    stations = sorted(node.id for node in graph.nodes_of_kind(NodeKind.STATION))
    storage = sorted(node.id for node in graph.nodes_of_kind(NodeKind.STORAGE))
    for source in stations:
        for target in storage[::7]:
            route = shortest_path(graph, source, target).route
            assert route is not None
            assert times.route_ticks(route) == TENTH.to_ticks(route.length_m / 1.0)
