"""Adapters from existing leaf benchmarks to RuntimeBench family records."""

from __future__ import annotations

from collections.abc import Iterable
from tempfile import TemporaryDirectory

from agentkernel import (
    Agent,
    AgentBudget,
    ApproximateTokenEstimator,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    ContextProjector,
    CooperativeScheduler,
    EventType,
    LocalResourceStore,
    ModelUsage,
    ProcessBudgetExceeded,
    ProcessControlBlock,
    ProcessState,
    RESOURCE_READ_ACTION,
    ResourceAccessDenied,
    ResourceMetrics,
    ResourceOwner,
    ResourceService,
    SchedulerSafePoint,
    Session,
    UsageCollector,
)
from benchmarks.capability_runtime_benchmark import run as run_capability_runtime
from benchmarks.common.metrics import BenchmarkRecord
from benchmarks.context_vm.runner import run as run_context_vm
from benchmarks.durable_tool.runner import run as run_durable_tool
from benchmarks.recovery.runner import run as run_recovery
from benchmarks.resource_handle.runner import run as run_resource_handle
from benchmarks.runtimebench.fixtures import (
    boundary_isolation_fixture,
    capability_attack_fixture,
    context_truth_fixture,
    crash_fixture,
    long_horizon_fixture,
    resource_governance_fixture,
    side_effect_fixture,
)
from benchmarks.runtimebench.long_horizon import (
    LONG_HORIZON_PROFILES,
    run_long_horizon_profiles,
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
    """Run the implemented RuntimeBench V0.7 families."""

    return [
        fault_tolerance_record(),
        side_effect_safety_record(),
        context_truth_record(),
        capability_isolation_record(),
        resource_governance_record(),
        long_horizon_runtime_stability_record(),
        boundary_isolation_record(),
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


def resource_governance_record() -> RuntimeBenchRecord:
    records = _resource_governance_records()
    blocked_records = [
        record
        for record in records
        if record.case
        in {
            "token_budget_safe_point",
            "tool_budget_safe_point",
            "resource_budget_safe_point",
            "wall_time_budget_safe_point",
        }
    ]
    success = all(_success(record) for record in records)
    blocked_correctness = all(
        bool(_metric(record, "blocked", False))
        and _metric(record, "process_state") == ProcessState.BLOCKED.value
        for record in blocked_records
    )
    budget_overshoot = round(
        sum(_overshoot(record) for record in blocked_records),
        4,
    )
    resource_case = _find(records, "resource_budget_safe_point", case=True)
    usage_case = _find(records, "usage_snapshot_accuracy", case=True)
    unblock_case = _find(records, "unblock_recovery", case=True)

    return RuntimeBenchRecord(
        benchmark_id="B5_resource_governance",
        category="runtime_governance",
        description=(
            "Process runtime usage observation and cooperative scheduler "
            "blocking at resource budget safe points."
        ),
        fixture=resource_governance_fixture(),
        mechanism_under_test=(
            "ProcessUsageSnapshot",
            "UsageCollector",
            "CooperativeScheduler.safe_point",
            "AgentBudget runtime limits",
            "BLOCKED process state",
        ),
        baseline=BaselineSpec(
            name="naive_post_run_accounting",
            type="conceptual_internal_baseline",
            description=(
                "A loop that counts usage only after work completes and cannot "
                "stop at kernel safe points."
            ),
        ),
        failure_injection=FailureInjectionSpec(
            enabled=True,
            point="budget_pressure_at_cooperative_safe_points",
            description=(
                "Token, tool-call, resource-byte, and wall-time limits are "
                "exceeded before scheduler safe points."
            ),
        ),
        metrics={
            "budget_case_count": len(blocked_records),
            "blocked_case_count": sum(
                1 for record in blocked_records if bool(_metric(record, "blocked"))
            ),
            "budget_overshoot": budget_overshoot,
            "blocked_correctness": blocked_correctness,
            "resource_usage_accuracy": bool(
                _metric(resource_case, "resource_usage_accuracy", False)
            ),
            "usage_snapshot_accuracy": bool(
                _metric(usage_case, "usage_snapshot_accuracy", False)
            ),
            "safe_point_blocking_success": blocked_correctness,
            "unblock_correctness": bool(
                _metric(unblock_case, "unblock_correctness", False)
            ),
            "wall_time_observed": bool(
                _metric(
                    _find(records, "wall_time_budget_safe_point", case=True),
                    "wall_time_observed",
                    False,
                )
            ),
            "budget_recovery_success": _success(unblock_case),
            "raw_record_count": len(records),
        },
        result=ResultSpec(
            status=status_for(success),
            oracle="usage_observation_blocks_processes_at_scheduler_safe_points",
        ),
        success=success,
        limitations=(
            "cooperative safe points only",
            "synthetic deterministic resource pressure",
            "accounting is runtime observation, not durable billing truth",
            "does not implement preemptive scheduling or V0.8 process trees",
        ),
        raw_records=_raw_dicts(records),
    )


def long_horizon_runtime_stability_record(
    profiles: Iterable[int] = LONG_HORIZON_PROFILES,
) -> RuntimeBenchRecord:
    records = run_long_horizon_profiles(profiles)
    all_profiles_passed = all(_success(record) for record in records)
    step_counts = [int(_metric(record, "profile_steps", 0)) for record in records]
    steps_completed = [int(_metric(record, "steps_completed", 0)) for record in records]
    durable_truth_lost = sum(
        0 if bool(_metric(record, "truth_preserved", False)) else 1
        for record in records
    )
    duplicate_external_effects = sum(
        int(_metric(record, "duplicate_external_effects", 0)) for record in records
    )
    unauthorized_effect_count = sum(
        int(_metric(record, "unauthorized_effect_count", 0)) for record in records
    )
    resource_restart_success = all(
        bool(_metric(record, "resource_restart_success", False))
        for record in records
    )
    budget_blocks = sum(int(_metric(record, "budget_blocks", 0)) for record in records)
    budget_recoveries = sum(
        int(_metric(record, "budget_recoveries", 0)) for record in records
    )
    recovery_failure_count = sum(
        int(_metric(record, "recovery_failure_count", 0)) for record in records
    )
    recovery_mappings_legal = all(
        int(_metric(record, "reconcile_required_count", 0)) == 1
        and int(_metric(record, "recovery_failure_count", 0)) == 0
        for record in records
    )
    final_durable_consistency = all(
        bool(_metric(record, "final_durable_consistency", False))
        for record in records
    )
    success = (
        all_profiles_passed
        and step_counts == steps_completed
        and durable_truth_lost == 0
        and duplicate_external_effects == 0
        and unauthorized_effect_count == 0
        and resource_restart_success
        and budget_blocks == len(records)
        and budget_recoveries == len(records)
        and recovery_mappings_legal
        and final_durable_consistency
    )

    return RuntimeBenchRecord(
        benchmark_id="B6_long_horizon_runtime_stability",
        category="runtime_composition",
        description=(
            "Long-horizon composition of Session replay, WAL recovery, Context VM, "
            "Resource Handle restart reads, Capability denial, Scheduler budget "
            "blocking, and Usage accounting."
        ),
        fixture=long_horizon_fixture(),
        mechanism_under_test=(
            "Session Event Log",
            "Recovery replay",
            "ProcessControlBlock.from_recovery",
            "ContextManager.build_working_set",
            "ResourceService with LocalResourceStore",
            "DurableToolExecutor WAL and reconcile",
            "CapabilityEvaluator denial",
            "UsageCollector",
            "CooperativeScheduler.safe_point",
        ),
        baseline=BaselineSpec(
            name="naive_loop_with_truncation_and_retry",
            type="conceptual_internal_baseline",
            description=(
                "A single in-memory loop that truncates context, stores large "
                "outputs inline or out of band without kernel handles, retries "
                "mutations directly, and only checks resource usage after work."
            ),
        ),
        failure_injection=FailureInjectionSpec(
            enabled=True,
            point=(
                "long_horizon_dispatch_crash_resource_restart_capability_denial_"
                "budget_pressure"
            ),
            description=(
                "Each profile injects a durable dispatch crash, reloads from JSONL, "
                "reconciles, restarts resource reads, denies unauthorized actions, "
                "and blocks/unblocks at a scheduler safe point."
            ),
        ),
        metrics={
            "profile_count": len(records),
            "profile_steps": step_counts,
            "steps_completed": steps_completed,
            "session_events": sum(
                int(_metric(record, "session_events", 0)) for record in records
            ),
            "recovered_events": sum(
                int(_metric(record, "recovered_events", 0)) for record in records
            ),
            "durable_operations": sum(
                int(_metric(record, "durable_operations", 0)) for record in records
            ),
            "reconcile_required_count": sum(
                int(_metric(record, "reconcile_required_count", 0))
                for record in records
            ),
            "duplicate_external_effects": duplicate_external_effects,
            "context_working_set_tokens_peak": max(
                (
                    int(_metric(record, "context_working_set_tokens_peak", 0))
                    for record in records
                ),
                default=0,
            ),
            "reclaim_tokens_saved": sum(
                int(_metric(record, "reclaim_tokens_saved", 0)) for record in records
            ),
            "context_reclaim_count": sum(
                int(_metric(record, "context_reclaim_count", 0)) for record in records
            ),
            "resource_count": sum(
                int(_metric(record, "resource_count", 0)) for record in records
            ),
            "resource_bytes": sum(
                int(_metric(record, "resource_bytes", 0)) for record in records
            ),
            "resource_restart_success": resource_restart_success,
            "budget_blocks": budget_blocks,
            "budget_recoveries": budget_recoveries,
            "capability_denials": sum(
                int(_metric(record, "capability_denials", 0)) for record in records
            ),
            "unauthorized_effect_count": unauthorized_effect_count,
            "recovery_success_count": sum(
                int(_metric(record, "recovery_success_count", 0))
                for record in records
            ),
            "recovery_failure_count": recovery_failure_count,
            "agent_process_session_isolation": all(
                bool(_metric(record, "agent_process_session_isolation", False))
                for record in records
            ),
            "truth_preserved": durable_truth_lost == 0,
            "final_durable_consistency": final_durable_consistency,
            "wall_time_ms": round(
                sum(float(_metric(record, "wall_time_ms", 0.0)) for record in records),
                3,
            ),
            "raw_record_count": len(records),
        },
        result=ResultSpec(
            status=status_for(success),
            oracle=(
                "all_profiles_complete_without_truth_loss_duplicate_effects_"
                "unauthorized_effects_or_budget_recovery_failure"
            ),
        ),
        success=success,
        limitations=(
            "synthetic deterministic long-horizon fixture",
            "single-agent V0.7 runtime only",
            "does not implement or validate Process Tree, IPC, Multi-Agent, "
            "Delegation, Namespace, or Memory",
            "wall-clock measurements are local machine observations",
        ),
        raw_records=_raw_dicts(records),
    )


def boundary_isolation_record() -> RuntimeBenchRecord:
    records = _boundary_isolation_records()
    success = all(_success(record) for record in records)
    authority_leak_count = sum(
        int(_metric(record, "authority_leak", 0)) for record in records
    )
    durable_truth_mutation_count = sum(
        int(_metric(record, "durable_truth_mutation", 0)) for record in records
    )
    object_identity_confusion_count = sum(
        int(_metric(record, "object_identity_confusion", 0)) for record in records
    )
    regression_count = sum(0 if _success(record) else 1 for record in records)

    return RuntimeBenchRecord(
        benchmark_id="B7_boundary_isolation",
        category="runtime_object_model",
        description=(
            "Kernel object-model invariant checks for Agent, Process, Session, "
            "Context, Accounting, and ResourceStore boundaries."
        ),
        fixture=boundary_isolation_fixture(),
        mechanism_under_test=(
            "Agent as capability principal",
            "Process as runtime identity",
            "Session as durable truth",
            "Context Page as projection",
            "Usage accounting as observation",
            "ResourceStore as storage",
        ),
        baseline=BaselineSpec(
            name="collapsed_agent_loop_state",
            type="conceptual_internal_baseline",
            description=(
                "A framework where transcript, runtime state, authority, and "
                "storage driver responsibilities are not separated."
            ),
        ),
        failure_injection=FailureInjectionSpec(
            enabled=True,
            point="kernel_object_boundary_invariants",
            description=(
                "Process identity, accounting, context projection, and store "
                "access are exercised without granting them authority."
            ),
        ),
        metrics={
            "boundary_invariant_passed": success,
            "authority_leak_count": authority_leak_count,
            "durable_truth_mutation_count": durable_truth_mutation_count,
            "object_identity_confusion_count": object_identity_confusion_count,
            "regression_count": regression_count,
            "raw_record_count": len(records),
        },
        result=ResultSpec(
            status=status_for(success),
            oracle="runtime_objects_preserve_kernel_boundary_invariants",
        ),
        success=success,
        limitations=(
            "single-agent runtime only",
            "does not test delegation, namespace, IPC, or memory",
            "ResourceStore boundary is checked through ResourceService denial",
            "context projection checks do not measure semantic answer quality",
        ),
        raw_records=_raw_dicts(records),
    )


def _resource_governance_records() -> list[BenchmarkRecord]:
    return [
        _token_budget_case(),
        _tool_budget_case(),
        _resource_budget_case(),
        _wall_time_budget_case(),
        _usage_snapshot_accuracy_case(),
        _unblock_recovery_case(),
    ]


def _token_budget_case() -> BenchmarkRecord:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running_process(
        scheduler,
        process_id="process-token-budget",
        agent_id="agent-token-budget",
        session_id="session-token-budget",
        budget=AgentBudget(max_token_usage=10),
    )
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=7, output_tokens=4, total_tokens=11),
    )
    observed = _capture_budget_block(
        scheduler,
        process.process_id,
        SchedulerSafePoint.AFTER_LLM_CALL,
    )

    return _budget_record(
        case="token_budget_safe_point",
        safe_point=SchedulerSafePoint.AFTER_LLM_CALL,
        expected_limit="max_token_usage",
        expected_maximum=10,
        observed=observed,
        process=process,
    )


