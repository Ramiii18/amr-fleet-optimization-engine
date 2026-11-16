"""Paired comparisons of policies over seeds, with the evidence a critical reader asks for (D36).

The unit of replication is the seed: orders within one run share robots and congestion, so
they are not independent observations. Every statistic here is computed from one value per
seed and policy.

For a KPI x and two policies A (the variant) and B (the baseline), with d = x(A) - x(B) per
seed, a `Contrast` reports:

- the mean of d with a Student t interval (`metrics.paired_difference`) and its p-value;
- a percentile bootstrap interval over seeds, for KPIs whose differences are skewed (a maximum
  or a P95), where the t interval's symmetry is doubtful;
- wins, losses and ties, and the p-value of the Wilcoxon signed-rank test: a check that does
  not depend on a few large differences;
- **how much pairing helped**: the correlation of x(A) and x(B) over seeds, and the variance
  of d divided by var x(A) + var x(B). The ratio is 1 for unrelated runs and falls towards 0
  as the two policies' results move together. It measures the common conditions directly;
- a verdict against a margin stated before the runs. "Equivalent" needs the whole interval
  inside the margin: an interval that merely includes zero is "inconclusive".
"""

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from scipy.stats import t as student_t
from scipy.stats import wilcoxon

from amr_optimizer.metrics import PairedDifference, paired_difference

HIGHER_IS_BETTER = frozenset({"throughput_per_h", "delivered", "utilization"})
"""KPIs where more is better. For every other KPI, less is better."""

VERDICTS = ("better", "worse", "equivalent", "inconclusive")


@dataclass(frozen=True, slots=True)
class Contrast:
    """Variant minus baseline on one KPI, over seeds. Values that need at least two seeds (or
    some variation) are ``nan`` without them."""

    kpi: str
    variant: str
    baseline: str
    seeds: int
    mean_variant: float
    mean_baseline: float
    difference: float
    low: float  # Student t interval
    high: float
    p_value: float  # two-sided t test of "the mean difference is zero"
    bootstrap_low: float
    bootstrap_high: float
    wins: int  # seeds where the variant is better
    losses: int
    ties: int
    signed_rank_p: float
    correlation: float
    variance_ratio: float
    margin: float
    verdict: str
    holm_p: float | None = None  # set for primary tests only (`holm`)

    def to_dict(self) -> dict[str, object]:
        return {
            k: (None if isinstance(v, float) and math.isnan(v) else v)
            for k, v in asdict(self).items()
        }


def _p_value(diffs: Sequence[float]) -> float:
    if len(diffs) < 2:
        return math.nan
    spread = statistics.stdev(diffs)
    mean = statistics.fmean(diffs)
    if spread == 0:
        return 1.0 if mean == 0 else 0.0
    statistic = mean / (spread / math.sqrt(len(diffs)))
    return float(2 * student_t.sf(abs(statistic), len(diffs) - 1))


def bootstrap_interval(
    diffs: Sequence[float], *, confidence: float, resamples: int, rng: random.Random
) -> tuple[float, float]:
    """A percentile bootstrap interval for the mean of ``diffs``: seeds are resampled whole,
    with replacement. ``nan`` with fewer than two seeds."""
    if len(diffs) < 2:
        return math.nan, math.nan
    count = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=count)) for _ in range(resamples))
    tail = (1 - confidence) / 2
    return means[int(tail * resamples)], means[min(resamples - 1, int((1 - tail) * resamples))]


def _signed_rank_p(diffs: Sequence[float]) -> float:
    if len(diffs) < 2 or all(d == 0 for d in diffs):
        return math.nan
    return float(wilcoxon(diffs).pvalue)


def _verdict(difference: PairedDifference, *, lower_is_better: bool, margin: float) -> str:
    if difference.seeds < 2:
        return "inconclusive"
    low, high = difference.low, difference.high
    if low > 0 or high < 0:
        return "better" if (high < 0) == lower_is_better else "worse"
    if -margin <= low and high <= margin and margin > 0:
        return "equivalent"
    return "inconclusive"


def paired_contrast(
    kpi: str,
    variant: str,
    baseline: str,
    values: Sequence[float],
    base: Sequence[float],
    *,
    margin: float = 0.0,
    confidence: float = 0.95,
    resamples: int = 10_000,
    rng: random.Random | None = None,
) -> Contrast:
    """The contrast of ``values`` (variant) against ``base`` (baseline): the same seeds in the
    same order. ``margin`` is the difference below which two policies count as equivalent, in
    the KPI's unit."""
    if margin < 0:
        raise ValueError(f"the margin must be non-negative, got {margin}")
    if any(math.isnan(v) for v in (*values, *base)):
        raise ValueError(f"{kpi}: a run has no value (was nothing delivered?)")
    difference = paired_difference(values, base, confidence=confidence)
    diffs = [v - b for v, b in zip(values, base, strict=True)]
    lower_is_better = kpi not in HIGHER_IS_BETTER
    better = [d < 0 if lower_is_better else d > 0 for d in diffs]
    ties = sum(d == 0 for d in diffs)
    correlation = variance_ratio = math.nan
    if len(diffs) >= 2:
        spread = statistics.variance(values) + statistics.variance(base)
        if spread > 0:
            variance_ratio = statistics.variance(diffs) / spread
        if statistics.variance(values) > 0 and statistics.variance(base) > 0:
            correlation = statistics.correlation(values, base)
    low, high = bootstrap_interval(
        diffs, confidence=confidence, resamples=resamples, rng=rng or random.Random(0)
    )
    return Contrast(
        kpi=kpi,
        variant=variant,
        baseline=baseline,
        seeds=len(diffs),
        mean_variant=statistics.fmean(values),
        mean_baseline=statistics.fmean(base),
        difference=difference.mean,
        low=difference.low,
        high=difference.high,
        p_value=_p_value(diffs),
        bootstrap_low=low,
        bootstrap_high=high,
        wins=sum(better),
        losses=len(diffs) - sum(better) - ties,
        ties=ties,
        signed_rank_p=_signed_rank_p(diffs),
        correlation=correlation,
        variance_ratio=variance_ratio,
        margin=margin,
        verdict=_verdict(difference, lower_is_better=lower_is_better, margin=margin),
    )


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm's adjusted p-values, in the order given: the smallest is multiplied by the number
    of tests, the next by one less, and so on, never decreasing and at most 1. Rejecting where
    the adjusted value is below alpha keeps the chance of any false rejection below alpha."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted
