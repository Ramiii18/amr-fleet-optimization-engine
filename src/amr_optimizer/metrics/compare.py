"""Paired comparisons of two variants over the same seeds (D17)."""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from scipy.stats import t as student_t


@dataclass(frozen=True, slots=True)
class PairedDifference:
    """The mean per-seed difference between two variants, with a confidence interval.

    With fewer than two seeds there is no interval, and ``low`` and ``high`` are ``nan``.
    """

    mean: float
    low: float
    high: float
    seeds: int

    @property
    def excludes_zero(self) -> bool:
        """True if the interval lies entirely above or entirely below zero."""
        return self.low > 0 or self.high < 0

    def format(self, unit: str = "s") -> str:
        if self.seeds < 2:
            return f"{self.mean:+.1f} {unit}"
        return f"{self.mean:+.1f} {unit} [{self.low:+.1f}, {self.high:+.1f}]"


def paired_difference(
    values: Sequence[float], baseline: Sequence[float], *, confidence: float = 0.95
) -> PairedDifference:
    """Mean of ``values[i] - baseline[i]`` over seeds, with a t confidence interval.

    Both sequences must list the same seeds in the same order. Pairing removes the variation
    between seeds, which affects both variants alike (common random numbers, D14).
    """
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    diffs = [v - b for v, b in zip(values, baseline, strict=True)]
    if not diffs:
        raise ValueError("a paired difference needs at least one seed")
    mean = statistics.fmean(diffs)
    if len(diffs) < 2:
        return PairedDifference(mean, math.nan, math.nan, len(diffs))
    quantile = float(student_t.ppf((1 + confidence) / 2, len(diffs) - 1))
    half_width = quantile * statistics.stdev(diffs) / math.sqrt(len(diffs))
    return PairedDifference(mean, mean - half_width, mean + half_width, len(diffs))