def _tool_budget_case() -> BenchmarkRecord:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running_process(
        scheduler,
        process_id="process-tool-budget",
        agent_id="agent-tool-budget",
        session_id="session-tool-budget",
        budget=AgentBudget(max_total_tool_calls=1),
    )
    collector.record_tool_call(process.process_id, count=2)
    observed = _capture_budget_block(
        scheduler,
        process.process_id,
        SchedulerSafePoint.AFTER_TOOL_CALL,
    )

    return _budget_record(
        case="tool_budget_safe_point",
        safe_point=SchedulerSafePoint.AFTER_TOOL_CALL,
        expected_limit="max_total_tool_calls",
        expected_maximum=1,
        observed=observed,
        process=process,
    )


def _resource_budget_case() -> BenchmarkRecord:
    clock = _FakeClock()
    collector = UsageCollector(clock=clock)
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running_process(
        scheduler,
        process_id="process-resource-budget",
        agent_id="agent-resource-budget",
        session_id="session-resource-budget",
        budget=AgentBudget(max_resource_bytes=4),
    )
    metrics = ResourceMetrics()
    owner = ResourceOwner("agent-resource-budget", "session-resource-budget")
    with TemporaryDirectory(prefix="agentkernel-runtimebench-") as root:
        service = ResourceService(
            LocalResourceStore(root),
            metrics=metrics,
            resource_id_factory=lambda: "res_budget",
            handle_id_factory=lambda: "hdl_budget",
        )
        handle = service.create_artifact(
            b"0123456789",
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="fixture.resource",
            source_tool_call_id="call-resource",
            source_operation_id="op-resource",
        )
        collector.begin_resource_metrics(process.process_id, metrics.snapshot())
        service.read(handle.uri, owner=owner, offset=0, limit=6)
        collector.observe_resource_metrics(process.process_id, metrics.snapshot())

    snapshot = collector.snapshot(process.process_id)
    observed = _capture_budget_block(
        scheduler,
        process.process_id,
        SchedulerSafePoint.AFTER_TOOL_CALL,
    )
    record = _budget_record(
        case="resource_budget_safe_point",
        safe_point=SchedulerSafePoint.AFTER_TOOL_CALL,
        expected_limit="max_resource_bytes",
        expected_maximum=4,
        observed=observed,
        process=process,
        extra={
            "resource_reads": snapshot.resource_reads,
            "resource_bytes": snapshot.resource_bytes,
            "resource_usage_accuracy": (
                snapshot.resource_reads == 1 and snapshot.resource_bytes == 6
            ),
        },
    )
    return record


