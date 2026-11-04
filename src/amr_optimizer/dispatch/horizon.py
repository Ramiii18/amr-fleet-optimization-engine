"""Bounded-horizon planning: robot availability, the planning problem and plans (D18).

At a decision point, a planning policy looks further ahead than idle robots and single jobs:

- **Every robot** is planned, from the time and place where it can start a new job. An idle
  robot starts now, where it stands. A busy robot starts when it delivers its current order,
  at that order's drop-off. Robots never interfere in this model, so the prediction is exact.
- **The oldest pending orders**, at most ``max_orders`` of them, are planned.
- A **plan** gives each robot a sequence of orders. A robot drives to the first pickup, loads,
  drives to the drop-off and unloads; then it drives on to the next pickup, and so on.

Only the first job of each idle robot is carried out (`first_jobs`). The rest of the plan is
what makes that first job a good choice, and it is planned again at the next decision.

Times in a `HorizonProblem` are ticks counted from the decision tick. The objective is the
total completion time of the planned orders (`total_completion`): each order's time from
pickup to delivery is fixed, so this also minimises total waiting time.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from amr_optimizer.domain import RobotId, RobotPhase, RobotState, TransportOrder
from amr_optimizer.sim import Assignment, DispatchSnapshot, TravelPlanner
from amr_optimizer.warehouse import NodeId

Plan = tuple[tuple[int, ...], ...]
"""One sequence of order indices per robot, in the problem's robot order."""

DEFAULT_MAX_ORDERS = 10
"""How many of the oldest pending orders a planning policy considers (D18). In the M2 runs at
about 90% utilization, the queue held 4 to 6 orders on average (Little's law on the measured
assignment delay), so 10 covers most of it while keeping each problem small."""


class PlanningProblemError(ValueError):
    """The data of a planning problem is inconsistent."""


class InvalidPlanError(ValueError):
    """A plan does not fit its problem: wrong shape, a missing or repeated order, or a trip
    that does not exist."""


@dataclass(frozen=True, slots=True)
class RobotAvailability:
    """When and where a robot can start its next job.

    ``ready`` counts ticks from the decision tick, and is 0 for an idle robot. Only idle
    robots can be given a job now; busy robots appear in the plan for look-ahead only.
    """

    robot_id: RobotId
    node: NodeId
    ready: int
    idle: bool

    def __post_init__(self) -> None:
        if self.ready < 0:
            raise PlanningProblemError(f"robot {self.robot_id!r}: ready time {self.ready} < 0")
        if self.idle and self.ready != 0:
            raise PlanningProblemError(f"robot {self.robot_id!r} is idle, so it is ready now")


def availability(robot: RobotState, snapshot: DispatchSnapshot) -> RobotAvailability:
    """When and where ``robot`` can start a new job, seen from ``snapshot.tick``.

    For a busy robot this is the rest of its current phase plus the fixed durations of the
    phases still to come. The result is exact while robots never interfere; with traffic
    (M4) it becomes an estimate.
    """
    if robot.is_idle:
        return RobotAvailability(robot.robot_id, robot.node, 0, idle=True)
    order, until = robot.order, robot.until_tick
    if order is None or until is None or until < snapshot.tick:
        raise ValueError(f"robot {robot.robot_id!r} is busy without a current job")
    if robot.phase is RobotPhase.UNLOADING:
        later = 0
    elif robot.phase is RobotPhase.TO_DROPOFF:
        later = snapshot.unload_ticks
    else:  # travelling to the pickup, or loading: the loaded trip is still ahead
        later = _trip(snapshot.planner, order.pickup, order.dropoff) + snapshot.unload_ticks
        if robot.phase is RobotPhase.TO_PICKUP:
            later += snapshot.load_ticks
    return RobotAvailability(robot.robot_id, order.dropoff, until - snapshot.tick + later, False)


@dataclass(frozen=True, slots=True)
class HorizonProblem:
    """One planning decision as plain numbers. Times are ticks from ``tick``.

    - ``robots``: every robot, in scenario order, with its availability.
    - ``orders``: the planned orders, oldest first.
    - ``job_ticks[j]``: loading, the loaded trip and unloading of order j, the same whichever
      robot does it.
    - ``start_ticks[r][j]``: robot r's trip from where it becomes ready to order j's pickup,
      or ``None`` if there is no route.
    - ``setup_ticks[i][j]``: the empty trip from order i's drop-off to order j's pickup. The
      diagonal is unused and 0.

    Checked on construction: every job takes at least one tick, every order can be the first
    job of some robot, and every trip between two orders exists. A feasible plan therefore
    always exists: give every order to one robot that can reach the first of them.
    """

    tick: int
    robots: tuple[RobotAvailability, ...]
    orders: tuple[TransportOrder, ...]
    job_ticks: tuple[int, ...]
    start_ticks: tuple[tuple[int | None, ...], ...]
    setup_ticks: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        n = len(self.orders)
        if not self.robots:
            raise PlanningProblemError("a planning problem needs at least one robot")
        if len({robot.robot_id for robot in self.robots}) != len(self.robots):
            raise PlanningProblemError("robot ids must be unique")
        if len({order.id for order in self.orders}) != n:
            raise PlanningProblemError("order ids must be unique")
        if (
            len(self.job_ticks) != n
            or len(self.start_ticks) != len(self.robots)
            or any(len(row) != n for row in self.start_ticks)
            or len(self.setup_ticks) != n
            or any(len(row) != n for row in self.setup_ticks)
        ):
            raise PlanningProblemError("job, start and setup times must match robots x orders")
        if any(ticks < 1 for ticks in self.job_ticks):
            raise PlanningProblemError("every job takes at least one tick")
        starts = [ticks for row in self.start_ticks for ticks in row if ticks is not None]
        if any(ticks < 0 for ticks in starts) or any(
            t < 0 for row in self.setup_ticks for t in row
        ):
            raise PlanningProblemError("travel times must be non-negative")
        for j, order in enumerate(self.orders):
            if all(row[j] is None for row in self.start_ticks):
                raise PlanningProblemError(f"order {order.id!r}: no robot can reach its pickup")


