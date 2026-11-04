"""A scenario: everything one simulation run needs, validated as a whole."""

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from amr_optimizer.domain import RobotState, TimeModel, TransportOrder
from amr_optimizer.warehouse import NodeId, WarehouseGraph


class ScenarioValidationError(ValueError):
    """A scenario whose parts do not fit together."""


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


@dataclass(frozen=True, slots=True)
class Scenario:
    """The complete input of one simulation run.

    ``robots`` are the initial states: every robot is idle at tick 0, on its own node.
    ``orders`` are sorted by release tick. Loading and unloading take a fixed number of
    ticks; travel times follow from ``speed_mps`` and ``time_model`` (D12).
    """

    graph: WarehouseGraph
    time_model: TimeModel
    speed_mps: float
    load_ticks: int
    unload_ticks: int
    robots: tuple[RobotState, ...]
    orders: tuple[TransportOrder, ...]

    def __post_init__(self) -> None:
        if not (math.isfinite(self.speed_mps) and self.speed_mps > 0):
            raise ScenarioValidationError(f"speed must be positive, got {self.speed_mps!r}")
        if self.load_ticks < 0 or self.unload_ticks < 0:
            raise ScenarioValidationError("loading and unloading ticks must be non-negative")
        self._check_robots()
        self._check_orders()

    def _check_robots(self) -> None:
        if not self.robots:
            raise ScenarioValidationError("a scenario needs at least one robot")
        if duplicated := _duplicates(robot.robot_id for robot in self.robots):
            raise ScenarioValidationError(f"duplicate robot ids: {duplicated}")
        if shared := _duplicates(robot.node for robot in self.robots):
            raise ScenarioValidationError(f"several robots start on the same node: {shared}")
        for robot in self.robots:
            if not robot.is_idle or robot.since_tick != 0:
                raise ScenarioValidationError(f"robot {robot.robot_id!r} must start idle at tick 0")
            self._check_node(robot.node, f"robot {robot.robot_id!r}")

    def _check_orders(self) -> None:
        if duplicated := _duplicates(order.id for order in self.orders):
            raise ScenarioValidationError(f"duplicate order ids: {duplicated}")
        previous_release = 0
        for order in self.orders:
            if order.release_tick < previous_release:
                raise ScenarioValidationError(
                    f"orders must be sorted by release tick; {order.id!r} is out of order"
                )
            previous_release = order.release_tick
            self._check_node(order.pickup, f"order {order.id!r} pickup")
            self._check_node(order.dropoff, f"order {order.id!r} drop-off")

    def _check_node(self, node: NodeId, owner: str) -> None:
        if node not in self.graph:
            raise ScenarioValidationError(f"{owner}: unknown node {node!r}")
