"""A disturbance schedule: delays and breakdowns fixed before a run (D36).

The independent model (`DelayModel`, D25 and D28) draws a delay and a breakdown each time a
robot enters a corridor, from the robot's own stream. Two dispatch policies route a robot
differently, so the same draw lands on another corridor at another time: with the same seed,
two policies face the same *distribution* of disturbances and not the same disturbances.

A schedule fixes what is exogenous before the run, from the seed and the map alone:

- **Delay episodes** belong to a corridor: "this corridor is slow from tick ``start`` to tick
  ``end``". A robot that enters the corridor during an episode takes extra time. How much is
  a function of the seed, the corridor, the episode and the robot, so the same robot in the
  same episode gets the same delay under every policy, and two robots get different ones.
- **Breakdowns** belong to a robot: "this robot's next corridor entry at or after tick
  ``tick`` breaks down, for ``repair_ticks``". A robot that drives less does not escape its
  breakdown; it meets it later, on another corridor.

Episodes follow a renewal process per corridor (and per robot): the time to the next one is
exponential, and there is no shared grid of time windows. Which episodes a robot meets still
depends on the policy. That is the effect a comparison measures, not noise.

A schedule is a value: it can be counted, exported and compared before any run. Nothing in it
is known to the planners: delays stay unannounced, and a breakdown is announced when it
happens, exactly as in the independent model.
"""

import bisect
import hashlib
import json
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from amr_optimizer.domain import RobotId
from amr_optimizer.warehouse import NodeId, WarehouseGraph

CorridorKey = tuple[NodeId, NodeId]


def corridor_key(a: NodeId, b: NodeId) -> CorridorKey:
    """The corridor between two nodes, whichever way it is driven."""
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True, slots=True)
class DisturbanceSpec:
    """The parameters of a schedule. Every time is in ticks.

    ``delay_share`` is the share of time each corridor spends in a delay episode: a robot that
    enters a corridor at a random moment is delayed with that probability, which is how the
    independent model's ``probability`` is defined. ``breakdown_gap_ticks`` is the mean time
    between two breakdowns of one robot; 0 means no breakdowns.
    """

    horizon_ticks: int
    delay_share: float = 0.0
    episode_min_ticks: int = 300
    episode_max_ticks: int = 1200
    delay_min_ticks: int = 1
    delay_max_ticks: int = 1
    breakdown_gap_ticks: int = 0
    repair_min_ticks: int = 1
    repair_max_ticks: int = 1

    def __post_init__(self) -> None:
        if self.horizon_ticks < 0:
            raise ValueError(f"the horizon must be non-negative, got {self.horizon_ticks}")
        if not 0 <= self.delay_share < 1:
            raise ValueError(f"the delay share must be in [0, 1), got {self.delay_share}")
        if self.breakdown_gap_ticks < 0:
            raise ValueError(
                f"the breakdown gap must be non-negative, got {self.breakdown_gap_ticks}"
            )
        for name, low, high in (
            ("episode", self.episode_min_ticks, self.episode_max_ticks),
            ("delay", self.delay_min_ticks, self.delay_max_ticks),
            ("repair", self.repair_min_ticks, self.repair_max_ticks),
        ):
            if not 1 <= low <= high:
                raise ValueError(f"{name} ticks need 1 <= min <= max, got {low}, {high}")


@dataclass(frozen=True, slots=True)
class DelayEpisode:
    """Corridor ``corridor`` is slow from ``start`` to ``end`` (``end`` not included).
    ``index`` counts the corridor's episodes from 0."""

    corridor: CorridorKey
    index: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Breakdown:
    """The robot's next corridor entry at or after ``tick`` breaks down for ``repair_ticks``."""

    robot_id: RobotId
    tick: int
    repair_ticks: int


