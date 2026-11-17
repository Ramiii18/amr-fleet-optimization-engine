"""Tests for the experiment lab (D36): the statistics by hand, experiment files, and a small
experiment from file to results."""

import copy
import dataclasses
import json
import math
import random
from pathlib import Path
from typing import Any

import pytest

from amr_optimizer.lab import (
    ExperimentValidationError,
    UnpairedRunsError,
    analyse,
    bootstrap_interval,
    experiment_from_dict,
    holm,
    load_experiment,
    paired_contrast,
    report,
    results_to_dict,
    run_experiment,
    write_results,
)
from amr_optimizer.lab.__main__ import main
from amr_optimizer.layout import NotWellFormedError

ROOT = Path(__file__).resolve().parents[3]
COMMITTED = ROOT / "experiments" / "dispatch_disturbed.json"

SMALL: dict[str, Any] = {
    "format": "amr-experiment",
    "version": 1,
    "name": "small",
    "families": [
        {
            "name": "small-3",
            "map": "small",
            "robots": 3,
            "orders_per_hour": 200.0,
            "duration_s": 300.0,
        }
    ],
    "policies": ["greedy", "insertion-fair"],
    "baseline": "greedy",
    "seeds": [0, 1, 2],
    "disturbances": {
        "model": "schedule",
        "delay_share": 0.15,
        "delay_min_s": 1.0,
        "delay_max_s": 4.0,
        "breakdown_gap_s": 200.0,
        "repair_min_s": 20.0,
        "repair_max_s": 90.0,
        "recovery": "replan",
    },
    "kpis": ["completion_max_s"],
    "margin": 5.0,
    "resamples": 200,
}


# --- statistics ----------------------------------------------------------------------------------


def test_a_contrast_by_hand() -> None:
    # Differences -2, -4, -6, -8: mean -5, standard deviation sqrt(20/3), t(3, 0.975) = 3.1824.
    contrast = paired_contrast(
        "completion_mean_s", "b", "a", [8, 16, 24, 32], [10, 20, 30, 40], margin=1.0, resamples=500
    )
    half = 3.182446 * math.sqrt(20 / 3) / 2

    assert (contrast.difference, contrast.seeds) == (-5.0, 4)
    assert (contrast.low, contrast.high) == pytest.approx((-5 - half, -5 + half), abs=1e-4)
    assert (contrast.wins, contrast.losses, contrast.ties) == (4, 0, 0)
    assert contrast.signed_rank_p == pytest.approx(0.125)  # the smallest possible with 4 pairs
    assert contrast.correlation == pytest.approx(1.0)
    # var(d) = 20/3; var(values) = 320/3; var(baseline) = 500/3.
    assert contrast.variance_ratio == pytest.approx(20 / 820)
    assert contrast.verdict == "better"
    assert contrast.low < contrast.bootstrap_low < contrast.bootstrap_high < contrast.high + 5


def test_more_is_better_for_throughput_and_ties_are_counted() -> None:
    contrast = paired_contrast(
        "throughput_per_h", "b", "a", [6, 7, 9, 8, 10, 9], [4, 5, 6, 6, 7, 9]
    )

    assert (contrast.wins, contrast.losses, contrast.ties) == (5, 0, 1)
    assert contrast.verdict == "better"
    worse = paired_contrast("completion_mean_s", "b", "a", [5, 7, 9, 11], [4, 5, 6, 9])
    assert worse.verdict == "worse"


def test_equivalent_needs_the_whole_interval_inside_the_margin() -> None:
    values, base = [10.0, 11.0, 9.5, 10.5], [10.2, 10.6, 9.9, 10.4]

    assert paired_contrast("completion_mean_s", "b", "a", values, base, margin=2.0).verdict == (
        "equivalent"
    )
    # The same data without a margin, or with one the interval does not fit in: no verdict.
    assert paired_contrast("completion_mean_s", "b", "a", values, base).verdict == "inconclusive"
    assert paired_contrast("completion_mean_s", "b", "a", values, base, margin=0.1).verdict == (
        "inconclusive"
    )


