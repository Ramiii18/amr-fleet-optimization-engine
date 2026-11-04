"""Domain model of the fleet: time units, transport orders, robots and their state machine."""

from amr_optimizer.domain.orders import OrderId, OrderValidationError, TransportOrder
from amr_optimizer.domain.robots import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    RobotId,
    RobotPhase,
    RobotState,
    RobotStateError,
)
from amr_optimizer.domain.timing import TICK_ROUNDING_TOLERANCE, TimeModel, TravelTimes

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TICK_ROUNDING_TOLERANCE",
    "InvalidTransitionError",
    "OrderId",
    "OrderValidationError",
    "RobotId",
    "RobotPhase",
    "RobotState",
    "RobotStateError",
    "TimeModel",
    "TransportOrder",
    "TravelTimes",
]
