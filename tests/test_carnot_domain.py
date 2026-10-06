"""_carnot_and_bound rejects T <= 0 K and T_hot <= T_cold before evaluating 1 - Tc/Th."""
from __future__ import annotations

import math

import pytest

import thermoelectric_ledger as tl

BOUNDS = {"hot_temperature_K": 0.5, "cold_temperature_K": 0.5}


def _s(th, tc):
    return {"hot_face_temperature_K": th, "cold_face_temperature_K": tc}


def test_valid_domain():
    eta, bound = tl._carnot_and_bound(_s(350.0, 300.0), BOUNDS)
    assert math.isclose(eta, 1.0 - 300.0 / 350.0)
    expected = max(abs(eta - (1 - 300.5 / 349.5)), abs((1 - 299.5 / 350.5) - eta))
    assert math.isclose(bound, expected)


@pytest.mark.parametrize("th, tc", [
    (0.0, 300.0), (-1.0, 300.0), (350.0, 0.0), (350.0, -1.0), (0.0, 0.0),
])
def test_rejects_nonpositive_absolute_temperature(th, tc):
    with pytest.raises(tl.LedgerError, match="above 0 K"):
        tl._carnot_and_bound(_s(th, tc), BOUNDS)


@pytest.mark.parametrize("th, tc", [(300.0, 300.0), (300.0, 350.0)])
def test_rejects_hot_not_above_cold(th, tc):
    with pytest.raises(tl.LedgerError, match="T_hot > T_cold"):
        tl._carnot_and_bound(_s(th, tc), BOUNDS)


def test_rejects_hot_lower_bound_at_or_below_zero_kelvin():
    with pytest.raises(tl.LedgerError):
        tl._carnot_and_bound(_s(1.0, 0.5), {"hot_temperature_K": 1.0, "cold_temperature_K": 0.0})


def test_rejects_nonfinite_temperature():
    with pytest.raises(tl.LedgerError):
        tl._carnot_and_bound(_s(float("inf"), 300.0), BOUNDS)
