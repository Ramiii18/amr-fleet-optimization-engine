"""Tests for timed paths and their occupancies.

``line_graph``: A - B - C, 1 m apart; at 1 m/s and 0.1 s ticks each edge takes 10 ticks.
"""

import pytest

from amr_optimizer.domain import TimeModel, TravelTimes
from amr_optimizer.traffic import (
    FOREVER,
    Corridor,
    InvalidTimedPathError,
    Occupancy,
    TimedPath,
    Visit,
    validate_timed_path,
)
from amr_optimizer.warehouse import NodeId, WarehouseGraph

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")


def _times(graph: WarehouseGraph) -> TravelTimes:
    return TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1))


def test_a_path_occupies_nodes_closed_and_corridors_open() -> None:
    path = TimedPath((Visit(A, 0, 5), Visit(B, 15, 15), Visit(C, 25, FOREVER)))

    assert list(path.occupancies()) == [
        Occupancy(A, 0, 5),
        Occupancy(Corridor(A, B), 5, 15, A, B),
        Occupancy(B, 15, 15),
        Occupancy(Corridor(B, C), 15, 25, B, C),
        Occupancy(C, 25, FOREVER),
    ]
    assert path.holds and path.nodes == (A, B, C)
    assert (path.start.node, path.end.node) == (A, C)


def test_corridors_are_the_same_in_both_directions() -> None:
    assert Corridor.between(B, A) == Corridor.between(A, B) == Corridor(A, B)
    with pytest.raises(ValueError, match="ordered"):
        Corridor(B, A)
    with pytest.raises(ValueError, match="distinct"):
        Corridor(A, A)


@pytest.mark.parametrize(
    ("first", "second", "overlap"),
    [
        (Occupancy(A, 0, 5), Occupancy(A, 5, 9), True),  # nodes: touching at tick 5 is a clash
        (Occupancy(A, 0, 5), Occupancy(A, 6, 9), False),
        (Occupancy(A, 0, 5), Occupancy(B, 0, 5), False),  # different resources
        (Occupancy(Corridor(A, B), 0, 10), Occupancy(Corridor(A, B), 10, 20), False),  # hand-off
        (Occupancy(Corridor(A, B), 0, 10), Occupancy(Corridor(A, B), 9, 19), True),
        (Occupancy(Corridor(A, B), 0, 10), Occupancy(Corridor(A, B), 0, 10), True),
    ],
)
def test_overlap_follows_the_closed_and_open_conventions(
    first: Occupancy, second: Occupancy, overlap: bool
) -> None:
    assert first.overlaps(second) is overlap
    assert second.overlaps(first) is overlap


@pytest.mark.parametrize(
    ("visits", "message"),
    [
        ((), "at least one visit"),
        ((Visit(A, 0, 5), Visit(A, 6, 9)), "merge them"),
        ((Visit(A, 0, 5), Visit(B, 5, 9)), "must come after"),
        ((Visit(A, 0, FOREVER), Visit(B, 10, 20)), "only the last visit"),
    ],
)
def test_inconsistent_paths_are_rejected(visits: tuple[Visit, ...], message: str) -> None:
    with pytest.raises(InvalidTimedPathError, match=message):
        TimedPath(visits)


def test_inconsistent_visits_are_rejected() -> None:
    with pytest.raises(InvalidTimedPathError, match="before arrival"):
        Visit(A, 5, 4)
    with pytest.raises(InvalidTimedPathError, match="< 0"):
        Visit(A, -1, 4)


def test_validation_checks_edges_and_travel_times(line_graph: WarehouseGraph) -> None:
    times = _times(line_graph)

    validate_timed_path(TimedPath((Visit(A, 0, 3), Visit(B, 13, 20), Visit(C, 30, 30))), times)
    with pytest.raises(InvalidTimedPathError, match="takes 10 ticks"):
        validate_timed_path(TimedPath((Visit(A, 0, 3), Visit(B, 12, 20))), times)
    with pytest.raises(InvalidTimedPathError, match="no edge"):
        validate_timed_path(TimedPath((Visit(A, 0, 3), Visit(C, 13, 20))), times)
