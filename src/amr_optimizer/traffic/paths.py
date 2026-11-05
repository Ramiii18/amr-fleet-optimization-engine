"""Timed paths, and the resources they occupy (D23).

A `TimedPath` says where a robot is at every tick: it stands at each visited node from its
arrival tick to its departure tick, and drives along an edge in between. Driving an edge takes
exactly the edge's travel time (D12), so a path is valid only if every gap between two visits
equals the travel time of the edge that joins them. Waiting is a visit that lasts longer.

Occupancy, the model's definition of "being somewhere":

- a robot at node v from tick a to tick d occupies v during the **closed** interval [a, d];
- a robot driving from u to v, departing at d and arriving at d + t, occupies the corridor
  {u, v} during the **open** interval (d, d + t). The corridor is one resource for both
  directions, so head-on, swap and following conflicts on one corridor are all conflicts.

A path may end in a **hold**: the robot stays at its last node with no planned departure. Its
departure is then `FOREVER`, a tick later than any simulation reaches.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from amr_optimizer.domain import TravelTimes
from amr_optimizer.warehouse import NodeId

FOREVER = 2**62
"""The departure tick of a hold: later than any tick a simulation can reach."""


class InvalidTimedPathError(ValueError):
    """A timed path that is inconsistent in itself or with the map's travel times."""


@dataclass(frozen=True, slots=True, order=True)
class Corridor:
    """The undirected connection between two nodes: one resource for both directions.

    Build it with `Corridor.between`, which orders the two ends, so that u -> v and v -> u
    give the same corridor.
    """

    a: NodeId
    b: NodeId

    def __post_init__(self) -> None:
        if not self.a < self.b:
            raise ValueError(f"corridor ends must be distinct and ordered: {self.a!r}, {self.b!r}")

    @classmethod
    def between(cls, u: NodeId, v: NodeId) -> "Corridor":
        return cls(u, v) if u < v else cls(v, u)


Resource = NodeId | Corridor


@dataclass(frozen=True, slots=True)
class Occupancy:
    """One stretch of time during which a robot occupies a resource.

    For a node, the interval is closed: [start, end]. For a corridor it is open: (start, end),
    and ``source`` and ``target`` record the direction of travel.
    """

    resource: Resource
    start: int
    end: int
    source: NodeId | None = None
    target: NodeId | None = None

    @property
    def is_node(self) -> bool:
        return not isinstance(self.resource, Corridor)

    def overlaps(self, other: "Occupancy") -> bool:
        """True if both occupy the same resource at some common moment (see the module)."""
        if self.resource != other.resource:
            return False
        if self.is_node:
            return self.start <= other.end and other.start <= self.end
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class Visit:
    """A robot stands at ``node`` from tick ``arrive`` until tick ``depart``, both included."""

    node: NodeId
    arrive: int
    depart: int

    def __post_init__(self) -> None:
        if self.arrive < 0:
            raise InvalidTimedPathError(f"visit to {self.node!r}: arrival {self.arrive} < 0")
        if self.depart < self.arrive:
            raise InvalidTimedPathError(
                f"visit to {self.node!r}: departure {self.depart} before arrival {self.arrive}"
            )


@dataclass(frozen=True, slots=True)
class TimedPath:
    """A sequence of visits; between two visits the robot drives the edge that joins them.

    Checked on construction: at least one visit, consecutive visits at different nodes, each
    arrival after the previous departure (driving takes at least one tick), and only the last
    visit may be a hold. `validate_timed_path` also checks the edges and their travel times.
    """

    visits: tuple[Visit, ...]

    def __post_init__(self) -> None:
        if not self.visits:
            raise InvalidTimedPathError("a timed path needs at least one visit")
        for before, after in zip(self.visits, self.visits[1:], strict=False):
            if before.node == after.node:
                raise InvalidTimedPathError(
                    f"two consecutive visits to {before.node!r}: merge them into one wait"
                )
            if before.depart == FOREVER:
                raise InvalidTimedPathError("only the last visit of a path can be a hold")
            if after.arrive <= before.depart:
                raise InvalidTimedPathError(
                    f"arrival at {after.node!r} ({after.arrive}) must come after leaving "
                    f"{before.node!r} ({before.depart})"
                )

    @property
    def start(self) -> Visit:
        return self.visits[0]

    @property
    def end(self) -> Visit:
        return self.visits[-1]

    @property
    def holds(self) -> bool:
        """True if the robot stays at the last node with no planned departure."""
        return self.end.depart == FOREVER

    @property
    def nodes(self) -> tuple[NodeId, ...]:
        return tuple(visit.node for visit in self.visits)

    def occupancies(self) -> Iterator[Occupancy]:
        """Every node and corridor occupancy of the path, in time order."""
        for index, visit in enumerate(self.visits):
            yield Occupancy(visit.node, visit.arrive, visit.depart)
            if index + 1 < len(self.visits):
                after = self.visits[index + 1]
                yield Occupancy(
                    Corridor.between(visit.node, after.node),
                    visit.depart,
                    after.arrive,
                    visit.node,
                    after.node,
                )


def validate_timed_path(path: TimedPath, travel_times: TravelTimes) -> None:
    """Raise `InvalidTimedPathError` unless every step of ``path`` drives an existing edge in
    exactly that edge's travel time."""
    for before, after in zip(path.visits, path.visits[1:], strict=False):
        try:
            ticks = travel_times.edge_ticks(before.node, after.node)
        except LookupError as error:
            raise InvalidTimedPathError(
                f"no edge {before.node!r} -> {after.node!r} on the map"
            ) from error
        if after.arrive - before.depart != ticks:
            raise InvalidTimedPathError(
                f"edge {before.node!r} -> {after.node!r} takes {ticks} ticks, the path allows "
                f"{after.arrive - before.depart}"
            )
