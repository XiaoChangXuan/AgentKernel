from __future__ import annotations

import pytest

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
    IPCChannel,
    IPCMessageState,
    IPCMessageEnvelope,
    InMemoryIPCPersistence,
    IPCRecordType,
    KernelIPC,
    LocalResourceStore,
    MultiAgentRecoveryCorruptionError,
    OperationRecoveryClassification,
    ProcessManager,
    ProcessRecoveryDisposition,
    ProcessState,
    RESOURCE_READ_ACTION,
    ReconcileStatus,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    ResourceShareGrant,
    ResourceShareRegistry,
    SchedulerSafePoint,
    Session,
    ToolCall,
    ToolEffectKind,
    UsageCollector,
    recover_multi_agent_runtime,
)
from agentkernel.protocol import JsonValue


def _runtime_world(tmp_path):
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
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
    child_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-child",
        agent=child.control,
        record_session=child_session,
        creation_id="create-process-child",
    )

    store = LocalResourceStore(tmp_path / "resources")
    shares = ResourceShareRegistry(
        agent_registry=registry,
        clock=lambda: 100.0,
        share_id_factory=lambda: "share-generated",
    )
    resources = ResourceService(
        store,
        share_registry=shares,
        resource_id_factory=lambda: "res_secret",
        handle_id_factory=lambda: "hdl_secret",
        clock=lambda: 10.0,
    )
    owner = ResourceOwner(parent.control.agent_id, parent.control.session_id)
    handle = resources.create_artifact(
        b"secret",
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="producer",
        source_tool_call_id="call-producer",
        source_operation_id="op-producer",
    )
    share = resources.share(
        handle.uri,
        owner=owner,
        grantee_agent_id=child.control.agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        record_session=parent_session,
        share_id="share_secret",
        correlation_id="corr-share",
    )
    assert share.allowed

    ipc_persistence = InMemoryIPCPersistence()
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        persistence=ipc_persistence,
        sessions={
            parent.control.agent_id: parent_session,
            child.control.agent_id: child_session,
        },
        channel_id_factory=lambda: "channel-parent-child",
        message_id_factory=lambda: "message-generated",
        time_fn=lambda: 1.0,
    )
    ipc.create_channel(
        channel_id="channel-parent-child",
        sender_agent_id=parent.control.agent_id,
        receiver_agent_id=child.control.agent_id,
        receiver_process_id=child_process.process_id,
    )
    message = ipc.send(
        channel_id="channel-parent-child",
        sender_process_id=parent_process.process_id,
        payload={"body": "hello"},
        resource_refs=(handle.uri,),
        message_id="message-1",
        correlation_id="corr-message",
    )

    return {
        "registry": registry,
        "manager": manager,
        "ipc": ipc,
        "parent_session": parent_session,
        "child_session": child_session,
        "parent_process_id": parent_process.process_id,
        "child_process_id": child_process.process_id,
        "channel_id": "channel-parent-child",
        "handle": handle,
        "store": store,
        "ipc_persistence": ipc_persistence,
        "message_id": message.message_id,
    }


def _identity_world():
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
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
    child_process = manager.create_child_process(
        parent_process_id=parent_process.process_id,
        process_id="process-child",
        agent=child.control,
        record_session=child_session,
        creation_id="create-process-child",
    )
    return {
        "registry": registry,
        "manager": manager,
        "parent_session": parent_session,
        "child_session": child_session,
        "parent_process_id": parent_process.process_id,
        "child_process_id": child_process.process_id,
    }


def _payment_call() -> ToolCall:
    return ToolCall(
        "call-payment",
        "payment.charge",
        {"invoice_id": "invoice-1", "amount_cents": 4200},
    )


def _authorization_context(agent_id: str) -> dict[str, JsonValue]:
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


def _append_payment_operation(
    session: Session,
    *,
    agent_id: str | None,
    effect_kind: ToolEffectKind = ToolEffectKind.RECONCILABLE_MUTATION,
    operation_id: str = "op-payment",
    dispatched: bool = True,
    committed: bool = False,
    reconcile_status: ReconcileStatus | None = None,
) -> None:
    call = _payment_call()
    authorization = None if agent_id is None else _authorization_context(agent_id)
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": 1, "content": "Charge invoice-1."},
    )
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
    if authorization is not None:
        session.append(
            EventType.AUTHORIZATION_GRANTED,
            {
                "turn": 1,
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
            "turn": 1,
            "step": 1,
            "operation_id": operation_id,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": effect_kind.value,
            **({} if authorization is None else {"authorization": authorization}),
        },
    )
    if dispatched:
        if authorization is not None:
            session.append(
                EventType.AUTHORIZATION_GRANTED,
                {
                    "turn": 1,
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
                "turn": 1,
                "step": 1,
                "operation_id": operation_id,
                "attempt": 1,
                **({} if authorization is None else {"authorization": authorization}),
            },
        )
    if reconcile_status is not None:
        session.append(
            EventType.TOOL_RECONCILE,
            {
                "turn": 1,
                "step": 1,
                "operation_id": operation_id,
                "observed_status": reconcile_status.value,
                **(
                    {"output": {"ok": True}}
                    if reconcile_status is ReconcileStatus.SUCCEEDED
                    else {}
                ),
            },
        )
    if committed:
        session.append(
            EventType.TOOL_COMMIT,
            {
                "turn": 1,
                "step": 1,
                "operation_id": operation_id,
                "output": {"charged": True},
            },
        )


