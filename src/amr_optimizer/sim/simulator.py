"""Discrete-event simulation of a fleet executing transport orders (D15).

The simulator owns time and state. Time jumps from one event to the next, and every event
time is an integer tick (D6). There are two kinds of event:

- an order is **released** and joins the pending orders;
- a robot's current phase **ends** (arrival, loading done, unloading done), and the robot
  moves to its next phase through the state machine (D13).

After all events of a tick are handled, the dispatch policy is asked for assignments if a
robot is idle and an order is pending. If executing those assignments creates more events
at the same tick (a robot already at the pickup with zero loading time), they are handled
and the policy is asked again before time moves on.

Events at the same tick are handled in the order they were scheduled. Together with
scenario-ordered robots and release-ordered orders, this makes every run deterministic.

Robots do not interact in M2: no collisions, no blocking, no waiting for each other. That
limitation is deliberate and documented; traffic arrives in M4.

After every tick the simulator checks its invariants and raises
`SimulationInvariantError` if one is broken. That would be a bug in the simulator.
"""

import heapq
import itertools
from collections.abc import Sequence
from dataclasses import dataclass

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
from amr_optimizer.warehouse import NodeId


class SimulationInvariantError(RuntimeError):
    """The simulation reached a state that its invariants rule out: a simulator bug."""


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Everything a run produced. Undelivered orders are listed, never dropped.

    Without ``max_tick``, a run ends when no events remain, so ``in_progress`` and
    ``unreleased`` are empty; ``pending`` lists orders the policy never assigned.
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

    @property
    def all_delivered(self) -> bool:
        return len(self.delivered) == len(self.scenario.orders)


def simulate(
    scenario: Scenario, policy: DispatchPolicy, *, max_tick: int | None = None
) -> SimulationResult:
    """Run ``scenario`` under ``policy`` until no events remain, or past ``max_tick``.

    With ``max_tick``, events after that tick are not handled, and the result reports the
    orders still in progress, pending or unreleased at that point.
    """
    if max_tick is not None and max_tick < 0:
        raise ValueError(f"max_tick must be non-negative, got {max_tick}")
    return _Simulation(scenario, policy, max_tick).run()


@dataclass(frozen=True, slots=True)
class _Release:
    order: TransportOrder


@dataclass(frozen=True, slots=True)
class _PhaseEnd:
    robot_id: RobotId


class _Simulation:
    """The mutable state of one run. Used only through `simulate`."""

    def __init__(self, scenario: Scenario, policy: DispatchPolicy, max_tick: int | None) -> None:
        self.scenario = scenario
        self.policy = policy
        self.max_tick = max_tick
        self.travel_times = TravelTimes(
            scenario.graph, speed_mps=scenario.speed_mps, time_model=scenario.time_model
        )
        self.planner = TravelPlanner(self.travel_times)
        self.robots: dict[RobotId, RobotState] = {r.robot_id: r for r in scenario.robots}
        self.pending: dict[OrderId, TransportOrder] = {}
        self.released = 0
        self.delivered: list[OrderId] = []
        self.log: list[LogEntry] = []
        self.now = 0
        self._queue: list[tuple[int, int, _Release | _PhaseEnd]] = []
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

    def _push(self, tick: int, event: _Release | _PhaseEnd) -> None:
        heapq.heappush(self._queue, (tick, next(self._counter), event))

    def _handle(self, event: _Release | _PhaseEnd) -> None:
        if isinstance(event, _Release):
            self.pending[event.order.id] = event.order
            self.released += 1
            self._log(EventKind.RELEASED, event.order, event.order.pickup)
        else:
            self._end_phase(event.robot_id)

    # --- robots ------------------------------------------------------------------------------

    def _end_phase(self, robot_id: RobotId) -> None:
        robot = self.robots[robot_id]
        if self._scheduled.pop(robot_id, None) != self.now or robot.order is None:
            raise SimulationInvariantError(f"robot {robot_id!r}: unexpected phase-end event")
        order, now = robot.order, self.now
        match robot.phase:
            case RobotPhase.TO_PICKUP:
                new = robot.arrive(tick=now, until_tick=now + self.scenario.load_ticks)
                self._log(EventKind.PICKUP_REACHED, order, new.node, robot_id)
            case RobotPhase.LOADING:
                route, ticks = self._route(robot.node, order.dropoff)
                new = robot.finish_loading(route, tick=now, until_tick=now + ticks)
                self._log(EventKind.LOADED, order, robot.node, robot_id, route.length_m)
            case RobotPhase.TO_DROPOFF:
                new = robot.arrive(tick=now, until_tick=now + self.scenario.unload_ticks)
                self._log(EventKind.DROPOFF_REACHED, order, new.node, robot_id)
            case RobotPhase.UNLOADING:
                new = robot.finish_unloading(tick=now)
                self.delivered.append(order.id)
                self._log(EventKind.DELIVERED, order, robot.node, robot_id)
            case RobotPhase.IDLE:  # unreachable: the order check above excludes idle robots
                raise SimulationInvariantError(f"robot {robot_id!r}: idle robots have no events")
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
        snapshot = DispatchSnapshot(
            self.now, tuple(self.robots.values()), tuple(self.pending.values()), self.planner
        )
        assignments = self.policy.assign(snapshot)
        self._check_assignments(assignments)
        for assignment in assignments:
            self._start_job(assignment)

    def _check_assignments(self, assignments: Sequence[Assignment]) -> None:
        """Validate the whole decision before executing any part of it."""
        robots_seen: set[RobotId] = set()
        orders_seen: set[OrderId] = set()
        for a in assignments:
            robot = self.robots.get(a.robot_id)
            order = self.pending.get(a.order_id)
            if robot is None or not robot.is_idle or a.robot_id in robots_seen:
                raise InvalidAssignmentError(f"robot {a.robot_id!r} is not an idle robot")
            if order is None or a.order_id in orders_seen:
                raise InvalidAssignmentError(f"order {a.order_id!r} is not a pending order")
            if self.planner.route(robot.node, order.pickup) is None:
                raise InvalidAssignmentError(
                    f"robot {a.robot_id!r} cannot reach the pickup of {a.order_id!r}"
                )
            if self.planner.route(order.pickup, order.dropoff) is None:
                raise InvalidAssignmentError(f"order {a.order_id!r}: drop-off is unreachable")
            robots_seen.add(a.robot_id)
            orders_seen.add(a.order_id)

    def _start_job(self, assignment: Assignment) -> None:
        robot = self.robots[assignment.robot_id]
        order = self.pending.pop(assignment.order_id)
        route, ticks = self._route(robot.node, order.pickup)
        if len(route.nodes) == 1:  # already at the pickup: start loading now
            ticks = self.scenario.load_ticks
        new = robot.assign(order, route, tick=self.now, until_tick=self.now + ticks)
        self._log(EventKind.ASSIGNED, order, robot.node, robot.robot_id, route.length_m)
        if new.phase is RobotPhase.LOADING:
            self._log(EventKind.PICKUP_REACHED, order, new.node, robot.robot_id)
        self._set(new)

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
            return self.travel_times.route_ticks(robot.route)
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
        )
