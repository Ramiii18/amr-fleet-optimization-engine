"""Run an experiment and analyse it (D36).

Every policy runs every seed of every family. A run gives one row: its KPIs, what disturbed
it, and a **fingerprint** of everything exogenous (orders, start positions, disturbances).
Rows of one family and seed must have equal fingerprints whatever the policy: that is checked,
and it is what makes the paired differences meaningful.
"""

import json
import math
import random
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.lab.analysis import Contrast, holm, paired_contrast
from amr_optimizer.lab.experiment import KPI_NAMES, Disturbances, Experiment, Family
from amr_optimizer.layout import require_well_formed
from amr_optimizer.metrics import compute_kpis
from amr_optimizer.scenario import Scenario, ScenarioSpec, generate_scenario
from amr_optimizer.sim import RobotEventKind, simulate
from amr_optimizer.traffic import (
    NO_DELAYS,
    DelayModel,
    DisturbanceSchedule,
    DisturbanceSpec,
    Recovery,
    fingerprint,
    generate_disturbances,
)
from amr_optimizer.warehouse import PRESETS, generate_grid_warehouse

RESULTS_FORMAT = "amr-experiment-results"
RESULTS_VERSION = 1
Row = dict[str, object]


def _ticks(seconds: float, tick_s: float) -> int:
    return max(1, round(seconds / tick_s))


def schedule_for(
    scenario: Scenario, family: Family, d: Disturbances, seed: int
) -> DisturbanceSchedule:
    """The schedule of one replication. Its horizon is four order windows and an hour: far
    beyond a run that keeps up with its orders. A run that lasts longer is flagged."""
    tick_s = scenario.time_model.tick_s
    spec = DisturbanceSpec(
        horizon_ticks=_ticks(4 * family.duration_s + 3600, tick_s),
        delay_share=d.delay_share,
        episode_min_ticks=_ticks(d.episode_min_s, tick_s),
        episode_max_ticks=_ticks(d.episode_max_s, tick_s),
        delay_min_ticks=_ticks(d.delay_min_s, tick_s),
        delay_max_ticks=_ticks(d.delay_max_s, tick_s),
        breakdown_gap_ticks=_ticks(d.breakdown_gap_s, tick_s) if d.breakdown_gap_s else 0,
        repair_min_ticks=_ticks(d.repair_min_s, tick_s),
        repair_max_ticks=_ticks(d.repair_max_s, tick_s),
    )
    return generate_disturbances(scenario.graph, [r.robot_id for r in scenario.robots], spec, seed)


def run_one(family: Family, d: Disturbances, seed: int, policy: str) -> Row:
    """One run: ``policy`` on replication ``seed`` of ``family``."""
    spec = ScenarioSpec(
        robots=family.robots, orders_per_hour=family.orders_per_hour, duration_s=family.duration_s
    )
    scenario = generate_scenario(generate_grid_warehouse(PRESETS[family.map]), spec, seed=seed)
    tick_s = scenario.time_model.tick_s
    conditions: object = asdict(d) | {"seed": seed}
    schedule = None
    delays = breakdowns = NO_DELAYS
    if d.model == "schedule":
        schedule = schedule_for(scenario, family, d, seed)
        conditions = schedule.to_dict()
    elif d.model == "independent":
        if d.delay_share:
            delays = DelayModel(
                d.delay_share, _ticks(d.delay_min_s, tick_s), _ticks(d.delay_max_s, tick_s)
            )
        if d.breakdown_share:
            breakdowns = DelayModel(
                d.breakdown_share, _ticks(d.repair_min_s, tick_s), _ticks(d.repair_max_s, tick_s)
            )
    broken = breakdowns.probability > 0 or (schedule is not None and bool(schedule.breakdowns))
    result = simulate(
        scenario,
        POLICIES[policy](),
        traffic=True,
        delays=delays,
        delay_seed=seed,
        breakdowns=breakdowns,
        breakdown_seed=seed,
        recovery=Recovery(d.recovery) if broken else Recovery.WAIT,
        disturbances=schedule,
    )
    kpis = asdict(compute_kpis(result))
    events = result.robot_events
    delay_events = [e for e in events if e.kind is RobotEventKind.DELAY]
    scheduled = None
    if schedule is not None:
        scheduled = sum(b.tick <= result.end_tick for b in schedule.breakdowns)
    return {
        "family": family.name,
        "seed": seed,
        "policy": policy,
        "fingerprint": fingerprint(
            [[o.id, o.release_tick, o.pickup, o.dropoff] for o in scenario.orders],
            [[r.robot_id, r.node] for r in scenario.robots],
            conditions,
        ),
        **{name: kpis[name] for name in KPI_NAMES},
        "all_delivered": result.all_delivered,
        "end_tick": result.end_tick,
        "delays": len(delay_events),
        "delay_ticks": sum(e.ticks or 0 for e in delay_events),
        "breakdowns": result.breakdowns,
        "breakdown_ticks": result.breakdown_ticks,
        "replans": result.replans,
        # Scheduled up to the end of the run. More than were met when a robot stood still
        # through several of them, or the run ended before its next corridor entry.
        "breakdowns_scheduled": scheduled,
        "beyond_horizon": schedule is not None and result.end_tick > schedule.spec.horizon_ticks,
    }


def _run(args: tuple[Family, Disturbances, int, str]) -> Row:
    return run_one(*args)


