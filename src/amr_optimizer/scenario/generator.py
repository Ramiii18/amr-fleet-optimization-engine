"""Seeded scenario generation: a stream of transport orders and the fleet's start positions.

**Arrivals.** Time is integer ticks (D6), so orders arrive in a *Bernoulli process*: in
each tick, an order is released with probability p = rate x tick length. This is the
discrete-time counterpart of a Poisson process: counts are binomial, gaps between orders
are geometric, and both converge to the Poisson case as p gets small (p is about 0.0017
at 60 orders per hour and 0.1 s ticks). Release times need no rounding, and at most one
order is released per tick.

**Reproducibility (D14).** Only ``random``, ``choice`` and ``sample`` of `random.Random`
are used. They need no platform maths library (an exponential sampler would call ``log``),
so a seed gives the same scenario on Windows and Linux; a test pins one scenario and CI
checks it on both. Orders and robot placement draw from two separate streams derived from
the seed, so changing the fleet size leaves the order stream unchanged. That makes
comparisons across fleet sizes paired: every fleet faces the same orders.
"""

import math
import random
from dataclasses import dataclass

from amr_optimizer.domain import OrderId, RobotId, RobotState, TimeModel, TransportOrder
from amr_optimizer.scenario.model import Scenario, ScenarioValidationError
from amr_optimizer.warehouse import NodeId, NodeKind, WarehouseGraph

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """Parameters of a generated scenario. All values are synthetic defaults."""

    robots: int = 5
    orders_per_hour: float = 60.0
    duration_s: float = 3600.0  # orders are released during [0, duration)
    speed_mps: float = 1.0
    tick_s: float = 0.1
    load_s: float = 10.0
    unload_s: float = 10.0

    def __post_init__(self) -> None:
        if self.robots < 1:
            raise ScenarioValidationError(f"robots must be at least 1, got {self.robots}")
        for name in ("orders_per_hour", "duration_s", "speed_mps", "tick_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ScenarioValidationError(f"{name} must be positive, got {value!r}")
        for name in ("load_s", "unload_s"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ScenarioValidationError(f"{name} must be non-negative, got {value!r}")


def generate_orders(
    graph: WarehouseGraph,
    *,
    orders_per_hour: float,
    duration_ticks: int,
    time_model: TimeModel,
    rng: random.Random,
) -> tuple[TransportOrder, ...]:
    """Retrieval orders: from a uniformly random storage location to a uniformly random station.

    Each of the ticks ``0 .. duration_ticks - 1`` releases one order with probability
    ``orders_per_hour / 3600 * tick_s``. Order ids are numbered in release order.
    """
    storage = sorted(node.id for node in graph.nodes_of_kind(NodeKind.STORAGE))
    stations = sorted(node.id for node in graph.nodes_of_kind(NodeKind.STATION))
    if not storage or not stations:
        raise ScenarioValidationError("the map needs at least one storage location and station")
    probability = orders_per_hour / SECONDS_PER_HOUR * time_model.tick_s
    if probability > 1:
        raise ScenarioValidationError(
            f"{orders_per_hour} orders per hour is more than one order per tick"
        )

    orders: list[TransportOrder] = []
    for tick in range(duration_ticks):
        if rng.random() < probability:
            order_id = OrderId(f"O{len(orders) + 1:05d}")
            orders.append(TransportOrder(order_id, rng.choice(storage), rng.choice(stations), tick))
    return tuple(orders)


def place_robots(graph: WarehouseGraph, count: int, rng: random.Random) -> tuple[RobotState, ...]:
    """Idle robots at tick 0, each on its own node, as at the start of a shift.

    Parking spots are filled first, then chargers, both in id order. Any further robots
    start at storage locations drawn at random. Stations are left free for the orders.
    """
    homes = [
        node.id
        for kind in (NodeKind.PARKING, NodeKind.CHARGER)
        for node in sorted(graph.nodes_of_kind(kind), key=lambda node: node.id)
    ]
    starts: list[NodeId] = homes[:count]
    if count > len(starts):
        storage = sorted(node.id for node in graph.nodes_of_kind(NodeKind.STORAGE))
        extra = count - len(starts)
        if extra > len(storage):
            raise ScenarioValidationError(
                f"the map has room for {len(homes) + len(storage)} robots, not {count}"
            )
        starts.extend(rng.sample(storage, extra))
    return tuple(RobotState.idle(RobotId(f"R{i + 1:02d}"), node) for i, node in enumerate(starts))


def generate_scenario(graph: WarehouseGraph, spec: ScenarioSpec, *, seed: int) -> Scenario:
    """A complete scenario on ``graph``, fully determined by ``spec`` and ``seed``."""
    time_model = TimeModel(spec.tick_s)
    orders = generate_orders(
        graph,
        orders_per_hour=spec.orders_per_hour,
        duration_ticks=time_model.to_ticks(spec.duration_s),
        time_model=time_model,
        rng=random.Random(f"{seed}:orders"),
    )
    robots = place_robots(graph, spec.robots, random.Random(f"{seed}:fleet"))
    return Scenario(
        graph=graph,
        time_model=time_model,
        speed_mps=spec.speed_mps,
        load_ticks=time_model.to_ticks(spec.load_s),
        unload_ticks=time_model.to_ticks(spec.unload_s),
        robots=robots,
        orders=orders,
    )
