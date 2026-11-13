"""Write the golden fixtures of the replay viewer into ``tests/data/replay/`` (D33, D34).

    .venv\\Scripts\\python.exe scripts\\make_replay_fixtures.py

Each fixture is a pair of files, both produced by real runs of the simulator:

- ``<name>.trace.json``: the run's replay trace, exactly as `trace_to_json` writes it;
- ``<name>.samples.json``: where every robot is and what it is doing at chosen ticks, computed
  by the reference functions `viz.motion.position_at` and `viz.motion.ActivityTimeline`.

A viewer in another language must reproduce the samples from the trace alone. The ticks are
chosen where an interpolation can go wrong: at every arrival and departure, between two nodes
at a fraction of a tick, at every change of activity and just before it, at tick 0, at the
end of the run and after it.

The files are committed. A test fails if they differ from what this script would write now,
so a change to the trace format or to the fixture runs cannot go unnoticed.
"""

import json
import math
import random
from collections.abc import Callable, Mapping
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import BatterySpec, ChargingPolicy, RobotId
from amr_optimizer.replay import build_trace, downgrade_to_v1, trace_to_json
from amr_optimizer.replay.state import events_by_robot, level_at, status_at
from amr_optimizer.replay.state import position_at as replay_position_at
from amr_optimizer.scenario import ScenarioSpec, generate_scenario
from amr_optimizer.sim import SimulationResult, simulate
from amr_optimizer.traffic import DelayModel, Recovery, TimedPath
from amr_optimizer.viz.motion import ActivityTimeline, position_at
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "data" / "replay"
SAMPLES_FORMAT = "amr-replay-samples"
SAMPLES_VERSION = 1
RANDOM_TICKS_PER_ROBOT = 25

Settings = dict[str, object]


def _free_flow(max_tick: int | None = None) -> tuple[SimulationResult, Settings]:
    settings: Settings = {
        "map": "small",
        "robots": 3,
        "orders_per_hour": 240.0,
        "duration_s": 240.0,
        "seed": 2,
        "policy": "greedy",
        "traffic": False,
        "max_tick": max_tick,
    }
    spec = ScenarioSpec(robots=3, orders_per_hour=240.0, duration_s=240.0)
    scenario = generate_scenario(generate_grid_warehouse(PRESETS["small"]), spec, seed=2)
    return simulate(scenario, POLICIES["greedy"](), max_tick=max_tick), settings


def _cut_short() -> tuple[SimulationResult, Settings]:
    return _free_flow(max_tick=1500)


def _disturbed() -> tuple[SimulationResult, Settings]:
    """Traffic with everything that bends a trajectory: delays, charging, breakdowns, replans."""
    settings: Settings = {
        "map": "small",
        "robots": 3,
        "orders_per_hour": 180.0,
        "duration_s": 300.0,
        "seed": 4,
        "policy": "insertion-fair",
        "traffic": True,
        "delays": {"share": 0.1, "min_s": 1.0, "max_s": 3.0},
        "charging": "opportunistic",
        "breakdowns": {"share": 0.05, "min_s": 10.0, "max_s": 60.0},
        "recovery": "replan",
    }
    spec = ScenarioSpec(robots=3, orders_per_hour=180.0, duration_s=300.0)
    scenario = generate_scenario(generate_grid_warehouse(PRESETS["small"]), spec, seed=4)
    result = simulate(
        scenario,
        POLICIES["insertion-fair"](),
        traffic=True,
        delays=DelayModel(0.1, 10, 30),
        delay_seed=4,
        battery=BatterySpec(),
        battery_seed=4,
        charging=ChargingPolicy.OPPORTUNISTIC,
        breakdowns=DelayModel(0.05, 100, 600),
        breakdown_seed=4,
        recovery=Recovery.REPLAN,
    )
    return result, settings


CASES: Mapping[str, Callable[[], tuple[SimulationResult, Settings]]] = {
    "free_flow": _free_flow,
    "cut_short": _cut_short,
    "disturbed": _disturbed,
}


def sample_ticks(
    path: TimedPath, changes: list[int], end_tick: int, rng: random.Random
) -> list[float]:
    """The ticks at which one robot is sampled, in order and without repeats."""
    ticks: set[float] = {0.0, 0.5, float(end_tick), end_tick + 7.5}
    for visit, following in zip(path.visits, [*path.visits[1:], None], strict=True):
        ticks.add(float(visit.arrive))
        if visit.depart <= end_tick:  # not a hold
            ticks.add(float(visit.depart))
        if following is not None:
            # A third of the way along the edge: never a whole tick on a round travel time.
            ticks.add(visit.depart + (following.arrive - visit.depart) / 3)
    for change in changes:
        ticks.update((float(change), change - 0.5))
    ticks.update(round(rng.uniform(0, end_tick), 3) for _ in range(RANDOM_TICKS_PER_ROBOT))
    return sorted(tick for tick in ticks if tick >= 0)


def samples_of(result: SimulationResult) -> dict[str, list[list[object]]]:
    """For every robot, rows of ``[tick, x, y, activity]`` from the reference functions."""
    graph = result.scenario.graph
    activities = ActivityTimeline(result.log)
    rng = random.Random(0)
    rows: dict[str, list[list[object]]] = {}
    for robot in result.scenario.robots:
        robot_id: RobotId = robot.robot_id
        path = result.trajectories[robot_id]
        changes = [e.tick for e in result.log if e.robot_id == robot_id]
        rows[robot_id] = []
        for tick in sample_ticks(path, changes, result.end_tick, rng):
            x, y = position_at(path, tick, graph)
            # An activity changes at the tick of its event, so a fraction of a tick before the
            # next whole tick still has the activity of the tick it is in.
            activity = activities.at(robot_id, math.floor(tick))
            rows[robot_id].append([tick, x, y, activity.value])
    return rows


