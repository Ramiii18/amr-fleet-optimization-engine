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
from amr_optimizer.replay import build_trace, trace_to_json
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


def fixture_files() -> dict[str, str]:
    """Every fixture file's name and exact text."""
    files: dict[str, str] = {}
    for name, run in CASES.items():
        result, settings = run()
        files[f"{name}.trace.json"] = trace_to_json(build_trace(result, run=settings)) + "\n"
        samples = {
            "format": SAMPLES_FORMAT,
            "version": SAMPLES_VERSION,
            "trace": f"{name}.trace.json",
            "source": "viz.motion.position_at and ActivityTimeline, on the run of the trace",
            "columns": ["tick", "x_m", "y_m", "activity"],
            "robots": samples_of(result),
        }
        files[f"{name}.samples.json"] = (
            json.dumps(samples, separators=(",", ":"), allow_nan=False) + "\n"
        )
    return files


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, text in fixture_files().items():
        # newline="\n": identical bytes on every OS.
        (FIXTURES / name).write_text(text, encoding="ascii", newline="\n")
        print(f"{name}: {len(text) / 1000:.1f} kB")


if __name__ == "__main__":
    main()
