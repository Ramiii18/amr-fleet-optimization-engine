"""Is a map fit for the traffic planner? A check before the run, for any map (D39).

With traffic, a planning failure is ruled out by design, not by luck (D24): every endpoint is a
dead end, every reservation but the final holds is finite, and a robot's goals are endpoints nobody
else holds or plans to visit. The design rests on properties of the map and of the fleet size. On a
map without them, a run can stop halfway with a `TrafficPlanningError`. This module checks the
properties first and names what is wrong.

Rules (each `Violation` carries its rule's name):

- ``leaf-endpoints``: every endpoint (any node that is not an intersection) has exactly one
  neighbour, an intersection, joined in both directions. Otherwise a path could pass through a
  robot standing at an endpoint, or a robot could not leave it.
- ``strongly-connected``: every node can reach every other. Otherwise some order or holding spot
  cannot be reached from where a robot stands.
- ``storage-and-stations``: at least one storage location and one station, which generated orders
  need.
- ``holding-spots`` (with ``robots``): enough endpoints where a robot may wait (storage, parking,
  and chargers unless batteries reserve them) that one is always free. See `holding_spots_needed`.
- ``chargers`` and ``battery-threshold`` (with ``battery``): at least one charger, and a low
  threshold that covers a job and a drive to a charger on this map, as `EnergyModel` requires.

Checking reads the map and changes nothing. It costs O(V + E) for the first four rules, plus one
Dijkstra per endpoint for the battery rule.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from amr_optimizer.domain import UNITS_PER_METRE, BatterySpec
from amr_optimizer.domain.energy import farthest_endpoints
from amr_optimizer.routing import strongly_connected_components
from amr_optimizer.warehouse import MapValidationError, NodeId, NodeKind, WarehouseGraph

LISTED = 8
"""How many node ids a message lists before it says how many more there are."""


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken rule: its name, the nodes involved (possibly none) and a readable message."""

    rule: str
    nodes: tuple[NodeId, ...]
    message: str