def test_one_seed_gives_a_difference_and_no_interval() -> None:
    contrast = paired_contrast("completion_mean_s", "b", "a", [3.0], [5.0])

    assert contrast.difference == -2.0 and contrast.verdict == "inconclusive"
    assert math.isnan(contrast.low) and math.isnan(contrast.p_value)
    assert contrast.to_dict()["low"] is None


def test_a_run_without_a_value_is_an_error_not_a_number() -> None:
    with pytest.raises(ValueError, match="no value"):
        paired_contrast("completion_mean_s", "b", "a", [1.0, math.nan], [1.0, 2.0])


def test_holm_by_hand() -> None:
    # Sorted 0.01, 0.02, 0.03, 0.04 with multipliers 4, 3, 2, 1; then never decreasing.
    assert holm([0.04, 0.01, 0.03, 0.02]) == pytest.approx([0.06, 0.04, 0.06, 0.06])
    assert holm([0.5, 0.9]) == [1.0, 1.0]
    assert holm([]) == []


def test_the_bootstrap_is_repeatable_and_covers_the_mean() -> None:
    diffs = [-3.0, -1.0, -4.0, -2.0, -5.0, 0.5]
    first = bootstrap_interval(diffs, confidence=0.95, resamples=2000, rng=random.Random(1))

    assert first == bootstrap_interval(diffs, confidence=0.95, resamples=2000, rng=random.Random(1))
    assert first[0] < sum(diffs) / len(diffs) < first[1]


# --- experiment files ----------------------------------------------------------------------------


def test_the_committed_experiment_is_read_and_written_back() -> None:
    experiment = load_experiment(COMMITTED)

    assert experiment.primary_kpi == "completion_mean_s" and len(experiment.seeds) == 20
    assert experiment_from_dict(experiment.to_dict()) == experiment


def _change(path: list[Any], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(SMALL)
    target: Any = data
    for key in path[:-1]:
        target = target[key]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (_change(["colour"], "red"), r"unknown field\(s\) \['colour'\]"),
        (_change(["seeds"], None), r"missing field\(s\) \['seeds'\]"),
        (_change(["version"], 3), "expected format"),
        (_change(["seeds"], [0, 0]), "distinct"),
        (_change(["seeds"], [0, 1.5]), r"seeds\[1\]: expected int"),
        (_change(["policies"], ["greedy"]), "at least two"),
        (_change(["policies"], ["greedy", "magic"]), "unknown policy 'magic'"),
        (_change(["baseline"], "cpsat"), "not among the policies"),
        (_change(["kpis"], ["speed"]), "unknown KPI 'speed'"),
        (_change(["kpis"], ["completion_mean_s"]), "primary KPI not among"),
        (_change(["families", 0, "map"], "huge"), "unknown map 'huge'"),
        (_change(["families", 0, "robots"], "3"), r"families\[0\].robots: expected int"),
        (_change(["families", 0, "depot"], "A"), r"families\[0\]: unknown field"),
        (_change(["disturbances", "model"], "chaos"), "disturbances.model"),
        (_change(["disturbances", "delay_share"], 1.5), "shares"),
        (_change(["disturbances", "repair_max_s"], 1.0), "repair_min_s <= repair_max_s"),
        (_change(["disturbances", "recovery"], "pray"), "unknown recovery"),
        (_change(["margin"], -1.0), "margin"),
        ([], "expected an object"),
    ],
)
def test_a_broken_experiment_is_refused_with_the_reason(data: object, reason: str) -> None:
    with pytest.raises(ExperimentValidationError, match=reason):
        experiment_from_dict(data)


# --- a small experiment, from file to results ----------------------------------------------------


@pytest.fixture(scope="module")
def small() -> tuple[Any, list[dict[str, object]]]:
    experiment = experiment_from_dict(SMALL)
    return experiment, run_experiment(experiment)


def test_every_policy_runs_every_seed_under_the_same_conditions(small: Any) -> None:
    experiment, rows = small

    assert [(r["seed"], r["policy"]) for r in rows] == [
        (seed, policy) for seed in (0, 1, 2) for policy in ("greedy", "insertion-fair")
    ]
    for seed in (0, 1, 2):
        greedy, fair = (r for r in rows if r["seed"] == seed)
        assert greedy["fingerprint"] == fair["fingerprint"]  # whatever the policy
        assert greedy["completion_mean_s"] != fair["completion_mean_s"]
    assert len({r["fingerprint"] for r in rows}) == 3  # and different between seeds
    assert all(r["all_delivered"] and not r["beyond_horizon"] for r in rows)
    assert all(r["delays"] and r["breakdowns"] for r in rows)
    assert all(r["breakdowns"] <= r["breakdowns_scheduled"] for r in rows)


