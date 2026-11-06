"""Fleet control over VDA 5050 messages, and runs of mock robots (D30).

Fleet control sends each robot its job as an order and releases a base that follows the planned
order of robots at every node and corridor (D25): it extends a robot's base by the next edge and
its end node only when that robot is first in the planned order of both. A robot can stop only at a
node, so this is stricter than the simulator's execution, where a robot may enter a corridor and
wait at its end.

Fleet control knows only what the robots report. Each state message says which node a robot
reached last and whether it is driving again, and from that fleet control marks the nodes and
corridors the robot has left.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from amr_optimizer.domain import RobotId, TransportOrder
from amr_optimizer.scenario import Scenario
from amr_optimizer.sim.traffic_control import TrafficControl
from amr_optimizer.traffic import FOREVER, Corridor, ResourceOrder, TimedPath, Visit
from amr_optimizer.vda5050.messages import Action, Edge, HeaderCounter, Node, State, topic
from amr_optimizer.vda5050.mock import Message, MessageBus, MockVehicle, clock
from amr_optimizer.vda5050.orders import order_message, route_of
from amr_optimizer.warehouse import NodeId, WarehouseGraph

STALL_TICKS = 6_000  # 10 simulated minutes without any robot moving: a run has stalled


class StalledError(RuntimeError):
    """No robot moved for `STALL_TICKS`: fleet control is waiting on a cycle it cannot release."""


@dataclass(frozen=True, slots=True)
class Job:
    """One planned job to send: the robot's itinerary, and the visits where it picks and drops
    (``None`` for a plain move)."""

    robot: RobotId
    order_id: str
    path: TimedPath
    pickup: int | None = None
    dropoff: int | None = None


@dataclass(slots=True)
class _Robot:
    serial: str
    headers: HeaderCounter
    route: tuple[tuple[Node, ...], tuple[Edge, ...]] | None = None
    order_id: str = ""
    update_id: int = 0
    base_end: int = 0
    reached: int = 0  # route index of the last node reached
    left: bool = False  # whether it has left that node again
    drop_action: str | None = None
    finished: set[str] = field(default_factory=set)  # action ids reported finished


class FleetControl:
    """Sends orders and updates, and follows the robots through their state messages."""

    def __init__(
        self,
        graph: WarehouseGraph,
        map_id: str,
        bus: MessageBus,
        tick_s: float,
        starts: Mapping[RobotId, NodeId],
        *,
        manufacturer: str = "synthetic",
    ) -> None:
        self._graph, self._map_id, self._bus = graph, map_id, bus
        self._manufacturer, self._tick_s = manufacturer, tick_s
        self._order = ResourceOrder()
        self._robots: dict[RobotId, _Robot] = {}
        for robot, node in starts.items():
            self._order.add(robot, TimedPath((Visit(node, 0, FOREVER),)), include_start=True)
            self._robots[robot] = _Robot(str(robot), HeaderCounter(manufacturer, str(robot)))
        self.delivered_at: dict[str, int] = {}  # order id -> tick its drop was reported finished
        self.rotations = 0  # robots released as part of a cyclic wait (D31)

    def assign(self, tick: int, jobs: Sequence[Job]) -> None:
        """Send ``jobs`` as new orders. Each path must start where its robot stands.

        Every job joins the planned order before any base is released: otherwise a robot could
        be released through a node at which a job sent after it, in the same call, is planned
        earlier (a bug the larger demo runs found).
        """
        for job in jobs:
            robot = self._robots[job.robot]
            actions: dict[int, list[Action]] = {}
            load = (("loadId", job.order_id),)
            if job.pickup is not None:
                actions[job.pickup] = [Action("pick", f"{job.order_id}-pick", "HARD", load)]
            if job.dropoff is not None:
                actions[job.dropoff] = [Action("drop", f"{job.order_id}-drop", "HARD", load)]
                robot.drop_action = f"{job.order_id}-drop"
            robot.route = route_of(job.path, self._graph, self._map_id, actions)
            robot.order_id, robot.update_id = job.order_id, 0
            robot.base_end, robot.reached, robot.left = 0, 0, False
            self._order.add(job.robot, job.path, include_start=False)
        for job in jobs:
            robot = self._robots[job.robot]
            robot.base_end = self._releasable(job.robot)
            self._send(tick, robot, start=0)

    def receive(self, tick: int) -> None:
        """Read every robot's new state messages, and note what each robot has left."""
        for robot_id, robot in self._robots.items():
            state_topic = topic(self._manufacturer, robot.serial, "state")
            for data in self._bus.take(state_topic):
                self._follow(tick, robot_id, robot, State.from_json(data))

    def release(self, tick: int) -> None:
        """Extend every base as far as the planned order allows, with an order update."""
        for robot_id, robot in self._robots.items():
            if robot.route is None:
                continue
            end = self._releasable(robot_id)
            if end > robot.base_end:
                start, robot.base_end = robot.base_end, end
                robot.update_id += 1
                self._send(tick, robot, start=start)
        self._release_rotations(tick)

    def _ready_to_leave(self, robot: _Robot) -> bool:
        """Standing at its decision point, with every action there finished."""
        if robot.route is None or robot.left or robot.reached != robot.base_end:
            return False
        actions = robot.route[0][robot.reached].actions
        return all(a.action_id in robot.finished for a in actions)

    def _release_rotations(self, tick: int) -> None:
        """Release cyclic waits as one rotation (D31).

        A robot can stop only at a node, so robots standing on a cycle of nodes, each waiting
        for the node the next one stands on, would wait forever: each next node is released only
        once its occupant has left it. When every robot of such a cycle is ready to leave, is
        first on its next corridor, and is the next user of its next node after that node's
        occupant, all of them are released one step at once. They leave in the same tick, and
        each reaches its next node at least one tick after the robot ahead has left it.
        """
        blocker: dict[RobotId, RobotId] = {}
        for robot_id, robot in self._robots.items():
            if not self._ready_to_leave(robot) or robot.route is None:
                continue
            nodes = robot.route[0]
            if robot.base_end + 1 >= len(nodes):
                continue
            here, there = (
                NodeId(nodes[robot.base_end].node_id),
                NodeId(nodes[robot.base_end + 1].node_id),
            )
            if self._order.next_user(Corridor.between(here, there)) != robot_id:
                continue
            users = self._order.users(there)
            if len(users) < 2 or users[1] != robot_id:
                continue
            occupant = self._robots[users[0]]
            if not self._ready_to_leave(occupant) or occupant.route is None:
                continue
            if NodeId(occupant.route[0][occupant.reached].node_id) == there:
                blocker[robot_id] = users[0]
        seen: set[RobotId] = set()
        for start in blocker:
            chain: list[RobotId] = []
            current: RobotId | None = start
            while current is not None and current in blocker and current not in seen:
                if current in chain:
                    break
                chain.append(current)
                current = blocker[current]
            if current is not None and current in chain:
                for member in chain[chain.index(current) :]:
                    robot = self._robots[member]
                    robot.base_end += 1
                    robot.update_id += 1
                    self._send(tick, robot, start=robot.base_end - 1)
                    self.rotations += 1
            seen.update(chain)

    def _releasable(self, robot_id: RobotId) -> int:
        """The last node the robot may be released to.

        A route can use a resource twice: a robot drives into a pickup's dead-end spur and back
        through the same intersection, and another robot may be planned through it in between.
        While the robot's earlier use is released but not yet left, the robot itself heads that
        resource's queue, which says nothing about its later use. A later use is therefore
        released only once the earlier one has been left.
        """
        robot = self._robots[robot_id]
        assert robot.route is not None
        nodes, end = robot.route[0], robot.base_end

        def node(i: int) -> NodeId:
            return NodeId(nodes[i].node_id)

        def corridor(i: int) -> Corridor:  # between node i and node i + 1
            return Corridor.between(node(i), node(i + 1))

        first_node = robot.reached + (1 if robot.left else 0)
        pending: set[NodeId | Corridor] = {node(i) for i in range(first_node, end + 1)}
        pending |= {corridor(i) for i in range(robot.reached, end)}
        while end + 1 < len(nodes):
            there, way = node(end + 1), corridor(end)
            if there in pending or way in pending:
                break
            if self._order.next_user(way) != robot_id or self._order.next_user(there) != robot_id:
                break
            pending |= {there, way}
            end += 1
        return end

    def _send(self, tick: int, robot: _Robot, *, start: int) -> None:
        assert robot.route is not None
        order_topic = topic(self._manufacturer, robot.serial, "order")
        header = robot.headers.next("order", clock(tick, self._tick_s))
        message = order_message(
            header,
            robot.order_id,
            robot.update_id,
            robot.route,
            start=start,
            base_end=robot.base_end,
        )
        self._bus.publish(tick, order_topic, message.to_json())

    def _follow(self, tick: int, robot_id: RobotId, robot: _Robot, state: State) -> None:
        if robot.route is None or state.order_id != robot.order_id:
            return
        nodes = robot.route[0]
        target = (state.last_node_sequence_id // 2, state.driving)
        while (robot.reached, robot.left) < target:
            if not robot.left:  # it left the node it had reached
                self._order.leave(NodeId(nodes[robot.reached].node_id), robot_id, tick)
                robot.left = True
            else:  # it reached the next node: it left the corridor before it
                here = NodeId(nodes[robot.reached].node_id)
                there = NodeId(nodes[robot.reached + 1].node_id)
                self._order.leave(Corridor.between(here, there), robot_id, tick)
                robot.reached, robot.left = robot.reached + 1, False
        done = {a.action_id for a in state.action_states if a.action_status == "FINISHED"}
        robot.finished = done
        if robot.drop_action in done and robot.order_id not in self.delivered_at:
            self.delivered_at[robot.order_id] = tick


# --- runs ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FleetRun:
    """What a run of mock robots produced: every message, every robot's trajectory, and when
    each job was delivered, as fleet control saw it and as planned."""

    log: tuple[Message, ...]
    trajectories: Mapping[RobotId, TimedPath]
    delivered_at: Mapping[str, int]
    planned_at: Mapping[str, int] = field(default_factory=dict)
    end_tick: int = 0


def first_jobs(scenario: Scenario, traffic: TrafficControl, limit: int | None = None) -> list[Job]:
    """One job per robot at tick 0, planned with SIPP as the simulator does: the i-th robot
    takes the i-th order whose pickup no earlier job uses and nobody stands on.

    As in the simulator, no robot is sent to park after its job on the pickup of another job
    (``avoid``): it would hold that pickup before the other robot gets there.
    """
    robots = scenario.robots[:limit]
    taken = {robot.node for robot in scenario.robots}
    chosen: list[TransportOrder] = []
    for order in scenario.orders:
        if len(chosen) == len(robots):
            break
        if order.pickup not in taken:
            taken.add(order.pickup)
            chosen.append(order)
    avoid = frozenset(order.pickup for order in chosen)
    jobs: list[Job] = []
    for robot, order in zip(robots, chosen, strict=False):
        planned = traffic.plan_job(robot, order, 0, avoid)
        pickup, dropoff, _ = planned.stop_visits
        jobs.append(Job(robot.robot_id, str(order.id), planned.path, pickup, dropoff))
    return jobs


def run_jobs(
    scenario: Scenario,
    jobs: Sequence[Job],
    *,
    map_id: str,
    max_tick: int = 100_000,
) -> FleetRun:
    """Execute ``jobs`` with one mock robot per robot of ``scenario``, through messages only.

    Each tick, in order: every robot steps (drives, acts, reports); fleet control reads the
    states and extends bases; every robot reads its orders. Robots therefore start a released
    edge one tick after fleet control releases it.
    """
    bus = MessageBus()
    tick_s = scenario.time_model.tick_s
    starts = {r.robot_id: r.node for r in scenario.robots}
    fleet = FleetControl(scenario.graph, map_id, bus, tick_s, starts)
    action_ticks = {"pick": scenario.load_ticks, "drop": scenario.unload_ticks}
    vehicles = {
        r.robot_id: MockVehicle(
            str(r.robot_id),
            r.node,
            bus,
            scenario.time_model,
            speed_mps=scenario.speed_mps,
            action_ticks=action_ticks,
        )
        for r in scenario.robots
    }
    for vehicle in vehicles.values():
        vehicle.connect(0)
    fleet.assign(0, jobs)
    tick, last_change = 0, 0
    progress: tuple[int, int] | None = None
    while True:
        for vehicle in vehicles.values():
            vehicle.receive(tick)
        delivered = len(fleet.delivered_at) == sum(j.dropoff is not None for j in jobs)
        if (delivered and all(v.idle for v in vehicles.values())) or tick >= max_tick:
            break
        tick += 1
        for vehicle in vehicles.values():
            vehicle.step(tick)
        fleet.receive(tick)
        fleet.release(tick)
        signature = (sum(len(v.track) for v in vehicles.values()), len(fleet.delivered_at))
        if signature != progress:
            progress, last_change = signature, tick
        elif tick - last_change > STALL_TICKS:
            raise StalledError(f"no robot has moved since tick {last_change}")
    trajectories = {}
    for robot_id, vehicle in vehicles.items():
        *done, last = vehicle.track
        trajectories[robot_id] = TimedPath(
            (*done, Visit(last.node, last.arrive, max(last.arrive, tick)))
        )
    planned = {
        j.order_id: j.path.visits[j.dropoff].arrive + scenario.unload_ticks
        for j in jobs
        if j.dropoff is not None
    }
    return FleetRun(tuple(bus.log), trajectories, dict(fleet.delivered_at), planned, tick)
