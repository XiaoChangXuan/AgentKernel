from __future__ import annotations

import pytest

from agentkernel import (
    AgentRegistry,
    AuthorizationRequest,
    CapabilityDelegator,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    EventType,
    InMemoryIPCPersistence,
    InMemoryMemoryStore,
    KernelIPC,
    LocalResourceStore,
    MEMORY_ADMIT_ACTION,
    MEMORY_FORGET_ACTION,
    MEMORY_PROPOSE_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    ProcessControlBlock,
    ProcessManager,
    RESOURCE_READ_ACTION,
    ResourceOwner,
    ResourceService,
    ResourceShareGrant,
    ResourceAccessDenied,
    Session,
    MemoryAccessDenied,
    MemoryProvenance,
    MemoryService,
    memory_namespace_scope,
    project_memories_to_context_pages,
)


AGENT_A = "agent-a"
AGENT_B = "agent-b"
AGENT_C = "agent-c"
PUBLIC = "public"
PRIVATE = "private"


def _service(store=None) -> MemoryService:
    memory_ids = iter(f"mem_{index:04d}" for index in range(2000))
    event_ids = iter(f"mev_{index:04d}" for index in range(5000))
    proposal_ids = iter(f"mpr_{index:04d}" for index in range(2000))
    decision_ids = iter(f"mad_{index:04d}" for index in range(2000))
    ticks = iter(float(index) for index in range(10000))
    return MemoryService(
        store or InMemoryMemoryStore(),
        memory_id_factory=lambda: next(memory_ids),
        event_id_factory=lambda: next(event_ids),
        proposal_id_factory=lambda: next(proposal_ids),
        decision_id_factory=lambda: next(decision_ids),
        clock=lambda: next(ticks),
    )


def _grants(agent_id: str, *items: tuple[str, str]) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, scope) for action, scope in items
    )


def _namespace_grant(agent_id: str, action: str, namespace: str) -> tuple[str, str]:
    return (action, memory_namespace_scope(AGENT_A, namespace))


def _memory_grant(agent_id: str, action: str, uri: str) -> CapabilityEvaluator:
    return CapabilityEvaluator((CapabilityGrant(agent_id, action, uri),))


def _provenance(source: str = "host", source_class: str = "HOST_VERIFIED") -> MemoryProvenance:
    return MemoryProvenance(
        source=source,
        source_class=source_class,  # type: ignore[arg-type]
        source_session_id="session-a",
        source_event_id="event-1",
        source_agent_id=AGENT_A,
    )


def _write(memory: MemoryService, namespace: str, content: str):
    return memory.remember(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=namespace,
        content=content,
        provenance=_provenance(),
        capability_evaluator=_grants(
            AGENT_A,
            _namespace_grant(AGENT_A, MEMORY_WRITE_ACTION, namespace),
        ),
    )


def _proposal(memory: MemoryService, namespace: str, content: str = "Candidate memory."):
    return memory.propose(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=namespace,
        content=content,
        provenance=_provenance("user", "USER_EXPLICIT"),
        capability_evaluator=_grants(
            AGENT_A,
            _namespace_grant(AGENT_A, MEMORY_PROPOSE_ACTION, namespace),
        ),
    )


def _admit(memory: MemoryService, proposal_id: str, namespace: str):
    return memory.admit(
        proposal_id,
        agent_id=AGENT_A,
        reason="host admitted",
        evidence_provenance=_provenance(),
        capability_evaluator=_grants(
            AGENT_A,
            _namespace_grant(AGENT_A, MEMORY_ADMIT_ACTION, namespace),
        ),
    )


def test_d1_memory_uri_is_not_authority() -> None:
    memory = _service()
    record = _write(memory, PRIVATE, "Secret launch plan.")

    with pytest.raises(MemoryAccessDenied):
        memory.read(
            record.uri,
            agent_id=AGENT_B,
            capability_evaluator=CapabilityEvaluator(()),
        )


def test_d2_delegated_read_capability_allows_read() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Shared roadmap summary.")
    parent_grant = CapabilityGrant(AGENT_A, MEMORY_READ_ACTION, record.uri)
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            parent_agent_id=AGENT_A,
            child_agent_id=AGENT_B,
            action=MEMORY_READ_ACTION,
            resource_scope=record.uri,
        ),
        parent_grants=(parent_grant,),
    )

    assert decision.allowed is True
    assert decision.delegated_grant is not None
    restored = memory.read(
        record.uri,
        agent_id=AGENT_B,
        capability_evaluator=CapabilityEvaluator((decision.delegated_grant,)),
    )

    assert restored.content == "Shared roadmap summary."


