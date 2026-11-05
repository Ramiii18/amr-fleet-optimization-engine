"""Batteries in integer energy units (D26, Phase 2).

One energy unit is the energy of driving one millimetre, so every edge drains a whole number of
units (its length in millimetres) and comparisons are exact, as integer ticks are for time (D6).
A robot standing idle at a charger gains a fixed number of units per tick, up to its capacity.

**The safety threshold.** Only robots above the low threshold receive jobs. That threshold must
cover the most energy a robot can need before it reaches a charger:
- drive to a pickup, to a drop-off and to a holding spot (one job);
- then drive to a charger.

`safe_low_m` bounds this from the map, as four times the longest shortest route between any two
endpoints. `EnergyModel` refuses a threshold below it. The bound assumes shortest drives: with
traffic, itineraries may detour and low robots may be moved out of the way, so the simulator also
checks every battery after every edge.
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from amr_optimizer.domain.timing import TimeModel
from amr_optimizer.routing import shortest_path_lengths
from amr_optimizer.warehouse import NodeId, NodeKind, WarehouseGraph

UNITS_PER_METRE = 1000
"""Energy units per metre driven: one unit is the energy of one millimetre."""


class ChargingPolicy(Enum):
    """When robots charge (D26, D27)."""

    WHEN_LOW = "when-low"
    """Only below the low threshold; out of service until the high threshold."""
    OPPORTUNISTIC = "opportunistic"
    """As when-low, and idle robots in service also top up when no order is waiting and a
    charger is free. They stay in service while they charge."""
    OPPORTUNISTIC_EARLY = "opportunistic-early"
    """As opportunistic, but a low robot is back in service as soon as it has charged to the
    low threshold, not the high one."""

    @property
    def tops_up(self) -> bool:
        """Whether idle robots in service top up."""
        return self is not ChargingPolicy.WHEN_LOW

    @property
    def back_at_low(self) -> bool:
        """Whether a low robot returns to service at the low threshold (else the high one)."""
        return self is ChargingPolicy.OPPORTUNISTIC_EARLY


@dataclass(frozen=True, slots=True)
class BatterySpec:
    """Battery and charging parameters, in metres of driving and seconds (D26).

    The defaults are synthetic, chosen so that charging matters within a one-hour run.
    """

    capacity_m: float = 3600.0  # driving on a full battery: one hour at 1 m/s
    full_charge_s: float = 1200.0  # from empty to full: 20 minutes
    low_share: float = 0.25  # below this, a robot goes to charge and takes no new jobs
    high_share: float = 0.90  # a robot charging when low is back in service at this level
    start_min_share: float = 0.30
    start_max_share: float = 1.0

    def __post_init__(self) -> None:
        if not (self.capacity_m > 0 and self.full_charge_s > 0):
            raise ValueError("battery capacity and full-charge time must be positive")
        if not 0 < self.low_share < self.high_share <= 1:
            raise ValueError(
                f"thresholds need 0 < low < high <= 1, got {self.low_share}, {self.high_share}"
            )
        if not 0 <= self.start_min_share <= self.start_max_share <= 1:
            raise ValueError("start levels need 0 <= min <= max <= 1")


class EnergyModel:
    """A `BatterySpec` in integer units on one map and time model."""

    __slots__ = ("capacity", "charge_per_tick", "high", "low", "spec", "_graph", "_start")

    def __init__(self, spec: BatterySpec, graph: WarehouseGraph, time_model: TimeModel) -> None:
        self.spec = spec
        self._graph = graph
        self.capacity = round(spec.capacity_m * UNITS_PER_METRE)
        full_ticks = time_model.to_ticks(spec.full_charge_s)
        self.charge_per_tick = max(1, self.capacity // full_ticks)
        self.low = round(spec.low_share * self.capacity)
        self.high = round(spec.high_share * self.capacity)
        self._start = (
            round(spec.start_min_share * self.capacity),
            round(spec.start_max_share * self.capacity),
        )
        needed = round(safe_low_m(graph) * UNITS_PER_METRE)
        if self.low < needed:
            raise ValueError(
                f"the low threshold ({self.low / UNITS_PER_METRE:.0f} m of driving) is below "
                f"what a job and a drive to a charger can need on this map "
                f"({needed / UNITS_PER_METRE:.0f} m)"
            )

    def edge_cost(self, source: NodeId, target: NodeId) -> int:
        """The energy of driving the edge from ``source`` to ``target``."""
        edge = self._graph.get_edge(source, target)
        if edge is None:
            raise LookupError(f"no edge {source!r} -> {target!r}")
        return round(edge.length_m * UNITS_PER_METRE)

    def charged(self, energy: int, ticks: int) -> int:
        """The level after charging for ``ticks`` from ``energy``, up to the capacity."""
        return min(self.capacity, energy + max(0, ticks) * self.charge_per_tick)

    def ticks_to_reach(self, energy: int, target: int) -> int:
        """How long charging from ``energy`` takes to reach ``target`` (0 if already there)."""
        if energy >= target:
            return 0
        return math.ceil((min(target, self.capacity) - energy) / self.charge_per_tick)

    def start_levels(self, count: int, rng: random.Random) -> list[int]:
        """Seeded start levels, uniform between the spec's bounds."""
        low, high = self._start
        return [rng.randint(low, high) for _ in range(count)]


def safe_low_m(graph: WarehouseGraph) -> float:
    """An upper bound, in metres, on the driving a robot can need before reaching a charger,
    if it drives shortest routes.

    A job is three drives between endpoints (to the pickup, to the drop-off, to a holding spot),
    and the drive to a charger is a fourth, each at most the longest shortest route between two
    endpoints.
    """
    endpoints: Sequence[NodeId] = [n.id for n in graph.nodes if n.kind is not NodeKind.INTERSECTION]
    longest = 0.0
    for source in endpoints:
        lengths = shortest_path_lengths(graph, source)
        longest = max(longest, max(lengths.get(t, 0.0) for t in endpoints))
    return 4 * longest
