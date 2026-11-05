"""Safe-interval path planning (SIPP) for whole itineraries (D24).

A robot's job is an itinerary: drive to a stop, stay there for its service time (loading,
unloading), drive to the next stop, and so on, ending with a hold at the last stop. The
planner finds the itinerary that reaches the last stop earliest, waiting wherever needed so
that it never overlaps a reservation of another robot.

**Safe intervals.** Searching over (node, tick) pairs would expand every tick of every wait:
an edge takes 10 to 40 ticks. SIPP (Phillips and Likhachev, ICRA 2011) groups the ticks of a
node into its safe intervals, the maximal stretches in which no other robot is there, and
searches over (stage, node, safe interval). Within one safe interval, arriving earlier is
never worse, because the robot can wait inside it. So it is enough to keep the earliest
arrival per state, and the search finds the earliest itinerary, like a search over every tick.

**One step.** From node u, reached at tick t within safe interval [s_u, e_u], to a safe
interval [s_v, e_v] of neighbour v over an edge of tau ticks: depart at the earliest d with
- d >= t and d >= s_v - tau (arrive no earlier than the interval opens);
- the corridor free during (d, d + tau);
- d <= e_u (the robot waits at u until it leaves) and d + tau <= e_v.

**Stops.** At the stop of the current stage, the robot may start its service: it stays for
the service time, inside the same safe interval, and continues in the next stage. The last
stop is a hold: the robot can only finish in a safe interval that never ends.

**Search.** A* with the free-flow travel time to the current stop, plus everything left after
it (later services and the free-flow trips between later stops), as the heuristic. Waiting can
only add time, so the heuristic never overestimates, and it is consistent, because it is a
shortest-path distance plus a constant per stage.
"""

import heapq
import itertools
from collections.abc import Sequence
from dataclasses import dataclass

from amr_optimizer.domain import TravelTimes
from amr_optimizer.traffic.paths import FOREVER, TimedPath, Visit
from amr_optimizer.traffic.reservations import ReservationTable
from amr_optimizer.warehouse import NodeId


@dataclass(frozen=True, slots=True)
class Stop:
    """A place to reach and how long to stay. ``service_ticks`` is ``None`` for the final hold."""

    node: NodeId
    service_ticks: int | None = None

    def __post_init__(self) -> None:
        if self.service_ticks is not None and self.service_ticks < 0:
            raise ValueError(f"stop {self.node!r}: service time {self.service_ticks} < 0")


@dataclass(frozen=True, slots=True)
class Itinerary:
    """A planned itinerary: its timed path, the tick each stop is reached and its service
    begins, and how many search states were expanded to find it."""

    path: TimedPath
    stop_ticks: tuple[int, ...]
    expansions: int


_State = tuple[int, NodeId, int]  # stage, node, start of the safe interval


