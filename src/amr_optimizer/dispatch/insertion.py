"""Cheapest-insertion planning, and the receding-horizon policy that uses it (D19)."""

import itertools
from collections.abc import Sequence

from amr_optimizer.dispatch.horizon import (
    DEFAULT_MAX_ORDERS,
    HorizonProblem,
    Plan,
    build_problem,
    first_jobs,
)
from amr_optimizer.sim import Assignment, DispatchSnapshot


def insertion_plan(problem: HorizonProblem) -> Plan:
    """Build a plan by cheapest insertion: orders oldest first, each one inserted at the robot
    and position where it adds the least total completion time.

    Ties go to the robot listed first, then to the earliest position. An insertion is always
    possible: a robot that can reach the order can take it first, and every trip between two
    orders exists (`HorizonProblem` checks both).

    Cost: O(K^2 * (K + R)) for K orders and R robots. Each of the K insertions tries
    O(K + R) positions and evaluates the changed sequence in O(K).

    The rule is myopic. An early order can take the robot that a later order needs, and
    insertion never moves an order once placed. `CpSatDispatch` searches all plans.
    """
    sequences: list[list[int]] = [[] for _ in problem.robots]
    totals = [0] * len(problem.robots)
    for j in range(len(problem.orders)):
        best: tuple[int, int, int] | None = None  # (added completion time, robot, position)
        for r, sequence in enumerate(sequences):
            for position in range(len(sequence) + 1):
                total = sequence_total(problem, r, [*sequence[:position], j, *sequence[position:]])
                if total is not None and (best is None or total - totals[r] < best[0]):
                    best = (total - totals[r], r, position)
        if best is None:  # unreachable: HorizonProblem guarantees a feasible insertion
            raise AssertionError(f"order {problem.orders[j].id!r} cannot be inserted")
        added, r, position = best
        sequences[r].insert(position, j)
        totals[r] += added
    return tuple(tuple(sequence) for sequence in sequences)


def sequence_total(problem: HorizonProblem, robot: int, sequence: Sequence[int]) -> int | None:
    """Total completion time of ``sequence`` on robot ``robot``, or ``None`` if the robot
    cannot reach the first pickup. Agrees with `completion_offsets` (tested)."""
    if not sequence:
        return 0
    first = problem.start_ticks[robot][sequence[0]]
    if first is None:
        return None
    time = problem.robots[robot].ready + first + problem.job_ticks[sequence[0]]
    total = time
    for previous, j in itertools.pairwise(sequence):
        time += problem.setup_ticks[previous][j] + problem.job_ticks[j]
        total += time
    return total


class InsertionDispatch:
    """Receding-horizon dispatch with the cheapest-insertion plan (D18, D19).

    At each decision: plan every robot and the oldest ``max_orders`` pending orders by cheapest
    insertion, then start the first planned job of each idle robot. An idle robot with no
    planned job waits, because the plan gives every order to a robot that can do it sooner.
    """

    name = "insertion"

    def __init__(self, *, max_orders: int = DEFAULT_MAX_ORDERS) -> None:
        if max_orders < 1:
            raise ValueError(f"max_orders must be at least 1, got {max_orders}")
        self.max_orders = max_orders

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        if not snapshot.idle_robots or not snapshot.pending_orders:
            return []
        problem = build_problem(snapshot, max_orders=self.max_orders)
        return first_jobs(problem, insertion_plan(problem))
