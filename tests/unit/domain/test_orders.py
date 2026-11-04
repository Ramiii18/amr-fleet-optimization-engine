"""Tests for TransportOrder."""

import dataclasses

import pytest

from amr_optimizer.domain import OrderId, OrderValidationError, TransportOrder
from amr_optimizer.warehouse import NodeId

P, D = NodeId("P"), NodeId("D")


def test_valid_order() -> None:
    order = TransportOrder(OrderId("O1"), pickup=P, dropoff=D, release_tick=0)

    assert (order.pickup, order.dropoff, order.release_tick) == (P, D, 0)


def test_order_is_immutable() -> None:
    order = TransportOrder(OrderId("O1"), P, D, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        order.release_tick = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("order_id", "pickup", "dropoff", "release_tick", "message"),
    [
        ("", P, D, 0, "non-empty"),
        ("O1", P, P, 0, "pickup and drop-off are both 'P'"),
        ("O1", P, D, -1, "release tick must be non-negative"),
    ],
)
def test_malformed_orders_are_rejected(
    order_id: str, pickup: NodeId, dropoff: NodeId, release_tick: int, message: str
) -> None:
    with pytest.raises(OrderValidationError, match=message):
        TransportOrder(OrderId(order_id), pickup, dropoff, release_tick)
