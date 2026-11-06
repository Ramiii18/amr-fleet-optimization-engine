"""The service's CPU-bound work, run in worker processes (D29).

Every function here takes a request model and returns a response model, so it can be sent to a
process pool. Requests are converted to the engine's own types in this module and nowhere else.
"""

import functools
import math
import time
from collections.abc import Mapping
from dataclasses import asdict
from types import MappingProxyType

from amr_optimizer.api.models import (
    TICK_S,
    Assignment,
    Disturbance,
    Number,
    PlanRequest,
    PlanResponse,
    SimulationOutcome,
    SimulationRequest,
)
from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import (
    BatterySpec,
    ChargingPolicy,
    OrderId,
    OrderValidationError,
    RobotId,
    RobotState,
    TimeModel,
    TransportOrder,
    TravelTimes,
)
from amr_optimizer.metrics import compute_charging_kpis, compute_kpis
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import DispatchSnapshot, TravelPlanner, simulate
from amr_optimizer.traffic import NO_DELAYS, DelayModel, Recovery
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


def _model(disturbance: Disturbance | None, time_model: TimeModel) -> DelayModel:
    if disturbance is None:
        return NO_DELAYS
    low, high = (time_model.to_ticks(s) for s in (disturbance.min_s, disturbance.max_s))
    return DelayModel(disturbance.share, low, high)


def _json_safe(values: Mapping[str, object]) -> dict[str, Number]:
    """KPIs for JSON, which has no NaN: an undefined KPI (nothing delivered) becomes null."""
    safe: dict[str, Number] = {}
    for key, value in values.items():
        if isinstance(value, float) and not math.isfinite(value):
            safe[key] = None
        elif isinstance(value, float | int | str):
            safe[key] = value
    return safe


def simulate_job(request: SimulationRequest) -> SimulationOutcome:
    """A whole run, as `amr-sim` runs it: every random stream is seeded by ``request.seed``."""
    spec = ScenarioSpec(
        robots=request.robots,
        orders_per_hour=request.orders_per_hour,
        duration_s=request.duration_s,
    )
    scenario = generate_scenario(preset_graph(request.map), spec, seed=request.seed)
    time_model = scenario.time_model
    started = time.perf_counter()
    result = simulate(
        scenario,
        POLICIES[request.policy](),
        traffic=request.traffic,
        delays=_model(request.delays, time_model),
        delay_seed=request.seed,
        battery=BatterySpec() if request.charging else None,
        battery_seed=request.seed,
        charging=ChargingPolicy(request.charging) if request.charging else ChargingPolicy.WHEN_LOW,
        breakdowns=_model(request.breakdowns, time_model),
        breakdown_seed=request.seed,
        recovery=Recovery(request.recovery),
    )
    kpis = compute_kpis(result)
    charging = compute_charging_kpis(result) if result.battery else None
    return SimulationOutcome(
        kpis=_json_safe(asdict(kpis) | {"empty_share": kpis.empty_share}),
        charging=_json_safe(asdict(charging)) if charging else None,
        breakdowns=result.breakdowns,
        replans=result.replans,
        compute_s=time.perf_counter() - started,
    )
