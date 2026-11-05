"""Tests for the reservation table, by hand and against the independent conflict checker."""

import random

import pytest

from amr_optimizer.domain import RobotId
from amr_optimizer.traffic import (
    FOREVER,
    Corridor,
    Reservation,
    ReservationConflictError,
    ReservationTable,
    TimedPath,
    Visit,
    find_conflicts,
)
from amr_optimizer.warehouse import NodeId

A, B, C, D = (NodeId(n) for n in "ABCD")
R1, R2, R3 = RobotId("R1"), RobotId("R2"), RobotId("R3")


def _path(*visits: tuple[NodeId, int, int]) -> TimedPath:
    return TimedPath(tuple(Visit(node, arrive, depart) for node, arrive, depart in visits))


def test_safe_intervals_are_the_gaps_between_reservations() -> None:
    table = ReservationTable()
    table.reserve(R1, _path((A, 0, 0), (B, 10, 12)))
    table.reserve(R2, _path((C, 0, 10), (B, 20, 25)))

    assert table.safe_intervals(B) == [(0, 9), (13, 19), (26, FOREVER)]
    assert table.safe_intervals(B, after=15) == [(15, 19), (26, FOREVER)]
    assert table.safe_intervals(D) == [(0, FOREVER)]
    assert table.corridor_reservations(B, A) == (Reservation(0, 10, R1),)


def test_a_conflicting_reservation_is_refused_and_changes_nothing() -> None:
    table = ReservationTable()
    table.reserve(R1, _path((A, 0, 0), (B, 10, 10)))
    before = (table.node_reservations(A), table.node_reservations(B))

    with pytest.raises(ReservationConflictError, match="overlaps robot 'R1'"):
        # C is free, but the corridor B -> A overlaps R1's A -> B: a swap.
        table.reserve(R2, _path((C, 0, 0), (B, 5, 5), (A, 15, 15)))

    assert (table.node_reservations(A), table.node_reservations(B)) == before
    assert table.node_reservations(C) == ()


def test_holds_block_until_they_are_ended() -> None:
    table = ReservationTable()
    table.reserve(R1, _path((B, 0, 0), (C, 10, FOREVER)))

    assert table.holder(C) == R1 and table.held_node(R1) == C
    assert table.safe_intervals(C) == [(0, 9)]
    assert not table.is_free_from(C, 1000)
    with pytest.raises(ReservationConflictError, match="still holds"):
        table.reserve(R1, _path((C, 50, 50), (B, 60, 60)))

    assert table.end_hold(R1, 50) == C  # the next path takes over at tick 50
    table.reserve(R1, _path((C, 50, 52), (B, 62, 62)))

    assert table.holder(C) is None
    assert table.node_reservations(C) == (Reservation(10, 49, R1), Reservation(50, 52, R1))
    assert table.is_free_from(C, 53)


def test_a_hold_ended_before_it_began_disappears() -> None:
    table = ReservationTable()
    table.reserve(R1, _path((B, 0, 0), (C, 10, FOREVER)))

    table.end_hold(R1, 10)

    assert table.node_reservations(C) == ()
    with pytest.raises(KeyError):
        table.end_hold(R1, 20)


def test_prune_forgets_only_finished_reservations() -> None:
    table = ReservationTable()
    table.reserve(R1, _path((A, 0, 0), (B, 10, 20)))
    table.reserve(R2, _path((C, 0, 0), (D, 10, FOREVER)))

    table.prune(15)

    assert table.node_reservations(A) == ()
    assert table.corridor_reservations(A, B) == ()
    assert table.node_reservations(B) == (Reservation(10, 20, R1),)
    assert table.holder(D) == R2


# --- against the independent checker -----------------------------------------------------------

# A small two-way graph: a square A-B-C-D-A with a diagonal A-C. Ticks per corridor below.
TICKS = {
    Corridor(A, B): 3,
    Corridor(B, C): 5,
    Corridor(C, D): 3,
    Corridor(A, D): 5,
    Corridor(A, C): 4,
}
NEIGHBOURS = {
    node: sorted(c.b if c.a == node else c.a for c in TICKS if node in (c.a, c.b))
    for node in (A, B, C, D)
}


def _random_path(rng: random.Random) -> TimedPath:
    node, tick = rng.choice([A, B, C, D]), rng.randint(0, 30)
    visits = []
    for _ in range(rng.randint(1, 5)):
        depart = tick + rng.randint(0, 4)
        visits.append(Visit(node, tick, depart))
        following = rng.choice(NEIGHBOURS[node])
        tick, node = depart + TICKS[Corridor.between(node, following)], following
    hold = rng.random() < 0.2
    visits.append(Visit(node, tick, FOREVER if hold else tick + rng.randint(0, 4)))
    return TimedPath(tuple(visits))


def test_the_table_accepts_exactly_the_paths_the_checker_finds_conflict_free() -> None:
    accepted_total = refused_total = 0
    for seed in range(300):
        rng = random.Random(seed)
        table = ReservationTable()
        accepted: list[tuple[RobotId, TimedPath]] = []
        for index in range(4):
            robot = RobotId(f"R{index}")
            path = _random_path(rng)
            occupancies = [(robot, o) for o in path.occupancies()] + [
                (r, o) for r, p in accepted for o in p.occupancies()
            ]
            expected_ok = not find_conflicts(occupancies)
            try:
                table.reserve(robot, path)
            except ReservationConflictError:
                assert not expected_ok, f"seed {seed}: refused a conflict-free path"
                refused_total += 1
            else:
                assert expected_ok, f"seed {seed}: accepted a conflicting path"
                accepted.append((robot, path))
                accepted_total += 1

    assert accepted_total > 0 and refused_total > 0  # both outcomes occur
