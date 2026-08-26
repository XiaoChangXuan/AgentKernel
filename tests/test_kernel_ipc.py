from __future__ import annotations

import pytest

from agentkernel import (
    AgentRegistry,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    CooperativeScheduler,
    EventType,
    IPCBackpressureError,
    IPCChannel,
    IPCChannelAlreadyExists,
    IPCCorruptionError,
    IPCMessageConflict,
    IPCMessageEnvelope,
    IPCMessageState,
    IPCParticipantError,
    IPCPayloadError,
    IPCRecordType,
    IPCStateTransitionError,
    InMemoryIPCPersistence,
    JsonlIPCPersistence,
    KernelIPC,
    LocalResourceStore,
    ProcessManager,
    ProcessState,
    RESOURCE_READ_ACTION,
    ResourceAccessDenied,
    ResourceOwner,
    ResourceService,
    Session,
    SessionCorruptionError,
    SessionStatus,
    TOOL_EXECUTE_ACTION,
    canonical_message_bytes,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def _kernel(
    *,
    persistence: InMemoryIPCPersistence | None = None,
    scheduler: CooperativeScheduler | None = None,
) -> tuple[
    AgentRegistry,
    Session,
    Session,
    ProcessManager,
    CooperativeScheduler,
    KernelIPC,
]:
    registry = AgentRegistry()
    sender_session = Session("session-a")
    receiver_session = Session("session-b")
    sender = registry.create_root(
        agent_id="agent-a",
        session=sender_session,
        creation_id="create-agent-a",
        record=False,
    )
    receiver = registry.create_root(
        agent_id="agent-b",
        session=receiver_session,
        creation_id="create-agent-b",
        record=False,
    )
    manager = scheduler.manager if scheduler is not None else ProcessManager(
        agent_registry=registry
    )
    scheduler = scheduler or CooperativeScheduler(manager)
    scheduler.create_process(process_id="process-a", agent=sender.control)
    scheduler.create_process(process_id="process-b", agent=receiver.control)
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        scheduler=scheduler,
        persistence=persistence or InMemoryIPCPersistence(),
        sessions={"agent-a": sender_session, "agent-b": receiver_session},
        channel_id_factory=lambda: "channel-ab",
        message_id_factory=lambda: "message-generated",
        time_fn=_Clock(),
    )
    return registry, sender_session, receiver_session, manager, scheduler, ipc


def _channel(ipc: KernelIPC, **overrides: object) -> IPCChannel:
    args = {
        "channel_id": "channel-ab",
        "sender_agent_id": "agent-a",
        "receiver_agent_id": "agent-b",
    }
    args.update(overrides)
    return ipc.create_channel(**args)  # type: ignore[arg-type]


def test_channel_creation_validates_kernel_participants() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()

    channel = _channel(ipc)

    assert channel.channel_id == "channel-ab"
    assert channel.sender_agent_id == "agent-a"
    assert channel.receiver_agent_id == "agent-b"

    with pytest.raises(IPCParticipantError):
        ipc.create_channel(
            channel_id="channel-missing",
            sender_agent_id="missing-agent",
            receiver_agent_id="agent-b",
        )

    with pytest.raises(IPCParticipantError, match="receiver Process"):
        ipc.create_channel(
            channel_id="channel-wrong-process",
            sender_agent_id="agent-a",
            receiver_agent_id="agent-b",
            receiver_process_id="process-a",
        )

    with pytest.raises(IPCChannelAlreadyExists, match="conflicting"):
        ipc.create_channel(
            channel_id="channel-ab",
            sender_agent_id="agent-a",
            receiver_agent_id="agent-b",
            max_messages=1,
        )


def test_sender_identity_is_derived_from_process_not_payload() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc)

    envelope = ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"sender_agent_id": "agent-root", "body": "hello"},
        message_id="message-forged",
    )

    assert envelope.sender_agent_id == "agent-a"
    assert envelope.sender_process_id == "process-a"
    assert envelope.payload == {"sender_agent_id": "agent-root", "body": "hello"}

    with pytest.raises(IPCParticipantError, match="sender Process"):
        ipc.send(
            channel_id="channel-ab",
            sender_process_id="process-b",
            payload={"body": "not allowed"},
            message_id="message-wrong-sender",
        )


def test_receiver_identity_is_validated_for_receive_and_ack() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc, receiver_process_id="process-b")
    sent = ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "hello"},
        message_id="message-1",
    )

    with pytest.raises(IPCParticipantError, match="receiver Agent"):
        ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-a")
    with pytest.raises(IPCParticipantError, match="receiver Process"):
        ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")

    delivered = ipc.receive(
        channel_id="channel-ab",
        receiver_agent_id="agent-b",
        receiver_process_id="process-b",
    )
    assert delivered is not None
    assert delivered.message_id == sent.message_id

    with pytest.raises(IPCParticipantError, match="receiver Agent"):
        ipc.ack(
            channel_id="channel-ab",
            message_id=sent.message_id,
            receiver_agent_id="agent-a",
        )


