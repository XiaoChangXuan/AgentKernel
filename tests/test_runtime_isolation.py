from __future__ import annotations

import pytest

from agentkernel import (
    Agent,
    AgentBudget,
    AgentRegistry,
    CapabilityGrant,
    CooperativeScheduler,
    EventType,
    HostBudget,
    InMemoryIPCPersistence,
    KernelIPC,
    ModelUsage,
    OperationRecoveryClassification,
    ProcessBudgetExceeded,
    ProcessCancelled,
    ProcessManager,
    ProcessState,
    ProcessTreeError,
    RESOURCE_READ_ACTION,
    ResourceShareGrant,
    SchedulerSafePoint,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolEffectKind,
    UsageCollector,
    effective_runtime_budget,
)


def _agent(
    agent_id: str,
    session_id: str,
    *,
    budget: AgentBudget | None = None,
    capabilities: set[str] | None = None,
    capability_grants: tuple[CapabilityGrant, ...] = (),
) -> Agent:
    return Agent.create(
        agent_id=agent_id,
        session=Session(session_id),
        budget=budget,
        capabilities=capabilities or set(),
        capability_grants=capability_grants,
    )


def _running(
    scheduler: CooperativeScheduler,
    process_id: str,
    agent: Agent,
    *,
    budget: AgentBudget | None = None,
    parent_process_id: str | None = None,
):
    process = scheduler.create_process(
        process_id=process_id,
        agent=agent.control,
        budget=budget,
        parent_process_id=parent_process_id,
    )
    scheduler.dispatch(process_id)
    return process


def _record_tokens(collector: UsageCollector, process_id: str, total: int) -> None:
    collector.record_llm_usage(
        process_id,
        ModelUsage(
            input_tokens=total,
            output_tokens=0,
            total_tokens=total,
        ),
    )


def _prepared_session() -> Session:
    session = Session("session-wal")
    call = ToolCall("call-1", "payments.charge", {"amount": 42})
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "Pay."})
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "kernel-op-1",
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
        },
    )
    session.flush()
    return session


def _dispatched_session() -> Session:
    session = _prepared_session()
    session.append(
        EventType.TOOL_DISPATCH,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "kernel-op-1",
            "attempt": 1,
        },
    )
    session.flush()
    return session


def test_budget_hierarchy_validation_and_effective_limits() -> None:
    agent = _agent(
        "agent-a",
        "session-a",
        budget=AgentBudget(max_token_usage=5),
    )
    scheduler = CooperativeScheduler()

    with pytest.raises(ProcessTreeError, match="exceeds"):
        scheduler.create_process(
            process_id="process-too-large",
            agent=agent.control,
            budget=AgentBudget(max_token_usage=10),
        )

    host = HostBudget(max_token_usage=100)
    effective = effective_runtime_budget(
        host,
        agent.control.budget,
        AgentBudget(max_token_usage=None),
    )

    assert host.max_token_usage == 100
    assert effective.max_token_usage == 5


def test_scheduler_validates_host_supplied_agent_budget() -> None:
    agent = _agent("agent-a", "session-a")
    scheduler = CooperativeScheduler(
        agent_budgets={"agent-a": AgentBudget(max_token_usage=5)}
    )

    with pytest.raises(ProcessTreeError, match="exceeds"):
        scheduler.create_process(
            process_id="process-too-large",
            agent=agent.control,
            budget=AgentBudget(max_token_usage=10),
        )


def test_none_process_limit_still_obeys_agent_aggregate_budget() -> None:
    agent = _agent(
        "agent-a",
        "session-a",
        budget=AgentBudget(max_token_usage=5),
    )
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running(
        scheduler,
        "process-a",
        agent,
        budget=AgentBudget(max_token_usage=None),
    )
    _record_tokens(collector, process.process_id, 6)

    with pytest.raises(ProcessBudgetExceeded) as captured:
        scheduler.safe_point(process.process_id, SchedulerSafePoint.AFTER_LLM_CALL)

    assert captured.value.exceeded.scope == "agent"
    assert process.state is ProcessState.BLOCKED
    assert process.blocked_reason == "budget:agent:agent-a:max_token_usage"


