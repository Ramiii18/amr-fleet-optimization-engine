"""Executing planned timed paths when robots are delayed (D25).

A plan says when every robot arrives at and leaves every node. When a robot is delayed while
driving, those times can no longer be met. Two ways to execute a plan anyway:

- **Naive**: every robot keeps its own schedule. It leaves each node at its planned time, or as
  soon as it can if it is late, and ignores everyone else. Delays then cause conflicts.
- **Ordered**: every robot uses every node and corridor in the order the plan gives (the idea of
  the temporal plan graph and of the action dependency graph, D25):
  - it enters a corridor only after every robot planned earlier on that corridor has left it;
  - it enters a node only after every robot planned earlier at that node has left it, at least one
    tick later (D23); until then it waits at the end of its corridor.

  No robot leaves a node before its planned time, nor before its service there is done.

**One pass suffices.** In a conflict-free plan, every dependency of ordered execution goes from an
event planned earlier to one planned later. Sorting all events by planned time (arrivals before
departures at equal times) therefore lists every event after the events it depends on, and the
actual time of each event follows from those already computed. This is a longest-path computation
in a directed acyclic graph, so ordered execution always finishes: it cannot deadlock.
"""

import bisect
import itertools
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from amr_optimizer.domain import RobotId, TravelTimes
from amr_optimizer.traffic.conflicts import path_conflicts
from amr_optimizer.traffic.paths import (
    FOREVER,
    Corridor,
    Resource,
    TimedPath,
    Visit,
    validate_timed_path,
)
from amr_optimizer.warehouse import NodeId


class ExecutionPolicy(Enum):
    """How plans are executed when robots are delayed (D25)."""

    NAIVE = "naive"  # keep each robot's own schedule; conflicts possible
    ORDERED = "ordered"  # keep the planned order of robots at every node and corridor


@dataclass(frozen=True, slots=True)
class DelayModel:
    """Each time a robot drives an edge, with probability ``probability`` it takes extra time,
    drawn uniformly from ``min_ticks`` to ``max_ticks``."""

    probability: float
    min_ticks: int = 1
    max_ticks: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.probability <= 1:
            raise ValueError(f"delay probability must be in [0, 1], got {self.probability}")
        if not 1 <= self.min_ticks <= self.max_ticks:
            raise ValueError(
                f"delays need 1 <= min_ticks <= max_ticks, got {self.min_ticks}, {self.max_ticks}"
            )

    def draw(self, rng: random.Random) -> int:
        """The extra ticks of one edge traversal. Always consumes the same amount of the stream,
        delayed or not, so that one robot's sequence of delays doesn't depend on the outcomes."""
        delayed = rng.random() < self.probability
        extra = rng.randint(self.min_ticks, self.max_ticks)
        return extra if delayed else 0


NO_DELAYS = DelayModel(0.0)


