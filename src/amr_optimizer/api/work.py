"""The service's CPU-bound work, run in worker processes (D29).

Every function here takes a request model and returns a response model, so it can be sent to a
process pool. Requests are converted to the engine's own types in this module and nowhere else.
"""

import functools
import time
from collections.abc import Mapping
from types import MappingProxyType

from amr_optimizer.api.models import TICK_S, Assignment, PlanRequest, PlanResponse
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import (
    OrderId,
    OrderValidationError,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.sim import DispatchSnapshot, TravelPlanner
from amr_optimizer.warehouse import PRESETS, NodeId, WarehouseGraph, generate_grid_warehouse


class InvalidRequestError(ValueError):
    """A request that is well formed but does not fit the map, such as an unknown node."""


@functools.cache
def preset_graph(name: str) -> WarehouseGraph:
    """The preset map ``name``, generated once per process."""
    return generate_grid_warehouse(PRESETS[name])


def snapshot_of(request: PlanRequest) -> DispatchSnapshot:
    """The engine's view of ``request``.

    A busy robot is idle at the node where it will be free, but unavailable now, and planned to be
    ready there at its ``ready_tick``: exactly how look-ahead planners see a robot that is out of
    service until a known tick (D18, D26).
    """
    graph = preset_graph(request.map)
    for node in [r.node for r in request.robots] + [
        n for o in request.orders for n in (o.pickup, o.dropoff)
    ]:
        if node not in graph:
            raise InvalidRequestError(f"node {node!r} is not on map {request.map!r}")
    time_model = TimeModel(TICK_S)
    robots = tuple(RobotState.idle(RobotId(r.id), NodeId(r.node)) for r in request.robots)
    ready: Mapping[RobotId, tuple[int, NodeId]] = {
        RobotId(r.id): (r.ready_tick, NodeId(r.node))
        for r in request.robots
        if r.ready_tick is not None
    }
    try:
        orders = tuple(
            TransportOrder(OrderId(o.id), NodeId(o.pickup), NodeId(o.dropoff), o.release_tick)
            for o in request.orders
        )
    except OrderValidationError as error:
        raise InvalidRequestError(str(error)) from error
    travel_times = TravelTimes(graph, speed_mps=request.speed_mps, time_model=time_model)
    return DispatchSnapshot(
        request.tick,
        robots,
        orders,
        TravelPlanner(travel_times),
        load_ticks=time_model.to_ticks(request.load_s),
        unload_ticks=time_model.to_ticks(request.unload_s),
        planned_ready=MappingProxyType(dict(ready)),
        unavailable=frozenset(ready),
    )


def decide(request: PlanRequest) -> PlanResponse:
    """One dispatch decision by the requested policy."""
    snapshot = snapshot_of(request)
    policy = POLICIES[request.policy]()
    started = time.perf_counter()
    assignments = policy.assign(snapshot)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return PlanResponse(
        policy=request.policy,
        tick=request.tick,
        assignments=[Assignment(robot=a.robot_id, order=a.order_id) for a in assignments],
        compute_ms=elapsed_ms,
    )
