from __future__ import annotations

from benchmarks.runtimebench.adapters import multi_agent_runtime_record
from benchmarks.runtimebench.multi_agent import (
    run_long_horizon_multi_agent,
    run_multi_agent_runtime_records,
)
from benchmarks.runtimebench.runner import run_runtimebench


def test_multi_agent_runtime_leaf_records_pass() -> None:
    records = run_multi_agent_runtime_records((10,))

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


def test_long_horizon_multi_agent_preserves_invariants() -> None:
    record = run_long_horizon_multi_agent(10)
    metrics = record.metrics

    assert metrics["success"] is True
    assert metrics["logical_steps"] == 10
    assert metrics["ipc_sent"] == 10
    assert metrics["ipc_deliveries"] == 10
    assert metrics["ipc_acks"] == 10
    assert metrics["unauthorized_effects"] == 0
    assert metrics["unsafe_duplicate_effects"] == 0
    assert metrics["cross_agent_resource_leaks"] == 0
    assert metrics["authority_escalations"] == 0
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
    assert metrics["unauthorized_effects"] == 0
    assert metrics["authority_escalations"] == 0
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
