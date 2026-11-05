"""Traffic control for the simulator: every movement is planned around all others (D23, D24).

The simulator asks this controller three things:

- **Which pending orders can be assigned now** (`visible_orders`). An order whose pickup is
  reserved by a robot that is not idle there is hidden until that reservation ends. Among
  orders that share a pickup, only the oldest is offered, so one decision never sends two
  robots to the same pickup.
- **Plan a job** (`plan_job`). The whole itinerary is planned at once with SIPP: drive to the
  pickup and load, drive to the drop-off and unload, then drive to a free holding spot nearby
  and hold it. It is reserved against every robot planned before.
- **Move an idle robot out of the way** (`plan_move`), to a free holding spot, when another
  robot is assigned the order whose pickup it stands on.

Holding spots are endpoints (storage, chargers, parking), never stations, that nobody holds or
plans to visit. Endpoints are dead-end spurs on every generated map, so no plan ever has to pass
through one, and SIPP always finds a plan (D24). A failure is therefore a bug, and it raises.
"""

import heapq
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from amr_optimizer.domain import RobotId, RobotState, TransportOrder, TravelTimes
from amr_optimizer.routing import Route
from amr_optimizer.scenario import Scenario
from amr_optimizer.traffic import (
    FOREVER,
    Itinerary,
    ItineraryPlanner,
    ReservationTable,
    Stop,
    TimedPath,
    Visit,
)
from amr_optimizer.warehouse import NodeId, NodeKind


class TrafficPlanningError(RuntimeError):
    """No plan could be found, although the design rules that out (D24): a bug."""


@dataclass(frozen=True, slots=True)
class PlannedJob:
    """A job's whole itinerary, split into the three drives the robot's phases follow."""

    path: TimedPath
    to_pickup: Route
    to_dropoff: Route
    to_holding: Route
    pickup_tick: int  # reaches the pickup and starts loading
    dropoff_tick: int  # reaches the drop-off and starts unloading
    holding_tick: int  # reaches the holding spot and becomes idle
    releases: tuple[int, ...]  # ticks at which a node this robot held becomes free again


@dataclass(frozen=True, slots=True)
class PlannedMove:
    """An idle robot's drive to a holding spot, out of another robot's way."""

    path: TimedPath
    route: Route
    holding_tick: int
    releases: tuple[int, ...]


