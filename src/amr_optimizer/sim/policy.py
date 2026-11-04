"""The interface between the simulator and a dispatch policy.

A policy is a pure decision function: it receives a `DispatchSnapshot` and returns
assignments. It owns no time and changes no state; the simulator validates the decision
and executes it (see ARCHITECTURE.md, "Planners are pure functions").
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from amr_optimizer.domain import OrderId, RobotId, RobotState, TransportOrder
from amr_optimizer.sim.planner import TravelPlanner


class InvalidAssignmentError(ValueError):
    """A policy returned an assignment the simulator cannot execute."""


@dataclass(frozen=True, slots=True)
class Assignment:
    """Give order ``order_id`` to robot ``robot_id``, starting now."""

    robot_id: RobotId
    order_id: OrderId


@dataclass(frozen=True, slots=True)
class DispatchSnapshot:
    """What a policy may look at when it decides.

    ``robots`` holds every robot's current state, in scenario order. ``pending_orders``
    holds the released orders that no robot has taken yet, in release order. ``planner``
    answers route and travel-time questions; it caches answers, which does not change them.
    ``load_ticks`` and ``unload_ticks`` are the scenario's handling times, so a policy can
    predict when a busy robot will finish (D18).
    """

    tick: int
    robots: tuple[RobotState, ...]
    pending_orders: tuple[TransportOrder, ...]
    planner: TravelPlanner
    load_ticks: int
    unload_ticks: int

    def __post_init__(self) -> None:
        if self.load_ticks < 0 or self.unload_ticks < 0:
            raise ValueError(
                f"handling times must be non-negative, got load {self.load_ticks}, "
                f"unload {self.unload_ticks}"
            )

    @property
    def idle_robots(self) -> tuple[RobotState, ...]:
        return tuple(robot for robot in self.robots if robot.is_idle)


class DispatchPolicy(Protocol):
    """Anything with a ``name`` and an ``assign`` method of this shape is a policy."""

    @property
    def name(self) -> str: ...

    def assign(self, snapshot: DispatchSnapshot) -> Sequence[Assignment]:
        """Assignments to start at ``snapshot.tick``.

        Each robot must be idle and each order pending, and neither may appear twice. A
        policy may leave robots idle and orders waiting.
        """
        ...