def test_process_agent_and_host_budget_blocking_scopes() -> None:
    process_agent = _agent(
        "agent-process",
        "session-process",
        budget=AgentBudget(max_token_usage=20),
    )
    process_collector = UsageCollector()
    process_scheduler = CooperativeScheduler(usage_collector=process_collector)
    process = _running(
        process_scheduler,
        "process-local",
        process_agent,
        budget=AgentBudget(max_token_usage=5),
    )
    _record_tokens(process_collector, process.process_id, 6)

    with pytest.raises(ProcessBudgetExceeded) as process_error:
        process_scheduler.safe_point(
            process.process_id,
            SchedulerSafePoint.AFTER_LLM_CALL,
        )

    assert process_error.value.exceeded.scope == "process"
    assert process.blocked_reason == "budget:process:process-local:max_token_usage"
    assert process.exit_status is None

    aggregate_agent = _agent(
        "agent-aggregate",
        "session-aggregate",
        budget=AgentBudget(max_total_tool_calls=1),
    )
    aggregate_collector = UsageCollector()
    aggregate_scheduler = CooperativeScheduler(usage_collector=aggregate_collector)
    first = _running(
        aggregate_scheduler,
        "process-aggregate-1",
        aggregate_agent,
        budget=AgentBudget(max_total_tool_calls=None),
    )
    aggregate_scheduler.yield_process(first.process_id)
    second = _running(
        aggregate_scheduler,
        "process-aggregate-2",
        aggregate_agent,
        budget=AgentBudget(max_total_tool_calls=None),
    )
    aggregate_collector.record_tool_call(first.process_id)
    aggregate_collector.record_tool_call(second.process_id)

    with pytest.raises(ProcessBudgetExceeded) as agent_error:
        aggregate_scheduler.safe_point(
            second.process_id,
            SchedulerSafePoint.AFTER_TOOL_CALL,
        )

    assert agent_error.value.exceeded.scope == "agent"
    assert second.blocked_reason == (
        "budget:agent:agent-aggregate:max_total_tool_calls"
    )

    host_a = _agent("agent-host-a", "session-host-a")
    host_b = _agent("agent-host-b", "session-host-b")
    host_collector = UsageCollector()
    host_scheduler = CooperativeScheduler(
        usage_collector=host_collector,
        host_budget=HostBudget(max_total_tool_calls=1),
    )
    host_first = _running(host_scheduler, "process-host-1", host_a)
    host_second = _running(host_scheduler, "process-host-2", host_b)
    host_collector.record_tool_call(host_first.process_id)
    host_collector.record_tool_call(host_second.process_id)

    with pytest.raises(ProcessBudgetExceeded) as host_error:
        host_scheduler.safe_point(
            host_second.process_id,
            SchedulerSafePoint.AFTER_TOOL_CALL,
        )

    assert host_error.value.exceeded.scope == "host"
    assert host_second.blocked_reason == "budget:host:max_total_tool_calls"


def test_usage_attribution_uses_agent_identity_not_process_tree() -> None:
    agent_a = _agent("agent-a", "session-a")
    agent_b = _agent("agent-b", "session-b")
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    parent = scheduler.create_process(process_id="process-a", agent=agent_a.control)
    child = scheduler.create_process(
        process_id="process-b",
        agent=agent_b.control,
        parent_process_id=parent.process_id,
    )
    _record_tokens(collector, child.process_id, 7)

    agent_a_usage = collector.usage_for_agent(
        "agent-a",
        scheduler.manager.list_processes(),
    )
    agent_b_usage = collector.usage_for_agent(
        "agent-b",
        scheduler.manager.list_processes(),
    )
    host_usage = collector.host_usage(scheduler.manager.list_processes())

    assert agent_a_usage.token_usage == 0
    assert agent_b_usage.token_usage == 7
    assert host_usage.token_usage == 7


def test_agent_budget_isolation_between_agents_until_host_exhaustion() -> None:
    agent_a = _agent(
        "agent-a",
        "session-a",
        budget=AgentBudget(max_token_usage=5),
    )
    agent_b = _agent(
        "agent-b",
        "session-b",
        budget=AgentBudget(max_token_usage=100),
    )
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process_a = _running(
        scheduler,
        "process-a",
        agent_a,
        budget=AgentBudget(max_token_usage=None),
    )
    process_b = _running(
        scheduler,
        "process-b",
        agent_b,
        budget=AgentBudget(max_token_usage=None),
    )
    _record_tokens(collector, process_a.process_id, 6)
    _record_tokens(collector, process_b.process_id, 1)

    with pytest.raises(ProcessBudgetExceeded) as captured:
        scheduler.safe_point(process_a.process_id, SchedulerSafePoint.AFTER_LLM_CALL)

    assert captured.value.exceeded.subject == "agent-a"
    assert process_a.state is ProcessState.BLOCKED
    assert scheduler.safe_point(
        process_b.process_id,
        SchedulerSafePoint.AFTER_LLM_CALL,
    ) is process_b
    assert process_b.state is ProcessState.RUNNING


