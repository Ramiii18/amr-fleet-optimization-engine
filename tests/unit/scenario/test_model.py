"""Tests for Scenario validation."""

from collections.abc import Callable

import pytest

from amr_optimizer.domain import OrderId, RobotId, RobotState, TimeModel, TransportOrder
from amr_optimizer.routing import Route
from amr_optimizer.scenario import Scenario, ScenarioValidationError
from amr_optimizer.warehouse import NodeId, WarehouseGraph

A, B, C, Z = NodeId("A"), NodeId("B"), NodeId("C"), NodeId("Z")
R1, R2 = RobotId("R1"), RobotId("R2")
O1 = TransportOrder(OrderId("O1"), A, C, 0)
O2 = TransportOrder(OrderId("O2"), C, A, 10)


def _scenario(graph: WarehouseGraph, **changes: object) -> Scenario:
    fields: dict[str, object] = {
        "graph": graph,
        "time_model": TimeModel(0.1),
        "speed_mps": 1.0,
        "load_ticks": 50,
        "unload_ticks": 50,
        "robots": (RobotState.idle(R1, A), RobotState.idle(R2, B)),
        "orders": (O1, O2),
    }
    fields.update(changes)
    return Scenario(**fields)  # type: ignore[arg-type]


def test_valid_scenario(line_graph: WarehouseGraph) -> None:
    scenario = _scenario(line_graph)

    assert len(scenario.robots) == 2
    assert scenario.orders == (O1, O2)


def test_scenario_without_orders_is_valid(line_graph: WarehouseGraph) -> None:
    assert _scenario(line_graph, orders=()).orders == ()


def _busy_robot() -> RobotState:
    return RobotState.idle(R1, A).assign(O2, Route((A, B, C), 2.0), tick=10, until_tick=30)


INVALID: list[tuple[str, Callable[[], dict[str, object]], str]] = [
    ("no robots", lambda: {"robots": ()}, "at least one robot"),
    (
        "duplicate robot ids",
        lambda: {"robots": (RobotState.idle(R1, A), RobotState.idle(R1, B))},
        "duplicate robot ids: \\['R1'\\]",
    ),
    (
        "shared start node",
        lambda: {"robots": (RobotState.idle(R1, A), RobotState.idle(R2, A))},
        "same node: \\['A'\\]",
    ),
    ("robot not idle", lambda: {"robots": (_busy_robot(),)}, "must start idle at tick 0"),
    (
        "robot idle since later",
        lambda: {"robots": (RobotState.idle(R1, A, tick=5),)},
        "must start idle at tick 0",
    ),
    ("robot off the map", lambda: {"robots": (RobotState.idle(R1, Z),)}, "unknown node 'Z'"),
    ("duplicate order ids", lambda: {"orders": (O1, O1)}, "duplicate order ids: \\['O1'\\]"),
    ("unsorted orders", lambda: {"orders": (O2, O1)}, "'O1' is out of order"),
    (
        "pickup off the map",
        lambda: {"orders": (TransportOrder(OrderId("O3"), Z, A, 0),)},
        "'O3' pickup: unknown node 'Z'",
    ),
    (
        "drop-off off the map",
        lambda: {"orders": (TransportOrder(OrderId("O3"), A, Z, 0),)},
        "'O3' drop-off: unknown node 'Z'",
    ),
    ("negative load time", lambda: {"load_ticks": -1}, "non-negative"),
    ("zero speed", lambda: {"speed_mps": 0.0}, "speed must be positive"),
]


@pytest.mark.parametrize(
    ("changes", "message"), [(c, m) for _, c, m in INVALID], ids=[n for n, _, _ in INVALID]
)
def test_inconsistent_scenarios_are_rejected(
    line_graph: WarehouseGraph, changes: Callable[[], dict[str, object]], message: str
) -> None:
    with pytest.raises(ScenarioValidationError, match=message):
        _scenario(line_graph, **changes())
