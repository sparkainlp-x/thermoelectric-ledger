"""Per-sample uncertainty-adjusted hot-side lower bounds; strict > 0 rule incl. -0.0."""
from __future__ import annotations

import math

import pytest

import thermoelectric_ledger as tl
from conftest import make_doc, make_sample


@pytest.mark.parametrize("value, expected", [
    (1.0, True), (5e-324, True), (9.0, True),
    (0.0, False), (-0.0, False), (-5e-324, False), (-1.0, False),
    (float("nan"), False), (float("inf"), False), (float("-inf"), False),
])
def test_strictly_positive_rule(value, expected):
    assert tl._strictly_positive(value) is expected


def _heat_report(q_values, u_q):
    samples = [make_sample(float(t), q=q) for t, q in enumerate(q_values)]
    return tl.analyze(make_doc(samples, u={"hot_heat_flow_W": u_q}))


def test_lower_bounds_are_exposed_per_sample():
    report = _heat_report([10.0, 11.0, 12.0], 1.0)
    heat = report["hot_side_heat"]
    assert heat["sample_lower_bounds_W"] == [9.0, 10.0, 11.0]
    assert [r["lower_uncertainty_bound_W"] for r in heat["samples"]] == [9.0, 10.0, 11.0]
    assert [r["lower_bound_strictly_positive"] for r in heat["samples"]] == [True, True, True]
    assert heat["all_sample_lower_bounds_strictly_positive"] is True
    assert heat["lower_bound_rule"] == tl.LOWER_BOUND_RULE
    assert report["carnot_exergy_ceiling"]["status"] == "computed"


def test_boundary_q10_u1_admissible():
    report = _heat_report([10.0, 10.0, 10.0], 1.0)
    assert report["hot_side_heat"]["sample_lower_bounds_W"] == [9.0, 9.0, 9.0]
    assert report["hot_side_heat"]["all_sample_lower_bounds_strictly_positive"] is True


def test_boundary_q1_u1_zero_lower_bound_rejected():
    report = _heat_report([1.0, 1.0, 1.0], 1.0)
    assert report["hot_side_heat"]["sample_lower_bounds_W"] == [0.0, 0.0, 0.0]
    assert report["hot_side_heat"]["all_sample_lower_bounds_strictly_positive"] is False
    assert report["carnot_exergy_ceiling"]["ceiling_J"] is None
    assert "HOT_HEAT_FLOW_LOWER_BOUND_NOT_STRICTLY_POSITIVE_AT_EVERY_SAMPLE" in report["flags"]


def test_boundary_q05_u1_negative_lower_bound_rejected():
    report = _heat_report([0.5, 0.5, 0.5], 1.0)
    assert report["hot_side_heat"]["sample_lower_bounds_W"] == [-0.5, -0.5, -0.5]
    assert report["hot_side_heat"]["all_sample_lower_bounds_strictly_positive"] is False
    assert report["carnot_exergy_ceiling"]["ceiling_J"] is None


def test_one_failed_sample_rejects_whole_run():
    report = _heat_report([10.0, 1.0, 10.0], 1.0)
    heat = report["hot_side_heat"]
    assert [r["lower_bound_strictly_positive"] for r in heat["samples"]] == [True, False, True]
    assert heat["all_sample_lower_bounds_strictly_positive"] is False
    assert report["carnot_exergy_ceiling"]["status"] == "unavailable"
    assert report["thermal_efficiency"]["value"] is None


def test_negative_zero_lower_bound_is_rejected():
    # -0.0 - 0.0 == -0.0 in IEEE 754: a lower bound that is "zero with a sign bit".
    report = _heat_report([-0.0, 10.0, 10.0], 0.0)
    heat = report["hot_side_heat"]
    lb = heat["sample_lower_bounds_W"][0]
    assert lb == 0.0 and math.copysign(1.0, lb) == -1.0
    # The signed reading itself is preserved, sign bit included.
    assert math.copysign(1.0, report["sample_log"][0]["hot_heat_flow_W"]) == -1.0
    assert heat["samples"][0]["lower_bound_strictly_positive"] is False
    assert heat["all_sample_lower_bounds_strictly_positive"] is False
    assert report["carnot_exergy_ceiling"]["ceiling_J"] is None
    assert report["carnot_exergy_ceiling"]["reason"] == (
        "hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample")


def test_positive_zero_lower_bound_is_rejected():
    report = _heat_report([0.0, 10.0, 10.0], 0.0)
    assert report["hot_side_heat"]["samples"][0]["lower_bound_strictly_positive"] is False
    assert report["hot_side_heat"]["all_sample_lower_bounds_strictly_positive"] is False


def test_heat_flow_bound_is_one_documented_scalar():
    report = _heat_report([10.0, 11.0, 12.0], 0.75)
    heat = report["hot_side_heat"]
    assert heat["uncertainty_bound_W"] == 0.75
    assert heat["uncertainty_scope"] == tl.HEAT_FLOW_UNCERTAINTY_SCOPE
    assert {r["uncertainty_bound_W"] for r in heat["samples"]} == {0.75}


def test_no_heat_flow_lower_bounds_are_null():
    report = tl.analyze(make_doc(heat_status="not_measured"))
    heat = report["hot_side_heat"]
    assert heat["sample_lower_bounds_W"] is None
    assert heat["all_sample_lower_bounds_strictly_positive"] is None
    assert heat["uncertainty_bound_W"] is None
    assert heat["input_energy_J"] is None
    assert all(r["lower_uncertainty_bound_W"] is None for r in heat["samples"])
