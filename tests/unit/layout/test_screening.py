"""Static screening of candidate layouts (D43): CP-SAT against brute force, separability, and
the spread control."""

import itertools
import json
import random
from pathlib import Path

import networkx as nx
import pytest

from amr_optimizer.domain import BatterySpec
from amr_optimizer.lab import experiment_from_dict
from amr_optimizer.lab.runner import pairing_key
from amr_optimizer.layout import (
    Layout,
    cell_node_id,
    compile_layout,
    load_layout,
    require_well_formed,
)
from amr_optimizer.layout.__main__ import main as layout_command
from amr_optimizer.layout.screening import (
    ScreeningError,
    experiment_for,
    p_median,
    proxies,
    screen,
    slot_costs,
    write_candidates,
)
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.warehouse import NodeKind

ROOT = Path(__file__).resolve().parents[3]
HALL = load_layout(ROOT / "layouts" / "examples" / "small_hall.layout.json")
# The small hall with its stations and chargers turned into slots: 12 slots.
BASE = Layout(
    tuple(row.replace("T", "?").replace("C", "?") for row in HALL.grid), 1.0, "Synthetic base"
)


def brute_force(costs: list[list[int]], p: int) -> int:
    return min(
        sum(min(row[j] for j in chosen) for row in costs)
        for chosen in itertools.combinations(range(len(costs[0])), p)
    )


@pytest.mark.parametrize("seed", range(220))
def test_cp_sat_finds_the_brute_force_optimum(seed: int) -> None:
    rng = random.Random(seed)
    sources, columns = rng.randint(1, 6), rng.randint(2, 8)
    p = rng.randint(1, min(3, columns))
    costs = [[rng.randint(0, 50) for _ in range(columns)] for _ in range(sources)]
    chosen, total = p_median(costs, p)
    assert len(chosen) == p == len(set(chosen))
    assert total == sum(min(row[j] for j in chosen) for row in costs)
    assert total == brute_force(costs, p)


def test_a_p_median_that_cannot_be_formed_is_refused() -> None:
    with pytest.raises(ScreeningError):
        p_median([[1, 2]], 3)
    with pytest.raises(ScreeningError):
        p_median([], 1)


def test_slot_distances_agree_with_networkx() -> None:
    costs = slot_costs(BASE)
    assert len(costs.slots) == 12
    graph = compile_layout(BASE.assign({cell: NodeKind.PARKING for cell in costs.slots}))
    digraph: nx.DiGraph[str] = nx.DiGraph()
    digraph.add_weighted_edges_from((e.source, e.target, e.length_m) for e in graph.edges)
    storage = [n.id for n in graph.nodes_of_kind(NodeKind.STORAGE)]
    for cell in costs.slots[:4]:
        node = cell_node_id(NodeKind.PARKING, cell)
        out = nx.single_source_dijkstra_path_length(digraph, node)
        back = nx.single_source_dijkstra_path_length(digraph.reverse(), node)
        expected = sum(out[k] + back[k] for k in storage) / len(storage)
        assert costs.round_trip_m[cell] == pytest.approx(expected)
        assert costs.to_storage_m[cell] == pytest.approx(
            sum(out[k] for k in storage) / len(storage)
        )


def test_the_proxy_is_separable_and_the_best_set_is_the_nearest_slots() -> None:
    costs = slot_costs(BASE)
    candidates = screen(BASE, stations=3, chargers=2, top=4)
    best = candidates[0]
    assert best.name == "proxy-1"
    nearest = sorted(costs.slots, key=lambda c: (costs.round_trip_m[c], c))[:3]
    assert sorted(best.stations) == sorted(nearest)
    proxies_in_order = [c.station_round_trip_m for c in candidates[:4]]
    assert proxies_in_order == sorted(proxies_in_order)
    every = [
        sum(costs.round_trip_m[c] for c in s) / 3 for s in itertools.combinations(costs.slots, 3)
    ]
    assert best.station_round_trip_m == pytest.approx(min(every))


def test_the_spread_control_maximises_the_smallest_gap_between_stations() -> None:
    costs = slot_costs(BASE)
    candidates = screen(BASE, stations=3, chargers=2, top=4)
    spread = candidates[-1]
    assert spread.name == "spread"
    gaps = [
        min(costs.between_m[a, b] + costs.between_m[b, a] for a, b in itertools.combinations(s, 2))
        for s in itertools.combinations(costs.slots, 3)
    ]
    assert spread.smallest_station_gap_m == pytest.approx(max(gaps))
    assert spread.smallest_station_gap_m > candidates[0].smallest_station_gap_m


def test_chargers_are_the_exact_p_median_of_each_station_set() -> None:
    costs = slot_costs(BASE)
    for candidate in screen(BASE, stations=3, chargers=2, top=4):
        free = [c for c in costs.slots if c not in candidate.stations]
        matrix = [
            [round((costs.between_m[i, j] + costs.to_storage_m[j]) * 1000) for j in free]
            for i in candidate.stations
        ]
        assert candidate.charger_trip_m == pytest.approx(brute_force(matrix, 2) / 1000 / 3)
        assert not set(candidate.chargers) & set(candidate.stations)


