"""Shared helpers for the planning tests: a brute-force oracle, random small problems, and
the look-ahead example as a snapshot.

The oracle tries every plan: every order of the orders, cut into one (possibly empty)
sequence per robot. That is n! * C(n + R - 1, R - 1) plans, for example 2,520 for five
orders and three robots, so it is only for small problems.
"""

import itertools
import random
from collections.abc import Callable

import pytest

from amr_optimizer.dispatch import (
    HorizonProblem,
    InvalidPlanError,
    Objective,
    Plan,
    RobotAvailability,
    plan_cost,
)
from amr_optimizer.domain import (
    OrderId,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.routing import Route
from amr_optimizer.sim import DispatchSnapshot, TravelPlanner
from amr_optimizer.warehouse import Edge, Node, NodeId, WarehouseGraph

BruteForce = Callable[[HorizonProblem], tuple[int, Plan]]
RandomProblem = Callable[..., HorizonProblem]


def all_plans(problem: HorizonProblem) -> list[Plan]:
    n, robots = len(problem.orders), len(problem.robots)
    plans = []
    for permutation in itertools.permutations(range(n)):
        for cuts in itertools.combinations_with_replacement(range(n + 1), robots - 1):
            bounds = (0, *cuts, n)
            plans.append(tuple(permutation[bounds[k] : bounds[k + 1]] for k in range(robots)))
    return plans


def brute_force_optimum(problem: HorizonProblem) -> tuple[int, Plan]:
    """The smallest cost (`plan_cost`, under the problem's objective) over all feasible plans,
    and the first plan found with it."""
    best: tuple[int, Plan] | None = None
    for plan in all_plans(problem):
        try:
            total = plan_cost(problem, plan)
        except InvalidPlanError:  # a first job that the robot cannot reach
            continue
        if best is None or total < best[0]:
            best = (total, plan)
    assert best is not None, "a valid problem always has a feasible plan"
    return best


def random_problem(
    rng: random.Random,
    robots: int,
    orders: int,
    *,
    unreachable: float = 0.2,
    objective: Objective = Objective.TOTAL,
) -> HorizonProblem:
    """Random times, without any geometry. Some robots are busy; some first trips are
    missing, but every order stays reachable by at least one robot.

    The waiting times are drawn last, so adding them left every earlier problem of a seed
    unchanged."""
    availability = []
    for r in range(robots):
        idle = rng.random() < 0.5
        ready = 0 if idle else rng.randint(1, 60)
        availability.append(RobotAvailability(RobotId(f"R{r}"), NodeId(f"X{r}"), ready, idle))
    starts = [
        [None if rng.random() < unreachable else rng.randint(0, 40) for _ in range(orders)]
        for _ in range(robots)
    ]
    for j in range(orders):
        if all(row[j] is None for row in starts):
            starts[rng.randrange(robots)][j] = rng.randint(0, 40)
    job_ticks = tuple(rng.randint(1, 40) for _ in range(orders))
    setup_ticks = tuple(
        tuple(0 if i == j else rng.randint(0, 40) for j in range(orders)) for i in range(orders)
    )
    return HorizonProblem(
        tick=0,
        robots=tuple(availability),
        orders=tuple(
            TransportOrder(OrderId(f"O{j}"), NodeId(f"P{j}"), NodeId(f"D{j}"), 0)
            for j in range(orders)
        ),
        job_ticks=job_ticks,
        start_ticks=tuple(tuple(row) for row in starts),
        setup_ticks=setup_ticks,
        waited=tuple(rng.randint(0, 150) for _ in range(orders)),
        objective=objective,
    )


@pytest.fixture
def look_ahead_snapshot() -> DispatchSnapshot:
    """The worked example of algorithms section 11.1, as the snapshot at tick 20.

    The line N0 <-> N1 <-> ... <-> N4 has 10 ticks per edge; loading takes 5 ticks and
    unloading 7. R1 is idle at N0. R2 is carrying O1 from N1 to N3 and will be ready there
    at tick 42. Pending: O2 (N4 -> N0, older) and O3 (N2 -> N4). The best plan gives O3 to
    R1 and leaves O2 for R2: 136 ticks of total completion time, against 156 for greedy.
    """
    n = [NodeId(f"N{i}") for i in range(5)]
    graph = WarehouseGraph(
        [Node(n[i], float(i), 0.0) for i in range(5)],
        [Edge(n[i], n[j], 1.0) for i in range(4) for i, j in ((i, i + 1), (i + 1, i))],
    )
    carried = TransportOrder(OrderId("O1"), n[1], n[3], 0)
    r2 = (
        RobotState.idle(RobotId("R2"), n[0])
        .assign(carried, Route((n[0], n[1]), 1.0), tick=0, until_tick=10)
        .arrive(tick=10, until_tick=15)
        .finish_loading(Route((n[1], n[2], n[3]), 2.0), tick=15, until_tick=35)
    )
    return DispatchSnapshot(
        20,
        (RobotState.idle(RobotId("R1"), n[0]), r2),
        (
            TransportOrder(OrderId("O2"), n[4], n[0], 1),
            TransportOrder(OrderId("O3"), n[2], n[4], 2),
        ),
        TravelPlanner(TravelTimes(graph, speed_mps=1.0, time_model=TimeModel(0.1))),
        load_ticks=5,
        unload_ticks=7,
    )


@pytest.fixture
def myopic_problem() -> HorizonProblem:
    """A case where cheapest insertion is myopic (algorithms section 11.2).

    Two idle robots. O0 is 10 ticks from R1 and 11 from R2; O1 is 10 from R1 and 100 from
    R2. Every job takes 10 ticks, and every trip between the two orders 50. Insertion gives
    the older O0 to R1 and ends at 100; the optimum, R1 taking O1 and R2 taking O0, is 41.
    """
    return HorizonProblem(
        tick=0,
        robots=(
            RobotAvailability(RobotId("R1"), NodeId("X1"), 0, idle=True),
            RobotAvailability(RobotId("R2"), NodeId("X2"), 0, idle=True),
        ),
        orders=tuple(
            TransportOrder(OrderId(f"O{j}"), NodeId(f"P{j}"), NodeId(f"D{j}"), j) for j in range(2)
        ),
        job_ticks=(10, 10),
        start_ticks=((10, 10), (11, 100)),
        setup_ticks=((0, 50), (50, 0)),
        waited=(0, 0),
    )


@pytest.fixture
def fairness_problem() -> HorizonProblem:
    """A case where the two objectives disagree (algorithms section 11.5).

    One idle robot. Order A has waited 100 ticks; its pickup is 50 ticks away. Order B has
    just arrived and is right next to the robot. Each job takes 10 ticks, and the trip between
    the two orders 50 either way. Serving B first gives the lower total (80 against 180), but
    A's lead time then becomes 170 instead of 160.
    """
    return HorizonProblem(
        tick=0,
        robots=(RobotAvailability(RobotId("R1"), NodeId("X"), 0, idle=True),),
        orders=(
            TransportOrder(OrderId("A"), NodeId("PA"), NodeId("DA"), 0),
            TransportOrder(OrderId("B"), NodeId("PB"), NodeId("DB"), 0),
        ),
        job_ticks=(10, 10),
        start_ticks=((50, 0),),
        setup_ticks=((0, 50), (50, 0)),
        waited=(100, 0),
    )


@pytest.fixture
def brute_force() -> BruteForce:
    return brute_force_optimum


@pytest.fixture
def enumerate_plans() -> Callable[[HorizonProblem], list[Plan]]:
    return all_plans


@pytest.fixture
def make_problem() -> RandomProblem:
    return random_problem
