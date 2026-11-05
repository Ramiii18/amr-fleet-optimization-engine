"""The reservation table: which robot occupies which node and corridor, and when (D23, D24).

Robots plan one after another (prioritized planning). Each planned path is reserved here, and
later plans must avoid it. The table refuses any reservation that overlaps another robot's, so
a planner bug surfaces at the moment it happens, not as a collision found later.

Each resource keeps its reservations sorted by start. They never overlap each other, so a new
reservation can only overlap its neighbours in that order: checking a reservation costs
O(log k) for k reservations on its resource.

A path that ends in a hold occupies its last node until `FOREVER`. When the robot plans its
next path, `end_hold` cuts the hold short at the tick the new path takes over.
"""

import bisect
from collections import defaultdict
from dataclasses import dataclass

from amr_optimizer.domain import RobotId
from amr_optimizer.traffic.paths import FOREVER, Corridor, Occupancy, Resource, TimedPath
from amr_optimizer.warehouse import NodeId


class ReservationConflictError(RuntimeError):
    """A reservation overlaps one held by another robot: a bug in whoever planned it."""


@dataclass(frozen=True, slots=True, order=True)
class Reservation:
    """``robot`` occupies the resource from ``start`` to ``end``: closed for a node, open for a
    corridor (see `amr_optimizer.traffic.paths`)."""

    start: int
    end: int
    robot: RobotId


class ReservationTable:
    """All current reservations of nodes and corridors."""

    __slots__ = ("_by_resource", "_holds")

    def __init__(self) -> None:
        self._by_resource: dict[Resource, list[Reservation]] = defaultdict(list)
        self._holds: dict[RobotId, tuple[NodeId, Reservation]] = {}

    # --- adding and removing -----------------------------------------------------------------

    def reserve(self, robot: RobotId, path: TimedPath) -> None:
        """Reserve every occupancy of ``path`` for ``robot``, or nothing at all.

        Raises `ReservationConflictError` if any occupancy overlaps another robot's
        reservation, or if ``robot`` still holds a node (end the hold first).
        """
        if robot in self._holds:
            raise ReservationConflictError(f"robot {robot!r} still holds {self._holds[robot][0]!r}")
        occupancies = list(path.occupancies())
        for occupancy in occupancies:
            clash = self._overlapping(occupancy)
            if clash is not None:
                raise ReservationConflictError(
                    f"robot {robot!r} at {occupancy.resource!r} during "
                    f"{occupancy.start}..{occupancy.end} overlaps robot {clash.robot!r} "
                    f"({clash.start}..{clash.end})"
                )
        for occupancy in occupancies:
            entry = Reservation(occupancy.start, occupancy.end, robot)
            bisect.insort(self._by_resource[occupancy.resource], entry)
        if path.holds:
            self._holds[robot] = (path.end.node, Reservation(path.end.arrive, FOREVER, robot))

    def end_hold(self, robot: RobotId, tick: int) -> NodeId:
        """End ``robot``'s hold so that its next path can take over at ``tick``.

        The hold then covers up to ``tick - 1``, or disappears if it began at ``tick`` or later.
        Returns the held node. Raises `KeyError` if the robot holds nothing.
        """
        node, hold = self._holds.pop(robot)
        entries = self._by_resource[node]
        entries.remove(hold)
        if tick > hold.start:
            bisect.insort(entries, Reservation(hold.start, tick - 1, robot))
        return node

    def release(self, robot: RobotId) -> None:
        """Remove every reservation of ``robot``, its hold included, so that its plan can be
        reserved again with new times (re-timing, D28)."""
        self._holds.pop(robot, None)
        for resource, entries in self._by_resource.items():
            if any(entry.robot == robot for entry in entries):
                self._by_resource[resource] = [e for e in entries if e.robot != robot]

    def prune(self, before: int) -> None:
        """Forget reservations that ended before tick ``before``. They cannot affect any plan
        that starts at ``before`` or later."""
        for resource, entries in self._by_resource.items():
            self._by_resource[resource] = [e for e in entries if e.end >= before]

    # --- questions ---------------------------------------------------------------------------

    def holder(self, node: NodeId) -> RobotId | None:
        """The robot holding ``node`` with no planned departure, if any."""
        for robot, (held, _) in self._holds.items():
            if held == node:
                return robot
        return None

    def held_node(self, robot: RobotId) -> NodeId | None:
        hold = self._holds.get(robot)
        return None if hold is None else hold[0]

    def is_free_from(self, node: NodeId, tick: int) -> bool:
        """True if no robot occupies ``node`` at ``tick`` or later."""
        return all(entry.end < tick for entry in self._by_resource.get(node, ()))

    def node_reservations(self, node: NodeId) -> tuple[Reservation, ...]:
        return tuple(self._by_resource.get(node, ()))

    def corridor_reservations(self, u: NodeId, v: NodeId) -> tuple[Reservation, ...]:
        """Reservations of the corridor between ``u`` and ``v``, in either direction."""
        return tuple(self._by_resource.get(Corridor.between(u, v), ()))

    def safe_intervals(self, node: NodeId, after: int = 0) -> list[tuple[int, int]]:
        """The maximal closed intervals [s, e] of ticks >= ``after`` in which ``node`` is free.

        The last interval ends at `FOREVER` unless the node is held. These are the "safe
        intervals" that SIPP searches over (D24).
        """
        intervals: list[tuple[int, int]] = []
        start = after
        for entry in self._by_resource.get(node, ()):
            if entry.end < start:
                continue
            if entry.start > start:
                intervals.append((start, entry.start - 1))
            start = max(start, entry.end + 1)
            if start > FOREVER:
                return intervals
        if start <= FOREVER:
            intervals.append((start, FOREVER))
        return intervals

    def _overlapping(self, occupancy: Occupancy) -> Reservation | None:
        """A reservation by another robot that overlaps ``occupancy``, if there is one."""
        entries = self._by_resource.get(occupancy.resource, [])
        index = bisect.bisect_left(entries, (occupancy.start,), key=lambda e: (e.start,))
        for neighbour in entries[max(0, index - 1) : index + 1]:
            other = Occupancy(occupancy.resource, neighbour.start, neighbour.end)
            if occupancy.overlaps(other):
                return neighbour
        # Reservations starting inside the new occupancy, beyond the immediate neighbour.
        for neighbour in entries[index + 1 :]:
            if neighbour.start > occupancy.end:
                break
            if occupancy.overlaps(Occupancy(occupancy.resource, neighbour.start, neighbour.end)):
                return neighbour
        return None