def test_budget_update_reset_and_unblock_are_host_policy_mechanisms() -> None:
    agent = _agent(
        "agent-a",
        "session-a",
        budget=AgentBudget(max_token_usage=10),
    )
    collector = UsageCollector()
    scheduler = CooperativeScheduler(usage_collector=collector)
    process = _running(
        scheduler,
        "process-a",
        agent,
        budget=AgentBudget(max_token_usage=5),
    )
    _record_tokens(collector, process.process_id, 6)

    with pytest.raises(ProcessBudgetExceeded):
        scheduler.safe_point(process.process_id, SchedulerSafePoint.AFTER_LLM_CALL)

    scheduler.update_process_budget(
        process.process_id,
        AgentBudget(max_token_usage=10),
    )
    scheduler.reset_usage(process.process_id)
    scheduler.unblock(process.process_id)

    assert process.state is ProcessState.READY
    assert process.blocked_reason is None
    assert collector.snapshot(process.process_id).token_usage == 0


def test_child_fault_notification_does_not_fail_parent_or_sibling() -> None:
    parent_agent = _agent("agent-parent", "session-parent")
    child_agent = _agent("agent-child", "session-child")
    sibling_agent = _agent("agent-sibling", "session-sibling")
    scheduler = CooperativeScheduler()
    parent = scheduler.create_process(
        process_id="process-parent",
        agent=parent_agent.control,
    )
    child = scheduler.create_process(
        process_id="process-child",
        agent=child_agent.control,
        parent_process_id=parent.process_id,
    )
    sibling = scheduler.create_process(
        process_id="process-sibling",
        agent=sibling_agent.control,
        parent_process_id=parent.process_id,
    )

    notification = scheduler.record_process_fault(child.process_id, "tool_crash")

    assert notification.process_id == child.process_id
    assert notification.exit_status == "failed:tool_crash"
    assert child.state is ProcessState.EXITED
    assert parent.state is ProcessState.READY
    assert sibling.state is ProcessState.READY
    assert scheduler.child_faults_of(parent.process_id) == (notification,)
    assert scheduler.ready_queue == (parent.process_id, sibling.process_id)


def test_structured_cancellation_follows_process_tree_only() -> None:
    root_agent = _agent("agent-root", "session-root")
    waiting_agent = _agent("agent-waiting", "session-waiting")
    blocked_agent = _agent("agent-blocked", "session-blocked")
    paused_agent = _agent("agent-paused", "session-paused")
    ready_agent = _agent("agent-ready", "session-ready")
    running_agent = _agent("agent-running", "session-running")
    unrelated_agent = _agent("agent-unrelated", "session-unrelated")
    scheduler = CooperativeScheduler()
    root = _running(scheduler, "process-root", root_agent)
    waiting = _running(
        scheduler,
        "process-waiting",
        waiting_agent,
        parent_process_id=root.process_id,
    )
    scheduler.yield_process(waiting.process_id, ProcessState.WAITING, reason="ipc")
    blocked = _running(
        scheduler,
        "process-blocked",
        blocked_agent,
        parent_process_id=root.process_id,
    )
    scheduler.yield_process(
        blocked.process_id,
        ProcessState.BLOCKED,
        reason="budget",
    )
    paused = scheduler.create_process(
        process_id="process-paused",
        agent=paused_agent.control,
        parent_process_id=root.process_id,
    )
    scheduler.pause(paused.process_id)
    ready = scheduler.create_process(
        process_id="process-ready",
        agent=ready_agent.control,
        parent_process_id=root.process_id,
    )
    running = _running(
        scheduler,
        "process-running-child",
        running_agent,
        parent_process_id=root.process_id,
    )
    unrelated = scheduler.create_process(
        process_id="process-unrelated",
        agent=unrelated_agent.control,
    )

    requested = scheduler.request_cancel_subtree(root.process_id, reason="shutdown")

    assert requested == (
        root.process_id,
        waiting.process_id,
        blocked.process_id,
        paused.process_id,
        ready.process_id,
        running.process_id,
    )
    assert waiting.state is ProcessState.EXITED
    assert blocked.state is ProcessState.EXITED
    assert paused.state is ProcessState.EXITED
    assert ready.state is ProcessState.EXITED
    assert running.state is ProcessState.RUNNING
    assert root.state is ProcessState.RUNNING
    assert unrelated.state is ProcessState.READY

    with pytest.raises(ProcessCancelled) as root_cancelled:
        scheduler.safe_point(root.process_id, SchedulerSafePoint.BEFORE_LLM_CALL)
    with pytest.raises(ProcessCancelled) as child_cancelled:
        scheduler.safe_point(running.process_id, SchedulerSafePoint.BEFORE_LLM_CALL)

    assert root_cancelled.value.reason == "shutdown"
    assert child_cancelled.value.reason == "shutdown"
    assert root.state is ProcessState.EXITED
    assert running.state is ProcessState.EXITED
    assert unrelated.state is ProcessState.READY
    assert scheduler.request_cancel_subtree(root.process_id, reason="shutdown") == ()


