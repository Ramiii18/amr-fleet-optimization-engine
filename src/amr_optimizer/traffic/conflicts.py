"""An independent conflict checker for timed paths (D23).

It is the reference that the reservation table, the path planner and the traffic-aware
simulator are tested against, so it is deliberately simple: group every occupancy by resource,
and compare every pair of robots on the same resource, using `Occupancy.overlaps`. It shares
no code with the reservation table beyond the occupancy definition.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from amr_optimizer.domain import RobotId
from amr_optimizer.traffic.paths import Occupancy, Resource, TimedPath


@dataclass(frozen=True, slots=True)
class Conflict:
    """Two robots occupy the same resource at a common moment."""

    resource: Resource
    first: tuple[RobotId, Occupancy]
    second: tuple[RobotId, Occupancy]

    @property
    def kind(self) -> str:
        """``vertex`` (same node), ``head-on`` (a corridor in opposite directions) or
        ``following`` (a corridor in the same direction)."""
        a, b = self.first[1], self.second[1]
        if a.is_node:
            return "vertex"
        return "following" if a.source == b.source else "head-on"


def find_conflicts(occupancies: Iterable[tuple[RobotId, Occupancy]]) -> list[Conflict]:
    """Every pair of occupancies by different robots that overlap, sorted by resource and time.

    Within a resource, occupancies are sorted by start, so the comparison of one occupancy
    stops at the first later occupancy that starts after it ends: nothing after that can
    overlap it. Pairs of the same robot are never conflicts.
    """
    by_resource: dict[Resource, list[tuple[RobotId, Occupancy]]] = defaultdict(list)
    for robot, occupancy in occupancies:
        by_resource[occupancy.resource].append((robot, occupancy))
    conflicts: list[Conflict] = []
    for resource in sorted(by_resource, key=repr):
        entries = sorted(by_resource[resource], key=lambda e: (e[1].start, e[1].end, e[0]))
        for i, (robot, occupancy) in enumerate(entries):
            for other_robot, other in entries[i + 1 :]:
                if other.start > occupancy.end:
                    break
                if other_robot != robot and occupancy.overlaps(other):
                    conflicts.append(Conflict(resource, (robot, occupancy), (other_robot, other)))
    return conflicts


def path_conflicts(paths: Mapping[RobotId, Sequence[TimedPath]]) -> list[Conflict]:
    """`find_conflicts` over every occupancy of every path of every robot."""
    return find_conflicts(
        (robot, occupancy)
        for robot, robot_paths in paths.items()
        for path in robot_paths
        for occupancy in path.occupancies()
    )
