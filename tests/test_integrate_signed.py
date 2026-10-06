"""_integrate_signed contract: tuple[float, float, list[dict]], count and dt checks."""
from __future__ import annotations

import math

import pytest

import thermoelectric_ledger as tl

BOUNDS = {"timestamp_s": 0.0}


def _rows(times, rates):
    return [{"timestamp_s": t, "r": r} for t, r in zip(times, rates)]


def test_returns_three_tuple_with_segment_dicts():
    result = tl._integrate_signed(_rows([0.0, 2.0, 3.0], [1.0, 3.0, -5.0]), BOUNDS, "r", [0.1, 0.1, 0.1])
    assert isinstance(result, tuple) and len(result) == 3
    total, bound, segments = result
    assert isinstance(total, float) and isinstance(bound, float) and isinstance(segments, list)
    # (1+3)/2*2 + (3-5)/2*1 = 4 - 1 = 3
    assert math.isclose(total, 3.0)
    assert math.isclose(bound, 0.1 * 2 + 0.1 * 1)
    assert [set(s) for s in segments] == [{"start_s", "end_s", "energy_J", "uncertainty_bound_J"}] * 2
    assert segments[1]["energy_J"] == -1.0


def test_signed_negative_rates_are_not_clipped():
    total, _, _ = tl._integrate_signed(_rows([0.0, 1.0], [-2.0, -4.0]), BOUNDS, "r", [0.0, 0.0])
    assert total == -3.0


def test_timestamp_uncertainty_enters_bound():
    _, bound, _ = tl._integrate_signed(_rows([0.0, 1.0], [2.0, 2.0]), {"timestamp_s": 0.5}, "r", [0.0, 0.0])
    assert math.isclose(bound, abs(2.0) * 1.0)  # |avg| * 2 * u_t


@pytest.mark.parametrize("n_unc", [1, 2, 4])
def test_rejects_uncertainty_count_mismatch(n_unc):
    with pytest.raises(tl.LedgerError, match="does not match sample count"):
        tl._integrate_signed(_rows([0.0, 1.0, 2.0], [1.0, 1.0, 1.0]), BOUNDS, "r", [0.1] * n_unc)


@pytest.mark.parametrize("times", [[0.0, 0.0], [1.0, 0.5], [0.0, 1.0, 1.0]])
def test_rejects_dt_not_positive(times):
    with pytest.raises(tl.LedgerError, match="dt <= 0"):
        tl._integrate_signed(_rows(times, [1.0] * len(times)), BOUNDS, "r", [0.0] * len(times))


def test_rejects_negative_or_nonfinite_uncertainty():
    with pytest.raises(tl.LedgerError):
        tl._integrate_signed(_rows([0.0, 1.0], [1.0, 1.0]), BOUNDS, "r", [0.1, -0.1])
    with pytest.raises(tl.LedgerError):
        tl._integrate_signed(_rows([0.0, 1.0], [1.0, 1.0]), BOUNDS, "r", [0.1, float("nan")])


def test_rejects_fewer_than_two_samples():
    with pytest.raises(tl.LedgerError):
        tl._integrate_signed(_rows([0.0], [1.0]), BOUNDS, "r", [0.0])
