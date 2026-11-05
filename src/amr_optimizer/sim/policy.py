"""The interface between the simulator and a dispatch policy.

A policy is a pure decision function: it receives a `DispatchSnapshot` and returns
assignments. It owns no time and changes no state; the simulator validates the decision
and executes it (see ARCHITECTURE.md, "Planners are pure functions").
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from amr_optimizer.domain import OrderId, RobotId, RobotState, TransportOrder
from amr_optimizer.sim.planner import TravelPlanner
from amr_optimizer.warehouse import NodeId


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
    holds the released orders that can be assigned now, in release order: without traffic,
    every order that no robot has taken yet; with traffic, only those whose pickup is not
    reserved by another robot (D24). ``planner`` answers route and travel-time questions; it
    caches answers, which does not change them. ``load_ticks`` and ``unload_ticks`` are the
    scenario's handling times, so a policy can predict when a busy robot will finish (D18).
    ``planned_ready`` gives, with traffic, the tick and node at which each busy robot will
    become idle, as planned; it is empty without traffic. ``unavailable`` lists idle robots
    that may not take a job now, because their battery is low (D26); ``idle_robots`` leaves
    them out, and ``planned_ready`` says when and where they will be back in service.
    """

    tick: int
    robots: tuple[RobotState, ...]
    pending_orders: tuple[TransportOrder, ...]
    planner: TravelPlanner
    load_ticks: int
    unload_ticks: int
    planned_ready: Mapping[RobotId, tuple[int, NodeId]] = MappingProxyType({})
    unavailable: frozenset[RobotId] = frozenset()

    def __post_init__(self) -> None:
        if self.load_ticks < 0 or self.unload_ticks < 0:
            raise ValueError(
                f"handling times must be non-negative, got load {self.load_ticks}, "
                f"unload {self.unload_ticks}"
            )

    @property
    def idle_robots(self) -> tuple[RobotState, ...]:
        """The robots that can take a job now: idle, and not out of service."""
        return tuple(
            robot
            for robot in self.robots
            if robot.is_idle and robot.robot_id not in self.unavailable
        )


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