def _wall_time_budget_case() -> BenchmarkRecord:
    clock = _FakeClock()
    collector = UsageCollector(clock=clock)
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running_process(
        scheduler,
        process_id="process-wall-time-budget",
        agent_id="agent-wall-time-budget",
        session_id="session-wall-time-budget",
        budget=AgentBudget(max_wall_time_seconds=1.0),
    )
    collector.start_process(process.process_id)
    clock.advance(1.25)
    observed = _capture_budget_block(
        scheduler,
        process.process_id,
        SchedulerSafePoint.BEFORE_STEP_START,
    )

    return _budget_record(
        case="wall_time_budget_safe_point",
        safe_point=SchedulerSafePoint.BEFORE_STEP_START,
        expected_limit="max_wall_time_seconds",
        expected_maximum=1.0,
        observed=observed,
        process=process,
        extra={"wall_time_observed": observed["usage"] == 1.25},
    )


def _usage_snapshot_accuracy_case() -> BenchmarkRecord:
    clock = _FakeClock()
    collector = UsageCollector(clock=clock)
    process_id = "process-usage-snapshot"
    collector.start_process(process_id)
    collector.record_llm_usage(
        process_id,
        ModelUsage(input_tokens=8, output_tokens=5, total_tokens=13),
        model_cost=0.125,
    )
    collector.record_tool_call(process_id, count=3)
    collector.record_resource_read(process_id, 512)
    collector.record_resource_read(process_id, 256)
    clock.advance(2.0)
    snapshot = collector.snapshot(process_id)
    success = (
        snapshot.token_usage == 13
        and snapshot.model_cost == 0.125
        and snapshot.tool_calls == 3
        and snapshot.resource_reads == 2
        and snapshot.resource_bytes == 768
        and snapshot.wall_time == 2.0
    )

    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="usage_snapshot_accuracy",
        strategy="process_usage_snapshot_totals",
        metrics={
            "token_usage": snapshot.token_usage,
            "model_cost": snapshot.model_cost,
            "tool_calls": snapshot.tool_calls,
            "resource_reads": snapshot.resource_reads,
            "resource_bytes": snapshot.resource_bytes,
            "wall_time": snapshot.wall_time,
            "usage_snapshot_accuracy": success,
            "success": success,
        },
    )