def _append_dispatched_payment(session: Session, *, agent_id: str) -> None:
    _append_payment_operation(session, agent_id=agent_id)


def _sessions(world) -> tuple[Session, Session]:
    return world["parent_session"], world["child_session"]


def _read_grant(agent_id: str, scope: str = ARTIFACT_RESOURCE_SCOPE) -> CapabilityGrant:
    return CapabilityGrant(agent_id, RESOURCE_READ_ACTION, scope)


def _read_evaluator(agent_id: str) -> CapabilityEvaluator:
    return CapabilityEvaluator((_read_grant(agent_id),))


def _recover_world(world, **kwargs):
    parent_session, child_session = _sessions(world)
    defaults = {
        "resource_store": world.get("store"),
        "ipc_persistence": world.get("ipc_persistence"),
    }
    defaults.update(kwargs)
    return recover_multi_agent_runtime((parent_session, child_session), **defaults)


def _delegation_world():
    root_grant = _read_grant("agent-parent")
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
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
    child_process = manager.create_child_process(
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
        ),
        record_session=child_session,
    )
    assert decision.allowed is True
    return {
        "root_grant": root_grant,
        "delegated_grant": decision.delegated_grant,
        "parent_session": parent_session,
        "child_session": child_session,
        "parent_process_id": parent_process.process_id,
        "child_process_id": child_process.process_id,
    }


def _ipc_truth_world(*, bind_sessions: bool = False, tmp_path=None):
    world = _identity_world()
    sessions = (
        {
            "agent-parent": world["parent_session"],
            "agent-child": world["child_session"],
        }
        if bind_sessions
        else None
    )
    persistence = InMemoryIPCPersistence()
    ipc = KernelIPC(
        agent_registry=world["registry"],
        process_manager=world["manager"],
        persistence=persistence,
        sessions=sessions,
        time_fn=lambda: 7.0,
    )
    ipc.create_channel(
        channel_id="channel-parent-child",
        sender_agent_id="agent-parent",
        receiver_agent_id="agent-child",
        receiver_process_id=world["child_process_id"],
    )
    message = ipc.send(
        channel_id="channel-parent-child",
        sender_process_id=world["parent_process_id"],
        payload={"body": "hello"},
        message_id="message-1",
        correlation_id="corr-1",
    )
    world.update(
        {
            "ipc": ipc,
            "ipc_persistence": persistence,
            "channel_id": "channel-parent-child",
            "message_id": message.message_id,
        }
    )
    return world


def _corrupt_ipc_persistence(
    world,
    *,
    sender_process_id: str | None = None,
    receiver_process_id: str | None = None,
    channel_receiver_process_id: str | None = None,
) -> InMemoryIPCPersistence:
    persistence = InMemoryIPCPersistence()
    channel = IPCChannel(
        channel_id="channel-corrupt",
        sender_agent_id="agent-parent",
        receiver_agent_id="agent-child",
        receiver_process_id=(
            world["child_process_id"]
            if channel_receiver_process_id is None
            else channel_receiver_process_id
        ),
        created_at=1.0,
    )
    envelope = IPCMessageEnvelope(
        message_id="message-corrupt",
        channel_id=channel.channel_id,
        sender_agent_id="agent-parent",
        sender_process_id=sender_process_id or world["parent_process_id"],
        receiver_agent_id="agent-child",
        receiver_process_id=(
            world["child_process_id"]
            if receiver_process_id is None
            else receiver_process_id
        ),
        payload={"body": "bad participant"},
        resource_refs=(),
        sequence=1,
        correlation_id="corr-corrupt",
        created_at=2.0,
    )
    persistence.append(IPCRecordType.CHANNEL_CREATED, channel.as_dict())
    persistence.append(IPCRecordType.MESSAGE_SENT, envelope.as_dict())
    return persistence


