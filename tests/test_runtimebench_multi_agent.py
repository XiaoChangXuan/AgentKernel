from __future__ import annotations

from benchmarks.runtimebench.adapters import multi_agent_runtime_record
from benchmarks.runtimebench.multi_agent import (
    run_long_horizon_multi_agent,
    run_multi_agent_runtime_records,
)
from benchmarks.runtimebench.runner import run_runtimebench


def test_multi_agent_runtime_leaf_records_pass() -> None:
    records = run_multi_agent_runtime_records((10,))
    by_case = {record.case: record for record in records}

    assert len(records) == 10
    assert [record.case for record in records] == [
        "M1_agent_process_identity_isolation",
        "M2_agent_tree_process_tree_separation",
        "M3_capability_delegation_narrowing",
        "M4_ipc_delivery_authority_isolation",
        "M5_resource_sharing_isolation",
        "M6_hierarchical_budget_isolation",
        "M7_fault_cancellation_isolation",
        "M8_integrated_multi_agent_recovery",
        "M9_authority_shrink_after_restart",
        "M10_long_horizon_multi_agent_composition",
    ]
    assert all(record.metrics["success"] is True for record in records)
    assert by_case["M3_capability_delegation_narrowing"].metrics[
        "multi_hop_delegation_depth"
    ] == 2
    assert by_case["M3_capability_delegation_narrowing"].metrics[
        "grandchild_parent_bound"
    ] is True
    m8 = by_case["M8_integrated_multi_agent_recovery"].metrics
    assert m8["ipc_messages_recovered"] == 3
    assert m8["pending_delivered_after_recovery"] == 1
    assert m8["unacked_redelivery"] == 1
    assert m8["acked_not_redelivered"] == 1
    assert m8["payload_grant_inert"] == 1
    assert m8["resource_refs_inert"] == 1
    assert m8["mandatory_wal_obligations_surfaced"] == 1
    assert m8["lost_mandatory_wal_obligations"] == 0
    m9 = by_case["M9_authority_shrink_after_restart"].metrics
    assert m9["historical_delegation_facts"] == 2
    assert m9["multi_hop_authority_shrink"] is True
    assert m9["stale_authority_restored"] == 0


def test_long_horizon_multi_agent_preserves_invariants() -> None:
    record = run_long_horizon_multi_agent(10)
    metrics = record.metrics

    assert metrics["success"] is True
    assert metrics["logical_steps"] == 10
    assert metrics["ipc_sent"] == 10
    assert metrics["ipc_deliveries"] == 10 + metrics["ipc_redeliveries"]
    assert metrics["ipc_redeliveries"] > 0
    assert metrics["ipc_acks"] == 10
    assert metrics["runtime_restarts"] > 0
    assert (
        metrics["runtime_object_replacements_verified"]
        == metrics["runtime_restarts"]
    )
    assert metrics["agent_ids_preserved"] == metrics["runtime_restarts"]
    assert metrics["process_ids_preserved"] == metrics["runtime_restarts"]
    assert metrics["session_durable_events_preserved"] == metrics["runtime_restarts"]
    assert metrics["resource_metadata_preserved"] == metrics["runtime_restarts"]
    assert metrics["resource_share_preserved"] == metrics["runtime_restarts"]
    assert metrics["ipc_durable_envelopes_preserved"] == metrics["runtime_restarts"]
    assert metrics["wal_obligations_preserved"] > 0
    assert metrics["process_budget_blocks"] > 0
    assert metrics["agent_budget_blocks"] > 0
    assert metrics["host_budget_blocks"] > 0
    assert metrics["child_faults"] > 0
    assert metrics["cancellations"] > 0
    assert metrics["durable_operations"] > 0
    assert metrics["dispatch_before_crash_count"] > 0
    assert metrics["reconcile_required_observed"] > 0
    assert metrics["reconciliations"] > 0
    assert metrics["external_effect_count"] == metrics["durable_operations"]
    assert metrics["authority_shrink_events"] > 0
    assert metrics["positive_coverage_passed"] is True
    assert metrics["unauthorized_effects"] == 0
    assert metrics["unsafe_duplicate_effects"] == 0
    assert metrics["cross_agent_resource_leaks"] == 0
    assert metrics["authority_escalations"] == 0
    assert metrics["stale_authority_restored"] == 0
    assert metrics["lost_durable_facts"] == 0
    assert metrics["recovery_corruptions"] == 0
    assert metrics["unresolved_mandatory_wal"] == 0


