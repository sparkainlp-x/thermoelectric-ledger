"""Efficiency claim eligibility, synthetic withholding, and the always-computed accounting flag."""
from __future__ import annotations

import math

import thermoelectric_ledger as tl
from conftest import make_doc, make_sample

IMPOSSIBLE_FLAG = "IMPOSSIBLE_ENERGY_ACCOUNTING_ELECTRICAL_LOWER_BOUND_EXCEEDS_EXERGY_UPPER_BOUND"
OVERLAP_FLAG = "POINT_ESTIMATE_EXCEEDS_EXERGY_CEILING_BUT_UNCERTAINTY_OVERLAPS"


def _impossible_samples():
    # 10 W electrical out of 10 W heat across a ~14 % Carnot factor.
    return [make_sample(float(t), v=10.0, i=1.0, q=10.0) for t in range(3)]


def test_measured_efficiency_reported_with_worst_case_interval():
    report = tl.analyze(make_doc())
    eff = report["thermal_efficiency"]
    assert eff["status"] == "reported_from_measured_heat_input"
    e = report["electrical"]["energy_J"]
    ue = report["electrical"]["uncertainty_bound_J"]
    q = report["hot_side_heat"]["input_energy_J"]
    uq = report["hot_side_heat"]["uncertainty_bound_J"]
    assert math.isclose(eff["value"], e / q)
    assert math.isclose(eff["interval_lower"], (e - ue) / (q + uq))
    assert math.isclose(eff["interval_upper"], (e + ue) / (q - uq))
    assert math.isclose(eff["uncertainty_bound"],
                        max(eff["value"] - eff["interval_lower"], eff["interval_upper"] - eff["value"]))
    assert eff["uncertainty_bound_type"] == tl.EFFICIENCY_BOUND_TYPE
    assert "not_k2" in eff["uncertainty_bound_type"]
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["comparison_status"] == "no_measured_exceedance_of_uncertainty_adjusted_ceiling"
    assert ceiling["reason"] is None


def test_efficiency_withheld_when_net_heat_does_not_survive_bound():
    # Per-sample lower bounds stay positive, but a large timestamp bound swamps the integral.
    report = tl.analyze(make_doc(u={"timestamp_s": 10.0}))
    heat = report["hot_side_heat"]
    assert heat["all_sample_lower_bounds_strictly_positive"] is True
    assert heat["input_energy_J"] - heat["uncertainty_bound_J"] <= 0
    assert report["carnot_exergy_ceiling"]["status"] == "computed"
    assert report["carnot_exergy_ceiling"]["accounting_check"]["status"] == "computed"
    eff = report["thermal_efficiency"]
    assert eff["status"] == "not_reported_positive_measured_heat_input_not_resolved"
    assert eff["value"] is None


def test_measured_impossible_accounting_flagged_and_efficiency_withheld():
    report = tl.analyze(make_doc(_impossible_samples()))
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["comparison_status"] == "impossible_energy_accounting"
    assert ceiling["accounting_check"]["result"] == "impossible_energy_accounting"
    acc = ceiling["accounting_check"]
    assert acc["electrical_lower_bound_J"] > acc["exergy_upper_bound_J"]
    assert IMPOSSIBLE_FLAG in report["flags"]
    assert report["thermal_efficiency"]["status"] == "not_reported_impossible_energy_accounting"
    assert report["thermal_efficiency"]["value"] is None


def test_point_estimate_over_ceiling_within_uncertainty():
    samples = [make_sample(float(t), v=1.0, i=1.5, q=10.0) for t in range(3)]
    report = tl.analyze(make_doc(samples, u={"voltage_V": 0.2}))
    ceiling = report["carnot_exergy_ceiling"]
    assert report["electrical"]["energy_J"] > ceiling["ceiling_J"]
    assert ceiling["comparison_status"] == "point_estimate_exceeds_ceiling_within_uncertainty"
    assert OVERLAP_FLAG in report["flags"]


def test_synthetic_demo_never_reports_efficiency():
    report = tl.analyze(make_doc(kind="synthetic_demo"))
    assert report["is_measurement"] is False
    assert "SYNTHETIC_DEMO_NOT_MEASUREMENT" in report["flags"]
    assert report["hot_side_heat"]["status"] == "illustrative_only"
    assert report["carnot_exergy_ceiling"]["status"] == "computed"
    eff = report["thermal_efficiency"]
    assert eff["status"] == "not_reported_synthetic_data"
    assert eff["value"] is None and eff["uncertainty_bound"] is None
    assert eff["interval_lower"] is None and eff["interval_upper"] is None
    # The identical measured record would receive an efficiency figure.
    assert tl.analyze(make_doc())["thermal_efficiency"]["status"] == "reported_from_measured_heat_input"


def test_synthetic_accounting_flag_is_always_computed():
    report = tl.analyze(make_doc(_impossible_samples(), kind="synthetic_demo"))
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["accounting_check"]["status"] == "computed"
    assert ceiling["comparison_status"] == "impossible_energy_accounting"
    assert IMPOSSIBLE_FLAG in report["flags"]
    assert "SYNTHETIC_DEMO_NOT_MEASUREMENT" in report["flags"]
    assert report["thermal_efficiency"]["status"] == "not_reported_synthetic_data"
    assert report["thermal_efficiency"]["value"] is None


def test_synthetic_plausible_accounting_also_computed():
    report = tl.analyze(make_doc(kind="synthetic_demo"))
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["accounting_check"]["status"] == "computed"
    assert ceiling["comparison_status"] == "no_measured_exceedance_of_uncertainty_adjusted_ceiling"


def test_synthetic_heat_failure_uses_heat_reason():
    samples = [make_sample(float(t), q=0.5) for t in range(3)]
    report = tl.analyze(make_doc(samples, kind="synthetic_demo"))
    assert report["thermal_efficiency"]["status"] == (
        "not_reported_hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample")
    assert report["carnot_exergy_ceiling"]["accounting_check"]["status"] == "not_computed"
