"""The event log: one entry per step in an order's life, the raw material for KPIs."""

from dataclasses import dataclass
from enum import Enum

from amr_optimizer.domain import OrderId, RobotId
from amr_optimizer.warehouse import NodeId


class EventKind(Enum):
    """The steps of an order's life, in the order every delivered order goes through them."""

    RELEASED = "released"  # the order becomes known
    ASSIGNED = "assigned"  # a robot takes it and starts driving to the pickup
    PICKUP_REACHED = "pickup_reached"  # the robot is at the pickup and starts loading
    LOADED = "loaded"  # loading is done; the robot starts driving to the drop-off
    DROPOFF_REACHED = "dropoff_reached"  # the robot is at the drop-off and starts unloading
    DELIVERED = "delivered"  # unloading is done; the robot is idle again


ORDER_LIFECYCLE: tuple[EventKind, ...] = tuple(EventKind)
"""All event kinds in lifecycle order."""


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One step of one order.

    ``node`` is where it happened: the pickup for a release, otherwise the robot's node.
    ``distance_m`` is the length of the route the robot starts with this step. It is
    non-zero only for ASSIGNED (empty travel to the pickup) and LOADED (loaded travel).
    """

    tick: int
    kind: EventKind
    order_id: OrderId
    node: NodeId
    robot_id: RobotId | None = None  # None only for RELEASED
    distance_m: float = 0.0
