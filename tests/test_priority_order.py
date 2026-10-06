"""One shared priority order for exergy reason, comparison_status and efficiency status."""
from __future__ import annotations

import itertools

import pytest

import thermoelectric_ledger as tl
from conftest import make_doc, make_sample

HEAT_REASON = "hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample"


def _samples(th_tc_pairs, q):
    return [make_sample(float(t), th=th, tc=tc, q=q) for t, (th, tc) in enumerate(th_tc_pairs)]


FORWARD = [(350.0, 300.0)] * 3
EQUAL = [(300.0, 300.0)] * 3
REVERSED = [(300.0, 350.0)] * 3
MIXED = [(350.0, 300.0), (300.0, 350.0), (350.0, 300.0)]
UNRESOLVED = [(300.6, 300.0)] * 3  # forward, but 300.6 - 0.5 <= 300.0 + 0.5

CASES = [
    # (label, samples-builder args, heat_status, expected reason)
    ("no_heat_reversed", REVERSED, None, "not_measured", "no_measured_heat_input"),
    ("no_heat_forward", FORWARD, None, "not_measured", "no_measured_heat_input"),
    ("heat_fail_reversed", REVERSED, 0.5, "measured", HEAT_REASON),
    ("heat_fail_unresolved", UNRESOLVED, 0.5, "measured", HEAT_REASON),
    ("heat_fail_mixed", MIXED, -3.0, "measured", HEAT_REASON),
    ("equal", EQUAL, 10.0, "measured", "equal_temperature_faces"),
    ("reversed", REVERSED, 10.0, "measured", "reversed_temperature_gradient"),
    ("mixed", MIXED, 10.0, "measured", "mixed_or_crossing_temperature_gradient"),
    ("unresolved", UNRESOLVED, 10.0, "measured", "temperature_uncertainty_overlaps_zero_gradient"),
    ("synthetic_reversed", REVERSED, 10.0, "illustrative", "reversed_temperature_gradient"),
]


@pytest.mark.parametrize("label, pairs, q, heat_status, expected", CASES, ids=[c[0] for c in CASES])
def test_reason_and_comparison_status_share_priority(label, pairs, q, heat_status, expected):
    kind = "synthetic_demo" if heat_status == "illustrative" else "measured"
    doc = make_doc(_samples(pairs, q), kind=kind, heat_status=heat_status)
    report = tl.analyze(doc)
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["status"] == "unavailable"
    assert ceiling["ceiling_J"] is None
    assert ceiling["reason"] == expected
    assert ceiling["comparison_status"] == "not_assessable_" + expected
    assert ceiling["accounting_check"]["status"] == "not_computed"
    assert {seg["status"] for seg in ceiling["segments"]} == {"not_computed_" + expected}
    assert ceiling["reason_priority"] == list(tl.UNAVAILABLE_REASON_PRIORITY)
    eff = report["thermal_efficiency"]
    assert eff["status"] == tl._EFFICIENCY_STATUS_FOR_REASON[expected]
    assert eff["value"] is None and eff["uncertainty_bound"] is None


def test_heat_flow_reason_wins_over_gradient_and_is_the_only_reason():
    report = tl.analyze(make_doc(_samples(REVERSED, 0.5)))
    ceiling = report["carnot_exergy_ceiling"]
    assert ceiling["reason"] == HEAT_REASON
    assert "gradient" not in ceiling["comparison_status"]
    assert "gradient" not in report["thermal_efficiency"]["status"]
    # Gradient facts remain visible as data/flags, not as a second reason.
    assert ceiling["gradient_state"] == "reversed"
    assert "NO_VALID_FORWARD_CARNOT_CEILING_FOR_ENTIRE_RUN" in report["flags"]


def test_no_heat_flow_branch_keeps_efficiency_default_explicit():
    report = tl.analyze(make_doc(_samples(REVERSED, None), heat_status="not_measured"))
    assert report["thermal_efficiency"]["status"] == "not_reported_no_measured_heat_input"
    assert report["thermal_efficiency"]["value"] is None
    assert "HOT_SIDE_HEAT_FLOW_NOT_MEASURED" in report["flags"]


def _gate_fails(reason, has_heat, lb_ok, state, confident):
    return {
        "no_measured_heat_input": not has_heat,
        HEAT_REASON: has_heat and not lb_ok,
        "equal_temperature_faces": state == "equal",
        "reversed_temperature_gradient": state == "reversed",
        "mixed_or_crossing_temperature_gradient": state == "mixed_or_crossing",
        "temperature_uncertainty_overlaps_zero_gradient": state == "hot_to_cold" and not confident,
    }[reason]


# A resolved (confident) gradient implies every sample is hot_to_cold, so those
# impossible combinations are excluded rather than skipped.
GATE_COMBOS = [c for c in itertools.product(
    [True, False], [True, False], ["hot_to_cold", "equal", "reversed", "mixed_or_crossing"], [True, False])
    if not (c[3] and c[2] != "hot_to_cold")]


@pytest.mark.parametrize("has_heat, lb_ok, state, confident", GATE_COMBOS)
def test_reason_is_first_failing_gate_in_documented_order(has_heat, lb_ok, state, confident):
    lb_arg = lb_ok if has_heat else None
    got = tl._exergy_unavailable_reason(has_heat, lb_arg, state, confident)
    failing = [r for r in tl.UNAVAILABLE_REASON_PRIORITY if _gate_fails(r, has_heat, lb_ok, state, confident)]
    assert got == (failing[0] if failing else None)
    if got is not None:
        assert tl._comparison_status_for_reason(got) == "not_assessable_" + got
        assert got in tl._EFFICIENCY_STATUS_FOR_REASON
