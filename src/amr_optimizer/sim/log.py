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


class RobotEventKind(Enum):
    """What happens to a robot besides the steps of an order (D35).

    These events are observations: the simulator records them where it changes the state they
    describe, and never reads them back. Recording them draws no random number, schedules
    nothing and changes no decision.

    Intervals are a pair of events and include their start tick but not their end tick. An
    interval still open when the run ends is closed at the run's last tick, exactly as the
    simulator's own counters do.
    """

    # --- batteries (D26, D27). ``level`` is the robot's energy at that tick, in units. ---
    CHARGING_STARTED = "charging_started"  # became idle on a charger: it charges from now on
    CHARGING_STOPPED = "charging_stopped"  # left the charger; ``level`` is what it charged to
    OUT_OF_SERVICE = "out_of_service"  # found below the low threshold: it gets no new job
    BACK_IN_SERVICE = "back_in_service"  # charged to the return level: it gets jobs again
    CHARGER_WAIT_STARTED = "charger_wait_started"  # out of service, and no charger is free
    CHARGER_WAIT_ENDED = "charger_wait_ended"  # a charger was found for it
    # Sent to the charger ``target``; ``reason`` is "low" (out of service) or "top_up".
    CHARGER_TRIP_STARTED = "charger_trip_started"

    # --- one corridor traversal, from ``node`` to ``target`` (D25, D28) ---
    # Both are drawn when the robot enters the corridor, from separate random streams, and both
    # can happen on the same traversal. ``ticks`` is the extra time of this cause alone.
    DELAY = "delay"  # an unannounced slow-down: nobody plans around it
    BREAKDOWN = "breakdown"  # an announced stop for a repair: recovery policies respond to it
    # The robot reached the end of the corridor but may not enter ``target`` yet (ordered
    # execution): it stands at the end of the corridor until the matching ENDED event.
    CORRIDOR_WAIT_STARTED = "corridor_wait_started"
    CORRIDOR_WAIT_ENDED = "corridor_wait_ended"

    # --- plans ---
    REPLANNED = "replanned"  # a new route was adopted after a breakdown (D28)
    MOVED_OUT_OF_THE_WAY = "moved_out_of_the_way"  # an idle robot was sent to ``target``


@dataclass(frozen=True, slots=True)
class RobotEvent:
    """One robot event. ``node`` is where the robot is: the node it stands on, or the node it
    left for a corridor. The other fields are set only for the kinds that have them (see
    `RobotEventKind`)."""

    tick: int
    kind: RobotEventKind
    robot_id: RobotId
    node: NodeId
    target: NodeId | None = None
    ticks: int | None = None
    level: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BatteryRecord:
    """The battery model of a run, in the integer units the simulator computes with (D26).

    A robot on a charger for ``t`` ticks from level ``e`` has
    ``min(capacity, e + t * charge_per_tick)``: the formula the simulator itself uses.
    """

    capacity: int
    charge_per_tick: int
    low: int  # below this at a decision, an idle robot goes out of service
    high: int
    back_in_service: int  # the level at which a robot out of service returns (D27)
    units_per_metre: int  # driving one metre costs this many units; nothing else costs energy