def _unblock_recovery_case() -> BenchmarkRecord:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running_process(
        scheduler,
        process_id="process-unblock-recovery",
        agent_id="agent-unblock-recovery",
        session_id="session-unblock-recovery",
        budget=AgentBudget(max_token_usage=5),
    )
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    first = _capture_budget_block(
        scheduler,
        process.process_id,
        SchedulerSafePoint.AFTER_LLM_CALL,
    )
    collector.reset_process(process.process_id)
    scheduler.unblock(process.process_id)
    state_after_unblock = process.state.value
    scheduler.dispatch(process.process_id)
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )
    second_safe_point_ok = True
    try:
        scheduler.safe_point(
            process.process_id,
            SchedulerSafePoint.AFTER_LLM_CALL,
        )
    except ProcessBudgetExceeded:
        second_safe_point_ok = False
    snapshot = collector.snapshot(process.process_id)
    success = (
        first["blocked"]
        and state_after_unblock == ProcessState.READY.value
        and process.state is ProcessState.RUNNING
        and second_safe_point_ok
        and snapshot.token_usage == 2
    )

    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="unblock_recovery",
        strategy="reset_usage_then_unblock_process",
        metrics={
            "blocked_before_recovery": bool(first["blocked"]),
            "state_after_unblock": state_after_unblock,
            "state_after_dispatch": process.state.value,
            "token_usage_after_reset": snapshot.token_usage,
            "second_safe_point_ok": second_safe_point_ok,
            "unblock_correctness": success,
            "success": success,
        },
    )