def test_the_rows_do_not_depend_on_the_number_of_processes(small: Any) -> None:
    experiment, rows = small

    assert run_experiment(experiment, workers=2) == rows


def test_the_analysis_pairs_by_seed_and_adjusts_the_primary_tests_only(small: Any) -> None:
    experiment, rows = small
    primary, other = analyse(experiment, rows)

    by_policy = {
        policy: [float(r["completion_mean_s"]) for r in rows if r["policy"] == policy]
        for policy in experiment.policies
    }
    expected = sum(
        f - g for f, g in zip(by_policy["insertion-fair"], by_policy["greedy"], strict=True)
    )
    assert primary.difference == pytest.approx(expected / 3)
    assert (primary.kpi, primary.variant, primary.seeds) == (
        "completion_mean_s",
        "small-3/insertion-fair",
        3,
    )
    assert primary.holm_p == pytest.approx(primary.p_value)  # one primary test
    assert (other.kpi, other.holm_p, other.margin) == ("completion_max_s", None, 0.0)
    assert "small-3/insertion-fair | completion_mean_s (primary)" in report(
        experiment, rows, [primary, other]
    )


def test_runs_that_faced_different_conditions_are_not_compared(small: Any) -> None:
    experiment, rows = small
    tampered = [dict(row) for row in rows]
    tampered[1]["fingerprint"] = "0" * 16

    with pytest.raises(UnpairedRunsError, match="different conditions"):
        analyse(experiment, tampered)
    with pytest.raises(UnpairedRunsError, match="no run"):
        analyse(experiment, rows[:-1])


def test_the_independent_model_and_no_disturbances_run_too() -> None:
    experiment = experiment_from_dict(SMALL)
    for model in ("independent", "none"):
        d = dataclasses.replace(experiment.disturbances, model=model, breakdown_share=0.03)
        rows = run_experiment(dataclasses.replace(experiment, seeds=(0,), disturbances=d))
        assert rows[0]["fingerprint"] == rows[1]["fingerprint"]
        assert bool(rows[0]["breakdowns"]) == (model == "independent")
        assert rows[0]["breakdowns_scheduled"] is None


def test_the_command_writes_results_that_hold_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, out = tmp_path / "experiment.json", tmp_path / "results.json"
    source.write_text(json.dumps(SMALL | {"seeds": [0, 1]}), encoding="utf-8")

    assert main([str(source), "--out", str(out)]) == 0
    assert "| small-3/insertion-fair | completion_mean_s (primary)" in capsys.readouterr().out
    results = json.loads(out.read_text(encoding="utf-8"))
    assert results["format"] == "amr-experiment-results"
    assert experiment_from_dict(results["experiment"]).seeds == (0, 1)
    assert len(results["rows"]) == 4 and len(results["analysis"]) == 2
    # A broken file is reported, not a traceback.
    source.write_text("{", encoding="utf-8")
    assert main([str(source)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_results_without_a_number_are_written_as_null(tmp_path: Path) -> None:
    experiment = experiment_from_dict(SMALL)
    data = results_to_dict(experiment, [{"x": math.nan}], [], commit=None)
    write_results(tmp_path / "r.json", data)

    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["rows"] == [{"x": None}]


def test_a_fleet_too_large_for_its_map_is_refused_before_any_run(monkeypatch: Any) -> None:
    """The small preset has 19 holding spots: 6 robots fit (3N - 1 = 17), 7 do not (20)."""
    import amr_optimizer.lab.runner as runner

    def no_run(*_: object) -> object:
        raise AssertionError("a run started")

    monkeypatch.setattr(runner, "_run", no_run)
    families = [dict(SMALL["families"][0], robots=7)]
    experiment = experiment_from_dict(dict(SMALL, families=families))
    with pytest.raises(NotWellFormedError, match="7 robots need at least 20 holding spots"):
        run_experiment(experiment)
