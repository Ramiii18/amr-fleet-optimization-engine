"""Tests for min_cost_matching, including a brute-force oracle."""

import itertools
import random

import pytest

from amr_optimizer.dispatch import min_cost_matching

Costs = list[list[int | None]]


def _score(costs: Costs, pairs: list[tuple[int, int]]) -> tuple[int, int]:
    """(number of allowed pairs, their total cost) for a set of pairs."""
    allowed = [costs[r][c] for r, c in pairs if costs[r][c] is not None]
    return len(allowed), sum(c for c in allowed if c is not None)


def _brute_force(costs: Costs) -> tuple[int, int]:
    """Best (most pairs, then cheapest) score over every full assignment."""
    rows, cols = len(costs), len(costs[0])
    if rows <= cols:
        candidates = (list(enumerate(perm)) for perm in itertools.permutations(range(cols), rows))
    else:
        candidates = (
            [(r, c) for c, r in enumerate(perm)]
            for perm in itertools.permutations(range(rows), cols)
        )
    most_then_cheapest = min((-n, cost) for n, cost in (_score(costs, p) for p in candidates))
    return -most_then_cheapest[0], most_then_cheapest[1]


def test_square_example_from_the_scipy_documentation() -> None:
    costs: Costs = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]

    pairs = min_cost_matching(costs)

    assert pairs == [(0, 1), (1, 0), (2, 2)]
    assert _score(costs, pairs) == (3, 5)


def test_rectangular_matrices_pair_the_smaller_side_completely() -> None:
    assert min_cost_matching([[5, 1, 9], [1, 5, 9]]) == [(0, 1), (1, 0)]
    assert min_cost_matching([[5, 1], [1, 5], [0, 0]]) == [(1, 0), (2, 1)]


def test_forbidden_pairs_are_never_chosen() -> None:
    assert min_cost_matching([[None, 1], [2, None]]) == [(0, 1), (1, 0)]
    assert min_cost_matching([[1, None], [0, None]]) == [(1, 0)]
    assert min_cost_matching([[None, None], [None, None]]) == []


def test_more_pairs_beat_cheaper_pairs() -> None:
    # Pairing both rows costs 1000; pairing only row 0 with column 0 would cost 0.
    assert min_cost_matching([[0, 100], [None, 1000]]) == [(0, 0), (1, 1)]


def test_empty_inputs() -> None:
    assert min_cost_matching([]) == []
    assert min_cost_matching([[], []]) == []


def test_malformed_matrices_are_rejected() -> None:
    with pytest.raises(ValueError, match="same length"):
        min_cost_matching([[1, 2], [3]])
    with pytest.raises(ValueError, match="non-negative"):
        min_cost_matching([[1, -2]])


@pytest.mark.parametrize("seed", range(300))
def test_matches_brute_force_on_random_matrices(seed: int) -> None:
    rng = random.Random(seed)
    rows, cols = rng.randint(1, 5), rng.randint(1, 5)
    costs: Costs = [
        [None if rng.random() < 0.25 else rng.randint(0, 20) for _ in range(cols)]
        for _ in range(rows)
    ]

    pairs = min_cost_matching(costs)

    assert len({r for r, _ in pairs}) == len({c for _, c in pairs}) == len(pairs)
    assert all(costs[r][c] is not None for r, c in pairs)
    assert _score(costs, pairs) == _brute_force(costs)