def _boundary_isolation_records() -> list[BenchmarkRecord]:
    return [
        _agent_process_boundary_case(),
        _process_session_boundary_case(),
        _context_truth_boundary_case(),
        _accounting_authority_boundary_case(),
        _resource_store_authorization_boundary_case(),
    ]


def _agent_process_boundary_case() -> BenchmarkRecord:
    grant = CapabilityGrant(
        subject="agent-boundary",
        action=RESOURCE_READ_ACTION,
        resource_scope="artifact://boundary/**",
    )
    agent = Agent.create(
        agent_id="agent-boundary",
        session=Session("session-boundary-agent"),
        capability_grants=(grant,),
    )
    process = ProcessControlBlock.create(
        process_id="process-boundary",
        agent=agent.control,
    )
    evaluator = CapabilityEvaluator(agent.control.capability_grants)
    agent_decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=agent.control.agent_id,
            action=RESOURCE_READ_ACTION,
            resource="artifact://boundary/fact.txt",
        )
    )
    process_decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=process.process_id,
            action=RESOURCE_READ_ACTION,
            resource="artifact://boundary/fact.txt",
        )
    )
    success = (
        agent.control.agent_id != process.process_id
        and process.capability_snapshot.agent_id == agent.control.agent_id
        and agent_decision.allowed
        and not process_decision.allowed
    )

    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="agent_is_not_process",
        strategy="capability_principal_vs_runtime_identity",
        metrics={
            "agent_id": agent.control.agent_id,
            "process_id": process.process_id,
            "capability_snapshot_agent_id": process.capability_snapshot.agent_id,
            "agent_authorized": agent_decision.allowed,
            "process_authorized_as_subject": process_decision.allowed,
            "authority_leak": 0 if not process_decision.allowed else 1,
            "object_identity_confusion": 0 if success else 1,
            "durable_truth_mutation": 0,
            "success": success,
        },
    )