def test_payload_must_be_json_and_resource_refs_are_inert_data(tmp_path) -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc)
    resources = ResourceService(
        LocalResourceStore(tmp_path / "resources"),
        resource_id_factory=lambda: "res_secret",
        handle_id_factory=lambda: "hdl_secret",
    )
    handle = resources.create_artifact(
        b"secret",
        owner=ResourceOwner("agent-a", "session-a"),
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="producer",
        source_tool_call_id="call-producer",
        source_operation_id="op-producer",
    )

    with pytest.raises(IPCPayloadError):
        ipc.send(
            channel_id="channel-ab",
            sender_process_id="process-a",
            payload={"bad": object()},
            message_id="message-bad-payload",
        )

    sent = ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={
            "capability": "resource.read",
            "grant": {"action": RESOURCE_READ_ACTION, "scope": handle.uri},
        },
        resource_refs=[handle.uri],
        message_id="message-resource-ref",
    )
    delivered = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")

    assert delivered is not None
    assert delivered.resource_refs == (handle.uri,)
    assert sent.resource_refs == (handle.uri,)
    with pytest.raises(ResourceAccessDenied):
        resources.read(
            handle.uri,
            owner=ResourceOwner("agent-b", "session-b"),
            capability_evaluator=CapabilityEvaluator(()),
        )


def test_message_id_idempotency_and_conflict_detection() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc)

    first = ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "same"},
        message_id="message-stable",
        correlation_id="corr-1",
    )
    second = ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "same"},
        message_id="message-stable",
        correlation_id="corr-1",
    )

    assert first == second
    with pytest.raises(IPCMessageConflict):
        ipc.send(
            channel_id="channel-ab",
            sender_process_id="process-a",
            payload={"body": "different"},
            message_id="message-stable",
            correlation_id="corr-1",
        )


def test_delivery_lifecycle_redelivers_until_ack() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc)
    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "hello"},
        message_id="message-1",
    )

    first = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    second = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    assert first is not None
    assert second is not None
    assert first.message_id == second.message_id == "message-1"
    assert first.delivery_state is IPCMessageState.DELIVERED
    assert first.delivery_attempts == 1
    assert second.delivery_attempts == 2

    acked = ipc.ack(
        channel_id="channel-ab",
        message_id="message-1",
        receiver_agent_id="agent-b",
    )

    assert acked.delivery_state is IPCMessageState.ACKED
    assert ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b") is None


def test_per_channel_fifo_blocks_later_delivery_until_prior_ack() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc)
    for index in range(1, 4):
        ipc.send(
            channel_id="channel-ab",
            sender_process_id="process-a",
            payload={"index": index},
            message_id=f"message-{index}",
        )

    first = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    repeated_first = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    assert first is not None
    assert repeated_first is not None
    assert first.message_id == repeated_first.message_id == "message-1"

    ipc.ack(
        channel_id="channel-ab",
        message_id="message-1",
        receiver_agent_id="agent-b",
    )
    second = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    assert second is not None
    assert second.message_id == "message-2"
    ipc.ack(
        channel_id="channel-ab",
        message_id="message-2",
        receiver_agent_id="agent-b",
    )
    third = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")

    assert third is not None
    assert third.message_id == "message-3"
    assert [message.sequence for message in ipc.list_messages("channel-ab")] == [
        1,
        2,
        3,
    ]


def test_channel_bounds_and_canonical_byte_accounting() -> None:
    _registry, _sender_session, _receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc, max_messages=1)

    assert canonical_message_bytes({"b": 2, "a": 1}, ["artifact://res_a"]) == (
        canonical_message_bytes({"a": 1, "b": 2}, ["artifact://res_a"])
    )

    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "first"},
        message_id="message-1",
    )
    with pytest.raises(IPCBackpressureError, match="max_messages"):
        ipc.send(
            channel_id="channel-ab",
            sender_process_id="process-a",
            payload={"body": "second"},
            message_id="message-2",
        )

    _registry, _sender_session, _receiver_session, _manager, _scheduler, small = (
        _kernel()
    )
    _channel(small, max_bytes=canonical_message_bytes("x") - 1)
    with pytest.raises(IPCBackpressureError, match="max_bytes"):
        small.send(
            channel_id="channel-ab",
            sender_process_id="process-a",
            payload="x",
            message_id="message-too-large",
        )


