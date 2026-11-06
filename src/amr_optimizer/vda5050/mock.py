"""A message bus and a mock robot that speaks VDA 5050 3.0.0 (D30).

The bus carries JSON text on topics named as the specification requires, and logs every message.
The mock robot accepts an order only where it stands, accepts an update only from the stitching
node, drives only its released base at constant speed, runs its actions for their handling time,
and publishes its state on every event, and at least every 30 s.
"""

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from amr_optimizer.domain import TimeModel
from amr_optimizer.traffic import FOREVER, Visit
from amr_optimizer.vda5050.messages import (
    Action,
    ActionState,
    ActionStatus,
    Connection,
    Edge,
    EdgeState,
    HeaderCounter,
    Json,
    Node,
    NodeState,
    Order,
    State,
    topic,
)
from amr_optimizer.vda5050.orders import order_violations, update_violations
from amr_optimizer.warehouse import NodeId

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
"""Tick 0 of a run, as a wall-clock time for message timestamps (an arbitrary choice)."""
STATE_EVERY_S = 30.0  # the specification: a state at least every 30 seconds


def clock(tick: int, tick_s: float) -> datetime:
    return EPOCH + timedelta(seconds=tick * tick_s)


@dataclass(frozen=True, slots=True)
class Message:
    tick: int
    topic: str
    payload: str  # JSON text, as it would travel


class MessageBus:
    """An in-memory stand-in for an MQTT broker: messages wait on their topic until taken."""

    def __init__(self) -> None:
        self.log: list[Message] = []
        self._waiting: dict[str, list[str]] = defaultdict(list)

    def publish(self, tick: int, topic_name: str, data: Json) -> None:
        text = json.dumps(data, separators=(",", ":"))
        self.log.append(Message(tick, topic_name, text))
        self._waiting[topic_name].append(text)

    def take(self, topic_name: str) -> list[Json]:
        return [json.loads(text) for text in self._waiting.pop(topic_name, [])]


class OrderRejectedError(ValueError):
    """An order or update the robot must not accept: a bug in fleet control."""


@dataclass(slots=True)
class _Run:
    action: Action
    node_sequence: int
    status: ActionStatus = "WAITING"
    remaining: int = 0


