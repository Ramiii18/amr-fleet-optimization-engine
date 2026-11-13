"""What a robot's trace says about it at any tick: position, battery level and status (D35).

These pure functions are the reference for every viewer. They read a robot's recorded path,
its recorded levels and its events; they compute nothing the simulator did not.

**Position.** `viz.motion.position_at` moves a robot evenly along an edge from its departure
to its arrival. That is exact unless the robot had to wait at the end of the corridor before it
could enter the node: the simulator then records when it reached the end. `position_at` here
uses that: the robot moves evenly from its departure to the moment it reached the end of the
corridor, and stands there until it enters the node. Without a recorded wait, both functions
agree.

A traversal with a delay or a breakdown is longer than the edge's travel time, and the model
does not say where on the edge the robot loses that time. The position along such an edge is
therefore a convention (even movement), and `status_at` says when it applies.

**Battery level.** The simulator updates a level in three places, and `level_at` says which
one a value comes from:

- ``reading``: recorded by the simulator, on arrival at a node or in an event. Between two
  readings the last one is returned unchanged, with the tick it dates from. Driving an edge
  costs energy only on arrival, so there is no recorded value in between, and none is invented.
- ``charging_formula``: while the robot stands on a charger, the level is
  ``min(capacity, level at the start + ticks * charge_per_tick)``, the formula the simulator
  itself uses. It is a calculation by the model's rule, not a recorded reading.

**Status.** Every status is an interval between two events, or a traversal that an event
belongs to. Intervals include their start tick and not their end tick. One still open when the
run ends lasts to the end of the run.
"""

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from amr_optimizer.sim import BatteryRecord, RobotEvent
from amr_optimizer.sim import RobotEventKind as K
from amr_optimizer.traffic import TimedPath
from amr_optimizer.warehouse import WarehouseGraph


class Status(Enum):
    """What the events say a robot is in, besides its work on an order."""

    CHARGING = "charging"  # standing on a charger, gaining energy
    OUT_OF_SERVICE = "out_of_service"  # below the low threshold: it gets no job
    WAITING_FOR_CHARGER = "waiting_for_charger"  # out of service, and no charger is free
    # On an edge whose traversal includes a repair (announced) or a delay (not announced).
    # Where on the edge the robot loses the time is not modelled.
    BREAKDOWN = "breakdown"
    DELAY = "delay"
    CORRIDOR_WAIT = "corridor_wait"  # at the end of a corridor, not yet allowed into the node


class LevelSource(Enum):
    READING = "reading"
    CHARGING_FORMULA = "charging_formula"


@dataclass(frozen=True, slots=True)
class Level:
    """A battery level: ``units`` as of tick ``as_of``, and where the value comes from."""

    units: int
    as_of: int
    source: LevelSource


_INTERVALS = {
    K.CHARGING_STARTED: (K.CHARGING_STOPPED, Status.CHARGING),
    K.OUT_OF_SERVICE: (K.BACK_IN_SERVICE, Status.OUT_OF_SERVICE),
    K.CHARGER_WAIT_STARTED: (K.CHARGER_WAIT_ENDED, Status.WAITING_FOR_CHARGER),
    K.CORRIDOR_WAIT_STARTED: (K.CORRIDOR_WAIT_ENDED, Status.CORRIDOR_WAIT),
}
_ENDS = {end: status for end, status in _INTERVALS.values()}
_TRAVERSALS = {K.BREAKDOWN: Status.BREAKDOWN, K.DELAY: Status.DELAY}


def _visit_at(path: TimedPath, tick: float) -> int:
    """The index of the visit the robot is in at ``tick``, or has last left."""
    arrivals = [visit.arrive for visit in path.visits]
    return max(0, bisect.bisect_right(arrivals, tick) - 1)