def run_experiment(experiment: Experiment, *, workers: int = 1) -> list[Row]:
    """Every run of ``experiment``, in the order family, seed, policy. With ``workers`` > 1 the
    runs are spread over that many processes; the rows are the same."""
    for family in experiment.families:  # refuse an unfit map before any run (D39)
        require_well_formed(generate_grid_warehouse(PRESETS[family.map]), robots=family.robots)
    jobs = [
        (family, experiment.disturbances, seed, policy)
        for family in experiment.families
        for seed in experiment.seeds
        for policy in experiment.policies
    ]
    if workers <= 1:
        return [_run(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run, jobs))


class UnpairedRunsError(ValueError):
    """Runs that should have faced the same conditions did not, or a run is missing."""


def analyse(experiment: Experiment, rows: Sequence[Mapping[str, object]]) -> list[Contrast]:
    """For every family, every policy against the baseline on every KPI. The primary KPI's
    tests carry a Holm-adjusted p-value over all families and policies; the rest is
    exploratory. Raises `UnpairedRunsError` if the runs of a seed differ in fingerprint."""
    by_key = {(str(r["family"]), int(str(r["seed"])), str(r["policy"])): r for r in rows}
    contrasts: list[Contrast] = []
    rng = random.Random(0)
    for family in experiment.families:
        for seed in experiment.seeds:
            try:
                prints = {
                    by_key[family.name, seed, policy]["fingerprint"]
                    for policy in experiment.policies
                }
            except KeyError as error:
                raise UnpairedRunsError(f"no run for {error.args[0]}") from error
            if len(prints) != 1:
                raise UnpairedRunsError(
                    f"family {family.name!r}, seed {seed}: the policies faced different conditions"
                )
        for policy in experiment.policies:
            if policy == experiment.baseline:
                continue
            for kpi in (experiment.primary_kpi, *experiment.kpis):
                values, base = (
                    [float(str(by_key[family.name, seed, name][kpi])) for seed in experiment.seeds]
                    for name in (policy, experiment.baseline)
                )
                contrasts.append(
                    paired_contrast(
                        kpi,
                        f"{family.name}/{policy}",
                        experiment.baseline,
                        values,
                        base,
                        margin=experiment.margin if kpi == experiment.primary_kpi else 0.0,
                        confidence=experiment.confidence,
                        resamples=experiment.resamples,
                        rng=rng,
                    )
                )
    primary = [i for i, c in enumerate(contrasts) if c.kpi == experiment.primary_kpi]
    if all(not math.isnan(contrasts[i].p_value) for i in primary):
        adjusted = holm([contrasts[i].p_value for i in primary])
        for i, p in zip(primary, adjusted, strict=True):
            fields = {name: getattr(contrasts[i], name) for name in Contrast.__slots__}
            contrasts[i] = Contrast(**(fields | {"holm_p": p}))
    return contrasts


def results_to_dict(
    experiment: Experiment,
    rows: Sequence[Row],
    contrasts: Sequence[Contrast],
    *,
    commit: str | None,
) -> dict[str, object]:
    """Everything needed to check or repeat the experiment: its file, every run, the analysis."""
    return {
        "format": RESULTS_FORMAT,
        "version": RESULTS_VERSION,
        "source": "Synthetic simulation output of the AMR Fleet Optimization Engine.",
        "commit": commit,
        "experiment": experiment.to_dict(),
        "rows": [dict(row) for row in rows],
        "analysis": [contrast.to_dict() for contrast in contrasts],
    }


def write_results(path: str | Path, data: Mapping[str, object]) -> None:
    def clean(value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    Path(path).write_text(
        json.dumps(clean(dict(data)), indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )


def report(
    experiment: Experiment, rows: Sequence[Mapping[str, object]], contrasts: Sequence[Contrast]
) -> str:
    """The analysis as Markdown tables."""
    d = experiment.disturbances
    lines = [
        f"**{experiment.name}**: {len(experiment.seeds)} seeds, disturbances: {d.model}, "
        f"baseline {experiment.baseline}, primary KPI {experiment.primary_kpi}, "
        f"margin {experiment.margin:g}, {experiment.confidence:.0%} intervals.",
        "",
        "| Family / policy | KPI | Policy | Baseline | Difference [t interval] | Bootstrap | "
        "Wins-losses-ties | Signed-rank p | Holm p | Correlation | Variance ratio | Verdict |",
        "|---|---|---:|---:|---|---|---|---:|---:|---:|---:|---|",
    ]
    for c in contrasts:
        primary = c.kpi == experiment.primary_kpi
        lines.append(
            f"| {c.variant} | {c.kpi}{' (primary)' if primary else ''} | {c.mean_variant:.1f} "
            f"| {c.mean_baseline:.1f} | {c.difference:+.1f} [{c.low:+.1f}, {c.high:+.1f}] "
            f"| [{c.bootstrap_low:+.1f}, {c.bootstrap_high:+.1f}] | {c.wins}-{c.losses}-{c.ties} "
            f"| {c.signed_rank_p:.3f} | {'' if c.holm_p is None else f'{c.holm_p:.3f}'} "
            f"| {c.correlation:.2f} | {c.variance_ratio:.2f} | {c.verdict} |"
        )
    undelivered = sum(not r["all_delivered"] for r in rows)
    beyond = sum(bool(r["beyond_horizon"]) for r in rows)
    lines += [
        "",
        f"Runs: {len(rows)}; with undelivered orders: {undelivered}; "
        f"longer than their disturbance schedule: {beyond}.",
    ]
    return "\n".join(lines)