@dataclass(frozen=True, slots=True)
class WellFormedReport:
    violations: tuple[Violation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations

    def describe(self) -> str:
        if self.ok:
            return "the map is fit for traffic"
        lines = [f"the map is not fit for traffic ({len(self.violations)} problem(s)):"]
        lines += [f"  - [{v.rule}] {v.message}" for v in self.violations]
        return "\n".join(lines)


class NotWellFormedError(MapValidationError):
    """A map breaks the rules the traffic planner relies on. ``report`` lists every violation."""

    def __init__(self, report: WellFormedReport) -> None:
        super().__init__(report.describe())
        self.report = report


def holding_spots_needed(robots: int, *, dropoffs_hold: bool = False) -> int:
    """Holding spots that guarantee a free one for any robot, at any moment (D39).

    A robot planning a job or a move needs one holding spot that nobody occupies from now on.
    Each other robot can block at most three at once: the spot it is still waiting on before it
    leaves (its start), the storage location it will load at, and the spot where its plan ends.
    When an order can end at a holding spot (put-away orders), that drop-off is a fourth. One more
    may be excluded for the robot itself (the spot it stands on when it is moved out of the way,
    or the drop-off its own plan avoids). So ``3N - 1`` spots, or ``4N - 2``, always leave one free.
    """
    if robots < 1:
        raise ValueError(f"robots must be at least 1, got {robots}")
    per_robot = 4 if dropoffs_hold else 3
    return per_robot * (robots - 1) + 2


def holding_kinds(*, batteries: bool) -> frozenset[NodeKind]:
    """Where an idle robot may wait: every endpoint but stations, and chargers only when
    batteries are off (with batteries, a charger is only for charging, D26)."""
    kinds = {NodeKind.STORAGE, NodeKind.PARKING}
    if not batteries:
        kinds.add(NodeKind.CHARGER)
    return frozenset(kinds)


def _listed(nodes: Sequence[NodeId]) -> str:
    shown = ", ".join(nodes[:LISTED])
    return shown if len(nodes) <= LISTED else f"{shown} and {len(nodes) - LISTED} more"


def _leaf_violations(graph: WarehouseGraph) -> list[Violation]:
    sources: dict[NodeId, set[NodeId]] = {node.id: set() for node in graph}
    for edge in graph.edges:
        sources[edge.target].add(edge.source)
    violations = []
    for node in graph:
        if node.kind is NodeKind.INTERSECTION:
            continue
        outgoing = {edge.target for edge in graph.out_edges(node.id)}
        neighbours = sorted(outgoing | sources[node.id])
        what = f"endpoint {node.id} ({node.kind.value})"
        problem = None
        if not neighbours:
            problem = "has no connection"
        elif len(neighbours) > 1:
            problem = f"has {len(neighbours)} neighbours ({_listed(neighbours)})"
        elif outgoing != sources[node.id]:
            way = "out" if not outgoing else "in"
            problem = f"is joined to {neighbours[0]} one way only: robots could not get {way}"
        elif graph.node(neighbours[0]).kind is not NodeKind.INTERSECTION:
            problem = f"is joined to another endpoint, {neighbours[0]}"
        if problem is not None:
            violations.append(
                Violation(
                    "leaf-endpoints",
                    (node.id, *neighbours),
                    f"{what} {problem}; an endpoint must be a dead end joined to one floor node "
                    "in both directions",
                )
            )
    return violations


def _connectivity_violation(graph: WarehouseGraph) -> Violation | None:
    components = strongly_connected_components(graph)
    if len(components) <= 1:
        return None
    largest = max(components, key=len)  # the first of the largest, in Tarjan's order
    outside = [node.id for node in graph if node.id not in set(largest)]
    return Violation(
        "strongly-connected",
        tuple(outside),
        f"the map splits into {len(components)} parts that robots cannot drive between in "
        f"both directions; {len(outside)} node(s) are outside the largest part "
        f"({len(largest)} nodes): {_listed(outside)}",
    )


def check_well_formed(
    graph: WarehouseGraph,
    *,
    robots: int | None = None,
    battery: BatterySpec | None = None,
    dropoffs_hold: bool = False,
) -> WellFormedReport:
    """Every rule ``graph`` breaks, for a fleet of ``robots`` (if given), with ``battery`` (if
    given), and with orders that may end at holding spots (``dropoffs_hold``). Pure: it reads the
    map and changes nothing."""
    violations = _leaf_violations(graph)
    connectivity = _connectivity_violation(graph)
    if connectivity is not None:
        violations.append(connectivity)
    missing = [
        kind.value for kind in (NodeKind.STORAGE, NodeKind.STATION) if not graph.nodes_of_kind(kind)
    ]
    if missing:
        violations.append(
            Violation(
                "storage-and-stations",
                (),
                f"the map has no {' and no '.join(missing)} node; orders need both",
            )
        )
    if robots is not None:
        kinds = holding_kinds(batteries=battery is not None)
        spots = sum(1 for node in graph if node.kind in kinds)
        needed = holding_spots_needed(robots, dropoffs_hold=dropoffs_hold)
        if spots < needed:
            names = ", ".join(sorted(kind.value for kind in kinds))
            violations.append(
                Violation(
                    "holding-spots",
                    (),
                    f"{robots} robots need at least {needed} holding spots ({names}) so that "
                    f"one is always free; the map has {spots}",
                )
            )
    if battery is not None:
        if not graph.nodes_of_kind(NodeKind.CHARGER):
            violations.append(Violation("chargers", (), "batteries need at least one charger"))
        longest, source, target = farthest_endpoints(graph)
        capacity = round(battery.capacity_m * UNITS_PER_METRE)
        low = round(battery.low_share * capacity)
        needed_units = round(4 * longest * UNITS_PER_METRE)
        if low < needed_units and source is not None and target is not None:
            violations.append(
                Violation(
                    "battery-threshold",
                    (source, target),
                    f"the low threshold ({low / UNITS_PER_METRE:.0f} m of driving) is below "
                    f"four of the longest shortest routes between endpoints "
                    f"({needed_units / UNITS_PER_METRE:.0f} m; the longest runs from {source} "
                    f"to {target}, {longest:.1f} m)",
                )
            )
    return WellFormedReport(tuple(violations))


def require_well_formed(
    graph: WarehouseGraph,
    *,
    robots: int | None = None,
    battery: BatterySpec | None = None,
    dropoffs_hold: bool = False,
) -> WellFormedReport:
    """`check_well_formed`, raising `NotWellFormedError` with every violation if any."""
    report = check_well_formed(graph, robots=robots, battery=battery, dropoffs_hold=dropoffs_hold)
    if not report.ok:
        raise NotWellFormedError(report)
    return report
