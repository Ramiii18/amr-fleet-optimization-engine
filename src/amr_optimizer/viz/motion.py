"""Where a robot is, and what it is doing, at any tick of a run. Pure functions, no plotting.

Positions come from the robot's recorded trajectory (a timed path): at a node during a visit,
or part of the way along the edge to the next node while driving. Robots drive at constant
speed (D12), so the position along an edge is linear in time.

What a robot is doing comes from the event log: it is driving to a pickup (or loading) from
its assignment until the load is on, carrying a load until delivery, and free otherwise (idle,
or driving to a holding spot with traffic).
"""

import bisect
from collections.abc import Sequence
from enum import Enum

from amr_optimizer.domain import RobotId
from amr_optimizer.sim import EventKind, LogEntry
from amr_optimizer.traffic import TimedPath
from amr_optimizer.warehouse import WarehouseGraph


class Activity(Enum):
    """What a robot is doing, in the three categories the figures colour."""

    TO_PICKUP = "to pickup"  # assigned, driving to the pickup or loading
    LOADED = "loaded"  # carrying a load to its drop-off, or unloading
    FREE = "free"  # idle, or moving to a holding spot


def position_at(path: TimedPath, tick: float, graph: WarehouseGraph) -> tuple[float, float]:
    """The robot's (x, y) in metres at ``tick``, which may fall between whole ticks.

    Before the path starts, the robot is at its first node; after it ends, at its last.
    """
    visits = path.visits
    if tick <= visits[0].arrive:
        node = graph.node(visits[0].node)
        return node.x, node.y
    for visit, following in zip(visits, visits[1:], strict=False):
        if tick <= visit.depart:
            node = graph.node(visit.node)
            return node.x, node.y
        if tick < following.arrive:
            share = (tick - visit.depart) / (following.arrive - visit.depart)
            a, b = graph.node(visit.node), graph.node(following.node)
            return a.x + share * (b.x - a.x), a.y + share * (b.y - a.y)
    node = graph.node(visits[-1].node)
    return node.x, node.y


_CHANGES = {
    EventKind.ASSIGNED: Activity.TO_PICKUP,
    EventKind.LOADED: Activity.LOADED,
    EventKind.DELIVERED: Activity.FREE,
}


def activity_at(log: Sequence[LogEntry], robot: RobotId, tick: int) -> Activity:
    """What ``robot`` is doing at ``tick``, from the run's event log.

    The log is in time order, and a robot does one job at a time (tested in the simulator), so
    the robot's last assignment, load or delivery event at or before ``tick`` decides it.
    This is the reference definition; `ActivityTimeline` answers the same question faster.
    """
    activity = Activity.FREE
    for entry in log:
        if entry.tick > tick:
            break
        if entry.robot_id == robot and entry.kind in _CHANGES:
            activity = _CHANGES[entry.kind]
    return activity


class ActivityTimeline:
    """Every robot's activity changes, extracted once from a log, for fast lookups."""

    __slots__ = ("_changes",)

    def __init__(self, log: Sequence[LogEntry]) -> None:
        self._changes: dict[RobotId, tuple[list[int], list[Activity]]] = {}
        for entry in log:
            if entry.robot_id is not None and entry.kind in _CHANGES:
                ticks, activities = self._changes.setdefault(entry.robot_id, ([], []))
                ticks.append(entry.tick)
                activities.append(_CHANGES[entry.kind])

    def at(self, robot: RobotId, tick: int) -> Activity:
        """The same answer as `activity_at`, by binary search."""
        if robot not in self._changes:
            return Activity.FREE
        ticks, activities = self._changes[robot]
        index = bisect.bisect_right(ticks, tick) - 1
        return Activity.FREE if index < 0 else activities[index]
