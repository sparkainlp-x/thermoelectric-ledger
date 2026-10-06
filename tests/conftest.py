"""Shared fixtures: build explicit run documents for contract tests."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNITS = {
    "timestamp_s": "s",
    "hot_face_temperature": "K",
    "cold_face_temperature": "K",
    "load_voltage": "V",
    "load_current": "A",
    "hot_heat_flow": "W",
    "oes_residual": "dimensionless",
}

EMPTY_OES = {"residual": None, "worst_component": None, "event": None}


def make_sample(t: float, th: float = 350.0, tc: float = 300.0, v: float = 1.0, i: float = 0.3,
                q: float | None = 10.0, oes: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "timestamp_s": t,
        "hot_face_temperature_K": th,
        "cold_face_temperature_K": tc,
        "load_voltage_V": v,
        "load_current_A": i,
        "hot_heat_flow_W": q,
        "oes": copy.deepcopy(oes if oes is not None else EMPTY_OES),
    }


def make_doc(samples: list[dict[str, Any]] | None = None, *, kind: str = "measured",
             heat_status: str | None = None, u: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a valid document. Defaults: measured, heat flow present, forward gradient."""
    if heat_status is None:
        heat_status = "measured" if kind == "measured" else "illustrative"
    has_heat = heat_status in {"measured", "illustrative"}
    if samples is None:
        samples = [make_sample(float(t), q=10.0 if has_heat else None) for t in range(3)]
    bounds = {
        "timestamp_s": 0.0,
        "voltage_V": 0.01,
        "current_A": 0.001,
        "hot_temperature_K": 0.5,
        "cold_temperature_K": 0.5,
        "hot_heat_flow_W": 1.0 if has_heat else None,
    }
    if u:
        bounds.update(u)
    return {
        "schema_version": "1.0",
        "data_kind": kind,
        "run_id": "test-run",
        "description": "test fixture",
        "metadata": {
            "units": dict(UNITS),
            "electrical_sign_convention": "positive_voltage_times_current_means_power_delivered_to_load",
            "hot_heat_flow_sign_convention": "positive_heat_flow_means_heat_entering_module_hot_face",
            "heat_flow_status": heat_status,
            "uncertainty_model": "conservative_absolute_error_bounds_per_reading",
            "calibration_references": {
                "temperature": "test thermocouple cert",
                "voltage_current": "test DMM cert",
                "hot_heat_flow": "test heat-flux sensor cert" if has_heat else None,
            },
            "calibration_uncertainty_bounds": bounds,
        },
        "samples": samples,
    }


@pytest.fixture
def doc_factory():
    return make_doc


@pytest.fixture
def sample_factory():
    return make_sample