def _share_payload(*, owner_agent_id: str, grantee_agent_id: str = "agent-child"):
    return ResourceShareGrant(
        share_id="share_corrupt",
        resource_id="res_secret",
        owner_agent_id=owner_agent_id,
        grantee_agent_id=grantee_agent_id,
        allowed_actions=(RESOURCE_READ_ACTION,),
        created_at=100.0,
        correlation_id="corr-corrupt",
    ).as_payload()


def _runtime_facts_session(
    *,
    session_id: str = "session-agent",
    agent_id: str = "agent-a",
    parent_agent_id: str | None = None,
    process_id: str = "process-a",
    parent_process_id: str | None = None,
    process_session_id: str | None = None,
    creation_id: str = "create-agent-a",
    process_creation_id: str = "create-process-a",
) -> Session:
    session = Session(session_id)
    session.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "session_id": session_id,
            "creation_id": creation_id,
        },
    )
    session.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": process_id,
            "agent_id": agent_id,
            "session_id": process_session_id or session_id,
            "parent_process_id": parent_process_id,
            "creation_id": process_creation_id,
        },
    )
    return session


def test_integrated_recovery_replays_runtime_indexes_without_mutation(tmp_path) -> None:
    world = _runtime_world(tmp_path)
    parent_session = world["parent_session"]
    child_session = world["child_session"]
    ipc_persistence = world["ipc_persistence"]
    event_counts = {
        parent_session.session_id: len(parent_session.events),
        child_session.session_id: len(child_session.events),
    }
    ipc_record_count = len(ipc_persistence.load())

    result = recover_multi_agent_runtime(
        (parent_session, child_session),
        resource_store=world["store"],
        ipc_persistence=ipc_persistence,
    )

    assert result.agent_registry.contains("agent-parent")
    assert result.agent_registry.parent_of("agent-child") == "agent-parent"
    assert result.process_manager.get(world["parent_process_id"]).state is (
        ProcessState.CREATED
    )
    assert result.process_manager.parent_of(world["child_process_id"]) == (
        world["parent_process_id"]
    )
    assert result.scheduler.ready_queue == ()
    assert result.resource_shares is not None
    assert result.resource_shares.is_shared_with(
        resource_id="res_secret",
        grantee_agent_id="agent-child",
        action=RESOURCE_READ_ACTION,
    )
    assert result.ipc is not None
    assert result.ipc.get_message(world["message_id"]).delivery_state is (
        IPCMessageState.PENDING
    )
    assert result.ipc.get_message(world["message_id"]).payload == {"body": "hello"}
    assert result.durable_obligations == ()
    assert {
        disposition.disposition
        for disposition in result.process_dispositions
    } == {ProcessRecoveryDisposition.NOT_ADMITTED}
    assert len(parent_session.events) == event_counts[parent_session.session_id]
    assert len(child_session.events) == event_counts[child_session.session_id]
    assert len(ipc_persistence.load()) == ipc_record_count


def test_dispatched_durable_operation_surfaces_reconciliation_obligation(
    tmp_path,
) -> None:
    world = _runtime_world(tmp_path)
    parent_session = world["parent_session"]
    _append_dispatched_payment(parent_session, agent_id="agent-parent")

    result = recover_multi_agent_runtime(
        (parent_session, world["child_session"]),
        resource_store=world["store"],
        ipc_persistence=world["ipc_persistence"],
    )

    assert len(result.durable_obligations) == 1
    obligation = result.durable_obligations[0]
    assert obligation.session_id == parent_session.session_id
    assert obligation.operation_id == "op-payment"
    assert obligation.classification is OperationRecoveryClassification.RECONCILE_REQUIRED
    assert obligation.authorization is not None
    assert obligation.authorization["agent_id"] == "agent-parent"
    dispositions = {
        item.process_id: item.disposition
        for item in result.process_dispositions
    }
    assert dispositions[world["parent_process_id"]] is (
        ProcessRecoveryDisposition.NEEDS_RECONCILIATION
    )
    assert dispositions[world["child_process_id"]] is (
        ProcessRecoveryDisposition.NOT_ADMITTED
    )


def test_recovery_rejects_process_agent_session_mismatch() -> None:
    session = Session("session-agent")
    session.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-a",
            "parent_agent_id": None,
            "session_id": "session-agent",
            "creation_id": "create-agent-a",
        },
    )
    session.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-a",
            "agent_id": "agent-a",
            "session_id": "session-other",
            "parent_process_id": None,
            "creation_id": "create-process-a",
        },
    )

    with pytest.raises(MultiAgentRecoveryCorruptionError, match="session"):
        recover_multi_agent_runtime((session,))