def test_backpressure_blocks_running_sender_and_ack_unblocks() -> None:
    _registry, _sender_session, _receiver_session, manager, scheduler, ipc = _kernel()
    _channel(ipc, max_messages=1)
    scheduler.dispatch("process-a")
    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "first"},
        message_id="message-1",
    )

    with pytest.raises(IPCBackpressureError):
        ipc.send(
            channel_id="channel-ab",
            sender_process_id="process-a",
            payload={"body": "second"},
            message_id="message-2",
        )

    assert manager.get("process-a").state is ProcessState.BLOCKED
    assert manager.get("process-a").blocked_reason == (
        "ipc_backpressure:channel-ab:max_messages:1"
    )
    delivered = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    assert delivered is not None
    ipc.ack(
        channel_id="channel-ab",
        message_id=delivered.message_id,
        receiver_agent_id="agent-b",
    )

    assert manager.get("process-a").state is ProcessState.READY
    assert "process-a" in scheduler.ready_queue


def test_session_audit_facts_are_local_and_not_payload_transcripts() -> None:
    _registry, sender_session, receiver_session, _manager, _scheduler, ipc = _kernel()
    _channel(ipc)

    sent = ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"large": "payload"},
        message_id="message-1",
    )
    delivered = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    assert delivered is not None
    ipc.ack(
        channel_id="channel-ab",
        message_id=sent.message_id,
        receiver_agent_id="agent-b",
    )

    assert [event.type for event in sender_session.events] == [EventType.IPC_SEND]
    assert [event.type for event in receiver_session.events] == [
        EventType.IPC_RECEIVE,
        EventType.IPC_ACK,
    ]
    assert "payload" not in sender_session.events[0].data
    assert "payload" not in receiver_session.events[0].data
    assert sender_session.events[0].data["message_id"] == sent.message_id
    assert receiver_session.events[0].data["delivery_attempts"] == 1
    assert sender_session.recovery_analysis.status is SessionStatus.COMPLETED
    assert receiver_session.recovery_analysis.status is SessionStatus.COMPLETED


def test_reconstruct_after_send_delivery_and_ack_preserves_envelope_state() -> None:
    persistence = InMemoryIPCPersistence()
    registry, _sender_session, _receiver_session, manager, _scheduler, ipc = _kernel(
        persistence=persistence
    )
    _channel(ipc)
    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"body": "pending"},
        message_id="message-pending",
    )

    reconstructed = KernelIPC.reconstruct(
        agent_registry=registry,
        process_manager=manager,
        persistence=persistence,
    )

    assert reconstructed.get_message("message-pending").delivery_state is (
        IPCMessageState.PENDING
    )
    delivered = reconstructed.receive(
        channel_id="channel-ab",
        receiver_agent_id="agent-b",
    )
    assert delivered is not None
    assert delivered.delivery_attempts == 1

    after_delivery = KernelIPC.reconstruct(
        agent_registry=registry,
        process_manager=manager,
        persistence=persistence,
    )
    redelivered = after_delivery.receive(
        channel_id="channel-ab",
        receiver_agent_id="agent-b",
    )
    assert redelivered is not None
    assert redelivered.delivery_attempts == 2

    after_delivery.ack(
        channel_id="channel-ab",
        message_id="message-pending",
        receiver_agent_id="agent-b",
    )
    after_ack = KernelIPC.reconstruct(
        agent_registry=registry,
        process_manager=manager,
        persistence=persistence,
    )

    assert after_ack.get_message("message-pending").delivery_state is (
        IPCMessageState.ACKED
    )
    assert after_ack.receive(channel_id="channel-ab", receiver_agent_id="agent-b") is None
    assert after_ack.live_occupancy("channel-ab") == {"messages": 0, "bytes": 0}


def test_jsonl_persistence_reconstructs_queue_and_next_sequence(tmp_path) -> None:
    path = tmp_path / "ipc.jsonl"
    persistence = JsonlIPCPersistence(path)
    registry, _sender_session, _receiver_session, manager, _scheduler, ipc = _kernel(
        persistence=persistence
    )
    _channel(ipc)
    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"index": 1},
        message_id="message-1",
    )
    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"index": 2},
        message_id="message-2",
    )
    delivered = ipc.receive(channel_id="channel-ab", receiver_agent_id="agent-b")
    assert delivered is not None
    ipc.ack(
        channel_id="channel-ab",
        message_id=delivered.message_id,
        receiver_agent_id="agent-b",
    )
    persistence.close()

    restored = KernelIPC.reconstruct(
        agent_registry=registry,
        process_manager=manager,
        persistence=JsonlIPCPersistence(path),
    )
    next_message = restored.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"index": 3},
        message_id="message-3",
    )

    assert restored.get_message("message-1").delivery_state is IPCMessageState.ACKED
    assert restored.get_message("message-2").delivery_state is IPCMessageState.PENDING
    assert next_message.sequence == 3


