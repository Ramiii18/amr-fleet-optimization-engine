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

**Execution with traffic** (Phase 2, D25). Robots move edge by edge: an event when a robot
leaves a node, and one when it reaches the end of a corridor. With ``delays``, a drive can take
longer than planned. With ``execution=ExecutionPolicy.ORDERED`` (the default), every robot then
still uses every node and corridor in the planned order of robots, waiting at the end of its
corridor if the node ahead is not free yet; without delays this reproduces the plans exactly.
``ExecutionPolicy.NAIVE`` lets every robot keep its own schedule, and conflicts are counted.
Only events that change a robot's phase can lead to a dispatch decision, so movement alone
never makes the policy decide at a tick it otherwise wouldn't.

**Batteries** (``battery=BatterySpec(...)``, needs traffic, D26). Every edge driven drains
energy, and a robot idle at a charger charges. At every decision, an idle robot below the low
threshold goes out of service: it is sent to the nearest free charger (an idle, charged robot
standing on it is moved away first), hidden from the policy, and back in service at the high
threshold. A battery below zero is an invariant violation.

Events at the same tick are handled in the order they were scheduled. Together with
scenario-ordered robots and release-ordered orders, this makes every run deterministic.

After every tick the simulator checks its invariants and raises
`SimulationInvariantError` if one is broken. That would be a bug in the simulator.
"""

import heapq
import itertools
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from amr_optimizer.domain import (
    BatterySpec,
    EnergyModel,
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
from amr_optimizer.traffic import (
    FOREVER,
    NO_DELAYS,
    Corridor,
    DelayModel,
    ExecutionPolicy,
    Resource,
    ResourceOrder,
    TimedPath,
    Visit,
)
from amr_optimizer.warehouse import NodeId, NodeKind


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
    decision_seconds: tuple[float, ...] = ()  # wall time of each decision; see below
    battery: bool = False
    charging_trips: int = 0  # drives to a charger by robots below the low threshold
    out_of_service_ticks: int = 0  # robot-ticks spent out of service, below the threshold
    charger_wait_ticks: int = 0  # robot-ticks out of service while no charger was free
    lowest_energy_share: float | None = None  # the lowest level any battery reached

    # ``decision_seconds`` measures, for every call of the policy, the wall time from building
    # the snapshot to the end of executing the decision (with traffic, including the planning
    # of every itinerary). It depends on the machine and its load, unlike everything else in
    # the result, and nothing in the simulation reads it.

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
    delays: DelayModel = NO_DELAYS,
    delay_seed: int = 0,
    execution: ExecutionPolicy = ExecutionPolicy.ORDERED,
    battery: BatterySpec | None = None,
    battery_seed: int = 0,
) -> SimulationResult:
    """Run ``scenario`` under ``policy`` until no events remain, or past ``max_tick``.

    With ``max_tick``, events after that tick are not handled, and the result reports the
    orders still in progress, pending or unreleased at that point. With
    ``decision_interval_ticks``, the policy decides only at multiples of that interval;
    without it, the policy decides as soon as a robot is idle and an order is pending. With
    ``traffic``, robots plan around each other (see the module docstring); ``delays``,
    ``delay_seed`` and ``execution`` then set how plans are executed (D25). Each robot draws
    its delays from its own stream, named after ``delay_seed`` and the robot. With ``battery``
    (and traffic), robots drain and charge batteries (D26); start levels come from a stream
    named after ``battery_seed``.
    """
    if max_tick is not None and max_tick < 0:
        raise ValueError(f"max_tick must be non-negative, got {max_tick}")
    if decision_interval_ticks is not None and decision_interval_ticks < 1:
        raise ValueError(
            f"decision_interval_ticks must be at least 1, got {decision_interval_ticks}"
        )
    if not traffic and (delays.probability > 0 or execution is not ExecutionPolicy.ORDERED):
        raise ValueError("delays and execution policies need traffic=True")
    if not traffic and battery is not None:
        raise ValueError("batteries need traffic=True: chargers are shared resources")
    return _Simulation(
        scenario,
        policy,
        max_tick,
        decision_interval_ticks,
        traffic,
        delays,
        delay_seed,
        execution,
        battery,
        battery_seed,
    ).run()


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
    """Makes the loop stop when a reserved pickup becomes free again (traffic only), or when a
    robot is charged enough to return to service (batteries)."""


@dataclass(frozen=True, slots=True)
class _Depart:
    """A robot tries to leave its node for the next one on its path (traffic only)."""

    robot_id: RobotId


@dataclass(frozen=True, slots=True)
class _Reach:
    """A robot reaches the end of its corridor and tries to enter the node (traffic only)."""

    robot_id: RobotId


@dataclass(slots=True)
class _Cursor:
    """Where a robot is on its current planned path, during execution."""

    path: TimedPath
    stops: tuple[int, ...]  # visits where the robot serves, or holds (the last)
    index: int = 0  # the visit it is at, or the one it left while in the corridor after it
    in_corridor: bool = False


_Event = _Release | _PhaseEnd | _DecisionRound | _Wake | _Depart | _Reach


class _Simulation:
    """The mutable state of one run. Used only through `simulate`."""

    def __init__(
        self,
        scenario: Scenario,
        policy: DispatchPolicy,
        max_tick: int | None,
        decision_interval_ticks: int | None = None,
        traffic: bool = False,
        delays: DelayModel = NO_DELAYS,
        delay_seed: int = 0,
        execution: ExecutionPolicy = ExecutionPolicy.ORDERED,
        battery: BatterySpec | None = None,
        battery_seed: int = 0,
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
        self.traffic = (
            TrafficControl(scenario, self.travel_times, reserve_chargers=battery is not None)
            if traffic
            else None
        )
        self._energy_model = (
            EnergyModel(battery, scenario.graph, scenario.time_model) if battery else None
        )
        self._energy: dict[RobotId, int] = {}  # settled levels (see `_level` for charging)
        self._charging_since: dict[RobotId, int] = {}  # idle at a charger since this tick
        self._low: dict[RobotId, int] = {}  # out of service since this tick
        self._waiting_since: dict[RobotId, int] = {}  # out of service, no charger free yet
        self._arrival_level: dict[RobotId, int] = {}  # level on reaching the charger it drives to
        self._charging_trips = self._out_of_service = self._charger_wait = self._lowest = 0
        if self._energy_model is not None:
            levels = self._energy_model.start_levels(
                len(scenario.robots), random.Random(f"{battery_seed}:battery")
            )
            self._energy = {r.robot_id: e for r, e in zip(scenario.robots, levels, strict=True)}
            self._lowest = min(levels)
            for robot in scenario.robots:
                if self._is_charger(robot.node):
                    self._charging_since[robot.robot_id] = 0
        self._plans: dict[RobotId, PlannedJob | PlannedMove] = {}
        self._cursors: dict[RobotId, _Cursor] = {}
        self._delays = delays
        self._delay_streams = {
            r.robot_id: random.Random(f"{delay_seed}:delays:{r.robot_id}") for r in scenario.robots
        }
        self._order = ResourceOrder() if traffic and execution is ExecutionPolicy.ORDERED else None
        self._waiting: dict[RobotId, Resource] = {}  # robots waiting for a resource to free up
        self._decide = False  # did something happen this tick that the policy should see?
        self._last_prune = 0
        self.moves = self.deferred = 0
        self._decision_seconds: list[float] = []
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
        if self._energy_model is not None:
            self._push(0, _Wake())  # robots that start low go to charge at once
        if self._order is not None:
            for robot in scenario.robots:
                start = TimedPath((Visit(robot.node, 0, FOREVER),))
                self._order.add(robot.robot_id, start, include_start=True)

    # --- main loop ---------------------------------------------------------------------------

    def run(self) -> SimulationResult:
        while self._queue:
            tick = self._queue[0][0]
            if self.max_tick is not None and tick > self.max_tick:
                break
            if isinstance(self._queue[0][2], _Wake) and self._finished():
                break
            if tick < self.now:
                raise SimulationInvariantError(f"time went backwards: {tick} < {self.now}")
            self.now = tick
            while self._queue and self._queue[0][0] == tick:
                while self._queue and self._queue[0][0] == tick:
                    _, _, event = heapq.heappop(self._queue)
                    self._handle(event)
                if self._decide:
                    self._decide = False
                    self._dispatch()
            self._check_invariants()
        return self._result()

    def _finished(self) -> bool:
        """No order is waiting and only wake-ups are left: every order is released, and no robot
        is working or moving (each would have an event of its own). The run ends at the last
        event that did something, not at a later wake-up such as a return to service."""
        return (
            not self.pending
            and not self._waiting
            and all(isinstance(event, _Wake) for _, _, event in self._queue)
        )

    def _push(self, tick: int, event: _Event) -> None:
        heapq.heappush(self._queue, (tick, next(self._counter), event))

    def _handle(self, event: _Event) -> None:
        if isinstance(event, _Depart):
            self._depart(event.robot_id)
            return
        if isinstance(event, _Reach):
            self._reach(event.robot_id)
            return
        self._decide = True
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
        if order is None:  # unreachable: travelling ends without an order happen on arrival
            raise SimulationInvariantError(f"robot {robot_id!r}: busy without an order")
        match robot.phase:
            case RobotPhase.TO_PICKUP:
                new = robot.arrive(tick=now, until_tick=now + self.scenario.load_ticks)
                self._log(EventKind.PICKUP_REACHED, order, new.node, robot_id)
            case RobotPhase.LOADING:
                if isinstance(plan, PlannedJob):
                    route, until = (
                        plan.to_dropoff,
                        self._estimate(plan.dropoff_tick, plan.to_dropoff),
                    )
                    self._depart_after_service(robot_id)
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
                    until = self._estimate(plan.holding_tick, plan.to_holding)
                    new = robot.reposition(plan.to_holding, tick=now, until_tick=until)
                    self._depart_after_service(robot_id)
                else:
                    new = robot.finish_unloading(tick=now)
            case _:  # IDLE has no end; REPOSITIONING ends on arrival
                raise SimulationInvariantError(f"robot {robot_id!r}: unexpected {robot.phase}")
        self._set(new)

    def _set(self, robot: RobotState) -> None:
        """Store the robot's new state, and schedule the end of its phase if the phase has a
        fixed length. With traffic, drives end when the robot arrives (`_reach`)."""
        self.robots[robot.robot_id] = robot
        if robot.until_tick is not None and not (
            self.traffic is not None and robot.phase.is_travelling
        ):
            self._scheduled[robot.robot_id] = robot.until_tick
            self._push(robot.until_tick, _PhaseEnd(robot.robot_id))

    def _estimate(self, planned: int, route: Route) -> int:
        """When a drive that starts now should end: as planned, unless the robot is already
        too late for that. A drive never ends before either bound, so arrivals can only
        postpone the estimate (`RobotState.postpone`)."""
        return max(planned, self.now + self.travel_times.route_ticks(route))

    # --- movement with traffic (D25) -------------------------------------------------------------

    def _depart(self, robot_id: RobotId) -> None:
        """Leave the current node, if every robot planned earlier on the corridor has left it."""
        cursor = self._cursors[robot_id]
        visits = cursor.path.visits
        if cursor.in_corridor or cursor.index + 1 >= len(visits):
            raise SimulationInvariantError(f"robot {robot_id!r}: nowhere to depart to")
        here, there = visits[cursor.index].node, visits[cursor.index + 1].node
        if self._order is not None and not self._order.may_enter_corridor(here, there, robot_id):
            self._waiting[robot_id] = Corridor.between(here, there)
            return
        if self._order is not None:
            self._let_next_in(here, self._order.leave(here, robot_id, self.now))
        if self._energy_model is not None and robot_id in self._charging_since:
            self._energy[robot_id] = self._level(robot_id)  # stop charging
            del self._charging_since[robot_id]
            self._decide = True  # a charger becomes free: robots waiting for one may take it
        track = self._tracks[robot_id]
        track[-1] = Visit(track[-1].node, track[-1].arrive, self.now)
        cursor.in_corridor = True
        extra = self._delays.draw(self._delay_streams[robot_id])
        self._push(self.now + self.travel_times.edge_ticks(here, there) + extra, _Reach(robot_id))

    def _reach(self, robot_id: RobotId) -> None:
        """Enter the node at the end of the corridor, if it is free in the planned order;
        otherwise wait at the end of the corridor."""
        cursor = self._cursors[robot_id]
        visits = cursor.path.visits
        here, there = visits[cursor.index].node, visits[cursor.index + 1].node
        if self._order is not None:
            earliest = self._order.earliest_arrival(there, robot_id)
            if earliest is None:  # a robot planned earlier still has to pass
                self._waiting[robot_id] = there
                return
            if earliest > self.now:  # the node was left this tick: one tick of clearance
                self._push(earliest, _Reach(robot_id))
                return
            corridor = Corridor.between(here, there)
            self._let_next_in(corridor, self._order.leave(corridor, robot_id, self.now))
        cursor.index += 1
        cursor.in_corridor = False
        self._tracks[robot_id].append(Visit(there, self.now, FOREVER))
        if self._energy_model is not None:
            self._energy[robot_id] -= self._energy_model.edge_cost(here, there)
            if self._energy[robot_id] < 0:
                raise SimulationInvariantError(f"robot {robot_id!r}: the battery ran empty")
            self._lowest = min(self._lowest, self._energy[robot_id])
        if cursor.index in cursor.stops:
            self._decide = True
            self._arrive_at_stop(robot_id)
        else:
            self._push(max(self.now, visits[cursor.index].depart), _Depart(robot_id))

    def _let_next_in(self, resource: Resource, robot_id: RobotId | None) -> None:
        """Wake the robot that is next in line for ``resource``, if it is waiting for it."""
        if robot_id is None or self._waiting.get(robot_id) != resource:
            return
        del self._waiting[robot_id]
        if isinstance(resource, Corridor):
            self._push(self.now, _Depart(robot_id))
        else:
            self._push(self.now + 1, _Reach(robot_id))

    def _arrive_at_stop(self, robot_id: RobotId) -> None:
        """A drive ends at a stop: start loading or unloading, or become idle at the end."""
        robot = self.robots[robot_id]
        if robot.until_tick is not None and robot.until_tick < self.now:
            robot = robot.postpone(self.now)  # delayed
        now, order = self.now, robot.order
        if robot.phase is RobotPhase.REPOSITIONING:
            new = robot.finish_repositioning(tick=now)
            self._plans.pop(robot_id)
            self._cursors.pop(robot_id)
            assert self.traffic is not None
            self.traffic.became_idle(robot_id)
            self._arrival_level.pop(robot_id, None)
            if self._energy_model is not None and self._is_charger(new.node):
                self._charging_since[robot_id] = now
                if robot_id in self._low:  # decide again when it will be charged enough
                    model = self._energy_model
                    ticks = model.ticks_to_reach(self._energy[robot_id], model.high)
                    self._push(now + ticks, _Wake())
        elif robot.phase is RobotPhase.TO_PICKUP and order is not None:
            new = robot.arrive(tick=now, until_tick=now + self.scenario.load_ticks)
            self._log(EventKind.PICKUP_REACHED, order, new.node, robot_id)
        elif robot.phase is RobotPhase.TO_DROPOFF and order is not None:
            new = robot.arrive(tick=now, until_tick=now + self.scenario.unload_ticks)
            self._log(EventKind.DROPOFF_REACHED, order, new.node, robot_id)
        else:
            raise SimulationInvariantError(f"robot {robot_id!r}: arrived while {robot.phase}")
        self._set(new)

    def _depart_after_service(self, robot_id: RobotId) -> None:
        """After loading or unloading, leave no earlier than planned."""
        cursor = self._cursors[robot_id]
        self._push(max(self.now, cursor.path.visits[cursor.index].depart), _Depart(robot_id))

    def _route(self, source: NodeId, target: NodeId) -> tuple[Route, int]:
        route, ticks = self.planner.route(source, target), self.planner.ticks(source, target)
        if route is None or ticks is None:
            raise SimulationInvariantError(f"no route {source!r}->{target!r}")
        validate_route(self.scenario.graph, route)
        return route, ticks

    # --- dispatch ----------------------------------------------------------------------------

    def _dispatch(self) -> None:
        if self._energy_model is None:
            self._assign_orders()
            return
        self._manage_charging()
        self._assign_orders()
        # Only now move idle robots off chargers that low robots need: the policy may have
        # sent them to work instead.
        self._send_to_chargers(evict=True)

    def _assign_orders(self) -> None:
        if not self.pending or not any(
            robot.is_idle and robot.robot_id not in self._low for robot in self.robots.values()
        ):
            return
        if self.interval is not None and self.now % self.interval != 0:
            if self._next_round is None:
                self._next_round = (self.now // self.interval + 1) * self.interval
                self._push(self._next_round, _DecisionRound())
            return
        started = time.perf_counter()
        orders = tuple(self.pending.values())
        ready: Mapping[RobotId, tuple[int, NodeId]] = MappingProxyType({})
        if self.traffic is not None:
            self._prune()
            orders = self.traffic.visible_orders(orders, self.robots, self.now)
            if not orders:
                return
            ready = MappingProxyType(dict(self.traffic.ready) | self._charging_ready())
        snapshot = DispatchSnapshot(
            self.now,
            tuple(self.robots.values()),
            orders,
            self.planner,
            load_ticks=self.scenario.load_ticks,
            unload_ticks=self.scenario.unload_ticks,
            planned_ready=ready,
            unavailable=frozenset(self._low),
        )
        assignments = self.policy.assign(snapshot)
        self._check_assignments(assignments, {order.id for order in orders})
        if self.traffic is None:
            for assignment in assignments:
                self._start_job(assignment)
        else:
            self._execute_with_traffic(assignments)
        self._decision_seconds.append(time.perf_counter() - started)

    def _check_assignments(self, assignments: Sequence[Assignment], offered: set[OrderId]) -> None:
        """Validate the whole decision before executing any part of it."""
        robots_seen: set[RobotId] = set()
        orders_seen: set[OrderId] = set()
        for a in assignments:
            robot = self.robots.get(a.robot_id)
            order = self.pending.get(a.order_id)
            if (
                robot is None
                or not robot.is_idle
                or a.robot_id in self._low
                or a.robot_id in robots_seen
            ):
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
            self._follow(robot.robot_id, job, job.path, job.stop_visits, job.releases)
            route = job.to_pickup
            until = job.pickup_tick if len(route.nodes) > 1 else self.now + self.scenario.load_ticks
            if len(route.nodes) > 1:
                self._push(max(self.now, job.path.visits[0].depart), _Depart(robot.robot_id))
        new = robot.assign(order, route, tick=self.now, until_tick=until)
        self._log(EventKind.ASSIGNED, order, robot.node, robot.robot_id, route.length_m)
        if new.phase is RobotPhase.LOADING:
            self._log(EventKind.PICKUP_REACHED, order, new.node, robot.robot_id)
        self._set(new)

    def _move_out_of_the_way(self, robot_id: RobotId, pickups: frozenset[NodeId]) -> None:
        assert self.traffic is not None
        robot = self.robots[robot_id]
        move = self.traffic.plan_move(robot, self.now, pickups)
        self._follow(robot_id, move, move.path, move.stop_visits, move.releases)
        self.moves += 1
        self._set(robot.reposition(move.route, tick=self.now, until_tick=move.holding_tick))
        self._push(max(self.now, move.path.visits[0].depart), _Depart(robot_id))

    def _follow(
        self,
        robot_id: RobotId,
        plan: PlannedJob | PlannedMove,
        path: TimedPath,
        stops: tuple[int, ...],
        releases: Sequence[int],
    ) -> None:
        """Start executing a new plan: queue its uses of nodes and corridors in the planned
        order (its first node is already the robot's), and follow it from its first visit."""
        self._plans[robot_id] = plan
        self._cursors[robot_id] = _Cursor(path, stops)
        if self._order is not None:
            self._order.add(robot_id, path, include_start=False)
        self._wake_at(releases)

    # --- batteries (D26) ----------------------------------------------------------------------

    def _is_charger(self, node: NodeId) -> bool:
        return self.scenario.graph.node(node).kind is NodeKind.CHARGER

    def _level(self, robot_id: RobotId) -> int:
        """The robot's battery level now, including any charging since it was last settled."""
        assert self._energy_model is not None
        since = self._charging_since.get(robot_id)
        energy = self._energy[robot_id]
        return energy if since is None else self._energy_model.charged(energy, self.now - since)

    def _manage_charging(self) -> None:
        """Take low robots out of service, bring charged ones back, and send low robots to free
        chargers."""
        assert self._energy_model is not None
        model, now = self._energy_model, self.now
        for robot in self.robots.values():
            robot_id = robot.robot_id
            if not robot.is_idle:
                continue
            if robot_id in self._low:
                if robot_id in self._charging_since and self._level(robot_id) >= model.high:
                    self._out_of_service += now - self._low.pop(robot_id)
            elif self._level(robot_id) < model.low:
                self._low[robot_id] = now
                if robot_id in self._charging_since:  # already on a charger (at the start)
                    ticks = model.ticks_to_reach(self._level(robot_id), model.high)
                    self._push(now + ticks, _Wake())
        self._send_to_chargers(evict=False)

    def _send_to_chargers(self, *, evict: bool) -> None:
        """Send each low robot that is neither charging nor driving to a charger to the nearest
        free one. With ``evict``, a charger held by an idle robot in service also counts as free:
        that robot is moved away first."""
        now = self.now
        pickups = frozenset(order.pickup for order in self.pending.values())
        for robot_id in list(self._low):
            robot = self.robots[robot_id]
            if not robot.is_idle or robot_id in self._charging_since:
                continue  # driving to a charger, or charging
            charger = self._free_charger(robot.node, pickups, evict=evict)
            if charger is None:
                self._waiting_since.setdefault(robot_id, now)
                continue
            if (waiting := self._waiting_since.pop(robot_id, None)) is not None:
                self._charger_wait += now - waiting
            self._send_to_charger(robot_id, charger, pickups)

    def _free_charger(
        self, node: NodeId, pickups: frozenset[NodeId], *, evict: bool
    ) -> NodeId | None:
        """The nearest charger that is free or held by an idle robot in service. With ``evict``,
        that robot is moved away; without, the choice waits (None) until after the policy, which
        may send the robot to work instead."""
        assert self.traffic is not None
        for charger in self.traffic.nearest_chargers(node):
            if self.traffic.table.is_free_from(charger, self.now):
                return charger
            holder = self.traffic.idle_holder(charger, self.robots)
            if holder is not None and holder not in self._low:
                if not evict:
                    return None
                self._move_out_of_the_way(holder, pickups)
                return charger
        return None

    def _send_to_charger(
        self, robot_id: RobotId, charger: NodeId, pickups: frozenset[NodeId]
    ) -> None:
        assert self.traffic is not None and self._energy_model is not None
        robot = self.robots[robot_id]
        move = self.traffic.plan_move(robot, self.now, pickups, target=charger)
        self._follow(robot_id, move, move.path, move.stop_visits, move.releases)
        self._set(robot.reposition(move.route, tick=self.now, until_tick=move.holding_tick))
        self._push(max(self.now, move.path.visits[0].depart), _Depart(robot_id))
        cost = sum(
            self._energy_model.edge_cost(a, b) for a, b in itertools.pairwise(move.route.nodes)
        )
        self._arrival_level[robot_id] = self._level(robot_id) - cost
        self._charging_trips += 1

    def _charging_ready(self) -> dict[RobotId, tuple[int, NodeId]]:
        """When and where each robot out of service is expected back in service."""
        if self._energy_model is None or self.traffic is None:
            return {}
        model, now, ready = self._energy_model, self.now, {}
        for robot_id in self._low:
            robot = self.robots[robot_id]
            if robot_id in self._arrival_level:  # driving to a charger
                arrival, charger = self.traffic.ready[robot_id]
                charge = model.ticks_to_reach(self._arrival_level[robot_id], model.high)
                ready[robot_id] = (arrival + charge, charger)
            elif robot_id in self._charging_since:  # charging
                ready[robot_id] = (
                    now + model.ticks_to_reach(self._level(robot_id), model.high),
                    robot.node,
                )
            else:  # waiting for a charger: a rough guess, the nearest charger
                chargers = self.traffic.nearest_chargers(robot.node)
                trip = self.traffic.ticks_between(robot.node, chargers[0]) or 0
                charge = model.ticks_to_reach(self._level(robot_id), model.high)
                ready[robot_id] = (now + trip + charge, chargers[0])
        return ready

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
            if self.traffic is not None and robot.phase.is_travelling:
                # Drives end on arrival, at a time the delays decide: no phase-end event.
                if robot.robot_id in self._scheduled or robot.robot_id not in self._cursors:
                    raise SimulationInvariantError(
                        f"robot {robot.robot_id!r}: a drive must follow a plan, without an event"
                    )
                continue
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
        """How long the robot's current phase must last (free flow, or loading and
        unloading; drives with traffic are checked on arrival instead)."""
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
            trajectories=MappingProxyType(self._trajectories()),
            traffic=self.traffic is not None,
            moves_out_of_the_way=self.moves,
            deferred_assignments=self.deferred,
            decision_seconds=tuple(self._decision_seconds),
            battery=self._energy_model is not None,
            charging_trips=self._charging_trips,
            out_of_service_ticks=self._out_of_service
            + sum(self.now - since for since in self._low.values()),
            charger_wait_ticks=self._charger_wait
            + sum(self.now - since for since in self._waiting_since.values()),
            lowest_energy_share=(
                self._lowest / self._energy_model.capacity if self._energy_model else None
            ),
        )