def test_missing_resource_store_fails_only_when_share_facts_exist(tmp_path) -> None:
    world = _runtime_world(tmp_path)

    with pytest.raises(MultiAgentRecoveryCorruptionError, match="ResourceStore"):
        _recover_world(world, resource_store=None)

    no_resource_world = _identity_world()
    result = recover_multi_agent_runtime(
        _sessions(no_resource_world),
        resource_store=None,
        ipc_persistence=None,
    )

    assert result.resource_shares is None
    assert result.ipc is None


def test_missing_ipc_persistence_fails_only_when_ipc_audit_facts_exist(tmp_path) -> None:
    world = _runtime_world(tmp_path)

    with pytest.raises(MultiAgentRecoveryCorruptionError, match="IPCPersistence"):
        _recover_world(world, ipc_persistence=None)

    no_ipc_world = _identity_world()
    result = recover_multi_agent_runtime(
        _sessions(no_ipc_world),
        resource_store=None,
        ipc_persistence=None,
    )

    assert result.ipc is None


def test_durable_authorization_principal_must_match_session_owner(tmp_path) -> None:
    world = _runtime_world(tmp_path)
    parent_session = world["parent_session"]
    _append_payment_operation(parent_session, agent_id="agent-child")

    with pytest.raises(MultiAgentRecoveryCorruptionError, match="authorization"):
        _recover_world(world)


def test_legacy_durable_operation_without_authorization_stays_compatible(
    tmp_path,
) -> None:
    world = _identity_world()
    parent_session = world["parent_session"]
    _append_payment_operation(parent_session, agent_id=None)

    result = recover_multi_agent_runtime(_sessions(world))

    assert len(result.durable_obligations) == 1
    assert result.durable_obligations[0].authorization is None
    assert result.durable_obligations[0].classification is (
        OperationRecoveryClassification.RECONCILE_REQUIRED
    )


def test_capability_recovery_requires_current_parent_authority() -> None:
    world = _delegation_world()

    without_authority = recover_multi_agent_runtime(_sessions(world))
    with_authority = recover_multi_agent_runtime(
        _sessions(world),
        current_capability_grants={
            "agent-parent": (world["root_grant"],),
        },
    )

    assert without_authority.agent_registry.get("agent-child").capability_grants == ()
    assert with_authority.agent_registry.get("agent-child").capability_grants == (
        world["delegated_grant"],
    )


def test_capability_shrink_disables_historical_child_authority() -> None:
    world = _delegation_world()
    narrowed = CapabilityGrant(
        "agent-parent",
        RESOURCE_READ_ACTION,
        "artifact://project-b/**",
    )

    result = recover_multi_agent_runtime(
        _sessions(world),
        current_capability_grants={"agent-parent": (narrowed,)},
    )

    assert result.agent_registry.get("agent-child").capability_grants == ()


def test_multi_hop_parent_authority_loss_invalidates_child_chain() -> None:
    root_grant = _read_grant("agent-root")
    root_session = Session("session-root")
    child_session = Session("session-child")
    grandchild_session = Session("session-grandchild")
    registry = AgentRegistry()
    root = registry.create_root(
        agent_id="agent-root",
        session=root_session,
        capability_grants=(root_grant,),
        creation_id="create-root",
    )
    child = registry.create_child(
        parent_agent_id=root.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
    )
    grandchild = registry.create_child(
        parent_agent_id=child.control.agent_id,
        agent_id="agent-grandchild",
        session=grandchild_session,
        creation_id="create-grandchild",
    )
    manager = ProcessManager(agent_registry=registry)
    root_process = manager.create_process(
        process_id="process-root",
        agent=root.control,
        record_session=root_session,
        creation_id="create-process-root",
    )
    child_process = manager.create_child_process(
        parent_process_id=root_process.process_id,
        process_id="process-child",
        agent=child.control,
        record_session=child_session,
        creation_id="create-process-child",
    )
    manager.create_child_process(
        parent_process_id=child_process.process_id,
        process_id="process-grandchild",
        agent=grandchild.control,
        record_session=grandchild_session,
        creation_id="create-process-grandchild",
    )
    child_decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-root",
            "agent-child",
            RESOURCE_READ_ACTION,
            ARTIFACT_RESOURCE_SCOPE,
        ),
        record_session=child_session,
    )
    grandchild_decision = registry.delegate_capability(
        DelegateCapabilityRequest(
            "agent-child",
            "agent-grandchild",
            RESOURCE_READ_ACTION,
            ARTIFACT_RESOURCE_SCOPE,
        ),
        record_session=grandchild_session,
    )

    active = recover_multi_agent_runtime(
        (root_session, child_session, grandchild_session),
        current_capability_grants={"agent-root": (root_grant,)},
    )
    inactive = recover_multi_agent_runtime(
        (root_session, child_session, grandchild_session),
        current_capability_grants={},
    )

    assert child_decision.allowed is True
    assert grandchild_decision.allowed is True
    assert active.agent_registry.get("agent-grandchild").capability_grants == (
        grandchild_decision.delegated_grant,
    )
    assert inactive.agent_registry.get("agent-child").capability_grants == ()
    assert inactive.agent_registry.get("agent-grandchild").capability_grants == ()


