"""Static screening of candidate layouts: where should stations and chargers go? (D43)

A base layout marks candidate slots with ``?``. Screening chooses which slots become stations and
which become chargers, by free-flow distances alone, and writes fully assigned candidate layouts
for the simulation study to judge.

**Stations: a separable proxy.** Generated orders go from a uniformly random storage location to a
uniformly random station (`scenario.generator.generate_orders`). A robot then drives the order
loaded from storage to the station, and later empty from near the station to its next pickup. So a
station ``s`` costs, per order sent to it, about the mean round trip

    m(s) = mean over storage k of  d(k, s) + d(s, k)

(one-way aisles make the two directions differ), and a set of stations costs the mean of its
``m(s)``. That is **separable**: each station counts on its own, so the best set is simply the
slots with the smallest ``m``, and they tend to cluster where storage is densest. Congestion, which
clustering causes, is invisible to it; that interaction is what the simulation is for.

**Chargers: a p-median.** A robot goes to charge from wherever it is when it runs low, mostly a
holding spot near the station it just served, and afterwards starts its next job from the charger.
With the chosen stations as sources ``i`` and candidate slots ``j``, a charger trip costs
``c(i, j) = d(i, j) + mean over storage k of d(j, k)``, and each source is served by its best
charger. Choosing ``p`` chargers to minimise the total is the p-median problem, which is not
separable; it is solved exactly with CP-SAT (one worker, deterministic) and checked against brute
force in the tests.

**Slot roles.** By default any slot can take either role. ``station_slots`` and ``charger_slots``
restrict them, for a hall whose stations belong on a dock wall and whose chargers elsewhere.

**Parking is not chosen:** parking spots stay where the base layout draws them, and slots chosen
for nothing are blocked in the candidates.

**Candidates.** The ``top`` station sets by proxy (exact: every combination is enumerated, which is
cheap for the few slots of a hall), each with its p-median chargers; and a **spread** control, the
station set that maximises the smallest round trip between two of its stations, with its chargers.
All candidates share the base's storage cells and station and charger counts, so the lab can pair
them (D41).

Distances are shortest-path lengths in metres on the directed map (Dijkstra, one run per slot on
the map and one on the reversed map). Integer costs for CP-SAT are millimetres.
"""

import itertools
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ortools.sat.python import cp_model

from amr_optimizer.layout.compiler import cell_node_id, compile_layout
from amr_optimizer.layout.model import SLOT, Cell, Layout
from amr_optimizer.routing import shortest_path_lengths
from amr_optimizer.warehouse import Edge, NodeId, NodeKind, WarehouseGraph

MAX_COMBINATIONS = 200_000
"""Station sets are enumerated exhaustively; more combinations than this is refused."""


class ScreeningError(ValueError):
    """A base layout or a request that screening cannot handle."""


def _reversed(graph: WarehouseGraph) -> WarehouseGraph:
    return WarehouseGraph(graph.nodes, [Edge(e.target, e.source, e.length_m) for e in graph.edges])


@dataclass(frozen=True, slots=True)
class SlotCosts:
    """Free-flow distances between the slots and the storage locations of one base layout."""

    slots: tuple[Cell, ...]
    round_trip_m: Mapping[Cell, float]  # m(s): mean d(k, s) + d(s, k) over storage k
    to_storage_m: Mapping[Cell, float]  # mean d(s, k) over storage k
    between_m: Mapping[tuple[Cell, Cell], float]  # d(a, b) between slots


def slot_costs(base: Layout) -> SlotCosts:
    """Distances for every slot of ``base``. Slots are dead-end spurs, so making them all nodes
    (as parking, whose kind does not matter here) changes no other distance."""
    slots = base.slots()
    if not slots:
        raise ScreeningError("the base layout has no candidate slots (?)")
    graph = compile_layout(base.assign({cell: NodeKind.PARKING for cell in slots}))
    reverse = _reversed(graph)
    storage = [n.id for n in graph.nodes_of_kind(NodeKind.STORAGE)]
    if not storage:
        raise ScreeningError("the base layout has no storage")
    ids = {cell: cell_node_id(NodeKind.PARKING, cell) for cell in slots}
    round_trip, outbound, between = {}, {}, {}
    for cell in slots:
        forward = shortest_path_lengths(graph, ids[cell])
        backward = shortest_path_lengths(reverse, ids[cell])  # distances *to* the slot
        if any(k not in forward or k not in backward for k in storage):
            raise ScreeningError(f"slot {cell} cannot reach every storage location and back")
        outbound[cell] = sum(forward[k] for k in storage) / len(storage)
        round_trip[cell] = outbound[cell] + sum(backward[k] for k in storage) / len(storage)
        for other in slots:
            if other != cell:
                between[cell, other] = forward[ids[other]]
    return SlotCosts(slots, round_trip, outbound, between)


