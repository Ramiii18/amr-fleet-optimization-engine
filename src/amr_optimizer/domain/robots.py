"""Robots and their state machine: what a robot is doing, where, and until which tick.

One transport job takes a robot through these phases (D13):

    IDLE --assign--> TO_PICKUP --arrive--> LOADING --finish_loading--> TO_DROPOFF
      |                                       ^                            |
      +--assign, robot already at pickup------+                         arrive
      ^                                                                    v
      +-------------------------finish_unloading-------------------- UNLOADING

`ALLOWED_TRANSITIONS` lists exactly these phase changes. A `RobotState` never changes:
every transition method returns a new state and refuses anything outside the table.
The constructor checks that the state's fields agree with its phase, so a state that
breaks an invariant (an idle robot holding an order, a loading robot away from the
pickup) cannot exist.

Each non-idle phase has a known end, ``until_tick``. A transition out of it must happen
at exactly that tick, so a simulator that schedules an event at the wrong time fails
loudly instead of producing quietly wrong KPIs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import NewType, Self

from amr_optimizer.domain.orders import TransportOrder
from amr_optimizer.routing import Route
from amr_optimizer.warehouse import NodeId

RobotId = NewType("RobotId", str)
"""Identifier of a robot, e.g. ``RobotId("R01")``."""


class RobotStateError(ValueError):
    """A robot state whose fields contradict each other or its phase."""


class InvalidTransitionError(RobotStateError):
    """A phase change that the robot state machine does not allow."""


class RobotPhase(Enum):
    """What a robot is doing. Exactly one phase at any tick."""

    IDLE = "idle"  # no order; standing at ``node``
    TO_PICKUP = "to_pickup"  # driving ``route`` to the order's pickup
    LOADING = "loading"  # at the pickup, taking the load on
    TO_DROPOFF = "to_dropoff"  # driving ``route`` to the order's drop-off, loaded
    UNLOADING = "unloading"  # at the drop-off, handing the load over

    @property
    def is_travelling(self) -> bool:
        return self is RobotPhase.TO_PICKUP or self is RobotPhase.TO_DROPOFF


ALLOWED_TRANSITIONS: Mapping[RobotPhase, frozenset[RobotPhase]] = MappingProxyType(
    {
        RobotPhase.IDLE: frozenset({RobotPhase.TO_PICKUP, RobotPhase.LOADING}),
        RobotPhase.TO_PICKUP: frozenset({RobotPhase.LOADING}),
        RobotPhase.LOADING: frozenset({RobotPhase.TO_DROPOFF}),
        RobotPhase.TO_DROPOFF: frozenset({RobotPhase.UNLOADING}),
        RobotPhase.UNLOADING: frozenset({RobotPhase.IDLE}),
    }
)
"""Every phase change the state machine allows, from each phase."""


@dataclass(frozen=True, slots=True)
class RobotState:
    """Snapshot of one robot: its phase, position, order and the tick its phase ends.

    ``node`` is where the robot is, or, while travelling, the node it departed from
    (``route.source``). ``since_tick`` is when the current phase began. ``until_tick`` is
    when it ends; it is ``None`` only while idle, which has no planned end.
    """

    robot_id: RobotId
    phase: RobotPhase
    node: NodeId
    since_tick: int
    until_tick: int | None = None
    order: TransportOrder | None = None
    route: Route | None = None

    def __post_init__(self) -> None:
        name = repr(self.robot_id)
        if not self.robot_id:
            raise RobotStateError("robot id must be a non-empty string")
        if self.since_tick < 0:
            raise RobotStateError(f"robot {name}: since_tick must be non-negative")

        if self.phase is RobotPhase.IDLE:
            if self.order is not None or self.route is not None or self.until_tick is not None:
                raise RobotStateError(f"robot {name}: an idle robot has no order, route or end")
            return

        phase = self.phase.name
        if self.order is None or self.until_tick is None:
            raise RobotStateError(f"robot {name}: {phase} needs an order and an end tick")
        if self.until_tick < self.since_tick:
            raise RobotStateError(f"robot {name}: {phase} cannot end before it begins")

        if not self.phase.is_travelling:
            if self.route is not None:
                raise RobotStateError(f"robot {name}: only a travelling robot has a route")
            expected = self.order.pickup if self.phase is RobotPhase.LOADING else self.order.dropoff
            if self.node != expected:
                raise RobotStateError(f"robot {name}: {phase} must happen at {expected!r}")
            return

        target = self.order.pickup if self.phase is RobotPhase.TO_PICKUP else self.order.dropoff
        if self.route is None or len(self.route.nodes) < 2:
            raise RobotStateError(f"robot {name}: {phase} needs a route of at least one edge")
        if self.route.source != self.node:
            raise RobotStateError(f"robot {name}: route must start at {self.node!r}")
        if self.route.target != target:
            raise RobotStateError(f"robot {name}: {phase} route must end at {target!r}")
        if self.until_tick == self.since_tick:
            raise RobotStateError(f"robot {name}: travel takes at least one tick")

    @classmethod
    def idle(cls, robot_id: RobotId, node: NodeId, *, tick: int = 0) -> Self:
        """An idle robot standing at ``node`` from ``tick`` on."""
        return cls(robot_id, RobotPhase.IDLE, node, tick)

    @property
    def is_idle(self) -> bool:
        return self.phase is RobotPhase.IDLE

    # --- transitions ----------------------------------------------------------------------

    def assign(
        self, order: TransportOrder, route: Route, *, tick: int, until_tick: int
    ) -> "RobotState":
        """Take ``order`` at ``tick`` and start driving ``route`` to its pickup.

        If the robot already stands at the pickup, ``route`` is the single-node route and
        the robot starts loading straight away; ``until_tick`` is then the end of loading.
        """
        phase = RobotPhase.LOADING if len(route.nodes) == 1 else RobotPhase.TO_PICKUP
        self._check_transition("assign", phase, tick)
        if tick < order.release_tick:
            raise RobotStateError(
                f"robot {self.robot_id!r}: order {order.id!r} is released at tick "
                f"{order.release_tick}, cannot assign it at {tick}"
            )
        if route.source != self.node:
            raise RobotStateError(
                f"robot {self.robot_id!r}: route starts at {route.source!r}, robot is at "
                f"{self.node!r}"
            )
        return RobotState(
            self.robot_id,
            phase,
            self.node,
            tick,
            until_tick,
            order,
            route if phase.is_travelling else None,
        )

    def arrive(self, *, tick: int, until_tick: int) -> "RobotState":
        """Reach the route's end at ``tick``; load or unload from then until ``until_tick``."""
        phase = {
            RobotPhase.TO_PICKUP: RobotPhase.LOADING,
            RobotPhase.TO_DROPOFF: RobotPhase.UNLOADING,
        }.get(self.phase)
        if phase is None:
            raise InvalidTransitionError(
                f"robot {self.robot_id!r}: cannot arrive while {self.phase.name}"
            )
        self._check_transition("arrive", phase, tick)
        assert self.route is not None  # guaranteed by the travelling-phase invariant
        return RobotState(self.robot_id, phase, self.route.target, tick, until_tick, self.order)

    def finish_loading(self, route: Route, *, tick: int, until_tick: int) -> "RobotState":
        """Finish loading at ``tick`` and start driving ``route`` to the drop-off."""
        self._check_transition("finish loading", RobotPhase.TO_DROPOFF, tick)
        return RobotState(
            self.robot_id, RobotPhase.TO_DROPOFF, self.node, tick, until_tick, self.order, route
        )

    def finish_unloading(self, *, tick: int) -> "RobotState":
        """Hand the load over at ``tick``: the order is delivered and the robot is idle."""
        self._check_transition("finish unloading", RobotPhase.IDLE, tick)
        return RobotState(self.robot_id, RobotPhase.IDLE, self.node, tick)

    def _check_transition(self, action: str, phase: RobotPhase, tick: int) -> None:
        """Refuse a phase change outside the table, or one at the wrong tick."""
        name = repr(self.robot_id)
        if phase not in ALLOWED_TRANSITIONS[self.phase]:
            raise InvalidTransitionError(f"robot {name}: cannot {action} while {self.phase.name}")
        if self.until_tick is not None and tick != self.until_tick:
            raise RobotStateError(
                f"robot {name}: {self.phase.name} ends at tick {self.until_tick}, not {tick}"
            )
        if tick < self.since_tick:
            raise RobotStateError(
                f"robot {name}: tick {tick} is before the current phase began ({self.since_tick})"
            )
