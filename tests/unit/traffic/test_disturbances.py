"""Tests for disturbance schedules (D36): the value itself, and runs that follow one.

- A schedule depends on the seed and the map alone, and has the shares it was asked for.
- Reconciliation, both ways: every delay or breakdown of a run comes from the schedule, and
  every corridor entry that the schedule disturbs is disturbed in the run.
- Two policies on one schedule face the same conditions (equal fingerprints).
"""

import statistics
from typing import Any

import pytest

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import RobotId
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import RobotEventKind as K
from amr_optimizer.sim import SimulationResult, simulate
from amr_optimizer.traffic import (
    Breakdown,
    DelayEpisode,
    DelayModel,
    DisturbanceSchedule,
    DisturbanceSpec,
    Recovery,
    corridor_key,
    fingerprint,
    generate_disturbances,
)
from amr_optimizer.warehouse import PRESETS, NodeId, generate_grid_warehouse

GRAPH = generate_grid_warehouse(PRESETS["small"])
ROBOTS = [RobotId("R01"), RobotId("R02"), RobotId("R03")]
SPEC = DisturbanceSpec(
    horizon_ticks=40_000,
    delay_share=0.15,
    episode_min_ticks=300,
    episode_max_ticks=1200,
    delay_min_ticks=10,
    delay_max_ticks=40,
    breakdown_gap_ticks=2000,
    repair_min_ticks=200,
    repair_max_ticks=900,
)


def test_a_schedule_depends_on_the_seed_alone_and_differs_between_seeds() -> None:
    first = generate_disturbances(GRAPH, ROBOTS, SPEC, seed=3)

    assert first == generate_disturbances(GRAPH, reversed(ROBOTS), SPEC, seed=3)
    assert first.episodes != generate_disturbances(GRAPH, ROBOTS, SPEC, seed=4).episodes
    # Another robot adds its own breakdowns and changes nothing else.
    more = generate_disturbances(GRAPH, [*ROBOTS, RobotId("R04")], SPEC, seed=3)
    assert more.episodes == first.episodes
    assert [b for b in more.breakdowns if b.robot_id != "R04"] == list(first.breakdowns)


def test_episodes_lie_within_the_horizon_and_do_not_overlap() -> None:
    schedule = generate_disturbances(GRAPH, ROBOTS, SPEC, seed=0)
    last: dict[tuple[NodeId, NodeId], int] = {}

    assert schedule.episodes and schedule.breakdowns
    for episode in schedule.episodes:
        assert last.get(episode.corridor, 0) <= episode.start < episode.end <= SPEC.horizon_ticks
        assert episode.corridor == corridor_key(*episode.corridor)
        last[episode.corridor] = episode.end
    for breakdown in schedule.breakdowns:
        assert 0 < breakdown.tick < SPEC.horizon_ticks
        assert SPEC.repair_min_ticks <= breakdown.repair_ticks <= SPEC.repair_max_ticks


def test_a_corridor_is_slow_for_the_share_of_time_asked_for() -> None:
    # Over all corridors and five seeds, on a long horizon: the law of large numbers.
    spec = DisturbanceSpec(horizon_ticks=400_000, delay_share=0.15, breakdown_gap_ticks=2000)
    corridors = {corridor_key(e.source, e.target) for e in GRAPH.edges}
    slow = gaps = 0.0
    for seed in range(5):
        schedule = generate_disturbances(GRAPH, ROBOTS, spec, seed)
        slow += sum(e.end - e.start for e in schedule.episodes)
        ticks = [b.tick for b in schedule.breakdowns_of(ROBOTS[0])]
        gaps += statistics.fmean(b - a for a, b in zip([0, *ticks], ticks, strict=False))

    assert slow / (5 * len(corridors) * spec.horizon_ticks) == pytest.approx(0.15, abs=0.01)
    assert gaps / 5 == pytest.approx(2000, rel=0.15)


