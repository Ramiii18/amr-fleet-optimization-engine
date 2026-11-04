"""Simulation time in integer ticks, and travel times derived from edge lengths (D6, D12).

Every time inside the simulator is a whole number of ticks; seconds appear only at the
boundaries (configuration in, KPIs out). Integer times make "two robots at the same node
at the same time" an exact equality test, which conflict checks in M4 rely on.

Converting a physical duration to ticks always rounds **up**: a robot is never assumed to
arrive earlier than it physically could. Floating-point noise is not rounded up, though:
5.4 m at 1.2 m/s is exactly 45 ticks of 0.1 s, but the division evaluates to
45.00000000000001, and a plain ``ceil`` would charge 46. Values within
`TICK_ROUNDING_TOLERANCE` of a whole number snap to it.
"""

import itertools
import math
from dataclasses import dataclass

from amr_optimizer.routing import InvalidRouteError, Route
from amr_optimizer.warehouse import EdgeKey, NodeId, WarehouseGraph

TICK_ROUNDING_TOLERANCE = 1e-9
"""Relative tolerance below which a duration counts as a whole number of ticks."""


def _is_whole(value: float) -> bool:
    """True if a non-negative ``value`` is a whole number up to floating-point noise."""
    return abs(value - round(value)) <= TICK_ROUNDING_TOLERANCE * max(1.0, value)


def _ceil_ticks(value: float) -> int:
    """Round a non-negative number of ticks up, ignoring floating-point noise."""
    return round(value) if _is_whole(value) else math.ceil(value)


def _require_positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")


@dataclass(frozen=True, slots=True)
class TimeModel:
    """The length of one simulation tick, and conversions between seconds and ticks.

    A shorter tick loses less to rounding, but makes every time-indexed structure larger
    (reservation tables and space-time search in M4 grow with the number of ticks).
    """

    tick_s: float
    """Seconds per tick."""

    def __post_init__(self) -> None:
        _require_positive("tick_s", self.tick_s)

    def to_ticks(self, seconds: float) -> int:
        """A duration in whole ticks, rounded up. Zero seconds is zero ticks."""
        if not (math.isfinite(seconds) and seconds >= 0):
            raise ValueError(f"duration must be a non-negative finite number, got {seconds!r}")
        return _ceil_ticks(seconds / self.tick_s)

    def to_seconds(self, ticks: int) -> float:
        """A tick count (duration or timestamp) in seconds, for reporting."""
        if ticks < 0:
            raise ValueError(f"tick count must be non-negative, got {ticks!r}")
        return ticks * self.tick_s

    def travel_ticks(self, length_m: float, speed_mps: float) -> int:
        """Ticks needed to drive ``length_m`` at ``speed_mps``: rounded up, and at least 1.

        The minimum of one tick keeps every move strictly positive in time, so a robot can
        never leave and enter nodes within the same tick (the time analogue of D5).
        """
        _require_positive("length_m", length_m)
        _require_positive("speed_mps", speed_mps)
        return max(1, self.to_ticks(length_m / speed_mps))


class TravelTimes:
    """Travel time in ticks for every edge of one graph, at one robot speed.

    The table is computed once, in O(E), so the simulator and the dispatchers look times
    up instead of recomputing them. Each edge is rounded up separately, because a robot
    must reach every node on its route at a whole tick. A route's duration is therefore
    the sum of its edge durations, which can exceed its total length converted at once by
    up to one tick per edge (D12).
    """

    __slots__ = ("_edge_ticks", "_graph", "_is_exact", "_speed_mps", "_time_model")

    def __init__(self, graph: WarehouseGraph, *, speed_mps: float, time_model: TimeModel) -> None:
        _require_positive("speed_mps", speed_mps)
        self._graph = graph
        self._speed_mps = speed_mps
        self._time_model = time_model
        self._edge_ticks: dict[EdgeKey, int] = {}
        exact = True
        for edge in graph.edges:
            ticks = time_model.travel_ticks(edge.length_m, speed_mps)
            self._edge_ticks[edge.key] = ticks
            unrounded = edge.length_m / speed_mps / time_model.tick_s
            exact = exact and _is_whole(unrounded) and ticks == round(unrounded)
        self._is_exact = exact

    @property
    def graph(self) -> WarehouseGraph:
        return self._graph

    @property
    def speed_mps(self) -> float:
        return self._speed_mps

    @property
    def time_model(self) -> TimeModel:
        return self._time_model

    @property
    def is_exact(self) -> bool:
        """True if no edge needed rounding, so every edge's ticks are proportional to its length.

        Then the shortest route in metres is also the fastest in ticks. Otherwise the two can
        differ slightly, because rounding adds more to routes with many short edges (D12).
        """
        return self._is_exact

    def edge_ticks(self, source: NodeId, target: NodeId) -> int:
        """Travel time of the edge ``source -> target``; `LookupError` if there is no such edge."""
        try:
            return self._edge_ticks[(source, target)]
        except KeyError:
            raise LookupError(f"no edge {source!r}->{target!r} in the graph") from None

    def arrival_offsets(self, route: Route) -> tuple[int, ...]:
        """Ticks from leaving ``route.source`` until reaching each node of the route.

        The first offset is 0 and the last is the route's duration. Raises
        `InvalidRouteError` if the route does not follow edges of the graph.
        """
        if route.source not in self._graph:
            raise InvalidRouteError(f"route uses unknown node {route.source!r}")
        offsets = [0]
        for source, target in itertools.pairwise(route.nodes):
            ticks = self._edge_ticks.get((source, target))
            if ticks is None:
                raise InvalidRouteError(f"route uses non-existent edge {source!r}->{target!r}")
            offsets.append(offsets[-1] + ticks)
        return tuple(offsets)

    def route_ticks(self, route: Route) -> int:
        """Duration of ``route`` in ticks: the sum of its edges' travel times."""
        return self.arrival_offsets(route)[-1]
