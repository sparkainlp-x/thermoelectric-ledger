"""Read-only, no device I/O, residual energy invariant, OES sidecar validation."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import thermoelectric_ledger as tl
from conftest import make_doc, make_sample

SOURCE = Path(tl.__file__).read_text(encoding="utf-8")


def test_residual_energy_invariant():
    assert tl.RESIDUAL_ENERGY_J == 0.0
    samples = [make_sample(float(t), oes={"residual": 0.5 * t, "worst_component": t, "event": "e"})
               for t in range(3)]
    report = tl.analyze(make_doc(samples))
    assert report["electrical"]["residual_energy_J"] == 0.0
    assert len(report["oes_residual_log"]) == 3
    assert all(row["residual_energy_J"] == 0.0 for row in report["oes_residual_log"])
    # The residual never changes the electrical integral.
    plain = tl.analyze(make_doc())
    assert report["electrical"]["energy_J"] == plain["electrical"]["energy_J"]


def test_report_declares_read_only_and_no_hardware_control():
    report = tl.analyze(make_doc())
    assert report["read_only"] is True
    assert report["hardware_control"] is False


def test_module_imports_only_stdlib_without_device_or_network_io():
    tree = ast.parse(SOURCE)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "argparse", "copy", "json", "math", "sys", "pathlib", "typing"}


def test_only_file_access_is_read_mode():
    tree = ast.parse(SOURCE)
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in {"open", "write_text", "write_bytes", "unlink", "rename", "replace"}]
    assert len(opens) == 1 and opens[0].func.attr == "open"
    assert isinstance(opens[0].args[0], ast.Constant) and opens[0].args[0].value == "r"


def test_negative_oes_residual_rejected():
    samples = [make_sample(0.0, oes={"residual": -0.1, "worst_component": None, "event": None}),
               make_sample(1.0)]
    with pytest.raises(tl.LedgerError, match="non-negative"):
        tl.analyze(make_doc(samples))


@pytest.mark.parametrize("worst", [-1, 32, True, 1.5])
def test_worst_component_range(worst):
    samples = [make_sample(0.0, oes={"residual": 0.1, "worst_component": worst, "event": None}),
               make_sample(1.0)]
    with pytest.raises(tl.LedgerError):
        tl.analyze(make_doc(samples))


def test_duplicate_timestamps_rejected_by_validator():
    with pytest.raises(tl.LedgerError, match="duplicate"):
        tl.analyze(make_doc([make_sample(0.0), make_sample(0.0)]))