def test_process_snapshot_and_lineage_do_not_create_authority() -> None:
    world = _delegation_world()
    result = recover_multi_agent_runtime(_sessions(world))
    child = result.agent_registry.get("agent-child")
    child_process = result.process_manager.get(world["child_process_id"])

    assert child.capability_grants == ()
    assert child_process.parent_process_id == world["parent_process_id"]
    assert child_process.capability_snapshot is not None
    assert child_process.capability_snapshot.capability_grants == ()
    assert not CapabilityEvaluator(child.capability_grants).authorize(
        AuthorizationRequest(
            "agent-child",
            RESOURCE_READ_ACTION,
            "artifact://anything",
        )
    ).allowed


def test_ipc_recovery_preserves_pending_delivered_acked_sequence_and_occupancy() -> None:
    pending_world = _ipc_truth_world()
    pending_world["ipc"].send(
        channel_id=pending_world["channel_id"],
        sender_process_id=pending_world["parent_process_id"],
        payload={"body": "second"},
        message_id="message-2",
        correlation_id="corr-2",
    )
    pending = _recover_world(
        pending_world,
        resource_store=None,
        ipc_persistence=pending_world["ipc_persistence"],
    )
    assert pending.ipc is not None
    assert [message.sequence for message in pending.ipc.list_messages()] == [1, 2]
    assert pending.ipc.get_message("message-1").delivery_state is (
        IPCMessageState.PENDING
    )
    assert pending.ipc.live_occupancy(pending_world["channel_id"])["messages"] == 2

    delivered_world = _ipc_truth_world()
    delivered = delivered_world["ipc"].receive(
        channel_id=delivered_world["channel_id"],
        receiver_agent_id="agent-child",
        receiver_process_id=delivered_world["child_process_id"],
    )
    assert delivered is not None
    delivered_result = _recover_world(
        delivered_world,
        resource_store=None,
        ipc_persistence=delivered_world["ipc_persistence"],
    )
    assert delivered_result.ipc is not None
    redelivered = delivered_result.ipc.receive(
        channel_id=delivered_world["channel_id"],
        receiver_agent_id="agent-child",
        receiver_process_id=delivered_world["child_process_id"],
    )
    assert redelivered is not None
    assert redelivered.delivery_attempts == 2

    acked_world = _ipc_truth_world()
    received = acked_world["ipc"].receive(
        channel_id=acked_world["channel_id"],
        receiver_agent_id="agent-child",
        receiver_process_id=acked_world["child_process_id"],
    )
    assert received is not None
    acked_world["ipc"].ack(
        channel_id=acked_world["channel_id"],
        message_id=received.message_id,
        receiver_agent_id="agent-child",
        receiver_process_id=acked_world["child_process_id"],
    )
    acked = _recover_world(
        acked_world,
        resource_store=None,
        ipc_persistence=acked_world["ipc_persistence"],
    )
    assert acked.ipc is not None
    assert acked.ipc.get_message("message-1").delivery_state is IPCMessageState.ACKED
    assert (
        acked.ipc.receive(
            channel_id=acked_world["channel_id"],
            receiver_agent_id="agent-child",
            receiver_process_id=acked_world["child_process_id"],
        )
        is None
    )
    assert acked.ipc.live_occupancy(acked_world["channel_id"]) == {
        "messages": 0,
        "bytes": 0,
    }


