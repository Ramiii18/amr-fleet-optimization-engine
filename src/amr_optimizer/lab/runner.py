"""Run an experiment and analyse it (D36).

Every policy runs every seed of every family. A run gives one row: its KPIs, what disturbed
it, and a **fingerprint** of everything exogenous (orders, start positions, disturbances).
Rows of one family and seed must have equal fingerprints whatever the policy: that is checked,
and it is what makes the paired differences meaningful.

Every row also carries a **map hash**: a hash of the compiled map's content, so that a result can
never be attributed to the wrong map, whatever the map file was called (D40). It is kept apart from
the fingerprint, which therefore means what it meant before.
"""

import json
import math
import random
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from amr_optimizer.dispatch import POLICIES
from amr_optimizer.domain import ChargingPolicy
from amr_optimizer.lab.analysis import Contrast, holm, paired_contrast
from amr_optimizer.lab.experiment import KPI_NAMES, Battery, Disturbances, Experiment, Family
from amr_optimizer.layout import load_map_file, require_well_formed
from amr_optimizer.metrics import compute_charging_kpis, compute_kpis
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
from amr_optimizer.warehouse import (
    PRESETS,
    NodeId,
    NodeKind,
    WarehouseGraph,
    generate_grid_warehouse,
)
from amr_optimizer.warehouse.io import graph_to_dict

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


def family_graph(source: str | Path) -> WarehouseGraph:
    """The map of a family: a preset by name, or a map or layout file by path."""
    if isinstance(source, str) and source in PRESETS:
        return generate_grid_warehouse(PRESETS[source])
    return load_map_file(source)


def map_hash(graph: WarehouseGraph) -> str:
    """A short hash of the map's content: its nodes, kinds, coordinates and edges, in order."""
    return fingerprint(graph_to_dict(graph))


def _place(graph: WarehouseGraph, node: NodeId) -> str:
    """A node as candidate layouts share it: floor cells and storage by id (the same in every
    candidate, D38), stations, chargers and parking by kind and rank (their cells may move)."""
    kind = graph.node(node).kind
    if kind in (NodeKind.INTERSECTION, NodeKind.STORAGE):
        return node
    ranked = sorted(n.id for n in graph.nodes_of_kind(kind))
    return f"{kind.value}#{ranked.index(node)}"


def pairing_key(scenario: Scenario, conditions: object, *extra: object) -> str:
    """A hash of what two layouts of one replication should share (D41): the orders by release
    tick, storage location and station rank; the robots by id and start; and the disturbances
    on the floor and at storage. Equal keys mean that runs on two layouts can be paired."""
    graph = scenario.graph
    shared = conditions
    if isinstance(conditions, dict) and "delay_episodes" in conditions:
        fixed = {NodeKind.INTERSECTION, NodeKind.STORAGE}
        shared = conditions | {
            "delay_episodes": [
                episode
                for episode in conditions["delay_episodes"]
                if {graph.node(episode[0]).kind, graph.node(episode[1]).kind} <= fixed
            ]
        }
    return fingerprint(
        [
            [o.id, o.release_tick, _place(graph, o.pickup), _place(graph, o.dropoff)]
            for o in scenario.orders
        ],
        [[r.robot_id, _place(graph, r.node)] for r in scenario.robots],
        shared,
        *extra,
    )