def test_d3_read_capability_does_not_allow_mutation() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Read-only memory.")
    read_only = _memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri)

    assert memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=read_only)
    with pytest.raises(MemoryAccessDenied):
        memory.mark_stale(
            record.uri,
            agent_id=AGENT_B,
            reason="read-only actor tried to mutate lifecycle",
            evidence_provenance=_provenance(),
            capability_evaluator=read_only,
        )
    with pytest.raises(MemoryAccessDenied):
        memory.forget(record.uri, agent_id=AGENT_B, capability_evaluator=read_only)
    with pytest.raises(MemoryAccessDenied):
        memory.supersede(
            agent_id=AGENT_B,
            old_memory_id=record.memory_id,
            content="Mutated memory.",
            provenance=_provenance(),
            capability_evaluator=read_only,
        )
    with pytest.raises(MemoryAccessDenied):
        memory.admit(
            _proposal(memory, PUBLIC, "Read-only actor cannot admit.").proposal_id,
            agent_id=AGENT_B,
            reason="read-only actor tried to admit",
            evidence_provenance=_provenance(),
            capability_evaluator=read_only,
        )


def test_d4_delegated_mutation_records_actor_without_changing_owner() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Mutable by delegated actor.")
    parent_grant = CapabilityGrant(AGENT_A, MEMORY_WRITE_ACTION, record.uri)
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            parent_agent_id=AGENT_A,
            child_agent_id=AGENT_B,
            action=MEMORY_WRITE_ACTION,
            resource_scope=record.uri,
        ),
        parent_grants=(parent_grant,),
    )

    assert decision.allowed is True
    assert decision.delegated_grant is not None
    stale = memory.mark_stale(
        record.uri,
        agent_id=AGENT_B,
        reason="delegated actor supplied fresher evidence",
        evidence_provenance=_provenance(),
        capability_evaluator=CapabilityEvaluator((decision.delegated_grant,)),
    )
    event = memory.durable_events()[-1]

    assert stale.lifecycle_state == "STALE"
    assert stale.owner_agent_id == AGENT_A
    assert event.agent_id == AGENT_B
    assert event.owner_agent_id == AGENT_A


def test_d5_propose_and_admit_authority_are_separate() -> None:
    memory = _service()
    propose_only = _grants(AGENT_B, _namespace_grant(AGENT_B, MEMORY_PROPOSE_ACTION, PUBLIC))
    proposal = memory.propose(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=PUBLIC,
        content="Shared memory proposal requires separate admission.",
        provenance=_provenance("tool", "TOOL_DERIVED"),
        capability_evaluator=propose_only,
    )

    with pytest.raises(MemoryAccessDenied):
        memory.admit(
            proposal.proposal_id,
            agent_id=AGENT_B,
            reason="model attempted self-admission",
            evidence_provenance=_provenance(),
            capability_evaluator=propose_only,
        )
    admitted = _admit(memory, proposal.proposal_id, PUBLIC)

    assert admitted.owner_agent_id == AGENT_A


def test_d6_revocation_is_current_evaluator_removal() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Revocable fact.")
    delegated = _memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri)

    assert memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=delegated)
    with pytest.raises(MemoryAccessDenied):
        memory.read(
            record.uri,
            agent_id=AGENT_B,
            capability_evaluator=CapabilityEvaluator(()),
        )


def test_d7_search_excludes_revoked_memory() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Fresh search visible before revoke.")
    other_scope = _grants(
        AGENT_B,
        _namespace_grant(AGENT_B, MEMORY_READ_ACTION, PRIVATE),
    )

    assert memory.search(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        query="Fresh",
        limit=5,
        capability_evaluator=_memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri),
    )
    revoked_results = memory.search(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        query="search",
        limit=5,
        capability_evaluator=other_scope,
    )

    assert revoked_results == ()