def test_candidates_are_complete_well_formed_and_can_be_paired() -> None:
    candidates = screen(BASE, stations=3, chargers=2, top=4)
    assert len({c.stations for c in candidates}) == len(candidates)
    spec = ScenarioSpec(robots=4, orders_per_hour=200.0, duration_s=600.0)
    keys = set()
    for candidate in candidates:
        layout = candidate.layout(BASE)
        assert not any("?" in row for row in layout.grid)
        assert layout.description.startswith("Synthetic candidate")
        graph = compile_layout(layout)
        require_well_formed(graph, robots=4, battery=BatterySpec())
        assert len(graph.nodes_of_kind(NodeKind.STATION)) == 3
        assert len(graph.nodes_of_kind(NodeKind.CHARGER)) == 2
        measured = proxies(layout)  # on the candidate's own map: the same numbers
        assert measured.station_round_trip_m == pytest.approx(candidate.station_round_trip_m)
        assert measured.charger_trip_m == pytest.approx(candidate.charger_trip_m, abs=1e-3)
        keys.add(pairing_key(generate_scenario(graph, spec, seed=3), {"seed": 3}))
    assert len(keys) == 1, "same storage, same counts: the lab can pair every candidate"


def test_screening_is_deterministic_to_the_byte(tmp_path: Path) -> None:
    first = screen(BASE, stations=3, chargers=2, top=4)
    assert first == screen(BASE, stations=3, chargers=2, top=4)
    write_candidates(BASE, first, tmp_path / "a")
    write_candidates(BASE, first, tmp_path / "b")
    for path in (tmp_path / "a").iterdir():
        assert path.read_bytes() == (tmp_path / "b" / path.name).read_bytes()


@pytest.mark.parametrize(
    ("base", "counts", "message"),
    [
        (HALL, {"stations": 3, "chargers": 2}, "leave stations and chargers to its slots"),
        (BASE, {"stations": 11, "chargers": 2}, "do not fit 12 slots"),
        (BASE, {"stations": 0, "chargers": 2}, "do not fit"),
        (
            Layout(tuple(r.replace("?", "#") for r in BASE.grid), 1.0),
            {"stations": 1, "chargers": 0},
            "no candidate slots",
        ),
    ],
)
def test_impossible_screening_is_refused(
    base: Layout, counts: dict[str, int], message: str
) -> None:
    with pytest.raises(ScreeningError, match=message):
        screen(base, top=2, **counts)


def test_the_command_writes_candidates_proxies_and_an_experiment(tmp_path: Path) -> None:
    from amr_optimizer.layout import save_layout

    save_layout(BASE, tmp_path / "base.layout.json")
    baseline = BASE.assign(
        {(12, 2): NodeKind.STATION, (12, 6): NodeKind.STATION, (12, 9): NodeKind.STATION}
        | {(1, 2): NodeKind.CHARGER, (1, 8): NodeKind.CHARGER}
    )
    save_layout(
        Layout(tuple(r.replace("?", "#") for r in baseline.grid), 1.0, "Synthetic hand-made"),
        tmp_path / "hand.layout.json",
    )
    template = {
        "name": "stations",
        "families": [
            {"name": "hall-4", "robots": 4, "orders_per_hour": 150.0, "duration_s": 300.0}
        ],
        "policies": ["greedy"],
        "seeds": [0, 1],
        "disturbances": {"model": "none"},
        "margin": 5.0,
        "resamples": 200,
    }
    (tmp_path / "template.json").write_text(json.dumps(template), encoding="utf-8")
    out = tmp_path / "candidates"
    run = [
        "screen",
        str(tmp_path / "base.layout.json"),
        "--stations",
        "3",
        "--chargers",
        "2",
        "--top",
        "2",
        "--out",
        str(out),
        "--baseline",
        "../hand.layout.json",
        "--template",
        str(tmp_path / "template.json"),
    ]
    assert layout_command(run) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "experiment.json",
        "proxy-1.layout.json",
        "proxy-2.layout.json",
        "screening.json",
        "spread.layout.json",
    ]
    table = json.loads((out / "screening.json").read_text(encoding="utf-8"))
    assert [row["name"] for row in table["candidates"]] == [
        "baseline",
        "proxy-1",
        "proxy-2",
        "spread",
    ]
    experiment = experiment_from_dict(
        json.loads((out / "experiment.json").read_text(encoding="utf-8")), base_dir=out
    )
    assert experiment.baseline == "baseline"
    assert [layout.name for layout in experiment.layouts] == [
        "baseline",
        "proxy-1",
        "proxy-2",
        "spread",
    ]


def test_the_template_may_not_set_what_screening_writes() -> None:
    with pytest.raises(ScreeningError, match="'layouts'"):
        experiment_for({"a": "a.layout.json"}, "b.layout.json", {"layouts": []})
