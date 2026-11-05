"""Key performance indicators of one simulation run, computed from its event log and trajectories.

Definitions (all times in seconds of simulated time):

- **waiting time** of an order: from its release until a robot reaches its pickup;
- **assignment delay**: from release until a robot is assigned (shows the cost of rounds);
- **completion time** (lead time): from release until delivery; mean, P95 and maximum;
- **throughput**: delivered orders per hour, over the run from tick 0 to the last event;
- **utilization**: the share of robot time spent between assignment and delivery;
- **empty / loaded distance**: metres driven to pickups / with a load;
- **conflicts**: pairs of robots in the same node or corridor at the same time, found by the
  independent checker in the recorded trajectories (D23). Zero with traffic; in free flow it
  counts the collisions that traffic management has to prevent.

Times are reported over delivered orders; undelivered orders are counted separately and
never silently dropped. Percentiles use the nearest-rank method (`percentile`).
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from amr_optimizer.domain import OrderId
from amr_optimizer.sim import EventKind, SimulationResult
from amr_optimizer.traffic import path_conflicts

SECONDS_PER_HOUR = 3600.0


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank ``q``-th percentile: the smallest value with at least q% of values <= it.

    Unlike interpolating definitions, the result is always one of the observed values.
    """
    if not values:
        raise ValueError("percentile of no values")
    if not 0 < q <= 100:
        raise ValueError(f"q must be in (0, 100], got {q}")
    ordered = sorted(values)
    return ordered[math.ceil(q / 100 * len(ordered)) - 1]


@dataclass(frozen=True, slots=True)
class RunKpis:
    """The KPIs of one run. Time KPIs are ``nan`` if no order was delivered."""

    policy: str
    robots: int
    orders: int
    delivered: int
    undelivered: int
    duration_s: float  # from tick 0 to the last event
    throughput_per_h: float
    waiting_mean_s: float
    waiting_p95_s: float
    assignment_delay_mean_s: float
    completion_mean_s: float
    completion_p95_s: float
    completion_max_s: float  # the worst case: shows starvation that a mean and P95 can hide
    utilization: float
    empty_distance_m: float
    loaded_distance_m: float
    conflicts: int

    @property
    def empty_share(self) -> float:
        """Share of all driving that was empty travel to pickups: what dispatch can reduce."""
        total = self.empty_distance_m + self.loaded_distance_m
        return self.empty_distance_m / total if total else math.nan


def compute_kpis(result: SimulationResult) -> RunKpis:
    """KPIs of ``result``, from its event log."""
    scenario = result.scenario
    tick_s = scenario.time_model.tick_s
    first: dict[tuple[OrderId, EventKind], int] = {}
    empty_m = loaded_m = 0.0
    for entry in result.log:
        first.setdefault((entry.order_id, entry.kind), entry.tick)
        if entry.kind is EventKind.ASSIGNED:
            empty_m += entry.distance_m
        elif entry.kind is EventKind.LOADED:
            loaded_m += entry.distance_m

    def since_release(order_id: OrderId, kind: EventKind) -> float:
        return (first[(order_id, kind)] - first[(order_id, EventKind.RELEASED)]) * tick_s

    delivered = result.delivered
    waiting = [since_release(o, EventKind.PICKUP_REACHED) for o in delivered]
    delay = [since_release(o, EventKind.ASSIGNED) for o in delivered]
    completion = [since_release(o, EventKind.DELIVERED) for o in delivered]

    busy_ticks = sum(
        first.get((o, EventKind.DELIVERED), result.end_tick) - tick
        for (o, kind), tick in first.items()
        if kind is EventKind.ASSIGNED
    )
    robots = len(scenario.robots)
    duration_s = result.end_tick * tick_s

    def mean(values: list[float]) -> float:
        return statistics.fmean(values) if values else math.nan

    def p95(values: list[float]) -> float:
        return percentile(values, 95) if values else math.nan

    return RunKpis(
        policy=result.policy_name,
        robots=robots,
        orders=len(scenario.orders),
        delivered=len(delivered),
        undelivered=len(scenario.orders) - len(delivered),
        duration_s=duration_s,
        throughput_per_h=len(delivered) / duration_s * SECONDS_PER_HOUR if duration_s else math.nan,
        waiting_mean_s=mean(waiting),
        waiting_p95_s=p95(waiting),
        assignment_delay_mean_s=mean(delay),
        completion_mean_s=mean(completion),
        completion_p95_s=p95(completion),
        completion_max_s=max(completion) if completion else math.nan,
        utilization=busy_ticks / (robots * result.end_tick) if result.end_tick else math.nan,
        empty_distance_m=empty_m,
        loaded_distance_m=loaded_m,
        conflicts=len(path_conflicts({r: [p] for r, p in result.trajectories.items()})),
    )
