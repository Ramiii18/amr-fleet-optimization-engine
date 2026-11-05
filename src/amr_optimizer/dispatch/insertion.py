"""Cheapest-insertion planning, and the receding-horizon policy that uses it (D19, D22)."""

import itertools
from collections.abc import Sequence

from amr_optimizer.dispatch.horizon import (
    DEFAULT_MAX_ORDERS,
    HorizonProblem,
    Objective,
    Plan,
    build_problem,
    first_jobs,
    worst_weight,
)
from amr_optimizer.sim import Assignment, DispatchSnapshot


def insertion_plan(problem: HorizonProblem) -> Plan:
    """Build a plan by cheapest insertion: orders oldest first, each one inserted at the robot
    and position where the plan's cost (`plan_cost`) grows the least.

    Ties go to the robot listed first, then to the earliest position. An insertion is always
    possible: a robot that can reach the order can take it first, and every trip between two
    orders exists (`HorizonProblem` checks both).

    Cost: O(K^2 * (K + R) + K * R^2) for K orders and R robots. Each of the K insertions tries
    O(K + R) positions and evaluates the changed sequence in O(K); the worst lead time of the
    other robots costs O(R) per robot.

    The rule is myopic. An early order can take the robot that a later order needs, and
    insertion never moves an order once placed. `CpSatDispatch` searches all plans.
    """
    weight = worst_weight(problem)
    robots = len(problem.robots)
    sequences: list[list[int]] = [[] for _ in range(robots)]
    worst, totals = [0] * robots, [0] * robots  # per robot: longest lead time, total completion
    for j in range(len(problem.orders)):
        best: tuple[int, int, int, int, int] | None = None  # cost, robot, position, worst, total
        base = sum(totals)
        for r, sequence in enumerate(sequences):
            others = max((worst[s] for s in range(robots) if s != r), default=0)
            for position in range(len(sequence) + 1):
                values = _sequence_values(
                    problem, r, [*sequence[:position], j, *sequence[position:]]
                )
                if values is None:
                    continue
                new_worst, new_total = values
                cost = weight * max(others, new_worst) + base - totals[r] + new_total
                if best is None or cost < best[0]:
                    best = (cost, r, position, new_worst, new_total)
        if best is None:  # unreachable: HorizonProblem guarantees a feasible insertion
            raise AssertionError(f"order {problem.orders[j].id!r} cannot be inserted")
        _, r, position, worst[r], totals[r] = best
        sequences[r].insert(position, j)
    return tuple(tuple(sequence) for sequence in sequences)


def sequence_total(problem: HorizonProblem, robot: int, sequence: Sequence[int]) -> int | None:
    """Total completion time of ``sequence`` on robot ``robot``, or ``None`` if the robot
    cannot reach the first pickup. Agrees with `completion_offsets` (tested)."""
    values = _sequence_values(problem, robot, sequence)
    return None if values is None else values[1]


def _sequence_values(
    problem: HorizonProblem, robot: int, sequence: Sequence[int]
) -> tuple[int, int] | None:
    """The longest lead time and the total completion time of one robot's sequence."""
    if not sequence:
        return 0, 0
    first = problem.start_ticks[robot][sequence[0]]
    if first is None:
        return None
    time = problem.robots[robot].ready + first + problem.job_ticks[sequence[0]]
    total, worst = time, problem.waited[sequence[0]] + time
    for previous, j in itertools.pairwise(sequence):
        time += problem.setup_ticks[previous][j] + problem.job_ticks[j]
        total += time
        worst = max(worst, problem.waited[j] + time)
    return worst, total


class InsertionDispatch:
    """Receding-horizon dispatch with the cheapest-insertion plan (D18, D19).

    At each decision: plan every robot and the oldest ``max_orders`` pending orders by cheapest
    insertion, then start the first planned job of each idle robot. An idle robot with no
    planned job waits, because the plan gives every order to a robot that can do it sooner.
    With ``Objective.WORST_FIRST`` (D22), the policy is named ``insertion-fair``.
    """

    def __init__(
        self, *, max_orders: int = DEFAULT_MAX_ORDERS, objective: Objective = Objective.TOTAL
    ) -> None:
        if max_orders < 1:
            raise ValueError(f"max_orders must be at least 1, got {max_orders}")
        self.max_orders = max_orders
        self.objective = objective
        self.name = "insertion" if objective is Objective.TOTAL else "insertion-fair"

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        if not snapshot.idle_robots or not snapshot.pending_orders:
            return []
        problem = build_problem(snapshot, max_orders=self.max_orders, objective=self.objective)
        return first_jobs(problem, insertion_plan(problem))