def test_ipc_participant_mismatch_fails_closed_and_payload_is_audit_only() -> None:
    world = _ipc_truth_world()
    bad_sender = _corrupt_ipc_persistence(
        world,
        sender_process_id=world["child_process_id"],
    )
    with pytest.raises(MultiAgentRecoveryCorruptionError, match="sender"):
        _recover_world(world, resource_store=None, ipc_persistence=bad_sender)

    bad_receiver = _corrupt_ipc_persistence(
        world,
        receiver_process_id=world["parent_process_id"],
    )
    with pytest.raises(MultiAgentRecoveryCorruptionError, match="receiver"):
        _recover_world(world, resource_store=None, ipc_persistence=bad_receiver)

    payload_world = _identity_world()
    persistence = InMemoryIPCPersistence()
    ipc = KernelIPC(
        agent_registry=payload_world["registry"],
        process_manager=payload_world["manager"],
        persistence=persistence,
    )
    ipc.create_channel(
        channel_id="channel-grant-payload",
        sender_agent_id="agent-parent",
        receiver_agent_id="agent-child",
    )
    ipc.send(
        channel_id="channel-grant-payload",
        sender_process_id=payload_world["parent_process_id"],
        payload={
            "grant": {
                "subject": "agent-child",
                "action": RESOURCE_READ_ACTION,
                "resource_scope": ARTIFACT_RESOURCE_SCOPE,
            }
        },
        message_id="message-grant-like",
    )
    result = _recover_world(
        payload_world,
        resource_store=None,
        ipc_persistence=persistence,
    )

    assert result.ipc is not None
    assert result.ipc.get_message("message-grant-like").payload["grant"]["subject"] == (
        "agent-child"
    )
    assert result.agent_registry.get("agent-child").capability_grants == ()


def test_resource_recovery_requires_store_owner_and_dual_authority(tmp_path) -> None:
    world = _runtime_world(tmp_path)
    result = _recover_world(world)
    assert result.resource_shares is not None
    service = ResourceService(world["store"], share_registry=result.resource_shares)
    child_owner = ResourceOwner("agent-child", "session-child")

    with pytest.raises(ResourceAccessDenied, match="lacks"):
        service.read(
            world["handle"].uri,
            owner=child_owner,
            capability_evaluator=CapabilityEvaluator(()),
        )
    assert (
        service.read(
            world["handle"].uri,
            owner=child_owner,
            capability_evaluator=_read_evaluator("agent-child"),
        ).data
        == b"secret"
    )

    capability_only = ResourceService(world["store"])
    with pytest.raises(ResourceAccessDenied, match="not shared"):
        capability_only.read(
            world["handle"].uri,
            owner=child_owner,
            capability_evaluator=_read_evaluator("agent-child"),
        )


def test_resource_share_corruption_fails_closed(tmp_path) -> None:
    wrong_owner = _runtime_world(tmp_path / "wrong-owner")
    wrong_owner["parent_session"].append(
        EventType.RESOURCE_SHARED,
        _share_payload(owner_agent_id="agent-child", grantee_agent_id="agent-parent"),
    )
    with pytest.raises(MultiAgentRecoveryCorruptionError, match="owner"):
        _recover_world(wrong_owner)

    missing_resource = _runtime_world(tmp_path / "missing-resource")
    missing_resource["parent_session"].append(
        EventType.RESOURCE_SHARED,
        ResourceShareGrant(
            share_id="share_missing",
            resource_id="res_missing",
            owner_agent_id="agent-parent",
            grantee_agent_id="agent-child",
            allowed_actions=(RESOURCE_READ_ACTION,),
            created_at=101.0,
            correlation_id="corr-missing",
        ).as_payload(),
    )
    with pytest.raises(MultiAgentRecoveryCorruptionError, match="missing resource"):
        _recover_world(missing_resource)


def test_scheduler_and_process_runtime_state_are_not_restored(tmp_path) -> None:
    world = _runtime_world(tmp_path)
    scheduler = CooperativeScheduler(world["manager"])
    parent = world["manager"].get(world["parent_process_id"])
    child = world["manager"].get(world["child_process_id"])
    parent.transition(ProcessState.RUNNING)
    scheduler.yield_process(parent.process_id, ProcessState.WAITING, reason="llm")
    child.transition(ProcessState.RUNNING)
    scheduler.yield_process(child.process_id, ProcessState.BLOCKED, reason="budget")
    scheduler.record_process_fault(child.process_id, "tool_crash")

    result = _recover_world(world)

    assert result.process_manager.get(world["parent_process_id"]).state is (
        ProcessState.CREATED
    )
    assert result.process_manager.get(world["child_process_id"]).state is (
        ProcessState.CREATED
    )
    assert result.scheduler.ready_queue == ()
    assert result.scheduler.waiting_registry == {}
    assert result.scheduler.blocked_registry == {}
    assert result.scheduler.child_faults_of(world["parent_process_id"]) == ()


def test_accounting_runtime_usage_is_not_restored_and_host_budget_is_current(
    tmp_path,
) -> None:
    world = _runtime_world(tmp_path)
    collector = UsageCollector(clock=lambda: 10.0)
    collector.record_tool_call(world["parent_process_id"], 3)
    collector.record_resource_read(world["parent_process_id"], 128)
    assert collector.snapshot(world["parent_process_id"]).tool_calls == 3

    restart_budget = HostBudget(max_total_tool_calls=1)
    result = _recover_world(world, host_budget=restart_budget)

    assert result.scheduler.usage_collector is None
    assert result.scheduler.host_budget == restart_budget


