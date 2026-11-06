"""Request and response models of the HTTP service (D29). Pydantic lives here only (D3).

Times are integer ticks of 0.1 s, as everywhere in the engine (D6). Every collection has an upper
bound, so that one request cannot occupy the service for long.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.warehouse import PRESETS

TICK_S = 0.1  # the engine's time unit, in seconds
MAX_ROBOTS = 100
MAX_ORDERS = 200
NAME = Field(min_length=1, max_length=40)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RobotIn(_Model):
    """A robot in the fleet snapshot. Without ``ready_tick`` it is idle at ``node`` now; with it,
    it is busy and will be free at ``node`` from ``ready_tick`` on."""

    id: str = NAME
    node: str = NAME
    ready_tick: int | None = Field(default=None, ge=0)


class OrderIn(_Model):
    """A pending transport order, released at ``release_tick``."""

    id: str = NAME
    pickup: str = NAME
    dropoff: str = NAME
    release_tick: int = Field(ge=0)


class PlanRequest(_Model):
    """One dispatch decision: the fleet and the pending orders at ``tick``."""

    map: str = "large"
    policy: str = "greedy"
    tick: int = Field(default=0, ge=0)
    robots: list[RobotIn] = Field(min_length=1, max_length=MAX_ROBOTS)
    orders: list[OrderIn] = Field(default_factory=list, max_length=MAX_ORDERS)
    speed_mps: float = Field(default=1.0, gt=0, le=5)
    load_s: float = Field(default=10.0, ge=0, le=600)
    unload_s: float = Field(default=10.0, ge=0, le=600)

    @field_validator("map")
    @classmethod
    def _known_map(cls, value: str) -> str:
        if value not in PRESETS:
            raise ValueError(f"unknown map {value!r}; choose one of {sorted(PRESETS)}")
        return value

    @field_validator("policy")
    @classmethod
    def _known_policy(cls, value: str) -> str:
        if value not in POLICIES:
            raise ValueError(f"unknown policy {value!r}; choose one of {sorted(POLICIES)}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for kind, ids in (
            ("robot", [r.id for r in self.robots]),
            ("order", [o.id for o in self.orders]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{kind} ids must be unique")
        for robot in self.robots:
            if robot.ready_tick is not None and robot.ready_tick < self.tick:
                raise ValueError(f"robot {robot.id!r}: ready_tick is before tick")
        for order in self.orders:
            if order.release_tick > self.tick:
                raise ValueError(f"order {order.id!r} is not released yet at tick {self.tick}")
        return self


class Assignment(_Model):
    robot: str
    order: str


class PlanResponse(_Model):
    """The policy's decision: which idle robot starts which order now."""

    policy: str
    tick: int
    assignments: list[Assignment]
    compute_ms: float = Field(description="wall time of the decision, in the worker process")


class MapSummary(_Model):
    name: str
    nodes: int
    edges: int


class NodeOut(_Model):
    id: str
    x: float
    y: float
    kind: str


class MapDetail(_Model):
    name: str
    nodes: list[NodeOut]


class Health(_Model):
    status: str
    version: str
    tick_s: float
