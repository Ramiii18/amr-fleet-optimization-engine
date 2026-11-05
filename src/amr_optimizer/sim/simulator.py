"""Discrete-event simulation of a fleet executing transport orders (D15, D24).

The simulator owns time and state. Time jumps from one event to the next, and every event
time is an integer tick (D6). The events:

- an order is **released** and joins the pending orders;
- a robot's current phase **ends** (arrival, loading done, unloading done), and the robot
  moves to its next phase through the state machine (D13);
- a **decision round** or a **wake-up** makes the loop stop at a tick where the policy may
  have something new to decide.

After all events of a tick are handled, the dispatch policy is asked for assignments if a
robot is idle and an order is pending. If executing those assignments creates more events
at the same tick (a robot already at the pickup with zero loading time), they are handled
and the policy is asked again before time moves on.

With ``decision_interval_ticks``, the policy is asked only at multiples of that interval
(decision rounds, D16).

**Without traffic** (the default), robots drive shortest routes in free flow and never
interact: they pass through each other. **With traffic** (``traffic=True``, M4), every job
is planned as one timed itinerary against the reservations of all other robots (D23, D24):
robots wait where needed, never share a node or corridor, and after each delivery drive to a
free holding spot. Orders whose pickup is reserved by another robot are hidden from the
policy until it is free, and an idle robot standing on an assigned order's pickup is first
moved out of the way. Both modes record every robot's trajectory, so conflicts can be counted.

Events at the same tick are handled in the order they were scheduled. Together with
scenario-ordered robots and release-ordered orders, this makes every run deterministic.

After every tick the simulator checks its invariants and raises
`SimulationInvariantError` if one is broken. That would be a bug in the simulator.
"""