@dataclass(frozen=True, slots=True)
class DisturbanceSchedule:
    """Every delay episode and breakdown of a run, fixed before it starts.

    Episodes of one corridor do not overlap and are in time order, as are the breakdowns of
    one robot. After ``spec.horizon_ticks`` nothing is scheduled: a run that lasts longer
    meets no further disturbance, and a result says so (`beyond_horizon` in the lab's rows).
    """

    seed: int
    spec: DisturbanceSpec
    episodes: tuple[DelayEpisode, ...]
    breakdowns: tuple[Breakdown, ...]
    _starts: Mapping[CorridorKey, list[int]] = field(init=False, repr=False, compare=False)
    _by_corridor: Mapping[CorridorKey, list[DelayEpisode]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        by_corridor: dict[CorridorKey, list[DelayEpisode]] = {}
        for episode in self.episodes:
            own = by_corridor.setdefault(episode.corridor, [])
            if not 0 <= episode.start < episode.end:
                raise ValueError(f"a delay episode needs 0 <= start < end, got {episode}")
            if own and episode.start < own[-1].end:
                raise ValueError(f"delay episodes of {episode.corridor} overlap or are unordered")
            if episode.index != len(own):
                raise ValueError(f"delay episodes of {episode.corridor} must be numbered from 0")
            own.append(episode)
        last: dict[RobotId, int] = {}
        for breakdown in self.breakdowns:
            if breakdown.tick < last.get(breakdown.robot_id, 0) or breakdown.repair_ticks < 1:
                raise ValueError(f"breakdowns of a robot must be in time order, got {breakdown}")
            last[breakdown.robot_id] = breakdown.tick
        object.__setattr__(self, "_by_corridor", by_corridor)
        object.__setattr__(
            self, "_starts", {key: [e.start for e in own] for key, own in by_corridor.items()}
        )

    def episode_at(self, here: NodeId, there: NodeId, tick: int) -> DelayEpisode | None:
        """The delay episode of the corridor between ``here`` and ``there`` at ``tick``."""
        key = corridor_key(here, there)
        starts = self._starts.get(key)
        if not starts:
            return None
        index = bisect.bisect_right(starts, tick) - 1
        if index < 0:
            return None
        episode = self._by_corridor[key][index]
        return episode if tick < episode.end else None

    def delay_ticks(self, robot_id: RobotId, here: NodeId, there: NodeId, tick: int) -> int:
        """The extra ticks of ``robot_id`` entering that corridor at ``tick``: 0 outside an
        episode. Within one, the same robot always gets the same delay."""
        episode = self.episode_at(here, there, tick)
        if episode is None:
            return 0
        a, b = episode.corridor
        rng = random.Random(f"{self.seed}:delay:{a}:{b}:{episode.index}:{robot_id}")
        return rng.randint(self.spec.delay_min_ticks, self.spec.delay_max_ticks)

    def breakdowns_of(self, robot_id: RobotId) -> tuple[Breakdown, ...]:
        return tuple(b for b in self.breakdowns if b.robot_id == robot_id)

    def to_dict(self) -> dict[str, object]:
        """The schedule as plain data, for export and for the fingerprint."""
        spec = self.spec
        return {
            "seed": self.seed,
            "spec": {name: getattr(spec, name) for name in spec.__slots__},
            "delay_episodes": [[*e.corridor, e.start, e.end] for e in self.episodes],
            "breakdowns": [[b.robot_id, b.tick, b.repair_ticks] for b in self.breakdowns],
        }


def _gap(rng: random.Random, mean: float) -> int:
    return max(1, round(rng.expovariate(1 / mean)))


def generate_disturbances(
    graph: WarehouseGraph, robots: Iterable[RobotId], spec: DisturbanceSpec, seed: int
) -> DisturbanceSchedule:
    """A schedule from the seed and the map alone. Each corridor and each robot has its own
    stream, named after the seed, so adding a robot or a corridor changes nothing else.

    Delay episodes: an episode's length is uniform between the spec's bounds, and the gap to
    the next one is exponential with the mean that makes the corridor slow for
    ``delay_share`` of the time. Each corridor starts at a random point of its cycle, so tick
    0 is not special. Breakdowns: gaps are exponential with mean ``breakdown_gap_ticks``.
    """
    episodes: list[DelayEpisode] = []
    if spec.delay_share > 0:
        length = (spec.episode_min_ticks + spec.episode_max_ticks) / 2
        gap = length * (1 - spec.delay_share) / spec.delay_share
        corridors = sorted({corridor_key(edge.source, edge.target) for edge in graph.edges})
        for corridor in corridors:
            rng = random.Random(f"{seed}:episodes:{corridor[0]}:{corridor[1]}")
            # Start somewhere in a cycle: possibly inside an episode that began before tick 0.
            tick = -round(rng.random() * (length + gap))
            index = 0
            while True:
                start = tick + _gap(rng, gap)
                end = start + rng.randint(spec.episode_min_ticks, spec.episode_max_ticks)
                tick = end
                if start >= spec.horizon_ticks:
                    break
                if end > 0:
                    episodes.append(
                        DelayEpisode(corridor, index, max(0, start), min(end, spec.horizon_ticks))
                    )
                    index += 1
    breakdowns: list[Breakdown] = []
    if spec.breakdown_gap_ticks > 0:
        for robot_id in sorted(robots):
            rng = random.Random(f"{seed}:breakdowns:{robot_id}")
            tick = 0
            while True:
                tick += _gap(rng, spec.breakdown_gap_ticks)
                repair = rng.randint(spec.repair_min_ticks, spec.repair_max_ticks)
                if tick >= spec.horizon_ticks:
                    break
                breakdowns.append(Breakdown(robot_id, tick, repair))
    return DisturbanceSchedule(seed, spec, tuple(episodes), tuple(breakdowns))


def fingerprint(*parts: object) -> str:
    """A short hash of plain data (lists, dicts, numbers, strings): equal exactly when the
    data are equal. The lab uses it to show that two runs faced the same conditions."""
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()[:16]