def test_multi_agent_runtimebench_record_aggregates_m1_to_m10() -> None:
    record = multi_agent_runtime_record(horizons=(10,))
    metrics = record.metrics

    assert record.benchmark_id == "B8_multi_agent_runtime"
    assert record.success is True
    assert metrics["scenario_count"] == 10
    assert metrics["scenario_pass_count"] == 10
    assert metrics["m10_pass"] is True
    assert metrics["semantic_invariants_passed"] is True
    assert metrics["runtime_restarts"] > 0
    assert (
        metrics["runtime_object_replacements_verified"]
        == metrics["runtime_restarts"]
    )
    assert metrics["ipc_redeliveries"] > 0
    assert metrics["process_budget_blocks"] > 0
    assert metrics["agent_budget_blocks"] > 0
    assert metrics["host_budget_blocks"] > 0
    assert metrics["dispatch_before_crash_count"] > 0
    assert metrics["reconcile_required_observed"] > 0
    assert metrics["reconciliations"] > 0
    assert metrics["external_effect_count"] == metrics["durable_operations"]
    assert metrics["authority_shrink_events"] > 0
    assert metrics["mandatory_wal_obligations_surfaced"] > 0
    assert metrics["unauthorized_effects"] == 0
    assert metrics["authority_escalations"] == 0
    assert metrics["stale_authority_restored"] == 0
    assert metrics["lost_mandatory_wal_obligations"] == 0
    assert metrics["recovery_corruptions"] == 0


def test_runtimebench_v0_8_runner_includes_b8() -> None:
    document = run_runtimebench()
    payload = document.as_dict()
    benchmark_ids = [
        benchmark["benchmark_id"] for benchmark in payload["benchmarks"]
    ]

    assert payload["runtimebench_version"] == "0.8"
    assert payload["runtime_version"] == "AgentKernel V0.8"
    assert payload["summary"]["total"] == 8
    assert payload["summary"]["passed"] == 8
    assert payload["summary"]["decision"] == "PASS"
    assert benchmark_ids[-1] == "B8_multi_agent_runtime"

    b8 = payload["benchmarks"][-1]
    assert b8["success"] is True
    assert b8["metrics"]["scenario_pass_count"] == 10
    assert b8["metrics"]["m10_horizon_100_pass"] is True
    assert b8["metrics"]["m10_horizon_500_pass"] is True
    assert b8["metrics"]["m10_horizon_1000_pass"] is True
    assert b8["metrics"]["runtime_restarts"] > 0
    assert (
        b8["metrics"]["runtime_object_replacements_verified"]
        == b8["metrics"]["runtime_restarts"]
    )
    assert b8["metrics"]["ipc_redeliveries"] > 0
    assert b8["metrics"]["process_budget_blocks"] > 0
    assert b8["metrics"]["agent_budget_blocks"] > 0
    assert b8["metrics"]["host_budget_blocks"] > 0
    assert b8["metrics"]["authority_shrink_events"] > 0
    assert b8["metrics"]["unsafe_duplicate_effects"] == 0
    assert b8["metrics"]["unresolved_mandatory_wal"] == 0


def test_long_horizon_multi_agent_is_semantically_deterministic() -> None:
    first = run_long_horizon_multi_agent(100)
    second = run_long_horizon_multi_agent(100)

    assert first.metrics == second.metrics


def test_multi_agent_runtime_record_is_semantically_deterministic() -> None:
    first = multi_agent_runtime_record(horizons=(100,))
    second = multi_agent_runtime_record(horizons=(100,))

    assert first.metrics == second.metrics
    assert first.raw_records == second.raw_records