def test_corrupt_transition_is_rejected_during_replay() -> None:
    registry, _sender_session, _receiver_session, manager, _scheduler, _ipc = _kernel()
    persistence = InMemoryIPCPersistence()
    channel = IPCChannel(
        channel_id="channel-ab",
        sender_agent_id="agent-a",
        receiver_agent_id="agent-b",
        created_at=1.0,
    )
    envelope = IPCMessageEnvelope(
        message_id="message-1",
        channel_id="channel-ab",
        sender_agent_id="agent-a",
        sender_process_id="process-a",
        receiver_agent_id="agent-b",
        receiver_process_id=None,
        payload={"body": "hello"},
        resource_refs=(),
        sequence=1,
        correlation_id="message-1",
        created_at=2.0,
    )
    persistence.append(IPCRecordType.CHANNEL_CREATED, channel.as_dict())
    persistence.append(IPCRecordType.MESSAGE_SENT, envelope.as_dict())
    persistence.append(
        IPCRecordType.MESSAGE_ACKED,
        {"message_id": "message-1", "channel_id": "channel-ab", "acked_at": 3.0},
    )

    with pytest.raises(IPCCorruptionError, match="requires DELIVERED"):
        KernelIPC.reconstruct(
            agent_registry=registry,
            process_manager=manager,
            persistence=persistence,
        )


def test_ipc_payload_does_not_change_capability_or_process_tree_authority() -> None:
    registry = AgentRegistry()
    parent_session = Session("session-parent")
    child_session = Session("session-child")
    parent = registry.create_root(
        agent_id="agent-parent",
        session=parent_session,
        capability_grants=(
            CapabilityGrant("agent-parent", TOOL_EXECUTE_ACTION, "tool://math.add"),
        ),
        creation_id="create-parent",
        record=False,
    )
    child = registry.create_child(
        parent_agent_id=parent.control.agent_id,
        agent_id="agent-child",
        session=child_session,
        creation_id="create-child",
        record=False,
    )
    manager = ProcessManager(agent_registry=registry)
    manager.create_process(process_id="process-parent", agent=parent.control)
    child_process = manager.create_child_process(
        parent_process_id="process-parent",
        process_id="process-child",
        agent=child.control,
    )
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        sessions={"agent-parent": parent_session, "agent-child": child_session},
        persistence=InMemoryIPCPersistence(),
    )
    ipc.create_channel(
        channel_id="channel-parent-child",
        sender_agent_id="agent-parent",
        receiver_agent_id="agent-child",
    )

    ipc.send(
        channel_id="channel-parent-child",
        sender_process_id="process-parent",
        payload={
            "grant": {
                "subject": "agent-child",
                "action": TOOL_EXECUTE_ACTION,
                "resource_scope": "tool://math.add",
            }
        },
        message_id="message-grant-like-data",
    )
    delivered = ipc.receive(
        channel_id="channel-parent-child",
        receiver_agent_id="agent-child",
    )
    decision = CapabilityEvaluator(
        registry.get("agent-child").capability_grants
    ).authorize(
        AuthorizationRequest(
            "agent-child",
            TOOL_EXECUTE_ACTION,
            "tool://math.add",
        )
    )

    assert delivered is not None
    assert delivered.payload["grant"]["subject"] == "agent-child"  # type: ignore[index]
    assert decision.allowed is False
    assert registry.parent_of("agent-child") == "agent-parent"
    assert manager.parent_of("process-child") == "process-parent"
    assert child_process.capability_snapshot.agent_id == "agent-child"


def test_ipc_audit_shape_is_fail_closed() -> None:
    session = Session("session-ipc")
    session.append(
        EventType.IPC_SEND,
        {
            "message_id": "message-1",
            "channel_id": "channel-1",
            "sender_agent_id": "agent-a",
            "sender_process_id": "process-a",
            "receiver_agent_id": "agent-b",
            "receiver_process_id": None,
            "sequence": 1,
            "correlation_id": "message-1",
        },
    )
    assert session.recovery_analysis.status is SessionStatus.COMPLETED

    invalid = Session("session-invalid-ipc")
    invalid.append(
        EventType.IPC_ACK,
        {
            "message_id": "message-1",
            "channel_id": "channel-1",
            "sender_agent_id": "agent-a",
            "sender_process_id": "process-a",
            "receiver_agent_id": "agent-b",
            "receiver_process_id": None,
            "sequence": 1,
            "correlation_id": "message-1",
            "delivery_attempts": 1,
        },
    )
    with pytest.raises(SessionCorruptionError, match="unexpected fields"):
        invalid.recovery_analysis
