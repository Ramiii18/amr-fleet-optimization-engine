"""CP-SAT model of the bounded-horizon plan, and the policy that uses it (D20).

The model, for R robots and n orders, with completion times C(j) in ticks from now:

- **One "giant tour" circuit** over R robot nodes and n order nodes: robot 0, its orders in
  sequence, robot 1, its orders, ..., back to robot 0. Arc robot r -> order j means j is r's
  first job; order i -> order j that j follows i on the same robot; order j -> robot r + 1
  that j is r's last job; robot r -> robot r + 1 that r does nothing. CP-SAT's circuit
  constraint makes the arcs form one cycle through every node, so every order is done once.
- **Owners.** ``owner(j)`` is the robot doing order j. Each arc pins it (a first job's owner
  is its robot, a follower has its predecessor's owner, a last job's owner is the robot whose
  segment ends there), so the robots appear in index order and each plan is one circuit.
- **Completion times.** If j is r's first job, C(j) >= ready(r) + start(r, j) + job(j). If j
  follows i, C(j) >= C(i) + setup(i, j) + job(j).
- **Objective:** minimise the sum of C(j). With ``Objective.WORST_FIRST`` (D22), a variable
  W >= waited(j) + C(j) for every order is added, and the objective becomes
  `worst_weight` * W + sum of C(j): the longest lead time first, then the total.

Minimisation drives every C(j) down to its earliest value, so the optimum of the model is the
best plan's total completion time. The plan itself is read from the arcs and evaluated again
with `completion_offsets`, which stays the single definition of a plan's cost.

Robots never interfere and all times are known, so nothing in the model is uncertain. What
makes it hard is the combinatorics: n orders spread over R robots in sequence.
"""

import itertools
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ortools.sat.python import cp_model

from amr_optimizer.dispatch.horizon import (
    DEFAULT_MAX_ORDERS,
    HorizonProblem,
    Objective,
    Plan,
    build_problem,
    completion_offsets,
    completion_upper_bound,
    first_jobs,
    plan_cost,
    worst_lead,
    worst_weight,
)
from amr_optimizer.dispatch.insertion import insertion_plan
from amr_optimizer.sim import Assignment, DispatchSnapshot


class SolveStatus(Enum):
    """How the plan of one decision was obtained."""

    TRIVIAL = "trivial"  # at most one order: the insertion plan is optimal, no search needed
    OPTIMAL = "optimal"  # CP-SAT proved the plan optimal
    LIMIT = "limit"  # the time limit stopped CP-SAT; the best plan found is not proven optimal
    FALLBACK = "fallback"  # CP-SAT returned no plan in time; the insertion plan is used


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """CP-SAT limits (D20).

    One worker and a *deterministic* time limit make every solve reproducible: the same problem
    always gives the same plan, whatever the machine's load. A wall-clock limit would not.
    CP-SAT's deterministic time is a work measure that is meant to approximate seconds.
    """

    max_deterministic_time: float = 0.2  # measured trade-off between plan quality and time (D20)
    random_seed: int = 0

    def __post_init__(self) -> None:
        if not self.max_deterministic_time > 0:
            raise ValueError(
                f"max_deterministic_time must be positive, got {self.max_deterministic_time}"
            )


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    """The plan of one decision, and how good it is known to be.

    ``objective`` is the plan's cost (`plan_cost`), and ``hint_objective`` that of the
    insertion plan. ``bound`` is a proven lower bound on the best cost, so ``objective -
    bound`` is the largest possible remaining gain. ``wall_s`` is measured for reporting
    only; it never influences a decision.
    """

    plan: Plan
    status: SolveStatus
    objective: int
    hint_objective: int
    bound: int
    orders: int
    robots: int
    wall_s: float


