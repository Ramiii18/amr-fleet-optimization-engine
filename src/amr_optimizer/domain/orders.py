"""Transport orders: move one load from a pickup location to a drop-off location."""

from dataclasses import dataclass
from typing import NewType

from amr_optimizer.warehouse import NodeId

OrderId = NewType("OrderId", str)
"""Identifier of a transport order, e.g. ``OrderId("O0001")``."""


class OrderValidationError(ValueError):
    """A transport order is malformed."""


@dataclass(frozen=True, slots=True)
class TransportOrder:
    """A request to carry one load from ``pickup`` to ``dropoff``.

    The order becomes known to the dispatcher at ``release_tick``; it cannot be assigned
    earlier. The order itself never changes: its progress is the phase of the robot that
    carries it (D13).
    """

    id: OrderId
    pickup: NodeId
    dropoff: NodeId
    release_tick: int

    def __post_init__(self) -> None:
        if not self.id:
            raise OrderValidationError("order id must be a non-empty string")
        if self.pickup == self.dropoff:
            raise OrderValidationError(
                f"order {self.id!r}: pickup and drop-off are both {self.pickup!r}"
            )
        if self.release_tick < 0:
            raise OrderValidationError(
                f"order {self.id!r}: release tick must be non-negative, got {self.release_tick}"
            )
