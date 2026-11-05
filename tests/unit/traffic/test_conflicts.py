"""Tests for the independent conflict checker, on the classic multi-robot cases.

Every corridor below takes 10 ticks. The checker needs no map: it compares occupancies only.

Intersection layout for the crossing cases::

        N
        |
    W - X - E
        |
        S
"""

import pytest

from amr_optimizer.domain import RobotId
from amr_optimizer.traffic import FOREVER, Corridor, TimedPath, Visit, path_conflicts
from amr_optimizer.warehouse import NodeId

A, B, C = NodeId("A"), NodeId("B"), NodeId("C")
N, S, E, W, X = (NodeId(n) for n in "NSEWX")
R1, R2 = RobotId("R1"), RobotId("R2")


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


def _kinds(first: TimedPath, second: TimedPath) -> list[str]:
    return [c.kind for c in path_conflicts({R1: [first], R2: [second]})]


def test_head_on_on_a_two_way_corridor() -> None:
    # R1 drives A -> B during (0, 10); R2 drives B -> A during (5, 15).
    assert _kinds(_path((A, 0, 0), (B, 10, 10)), _path((B, 0, 5), (A, 15, 15))) == ["head-on"]


def test_swap_is_a_head_on_conflict() -> None:
    # The two robots exchange positions along the same corridor at the same time.
    conflicts = path_conflicts(
        {R1: [_path((A, 0, 0), (B, 10, 10))], R2: [_path((B, 0, 0), (A, 10, 10))]}
    )

    assert [c.kind for c in conflicts] == ["head-on"]
    assert conflicts[0].resource == Corridor(A, B)


def test_following_on_the_same_corridor_is_a_conflict() -> None:
    # R2 enters the corridor 3 ticks after R1, in the same direction.
    assert _kinds(_path((A, 0, 0), (B, 10, 10)), _path((A, 3, 3), (B, 13, 13))) == ["following"]


def test_a_hand_off_at_the_same_tick_is_not_a_conflict() -> None:
    # R1 reaches B at tick 10 and stays; R2, waiting at A, enters the corridor at tick 10. They
    # are never at the same node, and the corridor intervals (0, 10) and (10, 20) don't meet.
    r1 = _path((A, 0, 0), (B, 10, 30))
    r2 = _path((A, 1, 10), (B, 20, 20))

    assert _kinds(r1, r2) == ["vertex"]  # B: R1 holds [10, 30], R2 arrives at 20
    assert _kinds(r1, _path((A, 1, 10), (B, 31, 31))) == []  # ... unless R2 arrives later


def test_two_robots_crossing_an_intersection() -> None:
    west_east = _path((W, 0, 0), (X, 10, 10), (E, 20, 20))

    assert _kinds(west_east, _path((S, 0, 0), (X, 10, 10), (N, 20, 20))) == ["vertex"]
    assert _kinds(west_east, _path((S, 0, 1), (X, 11, 11), (N, 21, 21))) == []  # one tick later


def test_a_node_vacated_and_entered_at_the_same_tick_is_a_conflict() -> None:
    # R1 leaves X at tick 10; R2 arrives at X at tick 10: both are at X at that tick (D23).
    assert _kinds(_path((W, 0, 0), (X, 10, 10), (E, 20, 20)), _path((S, 0, 0), (X, 10, 15))) == [
        "vertex"
    ]


def test_a_hold_blocks_its_node_forever() -> None:
    hold = _path((B, 0, 0), (C, 10, FOREVER))

    assert _kinds(hold, _path((A, 0, 0), (B, 990, 990), (C, 1000, 1000))) == ["vertex"]


def test_a_robot_never_conflicts_with_itself() -> None:
    first = _path((A, 0, 0), (B, 10, 10))
    second = _path((B, 10, 10), (C, 20, 20))  # continues from where the first path ended

    assert path_conflicts({R1: [first, second]}) == []


@pytest.mark.parametrize("robots", [3, 4])
def test_every_pair_is_reported(robots: int) -> None:
    # All robots stand at X at tick 5: every pair conflicts once.
    paths = {RobotId(f"R{i}"): [_path((X, 5, 5))] for i in range(robots)}

    assert len(path_conflicts(paths)) == robots * (robots - 1) // 2
