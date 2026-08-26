from __future__ import annotations

import pytest

from agentkernel import (
    AgentRegistry,
    EventType,
    IPCMessageState,
    InMemoryIPCPersistence,
    KernelIPC,
    LocalResourceStore,
    MultiAgentRecoveryCorruptionError,
    OperationRecoveryClassification,
    ProcessManager,
    ProcessRecoveryDisposition,
    ProcessState,
    RESOURCE_READ_ACTION,
    ResourceOwner,
    ResourceService,
    ResourceShareRegistry,
    Session,
    ToolCall,
    ToolEffectKind,
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
        "parent_session": parent_session,
        "child_session": child_session,
        "parent_process_id": parent_process.process_id,
        "child_process_id": child_process.process_id,
        "handle": handle,
        "store": store,
        "ipc_persistence": ipc_persistence,
        "message_id": message.message_id,
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


def _append_dispatched_payment(session: Session, *, agent_id: str) -> None:
    call = _payment_call()
    authorization = _authorization_context(agent_id)
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
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "op-payment",
            "boundary": "prepare",
            **authorization,
        },
    )
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "op-payment",
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            "authorization": authorization,
        },
    )
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "op-payment",
            "boundary": "dispatch",
            **authorization,
        },
    )
    session.append(
        EventType.TOOL_DISPATCH,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "op-payment",
            "attempt": 1,
            "authorization": authorization,
        },
    )


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