def _process_session_boundary_case() -> BenchmarkRecord:
    session = Session("session-boundary-process")
    agent = Agent.create(agent_id="agent-process-boundary", session=session)
    process = ProcessControlBlock.create(
        process_id="process-session-boundary",
        agent=agent.control,
    )
    process.transition(ProcessState.READY)
    event_count_before_process_change = len(session.events)
    process.transition(ProcessState.RUNNING)
    event_count_after_process_change = len(session.events)
    process.transition(ProcessState.READY)
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "durable fact"})
    state_after_session_append = process.state.value
    success = (
        event_count_before_process_change == 0
        and event_count_after_process_change == 0
        and len(session.events) == 1
        and state_after_session_append == ProcessState.READY.value
    )

    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="process_is_not_session",
        strategy="lifecycle_state_vs_durable_journal",
        metrics={
            "event_count_before_process_change": event_count_before_process_change,
            "event_count_after_process_change": event_count_after_process_change,
            "event_count_after_session_append": len(session.events),
            "state_after_session_append": state_after_session_append,
            "authority_leak": 0,
            "object_identity_confusion": 0 if success else 1,
            "durable_truth_mutation": 0
            if event_count_after_process_change == event_count_before_process_change
            else 1,
            "success": success,
        },
    )


def _context_truth_boundary_case() -> BenchmarkRecord:
    session = Session("session-boundary-context")
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "durable fact"})
    event_count_before_projection = len(session.events)
    pages = ContextProjector(ApproximateTokenEstimator()).project(
        session,
        system_prompt="kernel policy prompt",
    )
    event_count_after_projection = len(session.events)
    projected_page_count = len(pages)
    success = (
        event_count_before_projection == 1
        and event_count_after_projection == event_count_before_projection
        and projected_page_count > event_count_after_projection
    )

    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="context_is_not_truth",
        strategy="projection_does_not_mutate_session",
        metrics={
            "event_count_before_projection": event_count_before_projection,
            "event_count_after_projection": event_count_after_projection,
            "projected_page_count": projected_page_count,
            "projection_mutated_session": event_count_after_projection
            != event_count_before_projection,
            "authority_leak": 0,
            "object_identity_confusion": 0 if success else 1,
            "durable_truth_mutation": 0
            if event_count_after_projection == event_count_before_projection
            else 1,
            "success": success,
        },
    )


