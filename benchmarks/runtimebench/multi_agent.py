"""RuntimeBench V0.8 multi-agent evidence scenarios.

The scenarios in this module are benchmark fixtures, not new Kernel features.
They exercise already implemented V0.8 primitives through public AgentKernel
APIs and report semantic invariants as deterministic ``BenchmarkRecord`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agentkernel import (
    ARTIFACT_RESOURCE_SCOPE,
    AgentBudget,
    AgentRegistry,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    CooperativeScheduler,
    DelegateCapabilityRequest,
    EventType,
    HostBudget,
    IPCMessageState,
    InMemoryIPCPersistence,
    KernelIPC,
    LocalResourceStore,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessBudgetExceeded,
    ProcessCancelled,
    ProcessManager,
    ProcessRecoveryDisposition,
    ProcessState,
    RESOURCE_READ_ACTION,
    ReconcileStatus,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    ResourceShareRegistry,
    SchedulerSafePoint,
    Session,
    ToolCall,
    ToolEffectKind,
    ToolResult,
    UsageCollector,
    recover_multi_agent_runtime,
)
from benchmarks.common.metrics import BenchmarkRecord


MULTI_AGENT_HORIZONS = (100, 500, 1000)
BENCHMARK = "runtimebench_v0.8_multi_agent"


@dataclass(slots=True)
class _World:
    registry: AgentRegistry
    manager: ProcessManager
    scheduler: CooperativeScheduler
    collector: UsageCollector
    store: LocalResourceStore
    shares: ResourceShareRegistry
    resources: ResourceService
    ipc_persistence: InMemoryIPCPersistence
    ipc: KernelIPC
    root_grant: CapabilityGrant
    parent_session: Session
    reader_session: Session
    worker_session: Session
    parent_process_id: str
    reader_process_id: str
    worker_process_id: str
    cancel_process_id: str
    channel_id: str
    handle_uri: str

    @property
    def sessions(self) -> tuple[Session, Session, Session]:
        return (self.parent_session, self.reader_session, self.worker_session)


def run_multi_agent_runtime_records(
    horizons: Iterable[int] = MULTI_AGENT_HORIZONS,
) -> list[BenchmarkRecord]:
    """Run the B8 M1-M10 multi-agent scenarios."""

    horizon_tuple = tuple(horizons)
    return [
        _m1_identity_isolation_case(),
        _m2_tree_separation_case(),
        _m3_capability_delegation_narrowing_case(),
        _m4_ipc_authority_isolation_case(),
        _m5_resource_sharing_isolation_case(),
        _m6_hierarchical_budget_isolation_case(),
        _m7_fault_cancellation_isolation_case(),
        _m8_integrated_recovery_case(),
        _m9_authority_shrink_after_restart_case(),
        _m10_long_horizon_composition_case(horizon_tuple),
    ]


def run_long_horizon_multi_agent(horizon: int) -> BenchmarkRecord:
    """Run one deterministic M10 profile for tests and aggregation."""

    if isinstance(horizon, bool) or horizon <= 0:
        raise ValueError("horizon must be a positive integer")

    with TemporaryDirectory(prefix="agentkernel-runtimebench-v08-m10-") as root:
        world = _create_world(Path(root), prefix=f"h{horizon}")
        counters = {
            "crashes": 0,
            "recoveries": 0,
            "runtime_restarts": 0,
            "runtime_object_replacements_verified": 0,
            "agent_ids_preserved": 0,
            "process_ids_preserved": 0,
            "session_durable_events_preserved": 0,
            "resource_metadata_preserved": 0,
            "resource_share_preserved": 0,
            "ipc_durable_envelopes_preserved": 0,
            "wal_obligations_preserved": 0,
            "ipc_sent": 0,
            "ipc_deliveries": 0,
            "ipc_redeliveries": 0,
            "ipc_acks": 0,
            "resource_allowed": 0,
            "resource_denied": 0,
            "delegation_allows": 1,
            "delegation_denies": 0,
            "process_budget_blocks": 0,
            "agent_budget_blocks": 0,
            "host_budget_blocks": 0,
            "child_faults": 0,
            "cancellations": 0,
            "durable_operations": 0,
            "dispatch_before_crash_count": 0,
            "reconcile_required_observed": 0,
            "reconciliations": 0,
            "external_effect_count": 0,
            "authority_shrink_events": 0,
            "unauthorized_effects": 0,
            "unsafe_duplicate_effects": 0,
            "cross_agent_resource_leaks": 0,
            "authority_escalations": 0,
            "stale_authority_restored": 0,
            "lost_durable_facts": 0,
            "recovery_corruptions": 0,
            "unresolved_mandatory_wal": 0,
        }
        external_effects: set[str] = set()
        recovered_event_total = 0
        resource_bytes_read = 0
        crash_interval = max(10, horizon // 10)
        denial_interval = max(5, horizon // 12)
        process_budget_step = min(2, horizon)
        agent_budget_step = min(3, horizon)
        host_budget_step = min(4, horizon)
        redelivery_step = min(max(5, horizon // 5), horizon)
        durable_crash_step = min(max(6, horizon // 4), horizon)
        authority_shrink_step = min(max(7, horizon // 2), horizon)
        child_fault_step = min(max(8, horizon // 3), horizon)
        cancellation_step = min(max(9, (2 * horizon) // 3), horizon)

        for step in range(1, horizon + 1):
            world.ipc.send(
                channel_id=world.channel_id,
                sender_process_id=world.parent_process_id,
                payload={"step": step, "kind": "work"},
                message_id=f"message-{step}",
                correlation_id=f"corr-message-{step}",
            )
            counters["ipc_sent"] += 1

            if step == redelivery_step:
                delivered_before_crash = world.ipc.receive(
                    channel_id=world.channel_id,
                    receiver_agent_id="agent-reader",
                    receiver_process_id=world.reader_process_id,
                )
                if delivered_before_crash is None:
                    counters["recovery_corruptions"] += 1
                else:
                    counters["ipc_deliveries"] += 1
                    counters["crashes"] += 1
                    world, restart_metrics = _replace_runtime_after_recovery(
                        world,
                        current_capability_grants={
                            "agent-parent": (world.root_grant,),
                        },
                    )
                    recovered_event_total += int(
                        restart_metrics["recovered_events"]
                    )
                    _merge_restart_metrics(counters, restart_metrics)
                    redelivered = world.ipc.receive(
                        channel_id=world.channel_id,
                        receiver_agent_id="agent-reader",
                        receiver_process_id=world.reader_process_id,
                    )
                    if (
                        redelivered is not None
                        and redelivered.message_id
                        == delivered_before_crash.message_id
                        and redelivered.delivery_attempts
                        == delivered_before_crash.delivery_attempts + 1
                    ):
                        counters["ipc_deliveries"] += 1
                        counters["ipc_redeliveries"] += 1
                        world.ipc.ack(
                            channel_id=world.channel_id,
                            message_id=redelivered.message_id,
                            receiver_agent_id="agent-reader",
                            receiver_process_id=world.reader_process_id,
                        )
                        counters["ipc_acks"] += 1
                    else:
                        counters["recovery_corruptions"] += 1
            else:
                delivered = world.ipc.receive(
                    channel_id=world.channel_id,
                    receiver_agent_id="agent-reader",
                    receiver_process_id=world.reader_process_id,
                )
                if delivered is not None:
                    counters["ipc_deliveries"] += 1
                    world.ipc.ack(
                        channel_id=world.channel_id,
                        message_id=delivered.message_id,
                        receiver_agent_id="agent-reader",
                        receiver_process_id=world.reader_process_id,
                    )
                    counters["ipc_acks"] += 1

            if step % 10 == 0:
                try:
                    data = world.resources.read(
                        world.handle_uri,
                        owner=ResourceOwner("agent-reader", "session-reader"),
                        offset=0,
                        limit=6,
                        capability_evaluator=_agent_evaluator(
                            world,
                            "agent-reader",
                        ),
                    ).data
                    counters["resource_allowed"] += 1
                    resource_bytes_read += len(data)
                except ResourceAccessDenied:
                    counters["cross_agent_resource_leaks"] += 1

            if step % denial_interval == 0:
                try:
                    world.resources.read(
                        world.handle_uri,
                        owner=ResourceOwner("agent-worker", "session-worker"),
                        offset=0,
                        limit=6,
                        capability_evaluator=_agent_evaluator(
                            world,
                            "agent-worker",
                        ),
                    )
                    counters["cross_agent_resource_leaks"] += 1
                    counters["unauthorized_effects"] += 1
                except ResourceAccessDenied:
                    counters["resource_denied"] += 1
                decision = world.registry.delegate_capability(
                    DelegateCapabilityRequest(
                        "agent-parent",
                        "agent-worker",
                        "resource.write",
                        ARTIFACT_RESOURCE_SCOPE,
                        delegation_id=f"deny-write-{step}",
                    ),
                    record=False,
                )
                if decision.allowed:
                    counters["authority_escalations"] += 1
                else:
                    counters["delegation_denies"] += 1

            if step == process_budget_step:
                counters["process_budget_blocks"] += _budget_block_once(
                    world.scheduler,
                    world.collector,
                    world.reader_process_id,
                    expected_scope="process",
                )

            if step == agent_budget_step:
                counters["agent_budget_blocks"] += _agent_budget_block_once(world)

            if step == host_budget_step:
                counters["host_budget_blocks"] += _host_budget_block_once(world)

            if step == child_fault_step:
                world.scheduler.record_process_fault(
                    world.worker_process_id,
                    "synthetic_child_fault",
                )
                counters["child_faults"] += 1

            if step == cancellation_step:
                cancelled = world.scheduler.request_cancel_subtree(
                    world.cancel_process_id,
                    reason="synthetic_cancel",
                )
                counters["cancellations"] += len(cancelled)

            if step == durable_crash_step:
                operation_id = f"op-payment-{step}"
                _append_payment_operation(
                    world.parent_session,
                    agent_id="agent-parent",
                    operation_id=operation_id,
                    call_id=f"call-payment-{step}",
                    turn=counters["durable_operations"] + 1,
                    dispatched=True,
                    committed=False,
                )
                counters["durable_operations"] += 1
                counters["dispatch_before_crash_count"] += 1
                if operation_id in external_effects:
                    counters["unsafe_duplicate_effects"] += 1
                external_effects.add(operation_id)
                counters["external_effect_count"] = len(external_effects)
                counters["crashes"] += 1
                world, restart_metrics = _replace_runtime_after_recovery(
                    world,
                    current_capability_grants={"agent-parent": (world.root_grant,)},
                )
                recovered_event_total += int(restart_metrics["recovered_events"])
                _merge_restart_metrics(counters, restart_metrics)
                obligation = next(
                    (
                        item
                        for item in restart_metrics["durable_obligations"]
                        if item.operation_id == operation_id
                    ),
                    None,
                )
                if (
                    obligation is not None
                    and obligation.classification
                    is OperationRecoveryClassification.RECONCILE_REQUIRED
                ):
                    counters["reconcile_required_observed"] += 1
                    _append_payment_reconciliation(
                        world.parent_session,
                        operation_id=operation_id,
                        call_id=f"call-payment-{step}",
                        turn=counters["durable_operations"],
                    )
                    counters["reconciliations"] += 1
                else:
                    counters["lost_durable_facts"] += 1

            if step == authority_shrink_step:
                counters["crashes"] += 1
                world, restart_metrics = _replace_runtime_after_recovery(
                    world,
                    current_capability_grants={"agent-parent": ()},
                )
                recovered_event_total += int(restart_metrics["recovered_events"])
                _merge_restart_metrics(counters, restart_metrics)
                shrink_allowed = _agent_evaluator(world, "agent-reader").authorize(
                    AuthorizationRequest(
                        "agent-reader",
                        RESOURCE_READ_ACTION,
                        "artifact://anything",
                    )
                ).allowed
                counters["authority_shrink_events"] += 1
                if shrink_allowed:
                    counters["stale_authority_restored"] += 1
                    counters["authority_escalations"] += 1
                else:
                    counters["delegation_denies"] += 1
                world, restart_metrics = _replace_runtime_after_recovery(
                    world,
                    current_capability_grants={"agent-parent": (world.root_grant,)},
                )
                recovered_event_total += int(restart_metrics["recovered_events"])
                _merge_restart_metrics(counters, restart_metrics)

            if step % crash_interval == 0 and step not in {
                redelivery_step,
                durable_crash_step,
                authority_shrink_step,
            }:
                counters["crashes"] += 1
                world, restart_metrics = _replace_runtime_after_recovery(
                    world,
                    current_capability_grants={"agent-parent": (world.root_grant,)},
                )
                recovered_event_total += int(restart_metrics["recovered_events"])
                _merge_restart_metrics(counters, restart_metrics)

        final_result = recover_multi_agent_runtime(
            world.sessions,
            current_capability_grants={"agent-parent": (world.root_grant,)},
            resource_store=world.store,
            ipc_persistence=world.ipc_persistence,
        )
        counters["recoveries"] += 1
        if final_result.durable_obligations:
            counters["unresolved_mandatory_wal"] += len(
                final_result.durable_obligations
            )
        if final_result.ipc is None or len(final_result.ipc.list_messages()) != horizon:
            counters["recovery_corruptions"] += 1
        if final_result.resource_shares is None:
            counters["recovery_corruptions"] += 1

        positive_coverage = (
            counters["crashes"] > 0
            and counters["recoveries"] > 0
            and counters["runtime_restarts"] > 0
            and counters["runtime_object_replacements_verified"]
            == counters["runtime_restarts"]
            and counters["ipc_sent"] > 0
            and counters["ipc_deliveries"] > 0
            and counters["ipc_acks"] > 0
            and counters["ipc_redeliveries"] > 0
            and counters["resource_allowed"] > 0
            and counters["resource_denied"] > 0
            and counters["delegation_allows"] > 0
            and counters["delegation_denies"] > 0
            and counters["process_budget_blocks"] > 0
            and counters["agent_budget_blocks"] > 0
            and counters["host_budget_blocks"] > 0
            and counters["child_faults"] > 0
            and counters["cancellations"] > 0
            and counters["durable_operations"] > 0
            and counters["dispatch_before_crash_count"] > 0
            and counters["reconcile_required_observed"] > 0
            and counters["reconciliations"] > 0
            and counters["external_effect_count"] == counters["durable_operations"]
            and counters["authority_shrink_events"] > 0
        )
        semantic_success = (
            counters["unauthorized_effects"] == 0
            and counters["unsafe_duplicate_effects"] == 0
            and counters["cross_agent_resource_leaks"] == 0
            and counters["authority_escalations"] == 0
            and counters["stale_authority_restored"] == 0
            and counters["lost_durable_facts"] == 0
            and counters["recovery_corruptions"] == 0
            and counters["unresolved_mandatory_wal"] == 0
            and counters["ipc_sent"] == horizon
            and counters["ipc_deliveries"]
            == horizon + counters["ipc_redeliveries"]
            and counters["ipc_acks"] == horizon
            and positive_coverage
        )

        metrics = {
            **counters,
            "logical_steps": horizon,
            "requested_horizon": horizon,
            "resource_bytes_read": resource_bytes_read,
            "recovered_events": recovered_event_total,
            "positive_coverage_passed": positive_coverage,
            "live_ipc_messages": len(final_result.ipc.list_messages())
            if final_result.ipc is not None
            else 0,
            "active_resource_shares": len(
                final_result.resource_shares.shares_for_resource(
                    world.handle_uri.removeprefix("artifact://")
                )
            )
            if final_result.resource_shares is not None
            else 0,
            "semantic_invariants_passed": semantic_success,
            "success": semantic_success,
        }
        return _record(
            "M10_long_horizon_multi_agent_composition",
            "composes_multi_agent_runtime_primitives",
            metrics,
        )


def _m1_identity_isolation_case() -> BenchmarkRecord:
    with TemporaryDirectory(prefix="agentkernel-runtimebench-v08-m1-") as root:
        world = _create_world(Path(root), prefix="m1")
        evaluator = CapabilityEvaluator((world.root_grant,))
        agent_decision = evaluator.authorize(
            AuthorizationRequest(
                "agent-parent",
                RESOURCE_READ_ACTION,
                "artifact://anything",
            )
        )
        process_decision = evaluator.authorize(
            AuthorizationRequest(
                world.parent_process_id,
                RESOURCE_READ_ACTION,
                "artifact://anything",
            )
        )
        parent_process = world.manager.get(world.parent_process_id)
        success = (
            agent_decision.allowed
            and not process_decision.allowed
            and parent_process.agent_id == "agent-parent"
            and parent_process.process_id != parent_process.agent_id
            and parent_process.session_id == "session-parent"
        )
        return _record(
            "M1_agent_process_identity_isolation",
            "agent_capability_principal_is_not_process_runtime_identity",
            {
                "agent_authorized": agent_decision.allowed,
                "process_authorized_as_agent": process_decision.allowed,
                "agent_id": parent_process.agent_id,
                "process_id": parent_process.process_id,
                "session_id": parent_process.session_id,
                "authority_escalations": 1 if process_decision.allowed else 0,
                "success": success,
            },
        )


def _m2_tree_separation_case() -> BenchmarkRecord:
    with TemporaryDirectory(prefix="agentkernel-runtimebench-v08-m2-") as root:
        world = _create_world(Path(root), prefix="m2")
        agent_children = world.registry.children_of("agent-parent")
        process_children = world.manager.children_of(world.parent_process_id)
        child_agents = tuple(world.manager.get(pid).agent_id for pid in process_children)
        before_events = len(world.parent_session.events)
        world.manager.get(world.parent_process_id).transition(ProcessState.RUNNING)
        world.manager.get(world.parent_process_id).transition(ProcessState.READY)
        after_events = len(world.parent_session.events)
        success = (
            agent_children == ("agent-reader", "agent-worker")
            and set(process_children)
            == {
                world.reader_process_id,
                world.worker_process_id,
                world.cancel_process_id,
            }
            and child_agents.count("agent-worker") == 2
            and before_events == after_events
        )
        return _record(
            "M2_agent_tree_process_tree_separation",
            "agent_lineage_and_process_supervision_are_distinct_indexes",
            {
                "agent_child_count": len(agent_children),
                "process_child_count": len(process_children),
                "worker_process_count": child_agents.count("agent-worker"),
                "process_state_mutated_session": after_events != before_events,
                "object_identity_confusion": 0 if success else 1,
                "success": success,
            },
        )


def _m3_capability_delegation_narrowing_case() -> BenchmarkRecord:
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    grandchild_session = Session("session-grandchild")
    root_grant = CapabilityGrant(
        "agent-parent",
        RESOURCE_READ_ACTION,
        "artifact://project-a/**",
        {"max_bytes": 1024},
    )
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(root_grant,),
        creation_id="create-agent-parent",
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-agent-child",
    )
    grandchild = registry.create_child(
        parent_agent_id=child.control.agent_id,
        agent_id="agent-grandchild",
        session=grandchild_session,
        creation_id="create-agent-grandchild",
    )
    del grandchild
    allowed = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/**",
            {"max_bytes": 512},
            delegation_id="delegate-logs",
        ),
        record_session=child_session,
    )
    grandchild_allowed = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/today/**",
            {"max_bytes": 256},
            delegation_id="delegate-today-logs",
        ),
        record_session=grandchild_session,
    )
    scope_denied = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://project-b/**",
            {"max_bytes": 512},
            delegation_id="delegate-project-b",
        ),
        record=False,
    )
    action_denied = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            "resource.write",
            "artifact://project-a/logs/**",
            {"max_bytes": 512},
            delegation_id="delegate-write",
        ),
        record=False,
    )
    constraint_denied = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/**",
            {"max_bytes": 2048},
            delegation_id="delegate-too-large",
        ),
        record=False,
    )
    grandchild_scope_denied = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://project-a/private/**",
            {"max_bytes": 256},
            delegation_id="delegate-grandchild-private",
        ),
        record=False,
    )
    grandchild_action_denied = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            "resource.write",
            "artifact://project-a/logs/today/**",
            {"max_bytes": 256},
            delegation_id="delegate-grandchild-write",
        ),
        record=False,
    )
    grandchild_constraint_denied = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/today/**",
            {"max_bytes": 2048},
            delegation_id="delegate-grandchild-too-large",
        ),
        record=False,
    )
    child_evaluator = CapabilityEvaluator(
        registry.get("agent-child").capability_grants
    )
    grandchild_evaluator = CapabilityEvaluator(
        registry.get("agent-grandchild").capability_grants
    )
    child_allowed = child_evaluator.authorize(
        AuthorizationRequest(
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/today.txt",
        )
    ).allowed
    child_secret_denied = not child_evaluator.authorize(
        AuthorizationRequest(
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://project-a/private.txt",
        )
    ).allowed
    grandchild_scope_allowed = grandchild_evaluator.authorize(
        AuthorizationRequest(
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/today/summary.txt",
        )
    ).allowed
    grandchild_parent_bound = not grandchild_evaluator.authorize(
        AuthorizationRequest(
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://project-a/logs/yesterday/summary.txt",
        )
    ).allowed
    success = (
        allowed.allowed
        and grandchild_allowed.allowed
        and not scope_denied.allowed
        and not action_denied.allowed
        and not constraint_denied.allowed
        and not grandchild_scope_denied.allowed
        and not grandchild_action_denied.allowed
        and not grandchild_constraint_denied.allowed
        and child_allowed
        and child_secret_denied
        and grandchild_scope_allowed
        and grandchild_parent_bound
    )
    return _record(
        "M3_capability_delegation_narrowing",
        "child_grant_must_be_narrower_than_parent_authority",
        {
            "delegation_allows": sum(
                1 for decision in (allowed, grandchild_allowed) if decision.allowed
            ),
            "delegation_denies": sum(
                1
                for decision in (
                    scope_denied,
                    action_denied,
                    constraint_denied,
                    grandchild_scope_denied,
                    grandchild_action_denied,
                    grandchild_constraint_denied,
                )
                if not decision.allowed
            ),
            "multi_hop_delegation_depth": 2,
            "grandchild_scope_allowed": grandchild_scope_allowed,
            "grandchild_parent_bound": grandchild_parent_bound,
            "child_scope_allowed": child_allowed,
            "child_secret_denied": child_secret_denied,
            "authority_escalations": 0 if success else 1,
            "success": success,
        },
    )


def _m4_ipc_authority_isolation_case() -> BenchmarkRecord:
    with TemporaryDirectory(prefix="agentkernel-runtimebench-v08-m4-") as root:
        world = _create_world(Path(root), prefix="m4")
        message = world.ipc.send(
            channel_id=world.channel_id,
            sender_process_id=world.parent_process_id,
            payload={
                "grant": {
                    "subject": "agent-reader",
                    "action": RESOURCE_READ_ACTION,
                    "resource_scope": ARTIFACT_RESOURCE_SCOPE,
                }
            },
            message_id="message-grant-like",
            correlation_id="corr-grant-like",
        )
        delivered = world.ipc.receive(
            channel_id=world.channel_id,
            receiver_agent_id="agent-reader",
            receiver_process_id=world.reader_process_id,
        )
        acked = None
        if delivered is not None:
            acked = world.ipc.ack(
                channel_id=world.channel_id,
                message_id=delivered.message_id,
                receiver_agent_id="agent-reader",
                receiver_process_id=world.reader_process_id,
            )
        reader = world.registry.get("agent-reader")
        payload_authorized = CapabilityEvaluator(reader.capability_grants).authorize(
            AuthorizationRequest(
                "agent-reader",
                "resource.write",
                "artifact://anything",
            )
        ).allowed
        success = (
            message.message_id == "message-grant-like"
            and delivered is not None
            and acked is not None
            and acked.delivery_state is IPCMessageState.ACKED
            and not payload_authorized
        )
        return _record(
            "M4_ipc_delivery_authority_isolation",
            "ipc_payload_delivery_does_not_install_authority",
            {
                "ipc_sent": 1,
                "ipc_deliveries": 1 if delivered is not None else 0,
                "ipc_acks": 1 if acked is not None else 0,
                "payload_grant_inert": not payload_authorized,
                "authority_escalations": 1 if payload_authorized else 0,
                "success": success,
            },
        )


def _m5_resource_sharing_isolation_case() -> BenchmarkRecord:
    with TemporaryDirectory(prefix="agentkernel-runtimebench-v08-m5-") as root:
        world = _create_world(Path(root), prefix="m5")
        reader_owner = ResourceOwner("agent-reader", "session-reader")
        worker_owner = ResourceOwner("agent-worker", "session-worker")
        reader_evaluator = CapabilityEvaluator(
            world.registry.get("agent-reader").capability_grants
        )
        worker_evaluator = CapabilityEvaluator(
            world.registry.get("agent-worker").capability_grants
        )
        denied_without_capability = False
        denied_without_share = False
        allowed_read = False
        try:
            world.resources.read(
                world.handle_uri,
                owner=reader_owner,
                capability_evaluator=CapabilityEvaluator(()),
            )
        except ResourceAccessDenied:
            denied_without_capability = True
        isolated = ResourceService(world.store)
        try:
            isolated.read(
                world.handle_uri,
                owner=reader_owner,
                capability_evaluator=reader_evaluator,
            )
        except ResourceAccessDenied:
            denied_without_share = True
        try:
            allowed_read = (
                world.resources.read(
                    world.handle_uri,
                    owner=reader_owner,
                    capability_evaluator=reader_evaluator,
                ).data
                == b"secret"
            )
        except ResourceAccessDenied:
            allowed_read = False
        try:
            world.resources.read(
                world.handle_uri,
                owner=worker_owner,
                capability_evaluator=worker_evaluator,
            )
            worker_leak = True
        except ResourceAccessDenied:
            worker_leak = False
        success = (
            denied_without_capability
            and denied_without_share
            and allowed_read
            and not worker_leak
        )
        return _record(
            "M5_resource_sharing_isolation",
            "resource_access_requires_share_and_capability",
            {
                "resource_allowed": 1 if allowed_read else 0,
                "resource_denied": sum(
                    int(value)
                    for value in (
                        denied_without_capability,
                        denied_without_share,
                        not worker_leak,
                    )
                ),
                "denied_without_capability": denied_without_capability,
                "denied_without_share": denied_without_share,
                "worker_leak": worker_leak,
                "cross_agent_resource_leaks": 1 if worker_leak else 0,
                "success": success,
            },
        )


def _m6_hierarchical_budget_isolation_case() -> BenchmarkRecord:
    process_block = _budget_scope_case(
        process_id="process-budget-process",
        agent_id="agent-budget-process",
        process_budget=AgentBudget(max_total_tool_calls=1),
        observed_tool_calls=2,
        expected_scope="process",
    )
    agent_block = _agent_budget_scope_case()
    host_block = _host_budget_scope_case()
    success = process_block and agent_block and host_block
    return _record(
        "M6_hierarchical_budget_isolation",
        "process_agent_and_host_budget_scopes_block_independently",
        {
            "process_budget_blocks": 1 if process_block else 0,
            "agent_budget_blocks": 1 if agent_block else 0,
            "host_budget_blocks": 1 if host_block else 0,
            "budget_scope_failures": 0 if success else 1,
            "success": success,
        },
    )


def _m7_fault_cancellation_isolation_case() -> BenchmarkRecord:
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        creation_id="create-agent-parent",
    )
    child = registry.create_child(
        parent_agent_id="agent-parent",
        agent_id="agent-child",
        session=child_session,
        creation_id="create-agent-child",
    )
    manager = ProcessManager(agent_registry=registry)
    scheduler = CooperativeScheduler(manager)
    parent_process = scheduler.create_process(
        process_id="process-parent",
        agent=parent.control,
    )
    faulted = scheduler.create_process(
        process_id="process-child-faulted",
        agent=child.control,
        parent_process_id=parent_process.process_id,
    )
    cancelled = scheduler.create_process(
        process_id="process-child-cancelled",
        agent=child.control,
        parent_process_id=parent_process.process_id,
    )
    scheduler.record_process_fault(faulted.process_id, "tool_failure")
    scheduler.dispatch(cancelled.process_id)
    try:
        scheduler.request_cancel(cancelled.process_id, reason="host_cancel")
        scheduler.safe_point(cancelled.process_id, SchedulerSafePoint.BEFORE_TOOL_CALL)
        cancelled_at_safe_point = False
    except ProcessCancelled:
        cancelled_at_safe_point = True
    parent_alive = parent_process.state is not ProcessState.EXITED
    child_faults = scheduler.child_faults_of(parent_process.process_id)
    success = (
        parent_alive
        and len(child_faults) == 1
        and faulted.state is ProcessState.EXITED
        and cancelled.state is ProcessState.EXITED
        and cancelled_at_safe_point
    )
    return _record(
        "M7_fault_cancellation_isolation",
        "child_faults_and_cancellations_do_not_erase_supervisor",
        {
            "child_faults": len(child_faults),
            "cancellations": 1 if cancelled_at_safe_point else 0,
            "parent_alive": parent_alive,
            "faulted_child_state": faulted.state.value,
            "cancelled_child_state": cancelled.state.value,
            "success": success,
        },
    )


def _m8_integrated_recovery_case() -> BenchmarkRecord:
    with TemporaryDirectory(prefix="agentkernel-runtimebench-v08-m8-") as root:
        world = _create_world(Path(root), prefix="m8")
        world.ipc.send(
            channel_id=world.channel_id,
            sender_process_id=world.parent_process_id,
            payload={"kind": "acked"},
            message_id="m8-acked",
            correlation_id="corr-m8-acked",
        )
        received_for_ack = world.ipc.receive(
            channel_id=world.channel_id,
            receiver_agent_id="agent-reader",
            receiver_process_id=world.reader_process_id,
        )
        if received_for_ack is not None:
            world.ipc.ack(
                channel_id=world.channel_id,
                message_id=received_for_ack.message_id,
                receiver_agent_id="agent-reader",
                receiver_process_id=world.reader_process_id,
            )
        world.ipc.send(
            channel_id=world.channel_id,
            sender_process_id=world.parent_process_id,
            payload={"kind": "delivered-unacked"},
            resource_refs=("res_forged",),
            message_id="m8-delivered",
            correlation_id="corr-m8-delivered",
        )
        delivered_before_recovery = world.ipc.receive(
            channel_id=world.channel_id,
            receiver_agent_id="agent-reader",
            receiver_process_id=world.reader_process_id,
        )
        world.ipc.send(
            channel_id=world.channel_id,
            sender_process_id=world.parent_process_id,
            payload={
                "kind": "pending",
                "grant": {
                    "subject": "agent-reader",
                    "action": "resource.write",
                    "resource_scope": ARTIFACT_RESOURCE_SCOPE,
                },
            },
            message_id="m8-pending",
            correlation_id="corr-m8-pending",
        )
        _append_payment_operation(
            world.parent_session,
            agent_id="agent-parent",
            operation_id="op-payment-crashed",
            call_id="call-payment-crashed",
            dispatched=True,
            committed=False,
        )
        before_counts = _event_counts(world.sessions)
        result = recover_multi_agent_runtime(
            world.sessions,
            current_capability_grants={"agent-parent": (world.root_grant,)},
            resource_store=world.store,
            ipc_persistence=world.ipc_persistence,
        )
        after_counts = _event_counts(world.sessions)
        obligation = result.durable_obligations[0] if result.durable_obligations else None
        recovered_messages = (
            {message.message_id: message for message in result.ipc.list_messages()}
            if result.ipc is not None
            else {}
        )
        redelivered = None
        acked_after_recovery = recovered_messages.get("m8-acked")
        pending_after_recovery = None
        if result.ipc is not None:
            redelivered = result.ipc.receive(
                channel_id=world.channel_id,
                receiver_agent_id="agent-reader",
                receiver_process_id=world.reader_process_id,
            )
            if redelivered is not None:
                result.ipc.ack(
                    channel_id=world.channel_id,
                    message_id=redelivered.message_id,
                    receiver_agent_id="agent-reader",
                    receiver_process_id=world.reader_process_id,
                )
            pending_after_recovery = result.ipc.receive(
                channel_id=world.channel_id,
                receiver_agent_id="agent-reader",
                receiver_process_id=world.reader_process_id,
            )
        pending_delivered_after_recovery = (
            pending_after_recovery is not None
            and pending_after_recovery.message_id == "m8-pending"
            and pending_after_recovery.delivery_attempts == 1
        )
        unacked_redelivery = (
            delivered_before_recovery is not None
            and redelivered is not None
            and redelivered.message_id == delivered_before_recovery.message_id
            and redelivered.delivery_attempts
            == delivered_before_recovery.delivery_attempts + 1
        )
        acked_not_redelivered = (
            acked_after_recovery is not None
            and acked_after_recovery.delivery_state is IPCMessageState.ACKED
            and redelivered is not None
            and redelivered.message_id != acked_after_recovery.message_id
        )
        payload_grant_inert = not CapabilityEvaluator(
            result.agent_registry.get("agent-reader").capability_grants
        ).authorize(
            AuthorizationRequest(
                "agent-reader",
                "resource.write",
                "artifact://anything",
            )
        ).allowed
        forged_resource_refs_inert = (
            result.resource_shares is not None
            and len(result.resource_shares.shares_for_resource("res_forged"))
            == 0
            and len(result.agent_registry.get("agent-reader").capability_grants) == 1
        )
        states = {process.state for process in result.process_manager.list_processes()}
        success = (
            before_counts == after_counts
            and result.agent_registry.contains("agent-parent")
            and result.agent_registry.contains("agent-reader")
            and result.resource_shares is not None
            and result.ipc is not None
            and len(recovered_messages) == 3
            and pending_delivered_after_recovery
            and unacked_redelivery
            and acked_not_redelivered
            and payload_grant_inert
            and forged_resource_refs_inert
            and obligation is not None
            and obligation.classification
            is OperationRecoveryClassification.RECONCILE_REQUIRED
            and obligation.authorization is not None
            and states == {ProcessState.CREATED}
            and any(
                disposition.disposition
                is ProcessRecoveryDisposition.NEEDS_RECONCILIATION
                for disposition in result.process_dispositions
            )
        )
        return _record(
            "M8_integrated_multi_agent_recovery",
            "recovery_reconstructs_indexes_and_surfaces_durable_obligations",
            {
                "reconstructed_agents": len(result.agent_registry.list_agents()),
                "reconstructed_processes": len(result.process_manager.list_processes()),
                "resource_shares_recovered": 1 if result.resource_shares else 0,
                "ipc_messages_recovered": len(result.ipc.list_messages())
                if result.ipc is not None
                else 0,
                "pending_delivered_after_recovery": 1
                if pending_delivered_after_recovery
                else 0,
                "unacked_redelivery": 1 if unacked_redelivery else 0,
                "acked_not_redelivered": 1 if acked_not_redelivered else 0,
                "payload_grant_inert": 1 if payload_grant_inert else 0,
                "resource_refs_inert": 1 if forged_resource_refs_inert else 0,
                "durable_obligations": len(result.durable_obligations),
                "mandatory_wal_obligations_surfaced": len(
                    result.durable_obligations
                ),
                "lost_mandatory_wal_obligations": 0
                if result.durable_obligations
                else 1,
                "reconcile_required": 1
                if obligation is not None
                and obligation.classification
                is OperationRecoveryClassification.RECONCILE_REQUIRED
                else 0,
                "lost_durable_facts": 0 if before_counts == after_counts else 1,
                "recovery_corruptions": 0 if success else 1,
                "unresolved_mandatory_wal": 0,
                "success": success,
            },
        )


def _m9_authority_shrink_after_restart_case() -> BenchmarkRecord:
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    grandchild_session = Session("session-grandchild")
    root_grant = CapabilityGrant(
        "agent-parent",
        RESOURCE_READ_ACTION,
        ARTIFACT_RESOURCE_SCOPE,
    )
    registry = AgentRegistry()
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(root_grant,),
        creation_id="create-agent-parent",
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-agent-child",
    )
    grandchild = registry.create_child(
        parent_agent_id=child.control.agent_id,
        agent_id="agent-grandchild",
        session=grandchild_session,
        creation_id="create-agent-grandchild",
    )
    manager = ProcessManager(agent_registry=registry)
    parent_process = manager.create_process(
        process_id="process-parent",
        agent=parent.control,
        record_session=parent_session,
        creation_id="create-process-parent",
    )
    manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-child",
        agent=child.control,
        record_session=child_session,
        creation_id="create-process-child",
    )
    manager.create_child_process(
        parent_process_id="process-child",
        process_id="process-grandchild",
        agent=grandchild.control,
        record_session=grandchild_session,
        creation_id="create-process-grandchild",
    )
    decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-child",
            RESOURCE_READ_ACTION,
            ARTIFACT_RESOURCE_SCOPE,
            delegation_id="delegate-read-all",
        ),
        record_session=child_session,
    )
    grandchild_decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://logs/**",
            delegation_id="delegate-grandchild-logs",
        ),
        record_session=grandchild_session,
    )
    broad_recovery = recover_multi_agent_runtime(
        (parent_session, child_session, grandchild_session),
        current_capability_grants={"agent-parent": (root_grant,)},
    )
    shrink_recovery = recover_multi_agent_runtime(
        (parent_session, child_session, grandchild_session),
        current_capability_grants={"agent-parent": ()},
    )
    broad_child_grants = broad_recovery.agent_registry.get(
        "agent-child"
    ).capability_grants
    broad_grandchild_grants = broad_recovery.agent_registry.get(
        "agent-grandchild"
    ).capability_grants
    shrink_child_grants = shrink_recovery.agent_registry.get(
        "agent-child"
    ).capability_grants
    shrink_grandchild_grants = shrink_recovery.agent_registry.get(
        "agent-grandchild"
    ).capability_grants
    shrink_allowed = CapabilityEvaluator(shrink_child_grants).authorize(
        AuthorizationRequest(
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://anything",
        )
    ).allowed
    shrink_grandchild_allowed = CapabilityEvaluator(shrink_grandchild_grants).authorize(
        AuthorizationRequest(
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            "artifact://logs/today.txt",
        )
    ).allowed
    historical_facts = sum(
        1
        for session in (child_session, grandchild_session)
        for event in session.events
        if event.type is EventType.CAPABILITY_DELEGATED
    )
    success = (
        decision.allowed
        and grandchild_decision.allowed
        and len(broad_child_grants) == 1
        and len(broad_grandchild_grants) == 1
        and len(shrink_child_grants) == 0
        and len(shrink_grandchild_grants) == 0
        and not shrink_allowed
        and not shrink_grandchild_allowed
        and historical_facts == 2
    )
    return _record(
        "M9_authority_shrink_after_restart",
        "current_parent_authority_bounds_replayed_delegations",
        {
            "historical_delegation_facts": historical_facts,
            "broad_restart_child_grants": len(broad_child_grants),
            "broad_restart_grandchild_grants": len(broad_grandchild_grants),
            "shrink_restart_child_grants": len(shrink_child_grants),
            "shrink_restart_grandchild_grants": len(shrink_grandchild_grants),
            "shrink_restart_authorized": shrink_allowed,
            "shrink_restart_grandchild_authorized": shrink_grandchild_allowed,
            "multi_hop_authority_shrink": not shrink_grandchild_allowed,
            "authority_escalations": 1
            if shrink_allowed or shrink_grandchild_allowed
            else 0,
            "stale_authority_restored": 1
            if shrink_allowed or shrink_grandchild_allowed
            else 0,
            "lost_durable_facts": 0 if historical_facts == 2 else 1,
            "success": success,
        },
    )


def _m10_long_horizon_composition_case(
    horizons: tuple[int, ...],
) -> BenchmarkRecord:
    records = [run_long_horizon_multi_agent(horizon) for horizon in horizons]
    metrics_by_horizon = {record.metrics["requested_horizon"]: record for record in records}
    invariant_keys = (
        "unauthorized_effects",
        "unsafe_duplicate_effects",
        "cross_agent_resource_leaks",
        "authority_escalations",
        "stale_authority_restored",
        "lost_durable_facts",
        "recovery_corruptions",
        "unresolved_mandatory_wal",
    )
    invariants = {
        key: sum(int(record.metrics[key]) for record in records)
        for key in invariant_keys
    }
    success = (
        all(bool(record.metrics["success"]) for record in records)
        and all(value == 0 for value in invariants.values())
        and tuple(sorted(metrics_by_horizon)) == horizons
    )
    return _record(
        "M10_long_horizon_multi_agent_composition",
        "multi_agent_runtime_invariants_hold_across_requested_horizons",
        {
            "horizon_count": len(records),
            "horizons": list(horizons),
            "horizon_100_pass": bool(
                metrics_by_horizon.get(100, _empty_record()).metrics["success"]
            ),
            "horizon_500_pass": bool(
                metrics_by_horizon.get(500, _empty_record()).metrics["success"]
            ),
            "horizon_1000_pass": bool(
                metrics_by_horizon.get(1000, _empty_record()).metrics["success"]
            ),
            "logical_steps": sum(int(record.metrics["logical_steps"]) for record in records),
            "requested_logical_steps": sum(horizons),
            "crashes": sum(int(record.metrics["crashes"]) for record in records),
            "recoveries": sum(int(record.metrics["recoveries"]) for record in records),
            "runtime_restarts": sum(
                int(record.metrics["runtime_restarts"]) for record in records
            ),
            "runtime_object_replacements_verified": sum(
                int(record.metrics["runtime_object_replacements_verified"])
                for record in records
            ),
            "ipc_sent": sum(int(record.metrics["ipc_sent"]) for record in records),
            "ipc_deliveries": sum(
                int(record.metrics["ipc_deliveries"]) for record in records
            ),
            "ipc_redeliveries": sum(
                int(record.metrics["ipc_redeliveries"]) for record in records
            ),
            "ipc_acks": sum(int(record.metrics["ipc_acks"]) for record in records),
            "resource_allowed": sum(
                int(record.metrics["resource_allowed"]) for record in records
            ),
            "resource_denied": sum(
                int(record.metrics["resource_denied"]) for record in records
            ),
            "delegation_allows": sum(
                int(record.metrics["delegation_allows"]) for record in records
            ),
            "delegation_denies": sum(
                int(record.metrics["delegation_denies"]) for record in records
            ),
            "process_budget_blocks": sum(
                int(record.metrics["process_budget_blocks"]) for record in records
            ),
            "agent_budget_blocks": sum(
                int(record.metrics["agent_budget_blocks"]) for record in records
            ),
            "host_budget_blocks": sum(
                int(record.metrics["host_budget_blocks"]) for record in records
            ),
            "child_faults": sum(int(record.metrics["child_faults"]) for record in records),
            "cancellations": sum(
                int(record.metrics["cancellations"]) for record in records
            ),
            "durable_operations": sum(
                int(record.metrics["durable_operations"]) for record in records
            ),
            "dispatch_before_crash_count": sum(
                int(record.metrics["dispatch_before_crash_count"])
                for record in records
            ),
            "reconcile_required_observed": sum(
                int(record.metrics["reconcile_required_observed"])
                for record in records
            ),
            "reconciliations": sum(
                int(record.metrics["reconciliations"]) for record in records
            ),
            "external_effect_count": sum(
                int(record.metrics["external_effect_count"]) for record in records
            ),
            "authority_shrink_events": sum(
                int(record.metrics["authority_shrink_events"]) for record in records
            ),
            **invariants,
            "semantic_invariants_passed": success,
            "success": success,
        },
    )


def _replace_runtime_after_recovery(
    world: _World,
    *,
    current_capability_grants: dict[str, tuple[CapabilityGrant, ...]],
    host_budget: HostBudget | None = None,
) -> tuple[_World, dict[str, object]]:
    """Recover fresh runtime objects and return a replacement live world.

    Durable truth remains in Session, IPCPersistence, ResourceStore, and WAL
    facts. Runtime-only objects are intentionally replaced.
    """

    pre_registry = world.registry
    pre_manager = world.manager
    pre_scheduler = world.scheduler
    pre_ipc = world.ipc
    pre_agent_ids = tuple(agent.agent_id for agent in pre_registry.list_agents())
    pre_process_ids = tuple(
        process.process_id for process in pre_manager.list_processes()
    )
    pre_event_counts = _event_counts(world.sessions)
    pre_message_count = len(pre_ipc.list_messages())
    resource_id = _resource_id_from_uri(world.handle_uri)
    pre_metadata = world.store.stat(resource_id).as_dict()

    restart_budget = host_budget or HostBudget(max_total_tool_calls=100_000)
    result = recover_multi_agent_runtime(
        world.sessions,
        current_capability_grants=current_capability_grants,
        resource_store=world.store,
        ipc_persistence=world.ipc_persistence,
        host_budget=restart_budget,
    )
    fresh_collector = UsageCollector(clock=_FakeClock())
    # The recovery coordinator owns the fresh scheduler. The benchmark Host
    # attaches fresh runtime-only accounting before continuing the workload.
    result.scheduler._usage_collector = fresh_collector  # type: ignore[attr-defined]

    resource_shares = result.resource_shares or world.shares
    ipc = result.ipc or world.ipc
    resources = ResourceService(
        world.store,
        share_registry=resource_shares,
        resource_id_factory=_IdFactory("res_restart"),
        handle_id_factory=_IdFactory("hdl_restart"),
        clock=lambda: 10.0,
    )
    replacement = replace(
        world,
        registry=result.agent_registry,
        manager=result.process_manager,
        scheduler=result.scheduler,
        collector=fresh_collector,
        shares=resource_shares,
        resources=resources,
        ipc=ipc,
    )

    post_agent_ids = tuple(
        agent.agent_id for agent in result.agent_registry.list_agents()
    )
    post_process_ids = tuple(
        process.process_id for process in result.process_manager.list_processes()
    )
    post_event_counts = _event_counts(world.sessions)
    post_metadata = world.store.stat(resource_id).as_dict()
    object_replaced = (
        pre_registry is not result.agent_registry
        and pre_manager is not result.process_manager
        and pre_scheduler is not result.scheduler
        and pre_ipc is not ipc
    )
    share_preserved = (
        result.resource_shares is not None
        and result.resource_shares.is_shared_with(
            resource_id=resource_id,
            grantee_agent_id="agent-reader",
            action=RESOURCE_READ_ACTION,
        )
    )
    ipc_preserved = result.ipc is not None and len(result.ipc.list_messages()) == (
        pre_message_count
    )

    return replacement, {
        "recoveries": 1,
        "runtime_restarts": 1,
        "runtime_object_replacements_verified": 1 if object_replaced else 0,
        "agent_ids_preserved": 1 if pre_agent_ids == post_agent_ids else 0,
        "process_ids_preserved": 1 if pre_process_ids == post_process_ids else 0,
        "session_durable_events_preserved": 1
        if pre_event_counts == post_event_counts
        else 0,
        "resource_metadata_preserved": 1 if pre_metadata == post_metadata else 0,
        "resource_share_preserved": 1 if share_preserved else 0,
        "ipc_durable_envelopes_preserved": 1 if ipc_preserved else 0,
        "wal_obligations_preserved": len(result.durable_obligations),
        "recovered_events": sum(len(session.events) for session in world.sessions),
        "durable_obligations": result.durable_obligations,
        "lost_durable_facts": 0 if pre_event_counts == post_event_counts else 1,
        "recovery_corruptions": 0
        if object_replaced and share_preserved and ipc_preserved
        else 1,
    }


def _merge_restart_metrics(
    counters: dict[str, int],
    restart_metrics: dict[str, object],
) -> None:
    for key in (
        "recoveries",
        "runtime_restarts",
        "runtime_object_replacements_verified",
        "agent_ids_preserved",
        "process_ids_preserved",
        "session_durable_events_preserved",
        "resource_metadata_preserved",
        "resource_share_preserved",
        "ipc_durable_envelopes_preserved",
        "wal_obligations_preserved",
        "lost_durable_facts",
        "recovery_corruptions",
    ):
        counters[key] += int(restart_metrics[key])


def _agent_evaluator(world: _World, agent_id: str) -> CapabilityEvaluator:
    return CapabilityEvaluator(world.registry.get(agent_id).capability_grants)


def _resource_id_from_uri(uri: str) -> str:
    return uri.removeprefix("artifact://")


def _create_world(root: Path, *, prefix: str) -> _World:
    root_grant = CapabilityGrant(
        "agent-parent",
        RESOURCE_READ_ACTION,
        ARTIFACT_RESOURCE_SCOPE,
    )
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    reader_session = Session("session-reader")
    worker_session = Session("session-worker")
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(root_grant,),
        creation_id="create-agent-parent",
    )
    reader = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-reader",
        session=reader_session,
        creation_id="create-agent-reader",
    )
    worker = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-worker",
        session=worker_session,
        creation_id="create-agent-worker",
    )
    manager = ProcessManager(agent_registry=registry)
    parent_process = manager.create_process(
        process_id="process-parent",
        agent=parent.control,
        record_session=parent_session,
        creation_id="create-process-parent",
    )
    reader_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-reader",
        agent=reader.control,
        budget=AgentBudget(max_total_tool_calls=3),
        record_session=reader_session,
        creation_id="create-process-reader",
    )
    worker_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-worker",
        agent=worker.control,
        record_session=worker_session,
        creation_id="create-process-worker",
    )
    cancel_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-cancel",
        agent=worker.control,
        record_session=worker_session,
        creation_id="create-process-cancel",
    )

    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(
        manager,
        usage_collector=collector,
        host_budget=HostBudget(max_total_tool_calls=100_000),
    )
    store = LocalResourceStore(root / "resources")
    shares = ResourceShareRegistry(
        agent_registry=registry,
        clock=lambda: 100.0,
        share_id_factory=lambda: f"share{prefix}",
    )
    resources = ResourceService(
        store,
        share_registry=shares,
        resource_id_factory=_IdFactory(f"res_{prefix}"),
        handle_id_factory=_IdFactory(f"hdl_{prefix}"),
        clock=lambda: 10.0,
    )
    owner = ResourceOwner("agent-parent", "session-parent")
    handle = resources.create_artifact(
        b"secret",
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="runtimebench.fixture",
        source_tool_call_id="call-resource",
        source_operation_id="op-resource",
    )
    share = resources.share(
        handle.uri,
        owner=owner,
        grantee_agent_id="agent-reader",
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=parent_session,
        share_id=f"share_{prefix}",
        correlation_id=f"corr-share-{prefix}",
    )
    if not share.allowed:
        raise RuntimeError(f"resource share fixture denied: {share.reason}")
    delegation = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-parent",
            "agent-reader",
            RESOURCE_READ_ACTION,
            ARTIFACT_RESOURCE_SCOPE,
            delegation_id=f"delegate-read-{prefix}",
        ),
        record_session=reader_session,
    )
    if not delegation.allowed:
        raise RuntimeError(f"capability delegation fixture denied: {delegation.reason}")

    ipc_persistence = InMemoryIPCPersistence()
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        persistence=ipc_persistence,
        sessions={
            "agent-parent": parent_session,
            "agent-reader": reader_session,
            "agent-worker": worker_session,
        },
        time_fn=lambda: 1.0,
    )
    channel_id = "channel-parent-reader"
    ipc.create_channel(
        channel_id=channel_id,
        sender_agent_id="agent-parent",
        receiver_agent_id="agent-reader",
        receiver_process_id=reader_process.process_id,
    )
    return _World(
        registry=registry,
        manager=manager,
        scheduler=scheduler,
        collector=collector,
        store=store,
        shares=shares,
        resources=resources,
        ipc_persistence=ipc_persistence,
        ipc=ipc,
        root_grant=root_grant,
        parent_session=parent_session,
        reader_session=reader_session,
        worker_session=worker_session,
        parent_process_id=parent_process.process_id,
        reader_process_id=reader_process.process_id,
        worker_process_id=worker_process.process_id,
        cancel_process_id=cancel_process.process_id,
        channel_id=channel_id,
        handle_uri=handle.uri,
    )


def _append_payment_operation(
    session: Session,
    *,
    agent_id: str,
    operation_id: str,
    call_id: str,
    turn: int = 1,
    dispatched: bool,
    committed: bool,
    reconcile_status: ReconcileStatus | None = None,
) -> None:
    call = ToolCall(
        call_id,
        "payment.charge",
        {"invoice_id": f"invoice-{turn}", "amount_cents": 4200},
    )
    authorization = _authorization_context(agent_id)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": turn, "content": f"Charge invoice-{turn}."},
    )
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": turn, "step": 1, **call.as_dict()})
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            "turn": turn,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": operation_id,
            "boundary": "prepare",
            **authorization,
        },
    )
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": turn,
            "step": 1,
            "operation_id": operation_id,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            "authorization": authorization,
        },
    )
    if dispatched:
        session.append(
            EventType.AUTHORIZATION_GRANTED,
            {
                "turn": turn,
                "step": 1,
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "operation_id": operation_id,
                "boundary": "dispatch",
                **authorization,
            },
        )
        session.append(
            EventType.TOOL_DISPATCH,
            {
                "turn": turn,
                "step": 1,
                "operation_id": operation_id,
                "attempt": 1,
                "authorization": authorization,
            },
        )
    if reconcile_status is not None:
        output = {"ok": True, "operation_id": operation_id}
        session.append(
            EventType.TOOL_RECONCILE,
            {
                "turn": turn,
                "step": 1,
                "operation_id": operation_id,
                "observed_status": reconcile_status.value,
                **(
                    {"output": output}
                    if reconcile_status is ReconcileStatus.SUCCEEDED
                    else {}
                ),
            },
        )
    if committed:
        output = {"ok": True, "operation_id": operation_id}
        session.append(
            EventType.TOOL_COMMIT,
            {
                "turn": turn,
                "step": 1,
                "operation_id": operation_id,
                "output": output,
            },
        )
        session.append(
            EventType.TOOL_RESULT,
            {"turn": turn, "step": 1, **ToolResult.success(call, output).as_dict()},
        )
        session.append(
            EventType.STEP_END,
            {"turn": turn, "step": 1, "outcome": "tool_result"},
        )
        session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _append_payment_reconciliation(
    session: Session,
    *,
    operation_id: str,
    call_id: str,
    turn: int,
) -> None:
    output = {"ok": True, "operation_id": operation_id}
    session.append(
        EventType.TOOL_RECONCILE,
        {
            "turn": turn,
            "step": 1,
            "operation_id": operation_id,
            "observed_status": ReconcileStatus.SUCCEEDED.value,
            "output": output,
        },
    )
    session.append(
        EventType.TOOL_COMMIT,
        {
            "turn": turn,
            "step": 1,
            "operation_id": operation_id,
            "output": output,
        },
    )
    session.append(
        EventType.TOOL_RESULT,
        {
            "turn": turn,
            "step": 1,
            **ToolResult.success(
                ToolCall(
                    call_id,
                    "payment.charge",
                    {"invoice_id": f"invoice-{turn}", "amount_cents": 4200},
                ),
                output,
            ).as_dict(),
        },
    )
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "tool_result"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _authorization_context(agent_id: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "action": "payment.charge",
        "resource_scope": "payment://charges/**",
        "reason": "allowed",
        "matched_grant": {
            "subject": agent_id,
            "action": "payment.charge",
            "resource_scope": "payment://charges/**",
        },
    }


def _budget_scope_case(
    *,
    process_id: str,
    agent_id: str,
    process_budget: AgentBudget,
    observed_tool_calls: int,
    expected_scope: str,
) -> bool:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(usage_collector=collector)
    session = Session(f"session-{agent_id}")
    registry = AgentRegistry()
    agent = registry.create_root(agent_id=agent_id, session=session, record=False)
    process = scheduler.create_process(
        process_id=process_id,
        agent=agent.control,
        budget=process_budget,
    )
    scheduler.dispatch(process.process_id)
    collector.record_tool_call(process.process_id, observed_tool_calls)
    try:
        scheduler.safe_point(process.process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
    except ProcessBudgetExceeded as error:
        return (
            error.exceeded.scope == expected_scope
            and process.state is ProcessState.BLOCKED
        )
    return False


def _agent_budget_scope_case() -> bool:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(usage_collector=collector)
    session = Session("session-agent-budget")
    registry = AgentRegistry()
    agent = registry.create_root(
        agent_id="agent-budget",
        session=session,
        budget=AgentBudget(max_total_tool_calls=2),
        record=False,
    )
    first = scheduler.create_process(
        process_id="process-agent-budget-a",
        agent=agent.control,
    )
    second = scheduler.create_process(
        process_id="process-agent-budget-b",
        agent=agent.control,
    )
    scheduler.dispatch(first.process_id)
    collector.record_tool_call(first.process_id, 1)
    scheduler.yield_process(first.process_id)
    scheduler.dispatch(second.process_id)
    collector.record_tool_call(second.process_id, 2)
    try:
        scheduler.safe_point(second.process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
    except ProcessBudgetExceeded as error:
        return (
            error.exceeded.scope == "agent"
            and error.exceeded.subject == "agent-budget"
            and second.state is ProcessState.BLOCKED
        )
    return False


def _host_budget_scope_case() -> bool:
    collector = UsageCollector(clock=_FakeClock())
    scheduler = CooperativeScheduler(
        usage_collector=collector,
        host_budget=HostBudget(max_total_tool_calls=2),
    )
    registry = AgentRegistry()
    agent_a = registry.create_root(
        agent_id="agent-host-a",
        session=Session("session-host-a"),
        record=False,
    )
    agent_b = registry.create_root(
        agent_id="agent-host-b",
        session=Session("session-host-b"),
        record=False,
    )
    first = scheduler.create_process(
        process_id="process-host-a",
        agent=agent_a.control,
    )
    second = scheduler.create_process(
        process_id="process-host-b",
        agent=agent_b.control,
    )
    scheduler.dispatch(first.process_id)
    collector.record_tool_call(first.process_id, 1)
    scheduler.dispatch(second.process_id)
    collector.record_tool_call(second.process_id, 2)
    try:
        scheduler.safe_point(second.process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
    except ProcessBudgetExceeded as error:
        return error.exceeded.scope == "host" and second.state is ProcessState.BLOCKED
    return False


def _budget_block_once(
    scheduler: CooperativeScheduler,
    collector: UsageCollector,
    process_id: str,
    *,
    expected_scope: str,
) -> int:
    process = scheduler.manager.get(process_id)
    if process.state is ProcessState.READY:
        scheduler.dispatch(process_id)
    collector.record_tool_call(process_id, 4)
    try:
        scheduler.safe_point(process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
    except ProcessBudgetExceeded as error:
        if error.exceeded.scope != expected_scope:
            return 0
        collector.reset_process(process_id)
        scheduler.unblock(process_id)
        return 1
    return 0


def _agent_budget_block_once(world: _World) -> int:
    process_id = world.worker_process_id
    world.scheduler.update_agent_budget(
        "agent-worker",
        AgentBudget(max_total_tool_calls=1),
    )
    _dispatch_if_ready(world.scheduler, process_id)
    world.collector.record_tool_call(process_id, 2)
    blocked = 0
    try:
        world.scheduler.safe_point(process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
    except ProcessBudgetExceeded as error:
        if error.exceeded.scope == "agent":
            blocked = 1
    finally:
        world.collector.reset_process(process_id)
        world.scheduler.update_agent_budget(
            "agent-worker",
            AgentBudget(max_total_tool_calls=None),
        )
        if world.scheduler.manager.get(process_id).state is ProcessState.BLOCKED:
            world.scheduler.unblock(process_id)
    return blocked


def _host_budget_block_once(world: _World) -> int:
    process_id = world.parent_process_id
    previous_budget = world.scheduler.host_budget
    world.scheduler.update_host_budget(HostBudget(max_total_tool_calls=1))
    _dispatch_if_ready(world.scheduler, process_id)
    world.collector.record_tool_call(process_id, 2)
    blocked = 0
    try:
        world.scheduler.safe_point(process_id, SchedulerSafePoint.AFTER_TOOL_CALL)
    except ProcessBudgetExceeded as error:
        if error.exceeded.scope == "host":
            blocked = 1
    finally:
        world.collector.reset_process(process_id)
        world.scheduler.update_host_budget(previous_budget)
        if world.scheduler.manager.get(process_id).state is ProcessState.BLOCKED:
            world.scheduler.unblock(process_id)
    return blocked


def _dispatch_if_ready(
    scheduler: CooperativeScheduler,
    process_id: str,
) -> None:
    process = scheduler.manager.get(process_id)
    if process.state is ProcessState.READY:
        scheduler.dispatch(process_id)


def _event_counts(sessions: tuple[Session, ...]) -> dict[str, int]:
    return {session.session_id: len(session.events) for session in sessions}


def _record(
    case: str,
    strategy: str,
    metrics: dict[str, object],
) -> BenchmarkRecord:
    success = bool(metrics.get("success", False))
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case=case,
        strategy=strategy,
        metrics={**metrics, "success": success},
    )


def _empty_record() -> BenchmarkRecord:
    return _record("empty", "missing", {"success": False})


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _IdFactory:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"{self._prefix}{self._counter}"