def _charging() -> tuple[SimulationResult, Settings]:
    """Batteries that start low on a map with one charger: charging, time out of service and
    waiting for the charger, with delays so that some traversals are longer than their edge."""
    settings: Settings = {
        "map": "small",
        "robots": 3,
        "orders_per_hour": 150.0,
        "duration_s": 360.0,
        "seed": 6,
        "policy": "greedy",
        "traffic": True,
        "delays": {"share": 0.1, "min_s": 1.0, "max_s": 3.0},
        "charging": "opportunistic-early",
        "battery": {"start_min_share": 0.26, "start_max_share": 0.32},
    }
    spec = ScenarioSpec(robots=3, orders_per_hour=150.0, duration_s=360.0)
    scenario = generate_scenario(generate_grid_warehouse(PRESETS["small"]), spec, seed=6)
    result = simulate(
        scenario,
        POLICIES["greedy"](),
        traffic=True,
        delays=DelayModel(0.1, 10, 30),
        delay_seed=6,
        battery=BatterySpec(start_min_share=0.26, start_max_share=0.32),
        battery_seed=6,
        charging=ChargingPolicy.OPPORTUNISTIC_EARLY,
    )
    return result, settings


V2_CASES: Mapping[str, Callable[[], tuple[SimulationResult, Settings]]] = {
    **CASES,
    "charging": _charging,
}
"""The runs of the version 2 fixtures: those of version 1, and one about charging."""

V2_COLUMNS = [
    "tick",
    "x_m",
    "y_m",
    "activity",
    "status",
    "level_units",
    "level_as_of_tick",
    "level_source",
]


def samples_v2_of(result: SimulationResult) -> dict[str, list[list[object]]]:
    """For every robot, rows of `V2_COLUMNS` from the reference functions of `replay.state`:
    the position with waits at the end of corridors, the statuses, and the battery level with
    the tick and the source of its value (null without a battery model)."""
    graph = result.scenario.graph
    activities = ActivityTimeline(result.log)
    events = events_by_robot(result.robot_events)
    rng = random.Random(1)
    rows: dict[str, list[list[object]]] = {}
    for robot in result.scenario.robots:
        robot_id: RobotId = robot.robot_id
        path = result.trajectories[robot_id]
        own = events.get(robot_id, [])
        changes = [e.tick for e in result.log if e.robot_id == robot_id]
        ticks = set(sample_ticks(path, changes, result.end_tick, rng))
        for event in own:  # at every robot event, just before it and just after it
            ticks.update((float(event.tick), event.tick - 0.5, event.tick + 0.5))
        rows[robot_id] = []
        for tick in sorted(t for t in ticks if t >= 0):
            x, y = replay_position_at(path, own, tick, graph)
            activity = activities.at(robot_id, math.floor(tick))
            row: list[object] = [tick, x, y, activity.value]
            row.append([status.value for status in status_at(path, own, tick)])
            if result.battery_record is None:
                row += [None, None, None]
            else:
                levels = result.energy_levels[robot_id]
                level = level_at(path, levels, own, result.battery_record, tick)
                row += [level.units, level.as_of, level.source.value]
            rows[robot_id].append(row)
    return rows


def _json(data: object) -> str:
    return json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n"


def fixture_files() -> dict[str, str]:
    """Every fixture file's name (relative to `FIXTURES`) and exact text.

    The version 1 files are frozen in what they contain: they are the version 2 trace of the
    same run with the additions of version 2 removed (`downgrade_to_v1`). If the simulator or
    the trace ever changed an older field, these files would change, and the test that compares
    them with the committed ones would fail.
    """
    files: dict[str, str] = {}
    for name, run in V2_CASES.items():
        result, settings = run()
        trace = trace_to_json(build_trace(result, run=settings))
        if name in CASES:
            files[f"{name}.trace.json"] = _json(downgrade_to_v1(json.loads(trace)))
            files[f"{name}.samples.json"] = _json(
                {
                    "format": SAMPLES_FORMAT,
                    "version": SAMPLES_VERSION,
                    "trace": f"{name}.trace.json",
                    "source": "viz.motion.position_at and ActivityTimeline, on the run of the "
                    "trace",
                    "columns": ["tick", "x_m", "y_m", "activity"],
                    "robots": samples_of(result),
                }
            )
        files[f"v2/{name}.trace.json"] = trace + "\n"
        files[f"v2/{name}.samples.json"] = _json(
            {
                "format": SAMPLES_FORMAT,
                "version": 2,
                "trace": f"{name}.trace.json",
                "source": "replay.state (position_at, status_at, level_at) and "
                "viz.motion.ActivityTimeline, on the run of the trace",
                "columns": V2_COLUMNS,
                "robots": samples_v2_of(result),
            }
        )
    return files


def main() -> None:
    for name, text in fixture_files().items():
        path = FIXTURES / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n": identical bytes on every OS.
        path.write_text(text, encoding="ascii", newline="\n")
        print(f"{name}: {len(text) / 1000:.1f} kB")


if __name__ == "__main__":
    main()