def p_median(costs: Sequence[Sequence[int]], p: int) -> tuple[tuple[int, ...], int]:
    """Choose ``p`` of the columns so that the sum over rows of each row's cheapest chosen column
    is least; exact (CP-SAT, one worker). Returns the chosen columns (ascending) and the total."""
    rows, columns = len(costs), len(costs[0]) if costs else 0
    if not 1 <= p <= columns or rows == 0:
        raise ScreeningError(f"cannot choose {p} of {columns} for {rows} sources")
    model = cp_model.CpModel()
    open_ = [model.new_bool_var(f"y{j}") for j in range(columns)]
    serve = [[model.new_bool_var(f"x{i}_{j}") for j in range(columns)] for i in range(rows)]
    model.add(sum(open_) == p)
    for i in range(rows):
        model.add_exactly_one(serve[i])
        for j in range(columns):
            model.add_implication(serve[i][j], open_[j])
    model.minimize(sum(costs[i][j] * serve[i][j] for i in range(rows) for j in range(columns)))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    if status != cp_model.OPTIMAL:
        raise ScreeningError(f"CP-SAT did not prove the p-median optimal (status {status})")
    chosen = tuple(j for j in range(columns) if solver.value(open_[j]))
    return chosen, round(solver.objective_value)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One assignment of slots: its name, stations, chargers and static proxies."""

    name: str
    stations: tuple[Cell, ...]
    chargers: tuple[Cell, ...]
    station_round_trip_m: float  # the station proxy: mean round trip per order
    charger_trip_m: float  # the charger proxy: mean charger trip per source
    smallest_station_gap_m: float  # the smallest round trip between two stations

    def layout(self, base: Layout) -> Layout:
        """``base`` with these stations and chargers; every other slot is blocked."""
        assigned = base.assign(
            {cell: NodeKind.STATION for cell in self.stations}
            | {cell: NodeKind.CHARGER for cell in self.chargers}
        )
        grid = tuple(row.replace(SLOT, "#") for row in assigned.grid)
        description = f"Synthetic candidate {self.name}, screened from: {base.description}"
        return Layout(grid, base.cell_m, description.strip())


def _gap(costs: SlotCosts, stations: Sequence[Cell]) -> float:
    pairs = itertools.combinations(stations, 2)
    return min((costs.between_m[a, b] + costs.between_m[b, a] for a, b in pairs), default=math.inf)


def _chargers(
    costs: SlotCosts, stations: Sequence[Cell], count: int, allowed: Sequence[Cell]
) -> tuple[tuple[Cell, ...], float]:
    if count == 0:
        return (), 0.0
    free = [cell for cell in allowed if cell not in stations]
    if len(free) < count:
        raise ScreeningError(f"{count} chargers do not fit the {len(free)} free charger slots")
    matrix = [
        [round((costs.between_m[i, j] + costs.to_storage_m[j]) * 1000) for j in free]
        for i in stations
    ]
    chosen, total = p_median(matrix, count)
    return tuple(free[j] for j in chosen), total / 1000 / len(stations)


def screen(
    base: Layout,
    *,
    stations: int,
    chargers: int,
    top: int,
    station_slots: Collection[Cell] | None = None,
    charger_slots: Collection[Cell] | None = None,
) -> list[Candidate]:
    """The ``top`` best station sets by the proxy (``proxy-1`` is the best), then the ``spread``
    control; each with its p-median chargers. Stations are chosen among ``station_slots`` and
    chargers among ``charger_slots`` (both default to every slot). Deterministic: ties go to the
    earlier slots."""
    if any(char in row for row in base.grid for char in "TC"):
        raise ScreeningError("the base layout must leave stations and chargers to its slots (?)")
    costs = slot_costs(base)
    for name, given in (("station", station_slots), ("charger", charger_slots)):
        if given is not None and not set(given) <= set(costs.slots):
            raise ScreeningError(f"some {name} slots are not candidate slots of the base")
    slots = tuple(c for c in costs.slots if station_slots is None or c in station_slots)
    for_chargers = tuple(c for c in costs.slots if charger_slots is None or c in charger_slots)
    if stations < 1 or chargers < 0 or top < 1 or stations + chargers > len(costs.slots):
        raise ScreeningError(
            f"{stations} stations and {chargers} chargers do not fit {len(costs.slots)} slots"
        )
    if stations > len(slots):
        raise ScreeningError(f"{stations} stations do not fit the {len(slots)} station slots")
    if math.comb(len(slots), stations) > MAX_COMBINATIONS:
        raise ScreeningError(f"too many station sets to enumerate ({len(slots)} slots)")
    sets = list(itertools.combinations(slots, stations))
    proxy = {s: sum(costs.round_trip_m[c] for c in s) / stations for s in sets}
    ranked = sorted(sets, key=lambda s: (round(proxy[s], 9), s))
    spread = max(
        sets,
        key=lambda s: (round(_gap(costs, s), 9), -round(proxy[s], 9), [-c for c in sum(s, ())]),
    )
    chosen = [(f"proxy-{rank + 1}", s) for rank, s in enumerate(ranked[:top])]
    if spread not in ranked[:top]:
        chosen.append(("spread", spread))
    candidates = []
    for name, station_set in chosen:
        charger_set, charger_trip = _chargers(costs, station_set, chargers, for_chargers)
        candidates.append(
            Candidate(
                name,
                station_set,
                charger_set,
                proxy[station_set],
                charger_trip,
                _gap(costs, station_set),
            )
        )
    return candidates


@dataclass(frozen=True, slots=True)
class Proxies:
    station_round_trip_m: float
    charger_trip_m: float


def proxies(layout: Layout) -> Proxies:
    """The two proxies of a fully assigned layout, computed on its own map (for any layout, such
    as a hand-made baseline): the mean station round trip, and the mean trip from each station to
    its best charger plus that charger's mean distance to storage."""
    graph = compile_layout(layout)
    reverse = _reversed(graph)
    storage = [n.id for n in graph.nodes_of_kind(NodeKind.STORAGE)]
    stations = [n.id for n in graph.nodes_of_kind(NodeKind.STATION)]
    chargers = [n.id for n in graph.nodes_of_kind(NodeKind.CHARGER)]
    if not storage or not stations:
        raise ScreeningError("the layout needs storage and stations")

    def mean_to_storage(node: NodeId) -> float:
        lengths = shortest_path_lengths(graph, node)
        return sum(lengths[k] for k in storage) / len(storage)

    round_trips = []
    for station in stations:
        backward = shortest_path_lengths(reverse, station)
        round_trips.append(
            mean_to_storage(station) + sum(backward[k] for k in storage) / len(storage)
        )
    charger_trip = 0.0
    if chargers:
        onward = {c: mean_to_storage(c) for c in chargers}
        trips = []
        for station in stations:
            lengths = shortest_path_lengths(graph, station)
            trips.append(min(lengths[c] + onward[c] for c in chargers))
        charger_trip = sum(trips) / len(trips)
    return Proxies(sum(round_trips) / len(round_trips), charger_trip)


