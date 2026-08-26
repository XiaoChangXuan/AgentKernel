from __future__ import annotations

from benchmarks.runtimebench.adapters import long_horizon_runtime_stability_record
from benchmarks.runtimebench.long_horizon import run_long_horizon_profiles


def test_long_horizon_profile_uses_kernel_mechanisms() -> None:
    records = run_long_horizon_profiles((12,))

    assert len(records) == 1
    metrics = records[0].metrics
    assert metrics["success"] is True
    assert metrics["profile_steps"] == 12
    assert metrics["steps_completed"] == 12
    assert metrics["durable_operations"] == 1
    assert metrics["reconcile_required_count"] == 1
    assert metrics["duplicate_external_effects"] == 0
    assert metrics["capability_denials"] == 3
    assert metrics["unauthorized_effect_count"] == 0
    assert metrics["resource_restart_success"] is True
    assert metrics["resource_reads"] > 0
    assert metrics["resource_read_bytes"] > 0
    assert metrics["budget_blocks"] == 1
    assert metrics["budget_recoveries"] == 1
    assert metrics["token_usage"] > metrics["token_usage_after_budget_recovery"]
    assert metrics["tool_calls"] > 0
    assert metrics["agent_process_session_isolation"] is True
    assert metrics["truth_preserved"] is True
    assert metrics["final_durable_consistency"] is True


def test_long_horizon_runtimebench_record_aggregates_profiles() -> None:
    record = long_horizon_runtime_stability_record(profiles=(12, 16))
    payload = record.as_dict()
    metrics = payload["metrics"]

    assert payload["benchmark_id"] == "B6_long_horizon_runtime_stability"
    assert payload["success"] is True
    assert metrics["profile_count"] == 2
    assert metrics["profile_steps"] == [12, 16]
    assert metrics["steps_completed"] == [12, 16]
    assert metrics["duplicate_external_effects"] == 0
    assert metrics["unauthorized_effect_count"] == 0
    assert metrics["budget_blocks"] == 2
    assert metrics["budget_recoveries"] == 2