class MockVehicle:
    """A mock robot. ``action_ticks`` gives the handling time of each action type."""

    def __init__(
        self,
        serial_number: str,
        node: str,
        bus: MessageBus,
        time_model: TimeModel,
        *,
        manufacturer: str = "synthetic",
        speed_mps: float = 1.0,
        action_ticks: Mapping[str, int] | None = None,
    ) -> None:
        self.serial_number, self._bus, self._time = serial_number, bus, time_model
        self._speed, self._action_ticks = speed_mps, dict(action_ticks or {})
        self._headers = HeaderCounter(manufacturer, serial_number)
        self.order_topic = topic(manufacturer, serial_number, "order")
        self.state_topic = topic(manufacturer, serial_number, "state")
        self.connection_topic = topic(manufacturer, serial_number, "connection")
        self.node = node
        self.track: list[Visit] = [Visit(NodeId(node), 0, FOREVER)]
        self._order: Order | None = None  # the last order or update accepted
        self._nodes: dict[int, Node] = {}
        self._edges: dict[int, Edge] = {}
        self._last_sequence = 0
        self._edge_ticks_left = 0  # > 0 while driving an edge
        self._runs: dict[str, _Run] = {}
        self.driving = False
        self._last_state = -(10**9)

    # --- what fleet control sends --------------------------------------------------------------

    def connect(self, tick: int) -> None:
        header = self._headers.next("connection", clock(tick, self._time.tick_s))
        self._bus.publish(tick, self.connection_topic, Connection(header, "ONLINE").to_json())

    def receive(self, tick: int) -> None:
        for data in self._bus.take(self.order_topic):
            self._accept(Order.from_json(data))
            self._publish(tick)

    def _accept(self, order: Order) -> None:
        previous = self._order
        if previous is not None and order.order_id == previous.order_id:
            problems = update_violations(previous, order)
        else:
            problems = order_violations(order)
            if not self.idle:
                problems.append("a new order while the last one is not finished")
            elif order.nodes and order.nodes[0].node_id != self.node:
                problems.append(f"a new order must start where the robot is, {self.node!r}")
            else:
                self._nodes, self._edges, self._runs = {}, {}, {}
                self._last_sequence = order.nodes[0].sequence_id
        if problems:
            raise OrderRejectedError(f"{self.serial_number}: {problems}")
        for node in order.nodes:
            self._nodes[node.sequence_id] = node
            self._add_runs(node.actions, node.sequence_id)
        for edge in order.edges:
            self._edges[edge.sequence_id] = edge
        self._order = order

    def _add_runs(self, actions: tuple[Action, ...], sequence: int) -> None:
        for action in actions:  # an action id is unique: one re-sent in an update is the same
            self._runs.setdefault(action.action_id, _Run(action, sequence))

    # --- moving and acting ---------------------------------------------------------------------

    @property
    def idle(self) -> bool:
        """No upcoming node or edge, and every action finished (the specification's idle)."""
        upcoming = any(s > self._last_sequence for s in (*self._nodes, *self._edges))
        return not upcoming and all(r.status == "FINISHED" for r in self._runs.values())

    def step(self, tick: int) -> None:
        """Advance one tick: drive, arrive, act or leave. Publish the state on any change."""
        changed = False
        if self._edge_ticks_left:
            self._edge_ticks_left -= 1
            if not self._edge_ticks_left:  # traversed the next node
                self._last_sequence += 2
                self.node = self._nodes[self._last_sequence].node_id
                self.track.append(Visit(NodeId(self.node), tick, FOREVER))
                changed = True
        if not self._edge_ticks_left:
            changed |= self._act(tick)
            if not self._acting():
                changed |= self._leave_or_stop(tick)
        if changed or (tick - self._last_state) * self._time.tick_s >= STATE_EVERY_S:
            self._publish(tick)

    def _acting(self) -> bool:
        return any(
            r.node_sequence == self._last_sequence and r.status != "FINISHED"
            for r in self._runs.values()
        )

    def _act(self, tick: int) -> bool:
        """Run this node's actions one after another (they block: HARD)."""
        for run in self._runs.values():
            if run.node_sequence != self._last_sequence or run.status == "FINISHED":
                continue
            if run.status == "WAITING":
                run.status, run.remaining = (
                    "RUNNING",
                    self._action_ticks.get(run.action.action_type, 0),
                )
            elif run.remaining:
                run.remaining -= 1
            if not run.remaining:
                run.status = "FINISHED"
            return True
        return False

    def _leave_or_stop(self, tick: int) -> bool:
        edge = self._edges.get(self._last_sequence + 1)
        node = self._nodes.get(self._last_sequence + 2)
        if edge is not None and node is not None and edge.released and node.released:
            self.track[-1] = replace(self.track[-1], depart=tick)
            length = edge.length_m or 0.0
            self._edge_ticks_left = max(1, self._time.to_ticks(length / self._speed))
            self.driving = True
            return True
        if self.driving:  # at the decision point, or at the end of the order
            self.driving = False
            return True
        return False

    # --- what the robot reports ----------------------------------------------------------------

    def _publish(self, tick: int) -> None:
        order = self._order
        state = State(
            self._headers.next("state", clock(tick, self._time.tick_s)),
            order.order_id if order else "",
            order.order_update_id if order else 0,
            self._nodes[self._last_sequence].node_id if order else self.node,
            self._last_sequence,
            tuple(
                NodeState(n.node_id, s, n.released)
                for s, n in sorted(self._nodes.items())
                if s > self._last_sequence
            ),
            tuple(
                EdgeState(e.edge_id, s, e.released)
                for s, e in sorted(self._edges.items())
                if s > self._last_sequence
            ),
            self.driving,
            tuple(
                ActionState(r.action.action_id, r.status, r.action.action_type)
                for r in self._runs.values()
            ),
        )
        self._bus.publish(tick, self.state_topic, state.to_json())
        self._last_state = tick

    def finished(self, action_id: str) -> bool:
        run = self._runs.get(action_id)
        return run is not None and run.status == "FINISHED"
