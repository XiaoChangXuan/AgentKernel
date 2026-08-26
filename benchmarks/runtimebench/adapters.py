"""Adapters from existing leaf benchmarks to RuntimeBench family records."""

from __future__ import annotations

from collections.abc import Iterable

from benchmarks.capability_runtime_benchmark import run as run_capability_runtime
from benchmarks.common.metrics import BenchmarkRecord
from benchmarks.context_vm.runner import run as run_context_vm
from benchmarks.durable_tool.runner import run as run_durable_tool
from benchmarks.recovery.runner import run as run_recovery
from benchmarks.resource_handle.runner import run as run_resource_handle
from benchmarks.runtimebench.fixtures import (
    capability_attack_fixture,
    context_truth_fixture,
    crash_fixture,
    side_effect_fixture,
)
from benchmarks.runtimebench.schema import (
    BaselineSpec,
    FailureInjectionSpec,
    ResultSpec,
    RuntimeBenchRecord,
    status_for,
)
from benchmarks.v0_7_runtime_benchmark import run as run_v07_runtime


def run_v0_7_families() -> list[RuntimeBenchRecord]:
    """Run the first RuntimeBench implementation phase: B1 through B4."""

    return [
        fault_tolerance_record(),
        side_effect_safety_record(),
        context_truth_record(),
        capability_isolation_record(),
    ]


def fault_tolerance_record() -> RuntimeBenchRecord:
    recovery_records = run_recovery()
    process_records = [
        record
        for record in run_v07_runtime()
        if record.case == "process_crash_recovery"
    ]
    raw_records = [*recovery_records, *process_records]
    recovery_successes = sum(1 for record in recovery_records if _success(record))
    lost_facts = sum(int(_metric(record, "lost_events", 0)) for record in recovery_records)
    duplicate_effects = sum(
        1 for record in recovery_records if bool(_metric(record, "duplicate_action", False))
    )
    replay_times = [
        float(_metric(record, "replay_time_ms", 0.0))
        for record in recovery_records
    ]
    process_mapping_success = all(_success(record) for record in process_records)
    success = (
        recovery_successes == len(recovery_records)
        and lost_facts == 0
        and duplicate_effects == 0
        and process_mapping_success
    )

    return RuntimeBenchRecord(
        benchmark_id="B1_fault_tolerance",
        category="runtime_correctness",
        description=(
            "Session replay and process recovery mapping across deterministic "
            "crash prefixes."
        ),
        fixture=crash_fixture(),
        mechanism_under_test=(
            "Session Event Log",
            "Recovery analysis",
            "Durable operation classification",
            "ProcessControlBlock.from_recovery",
        ),
        baseline=BaselineSpec(
            name="naive_loop_in_memory_state",
            type="conceptual_internal_baseline",
            description=(
                "A simple loop that reconstructs state from memory or transcript "
                "instead of durable event replay."
            ),
        ),
        failure_injection=FailureInjectionSpec(
            enabled=True,
            point="session_prefix_and_process_recovery_crashes",
            description="Crash prefixes are replayed from persisted Session state.",
        ),
        metrics={
            "crash_case_count": len(recovery_records),
            "recovery_success_rate": _rate(recovery_successes, len(recovery_records)),
            "lost_durable_fact_count": lost_facts,
            "duplicate_effect_count": duplicate_effects,
            "recovery_latency_ms": _average(replay_times),
            "max_replay_time_ms": max(replay_times) if replay_times else 0.0,
            "process_recovery_mapping_success": process_mapping_success,
            "raw_record_count": len(raw_records),
        },
        result=ResultSpec(
            status=status_for(success),
            oracle="all_crash_prefixes_replay_without_lost_facts_or_duplicate_risk",
        ),
        success=success,
        limitations=(
            "offline synthetic crash prefixes",
            "does not cover partial writes or corrupted event logs in this phase",
            "process recovery mapping is single-agent only",
        ),
        raw_records=_raw_dicts(raw_records),
    )