def test_cancellation_preserves_agent_capabilities_resource_shares_and_ipc() -> None:
    grant = CapabilityGrant("agent-a", TOOL_EXECUTE_ACTION, "tool://orders.create")
    agent_a = _agent(
        "agent-a",
        "session-a",
        capabilities={"orders.create"},
        capability_grants=(grant,),
    )
    agent_b = _agent("agent-b", "session-b")
    registry = AgentRegistry()
    registry.register_root(agent_a)
    registry.register_root(agent_b)
    manager = ProcessManager(agent_registry=registry)
    scheduler = CooperativeScheduler(manager)
    process_a = scheduler.create_process(process_id="process-a", agent=agent_a.control)
    process_b = scheduler.create_process(process_id="process-b", agent=agent_b.control)
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        scheduler=scheduler,
        persistence=InMemoryIPCPersistence(),
        sessions={"agent-a": agent_a.session, "agent-b": agent_b.session},
        channel_id_factory=lambda: "channel-ab",
        message_id_factory=lambda: "message-generated",
        time_fn=lambda: 1.0,
    )
    share = ResourceShareGrant(
        share_id="share_manual",
        resource_id="res_secret",
        owner_agent_id="agent-a",
        grantee_agent_id="agent-b",
        allowed_actions=(RESOURCE_READ_ACTION,),
        created_at=1.0,
        correlation_id="corr-share",
    )
    ipc.create_channel(
        sender_agent_id="agent-a",
        receiver_agent_id="agent-b",
        channel_id="channel-ab",
    )
    message = ipc.send(
        channel_id="channel-ab",
        sender_process_id=process_a.process_id,
        payload={"body": "durable envelope"},
        message_id="message-1",
    )

    scheduler.request_cancel_subtree(process_a.process_id, reason="stop")

    assert process_a.state is ProcessState.EXITED
    assert process_a.agent_id == "agent-a"
    assert process_a.capability_snapshot.agent_id == "agent-a"
    assert process_a.capability_snapshot.capabilities == frozenset({"orders.create"})
    assert process_a.capability_snapshot.capability_grants == (grant,)
    assert registry.get("agent-a").capability_grants == (grant,)
    assert share.allowed_actions == (RESOURCE_READ_ACTION,)
    assert ipc.get_message(message.message_id).payload == {"body": "durable envelope"}
    assert process_b.state is ProcessState.READY


def test_cancellation_does_not_erase_durable_tool_wal_boundaries() -> None:
    agent = _agent("agent-wal", "session-wal")
    scheduler = CooperativeScheduler()
    process = _running(scheduler, "process-wal", agent)
    scheduler.cancel(process.process_id, reason="stop-before-dispatch")
    empty_session = Session("session-empty")

    with pytest.raises(ProcessCancelled):
        scheduler.safe_point(
            process.process_id,
            SchedulerSafePoint.BEFORE_DURABLE_DISPATCH,
        )

    assert empty_session.events == ()

    prepared = _prepared_session()
    prepared_operation = prepared.recovery_analysis.durable_operations[0]
    prepared_process = scheduler.create_process(
        process_id="process-prepared",
        agent=agent.control,
    )
    scheduler.request_cancel_subtree(prepared_process.process_id, reason="stop")

    assert prepared_process.state is ProcessState.EXITED
    assert prepared_operation.classification is (
        OperationRecoveryClassification.SAFE_TO_RETRY
    )
    assert any(event.type is EventType.TOOL_PREPARE for event in prepared.events)

    dispatched = _dispatched_session()
    dispatched_operation = dispatched.recovery_analysis.durable_operations[0]
    dispatched_process = scheduler.create_process(
        process_id="process-dispatched",
        agent=agent.control,
    )
    scheduler.request_cancel_subtree(dispatched_process.process_id, reason="stop")

    assert dispatched_process.state is ProcessState.EXITED
    assert dispatched_operation.classification is (
        OperationRecoveryClassification.RECONCILE_REQUIRED
    )
    assert any(event.type is EventType.TOOL_DISPATCH for event in dispatched.events)
