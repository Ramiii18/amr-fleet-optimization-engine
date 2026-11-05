"""Domain model of the fleet: time units, transport orders, robots and their state machine,
and batteries."""

from amr_optimizer.domain.energy import UNITS_PER_METRE, BatterySpec, EnergyModel, safe_low_m
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
    "UNITS_PER_METRE",
    "BatterySpec",
    "EnergyModel",
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
    "safe_low_m",
]