def test_a_delay_belongs_to_the_episode_and_the_robot() -> None:
    schedule = generate_disturbances(GRAPH, ROBOTS, SPEC, seed=1)
    episode = schedule.episodes[0]
    a, b = episode.corridor
    first, later = episode.start, episode.end - 1

    assert schedule.episode_at(a, b, first) == schedule.episode_at(b, a, later) == episode
    assert schedule.episode_at(a, b, episode.end) != episode  # the end is not included
    delay = schedule.delay_ticks(ROBOTS[0], a, b, first)
    assert SPEC.delay_min_ticks <= delay <= SPEC.delay_max_ticks
    assert delay == schedule.delay_ticks(ROBOTS[0], b, a, later)  # same robot, same episode
    others = {schedule.delay_ticks(RobotId(f"R{i:02}"), a, b, first) for i in range(30)}
    assert len(others) > 5  # robots draw their own
    quiet = next(t for t in range(SPEC.horizon_ticks) if schedule.episode_at(a, b, t) is None)
    assert schedule.delay_ticks(ROBOTS[0], a, b, quiet) == 0


def test_no_disturbances_gives_an_empty_schedule() -> None:
    schedule = generate_disturbances(GRAPH, ROBOTS, DisturbanceSpec(horizon_ticks=1000), seed=0)

    assert schedule.episodes == () and schedule.breakdowns == ()