import heapq
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from amr_optimizer.domain import (
    OrderId,
    RobotId,
    RobotPhase,
    RobotState,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.routing import Route, validate_route
from amr_optimizer.scenario import Scenario
from amr_optimizer.sim.log import EventKind, LogEntry
from amr_optimizer.sim.planner import TravelPlanner
from amr_optimizer.sim.policy import (
    Assignment,
    DispatchPolicy,
    DispatchSnapshot,
    InvalidAssignmentError,
)
from amr_optimizer.sim.traffic_control import PlannedJob, PlannedMove, TrafficControl
from amr_optimizer.traffic import FOREVER, TimedPath, Visit
from amr_optimizer.warehouse import NodeId


class SimulationInvariantError(RuntimeError):
    """The simulation reached a state that its invariants rule out: a simulator bug."""


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Everything a run produced. Undelivered orders are listed, never dropped.

    Without ``max_tick``, a run ends when no events remain, so ``in_progress`` and
    ``unreleased`` are empty; ``pending`` lists orders the policy never assigned.
    ``trajectories`` holds every robot's movement over the run, as one timed path each.
    """

    scenario: Scenario
    policy_name: str
    end_tick: int
    log: tuple[LogEntry, ...]
    robots: tuple[RobotState, ...]  # final states, in scenario order
    delivered: tuple[OrderId, ...]  # in delivery order
    in_progress: tuple[OrderId, ...]  # assigned, not yet delivered
    pending: tuple[OrderId, ...]  # released, never assigned
    unreleased: tuple[OrderId, ...]  # released after the run stopped
    trajectories: Mapping[RobotId, TimedPath] = field(default_factory=dict)
    traffic: bool = False
    moves_out_of_the_way: int = 0  # idle robots moved off an assigned pickup (traffic)
    deferred_assignments: int = 0  # assignments postponed to break a cycle (traffic)

    @property
    def all_delivered(self) -> bool:
        return len(self.delivered) == len(self.scenario.orders)


def simulate(
    scenario: Scenario,
    policy: DispatchPolicy,
    *,
    max_tick: int | None = None,
    decision_interval_ticks: int | None = None,
    traffic: bool = False,
) -> SimulationResult:
    """Run ``scenario`` under ``policy`` until no events remain, or past ``max_tick``.

    With ``max_tick``, events after that tick are not handled, and the result reports the
    orders still in progress, pending or unreleased at that point. With
    ``decision_interval_ticks``, the policy decides only at multiples of that interval;
    without it, the policy decides as soon as a robot is idle and an order is pending. With
    ``traffic``, robots plan around each other (see the module docstring).
    """
    if max_tick is not None and max_tick < 0:
        raise ValueError(f"max_tick must be non-negative, got {max_tick}")
    if decision_interval_ticks is not None and decision_interval_ticks < 1:
        raise ValueError(
            f"decision_interval_ticks must be at least 1, got {decision_interval_ticks}"
        )
    return _Simulation(scenario, policy, max_tick, decision_interval_ticks, traffic).run()


@dataclass(frozen=True, slots=True)
class _Release:
    order: TransportOrder


@dataclass(frozen=True, slots=True)
class _PhaseEnd:
    robot_id: RobotId


@dataclass(frozen=True, slots=True)
class _DecisionRound:
    """Makes the loop stop at a round tick, so the policy is asked there."""


@dataclass(frozen=True, slots=True)
class _Wake:
    """Makes the loop stop when a reserved pickup becomes free again (traffic only)."""


_Event = _Release | _PhaseEnd | _DecisionRound | _Wake


class _Simulation:
    """The mutable state of one run. Used only through `simulate`."""

    def __init__(
        self,
        scenario: Scenario,
        policy: DispatchPolicy,
        max_tick: int | None,
        decision_interval_ticks: int | None = None,
        traffic: bool = False,
    ) -> None:
        self.scenario = scenario
        self.policy = policy
        self.max_tick = max_tick
        self.interval = decision_interval_ticks
        self._next_round: int | None = None
        self.travel_times = TravelTimes(
            scenario.graph, speed_mps=scenario.speed_mps, time_model=scenario.time_model
        )
        self.planner = TravelPlanner(self.travel_times)
        self.traffic = TrafficControl(scenario, self.travel_times) if traffic else None
        self._plans: dict[RobotId, PlannedJob | PlannedMove] = {}
        self._last_prune = 0
        self.moves = self.deferred = 0
        self.robots: dict[RobotId, RobotState] = {r.robot_id: r for r in scenario.robots}
        self._tracks: dict[RobotId, list[Visit]] = {
            r.robot_id: [Visit(r.node, 0, FOREVER)] for r in scenario.robots
        }
        self.pending: dict[OrderId, TransportOrder] = {}
        self.released = 0
        self.delivered: list[OrderId] = []
        self.log: list[LogEntry] = []
        self.now = 0
        self._queue: list[tuple[int, int, _Event]] = []
        self._counter = itertools.count()
        self._scheduled: dict[RobotId, int] = {}  # each busy robot's one pending event
        for order in scenario.orders:
            self._push(order.release_tick, _Release(order))

    # --- main loop ---------------------------------------------------------------------------

    def run(self) -> SimulationResult:
        while self._queue:
            tick = self._queue[0][0]
            if self.max_tick is not None and tick > self.max_tick:
                break
            if tick < self.now:
                raise SimulationInvariantError(f"time went backwards: {tick} < {self.now}")
            self.now = tick
            while self._queue and self._queue[0][0] == tick:
                while self._queue and self._queue[0][0] == tick:
                    _, _, event = heapq.heappop(self._queue)
                    self._handle(event)
                self._dispatch()
            self._check_invariants()
        return self._result()

    def _push(self, tick: int, event: _Event) -> None:
        heapq.heappush(self._queue, (tick, next(self._counter), event))

    def _handle(self, event: _Event) -> None:
        if isinstance(event, _Release):
            self.pending[event.order.id] = event.order
            self.released += 1
            self._log(EventKind.RELEASED, event.order, event.order.pickup)
        elif isinstance(event, _PhaseEnd):
            self._end_phase(event.robot_id)
        elif isinstance(event, _DecisionRound):
            self._next_round = None  # the round is now; the policy is asked after the events

    # --- robots ------------------------------------------------------------------------------

    def _end_phase(self, robot_id: RobotId) -> None:
        robot = self.robots[robot_id]
        if self._scheduled.pop(robot_id, None) != self.now or robot.is_idle:
            raise SimulationInvariantError(f"robot {robot_id!r}: unexpected phase-end event")
        now, order, plan = self.now, robot.order, self._plans.get(robot_id)
        if robot.phase is RobotPhase.REPOSITIONING:
            new = robot.finish_repositioning(tick=now)
            self._plans.pop(robot_id)
            if self.traffic is not None:
                self.traffic.became_idle(robot_id)
            self._set(new)
            return
        if order is None:  # unreachable: only a repositioning robot has no order
            raise SimulationInvariantError(f"robot {robot_id!r}: busy without an order")
        match robot.phase:
            case RobotPhase.TO_PICKUP:
                new = robot.arrive(tick=now, until_tick=now + self.scenario.load_ticks)
                self._log(EventKind.PICKUP_REACHED, order, new.node, robot_id)
            case RobotPhase.LOADING:
                if isinstance(plan, PlannedJob):
                    route, until = plan.to_dropoff, plan.dropoff_tick
                else:
                    route, ticks = self._route(robot.node, order.dropoff)
                    until = now + ticks
                    self._track_route(robot_id, route, now)
                new = robot.finish_loading(route, tick=now, until_tick=until)
                self._log(EventKind.LOADED, order, robot.node, robot_id, route.length_m)
            case RobotPhase.TO_DROPOFF:
                new = robot.arrive(tick=now, until_tick=now + self.scenario.unload_ticks)
                self._log(EventKind.DROPOFF_REACHED, order, new.node, robot_id)
            case RobotPhase.UNLOADING:
                self.delivered.append(order.id)
                self._log(EventKind.DELIVERED, order, robot.node, robot_id)
                if isinstance(plan, PlannedJob):
                    new = robot.reposition(plan.to_holding, tick=now, until_tick=plan.holding_tick)
                else:
                    new = robot.finish_unloading(tick=now)
            case _:  # IDLE and REPOSITIONING are handled above
                raise SimulationInvariantError(f"robot {robot_id!r}: unexpected {robot.phase}")
        self._set(new)

    def _set(self, robot: RobotState) -> None:
        self.robots[robot.robot_id] = robot
        if robot.until_tick is not None:
            self._scheduled[robot.robot_id] = robot.until_tick
            self._push(robot.until_tick, _PhaseEnd(robot.robot_id))

    def _route(self, source: NodeId, target: NodeId) -> tuple[Route, int]:
        route, ticks = self.planner.route(source, target), self.planner.ticks(source, target)
        if route is None or ticks is None:
            raise SimulationInvariantError(f"no route {source!r}->{target!r}")
        validate_route(self.scenario.graph, route)
        return route, ticks

    # --- dispatch ----------------------------------------------------------------------------

    def _dispatch(self) -> None:
        if not self.pending or not any(robot.is_idle for robot in self.robots.values()):
            return
        if self.interval is not None and self.now % self.interval != 0:
            if self._next_round is None:
                self._next_round = (self.now // self.interval + 1) * self.interval
                self._push(self._next_round, _DecisionRound())
            return
        orders = tuple(self.pending.values())
        ready: Mapping[RobotId, tuple[int, NodeId]] = MappingProxyType({})
        if self.traffic is not None:
            self._prune()
            orders = self.traffic.visible_orders(orders, self.robots, self.now)
            if not orders:
                return
            ready = MappingProxyType(dict(self.traffic.ready))
        snapshot = DispatchSnapshot(
            self.now,
            tuple(self.robots.values()),
            orders,
            self.planner,
            load_ticks=self.scenario.load_ticks,
            unload_ticks=self.scenario.unload_ticks,
            planned_ready=ready,
        )
        assignments = self.policy.assign(snapshot)
        self._check_assignments(assignments, {order.id for order in orders})
        if self.traffic is None:
            for assignment in assignments:
                self._start_job(assignment)
        else:
            self._execute_with_traffic(assignments)

    def _check_assignments(self, assignments: Sequence[Assignment], offered: set[OrderId]) -> None:
        """Validate the whole decision before executing any part of it."""
        robots_seen: set[RobotId] = set()
        orders_seen: set[OrderId] = set()
        for a in assignments:
            robot = self.robots.get(a.robot_id)
            order = self.pending.get(a.order_id)
            if robot is None or not robot.is_idle or a.robot_id in robots_seen:
                raise InvalidAssignmentError(f"robot {a.robot_id!r} is not an idle robot")
            if order is None or a.order_id in orders_seen or a.order_id not in offered:
                raise InvalidAssignmentError(
                    f"order {a.order_id!r} is not a pending order that can be assigned now"
                )
            if self.planner.route(robot.node, order.pickup) is None:
                raise InvalidAssignmentError(
                    f"robot {a.robot_id!r} cannot reach the pickup of {a.order_id!r}"
                )
            if self.planner.route(order.pickup, order.dropoff) is None:
                raise InvalidAssignmentError(f"order {a.order_id!r}: drop-off is unreachable")
            robots_seen.add(a.robot_id)
            orders_seen.add(a.order_id)

    def _execute_with_traffic(self, assignments: Sequence[Assignment]) -> None:
        """Plan the decision's jobs one by one, each against all earlier reservations.

        A robot's job is planned after the robot standing on its pickup has left: if that
        robot is assigned too, its own job is planned first; otherwise it is moved out of the
        way. Two robots standing on each other's pickups form a cycle. The cycle is broken by
        postponing one assignment: that robot is moved out of the way, and its order stays
        pending for a later decision.
        """
        assert self.traffic is not None
        traffic = self.traffic
        assigned = {a.robot_id: a for a in assignments}
        planned: set[RobotId] = set()
        postponed: set[RobotId] = set()
        visiting: set[RobotId] = set()
        pickups = frozenset(order.pickup for order in self.pending.values())

        def execute(assignment: Assignment) -> None:
            robot_id = assignment.robot_id
            if robot_id in planned or robot_id in postponed:
                return
            visiting.add(robot_id)
            pickup = self.pending[assignment.order_id].pickup
            other = traffic.idle_holder(pickup, self.robots)
            if other is not None and other != robot_id:
                if other in assigned and other not in planned and other not in postponed:
                    if other in visiting:
                        postponed.add(other)  # a cycle: the other robot gives way
                    else:
                        execute(assigned[other])
                if self.robots[other].is_idle:
                    self._move_out_of_the_way(other, pickups)
            visiting.discard(robot_id)
            if robot_id not in postponed:
                self._start_job(assignment, pickups)
                planned.add(robot_id)

        for assignment in assignments:
            execute(assignment)
        self.deferred += len(postponed)

    def _start_job(self, assignment: Assignment, pickups: frozenset[NodeId] = frozenset()) -> None:
        robot = self.robots[assignment.robot_id]
        order = self.pending.pop(assignment.order_id)
        if self.traffic is None:
            route, ticks = self._route(robot.node, order.pickup)
            if len(route.nodes) == 1:  # already at the pickup: start loading now
                ticks = self.scenario.load_ticks
            else:
                self._track_route(robot.robot_id, route, self.now)
            until = self.now + ticks
        else:
            job = self.traffic.plan_job(robot, order, self.now, pickups)
            self._plans[robot.robot_id] = job
            self._track_path(robot.robot_id, job.path)
            self._wake_at(job.releases)
            route = job.to_pickup
            until = job.pickup_tick if len(route.nodes) > 1 else self.now + self.scenario.load_ticks
        new = robot.assign(order, route, tick=self.now, until_tick=until)
        self._log(EventKind.ASSIGNED, order, robot.node, robot.robot_id, route.length_m)
        if new.phase is RobotPhase.LOADING:
            self._log(EventKind.PICKUP_REACHED, order, new.node, robot.robot_id)
        self._set(new)

    def _move_out_of_the_way(self, robot_id: RobotId, pickups: frozenset[NodeId]) -> None:
        assert self.traffic is not None
        robot = self.robots[robot_id]
        move = self.traffic.plan_move(robot, self.now, pickups)
        self._plans[robot_id] = move
        self._track_path(robot_id, move.path)
        self._wake_at(move.releases)
        self.moves += 1
        self._set(robot.reposition(move.route, tick=self.now, until_tick=move.holding_tick))

    def _wake_at(self, ticks: Sequence[int]) -> None:
        for tick in ticks:
            if tick > self.now:
                self._push(tick, _Wake())

    def _prune(self) -> None:
        """Forget reservations that have ended, every simulated minute at most."""
        assert self.traffic is not None
        if self.now - self._last_prune >= 600:
            self.traffic.table.prune(self.now)
            self._last_prune = self.now

    # --- trajectories --------------------------------------------------------------------------

    def _track_route(self, robot_id: RobotId, route: Route, depart: int) -> None:
        """Record a free-flow drive along ``route``, leaving its source at ``depart``."""
        offsets = self.travel_times.arrival_offsets(route)
        visits = [
            Visit(node, depart + o, depart + o)
            for node, o in zip(route.nodes, offsets, strict=True)
        ]
        visits[-1] = Visit(visits[-1].node, visits[-1].arrive, FOREVER)
        self._track_path(robot_id, TimedPath(tuple(visits)))

    def _track_path(self, robot_id: RobotId, path: TimedPath) -> None:
        """Continue ``robot_id``'s trajectory with ``path``, which starts where it stands."""
        track = self._tracks[robot_id]
        current, first = track[-1], path.visits[0]
        if current.node != first.node or current.depart != FOREVER:
            raise SimulationInvariantError(f"robot {robot_id!r}: path does not start where it is")
        track[-1] = Visit(current.node, current.arrive, first.depart)
        track.extend(path.visits[1:])

    def _trajectories(self) -> dict[RobotId, TimedPath]:
        result = {}
        for robot_id, track in self._tracks.items():
            last = track[-1]
            end = Visit(last.node, last.arrive, max(last.arrive, self.now))
            result[robot_id] = TimedPath((*track[:-1], end))
        return result

    # --- bookkeeping ---------------------------------------------------------------------------

    def _log(
        self,
        kind: EventKind,
        order: TransportOrder,
        node: NodeId,
        robot_id: RobotId | None = None,
        distance_m: float = 0.0,
    ) -> None:
        self.log.append(LogEntry(self.now, kind, order.id, node, robot_id, distance_m))

    def _check_invariants(self) -> None:
        carried = [robot.order.id for robot in self.robots.values() if robot.order is not None]
        if len(set(carried)) != len(carried):
            raise SimulationInvariantError(f"an order is carried by two robots: {carried}")
        if len(self.pending) + len(carried) + len(self.delivered) != self.released:
            raise SimulationInvariantError("released orders are lost or counted twice")
        for robot in self.robots.values():
            if robot.until_tick is None:
                if robot.robot_id in self._scheduled:
                    raise SimulationInvariantError(f"idle robot {robot.robot_id!r} has an event")
                if self.traffic is not None and self.traffic.table.held_node(robot.robot_id) != (
                    robot.node
                ):
                    raise SimulationInvariantError(
                        f"idle robot {robot.robot_id!r} does not hold its node"
                    )
                continue
            if self._scheduled.get(robot.robot_id) != robot.until_tick:
                raise SimulationInvariantError(
                    f"robot {robot.robot_id!r}: no event scheduled at the end of its phase"
                )
            if robot.until_tick - robot.since_tick != self._duration(robot):
                raise SimulationInvariantError(
                    f"robot {robot.robot_id!r}: {robot.phase.name} has the wrong duration"
                )

    def _duration(self, robot: RobotState) -> int:
        """How long the robot's current phase must last."""
        if robot.route is not None:
            free_flow = self.travel_times.route_ticks(robot.route)
            plan = self._plans.get(robot.robot_id)
            if plan is None:
                return free_flow
            planned = _planned_end(plan, robot.phase) - robot.since_tick
            if planned < free_flow:
                raise SimulationInvariantError(
                    f"robot {robot.robot_id!r}: planned faster than free flow"
                )
            return planned
        if robot.phase is RobotPhase.LOADING:
            return self.scenario.load_ticks
        return self.scenario.unload_ticks

    def _result(self) -> SimulationResult:
        carried = tuple(r.order.id for r in self.robots.values() if r.order is not None)
        accounted = set(self.delivered) | set(carried) | set(self.pending)
        return SimulationResult(
            scenario=self.scenario,
            policy_name=self.policy.name,
            end_tick=self.now,
            log=tuple(self.log),
            robots=tuple(self.robots.values()),
            delivered=tuple(self.delivered),
            in_progress=carried,
            pending=tuple(self.pending),
            unreleased=tuple(o.id for o in self.scenario.orders if o.id not in accounted),
            trajectories=MappingProxyType(self._trajectories()),
            traffic=self.traffic is not None,
            moves_out_of_the_way=self.moves,
            deferred_assignments=self.deferred,
        )


def _planned_end(plan: PlannedJob | PlannedMove, phase: RobotPhase) -> int:
    if isinstance(plan, PlannedMove):
        return plan.holding_tick
    return {
        RobotPhase.TO_PICKUP: plan.pickup_tick,
        RobotPhase.TO_DROPOFF: plan.dropoff_tick,
        RobotPhase.REPOSITIONING: plan.holding_tick,
    }[phase]
