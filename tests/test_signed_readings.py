"""Signed readings stay signed; negative heat flow is never clipped."""
from __future__ import annotations

import math

import thermoelectric_ledger as tl
from conftest import make_doc, make_sample


def test_negative_heat_flow_sample_is_preserved_not_clipped():
    samples = [make_sample(0.0, q=10.0), make_sample(1.0, q=-2.0), make_sample(2.0, q=10.0)]
    report = tl.analyze(make_doc(samples))
    heat = report["hot_side_heat"]
    assert [row["hot_heat_flow_W"] for row in report["sample_log"]] == [10.0, -2.0, 10.0]
    assert [row["hot_heat_flow_W"] for row in heat["samples"]] == [10.0, -2.0, 10.0]
    # Signed trapezoid: (10 + -2)/2 + (-2 + 10)/2 = 8 J. Clipping would give 10 J.
    assert math.isclose(heat["input_energy_J"], 8.0)
    assert heat["sample_lower_bounds_W"] == [9.0, -3.0, 9.0]
    assert heat["all_sample_lower_bounds_strictly_positive"] is False


def test_negative_heat_integral_is_reported_and_ceiling_stays_null():
    samples = [make_sample(float(t), q=-4.0) for t in range(3)]
    report = tl.analyze(make_doc(samples))
    heat = report["hot_side_heat"]
    assert math.isclose(heat["input_energy_J"], -8.0)
    assert heat["uncertainty_bound_J"] > 0
    assert [s["energy_J"] for s in heat["segments"]] == [-4.0, -4.0]
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["status"] == "unavailable"
    assert ceiling["ceiling_J"] is None
    assert ceiling["uncertainty_bound_J"] is None
    assert ceiling["reason"] == "hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample"
    assert report["thermal_efficiency"]["value"] is None


def test_negative_electrical_power_is_preserved():
    samples = [make_sample(float(t), v=-1.0, i=0.3) for t in range(3)]
    report = tl.analyze(make_doc(samples))
    assert all(row["signed_load_power_W"] < 0 for row in report["sample_log"])
    assert math.isclose(report["electrical"]["energy_J"], -0.6)
    assert report["electrical"]["mean_signed_power_W"] < 0
    assert report["thermal_efficiency"]["status"] == "not_reported_positive_electrical_output_not_resolved"
    assert report["thermal_efficiency"]["value"] is None
