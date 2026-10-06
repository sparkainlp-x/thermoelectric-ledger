#!/usr/bin/env python3
"""Read-only thermoelectric measurement ledger; no device I/O or control.

The ledger keeps three layers separate:

1. numeric computation  - signed trapezoidal integrals with conservative bounds;
2. physical admissibility - every hot-side heat-flow lower bound strictly > 0
   and a forward temperature gradient resolved by the calibration bounds;
3. claim eligibility    - an efficiency figure is reported only for measured
   data whose net heat input survives its own uncertainty bound.

Signed readings are never clipped. Input documents are never mutated.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

SCHEMA_VERSION = "1.0"
RESIDUAL_ENERGY_J = 0.0  # Invariant: an OES residual is bookkeeping, never energy.
EXPECTED_UNITS = {
    "timestamp_s": "s",
    "hot_face_temperature": "K",
    "cold_face_temperature": "K",
    "load_voltage": "V",
    "load_current": "A",
    "hot_heat_flow": "W",
    "oes_residual": "dimensionless",
}
EXPECTED_ELECTRICAL_CONVENTION = "positive_voltage_times_current_means_power_delivered_to_load"
EXPECTED_HEAT_CONVENTION = "positive_heat_flow_means_heat_entering_module_hot_face"
EXPECTED_UNCERTAINTY_MODEL = "conservative_absolute_error_bounds_per_reading"
SAMPLE_KEYS = {
    "timestamp_s", "hot_face_temperature_K", "cold_face_temperature_K",
    "load_voltage_V", "load_current_A", "hot_heat_flow_W", "oes",
}
UNCERTAINTY_KEYS = {
    "timestamp_s", "voltage_V", "current_A", "hot_temperature_K",
    "cold_temperature_K", "hot_heat_flow_W",
}

# Documented comparison rule for lower bounds: a value is admissible only if it
# is strictly greater than zero. Zero and negative zero (-0.0, e.g. produced by
# -0.0 - 0.0) are rejected, as is every negative value.
LOWER_BOUND_RULE = "strictly_greater_than_zero_reject_zero_negative_zero_and_negative"

# The hot-side heat-flow uncertainty is ONE scalar calibration bound
# (metadata.calibration_uncertainty_bounds.hot_heat_flow_W) applied to every
# sample. Per-sample uncertainty is not part of schema 1.0.
HEAT_FLOW_UNCERTAINTY_SCOPE = "single_scalar_calibration_bound_applied_to_every_sample"

# The efficiency uncertainty_bound is the half-width of a worst-case interval
# built from the conservative absolute bounds. It is NOT a k=2 expanded
# (coverage-factor) uncertainty in the GUM sense.
EFFICIENCY_BOUND_TYPE = "worst_case_interval_half_width_not_k2_expanded_uncertainty"

# Single priority order shared by carnot_exergy_ceiling.reason,
# carnot_exergy_ceiling.comparison_status, the per-segment exergy status and the
# thermal_efficiency status. Heat-flow reasons win over gradient reasons; only
# the first failing gate is emitted, so the fields cannot contradict each other.
UNAVAILABLE_REASON_PRIORITY = (
    "no_measured_heat_input",
    "hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample",
    "equal_temperature_faces",
    "reversed_temperature_gradient",
    "mixed_or_crossing_temperature_gradient",
    "temperature_uncertainty_overlaps_zero_gradient",
)
_EFFICIENCY_STATUS_FOR_REASON = {
    "no_measured_heat_input": "not_reported_no_measured_heat_input",
    "hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample":
        "not_reported_hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample",
    "equal_temperature_faces": "not_reported_gradient_not_forward_for_entire_run",
    "reversed_temperature_gradient": "not_reported_gradient_not_forward_for_entire_run",
    "mixed_or_crossing_temperature_gradient": "not_reported_gradient_not_forward_for_entire_run",
    "temperature_uncertainty_overlaps_zero_gradient": "not_reported_gradient_not_resolved_by_uncertainty",
}


class LedgerError(ValueError):
    """Invalid or incomplete input measurement record."""


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise LedgerError(f"{label} must be a finite number")
    return result


def _nonnegative(value: Any, label: str) -> float:
    result = _finite_number(value, label)
    if result < 0:
        raise LedgerError(f"{label} must be non-negative")
    return result


def _strictly_positive(value: float) -> bool:
    """Apply LOWER_BOUND_RULE: True only for finite values strictly above zero.

    ``0.0 > 0.0`` and ``-0.0 > 0.0`` are both False in IEEE 754, so zero and
    negative zero are rejected; the sign bit is checked explicitly as well so
    the rule does not depend on that subtlety.
    """
    return math.isfinite(value) and value > 0.0 and math.copysign(1.0, value) > 0.0


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LedgerError(f"{label} must be a JSON object")
    return value


def _require_keys(obj: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - obj.keys())
    extra = sorted(obj.keys() - keys)
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if extra:
            parts.append("unexpected: " + ", ".join(extra))
        raise LedgerError(f"{label} keys invalid ({'; '.join(parts)})")


def _validate_oes(value: Any, index: int) -> dict[str, Any]:
    oes = _require_mapping(value, f"samples[{index}].oes")
    _require_keys(oes, {"residual", "worst_component", "event"}, f"samples[{index}].oes")
    residual = oes["residual"]
    if residual is not None:
        residual = _nonnegative(residual, f"samples[{index}].oes.residual")  # raises when negative
    worst = oes["worst_component"]
    if worst is not None and (isinstance(worst, bool) or not isinstance(worst, int) or not 0 <= worst < 32):
        raise LedgerError(f"samples[{index}].oes.worst_component must be null or an integer from 0 to 31")
    event = oes["event"]
    if event is not None and (not isinstance(event, str) or not event.strip()):
        raise LedgerError(f"samples[{index}].oes.event must be null or a non-empty string")
    return {"residual": residual, "worst_component": worst, "event": event}


def validate_document(document: Any) -> dict[str, Any]:
    """Validate the complete explicit-input contract and return normalized values.

    The returned structure is a deep copy; it never aliases ``document``.
    """
    root = copy.deepcopy(_require_mapping(document, "document"))
    required_top = {"schema_version", "data_kind", "run_id", "metadata", "samples"}
    missing = sorted(required_top - root.keys())
    extra = sorted(root.keys() - (required_top | {"description"}))
    if missing or extra:
        bits = []
        if missing:
            bits.append("missing: " + ", ".join(missing))
        if extra:
            bits.append("unexpected: " + ", ".join(extra))
        raise LedgerError("document keys invalid (" + "; ".join(bits) + ")")
    if root["schema_version"] != SCHEMA_VERSION:
        raise LedgerError(f"schema_version must be {SCHEMA_VERSION!r}")
    kind = root["data_kind"]
    if kind not in ("measured", "synthetic_demo"):
        raise LedgerError("data_kind must be 'measured' or 'synthetic_demo'")
    run_id = root["run_id"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise LedgerError("run_id must be a non-empty string")
    if "description" in root and not isinstance(root["description"], str):
        raise LedgerError("description must be a string when supplied")

    metadata = _require_mapping(root["metadata"], "metadata")
    metadata_keys = {
        "units", "electrical_sign_convention", "hot_heat_flow_sign_convention",
        "heat_flow_status", "uncertainty_model", "calibration_references",
        "calibration_uncertainty_bounds",
    }
    _require_keys(metadata, metadata_keys, "metadata")
    units = _require_mapping(metadata["units"], "metadata.units")
    _require_keys(units, set(EXPECTED_UNITS), "metadata.units")
    if units != EXPECTED_UNITS:
        raise LedgerError(f"metadata.units must exactly match {EXPECTED_UNITS}")
    if metadata["electrical_sign_convention"] != EXPECTED_ELECTRICAL_CONVENTION:
        raise LedgerError("electrical sign convention must declare positive V·I as power delivered to the load")
    if metadata["hot_heat_flow_sign_convention"] != EXPECTED_HEAT_CONVENTION:
        raise LedgerError("hot-side heat-flow convention must declare positive flow into the module hot face")
    if metadata["uncertainty_model"] != EXPECTED_UNCERTAINTY_MODEL:
        raise LedgerError(f"uncertainty_model must be {EXPECTED_UNCERTAINTY_MODEL!r}")

    heat_status = metadata["heat_flow_status"]
    permitted_heat_status = {"measured", "not_measured"} if kind == "measured" else {"illustrative", "not_measured"}
    if not isinstance(heat_status, str) or heat_status not in permitted_heat_status:
        raise LedgerError(f"heat_flow_status for {kind} data must be one of {sorted(permitted_heat_status)}")
    has_heat_flow = heat_status in {"measured", "illustrative"}

    refs = _require_mapping(metadata["calibration_references"], "metadata.calibration_references")
    _require_keys(refs, {"temperature", "voltage_current", "hot_heat_flow"}, "metadata.calibration_references")
    for key in ("temperature", "voltage_current"):
        if not isinstance(refs[key], str) or not refs[key].strip():
            raise LedgerError(f"calibration reference {key!r} must be a non-empty string")
    heat_ref = refs["hot_heat_flow"]
    if has_heat_flow:
        if not isinstance(heat_ref, str) or not heat_ref.strip():
            raise LedgerError("hot_heat_flow calibration reference must be a non-empty string when heat flow is present")
    elif heat_ref is not None:
        raise LedgerError("hot_heat_flow calibration reference must be null when heat flow is unavailable")

    raw_u = _require_mapping(metadata["calibration_uncertainty_bounds"], "metadata.calibration_uncertainty_bounds")
    _require_keys(raw_u, UNCERTAINTY_KEYS, "metadata.calibration_uncertainty_bounds")
    bounds: dict[str, float | None] = {}
    for key in sorted(UNCERTAINTY_KEYS):
        value = raw_u[key]
        if key == "hot_heat_flow_W" and not has_heat_flow:
            if value is not None:
                raise LedgerError("hot_heat_flow_W uncertainty must be null when heat flow is unavailable")
            bounds[key] = None
        else:
            bounds[key] = _nonnegative(value, f"calibration_uncertainty_bounds.{key}")

    raw_samples = root["samples"]
    if not isinstance(raw_samples, list) or len(raw_samples) < 2:
        raise LedgerError("samples must contain at least two time-ordered readings")
    samples: list[dict[str, Any]] = []
    previous_time: float | None = None
    for index, raw_sample in enumerate(raw_samples):
        sample = _require_mapping(raw_sample, f"samples[{index}]")
        _require_keys(sample, SAMPLE_KEYS, f"samples[{index}]")
        t = _finite_number(sample["timestamp_s"], f"samples[{index}].timestamp_s")
        if t < 0:
            raise LedgerError(f"samples[{index}].timestamp_s must be non-negative")
        if previous_time is not None and t <= previous_time:
            reason = "duplicate" if t == previous_time else "non-monotonic"
            raise LedgerError(f"samples[{index}].timestamp_s is {reason}; timestamps must strictly increase")
        previous_time = t
        th = _finite_number(sample["hot_face_temperature_K"], f"samples[{index}].hot_face_temperature_K")
        tc = _finite_number(sample["cold_face_temperature_K"], f"samples[{index}].cold_face_temperature_K")
        if th <= 0 or tc <= 0:
            raise LedgerError(f"samples[{index}] absolute temperatures must be above 0 K")
        if th - float(bounds["hot_temperature_K"]) <= 0 or tc - float(bounds["cold_temperature_K"]) <= 0:
            raise LedgerError(f"samples[{index}] temperature uncertainty bounds must stay above 0 K")
        voltage = _finite_number(sample["load_voltage_V"], f"samples[{index}].load_voltage_V")
        current = _finite_number(sample["load_current_A"], f"samples[{index}].load_current_A")
        heat = sample["hot_heat_flow_W"]
        if has_heat_flow:
            # Signed: negative heat flow (heat leaving the hot face) is kept as-is, never clipped.
            heat = _finite_number(heat, f"samples[{index}].hot_heat_flow_W")
        elif heat is not None:
            raise LedgerError(f"samples[{index}].hot_heat_flow_W must be null when heat flow is unavailable")
        oes = _validate_oes(sample["oes"], index)
        samples.append({
            "timestamp_s": t, "hot_face_temperature_K": th, "cold_face_temperature_K": tc,
            "load_voltage_V": voltage, "load_current_A": current,
            "hot_heat_flow_W": heat, "oes": oes,
        })

    return {
        "schema_version": SCHEMA_VERSION, "data_kind": kind, "run_id": run_id,
        "description": root.get("description", ""), "metadata": metadata,
        "uncertainty_bounds": bounds, "heat_flow_status": heat_status,
        "has_heat_flow": has_heat_flow, "samples": samples,
    }


def _product_bound(a: float, ua: float, b: float, ub: float) -> tuple[float, float]:
    """Return product and exact box-bound deviation for independent bounded inputs."""
    nominal = a * b
    candidates = ((a - ua) * (b - ub), (a - ua) * (b + ub), (a + ua) * (b - ub), (a + ua) * (b + ub))
    if not math.isfinite(nominal) or not all(math.isfinite(value) for value in candidates):
        raise LedgerError("numeric product overflowed; check sample magnitudes and units")
    bound = max(abs(value - nominal) for value in candidates)
    if not math.isfinite(bound):
        raise LedgerError("numeric uncertainty bound overflowed")
    return nominal, bound


def _gradient(th: float, tc: float) -> str:
    if th > tc:
        return "hot_to_cold"
    if th < tc:
        return "reversed"
    return "equal"


def _carnot_and_bound(sample: dict[str, Any], bounds: dict[str, float | None]) -> tuple[float, float]:
    """Carnot factor 1 - T_cold/T_hot and its worst-case bound.

    Domain: T_hot > 0 K, T_cold > 0 K, T_hot > T_cold, and the hot-side lower
    temperature bound must stay above 0 K. Anything else raises LedgerError
    before the ratio is evaluated.
    """
    th = sample["hot_face_temperature_K"]
    tc = sample["cold_face_temperature_K"]
    uth = float(bounds["hot_temperature_K"])
    utc = float(bounds["cold_temperature_K"])
    for value, label in ((th, "T_hot"), (tc, "T_cold"), (uth, "u(T_hot)"), (utc, "u(T_cold)")):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise LedgerError(f"Carnot factor requires a finite {label}")
    if th <= 0.0 or tc <= 0.0:
        raise LedgerError("Carnot factor requires absolute temperatures above 0 K")
    if th <= tc:
        raise LedgerError("Carnot factor requires T_hot > T_cold")
    if uth < 0.0 or utc < 0.0:
        raise LedgerError("Carnot factor requires non-negative temperature bounds")
    if th - uth <= 0.0:
        raise LedgerError("Carnot factor requires T_hot - u(T_hot) above 0 K")
    eta = 1.0 - tc / th
    eta_min = 1.0 - (tc + utc) / (th - uth)
    eta_max = 1.0 - (tc - utc) / (th + uth)
    eta_bound = max(abs(eta - eta_min), abs(eta_max - eta))
    if not all(math.isfinite(value) for value in (eta, eta_min, eta_max, eta_bound)):
        raise LedgerError("Carnot factor or its uncertainty overflowed; check temperatures and bounds")
    return eta, eta_bound


def _integrate_signed(samples: list[dict[str, Any]], bounds: dict[str, float | None], rate_key: str,
                      rate_uncertainties: list[float]) -> tuple[float, float, list[dict[str, Any]]]:
    """Signed trapezoidal integral of ``rate_key`` over actual timestamps.

    Returns ``(total, total_uncertainty_bound, segments)``. Rates are never
    clipped. Raises LedgerError when the uncertainty list does not match the
    sample count, when any uncertainty is negative or non-finite, or when any
    segment has dt <= 0.
    """
    if len(samples) < 2:
        raise LedgerError("integration requires at least two samples")
    if len(rate_uncertainties) != len(samples):
        raise LedgerError(
            f"rate_uncertainties length {len(rate_uncertainties)} does not match sample count {len(samples)}")
    for value in rate_uncertainties:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise LedgerError("rate uncertainties must be finite and non-negative")
    u_time = float(bounds["timestamp_s"])
    if not math.isfinite(u_time) or u_time < 0:
        raise LedgerError("timestamp uncertainty must be finite and non-negative")
    total = 0.0
    total_bound = 0.0
    segments: list[dict[str, Any]] = []
    for i in range(len(samples) - 1):
        a, b = samples[i], samples[i + 1]
        dt = b["timestamp_s"] - a["timestamp_s"]
        if not dt > 0:
            raise LedgerError(f"segment {i} has dt <= 0 ({dt!r} s); timestamps must strictly increase")
        avg = (a[rate_key] + b[rate_key]) / 2.0
        avg_u = (rate_uncertainties[i] + rate_uncertainties[i + 1]) / 2.0
        dt_u = 2.0 * u_time
        energy = avg * dt
        energy_u = dt * avg_u + abs(avg) * dt_u + avg_u * dt_u
        if not math.isfinite(energy) or not math.isfinite(energy_u):
            raise LedgerError("integrated energy or uncertainty overflowed; check units and sample magnitudes")
        total += energy
        total_bound += energy_u
        if not math.isfinite(total) or not math.isfinite(total_bound):
            raise LedgerError("cumulative energy or uncertainty overflowed")
        segments.append({"start_s": a["timestamp_s"], "end_s": b["timestamp_s"], "energy_J": energy,
                         "uncertainty_bound_J": energy_u})
    return total, total_bound, segments


def _gradient_state(labels: list[str]) -> str:
    if all(label == "hot_to_cold" for label in labels):
        return "hot_to_cold"
    if all(label == "equal" for label in labels):
        return "equal"
    if all(label == "reversed" for label in labels):
        return "reversed"
    return "mixed_or_crossing"


def _exergy_unavailable_reason(has_heat_flow: bool, all_lower_bounds_strictly_positive: bool | None,
                               gradient_state: str, gradient_confident: bool) -> str | None:
    """First failing gate in UNAVAILABLE_REASON_PRIORITY, or None if the ceiling may be computed.

    Heat-flow reasons win over gradient reasons. ``comparison_status`` is derived
    from the same value, so the two cannot drift.
    """
    if not has_heat_flow:
        return "no_measured_heat_input"
    if not all_lower_bounds_strictly_positive:
        return "hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample"
    if gradient_state == "equal":
        return "equal_temperature_faces"
    if gradient_state == "reversed":
        return "reversed_temperature_gradient"
    if gradient_state != "hot_to_cold":
        return "mixed_or_crossing_temperature_gradient"
    if not gradient_confident:
        return "temperature_uncertainty_overlaps_zero_gradient"
    return None


def _comparison_status_for_reason(reason: str) -> str:
    return "not_assessable_" + reason


def _efficiency(status: str, value: float | None = None, bound: float | None = None,
                lower: float | None = None, upper: float | None = None) -> dict[str, Any]:
    return {"status": status, "value": value, "uncertainty_bound": bound,
            "uncertainty_bound_type": EFFICIENCY_BOUND_TYPE,
            "interval_lower": lower, "interval_upper": upper}


def analyze(document: Any) -> dict[str, Any]:
    """Validate and analyze explicitly supplied samples; never creates or controls data.

    Neither ``document`` nor the validated samples are mutated.
    """
    run = validate_document(document)
    samples = run["samples"]
    metadata = run["metadata"]
    bounds = run["uncertainty_bounds"]
    is_synthetic = run["data_kind"] == "synthetic_demo"

    # --- numeric layer: signed electrical energy -------------------------------
    electrical_rates: list[float] = []
    electrical_rate_bounds: list[float] = []
    for sample in samples:
        p, up = _product_bound(sample["load_voltage_V"], float(bounds["voltage_V"]),
                               sample["load_current_A"], float(bounds["current_A"]))
        electrical_rates.append(p)
        electrical_rate_bounds.append(up)
    # Preserve signed V·I, including negative power (power absorbed from the load).
    # Work on a deep copy: validated samples are never written to.
    electrical_samples = copy.deepcopy(samples)
    for sample, power in zip(electrical_samples, electrical_rates):
        sample["signed_load_power_W"] = power
    electrical_energy, electrical_energy_u, electrical_segments = _integrate_signed(
        electrical_samples, bounds, "signed_load_power_W", electrical_rate_bounds)
    elapsed_s = samples[-1]["timestamp_s"] - samples[0]["timestamp_s"]

    # --- physical admissibility: gradient --------------------------------------
    gradient_labels = [_gradient(s["hot_face_temperature_K"], s["cold_face_temperature_K"]) for s in samples]
    gradient_confident = all(
        s["hot_face_temperature_K"] - float(bounds["hot_temperature_K"])
        > s["cold_face_temperature_K"] + float(bounds["cold_temperature_K"])
        for s in samples
    )
    gradient_state = _gradient_state(gradient_labels)

    flags: list[str] = []
    if is_synthetic:
        flags.append("SYNTHETIC_DEMO_NOT_MEASUREMENT")

    # --- physical admissibility: signed heat flow and per-sample lower bounds ---
    heat_u: float | None = None
    heat_energy: float | None = None
    heat_energy_u: float | None = None
    heat_segments: list[dict[str, Any]] = []
    heat_flow_lower_bounds_W: list[float] | None = None
    heat_flow_lower_bound_positive: bool | None = None
    if run["has_heat_flow"]:
        heat_u = float(bounds["hot_heat_flow_W"])
        heat_flow_lower_bounds_W = [sample["hot_heat_flow_W"] - heat_u for sample in samples]
        heat_flow_lower_bound_positive = all(_strictly_positive(lb) for lb in heat_flow_lower_bounds_W)
        if not heat_flow_lower_bound_positive:
            flags.append("HOT_HEAT_FLOW_LOWER_BOUND_NOT_STRICTLY_POSITIVE_AT_EVERY_SAMPLE")
        # Signed heat is integrated for audit even when every downstream claim is withheld.
        heat_energy, heat_energy_u, heat_segments = _integrate_signed(
            samples, bounds, "hot_heat_flow_W", [heat_u] * len(samples))
    else:
        flags.append("HOT_SIDE_HEAT_FLOW_NOT_MEASURED")

    # --- Carnot/exergy ceiling gate (single shared priority order) -------------
    reason = _exergy_unavailable_reason(run["has_heat_flow"], heat_flow_lower_bound_positive,
                                        gradient_state, gradient_confident)
    exergy_ceiling: float | None = None
    exergy_u: float | None = None
    exergy_segments: list[dict[str, Any]] = []
    accounting: dict[str, Any] = {"status": "not_computed", "electrical_lower_bound_J": None,
                                  "exergy_upper_bound_J": None, "result": None}
    if reason is None:
        if heat_u is None:  # unreachable: the gate requires measured heat flow
            raise LedgerError("internal error: ceiling gate passed without a heat-flow bound")
        x_samples: list[dict[str, Any]] = []
        x_bounds: list[float] = []
        for sample in samples:
            eta, eta_u = _carnot_and_bound(sample, bounds)
            xdot, ux = _product_bound(eta, eta_u, sample["hot_heat_flow_W"], heat_u)
            x_samples.append({"timestamp_s": sample["timestamp_s"], "exergy_rate_W": xdot})
            x_bounds.append(ux)
        exergy_ceiling, exergy_u, exergy_segments = _integrate_signed(
            x_samples, bounds, "exergy_rate_W", x_bounds)

        # Accounting check is ALWAYS computed once a ceiling exists, including for
        # synthetic_demo data, so an impossible accounting can never be hidden.
        lower_e = electrical_energy - electrical_energy_u
        upper_x = exergy_ceiling + exergy_u
        if lower_e > upper_x:
            comparison_status = "impossible_energy_accounting"
            flags.append("IMPOSSIBLE_ENERGY_ACCOUNTING_ELECTRICAL_LOWER_BOUND_EXCEEDS_EXERGY_UPPER_BOUND")
        elif electrical_energy > exergy_ceiling:
            comparison_status = "point_estimate_exceeds_ceiling_within_uncertainty"
            flags.append("POINT_ESTIMATE_EXCEEDS_EXERGY_CEILING_BUT_UNCERTAINTY_OVERLAPS")
        else:
            comparison_status = "no_measured_exceedance_of_uncertainty_adjusted_ceiling"
        accounting = {"status": "computed", "electrical_lower_bound_J": lower_e,
                      "exergy_upper_bound_J": upper_x, "result": comparison_status}
    else:
        comparison_status = _comparison_status_for_reason(reason)
        exergy_segments = [{"start_s": samples[i]["timestamp_s"], "end_s": samples[i + 1]["timestamp_s"],
                            "status": "not_computed_" + reason}
                           for i in range(len(samples) - 1)]

    # --- claim eligibility: efficiency ----------------------------------------
    if reason is not None:
        efficiency = _efficiency(_EFFICIENCY_STATUS_FOR_REASON[reason])
    elif is_synthetic:
        efficiency = _efficiency("not_reported_synthetic_data")
    else:
        if heat_energy is None or heat_energy_u is None:  # unreachable: gate requires heat flow
            raise LedgerError("internal error: efficiency gate passed without a heat integral")
        if not _strictly_positive(heat_energy - heat_energy_u):
            efficiency = _efficiency("not_reported_positive_measured_heat_input_not_resolved")
        elif comparison_status == "impossible_energy_accounting":
            efficiency = _efficiency("not_reported_impossible_energy_accounting")
        elif not _strictly_positive(electrical_energy - electrical_energy_u):
            efficiency = _efficiency("not_reported_positive_electrical_output_not_resolved")
        else:
            ratio = electrical_energy / heat_energy
            low = (electrical_energy - electrical_energy_u) / (heat_energy + heat_energy_u)
            high = (electrical_energy + electrical_energy_u) / (heat_energy - heat_energy_u)
            efficiency = _efficiency("reported_from_measured_heat_input", ratio,
                                     max(ratio - low, high - ratio), low, high)

    if gradient_state in {"equal", "reversed", "mixed_or_crossing"}:
        flags.append("NO_VALID_FORWARD_CARNOT_CEILING_FOR_ENTIRE_RUN")
    elif not gradient_confident:
        flags.append("FORWARD_GRADIENT_NOT_RESOLVED_WITH_CALIBRATION_UNCERTAINTY")

    residual_log = []
    sample_log = []
    for sample, power in zip(samples, electrical_rates):
        oes = sample["oes"]
        sample_log.append({
            "timestamp_s": sample["timestamp_s"],
            "hot_face_temperature_K": sample["hot_face_temperature_K"],
            "cold_face_temperature_K": sample["cold_face_temperature_K"],
            "load_voltage_V": sample["load_voltage_V"],
            "load_current_A": sample["load_current_A"],
            "signed_load_power_W": power,
            "hot_heat_flow_W": sample["hot_heat_flow_W"],
            "oes": dict(oes),
        })
        if oes["residual"] is not None or oes["worst_component"] is not None or oes["event"] is not None:
            residual_log.append({
                "timestamp_s": sample["timestamp_s"], "residual": oes["residual"],
                "worst_component": oes["worst_component"], "event": oes["event"],
                "residual_energy_J": RESIDUAL_ENERGY_J,
            })

    heat_sample_rows = []
    for index, sample in enumerate(samples):
        lb = heat_flow_lower_bounds_W[index] if heat_flow_lower_bounds_W is not None else None
        heat_sample_rows.append({
            "timestamp_s": sample["timestamp_s"],
            "hot_heat_flow_W": sample["hot_heat_flow_W"],
            "uncertainty_bound_W": heat_u,
            "lower_uncertainty_bound_W": lb,
            "lower_bound_strictly_positive": None if lb is None else _strictly_positive(lb),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "ledger_version": __version__,
        "run_id": run["run_id"],
        "data_kind": run["data_kind"],
        "is_measurement": run["data_kind"] == "measured",
        "description": run["description"],
        "sample_count": len(samples),
        "time_span_s": elapsed_s,
        "input_metadata": metadata,
        "sample_log": sample_log,
        "electrical": {
            "integration": "trapezoidal_signed_V_times_I_over_actual_timestamps",
            "sign_convention": EXPECTED_ELECTRICAL_CONVENTION,
            "energy_J": electrical_energy,
            "uncertainty_bound_J": electrical_energy_u,
            "mean_signed_power_W": electrical_energy / elapsed_s,
            "residual_energy_J": RESIDUAL_ENERGY_J,
            "segments": electrical_segments,
        },
        "hot_side_heat": {
            "status": "measured" if run["data_kind"] == "measured" and run["has_heat_flow"] else (
                "illustrative_only" if is_synthetic and run["has_heat_flow"] else "not_measured"
            ),
            "sign_convention": EXPECTED_HEAT_CONVENTION,
            "uncertainty_bound_W": heat_u,
            "uncertainty_scope": HEAT_FLOW_UNCERTAINTY_SCOPE,
            "lower_bound_rule": LOWER_BOUND_RULE,
            "sample_lower_bounds_W": heat_flow_lower_bounds_W,
            "all_sample_lower_bounds_strictly_positive": heat_flow_lower_bound_positive,
            "input_energy_J": heat_energy,
            "uncertainty_bound_J": heat_energy_u,
            "samples": heat_sample_rows,
            "segments": heat_segments,
        },
        "carnot_exergy_ceiling": {
            "status": "computed" if exergy_ceiling is not None else "unavailable",
            "reason": reason,
            "reason_priority": list(UNAVAILABLE_REASON_PRIORITY),
            "gradient_state": gradient_state,
            "gradient_labels_by_sample": gradient_labels,
            "robustly_forward_gradient_with_uncertainty": gradient_confident,
            "ceiling_J": exergy_ceiling,
            "uncertainty_bound_J": exergy_u,
            "integration": "trapezoidal_sum_of_(1-Tcold_K/Thot_K)_times_signed_Qdot_hot_over_actual_timestamps",
            "comparison_status": comparison_status,
            "accounting_check": accounting,
            "segments": exergy_segments,
        },
        "thermal_efficiency": efficiency,
        "oes_residual_log": residual_log,
        "flags": flags,
        "interpretation": (
            "Explicitly supplied synthetic demonstration data; not measurements. No default or synthetic fallback was substituted."
            if is_synthetic else
            "Analysis of supplied data only. Measurement validity still depends on the stated instruments, calibration, and experimental protocol."
        ),
        "read_only": True,
        "hardware_control": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, stdlib thermoelectric energy and OES residual ledger")
    parser.add_argument("input_json", help="explicit JSON run record; the input file is never modified")
    parser.add_argument("--require-measured", action="store_true",
                        help="reject synthetic_demo records before analysis")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    try:
        with Path(args.input_json).open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        if args.require_measured and isinstance(document, dict) and document.get("data_kind") != "measured":
            raise LedgerError("--require-measured requires data_kind='measured'; synthetic demo data are not measurement")
        report = analyze(document)
        json.dump(report, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
        sys.stdout.write("\n")
        return 0
    except (OSError, json.JSONDecodeError, LedgerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