def corridor_arrivals(path: TimedPath, events: Sequence[RobotEvent]) -> dict[int, int]:
    """For each visit a robot left and then waited after: the tick it reached the end of the
    corridor. ``events`` are the robot's own. The key is the index of the visit it left."""
    departures = [visit.depart for visit in path.visits]
    reached: dict[int, int] = {}
    for event in events:
        if event.kind is K.CORRIDOR_WAIT_STARTED:
            # The traversal it belongs to: the last departure before the wait began.
            reached[bisect.bisect_left(departures, event.tick) - 1] = event.tick
    return reached


def position_at(
    path: TimedPath, events: Sequence[RobotEvent], tick: float, graph: WarehouseGraph
) -> tuple[float, float]:
    """The robot's (x, y) in metres at ``tick``, with waits at the end of corridors.

    ``events`` are the robot's own events. With no corridor wait among them, this equals
    `viz.motion.position_at`.
    """
    visits = path.visits
    index = _visit_at(path, tick)
    here = graph.node(visits[index].node)
    if index == len(visits) - 1 or tick <= visits[index].depart:
        return here.x, here.y
    following = visits[index + 1]
    there = graph.node(following.node)
    reached = corridor_arrivals(path, events).get(index, following.arrive)
    if tick >= reached:  # standing at the end of the corridor, by the node it will enter
        return there.x, there.y
    share = (tick - visits[index].depart) / (reached - visits[index].depart)
    return here.x + share * (there.x - here.x), here.y + share * (there.y - here.y)


def status_at(path: TimedPath, events: Sequence[RobotEvent], tick: float) -> tuple[Status, ...]:
    """Every status of the robot at ``tick``, in the order of `Status`. ``events`` are the
    robot's own events, in time order."""
    active: set[Status] = set()
    departures = [visit.depart for visit in path.visits]
    for event in events:
        if event.tick > tick:
            break
        if event.kind in _INTERVALS:
            active.add(_INTERVALS[event.kind][1])
        elif event.kind in _TRAVERSALS:
            # From the departure it was drawn at, until the robot enters the next node.
            index = bisect.bisect_left(departures, event.tick)
            arrival = path.visits[index + 1].arrive if index + 1 < len(path.visits) else math.inf
            if tick < arrival:
                active.add(_TRAVERSALS[event.kind])
        elif event.kind in _ENDS:
            active.discard(_ENDS[event.kind])
    return tuple(status for status in Status if status in active)


def level_at(
    path: TimedPath,
    levels: Sequence[int],
    events: Sequence[RobotEvent],
    model: BatteryRecord,
    tick: float,
) -> Level:
    """The robot's battery level at ``tick`` and where the value comes from (see the module).

    ``levels`` are the recorded levels on arrival at each visit of ``path``, and ``events`` the
    robot's own events, in time order.
    """
    index = _visit_at(path, tick)
    level = Level(levels[index], path.visits[index].arrive, LevelSource.READING)
    whole = math.floor(tick)
    charging: RobotEvent | None = None
    for event in events:
        if event.tick > tick:
            break
        if event.tick < path.visits[index].arrive:
            continue  # before this visit: already in the level of its arrival
        if event.kind is K.CHARGING_STARTED:
            charging = event
        elif event.kind is K.CHARGING_STOPPED:
            charging = None
        if event.level is not None:  # the simulator computed this level at this tick
            level = Level(event.level, event.tick, LevelSource.READING)
    if charging is not None and charging.level is not None and whole > charging.tick:
        units = min(
            model.capacity, charging.level + (whole - charging.tick) * model.charge_per_tick
        )
        if whole > level.as_of:  # later than the last recorded value: the formula says more
            return Level(units, whole, LevelSource.CHARGING_FORMULA)
    return level


def events_by_robot(events: Sequence[RobotEvent]) -> Mapping[str, list[RobotEvent]]:
    """``events`` grouped by robot, each group in time order."""
    grouped: dict[str, list[RobotEvent]] = {}
    for event in events:
        grouped.setdefault(event.robot_id, []).append(event)
    return grouped