def _accounting_authority_boundary_case() -> BenchmarkRecord:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(usage_collector=collector)
    agent = _runtime_agent(
        "agent-accounting-boundary",
        "session-accounting-boundary",
        budget=AgentBudget(max_token_usage=5),
    )
    process = scheduler.create_process(
        process_id="process-accounting-boundary",
        agent=agent.control,
    )
    scheduler.dispatch(process.process_id)
    collector.record_llm_usage(
        process.process_id,
        ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
    )
    exceeded = collector.exceeded_budget(process.process_id, process.budget)
    state_after_observation = process.state.value
    session_event_count = len(agent.session.events)
    success = (
        exceeded is not None
        and state_after_observation == ProcessState.RUNNING.value
        and session_event_count == 0
    )

    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="accounting_is_not_authority",
        strategy="usage_observation_without_scheduler_transition",
        metrics={
            "exceeded_limit": exceeded.limit if exceeded is not None else None,
            "state_after_observation": state_after_observation,
            "session_event_count": session_event_count,
            "observation_did_not_block": state_after_observation
            == ProcessState.RUNNING.value,
            "authority_leak": 0,
            "object_identity_confusion": 0 if success else 1,
            "durable_truth_mutation": 0 if session_event_count == 0 else 1,
            "success": success,
        },
    )


def _resource_store_authorization_boundary_case() -> BenchmarkRecord:
    owner = ResourceOwner("agent-store-boundary", "session-store-boundary")
    with TemporaryDirectory(prefix="agentkernel-runtimebench-store-") as root:
        store = _RecordingResourceStore(LocalResourceStore(root))
        service = ResourceService(
            store,
            resource_id_factory=lambda: "res_store",
            handle_id_factory=lambda: "hdl_store",
        )
        handle = service.create_artifact(
            b"secret",
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="fixture.store",
            source_tool_call_id="call-store",
            source_operation_id="op-store",
        )
        denied = False
        try:
            service.read(
                handle.uri,
                owner=owner,
                capability_evaluator=CapabilityEvaluator(),
            )
        except ResourceAccessDenied:
            denied = True
        resource_reads = service.metrics.resource_reads

    success = denied and store.read_calls == 0 and resource_reads == 0
    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case="resource_store_is_not_authorization",
        strategy="resource_service_denies_before_payload_read",
        metrics={
            "authorization_denied": denied,
            "store_stat_calls": store.stat_calls,
            "store_read_calls": store.read_calls,
            "resource_service_read_metrics": resource_reads,
            "payload_read_after_denial": store.read_calls > 0,
            "authority_leak": 0 if denied and store.read_calls == 0 else 1,
            "object_identity_confusion": 0 if success else 1,
            "durable_truth_mutation": 0,
            "success": success,
        },
    )


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, amount: float) -> None:
        self.now += amount