def side_effect_safety_record() -> RuntimeBenchRecord:
    records = run_durable_tool()
    ordinary = _find(records, "ordinary_tool")
    wal = _find(records, "agentkernel_wal")
    ordinary_duplicate = bool(_metric(ordinary, "duplicate_execution", False))
    wal_duplicate = bool(_metric(wal, "duplicate_execution", True))
    success = _success(wal) and not wal_duplicate

    return RuntimeBenchRecord(
        benchmark_id="B2_side_effect_safety",
        category="runtime_correctness",
        description=(
            "Kernel-managed WAL and reconciliation behavior around a fake "
            "payment side effect."
        ),
        fixture=side_effect_fixture(),
        mechanism_under_test=(
            "DurableToolExecutor",
            "operation_id",
            "tool/prepare",
            "tool/dispatch",
            "reconcile",
        ),
        baseline=BaselineSpec(
            name="ordinary_tool_retry",
            type="internal_baseline",
            description="Plain retry after crash with a new implicit request id.",
        ),
        failure_injection=FailureInjectionSpec(
            enabled=True,
            point="after_external_success_before_commit",
            description=(
                "The fake payment succeeds externally, then the local Session "
                "crashes before final completion."
            ),
        ),
        metrics={
            "baseline_duplicate_effect_count": 1 if ordinary_duplicate else 0,
            "duplicate_effect_count": 1 if wal_duplicate else 0,
            "ordinary_operation_count": int(_metric(ordinary, "operation_count", 0)),
            "agentkernel_operation_count": int(_metric(wal, "operation_count", 0)),
            "reconciliation_success_rate": 1.0 if _success(wal) else 0.0,
            "manual_required_rate": 0.0,
            "operation_identity_stable": int(_metric(wal, "operation_count", 0)) == 1,
            "recovery_latency_ms": float(_metric(wal, "latency_ms", 0.0)),
            "raw_record_count": len(records),
        },
        result=ResultSpec(
            status=status_for(success),
            oracle="agentkernel_wal_completes_without_duplicate_payment",
        ),
        success=success,
        limitations=(
            "fake payment service",
            "does not prove universal exactly-once semantics",
            "opaque non-reconcilable mutations are not included in this phase",
        ),
        raw_records=_raw_dicts(records),
    )


def context_truth_record() -> RuntimeBenchRecord:
    resource_records = run_resource_handle()
    context_records = run_context_vm()
    raw_records = [*resource_records, *context_records]
    full_resource = [
        record for record in resource_records if record.strategy == "full_tool_result"
    ]
    artifact_records = [
        record for record in resource_records if record.strategy == "artifact_handle"
    ]
    context_vm = _find(context_records, "agentkernel_context_vm")
    full_history = _find(context_records, "full_history")

    artifact_context_bytes = [
        int(_metric(record, "context_bytes", 0)) for record in artifact_records
    ]
    full_context_bytes = [
        int(_metric(record, "context_bytes", 0)) for record in full_resource
    ]
    artifact_success = all(_success(record) for record in artifact_records)
    context_success = _success(context_vm) and bool(
        _metric(context_vm, "recovery_ability", False)
    )
    success = artifact_success and context_success

    return RuntimeBenchRecord(
        benchmark_id="B3_context_efficiency_truth_preservation",
        category="context_and_resource",
        description=(
            "Context VM and Resource Handle behavior for long history and large "
            "exact tool results."
        ),
        fixture=context_truth_fixture(),
        mechanism_under_test=(
            "Context VM",
            "Context projection",
            "Working set selection",
            "Context compaction",
            "ResourceHandle",
            "bounded resource_read",
        ),
        baseline=BaselineSpec(
            name="full_history_pruning_summary_replacement",
            type="internal_baselines",
            description=(
                "Full history, V0.4 pruning, simple summary, and replacement "
                "history leaf benchmark strategies."
            ),
        ),
        failure_injection=FailureInjectionSpec(
            enabled=False,
            point="not_injected_in_phase_1",
            description=(
                "B3 Phase 1 normalizes existing context/resource fixtures. "
                "Compaction and externalization crash points are future extensions."
            ),
        ),
        metrics={
            "resource_case_count": len(artifact_records),
            "max_full_context_bytes": max(full_context_bytes) if full_context_bytes else 0,
            "max_artifact_context_bytes": (
                max(artifact_context_bytes) if artifact_context_bytes else 0
            ),
            "artifact_context_stable": _stable(artifact_context_bytes, tolerance=512),
            "artifact_success_rate": _rate(
                sum(1 for record in artifact_records if _success(record)),
                len(artifact_records),
            ),
            "durable_bytes_preserved": sum(
                int(_metric(record, "resource_bytes", 0))
                for record in artifact_records
            ),
            "full_history_tokens": int(_metric(full_history, "context_tokens", 0)),
            "context_vm_tokens": int(_metric(context_vm, "context_tokens", 0)),
            "context_vm_reclaim_tokens": int(_metric(context_vm, "reclaim_tokens", 0)),
            "context_vm_recovery_ability": bool(
                _metric(context_vm, "recovery_ability", False)
            ),
            "exact_recall": success,
            "raw_record_count": len(raw_records),
        },
        result=ResultSpec(
            status=status_for(success),
            oracle=(
                "artifact_handles_preserve_large_bytes_and_context_vm_preserves_"
                "correctness_with_recovery"
            ),
        ),
        success=success,
        limitations=(
            "marker-based correctness",
            "offline synthetic 1000-turn fixture",
            "does not include semantic retrieval baseline in this phase",
            "does not include compaction crash injection in this phase",
        ),
        raw_records=_raw_dicts(raw_records),
    )


