"""Tests for paired comparisons over seeds."""

import math

import pytest

from amr_optimizer.metrics import PairedDifference, paired_difference


def test_paired_difference_with_a_hand_computed_interval() -> None:
    # Differences 1, 2, 3: mean 2, standard deviation 1. The 97.5% quantile of the t
    # distribution with 2 degrees of freedom is 4.3027, so the half-width is 4.3027 / sqrt(3).
    result = paired_difference([11.0, 22.0, 33.0], [10.0, 20.0, 30.0])

    half_width = 4.302652729911275 / math.sqrt(3)
    assert result.mean == pytest.approx(2.0)
    assert result.low == pytest.approx(2.0 - half_width)
    assert result.high == pytest.approx(2.0 + half_width)
    assert result.seeds == 3
    assert not result.excludes_zero
    assert result.format() == "+2.0 s [-0.5, +4.5]"


def test_identical_variants_differ_by_exactly_zero() -> None:
    result = paired_difference([5.0, 7.0], [5.0, 7.0])

    assert (result.mean, result.low, result.high) == (0.0, 0.0, 0.0)
    assert result.format() == "+0.0 s [+0.0, +0.0]"


def test_an_interval_away_from_zero() -> None:
    assert paired_difference([3.0, 4.1, 5.0], [0.0, 0.0, 0.0]).excludes_zero
    assert paired_difference([-3.0, -4.1, -5.0], [0.0, 0.0, 0.0]).excludes_zero


def test_one_seed_has_no_interval() -> None:
    result = paired_difference([3.0], [1.0])

    assert result.mean == 2.0 and math.isnan(result.low) and math.isnan(result.high)
    assert result.format("ticks") == "+2.0 ticks"


def test_bad_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one seed"):
        paired_difference([], [])
    with pytest.raises(ValueError):
        paired_difference([1.0, 2.0], [1.0])  # different numbers of seeds
    with pytest.raises(ValueError, match="confidence"):
        paired_difference([1.0], [1.0], confidence=1.0)


def test_the_result_type_is_exported() -> None:
    assert isinstance(paired_difference([1.0], [0.0]), PairedDifference)
