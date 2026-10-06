"""Run the CLI on the shipped synthetic example."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import thermoelectric_ledger as tl

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "thermoelectric_ledger.py"
EXAMPLE = ROOT / "examples" / "synthetic_demo_run.json"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, check=False)


def test_cli_on_synthetic_example():
    digest = hashlib.sha256(EXAMPLE.read_bytes()).hexdigest()
    proc = _run(EXAMPLE)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["data_kind"] == "synthetic_demo"
    assert report["is_measurement"] is False
    assert "SYNTHETIC_DEMO_NOT_MEASUREMENT" in report["flags"]
    assert report["thermal_efficiency"]["status"] == "not_reported_synthetic_data"
    assert report["thermal_efficiency"]["value"] is None
    assert report["carnot_exergy_ceiling"]["status"] == "computed"
    assert report["carnot_exergy_ceiling"]["accounting_check"]["status"] == "computed"
    assert report["hot_side_heat"]["all_sample_lower_bounds_strictly_positive"] is True
    assert len(report["hot_side_heat"]["sample_lower_bounds_W"]) == report["sample_count"]
    assert report["electrical"]["residual_energy_J"] == 0.0
    assert report["read_only"] is True and report["hardware_control"] is False
    assert hashlib.sha256(EXAMPLE.read_bytes()).hexdigest() == digest  # input never modified


def test_cli_output_matches_library():
    proc = _run(EXAMPLE)
    assert json.loads(proc.stdout) == json.loads(json.dumps(tl.analyze(json.loads(EXAMPLE.read_text()))))


def test_cli_require_measured_rejects_synthetic():
    proc = _run(EXAMPLE, "--require-measured")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "synthetic demo data are not measurement" in proc.stderr


def test_cli_missing_file(tmp_path):
    proc = _run(tmp_path / "absent.json")
    assert proc.returncode == 2
    assert proc.stderr.startswith("error:")


def test_cli_invalid_document(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    proc = _run(bad)
    assert proc.returncode == 2
    assert "document keys invalid" in proc.stderr


def test_main_in_process(capsys):
    assert tl.main([str(EXAMPLE)]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "synthetic-demo-001"