def test_d8_context_projection_excludes_revoked_memory() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Fresh context visible before revoke.")
    visible = memory.search(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        query="context",
        limit=5,
        capability_evaluator=_memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri),
    )
    revoked_results = memory.search(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        query="context",
        limit=5,
        capability_evaluator=_grants(
            AGENT_B,
            _namespace_grant(AGENT_B, MEMORY_READ_ACTION, PRIVATE),
        ),
    )
    projection = project_memories_to_context_pages(revoked_results, top_k=5)

    assert len(visible) == 1
    assert revoked_results == ()
    assert projection.pages == ()


def test_d9_historical_session_observations_remain_after_revocation() -> None:
    memory = _service()
    session = Session("session-b")
    record = _write(memory, PUBLIC, "Observed before revoke.")
    session.append(
        EventType.TOOL_RESULT,
        {
            "name": "memory.read",
            "memory_uri": record.uri,
            "content": record.content,
        },
    )

    with pytest.raises(MemoryAccessDenied):
        memory.read(
            record.uri,
            agent_id=AGENT_B,
            capability_evaluator=CapabilityEvaluator(()),
        )

    assert session.events[0].data["content"] == "Observed before revoke."


def test_d10_ipc_payload_with_memory_uri_grants_nothing() -> None:
    memory = _service()
    record = _write(memory, PRIVATE, "IPC should not grant this.")
    registry = AgentRegistry()
    sender_session = Session("session-a")
    receiver_session = Session("session-b")
    sender = registry.create_root(agent_id=AGENT_A, session=sender_session, record=False)
    receiver = registry.create_root(agent_id=AGENT_B, session=receiver_session, record=False)
    manager = ProcessManager(agent_registry=registry)
    manager.create_process(process_id="process-a", agent=sender.control)
    manager.create_process(process_id="process-b", agent=receiver.control)
    ipc = KernelIPC(
        agent_registry=registry,
        process_manager=manager,
        sessions={AGENT_A: sender_session, AGENT_B: receiver_session},
        persistence=InMemoryIPCPersistence(),
    )
    ipc.create_channel(
        channel_id="channel-ab",
        sender_agent_id=AGENT_A,
        receiver_agent_id=AGENT_B,
    )

    ipc.send(
        channel_id="channel-ab",
        sender_process_id="process-a",
        payload={"memory_uri": record.uri},
    )
    delivered = ipc.receive(channel_id="channel-ab", receiver_agent_id=AGENT_B)

    assert delivered is not None
    assert delivered.payload == {"memory_uri": record.uri}
    with pytest.raises(MemoryAccessDenied):
        memory.read(
            record.uri,
            agent_id=AGENT_B,
            capability_evaluator=CapabilityEvaluator(()),
        )


def test_d11_list_history_and_resource_share_are_not_authority(tmp_path) -> None:
    memory = _service()
    public_record = _write(memory, PUBLIC, "Public fact.")
    private_record = _write(memory, PRIVATE, "Private fact.")
    public_proposal = _proposal(memory, PUBLIC, "Public proposal.")
    private_proposal = _proposal(memory, PRIVATE, "Private proposal.")
    admitted_public = _admit(memory, public_proposal.proposal_id, PUBLIC)
    _admit(memory, private_proposal.proposal_id, PRIVATE)
    public_read = _grants(
        AGENT_B,
        _namespace_grant(AGENT_B, MEMORY_READ_ACTION, PUBLIC),
    )

    listed = memory.list(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        capability_evaluator=public_read,
    )
    proposals = memory.list_proposals(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        capability_evaluator=public_read,
    )
    decisions = memory.admission_history(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        capability_evaluator=public_read,
    )

    assert [item.memory_id for item in listed] == [
        public_record.memory_id,
        admitted_public.memory_id,
    ]
    assert private_record.memory_id not in [item.memory_id for item in listed]
    assert [item.proposal_id for item in proposals] == [public_proposal.proposal_id]
    assert [item.proposal_id for item in decisions] == [public_proposal.proposal_id]
    owner = ResourceOwner(AGENT_A, "session-a")
    grantee = ResourceOwner(AGENT_B, "session-b")
    resource_service = ResourceService(
        LocalResourceStore(tmp_path / "resources"),
        resource_id_factory=lambda: "res_0001",
        handle_id_factory=lambda: "hdl_0001",
    )
    handle = resource_service.create_artifact(
        b"bytes",
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="producer",
        source_tool_call_id="call-1",
        source_operation_id="op-1",
    )
    resource_payload = ResourceShareGrant(
        share_id="share_0001",
        resource_id="res_0001",
        owner_agent_id=AGENT_A,
        grantee_agent_id=AGENT_B,
        allowed_actions=(RESOURCE_READ_ACTION,),
        created_at=1.0,
        correlation_id="corr-1",
    ).as_payload()

    with pytest.raises(ResourceAccessDenied):
        resource_service.read(
            handle.uri,
            owner=grantee,
            capability_evaluator=public_read,
        )
    assert resource_payload["resource_id"] == "res_0001"


