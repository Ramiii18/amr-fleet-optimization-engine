"""Tests for the synthetic grid-warehouse generator."""

import itertools
import math
from typing import Any

import networkx as nx
import pytest

from amr_optimizer.routing import astar, manhattan, shortest_path
from amr_optimizer.warehouse import (
    PRESETS,
    GridWarehouseSpec,
    MapValidationError,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)
from amr_optimizer.warehouse.generator import aisle_node_id, spread


def to_networkx(graph: WarehouseGraph) -> "nx.DiGraph[NodeId]":
    reference: nx.DiGraph[NodeId] = nx.DiGraph()
    reference.add_nodes_from(node.id for node in graph)
    reference.add_edges_from(edge.key for edge in graph.edges)
    return reference


ALL_LAYOUTS = {
    **PRESETS,
    "small-two-way": GridWarehouseSpec(aisles=4, rows=6, one_way_aisles=False),
    "no-storage": GridWarehouseSpec(aisles=3, rows=3, storage_every=0, parking=1),
    "sparse-storage": GridWarehouseSpec(aisles=2, rows=9, storage_every=3, stations=2, chargers=1),
}


# --- spread -------------------------------------------------------------------------------


def test_spread_examples() -> None:
    assert spread(0, 5) == []
    assert spread(1, 5) == [2]
    assert spread(2, 5) == [0, 4]
    assert spread(3, 7) == [0, 3, 6]
    assert spread(4, 4) == [0, 1, 2, 3]


def test_spread_always_returns_distinct_in_range_indices() -> None:
    for size in range(1, 30):
        for count in range(size + 1):
            indices = spread(count, size)
            assert len(set(indices)) == count
            assert all(0 <= i < size for i in indices)


def test_spread_rejects_too_many() -> None:
    with pytest.raises(ValueError):
        spread(4, 3)


# --- spec validation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"aisles": 1},
        {"rows": 1},
        {"aisle_pitch_m": 0.0},
        {"row_spacing_m": -1.0},
        {"spur_length_m": math.nan},
        {"spur_length_m": 2.0},  # not shorter than half of the 4 m aisle pitch
        {"extra_cross_rows": (0,)},  # bottom row is already a cross aisle
        {"extra_cross_rows": (9,)},  # top row (rows=10) is already a cross aisle
        {"extra_cross_rows": (3, 3)},
        {"storage_every": -1},
        {"stations": 7},  # more than one per aisle (6 aisles)
        {"chargers": -1},
        {"parking": 9},  # only 8 inner rows
    ],
    ids=str,
)
def test_invalid_specs_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(MapValidationError):
        GridWarehouseSpec(**overrides)


# --- generated structure ------------------------------------------------------------------


def test_small_preset_has_the_expected_counts() -> None:
    graph = generate_grid_warehouse(PRESETS["small"])  # 4 aisles x 6 rows

    counts = {kind: len(graph.nodes_of_kind(kind)) for kind in NodeKind}
    assert counts == {
        NodeKind.INTERSECTION: 24,  # 4 x 6 aisle nodes
        NodeKind.STORAGE: 16,  # rows 1-4 of each aisle
        NodeKind.STATION: 2,
        NodeKind.CHARGER: 1,
        NodeKind.PARKING: 2,
    }
    # 4 aisles x 5 one-way segments + 2 cross rows x 3 two-way links + 21 two-way spurs.
    assert graph.num_edges == 20 + 2 * 3 * 2 + 21 * 2


def test_large_preset_is_big_enough_for_the_25_robot_scenario() -> None:
    assert len(generate_grid_warehouse(PRESETS["large"])) >= 300


def test_generation_is_deterministic() -> None:
    first = generate_grid_warehouse(PRESETS["medium"])
    second = generate_grid_warehouse(PRESETS["medium"])

    assert first.nodes == second.nodes
    assert first.edges == second.edges


@pytest.mark.parametrize("name", ALL_LAYOUTS)
def test_every_node_can_reach_every_other_node(name: str) -> None:
    graph = generate_grid_warehouse(ALL_LAYOUTS[name])

    assert nx.is_strongly_connected(to_networkx(graph))


@pytest.mark.parametrize("name", ALL_LAYOUTS)
def test_endpoints_are_dead_end_spurs(name: str) -> None:
    """Well-formed infrastructure: no endpoint lies on a route between other nodes."""
    graph = generate_grid_warehouse(ALL_LAYOUTS[name])
    incoming: dict[NodeId, list[NodeId]] = {node.id: [] for node in graph}
    for edge in graph.edges:
        incoming[edge.target].append(edge.source)

    for node in graph:
        if node.kind is NodeKind.INTERSECTION:
            continue
        outgoing = [edge.target for edge in graph.out_edges(node.id)]
        assert len(outgoing) == 1, node.id
        assert incoming[node.id] == outgoing, node.id
        assert graph.node(outgoing[0]).kind is NodeKind.INTERSECTION, node.id


def test_one_way_aisles_alternate_direction() -> None:
    graph = generate_grid_warehouse(PRESETS["small"])
    up = (aisle_node_id(0, 2), aisle_node_id(0, 3))
    down = (aisle_node_id(1, 3), aisle_node_id(1, 2))

    assert graph.get_edge(*up) is not None
    assert graph.get_edge(*reversed(up)) is None
    assert graph.get_edge(*down) is not None
    assert graph.get_edge(*reversed(down)) is None


@pytest.mark.parametrize("name", ALL_LAYOUTS)
def test_all_edges_are_axis_aligned(name: str) -> None:
    """This is what makes the Manhattan heuristic consistent on generated maps."""
    graph = generate_grid_warehouse(ALL_LAYOUTS[name])

    for edge in graph.edges:
        a, b = graph.node(edge.source), graph.node(edge.target)
        assert a.x == b.x or a.y == b.y, edge
        assert math.isclose(edge.length_m, manhattan(a, b)), edge


def test_manhattan_astar_is_optimal_on_a_generated_map() -> None:
    graph = generate_grid_warehouse(PRESETS["medium"])
    endpoints = [node.id for node in graph if node.kind is not NodeKind.INTERSECTION]

    for source, target in itertools.islice(itertools.permutations(endpoints, 2), 0, None, 37):
        ours = astar(graph, source, target, manhattan)
        reference = shortest_path(graph, source, target)
        assert ours.route is not None
        assert reference.route is not None
        assert ours.route.length_m == pytest.approx(reference.route.length_m)