def solve_plan(
    problem: HorizonProblem, hint: Plan, settings: SolverSettings | None = None
) -> SolveOutcome:
    """The best plan CP-SAT finds within ``settings``, never worse than ``hint``.

    ``hint`` must be a valid plan, normally the insertion plan. It is given to CP-SAT as a
    starting solution, and it is the result if CP-SAT finds nothing better in time.
    """
    settings = settings or SolverSettings()
    started = time.perf_counter()
    hint_total = plan_cost(problem, hint)
    n, robots = len(problem.orders), len(problem.robots)
    if n <= 1:  # with one order, the best plan gives it to the robot that finishes it first
        return SolveOutcome(
            hint, SolveStatus.TRIVIAL, hint_total, hint_total, hint_total, n, robots, 0.0
        )

    model = _PlanModel(problem)
    model.add_hint(hint)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = settings.random_seed
    solver.parameters.max_deterministic_time = settings.max_deterministic_time
    status = solver.solve(model.model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        plan = model.read_plan(solver)
        total, objective = plan_cost(problem, plan), round(solver.objective_value)
        # The evaluator gives each order its earliest time, so it can only agree with the
        # model or be cheaper, and an optimal model value must be exactly the plan's cost.
        if total > objective or (status == cp_model.OPTIMAL and total != objective):
            raise AssertionError(f"plan costs {total}, but the model's value is {objective}")
        result = SolveStatus.OPTIMAL if status == cp_model.OPTIMAL else SolveStatus.LIMIT
        if total > hint_total:  # possible only when the limit stops the search early
            plan, total = hint, hint_total
    elif status == cp_model.UNKNOWN:
        plan, total, result = hint, hint_total, SolveStatus.FALLBACK
    else:  # the hint is feasible, so the model cannot be infeasible
        raise AssertionError(f"CP-SAT returned {solver.status_name(status)}")

    bound = min(total, max(0, math.ceil(solver.best_objective_bound - 1e-6)))
    if result is SolveStatus.OPTIMAL and bound != total:
        raise AssertionError(f"optimal plan costs {total}, but the bound is {bound}")
    elapsed = time.perf_counter() - started
    return SolveOutcome(plan, result, total, hint_total, bound, n, robots, elapsed)


class _PlanModel:
    """The giant-tour CP-SAT model of one `HorizonProblem`, and how to read a plan from it.

    Nodes 0..R-1 are the robots and nodes R..R+n-1 the orders. A single circuit visits every
    node: robot 0, its orders in sequence, robot 1, its orders, ..., and back to robot 0.
    ``owner[j]`` is the robot that does order j. Pinning it on every arc makes the robots
    appear in index order, so each plan corresponds to exactly one circuit; without it, the
    robots' segments could be arranged in (R - 1)! ways, all with the same cost.
    """

    def __init__(self, problem: HorizonProblem) -> None:
        self.problem = problem
        model = self.model = cp_model.CpModel()
        robots, n, job = len(problem.robots), len(problem.orders), problem.job_ticks
        lower, upper = _completion_lower_bounds(problem), completion_upper_bound(problem)
        self.done = [model.new_int_var(lower[j], upper, f"done_{j}") for j in range(n)]
        self.owner = [model.new_int_var(0, robots - 1, f"owner_{j}") for j in range(n)]
        arcs: list[cp_model.ArcT] = []

        # Robot r does nothing: the circuit goes straight on to the next robot. With a single
        # robot there is no such arc, because that robot must do every order.
        self.empty: dict[int, cp_model.IntVar] = {}
        if robots > 1:
            for r in range(robots):
                literal = self.empty[r] = model.new_bool_var(f"empty_{r}")
                arcs.append((r, (r + 1) % robots, literal))

        # Order j is robot r's first job.
        self.first: dict[tuple[int, int], cp_model.IntVar] = {}
        for r, robot in enumerate(problem.robots):
            for j, start in enumerate(problem.start_ticks[r]):
                if start is None:
                    continue
                literal = self.first[r, j] = model.new_bool_var(f"first_{r}_{j}")
                arcs.append((r, robots + j, literal))
                earliest = robot.ready + start + job[j]
                model.add(self.done[j] >= earliest).only_enforce_if(literal)
                model.add(self.owner[j] == r).only_enforce_if(literal)

        # Order j follows order i on the same robot.
        self.follows: dict[tuple[int, int], cp_model.IntVar] = {}
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                literal = self.follows[i, j] = model.new_bool_var(f"follows_{i}_{j}")
                arcs.append((robots + i, robots + j, literal))
                after = self.done[i] + problem.setup_ticks[i][j] + job[j]
                model.add(self.done[j] >= after).only_enforce_if(literal)
                model.add(self.owner[j] == self.owner[i]).only_enforce_if(literal)

        # Order j is robot r's last job: the circuit goes on to the next robot.
        self.last: dict[tuple[int, int], cp_model.IntVar] = {}
        for j in range(n):
            for r in range(robots):
                literal = self.last[j, r] = model.new_bool_var(f"last_{j}_{r}")
                arcs.append((robots + j, (r + 1) % robots, literal))
                model.add(self.owner[j] == r).only_enforce_if(literal)

        model.add_circuit(arcs)

        # Worst first (D22): W is at least every order's lead time, and dominates the objective.
        self.worst: cp_model.IntVar | None = None
        weight = worst_weight(problem)
        if weight:
            leads = [w + low for w, low in zip(problem.waited, lower, strict=True)]
            worst = self.worst = model.new_int_var(max(leads), max(problem.waited) + upper, "worst")
            for waited, done in zip(problem.waited, self.done, strict=True):
                model.add(worst >= waited + done)
            model.minimize(weight * worst + sum(self.done))
        else:
            model.minimize(sum(self.done))

    def add_hint(self, plan: Plan) -> None:
        """Give CP-SAT ``plan`` as a complete starting solution."""
        for variable, value in zip(self.done, completion_offsets(self.problem, plan), strict=True):
            self.model.add_hint(variable, value)
        if self.worst is not None:
            self.model.add_hint(self.worst, worst_lead(self.problem, plan))
        owners = {j: r for r, sequence in enumerate(plan) for j in sequence}
        for j, variable in enumerate(self.owner):
            self.model.add_hint(variable, owners[j])
        first = {(r, sequence[0]) for r, sequence in enumerate(plan) if sequence}
        follows = {pair for sequence in plan for pair in itertools.pairwise(sequence)}
        last = {(sequence[-1], r) for r, sequence in enumerate(plan) if sequence}
        for r, literal in self.empty.items():
            self.model.add_hint(literal, not plan[r])
        for chosen, literals in ((first, self.first), (follows, self.follows), (last, self.last)):
            for key, literal in literals.items():
                self.model.add_hint(literal, key in chosen)

    def read_plan(self, solver: cp_model.CpSolver) -> Plan:
        """Follow the circuit from each robot node through that robot's orders."""
        n, sequences = len(self.problem.orders), []
        for r in range(len(self.problem.robots)):
            sequence: list[int] = []
            current = next(
                (j for j in range(n) if (r, j) in self.first and solver.value(self.first[r, j])),
                None,
            )
            while current is not None and len(sequence) <= n:
                sequence.append(current)
                current = next(
                    (
                        k
                        for k in range(n)
                        if k != current and solver.value(self.follows[current, k])
                    ),
                    None,
                )
            sequences.append(tuple(sequence))
        return tuple(sequences)


def _completion_lower_bounds(problem: HorizonProblem) -> list[int]:
    """A lower bound on each order's completion time, in ticks from now.

    An order is either some robot's first job, or it follows another order, whose own
    completion takes at least that order's job time.
    """
    n, job = len(problem.orders), problem.job_ticks
    lower: list[int] = []
    for j in range(n):
        from_robot = [
            robot.ready + start
            for robot, row in zip(problem.robots, problem.start_ticks, strict=True)
            if (start := row[j]) is not None
        ]
        from_order = [job[i] + problem.setup_ticks[i][j] for i in range(n) if i != j]
        lower.append(job[j] + min(from_robot + from_order))
    return lower


class CpSatDispatch:
    """Receding-horizon dispatch with the CP-SAT plan (D18, D20).

    At each decision: build the problem for every robot and the oldest ``max_orders`` pending
    orders, plan by insertion, improve the plan with CP-SAT within ``settings``, and start the
    first planned job of each idle robot.

    ``outcomes`` records every solve, for the comparison script's solver statistics. It is an
    observation only: no decision reads it. With ``Objective.WORST_FIRST`` (D22), the policy is
    named ``cpsat-fair``.
    """

    def __init__(
        self,
        *,
        max_orders: int = DEFAULT_MAX_ORDERS,
        objective: Objective = Objective.TOTAL,
        settings: SolverSettings | None = None,
    ) -> None:
        if max_orders < 1:
            raise ValueError(f"max_orders must be at least 1, got {max_orders}")
        self.max_orders = max_orders
        self.objective = objective
        self.name = "cpsat" if objective is Objective.TOTAL else "cpsat-fair"
        self.settings = settings or SolverSettings()
        self.outcomes: list[SolveOutcome] = []

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        if not snapshot.idle_robots or not snapshot.pending_orders:
            return []
        problem = build_problem(snapshot, max_orders=self.max_orders, objective=self.objective)
        outcome = solve_plan(problem, insertion_plan(problem), self.settings)
        self.outcomes.append(outcome)
        return first_jobs(problem, outcome.plan)
