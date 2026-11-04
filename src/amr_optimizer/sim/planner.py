"""Cached routes and travel times, shared by the simulator and the dispatch policies."""

from amr_optimizer.domain import TravelTimes
from amr_optimizer.routing import Route, astar, euclidean
from amr_optimizer.warehouse import NodeId


class TravelPlanner:
    """Shortest routes (by length, D12) and their travel ticks, computed once per node pair.

    The map is static and nothing is blocked in M2, so a route stays valid once computed.
    Dispatch asks about the same robot-to-pickup pairs again and again, and the cache turns
    those repeats into dictionary lookups. Routes come from A* with the Euclidean heuristic,
    which is consistent on every valid map (D5, D10). An unreachable target gives ``None``
    (D4).

    Memory grows with the number of distinct pairs asked about. In practice these are pairs
    of endpoints (storage, stations, chargers, parking): at most 221 x 221 = 48,841 on the
    large preset.
    """

    __slots__ = ("_cache", "_travel_times")

    def __init__(self, travel_times: TravelTimes) -> None:
        self._travel_times = travel_times
        self._cache: dict[tuple[NodeId, NodeId], tuple[Route, int] | None] = {}

    @property
    def travel_times(self) -> TravelTimes:
        return self._travel_times

    def route(self, source: NodeId, target: NodeId) -> Route | None:
        """Shortest route from ``source`` to ``target``, or ``None`` if there is none."""
        entry = self._lookup(source, target)
        return None if entry is None else entry[0]

    def ticks(self, source: NodeId, target: NodeId) -> int | None:
        """Travel time of that route in ticks, or ``None`` if there is no route."""
        entry = self._lookup(source, target)
        return None if entry is None else entry[1]

    def _lookup(self, source: NodeId, target: NodeId) -> tuple[Route, int] | None:
        key = (source, target)
        if key not in self._cache:
            route = astar(self._travel_times.graph, source, target, euclidean).route
            self._cache[key] = (
                None if route is None else (route, self._travel_times.route_ticks(route))
            )
        return self._cache[key]
