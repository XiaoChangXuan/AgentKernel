from __future__ import annotations

import json

from benchmarks.minicode import (
    DEFAULT_OUTPUT,
    assert_repeatable,
    format_human_report,
    run_phase2f_validation,
    write_phase2f_validation,
)


def test_phase2f_validation_passes_all_checks_without_claiming_integrationbench():
    document = run_phase2f_validation()
    payload = document.as_dict()

    assert payload["suite"] == "minicode_phase2f_validation"
    assert payload["summary"] == {"decision": "PASS", "failed": 0, "passed": 8, "total": 8}
    assert [check["check_id"] for check in payload["checks"]] == [
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
    ]
    assert all(check["status"] == "PASS" for check in payload["checks"])
    assert payload["future_integrationbench_contract"]["status"] == "not_claimed_by_phase2f_validation"
    assert any("I8 Reviewer child Agent - DEFERRED" in item for item in payload["future_integrationbench_contract"]["frozen_ids"])


def test_phase2f_validation_artifact_is_stable_and_named_truthfully(tmp_path):
    first, second = assert_repeatable()
    output = write_phase2f_validation(first, tmp_path / DEFAULT_OUTPUT.name)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert first.as_dict() == second.as_dict()
    assert output.name == "minicode_phase2f_validation.json"
    assert payload["benchmark_version"] == "minicode.phase2f.validation.v0"
    assert "generated_at" not in payload


def test_phase2f_validation_human_report_uses_f_ids():
    report = format_human_report(run_phase2f_validation())

    assert "MiniCode Phase 2F Validation" in report
    assert "F1 Workspace" in report
    assert "F8 Trace Redaction" in report
    assert "Phase 2F Validation:" in report
    assert "IntegrationBench I1-I8" not in report