@pytest.mark.parametrize(
    "fields",
    [
        {"horizon_ticks": -1},
        {"horizon_ticks": 10, "delay_share": 1.0},
        {"horizon_ticks": 10, "breakdown_gap_ticks": -1},
        {"horizon_ticks": 10, "delay_min_ticks": 5, "delay_max_ticks": 4},
        {"horizon_ticks": 10, "episode_min_ticks": 0},
    ],
)
def test_a_spec_that_makes_no_sense_is_refused(fields: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        DisturbanceSpec(**fields)


def test_a_schedule_that_is_not_in_order_is_refused() -> None:
    spec = DisturbanceSpec(horizon_ticks=100)
    corridor = corridor_key(*next((e.source, e.target) for e in GRAPH.edges))

    with pytest.raises(ValueError, match="overlap"):
        DisturbanceSchedule(
            0, spec, (DelayEpisode(corridor, 0, 0, 10), DelayEpisode(corridor, 1, 5, 20)), ()
        )
    with pytest.raises(ValueError, match="time order"):
        DisturbanceSchedule(0, spec, (), (Breakdown(ROBOTS[0], 50, 5), Breakdown(ROBOTS[0], 10, 5)))


def test_a_fingerprint_is_equal_exactly_for_equal_data() -> None:
    schedule = generate_disturbances(GRAPH, ROBOTS, SPEC, seed=2)

    assert fingerprint(schedule.to_dict()) == fingerprint(
        generate_disturbances(GRAPH, ROBOTS, SPEC, seed=2).to_dict()
    )
    assert fingerprint(schedule.to_dict()) != fingerprint(
        generate_disturbances(GRAPH, ROBOTS, SPEC, seed=5).to_dict()
    )


# --- runs that follow a schedule -----------------------------------------------------------------


def _scenario(seed: int) -> Scenario:
    spec = ScenarioSpec(robots=3, orders_per_hour=200.0, duration_s=400.0)
    return generate_scenario(GRAPH, spec, seed=seed)


def _run(
    seed: int, policy: str, recovery: Recovery
) -> tuple[SimulationResult, DisturbanceSchedule]:
    scenario = _scenario(seed)
    schedule = generate_disturbances(GRAPH, [r.robot_id for r in scenario.robots], SPEC, seed)
    result = simulate(
        scenario, POLICIES[policy](), traffic=True, disturbances=schedule, recovery=recovery
    )
    return result, schedule


CASES = [
    (seed, policy, recovery)
    for seed in range(3)
    for policy, recovery in (
        ("greedy", Recovery.WAIT),
        ("insertion-fair", Recovery.REPLAN),
        ("greedy", Recovery.RETIME),
    )
]


@pytest.mark.parametrize(("seed", "policy", "recovery"), CASES)
def test_a_run_meets_exactly_what_the_schedule_holds(
    seed: int, policy: str, recovery: Recovery
) -> None:
    result, schedule = _run(seed, policy, recovery)
    assert result.all_delivered
    delays = {(e.robot_id, e.tick): e for e in result.robot_events if e.kind is K.DELAY}
    broken = {
        robot_id: [
            e for e in result.robot_events if e.kind is K.BREAKDOWN and e.robot_id == robot_id
        ]
        for robot_id in result.trajectories
    }
    assert delays and any(broken.values())

    # Every corridor entry of the run, against the schedule: delayed exactly when it should be.
    entries = 0
    for robot_id, path in result.trajectories.items():
        due = list(schedule.breakdowns_of(robot_id))
        events = iter(broken[robot_id])
        for visit, following in zip(path.visits, path.visits[1:], strict=False):
            entries += 1
            expected = schedule.delay_ticks(robot_id, visit.node, following.node, visit.depart)
            event = delays.pop((robot_id, visit.depart), None)
            assert (event.ticks if event else 0) == expected
            assert event is None or (event.node, event.target) == (visit.node, following.node)
            # The breakdowns scheduled up to this entry and not yet met: it meets the earliest.
            met = [b for b in due if b.tick <= visit.depart]
            if met:
                breakdown = next(events)
                assert (breakdown.tick, breakdown.ticks) == (visit.depart, met[0].repair_ticks)
                due = due[len(met) :]
        assert next(events, None) is None  # no breakdown without a scheduled one
    assert not delays  # no delay without a corridor entry in an episode
    assert entries > 100
    assert result.breakdowns == sum(len(events) for events in broken.values())


def test_two_policies_on_one_schedule_face_the_same_conditions() -> None:
    (greedy, schedule), (fair, same) = (
        _run(1, policy, Recovery.REPLAN) for policy in ("greedy", "insertion-fair")
    )

    assert schedule == same
    assert greedy.scenario.orders == fair.scenario.orders
    assert greedy.trajectories != fair.trajectories  # the policies do differ
    # Each robot's breakdowns have the scheduled repair times, in order, under both policies.
    for result in (greedy, fair):
        for robot_id in result.trajectories:
            scheduled = [b.repair_ticks for b in schedule.breakdowns_of(robot_id)]
            met = [
                e.ticks
                for e in result.robot_events
                if e.kind is K.BREAKDOWN and e.robot_id == robot_id
            ]
            remaining: Any = iter(scheduled)
            assert all(ticks in remaining for ticks in met)  # a subsequence


def test_a_run_with_a_schedule_is_repeatable_and_has_no_conflicts() -> None:
    first, _ = _run(2, "insertion-fair", Recovery.REPLAN)
    again, _ = _run(2, "insertion-fair", Recovery.REPLAN)

    assert first.robot_events == again.robot_events
    assert first.trajectories == again.trajectories


def test_a_schedule_replaces_the_other_models_and_needs_traffic() -> None:
    scenario = _scenario(0)
    schedule = generate_disturbances(GRAPH, ROBOTS, SPEC, seed=0)
    greedy = POLICIES["greedy"]()

    with pytest.raises(ValueError, match="needs traffic"):
        simulate(scenario, greedy, disturbances=schedule)
    with pytest.raises(ValueError, match="replaces"):
        simulate(scenario, greedy, traffic=True, disturbances=schedule, delays=DelayModel(0.1))
    empty = generate_disturbances(GRAPH, ROBOTS, DisturbanceSpec(horizon_ticks=10), seed=0)
    with pytest.raises(ValueError, match="needs breakdowns"):
        simulate(scenario, greedy, traffic=True, disturbances=empty, recovery=Recovery.REPLAN)
    # An empty schedule is a run without disturbances.
    plain = simulate(scenario, greedy, traffic=True)
    assert simulate(scenario, greedy, traffic=True, disturbances=empty).log == plain.log