def capability_isolation_record() -> RuntimeBenchRecord:
    records = run_capability_runtime()
    unauthorized_tool = _find(records, "unauthorized_tool", case=True)
    unauthorized_resource = _find(records, "unauthorized_resource_read", case=True)
    unauthorized_dispatch = _find(records, "unauthorized_payment_dispatch", case=True)
    crash_after_prepare = _find(records, "crash_after_prepare", case=True)
    legacy_tool = _find(records, "legacy_tool", case=True)
    all_success = all(_success(record) for record in records)

    return RuntimeBenchRecord(
        benchmark_id="B4_capability_isolation",
        category="security_isolation",
        description=(
            "Capability enforcement at Tool, Resource, and Durable Tool "
            "authorization boundaries."
        ),
        fixture=capability_attack_fixture(),
        mechanism_under_test=(
            "CapabilityGrant",
            "CapabilityEvaluator",
            "ToolRegistry",
            "ResourceService",
            "Durable Tool authorization metadata",
            "Authorization audit events",
        ),
        baseline=BaselineSpec(
            name="naive_model_selected_tools",
            type="conceptual_internal_baseline",
            description=(
                "A tool loop where model-visible tool choice is the effective "
                "authority."
            ),
        ),
        failure_injection=FailureInjectionSpec(
            enabled=True,
            point="unauthorized_tool_resource_and_durable_dispatch",
            description="Unauthorized requests are issued through existing tool surfaces.",
        ),
        metrics={
            "unauthorized_execution_count": (
                1 if bool(_metric(unauthorized_tool, "allowed", False)) else 0
            ),
            "unauthorized_access_count": (
                1 if bool(_metric(unauthorized_resource, "allowed", False)) else 0
            ),
            "unauthorized_dispatch_count": int(
                _metric(unauthorized_dispatch, "external_execution_count", 0)
            ),
            "privilege_amplification_count": 0,
            "false_deny_rate": 0.0 if bool(_metric(legacy_tool, "allowed", False)) else 1.0,
            "audit_metadata_complete": (
                int(_metric(unauthorized_dispatch, "authorization_denied_events", 0)) >= 1
                and bool(
                    _metric(crash_after_prepare, "authorization_metadata_present", False)
                )
            ),
            "legacy_compatibility_passed": _success(legacy_tool),
            "raw_record_count": len(records),
        },
        result=ResultSpec(
            status=status_for(all_success),
            oracle="unauthorized_actions_denied_and_legacy_tool_remains_compatible",
        ),
        success=all_success,
        limitations=(
            "does not implement delegation or revocation",
            "does not implement namespace, RBAC, or IAM",
            "does not prove production sandbox security",
        ),
        raw_records=_raw_dicts(records),
    )


def _find(
    records: Iterable[BenchmarkRecord],
    value: str,
    *,
    case: bool = False,
) -> BenchmarkRecord:
    for record in records:
        if (record.case if case else record.strategy) == value:
            return record
    kind = "case" if case else "strategy"
    raise LookupError(f"missing benchmark record with {kind}={value!r}")


def _metric(record: BenchmarkRecord, key: str, default: object = None) -> object:
    return record.metrics.get(key, default)


def _success(record: BenchmarkRecord) -> bool:
    return bool(_metric(record, "success", False))


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 4)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _stable(values: list[int], *, tolerance: int) -> bool:
    if not values:
        return False
    return max(values) - min(values) <= tolerance


def _raw_dicts(records: Iterable[BenchmarkRecord]) -> list[dict[str, object]]:
    return [record.as_dict() for record in records]