def execute_plans(
    plans: Mapping[RobotId, TimedPath],
    travel_times: TravelTimes,
    *,
    policy: ExecutionPolicy,
    delays: Mapping[RobotId, Sequence[int]] | None = None,
    min_dwell: Mapping[RobotId, Sequence[int]] | None = None,
) -> dict[RobotId, TimedPath]:
    """The trajectories that actually happen when ``plans`` are executed under ``policy``.

    ``delays[r][k]`` is the extra time of robot r's k-th edge (default 0). ``min_dwell[r][i]`` is
    the least time robot r must stay at its i-th visit, such as loading (default 0). Ordered
    execution requires ``plans`` to be conflict-free (checked), and every plan to drive real edges
    in their travel times (checked for both policies).
    """
    for path in plans.values():
        validate_timed_path(path, travel_times)
    ordered = policy is ExecutionPolicy.ORDERED
    if ordered and path_conflicts({r: [p] for r, p in plans.items()}):
        raise ValueError("ordered execution needs conflict-free plans")

    def delay(robot: RobotId, edge: int) -> int:
        values = delays.get(robot, ()) if delays else ()
        return values[edge] if edge < len(values) else 0

    def dwell(robot: RobotId, visit: int) -> int:
        values = min_dwell.get(robot, ()) if min_dwell else ()
        return values[visit] if visit < len(values) else 0

    # Every robot's use of every node and corridor, in planned order.
    node_users: dict[NodeId, list[tuple[int, RobotId, int]]] = defaultdict(list)
    corridor_users: dict[Corridor, list[tuple[int, RobotId, int]]] = defaultdict(list)
    events: list[tuple[int, int, RobotId, int]] = []  # (planned tick, 0 arrive / 1 depart, ...)
    for robot, path in plans.items():
        for i, visit in enumerate(path.visits):
            node_users[visit.node].append((visit.arrive, robot, i))
            events.append((visit.arrive, 0, robot, i))
            if visit.depart < FOREVER:
                events.append((visit.depart, 1, robot, i))
            if i + 1 < len(path.visits):
                corridor = Corridor.between(visit.node, path.visits[i + 1].node)
                corridor_users[corridor].append((visit.depart, robot, i))
    node_rank = {
        (r, i): k for users in node_users.values() for k, (_, r, i) in enumerate(sorted(users))
    }
    corridor_rank = {
        (r, i): k for users in corridor_users.values() for k, (_, r, i) in enumerate(sorted(users))
    }
    for users in (*node_users.values(), *corridor_users.values()):
        users.sort()

    arrive: dict[tuple[RobotId, int], int] = {}
    depart: dict[tuple[RobotId, int], int] = {}
    for _, kind, robot, i in sorted(events, key=lambda e: (e[0], e[1], e[2], e[3])):
        path = plans[robot]
        visit = path.visits[i]
        if kind == 0:  # arrival at visit i
            if i == 0:
                arrive[robot, 0] = visit.arrive
                continue
            before = path.visits[i - 1]
            ticks = travel_times.edge_ticks(before.node, visit.node)
            tick = depart[robot, i - 1] + ticks + delay(robot, i - 1)
            if ordered:  # wait at the end of the corridor until the node is free
                for _, other, j in node_users[visit.node][: node_rank[robot, i]]:
                    tick = max(tick, depart[other, j] + 1)
            arrive[robot, i] = tick
        else:  # departure from visit i
            tick = max(visit.depart, arrive[robot, i] + dwell(robot, i))
            if ordered:  # enter the corridor only after the robots planned before have left it
                corridor = Corridor.between(visit.node, path.visits[i + 1].node)
                for _, other, j in corridor_users[corridor][: corridor_rank[robot, i]]:
                    tick = max(tick, arrive[other, j + 1])
            depart[robot, i] = tick

    return {
        robot: TimedPath(
            tuple(
                Visit(v.node, arrive[robot, i], depart.get((robot, i), FOREVER))
                for i, v in enumerate(path.visits)
            )
        )
        for robot, path in plans.items()
    }


class ResourceOrder:
    """The planned order of robots at every node and corridor, during execution (D25).

    The online counterpart of `execute_plans`, for a simulation in which plans keep arriving.
    Every use of a resource is queued by its planned start tick. A robot may enter a resource
    only when its use is first in the queue, so every robot planned earlier has left it; a
    node also needs one tick of clearance after the previous robot left (D23). Leaving a
    resource removes the robot's use from the front of the queue.

    New plans are made from the current tick on, so their uses are queued behind every use
    planned earlier, even uses of robots that are running late. Every wait therefore points
    forward in planned time, and execution cannot deadlock (D25).
    """

    __slots__ = ("_counter", "_left_at", "_queues")

    def __init__(self) -> None:
        self._queues: dict[Resource, list[tuple[int, int, RobotId]]] = defaultdict(list)
        self._left_at: dict[Resource, int] = {}
        self._counter = itertools.count()

    def add(self, robot: RobotId, path: TimedPath, *, include_start: bool) -> None:
        """Queue every use of ``path``. Its first node is already queued if the robot is
        standing there from a previous plan; then ``include_start`` is false."""
        visits = path.visits
        if include_start:
            self._insert(visits[0].node, visits[0].arrive, robot)
        for before, after in itertools.pairwise(visits):
            self._insert(Corridor.between(before.node, after.node), before.depart, robot)
            self._insert(after.node, after.arrive, robot)

    def next_user(self, resource: Resource) -> RobotId | None:
        queue = self._queues.get(resource)
        return queue[0][2] if queue else None

    def may_enter_corridor(self, source: NodeId, target: NodeId, robot: RobotId) -> bool:
        return self.next_user(Corridor.between(source, target)) == robot

    def earliest_arrival(self, node: NodeId, robot: RobotId) -> int | None:
        """The earliest tick ``robot`` may enter ``node``, or ``None`` while a robot planned
        earlier has yet to leave it."""
        if self.next_user(node) != robot:
            return None
        return self._left_at.get(node, -1) + 1

    def leave(self, resource: Resource, robot: RobotId, tick: int) -> RobotId | None:
        """``robot`` leaves ``resource`` at ``tick``. Returns the robot that may enter next."""
        queue = self._queues.get(resource)
        if not queue or queue[0][2] != robot:
            raise RuntimeError(f"robot {robot!r} leaves {resource!r} out of the planned order")
        queue.pop(0)
        self._left_at[resource] = tick
        return self.next_user(resource)

    def _insert(self, resource: Resource, tick: int, robot: RobotId) -> None:
        bisect.insort(self._queues[resource], (tick, next(self._counter), robot))
