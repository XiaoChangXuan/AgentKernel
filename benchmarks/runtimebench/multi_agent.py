"""RuntimeBench V0.8 multi-agent evidence scenarios.

The scenarios in this module are benchmark fixtures, not new Kernel features.
They exercise already implemented V0.8 primitives through public AgentKernel
APIs and report semantic invariants as deterministic ``BenchmarkRecord`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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
        reader_owner = ResourceOwner("agent-reader", "session-reader")
        worker_owner = ResourceOwner("agent-worker", "session-worker")
        reader_evaluator = CapabilityEvaluator(
            world.registry.get("agent-reader").capability_grants
        )
        worker_evaluator = CapabilityEvaluator(
            world.registry.get("agent-worker").capability_grants
        )
        counters = {
            "crashes": 0,
            "recoveries": 0,
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
            "reconciliations": 0,
            "unauthorized_effects": 0,
            "unsafe_duplicate_effects": 0,
            "cross_agent_resource_leaks": 0,
            "authority_escalations": 0,
            "lost_durable_facts": 0,
            "recovery_corruptions": 0,
            "unresolved_mandatory_wal": 0,
        }
        recovered_event_total = 0
        resource_bytes_read = 0
        crash_interval = max(10, horizon // 10)
        operation_interval = max(25, horizon // 5)
        budget_interval = max(20, horizon // 8)
        denial_interval = max(15, horizon // 12)

        for step in range(1, horizon + 1):
            message = world.ipc.send(
                channel_id=world.channel_id,
                sender_process_id=world.parent_process_id,
                payload={"step": step, "kind": "work"},
                message_id=f"message-{step}",
                correlation_id=f"corr-message-{step}",
            )
            counters["ipc_sent"] += 1
            delivered = world.ipc.receive(
                channel_id=world.channel_id,
                receiver_agent_id="agent-reader",
                receiver_process_id=world.reader_process_id,
            )
            if delivered is not None:
                if delivered.delivery_attempts > 1:
                    counters["ipc_redeliveries"] += 1
                counters["ipc_deliveries"] += 1
                world.ipc.ack(
                    channel_id=world.channel_id,
                    message_id=delivered.message_id,
                    receiver_agent_id="agent-reader",
                    receiver_process_id=world.reader_process_id,
                )
                counters["ipc_acks"] += 1
            if message.delivery_state is IPCMessageState.ACKED:
                counters["unsafe_duplicate_effects"] += 1

            if step % 10 == 0:
                try:
                    data = world.resources.read(
                        world.handle_uri,
                        owner=reader_owner,
                        offset=0,
                        limit=6,
                        capability_evaluator=reader_evaluator,
                    ).data
                    counters["resource_allowed"] += 1
                    resource_bytes_read += len(data)
                except ResourceAccessDenied:
                    counters["cross_agent_resource_leaks"] += 1

            if step % denial_interval == 0:
                try:
                    world.resources.read(
                        world.handle_uri,
                        owner=worker_owner,
                        offset=0,
                        limit=6,
                        capability_evaluator=worker_evaluator,
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

            if step % budget_interval == 0:
                counters["process_budget_blocks"] += _budget_block_once(
                    world.scheduler,
                    world.collector,
                    world.reader_process_id,
                    expected_scope="process",
                )

            if step == max(2, horizon // 3):
                world.scheduler.record_process_fault(
                    world.worker_process_id,
                    "synthetic_child_fault",
                )
                counters["child_faults"] += 1

            if step == max(3, (2 * horizon) // 3):
                cancelled = world.scheduler.request_cancel_subtree(
                    world.cancel_process_id,
                    reason="synthetic_cancel",
                )
                counters["cancellations"] += len(cancelled)

            if step % operation_interval == 0:
                _append_payment_operation(
                    world.parent_session,
                    agent_id="agent-parent",
                    operation_id=f"op-payment-{step}",
                    call_id=f"call-payment-{step}",
                    turn=counters["durable_operations"] + 1,
                    dispatched=True,
                    committed=True,
                    reconcile_status=ReconcileStatus.SUCCEEDED,
                )
                counters["durable_operations"] += 1
                counters["reconciliations"] += 1

            if step % crash_interval == 0:
                counters["crashes"] += 1
                before_counts = _event_counts(world.sessions)
                try:
                    result = recover_multi_agent_runtime(
                        world.sessions,
                        current_capability_grants={
                            "agent-parent": (world.root_grant,),
                        },
                        resource_store=world.store,
                        ipc_persistence=world.ipc_persistence,
                    )
                    counters["recoveries"] += 1
                    recovered_event_total += sum(
                        len(session.events) for session in world.sessions
                    )
                    if _event_counts(world.sessions) != before_counts:
                        counters["lost_durable_facts"] += 1
                    if result.durable_obligations:
                        counters["unresolved_mandatory_wal"] += len(
                            result.durable_obligations
                        )
                    if result.ipc is None or len(result.ipc.list_messages()) != step:
                        counters["recovery_corruptions"] += 1
                    if result.resource_shares is None:
                        counters["recovery_corruptions"] += 1
                except Exception:
                    counters["recovery_corruptions"] += 1

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

        semantic_success = (
            counters["unauthorized_effects"] == 0
            and counters["unsafe_duplicate_effects"] == 0
            and counters["cross_agent_resource_leaks"] == 0
            and counters["authority_escalations"] == 0
            and counters["lost_durable_facts"] == 0
            and counters["recovery_corruptions"] == 0
            and counters["unresolved_mandatory_wal"] == 0
            and counters["ipc_sent"] == horizon
            and counters["ipc_deliveries"] == horizon
            and counters["ipc_acks"] == horizon
        )

        metrics = {
            **counters,
            "logical_steps": horizon,
            "requested_horizon": horizon,
            "resource_bytes_read": resource_bytes_read,
            "recovered_events": recovered_event_total,
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
    del child
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
    child_evaluator = CapabilityEvaluator(
        registry.get("agent-child").capability_grants
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
    success = (
        allowed.allowed
        and not scope_denied.allowed
        and not action_denied.allowed
        and not constraint_denied.allowed
        and child_allowed
        and child_secret_denied
    )
    return _record(
        "M3_capability_delegation_narrowing",
        "child_grant_must_be_narrower_than_parent_authority",
        {
            "delegation_allows": 1 if allowed.allowed else 0,
            "delegation_denies": sum(
                1
                for decision in (scope_denied, action_denied, constraint_denied)
                if not decision.allowed
            ),
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
        states = {process.state for process in result.process_manager.list_processes()}
        success = (
            before_counts == after_counts
            and result.agent_registry.contains("agent-parent")
            and result.agent_registry.contains("agent-reader")
            and result.resource_shares is not None
            and result.ipc is not None
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
                "durable_obligations": len(result.durable_obligations),
                "reconcile_required": 1
                if obligation is not None
                and obligation.classification
                is OperationRecoveryClassification.RECONCILE_REQUIRED
                else 0,
                "lost_durable_facts": 0 if before_counts == after_counts else 1,
                "recovery_corruptions": 0 if success else 1,
                "unresolved_mandatory_wal": 0
                if result.durable_obligations
                else 1,
                "success": success,
            },
        )


def _m9_authority_shrink_after_restart_case() -> BenchmarkRecord:
    parent_session = Session("session-parent")
    child_session = Session("session-child")
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
    broad_recovery = recover_multi_agent_runtime(
        (parent_session, child_session),
        current_capability_grants={"agent-parent": (root_grant,)},
    )
    shrink_recovery = recover_multi_agent_runtime(
        (parent_session, child_session),
        current_capability_grants={"agent-parent": ()},
    )
    broad_child_grants = broad_recovery.agent_registry.get(
        "agent-child"
    ).capability_grants
    shrink_child_grants = shrink_recovery.agent_registry.get(
        "agent-child"
    ).capability_grants
    shrink_allowed = CapabilityEvaluator(shrink_child_grants).authorize(
        AuthorizationRequest(
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://anything",
        )
    ).allowed
    historical_facts = sum(
        1
        for event in child_session.events
        if event.type is EventType.CAPABILITY_DELEGATED
    )
    success = (
        decision.allowed
        and len(broad_child_grants) == 1
        and len(shrink_child_grants) == 0
        and not shrink_allowed
        and historical_facts == 1
    )
    return _record(
        "M9_authority_shrink_after_restart",
        "current_parent_authority_bounds_replayed_delegations",
        {
            "historical_delegation_facts": historical_facts,
            "broad_restart_child_grants": len(broad_child_grants),
            "shrink_restart_child_grants": len(shrink_child_grants),
            "shrink_restart_authorized": shrink_allowed,
            "authority_escalations": 1 if shrink_allowed else 0,
            "lost_durable_facts": 0 if historical_facts == 1 else 1,
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
            "reconciliations": sum(
                int(record.metrics["reconciliations"]) for record in records
            ),
            **invariants,
            "semantic_invariants_passed": success,
            "success": success,
        },
    )


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