class TrafficControl:
    """The reservation table of one simulation run, and the plans made against it."""

    def __init__(self, scenario: Scenario, travel_times: TravelTimes) -> None:
        self.table = ReservationTable()
        self.planner = ItineraryPlanner(travel_times)
        self._graph = scenario.graph
        self._load_ticks, self._unload_ticks = scenario.load_ticks, scenario.unload_ticks
        excluded = (NodeKind.INTERSECTION, NodeKind.STATION)
        self._holding_spots = sorted(n.id for n in scenario.graph.nodes if n.kind not in excluded)
        self._distances: dict[NodeId, dict[NodeId, int]] = {}
        self.ready: dict[RobotId, tuple[int, NodeId]] = {}  # where and when busy robots go idle
        for robot in scenario.robots:
            self.table.reserve(robot.robot_id, TimedPath((Visit(robot.node, 0, FOREVER),)))

    # --- what the dispatch policy may see ------------------------------------------------------

    def visible_orders(
        self,
        pending: Sequence[TransportOrder],
        robots: Mapping[RobotId, RobotState],
        now: int,
    ) -> tuple[TransportOrder, ...]:
        """The pending orders that can be assigned at ``now``, oldest first."""
        offered, seen = [], set()
        for order in pending:
            if order.pickup in seen:
                continue
            seen.add(order.pickup)
            if self._pickup_available(order.pickup, robots, now):
                offered.append(order)
        return tuple(offered)

    def idle_holder(self, node: NodeId, robots: Mapping[RobotId, RobotState]) -> RobotId | None:
        """The idle robot holding ``node``, if there is one."""
        holder = self.table.holder(node)
        return holder if holder is not None and robots[holder].is_idle else None

    def _pickup_available(
        self, pickup: NodeId, robots: Mapping[RobotId, RobotState], now: int
    ) -> bool:
        """Free from now on, or held only by an idle robot that can be moved away."""
        return all(
            robots[r.robot].is_idle and self.table.held_node(r.robot) == pickup
            for r in self.table.node_reservations(pickup)
            if r.end >= now
        )

    # --- planning --------------------------------------------------------------------------------

    def plan_job(
        self, robot: RobotState, order: TransportOrder, now: int, avoid: frozenset[NodeId]
    ) -> PlannedJob:
        """Plan and reserve ``robot``'s whole job for ``order``, starting at ``now``.

        ``avoid`` lists nodes not to choose as the holding spot, such as the pickups of other
        pending orders, so that the robot will not stand in a later robot's way.
        """
        start = self.table.end_hold(robot.robot_id, now)
        holding = self._holding_spot(order.dropoff, now, avoid | {order.pickup})
        stops = [
            Stop(order.pickup, self._load_ticks),
            Stop(order.dropoff, self._unload_ticks),
            Stop(holding),
        ]
        itinerary = self._plan(robot.robot_id, start, now, stops)
        to_pickup, to_dropoff, to_holding = self._legs(itinerary, stops)
        pickup_tick, dropoff_tick, holding_tick = itinerary.stop_ticks
        self.ready[robot.robot_id] = (holding_tick, holding)
        return PlannedJob(
            itinerary.path,
            to_pickup,
            to_dropoff,
            to_holding,
            pickup_tick,
            dropoff_tick,
            holding_tick,
            _releases(itinerary.path, (start, order.pickup)),
        )

    def plan_move(self, robot: RobotState, now: int, avoid: frozenset[NodeId]) -> PlannedMove:
        """Plan and reserve a drive for idle ``robot`` from its node to a free holding spot."""
        start = self.table.end_hold(robot.robot_id, now)
        holding = self._holding_spot(start, now, avoid | {start})
        itinerary = self._plan(robot.robot_id, start, now, [Stop(holding)])
        self.ready[robot.robot_id] = (itinerary.stop_ticks[0], holding)
        return PlannedMove(
            itinerary.path,
            self._route(itinerary.path.visits),
            itinerary.stop_ticks[0],
            _releases(itinerary.path, (start,)),
        )

    def became_idle(self, robot: RobotId) -> None:
        self.ready.pop(robot, None)

    def _plan(self, robot: RobotId, start: NodeId, now: int, stops: list[Stop]) -> Itinerary:
        itinerary = self.planner.plan(self.table, start, now, stops)
        if itinerary is None:
            raise TrafficPlanningError(
                f"robot {robot!r}: no itinerary from {start!r} at tick {now} through "
                f"{[s.node for s in stops]}"
            )
        self.table.reserve(robot, itinerary.path)
        return itinerary

    def _holding_spot(self, near: NodeId, now: int, avoid: frozenset[NodeId]) -> NodeId:
        """The free endpoint closest to ``near`` (free-flow ticks, then id), outside ``avoid``
        if possible. Free means that nobody holds it or plans to visit it from now on."""
        distances = self._distances_from(near)
        ranked = sorted(
            (distances[n], n) for n in self._holding_spots if n in distances and n != near
        )
        free = [n for _, n in ranked if self.table.is_free_from(n, now)]
        for node in free:
            if node not in avoid:
                return node
        if free:
            return free[0]
        raise TrafficPlanningError(f"no free holding spot reachable from {near!r} at tick {now}")

    def _distances_from(self, source: NodeId) -> dict[NodeId, int]:
        """Free-flow travel ticks from ``source`` to every node it can reach (Dijkstra)."""
        if source not in self._distances:
            times = self.planner.travel_times
            distances = {source: 0}
            counter = itertools.count()
            heap = [(0, next(counter), source)]
            while heap:
                ticks, _, node = heapq.heappop(heap)
                if ticks > distances[node]:
                    continue
                for edge in self._graph.out_edges(node):
                    candidate = ticks + times.edge_ticks(node, edge.target)
                    if candidate < distances.get(edge.target, candidate + 1):
                        distances[edge.target] = candidate
                        heapq.heappush(heap, (candidate, next(counter), edge.target))
            self._distances[source] = distances
        return self._distances[source]

    def _legs(self, itinerary: Itinerary, stops: Sequence[Stop]) -> tuple[Route, Route, Route]:
        """Split the itinerary's path at its stops into the routes of the robot's phases."""
        visits = itinerary.path.visits
        cuts, index = [0], 0
        for stop, tick in zip(stops, itinerary.stop_ticks, strict=True):
            while not (
                visits[index].node == stop.node
                and visits[index].arrive <= tick <= visits[index].depart
            ):
                index += 1
            cuts.append(index)
        return (
            self._route(visits[cuts[0] : cuts[1] + 1]),
            self._route(visits[cuts[1] : cuts[2] + 1]),
            self._route(visits[cuts[2] : cuts[3] + 1]),
        )

    def _route(self, visits: Sequence[Visit]) -> Route:
        nodes = tuple(visit.node for visit in visits)
        length = sum(self._edge_length(u, v) for u, v in itertools.pairwise(nodes))
        return Route(nodes, length)

    def _edge_length(self, source: NodeId, target: NodeId) -> float:
        for edge in self._graph.out_edges(source):
            if edge.target == target:
                return edge.length_m
        raise TrafficPlanningError(f"no edge {source!r} -> {target!r}")


def _releases(path: TimedPath, nodes: tuple[NodeId, ...]) -> tuple[int, ...]:
    """The ticks at which the robot leaves each of ``nodes`` for the last time on ``path``,
    plus one: from then on, another robot may be planned there."""
    ticks = []
    for node in nodes:
        departures = [v.depart for v in path.visits if v.node == node and v.depart < FOREVER]
        if departures:
            ticks.append(max(departures) + 1)
    return tuple(sorted(set(ticks)))