def test_wal_obligations_are_surfaced_without_retry_or_side_effects() -> None:
    prepared = _identity_world()
    _append_payment_operation(
        prepared["parent_session"],
        agent_id="agent-parent",
        operation_id="op-prepared",
        dispatched=False,
    )
    dispatched = _identity_world()
    _append_payment_operation(
        dispatched["parent_session"],
        agent_id="agent-parent",
        operation_id="op-dispatched",
        effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
    )
    manual = _identity_world()
    _append_payment_operation(
        manual["parent_session"],
        agent_id="agent-parent",
        operation_id="op-manual",
        effect_kind=ToolEffectKind.OPAQUE_MUTATION,
    )
    committed = _identity_world()
    _append_payment_operation(
        committed["parent_session"],
        agent_id="agent-parent",
        operation_id="op-committed",
        committed=True,
    )

    prepared_events = len(prepared["parent_session"].events)
    prepared_result = recover_multi_agent_runtime(_sessions(prepared))
    dispatched_result = recover_multi_agent_runtime(_sessions(dispatched))
    manual_result = recover_multi_agent_runtime(_sessions(manual))
    committed_result = recover_multi_agent_runtime(_sessions(committed))

    assert prepared_result.durable_obligations[0].classification is (
        OperationRecoveryClassification.SAFE_TO_RETRY
    )
    assert dispatched_result.durable_obligations[0].classification is (
        OperationRecoveryClassification.RECONCILE_REQUIRED
    )
    assert manual_result.durable_obligations[0].classification is (
        OperationRecoveryClassification.MANUAL_REQUIRED
    )
    assert committed_result.durable_obligations == ()
    assert len(prepared["parent_session"].events) == prepared_events
    assert not any(
        event.type in {EventType.TOOL_RECONCILE, EventType.TOOL_COMMIT}
        for event in prepared["parent_session"].events[prepared_events:]
    )


def test_failed_runtime_process_cannot_erase_wal_obligation(tmp_path) -> None:
    world = _runtime_world(tmp_path)
    _append_dispatched_payment(world["parent_session"], agent_id="agent-parent")
    process = world["manager"].get(world["parent_process_id"])
    process.transition(ProcessState.RUNNING)
    process.transition(ProcessState.EXITED, exit_status="failed:crash")

    result = _recover_world(world)

    assert len(result.durable_obligations) == 1
    assert result.durable_obligations[0].classification is (
        OperationRecoveryClassification.RECONCILE_REQUIRED
    )
    assert result.process_manager.get(world["parent_process_id"]).state is (
        ProcessState.CREATED
    )


def test_cross_journal_ipc_truth_does_not_depend_on_session_audit() -> None:
    no_audit = _ipc_truth_world(bind_sessions=False)
    result = _recover_world(
        no_audit,
        resource_store=None,
        ipc_persistence=no_audit["ipc_persistence"],
    )
    assert result.ipc is not None
    assert result.ipc.get_message("message-1").delivery_state is (
        IPCMessageState.PENDING
    )
    assert not any(
        event.type in {EventType.IPC_SEND, EventType.IPC_RECEIVE, EventType.IPC_ACK}
        for session in _sessions(no_audit)
        for event in session.events
    )

    receive_audit_only = _ipc_truth_world(bind_sessions=False)
    receive_audit_only["child_session"].append(
        EventType.IPC_RECEIVE,
        {
            "message_id": "message-1",
            "channel_id": receive_audit_only["channel_id"],
            "sender_agent_id": "agent-parent",
            "sender_process_id": receive_audit_only["parent_process_id"],
            "receiver_agent_id": "agent-child",
            "receiver_process_id": receive_audit_only["child_process_id"],
            "sequence": 1,
            "correlation_id": "corr-1",
            "delivery_attempts": 1,
        },
    )
    audited = _recover_world(
        receive_audit_only,
        resource_store=None,
        ipc_persistence=receive_audit_only["ipc_persistence"],
    )
    assert audited.ipc is not None
    assert audited.ipc.get_message("message-1").delivery_state is (
        IPCMessageState.PENDING
    )

    delivered_truth = _ipc_truth_world(bind_sessions=False)
    delivered_truth["ipc"].receive(
        channel_id=delivered_truth["channel_id"],
        receiver_agent_id="agent-child",
        receiver_process_id=delivered_truth["child_process_id"],
    )
    delivered = _recover_world(
        delivered_truth,
        resource_store=None,
        ipc_persistence=delivered_truth["ipc_persistence"],
    )
    assert delivered.ipc is not None
    assert delivered.ipc.get_message("message-1").delivery_state is (
        IPCMessageState.DELIVERED
    )