class _RecordingResourceStore:
    def __init__(self, inner: LocalResourceStore) -> None:
        self._inner = inner
        self.commit_calls = 0
        self.stat_calls = 0
        self.read_calls = 0

    def commit(self, metadata: object, data: bytes) -> None:
        self.commit_calls += 1
        self._inner.commit(metadata, data)  # type: ignore[arg-type]

    def stat(self, resource_id: str) -> object:
        self.stat_calls += 1
        return self._inner.stat(resource_id)

    def read(self, resource_id: str, offset: int, limit: int) -> bytes:
        self.read_calls += 1
        return self._inner.read(resource_id, offset, limit)

    def list_metadata(self) -> object:
        return self._inner.list_metadata()


def _runtime_agent(
    agent_id: str,
    session_id: str,
    *,
    budget: AgentBudget | None = None,
) -> Agent:
    return Agent.create(
        agent_id=agent_id,
        session=Session(session_id),
        budget=budget,
    )


def _running_process(
    scheduler: CooperativeScheduler,
    *,
    process_id: str,
    agent_id: str,
    session_id: str,
    budget: AgentBudget,
) -> ProcessControlBlock:
    agent = _runtime_agent(agent_id, session_id, budget=budget)
    process = scheduler.create_process(process_id=process_id, agent=agent.control)
    scheduler.dispatch(process_id)
    return process


def _capture_budget_block(
    scheduler: CooperativeScheduler,
    process_id: str,
    safe_point: SchedulerSafePoint,
) -> dict[str, object]:
    try:
        scheduler.safe_point(process_id, safe_point)
    except ProcessBudgetExceeded as error:
        return {
            "blocked": True,
            "limit": error.exceeded.limit,
            "usage": error.exceeded.usage,
            "maximum": error.exceeded.maximum,
        }
    return {
        "blocked": False,
        "limit": None,
        "usage": None,
        "maximum": None,
    }


def _budget_record(
    *,
    case: str,
    safe_point: SchedulerSafePoint,
    expected_limit: str,
    expected_maximum: int | float,
    observed: dict[str, object],
    process: ProcessControlBlock,
    extra: dict[str, object] | None = None,
) -> BenchmarkRecord:
    usage = observed["usage"]
    maximum = observed["maximum"]
    success = (
        observed["blocked"] is True
        and observed["limit"] == expected_limit
        and maximum == expected_maximum
        and process.state is ProcessState.BLOCKED
        and process.blocked_reason == f"budget_exceeded:{expected_limit}"
    )
    metrics = {
        "safe_point": safe_point.value,
        "budget_limit": expected_limit,
        "budget_maximum": expected_maximum,
        "observed_usage": usage,
        "budget_overshoot": _number_overshoot(usage, maximum),
        "exceeded_limit": observed["limit"],
        "blocked": bool(observed["blocked"]),
        "process_state": process.state.value,
        "blocked_reason": process.blocked_reason,
        "success": success,
    }
    if extra is not None:
        metrics.update(extra)
    return BenchmarkRecord(
        benchmark="runtimebench_v0.7",
        case=case,
        strategy="scheduler_budget_safe_point",
        metrics=metrics,
    )


def _overshoot(record: BenchmarkRecord) -> float:
    return _number_overshoot(
        _metric(record, "observed_usage"),
        _metric(record, "budget_maximum"),
    )


def _number_overshoot(usage: object, maximum: object) -> float:
    if not isinstance(usage, (int, float)) or not isinstance(maximum, (int, float)):
        return 0.0
    return round(max(0.0, float(usage) - float(maximum)), 4)


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