def build_problem(snapshot: DispatchSnapshot, *, max_orders: int) -> HorizonProblem:
    """The planning problem for ``snapshot``: every robot and the oldest plannable orders.

    An order is plannable if its drop-off can be reached from its pickup, some robot can
    reach its pickup, and the empty trips between it and every older planned order exist in
    both directions. On a strongly connected map, such as every generated map, all orders are
    plannable. On other maps a skipped order stays pending, as it would with greedy dispatch.

    Cost: O(K * (K + R)) cached travel-time lookups for K planned orders and R robots.
    """
    if max_orders < 1:
        raise ValueError(f"max_orders must be at least 1, got {max_orders}")
    planner = snapshot.planner
    robots = tuple(availability(robot, snapshot) for robot in snapshot.robots)
    orders: list[TransportOrder] = []
    job_ticks: list[int] = []
    for order in snapshot.pending_orders:
        if len(orders) == max_orders:
            break
        loaded = planner.ticks(order.pickup, order.dropoff)
        if loaded is None or all(planner.ticks(r.node, order.pickup) is None for r in robots):
            continue
        if any(
            planner.ticks(older.dropoff, order.pickup) is None
            or planner.ticks(order.dropoff, older.pickup) is None
            for older in orders
        ):
            continue
        orders.append(order)
        job_ticks.append(snapshot.load_ticks + loaded + snapshot.unload_ticks)
    return HorizonProblem(
        tick=snapshot.tick,
        robots=robots,
        orders=tuple(orders),
        job_ticks=tuple(job_ticks),
        start_ticks=tuple(tuple(planner.ticks(r.node, o.pickup) for o in orders) for r in robots),
        setup_ticks=tuple(
            tuple(
                0 if i == j else _trip(planner, a.dropoff, b.pickup) for j, b in enumerate(orders)
            )
            for i, a in enumerate(orders)
        ),
    )


def completion_offsets(problem: HorizonProblem, plan: Plan) -> tuple[int, ...]:
    """Each order's delivery time under ``plan``, in ticks from the decision tick.

    Every robot starts each job as early as it can. Waiting on purpose never helps here: all
    times in the problem are fixed, so a later start only delays the robot's later orders.
    """
    _check_shape(problem, plan)
    done = [0] * len(problem.orders)
    for robot, starts, sequence in zip(problem.robots, problem.start_ticks, plan, strict=True):
        time, previous = robot.ready, None
        for j in sequence:
            trip = starts[j] if previous is None else problem.setup_ticks[previous][j]
            if trip is None:
                raise InvalidPlanError(
                    f"robot {robot.robot_id!r} cannot reach the pickup of {problem.orders[j].id!r}"
                )
            time += trip + problem.job_ticks[j]
            done[j] = time
            previous = j
    return tuple(done)


def total_completion(problem: HorizonProblem, plan: Plan) -> int:
    """The objective: the sum of the planned orders' delivery times, in ticks from now."""
    return sum(completion_offsets(problem, plan))


def first_jobs(problem: HorizonProblem, plan: Plan) -> list[Assignment]:
    """The part of ``plan`` to carry out now: each idle robot's first order, oldest first."""
    completion_offsets(problem, plan)  # validates the whole plan
    jobs = sorted(
        (sequence[0], robot.robot_id)
        for robot, sequence in zip(problem.robots, plan, strict=True)
        if robot.idle and sequence
    )
    return [Assignment(robot_id, problem.orders[j].id) for j, robot_id in jobs]


def _check_shape(problem: HorizonProblem, plan: Sequence[Sequence[int]]) -> None:
    if len(plan) != len(problem.robots):
        raise InvalidPlanError(
            f"a plan needs one sequence per robot: {len(plan)} for {len(problem.robots)} robots"
        )
    planned = sorted(j for sequence in plan for j in sequence)
    if planned != list(range(len(problem.orders))):
        raise InvalidPlanError(f"every order must be planned exactly once, got {planned}")


def _trip(planner: TravelPlanner, source: NodeId, target: NodeId) -> int:
    ticks = planner.ticks(source, target)
    if ticks is None:
        raise ValueError(f"no route {source!r}->{target!r}")
    return ticks
