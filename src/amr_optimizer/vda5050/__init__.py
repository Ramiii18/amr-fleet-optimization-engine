"""A mock interface shaped like VDA 5050, version 3.0.0 (Phase 2, P5, D30).

No real robot, broker or vendor software is involved: messages go through an in-memory bus, and
the robots are mock vehicles. Every message is validated against the specification's published
JSON schemas in the tests.
"""

from amr_optimizer.vda5050.fleet import FleetControl, FleetRun, Job, first_jobs, run_jobs
from amr_optimizer.vda5050.messages import (
    INTERFACE_NAME,
    MAJOR_VERSION,
    VERSION,
    Action,
    ActionState,
    Connection,
    Edge,
    EdgeState,
    Header,
    HeaderCounter,
    InstantActions,
    Node,
    NodePosition,
    NodeState,
    Order,
    State,
    format_timestamp,
    parse_timestamp,
    topic,
)
from amr_optimizer.vda5050.mock import Message, MessageBus, MockVehicle, OrderRejectedError
from amr_optimizer.vda5050.orders import (
    edge_id,
    order_message,
    order_violations,
    route_of,
    update_violations,
)

__all__ = [
    "INTERFACE_NAME",
    "MAJOR_VERSION",
    "VERSION",
    "Action",
    "ActionState",
    "Connection",
    "Edge",
    "EdgeState",
    "FleetControl",
    "FleetRun",
    "Header",
    "HeaderCounter",
    "InstantActions",
    "Job",
    "Message",
    "MessageBus",
    "MockVehicle",
    "Node",
    "NodePosition",
    "NodeState",
    "Order",
    "OrderRejectedError",
    "State",
    "edge_id",
    "first_jobs",
    "format_timestamp",
    "order_message",
    "order_violations",
    "parse_timestamp",
    "route_of",
    "run_jobs",
    "topic",
    "update_violations",
]
