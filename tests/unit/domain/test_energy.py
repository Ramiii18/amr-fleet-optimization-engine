"""Tests for the battery model (D26).

Hand map: a 2 m corridor W = E with 1 m dead-end spurs (all two-way)::

        A       B          storage
        |       |
  P1 -- W ===== E -- P2    parking
        |       |
        S1      S2         stations

The longest shortest route between two endpoints is 4 m (for example P1 -> W -> E -> P2), so a
job plus a drive to a charger needs at most 4 x 4 = 16 m of driving.
"""

import random

import pytest

from amr_optimizer.domain import UNITS_PER_METRE, BatterySpec, EnergyModel, TimeModel, safe_low_m
from amr_optimizer.warehouse import Edge, Node, NodeId, NodeKind, WarehouseGraph

A, B, W, E, S1, S2, P1, P2 = (NodeId(n) for n in ("A", "B", "W", "E", "S1", "S2", "P1", "P2"))


def _hand_map() -> WarehouseGraph:
    nodes = [
        Node(W, 0, 0),
        Node(E, 2, 0),
        Node(A, 0, 1, NodeKind.STORAGE),
        Node(B, 2, 1, NodeKind.STORAGE),
        Node(S1, 0, -1, NodeKind.STATION),
        Node(S2, 2, -1, NodeKind.STATION),
        Node(P1, -1, 0, NodeKind.PARKING),
        Node(P2, 3, 0, NodeKind.PARKING),
    ]
    links = [(W, E, 2.0), (W, A, 1.0), (W, S1, 1.0), (W, P1, 1.0)]
    links += [(E, B, 1.0), (E, S2, 1.0), (E, P2, 1.0)]
    edges = [Edge(u, v, length) for a, b, length in links for u, v in ((a, b), (b, a))]
    return WarehouseGraph(nodes, edges)


def _model(**spec: float) -> EnergyModel:
    return EnergyModel(BatterySpec(**spec), _hand_map(), TimeModel(0.1))


def test_the_default_battery_in_units() -> None:
    model = _model()

    assert model.capacity == 3600 * UNITS_PER_METRE  # one hour of driving at 1 m/s
    assert model.charge_per_tick == 300  # 3,600,000 units in 12,000 ticks (20 minutes)
    assert (model.low, model.high) == (900_000, 3_240_000)  # 25% and 90%


def test_edges_drain_their_length_in_millimetres() -> None:
    model = _model()

    assert model.edge_cost(W, E) == 2000
    assert model.edge_cost(E, P2) == 1000
    with pytest.raises(LookupError, match="no edge"):
        model.edge_cost(A, B)


def test_charging_is_linear_up_to_the_capacity() -> None:
    model = _model()

    assert model.charged(1000, 10) == 4000
    assert model.charged(model.capacity - 100, 10) == model.capacity
    assert model.charged(1000, -5) == 1000
    assert model.ticks_to_reach(1000, 1300) == 1
    assert model.ticks_to_reach(1000, 1301) == 2  # rounded up: a partial tick still takes a tick
    assert model.ticks_to_reach(5000, 4000) == 0
    assert model.ticks_to_reach(0, 10 * model.capacity) == 12_000  # never beyond full


def test_start_levels_are_seeded_and_within_bounds() -> None:
    model = _model()

    levels = model.start_levels(50, random.Random(3))

    assert levels == model.start_levels(50, random.Random(3))
    assert all(round(0.3 * model.capacity) <= e <= model.capacity for e in levels)


def test_the_safety_bound_comes_from_the_map() -> None:
    assert safe_low_m(_hand_map()) == 16.0
    with pytest.raises(ValueError, match="below what a job and a drive to a charger can need"):
        _model(capacity_m=40.0)  # low = 25% of 40 m = 10 m < 16 m
    assert _model(capacity_m=64.0).low == 16 * UNITS_PER_METRE  # exactly enough


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"capacity_m": 0.0}, "positive"),
        ({"full_charge_s": -1.0}, "positive"),
        ({"low_share": 0.9, "high_share": 0.5}, "thresholds"),
        ({"high_share": 1.2}, "thresholds"),
        ({"start_min_share": 0.8, "start_max_share": 0.5}, "start levels"),
    ],
)
def test_inconsistent_specs_are_rejected(spec: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        BatterySpec(**spec)