def test_integrated_recovery_is_idempotent_and_side_effect_free(tmp_path) -> None:
    world = _runtime_world(tmp_path)
    _append_dispatched_payment(world["parent_session"], agent_id="agent-parent")
    session_counts = {session.session_id: len(session.events) for session in _sessions(world)}
    ipc_records = len(world["ipc_persistence"].load())

    first = _recover_world(world)
    second = _recover_world(world)

    assert tuple(agent.agent_id for agent in first.agent_registry.list_agents()) == (
        tuple(agent.agent_id for agent in second.agent_registry.list_agents())
    )
    assert tuple(
        process.process_id for process in first.process_manager.list_processes()
    ) == tuple(process.process_id for process in second.process_manager.list_processes())
    assert first.durable_obligations == second.durable_obligations
    assert first.process_dispositions == second.process_dispositions
    assert first.resource_shares is not None
    assert second.resource_shares is not None
    assert first.resource_shares.shares_for_resource("res_secret") == (
        second.resource_shares.shares_for_resource("res_secret")
    )
    assert first.ipc is not None
    assert second.ipc is not None
    assert first.ipc.list_messages() == second.ipc.list_messages()
    assert {session.session_id: len(session.events) for session in _sessions(world)} == (
        session_counts
    )
    assert len(world["ipc_persistence"].load()) == ipc_records


def test_agent_registry_list_agents_is_public_creation_order_snapshot() -> None:
    world = _identity_world()
    snapshot = world["registry"].list_agents()

    assert tuple(agent.agent_id for agent in snapshot) == (
        "agent-parent",
        "agent-child",
    )
    assert isinstance(snapshot, tuple)


@pytest.mark.parametrize(
    ("sessions", "match"),
    [
        (
            (
                Session("session-process-only"),
            ),
            "Agent|Session|Process|agent",
        ),
        (
            (
                _runtime_facts_session(
                    session_id="session-a",
                    agent_id="agent-a",
                    parent_agent_id="agent-b",
                    process_id="process-a",
                ),
                _runtime_facts_session(
                    session_id="session-b",
                    agent_id="agent-b",
                    parent_agent_id="agent-a",
                    process_id="process-b",
                    creation_id="create-agent-b",
                    process_creation_id="create-process-b",
                ),
            ),
            "cycle|parent",
        ),
        (
            (
                _runtime_facts_session(
                    session_id="session-a",
                    agent_id="agent-a",
                    process_id="process-a",
                    parent_process_id="process-missing",
                ),
            ),
            "parent",
        ),
        (
            (
                _runtime_facts_session(
                    session_id="session-a",
                    agent_id="agent-a",
                    process_id="process-a",
                    parent_process_id="process-b",
                ),
                _runtime_facts_session(
                    session_id="session-b",
                    agent_id="agent-b",
                    process_id="process-b",
                    parent_process_id="process-a",
                    creation_id="create-agent-b",
                    process_creation_id="create-process-b",
                ),
            ),
            "cycle",
        ),
    ],
)
def test_corrupt_identity_and_process_facts_fail_closed(sessions, match) -> None:
    if len(sessions) == 1 and sessions[0].session_id == "session-process-only":
        sessions[0].append(
            EventType.PROCESS_CREATED,
            {
                "process_id": "process-a",
                "agent_id": "agent-a",
                "session_id": "session-process-only",
                "parent_process_id": None,
                "creation_id": "create-process-a",
            },
        )

    with pytest.raises(MultiAgentRecoveryCorruptionError, match=match):
        recover_multi_agent_runtime(sessions)


def test_duplicate_conflicting_agent_and_process_creation_fail_closed() -> None:
    first = Session("session-a")
    first.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-a",
            "parent_agent_id": None,
            "session_id": "session-a",
            "creation_id": "create-agent",
        },
    )
    second = Session("session-b")
    second.append(
        EventType.AGENT_CREATED,
        {
            "agent_id": "agent-a",
            "parent_agent_id": None,
            "session_id": "session-b",
            "creation_id": "create-agent",
        },
    )
    with pytest.raises(MultiAgentRecoveryCorruptionError, match="conflicting|multiple"):
        recover_multi_agent_runtime((first, second))

    process_conflict = _runtime_facts_session()
    process_conflict.append(
        EventType.PROCESS_CREATED,
        {
            "process_id": "process-a",
            "agent_id": "agent-a",
            "session_id": "session-agent",
            "parent_process_id": None,
            "creation_id": "create-process-conflict",
        },
    )
    with pytest.raises(MultiAgentRecoveryCorruptionError, match="multiple creation"):
        recover_multi_agent_runtime((process_conflict,))
