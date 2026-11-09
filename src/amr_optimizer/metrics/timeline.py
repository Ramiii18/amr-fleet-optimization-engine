"""The way of every order through a run: when each step happened, and by which robot.

The rows use the KPI definitions exactly (D17): waiting is from release until the pickup is
reached, completion from release until delivery, both counted in ticks and converted once.
The mean of the delivered rows' completion times is therefore the completion KPI.
"""

from dataclasses import dataclass

from amr_optimizer.domain import OrderId, RobotId
from amr_optimizer.sim import EventKind, SimulationResult


@dataclass(frozen=True, slots=True)
class OrderTimeline:
    """One order in one run. Times are seconds from the start of the run; a step that did not
    happen (an order not yet assigned or delivered when the run ended) is ``None``."""

    order_id: OrderId
    robot_id: RobotId | None
    pickup: str
    dropoff: str
    released_s: float | None
    assigned_s: float | None
    pickup_reached_s: float | None
    loaded_s: float | None
    dropoff_reached_s: float | None
    delivered_s: float | None
    waiting_s: float | None
    completion_s: float | None
    empty_m: float
    loaded_m: float


def order_timelines(result: SimulationResult) -> tuple[OrderTimeline, ...]:
    """One row per order of the scenario, in release order."""
    tick_s = result.scenario.time_model.tick_s
    first: dict[tuple[OrderId, EventKind], int] = {}
    robot: dict[OrderId, RobotId] = {}
    empty_m: dict[OrderId, float] = {}
    loaded_m: dict[OrderId, float] = {}
    for entry in result.log:
        first.setdefault((entry.order_id, entry.kind), entry.tick)
        if entry.kind is EventKind.ASSIGNED:
            if entry.robot_id is not None:
                robot.setdefault(entry.order_id, entry.robot_id)
            empty_m[entry.order_id] = empty_m.get(entry.order_id, 0.0) + entry.distance_m
        elif entry.kind is EventKind.LOADED:
            loaded_m[entry.order_id] = loaded_m.get(entry.order_id, 0.0) + entry.distance_m

    def since_release(order_id: OrderId, kind: EventKind) -> float | None:
        released = first.get((order_id, EventKind.RELEASED))
        reached = first.get((order_id, kind))
        if released is None or reached is None:
            return None
        return (reached - released) * tick_s

    def at(order_id: OrderId, kind: EventKind) -> float | None:
        tick = first.get((order_id, kind))
        return None if tick is None else tick * tick_s

    rows = []
    for order in result.scenario.orders:
        rows.append(
            OrderTimeline(
                order_id=order.id,
                robot_id=robot.get(order.id),
                pickup=order.pickup,
                dropoff=order.dropoff,
                released_s=at(order.id, EventKind.RELEASED),
                assigned_s=at(order.id, EventKind.ASSIGNED),
                pickup_reached_s=at(order.id, EventKind.PICKUP_REACHED),
                loaded_s=at(order.id, EventKind.LOADED),
                dropoff_reached_s=at(order.id, EventKind.DROPOFF_REACHED),
                delivered_s=at(order.id, EventKind.DELIVERED),
                waiting_s=since_release(order.id, EventKind.PICKUP_REACHED),
                completion_s=since_release(order.id, EventKind.DELIVERED),
                empty_m=empty_m.get(order.id, 0.0),
                loaded_m=loaded_m.get(order.id, 0.0),
            )
        )
    return tuple(rows)
