"""Messages of the VDA 5050 interface, version 3.0.0 (D30): the subset this mock adapter uses.

Field names follow the specification's JSON schemas (camelCase in JSON, snake_case here). Every
message converts to a JSON object (`to_json`), and the messages that fleet control and the mock
robots read convert back (`from_json`). The tests validate every message against the official
schemas.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

VERSION = "3.0.0"
INTERFACE_NAME = "vda5050"
MAJOR_VERSION = "v3"

Json = dict[str, Any]
BlockingType = Literal["NONE", "SOFT", "SINGLE", "HARD"]
ActionStatus = Literal[
    "WAITING", "INITIALIZING", "RUNNING", "PAUSED", "RETRIABLE", "FINISHED", "FAILED"
]
ConnectionState = Literal["ONLINE", "OFFLINE", "HIBERNATING", "CONNECTION_BROKEN"]


def topic(manufacturer: str, serial_number: str, name: str) -> str:
    """The MQTT topic: interfaceName/majorVersion/manufacturer/serialNumber/topic."""
    return f"{INTERFACE_NAME}/{MAJOR_VERSION}/{manufacturer}/{serial_number}/{name}"


def format_timestamp(at: datetime) -> str:
    """ISO 8601 in UTC with milliseconds: YYYY-MM-DDTHH:mm:ss.fffZ."""
    utc = at.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Header:
    """The protocol header every message starts with. ``header_id`` counts per topic."""

    header_id: int
    timestamp: datetime
    manufacturer: str
    serial_number: str
    version: str = VERSION

    def to_json(self) -> Json:
        return {
            "headerId": self.header_id,
            "timestamp": format_timestamp(self.timestamp),
            "version": self.version,
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial_number,
        }

    @classmethod
    def from_json(cls, data: Json) -> "Header":
        return cls(
            data["headerId"],
            parse_timestamp(data["timestamp"]),
            data["manufacturer"],
            data["serialNumber"],
            data["version"],
        )


class HeaderCounter:
    """Header ids per topic: each sender increments its own count by 1 with each message."""

    def __init__(self, manufacturer: str, serial_number: str) -> None:
        self.manufacturer, self.serial_number = manufacturer, serial_number
        self._next: dict[str, int] = {}

    def next(self, topic_name: str, at: datetime) -> Header:
        header_id = self._next.get(topic_name, 0)
        self._next[topic_name] = header_id + 1
        return Header(header_id, at, self.manufacturer, self.serial_number)


@dataclass(frozen=True, slots=True)
class Action:
    action_type: str
    action_id: str
    blocking_type: BlockingType = "HARD"
    parameters: tuple[tuple[str, Any], ...] = ()

    def to_json(self) -> Json:
        data: Json = {
            "actionType": self.action_type,
            "actionId": self.action_id,
            "blockingType": self.blocking_type,
        }
        if self.parameters:
            data["actionParameters"] = [{"key": k, "value": v} for k, v in self.parameters]
        return data

    @classmethod
    def from_json(cls, data: Json) -> "Action":
        parameters = tuple((p["key"], p["value"]) for p in data.get("actionParameters", ()))
        return cls(data["actionType"], data["actionId"], data["blockingType"], parameters)


@dataclass(frozen=True, slots=True)
class NodePosition:
    x: float
    y: float
    map_id: str

    def to_json(self) -> Json:
        return {"x": self.x, "y": self.y, "mapId": self.map_id}


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    sequence_id: int
    released: bool
    position: NodePosition | None = None
    actions: tuple[Action, ...] = ()

    def to_json(self) -> Json:
        data: Json = {
            "nodeId": self.node_id,
            "sequenceId": self.sequence_id,
            "released": self.released,
            "actions": [a.to_json() for a in self.actions],
        }
        if self.position is not None:
            data["nodePosition"] = self.position.to_json()
        return data

    @classmethod
    def from_json(cls, data: Json) -> "Node":
        position = data.get("nodePosition")
        return cls(
            data["nodeId"],
            data["sequenceId"],
            data["released"],
            None
            if position is None
            else NodePosition(position["x"], position["y"], position["mapId"]),
            tuple(Action.from_json(a) for a in data["actions"]),
        )


@dataclass(frozen=True, slots=True)
class Edge:
    """A directional connection between two nodes. In 3.0.0 an edge does not name its nodes:
    the edge with sequence id n connects the nodes with sequence ids n - 1 and n + 1."""

    edge_id: str
    sequence_id: int
    released: bool
    length_m: float | None = None
    actions: tuple[Action, ...] = ()

    def to_json(self) -> Json:
        data: Json = {
            "edgeId": self.edge_id,
            "sequenceId": self.sequence_id,
            "released": self.released,
            "actions": [a.to_json() for a in self.actions],
        }
        if self.length_m is not None:
            data["length"] = self.length_m
        return data

    @classmethod
    def from_json(cls, data: Json) -> "Edge":
        return cls(
            data["edgeId"],
            data["sequenceId"],
            data["released"],
            data.get("length"),
            tuple(Action.from_json(a) for a in data["actions"]),
        )


@dataclass(frozen=True, slots=True)
class Order:
    """Fleet control to robot: nodes and edges to traverse. Released ones form the base."""

    header: Header
    order_id: str
    order_update_id: int
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]

    def to_json(self) -> Json:
        return self.header.to_json() | {
            "orderId": self.order_id,
            "orderUpdateId": self.order_update_id,
            "nodes": [n.to_json() for n in self.nodes],
            "edges": [e.to_json() for e in self.edges],
        }

    @classmethod
    def from_json(cls, data: Json) -> "Order":
        return cls(
            Header.from_json(data),
            data["orderId"],
            data["orderUpdateId"],
            tuple(Node.from_json(n) for n in data["nodes"]),
            tuple(Edge.from_json(e) for e in data["edges"]),
        )


@dataclass(frozen=True, slots=True)
class NodeState:
    node_id: str
    sequence_id: int
    released: bool

    def to_json(self) -> Json:
        return {"nodeId": self.node_id, "sequenceId": self.sequence_id, "released": self.released}


@dataclass(frozen=True, slots=True)
class EdgeState:
    edge_id: str
    sequence_id: int
    released: bool

    def to_json(self) -> Json:
        return {"edgeId": self.edge_id, "sequenceId": self.sequence_id, "released": self.released}


@dataclass(frozen=True, slots=True)
class ActionState:
    action_id: str
    action_status: ActionStatus
    action_type: str | None = None

    def to_json(self) -> Json:
        data: Json = {"actionId": self.action_id, "actionStatus": self.action_status}
        if self.action_type is not None:
            data["actionType"] = self.action_type
        return data


@dataclass(frozen=True, slots=True)
class State:
    """Robot to fleet control: where it is in its order, and what it is doing.

    ``node_states`` and ``edge_states`` list what is still to be traversed; ``last_node_id`` is
    the last node traversed, or the node the robot stands on. The mock robots report a constant
    charge, no errors, and no active emergency stop (D30)."""

    header: Header
    order_id: str
    order_update_id: int
    last_node_id: str
    last_node_sequence_id: int
    node_states: tuple[NodeState, ...]
    edge_states: tuple[EdgeState, ...]
    driving: bool
    action_states: tuple[ActionState, ...]
    instant_action_states: tuple[ActionState, ...] = ()
    state_of_charge: float = 100.0
    charging: bool = False
    operating_mode: str = "AUTOMATIC"
    errors: tuple[Json, ...] = field(default=())

    def to_json(self) -> Json:
        return self.header.to_json() | {
            "orderId": self.order_id,
            "orderUpdateId": self.order_update_id,
            "lastNodeId": self.last_node_id,
            "lastNodeSequenceId": self.last_node_sequence_id,
            "nodeStates": [n.to_json() for n in self.node_states],
            "edgeStates": [e.to_json() for e in self.edge_states],
            "driving": self.driving,
            "actionStates": [a.to_json() for a in self.action_states],
            "instantActionStates": [a.to_json() for a in self.instant_action_states],
            "powerSupply": {"stateOfCharge": self.state_of_charge, "charging": self.charging},
            "operatingMode": self.operating_mode,
            "errors": list(self.errors),
            "safetyState": {"activeEmergencyStop": "NONE", "fieldViolation": False},
        }

    @classmethod
    def from_json(cls, data: Json) -> "State":
        def actions(items: list[Json]) -> tuple[ActionState, ...]:
            return tuple(
                ActionState(a["actionId"], a["actionStatus"], a.get("actionType")) for a in items
            )

        return cls(
            Header.from_json(data),
            data["orderId"],
            data["orderUpdateId"],
            data["lastNodeId"],
            data["lastNodeSequenceId"],
            tuple(
                NodeState(n["nodeId"], n["sequenceId"], n["released"]) for n in data["nodeStates"]
            ),
            tuple(
                EdgeState(e["edgeId"], e["sequenceId"], e["released"]) for e in data["edgeStates"]
            ),
            data["driving"],
            actions(data["actionStates"]),
            actions(data["instantActionStates"]),
            data["powerSupply"]["stateOfCharge"],
            data["powerSupply"]["charging"],
            data["operatingMode"],
            tuple(data["errors"]),
        )


@dataclass(frozen=True, slots=True)
class InstantActions:
    header: Header
    actions: tuple[Action, ...]

    def to_json(self) -> Json:
        return self.header.to_json() | {"actions": [a.to_json() for a in self.actions]}


@dataclass(frozen=True, slots=True)
class Connection:
    header: Header
    connection_state: ConnectionState

    def to_json(self) -> Json:
        return self.header.to_json() | {"connectionState": self.connection_state}