def portable_path(path: str) -> str:
    """``path`` with forward slashes, whatever the operating system: an experiment file written on
    Windows must also run on Linux. Backslashes are read as separators on every OS."""
    return PureWindowsPath(path).as_posix()


def experiment_for(
    candidates: Mapping[str, str], baseline: str, template: Mapping[str, object]
) -> dict[str, object]:
    """An experiment file (version 2) comparing ``candidates`` (name to layout path) against the
    ``baseline`` layout path, with everything else from ``template``: families (fleet and load),
    policies, seeds, disturbances, battery, KPIs, margin. The baseline is named ``baseline``."""
    for field in ("layouts", "baseline", "format", "version"):
        if field in template:
            raise ScreeningError(f"the template must not set {field!r}: screening writes it")
    layouts = [{"name": "baseline", "map": portable_path(baseline)}]
    layouts += [{"name": name, "map": portable_path(path)} for name, path in candidates.items()]
    return {
        "format": "amr-experiment",
        "version": 2,
        **template,
        "layouts": layouts,
        "baseline": "baseline",
    }


def write_candidates(base: Layout, candidates: Sequence[Candidate], folder: Path) -> dict[str, str]:
    """Write each candidate as ``<folder>/<name>.layout.json``; returns name to file name."""
    from amr_optimizer.layout.io import save_layout

    folder.mkdir(parents=True, exist_ok=True)
    written = {}
    for candidate in candidates:
        path = folder / f"{candidate.name}.layout.json"
        save_layout(candidate.layout(base), path)
        written[candidate.name] = path.name
    return written
