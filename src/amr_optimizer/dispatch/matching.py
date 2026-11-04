"""Minimum-cost bipartite matching with forbidden pairs (the linear assignment problem)."""

from collections.abc import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment


def min_cost_matching(costs: Sequence[Sequence[int | None]]) -> list[tuple[int, int]]:
    """Match rows to columns, each at most once: as many pairs as possible, then cheapest.

    ``costs[i][j]`` is the cost of pairing row ``i`` with column ``j``, or ``None`` if that
    pair is forbidden. The result lists the chosen ``(row, column)`` pairs, sorted by row.

    scipy's ``linear_sum_assignment`` (a modified Jonker-Volgenant shortest augmenting path
    algorithm, Crouse 2016) always pairs ``min(rows, columns)`` times, so forbidden pairs are
    given a cost larger than the sum of all allowed costs, and dropped afterwards. Then a
    matching with fewer forbidden pairs is always cheaper than one with more, so the solver
    maximises the number of allowed pairs first and minimises their total cost second.
    Worst case O(n^3) for n = max(rows, columns).
    """
    if not costs:
        return []
    width = len(costs[0])
    if any(len(row) != width for row in costs):
        raise ValueError("every row of the cost matrix must have the same length")
    if width == 0:
        return []
    allowed = [cost for row in costs for cost in row if cost is not None]
    if any(cost < 0 for cost in allowed):
        raise ValueError("costs must be non-negative")

    forbidden = sum(allowed) + 1
    matrix = np.array(
        [[forbidden if cost is None else cost for cost in row] for row in costs], dtype=np.int64
    )
    rows, columns = linear_sum_assignment(matrix)
    pairs = [(int(row), int(column)) for row, column in zip(rows, columns, strict=True)]
    return sorted((row, column) for row, column in pairs if costs[row][column] is not None)