def run_one(
    family: Family,
    d: Disturbances,
    seed: int,
    policy: str,
    source: str | Path | None = None,
    *,
    layout: str | None = None,
    battery: Battery | None = None,
) -> Row:
    """One run: ``policy`` on replication ``seed`` of ``family``, whose map is ``source`` (by
    default the family's ``map``, which must then be a preset or an absolute path). ``layout``
    names the layout the map belongs to, in an experiment that compares layouts."""
    spec = ScenarioSpec(
        robots=family.robots, orders_per_hour=family.orders_per_hour, duration_s=family.duration_s
    )
    graph = family_graph(family.map if source is None else source)
    scenario = generate_scenario(graph, spec, seed=seed)
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
        battery=None if battery is None else battery.spec(),
        battery_seed=seed,
        charging=ChargingPolicy.WHEN_LOW if battery is None else ChargingPolicy(battery.charging),
    )
    kpis = asdict(compute_kpis(result))
    events = result.robot_events
    delay_events = [e for e in events if e.kind is RobotEventKind.DELAY]
    scheduled = None
    if schedule is not None:
        scheduled = sum(b.tick <= result.end_tick for b in schedule.breakdowns)
    # A battery is part of the conditions; without one the fingerprint is as it always was.
    extra = () if battery is None else ({"battery": asdict(battery), "seed": seed},)
    row: Row = {"family": family.name}
    if layout is not None:
        row["layout"] = layout
    row |= {
        "seed": seed,
        "policy": policy,
        "fingerprint": fingerprint(
            [[o.id, o.release_tick, o.pickup, o.dropoff] for o in scenario.orders],
            [[r.robot_id, r.node] for r in scenario.robots],
            conditions,
            *extra,
        ),
        "map_hash": map_hash(graph),
        "pairing": pairing_key(scenario, conditions, *extra),
        **{name: kpis[name] for name in KPI_NAMES},
    }
    if battery is not None:
        row |= asdict(compute_charging_kpis(result))
    return row | {
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


Job = tuple[Family, Disturbances, int, str, str | Path, str | None, Battery | None]


def _run(job: Job) -> Row:
    family, d, seed, policy, source, layout, battery = job
    return run_one(family, d, seed, policy, source, layout=layout, battery=battery)


def _maps(experiment: Experiment) -> list[tuple[Family, str | None, str | Path]]:
    """Every (family, layout name, map source) of ``experiment``, in run order."""
    if experiment.compares_layouts:
        return [
            (family, layout.name, experiment.layout_source(layout))
            for family in experiment.families
            for layout in experiment.layouts
        ]
    return [(family, None, experiment.map_source(family)) for family in experiment.families]


def run_experiment(experiment: Experiment, *, workers: int = 1) -> list[Row]:
    """Every run of ``experiment``, in the order family, layout (if it compares layouts), seed,
    policy. With ``workers`` > 1 the runs are spread over that many processes; the rows are the
    same."""
    maps = _maps(experiment)
    battery = experiment.battery
    for family, _, source in maps:  # refuse an unfit map before any run (D39)
        require_well_formed(
            family_graph(source),
            robots=family.robots,
            battery=None if battery is None else battery.spec(),
        )
    jobs: list[Job] = [
        (family, experiment.disturbances, seed, policy, source, layout, battery)
        for family, layout, source in maps
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
    exploratory. Raises `UnpairedRunsError` if the runs of a seed differ in fingerprint.

    In an experiment that compares layouts: for every family and policy, the experiment's named
    contrasts (pairs of layouts), then every other layout against the baseline layout, paired by
    seed. With named contrasts, only those are primary tests (Holm over them); without, every
    layout against the baseline is. The runs of one seed must share their pairing key across
    layouts (D41), and their fingerprint across policies on one layout."""
    if experiment.compares_layouts:
        layout_contrasts, primary = _layout_contrasts(experiment, rows)
        return _with_holm(experiment, layout_contrasts, primary)
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
    return _with_holm(experiment, contrasts)


def _layout_contrasts(
    experiment: Experiment, rows: Sequence[Mapping[str, object]]
) -> tuple[list[Contrast], set[int]]:
    """The contrasts of a layout comparison, and the indices of the primary tests among them."""
    by_key = {
        (str(r["family"]), str(r["layout"]), int(str(r["seed"])), str(r["policy"])): r for r in rows
    }
    layouts = [layout.name for layout in experiment.layouts]

    def row(family: str, layout: str, seed: int, policy: str) -> Mapping[str, object]:
        try:
            return by_key[family, layout, seed, policy]
        except KeyError:
            raise UnpairedRunsError(f"no run for {(family, layout, seed, policy)}") from None

    for family in experiment.families:
        for seed in experiment.seeds:
            for layout in layouts:
                prints = {
                    row(family.name, layout, seed, p)["fingerprint"] for p in experiment.policies
                }
                if len(prints) != 1:
                    raise UnpairedRunsError(
                        f"family {family.name!r}, layout {layout!r}, seed {seed}: the policies "
                        "faced different conditions"
                    )
            for policy in experiment.policies:
                keys = {row(family.name, layout, seed, policy)["pairing"] for layout in layouts}
                if len(keys) != 1:
                    raise UnpairedRunsError(
                        f"family {family.name!r}, seed {seed}, policy {policy!r}: the layouts "
                        "faced different conditions (their pairing keys differ: orders, starts "
                        "or disturbances are not the same by index)"
                    )
    named = list(experiment.contrasts)
    pairs = named + [
        (layout, experiment.baseline)
        for layout in layouts
        if layout != experiment.baseline and (layout, experiment.baseline) not in named
    ]
    contrasts: list[Contrast] = []
    primary: set[int] = set()
    rng = random.Random(0)
    for family in experiment.families:
        for policy in experiment.policies:
            for layout, reference in pairs:
                for kpi in (experiment.primary_kpi, *experiment.kpis):
                    values, base = (
                        [
                            float(str(row(family.name, name, s, policy)[kpi]))
                            for s in experiment.seeds
                        ]
                        for name in (layout, reference)
                    )
                    if kpi == experiment.primary_kpi and (
                        not named or (layout, reference) in named
                    ):
                        primary.add(len(contrasts))
                    contrasts.append(
                        paired_contrast(
                            kpi,
                            f"{family.name}/{policy}/{layout}",
                            reference,
                            values,
                            base,
                            margin=experiment.margin if kpi == experiment.primary_kpi else 0.0,
                            confidence=experiment.confidence,
                            resamples=experiment.resamples,
                            rng=rng,
                        )
                    )
    return contrasts, primary


def _with_holm(
    experiment: Experiment, contrasts: list[Contrast], chosen: set[int] | None = None
) -> list[Contrast]:
    """The primary tests with Holm-adjusted p-values over all of them: by default every test of
    the primary KPI, or else the ``chosen`` ones."""
    primary = [
        i
        for i, c in enumerate(contrasts)
        if (c.kpi == experiment.primary_kpi if chosen is None else i in chosen)
    ]
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
        # Version 2 for layout comparisons: their variants name a layout too (family/policy/layout).
        "version": 2 if experiment.compares_layouts else RESULTS_VERSION,
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
    layouts = experiment.compares_layouts
    first, mean = (
        ("Family / policy / layout", "Layout") if layouts else ("Family / policy", "Policy")
    )
    lines = [
        f"**{experiment.name}**: {len(experiment.seeds)} seeds, disturbances: {d.model}, "
        f"baseline {experiment.baseline}, primary KPI {experiment.primary_kpi}, "
        f"margin {experiment.margin:g}, {experiment.confidence:.0%} intervals.",
        "",
        f"| {first} | KPI | {mean} | Baseline | Difference [t interval] | Bootstrap | "
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