class ItineraryPlanner:
    """Plans itineraries on one map. Free-flow distances to each goal are cached."""

    __slots__ = ("_distances", "_predecessors", "_travel_times")

    def __init__(self, travel_times: TravelTimes) -> None:
        self._travel_times = travel_times
        graph = travel_times.graph
        self._predecessors: dict[NodeId, list[tuple[NodeId, int]]] = {n.id: [] for n in graph.nodes}
        for node in graph.nodes:
            for edge in graph.out_edges(node.id):
                ticks = travel_times.edge_ticks(edge.source, edge.target)
                self._predecessors[edge.target].append((edge.source, ticks))
        self._distances: dict[NodeId, dict[NodeId, int]] = {}

    @property
    def travel_times(self) -> TravelTimes:
        return self._travel_times

    def distances_to(self, goal: NodeId) -> dict[NodeId, int]:
        """Free-flow travel ticks from every node that can reach ``goal`` (Dijkstra backwards)."""
        if goal not in self._distances:
            distances = {goal: 0}
            counter = itertools.count()
            heap = [(0, next(counter), goal)]
            while heap:
                ticks, _, node = heapq.heappop(heap)
                if ticks > distances[node]:
                    continue
                for before, edge_ticks in self._predecessors[node]:
                    candidate = ticks + edge_ticks
                    if candidate < distances.get(before, candidate + 1):
                        distances[before] = candidate
                        heapq.heappush(heap, (candidate, next(counter), before))
            self._distances[goal] = distances
        return self._distances[goal]

    def plan(
        self,
        table: ReservationTable,
        start: NodeId,
        start_tick: int,
        stops: Sequence[Stop],
    ) -> Itinerary | None:
        """The earliest itinerary from ``start`` at ``start_tick`` through ``stops``.

        Returns ``None`` if there is none (D4), for example because another robot holds the
        last stop. Raises `ValueError` if the stops are malformed, or if ``start`` is occupied
        at ``start_tick``: the caller must end the robot's own hold first.
        """
        if not stops or stops[-1].service_ticks is not None:
            raise ValueError("an itinerary needs stops, and the last one must be a hold")
        if any(stop.service_ticks is None for stop in stops[:-1]):
            raise ValueError("only the last stop can be a hold")
        first = table.safe_intervals(start, after=start_tick)
        if not first or first[0][0] != start_tick:
            raise ValueError(f"{start!r} is occupied at tick {start_tick}")

        heuristics = self._stage_heuristics(stops)
        if heuristics is None:
            return None  # a stop cannot be reached from the one before it
        last = len(stops) - 1
        intervals: dict[NodeId, list[tuple[int, int]]] = {start: first}

        def safe(node: NodeId) -> list[tuple[int, int]]:
            if node not in intervals:
                intervals[node] = table.safe_intervals(node, after=start_tick)
            return intervals[node]

        def h(stage: int, node: NodeId) -> int | None:
            distance, tail = heuristics[stage]
            ticks = distance.get(node)
            return None if ticks is None else ticks + tail

        counter = itertools.count()
        start_state: _State = (0, start, first[0][0])
        best: dict[_State, int] = {start_state: start_tick}
        ends: dict[_State, int] = {start_state: first[0][1]}
        parent: dict[_State, tuple[_State, int | None]] = {}
        h0 = h(0, start)
        if h0 is None:
            return None
        heap = [(start_tick + h0, next(counter), start_state)]
        closed: set[_State] = set()
        expansions = 0

        def push(state: _State, tick: int, end: int, came_from: tuple[_State, int | None]) -> None:
            estimate = h(state[0], state[1])
            if estimate is None or tick >= best.get(state, FOREVER):
                return
            best[state], ends[state], parent[state] = tick, end, came_from
            heapq.heappush(heap, (tick + estimate, next(counter), state))

        while heap:
            _, _, state = heapq.heappop(heap)
            if state in closed:
                continue
            closed.add(state)
            expansions += 1
            stage, node, _ = state
            tick, end = best[state], ends[state]
            stop = stops[stage]
            if stage == last and node == stop.node and end == FOREVER:
                return self._itinerary(state, parent, best, start, start_tick, expansions)
            if stage < last and node == stop.node:
                service = stop.service_ticks
                assert service is not None  # only the last stop is a hold
                if tick + service <= end:
                    push((stage + 1, node, state[2]), tick + service, end, (state, None))
            for target, ticks in self._successors(node):
                for interval_start, interval_end in safe(target):
                    if interval_end < tick + ticks:
                        continue
                    if interval_start > end + ticks:
                        break
                    depart = _earliest_departure(
                        table, node, target, max(tick, interval_start - ticks), ticks
                    )
                    if depart > end or depart + ticks > interval_end:
                        continue
                    push(
                        (stage, target, interval_start),
                        depart + ticks,
                        interval_end,
                        (state, depart),
                    )
        return None

    def _successors(self, node: NodeId) -> list[tuple[NodeId, int]]:
        graph = self._travel_times.graph
        return [
            (edge.target, self._travel_times.edge_ticks(edge.source, edge.target))
            for edge in graph.out_edges(node)
        ]

    def _stage_heuristics(
        self, stops: Sequence[Stop]
    ) -> list[tuple[dict[NodeId, int], int]] | None:
        """Per stage: free-flow distances to its stop, and the time left after reaching it."""
        tails: list[int] = [0] * len(stops)
        for stage in range(len(stops) - 2, -1, -1):
            service = stops[stage].service_ticks
            assert service is not None
            trip = self.distances_to(stops[stage + 1].node).get(stops[stage].node)
            if trip is None:
                return None
            tails[stage] = service + trip + tails[stage + 1]
        return [(self.distances_to(stop.node), tails[k]) for k, stop in enumerate(stops)]

    @staticmethod
    def _itinerary(
        goal: _State,
        parent: dict[_State, tuple[_State, int | None]],
        best: dict[_State, int],
        start: NodeId,
        start_tick: int,
        expansions: int,
    ) -> Itinerary:
        steps: list[tuple[_State, int | None]] = []  # (state reached, departure that led to it)
        state = goal
        while state in parent:
            previous, depart = parent[state]
            steps.append((state, depart))
            state = previous
        steps.reverse()

        visits: list[Visit] = []
        stop_ticks: list[int] = []
        node, arrive, previous_tick = start, start_tick, start_tick
        for reached, depart in steps:
            if depart is None:  # a service: it began when the previous state was reached
                stop_ticks.append(previous_tick)
            else:
                visits.append(Visit(node, arrive, depart))
                node, arrive = reached[1], best[reached]
            previous_tick = best[reached]
        visits.append(Visit(node, arrive, FOREVER))
        stop_ticks.append(best[goal])  # the final stage reached the last stop here
        return Itinerary(TimedPath(tuple(visits)), tuple(stop_ticks), expansions)


def _earliest_departure(
    table: ReservationTable, source: NodeId, target: NodeId, earliest: int, ticks: int
) -> int:
    """The earliest departure >= ``earliest`` with the corridor free while driving it.

    Reservations of one corridor are sorted and never overlap, so a single pass suffices:
    after a reservation pushes the departure to its end, no earlier one can block again.
    """
    depart = earliest
    for reservation in table.corridor_reservations(source, target):
        if depart < reservation.end and reservation.start < depart + ticks:
            depart = reservation.end
    return depart
