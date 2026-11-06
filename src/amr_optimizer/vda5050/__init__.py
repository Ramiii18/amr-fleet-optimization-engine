"""A mock interface shaped like VDA 5050, version 3.0.0 (Phase 2, P5, D30).

No real robot, broker or vendor software is involved: messages go through an in-memory bus, and
the robots are mock vehicles. Every message is validated against the specification's published
JSON schemas in the tests.
"""

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

__all__ = [
    "INTERFACE_NAME",
    "MAJOR_VERSION",
    "VERSION",
    "Action",
    "ActionState",
    "Connection",
    "Edge",
    "EdgeState",
    "Header",
    "HeaderCounter",
    "InstantActions",
    "Node",
    "NodePosition",
    "NodeState",
    "Order",
    "State",
    "format_timestamp",
    "parse_timestamp",
    "topic",
]