def test_d12_agent_identity_not_process_identity_is_authority_subject() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Agent-owned memory.")
    registry = AgentRegistry()
    agent = registry.create_root(
        agent_id=AGENT_B,
        session=Session("session-b"),
        record=False,
    )
    process = ProcessControlBlock.create(process_id=AGENT_A, agent=agent.control)
    process_grant = CapabilityEvaluator(
        (CapabilityGrant(process.process_id, MEMORY_READ_ACTION, record.uri),)
    )

    assert process.capability_snapshot.agent_id == AGENT_B
    with pytest.raises(MemoryAccessDenied):
        memory.read(record.uri, agent_id=AGENT_B, capability_evaluator=process_grant)


def test_d13_memory_scope_isolation() -> None:
    memory = _service()
    _write(memory, PUBLIC, "Public only.")
    private = _write(memory, PRIVATE, "Private only.")
    public_read = _grants(
        AGENT_B,
        _namespace_grant(AGENT_B, MEMORY_READ_ACTION, PUBLIC),
    )

    with pytest.raises(MemoryAccessDenied):
        memory.read(private.uri, agent_id=AGENT_B, capability_evaluator=public_read)
    assert memory.search(
        agent_id=AGENT_B,
        owner_agent_id=AGENT_A,
        namespace=None,
        query="only",
        limit=5,
        capability_evaluator=public_read,
    )[0].namespace == PUBLIC


def test_d14_delegation_attenuation_rejects_broader_scope() -> None:
    narrow = CapabilityGrant(
        AGENT_A,
        MEMORY_READ_ACTION,
        memory_namespace_scope(AGENT_A, PUBLIC),
    )
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            parent_agent_id=AGENT_A,
            child_agent_id=AGENT_B,
            action=MEMORY_READ_ACTION,
            resource_scope=f"memory://{AGENT_A}/**",
        ),
        parent_grants=(narrow,),
    )

    assert decision.allowed is False
    assert decision.reason == "parent_authority_not_found"


def test_d15_trust_boundary_is_orthogonal_to_authority() -> None:
    memory = _service()
    proposal = memory.propose(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=PUBLIC,
        content="Tool-derived claim should be reviewed.",
        provenance=_provenance("read_file", "TOOL_DERIVED"),
        capability_evaluator=_grants(
            AGENT_A,
            _namespace_grant(AGENT_A, MEMORY_PROPOSE_ACTION, PUBLIC),
        ),
    )
    read = _grants(AGENT_B, _namespace_grant(AGENT_B, MEMORY_READ_ACTION, PUBLIC))

    assert memory.read_proposal(
        proposal.proposal_id,
        agent_id=AGENT_B,
        capability_evaluator=read,
    ).has_untrusted_origin is True
    with pytest.raises(MemoryAccessDenied):
        memory.read_proposal(
            proposal.proposal_id,
            agent_id=AGENT_C,
            capability_evaluator=CapabilityEvaluator(()),
        )


def test_d16_lifecycle_state_is_orthogonal_to_authority() -> None:
    memory = _service()
    record = _write(memory, PUBLIC, "Lifecycle gated by authority.")
    stale = memory.mark_stale(
        record.uri,
        agent_id=AGENT_A,
        reason="fresh evidence superseded it",
        evidence_provenance=_provenance(),
        capability_evaluator=_grants(
            AGENT_A,
            _namespace_grant(AGENT_A, MEMORY_WRITE_ACTION, PUBLIC),
        ),
    )

    assert stale.lifecycle_state == "STALE"
    assert memory.read(
        record.uri,
        agent_id=AGENT_B,
        include_inactive=True,
        capability_evaluator=_memory_grant(AGENT_B, MEMORY_READ_ACTION, record.uri),
    ).lifecycle_state == "STALE"
    with pytest.raises(MemoryAccessDenied):
        memory.read(
            record.uri,
            agent_id=AGENT_C,
            include_inactive=True,
            capability_evaluator=CapabilityEvaluator(()),
        )
