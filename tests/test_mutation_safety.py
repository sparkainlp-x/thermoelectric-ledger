"""Neither the caller's document nor validated samples are mutated."""
from __future__ import annotations

import copy

import thermoelectric_ledger as tl
from conftest import make_doc, make_sample


def test_analyze_does_not_mutate_input_document():
    doc = make_doc([make_sample(0.0, q=10.0), make_sample(1.0, q=-2.0),
                    make_sample(2.0, q=10.0, oes={"residual": 0.1, "worst_component": 3, "event": "x"})])
    before = copy.deepcopy(doc)
    tl.analyze(doc)
    assert doc == before
    assert all("signed_load_power_W" not in s for s in doc["samples"])


def test_analyze_does_not_mutate_validated_samples(monkeypatch):
    captured = {}
    real_validate = tl.validate_document

    def spy(document):
        run = real_validate(document)
        captured["run"] = run
        captured["snapshot"] = copy.deepcopy(run)
        return run

    monkeypatch.setattr(tl, "validate_document", spy)
    tl.analyze(make_doc())
    assert captured["run"] == captured["snapshot"]
    assert all("signed_load_power_W" not in s for s in captured["run"]["samples"])


def test_validate_document_returns_deep_copy():
    doc = make_doc()
    before = copy.deepcopy(doc)
    run = tl.validate_document(doc)
    assert run["metadata"] is not doc["metadata"]
    run["metadata"]["calibration_references"]["temperature"] = "changed"
    run["samples"][0]["oes"]["event"] = "changed"
    assert doc == before


def test_report_does_not_alias_input():
    doc = make_doc()
    before = copy.deepcopy(doc)
    report = tl.analyze(doc)
    assert report["input_metadata"] is not doc["metadata"]
    report["input_metadata"]["units"]["load_voltage"] = "mV"
    report["sample_log"][0]["oes"]["event"] = "changed"
    assert doc == before


def test_repeated_analysis_is_identical():
    doc = make_doc()
    assert tl.analyze(doc) == tl.analyze(doc)
