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

**Re-timing** (`retime`, D28) is the same computation started mid-run: the events that already
happened are fixed, and the rest is scheduled as early as the order allows, assuming no further
delays. It brings stale plans back to one clock that matches reality, without changing any route or
any order.
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


class Recovery(Enum):
    """What the fleet does when a breakdown is announced (D28)."""

    WAIT = "wait"  # nothing: robots behind the broken one wait (ordered execution)
    RETIME = "retime"  # re-time every plan, so that new plans know about the breakdown
    REPLAN = "replan"  # re-time, then replan every delayed robot


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


@dataclass(frozen=True, slots=True)
class Progress:
    """Where a robot stands on its remaining plan, mid-run (an input of `retime`).

    The remaining plan starts at the visit the robot is at, or at the one it left last if it is
    in the corridor after it. ``arrived`` is when it actually arrived there, and ``departed`` when
    it actually left, if it has. ``ready`` is the earliest tick of its next event: reaching the
    end of its corridor (a breakdown or a delay makes this later than planned), or leaving its
    visit once any service there is done.
    """

    arrived: int
    departed: int | None = None
    ready: int = 0


def retime(
    plans: Mapping[RobotId, TimedPath],
    progress: Mapping[RobotId, Progress],
    travel_times: TravelTimes,
    *,
    now: int,
    left_at: Mapping[Resource, int] | None = None,
    min_dwell: Mapping[RobotId, Mapping[int, int]] | None = None,
) -> dict[RobotId, TimedPath]:
    """The earliest schedule of ``plans`` from a state reached mid-run, under ordered execution
    and assuming no further delays (D28).

    ``plans[r]`` is robot r's remaining plan, from the visit given by ``progress[r]``. Its planned
    times give the order of robots at every node and corridor; they must therefore come from
    plans that are mutually conflict-free, as every plan reserved in one table is. No pending
    event happens before ``now``, no robot leaves a visit before its planned time, and
    ``min_dwell[r][i]`` is the service at robot r's later visit i. ``left_at`` gives when each
    resource was last left by a robot that is done with it: a node needs one tick of clearance.

    The result keeps every robot's route and the order at every resource, so the plans it returns
    are again mutually conflict-free. This is the one-pass computation of `execute_plans`, with
    the events that already happened fixed.
    """
    left_at = left_at or {}

    def dwell(robot: RobotId, visit: int) -> int:
        values = min_dwell.get(robot, {}) if min_dwell else {}
        return values.get(visit, 0)

    node_users: dict[NodeId, list[tuple[int, RobotId, int]]] = defaultdict(list)
    corridor_users: dict[Corridor, list[tuple[int, RobotId, int]]] = defaultdict(list)
    events: list[tuple[int, int, RobotId, int]] = []  # (planned tick, 0 arrive / 1 depart, ...)
    arrive: dict[tuple[RobotId, int], int] = {}
    depart: dict[tuple[RobotId, int], int] = {}
    for robot, path in plans.items():
        state = progress[robot]
        visits = path.visits
        arrive[robot, 0] = state.arrived
        if state.departed is not None:  # done with its first node, in the corridor after it
            depart[robot, 0] = state.departed
        else:
            node_users[visits[0].node].append((visits[0].arrive, robot, 0))
            if visits[0].depart < FOREVER:
                events.append((visits[0].depart, 1, robot, 0))
        for i, visit in enumerate(visits):
            if i:
                node_users[visit.node].append((visit.arrive, robot, i))
                events.append((visit.arrive, 0, robot, i))
                if visit.depart < FOREVER:
                    events.append((visit.depart, 1, robot, i))
            if i + 1 < len(visits):
                corridor = Corridor.between(visit.node, visits[i + 1].node)
                corridor_users[corridor].append((visit.depart, robot, i))
    for users in (*node_users.values(), *corridor_users.values()):
        users.sort()
    node_rank = {(r, i): k for users in node_users.values() for k, (_, r, i) in enumerate(users)}
    corridor_rank = {
        (r, i): k for users in corridor_users.values() for k, (_, r, i) in enumerate(users)
    }

    for _, kind, robot, i in sorted(events):
        state, visits = progress[robot], plans[robot].visits
        visit = visits[i]
        if kind == 0:  # arrival at visit i, at the end of the corridor from visit i - 1
            before = visits[i - 1]
            tick = depart[robot, i - 1] + travel_times.edge_ticks(before.node, visit.node)
            if i == 1 and state.departed is not None:
                tick = max(tick, state.ready)
            tick = max(tick, now, left_at.get(visit.node, -1) + 1)
            for _, other, j in node_users[visit.node][: node_rank[robot, i]]:
                tick = max(tick, depart[other, j] + 1)
            arrive[robot, i] = tick
        else:  # departure from visit i into the corridor to visit i + 1
            tick = max(visit.depart, arrive[robot, i] + dwell(robot, i), now)
            if i == 0:
                tick = max(tick, state.ready)
            corridor = Corridor.between(visit.node, visits[i + 1].node)
            tick = max(tick, left_at.get(corridor, -1))
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

    def requeue(self, robot: RobotId, path: TimedPath, *, in_corridor: bool) -> None:
        """Replace ``robot``'s queued uses by those of ``path``, its remaining plan with new
        times: from the node it stands on, or from the corridor after that node if it is in it.

        The new times must keep the order of robots at every resource (re-timing does, D28) or
        come from a plan reserved against all others (replanning).
        """
        for resource, queue in self._queues.items():
            if any(entry[2] == robot for entry in queue):
                self._queues[resource] = [entry for entry in queue if entry[2] != robot]
        self.add(robot, path, include_start=not in_corridor)

    @property
    def left_at(self) -> Mapping[Resource, int]:
        """The tick each resource was last left."""
        return self._left_at

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
