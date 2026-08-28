from __future__ import annotations

import pytest

from agentkernel import (
    CapabilityDelegator,
    CapabilityEvaluator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    MEMORY_FORGET_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    MemoryAccessDenied,
    MemoryNotFound,
    MemoryProvenance,
    MemoryService,
    memory_namespace_scope,
    project_memories_to_context_pages,
)


AGENT_A = "agent-a"
AGENT_B = "agent-b"
NAMESPACE = "preferences"


def grants(agent_id: str, *actions: str) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, memory_namespace_scope(AGENT_A, NAMESPACE))
        for action in actions
    )


def service(store=None) -> MemoryService:
    ids = iter(f"mem_{index:04d}" for index in range(2000))
    event_ids = iter(f"mev_{index:04d}" for index in range(4000))
    ticks = iter(float(index) for index in range(4000))
    return MemoryService(
        store or InMemoryMemoryStore(),
        memory_id_factory=lambda: next(ids),
        event_id_factory=lambda: next(event_ids),
        clock=lambda: next(ticks),
    )


def provenance(
    *,
    source_session_id: str | None = None,
    source_event_id: str | None = None,
) -> MemoryProvenance:
    return MemoryProvenance(
        source="session" if source_session_id else "host",
        source_session_id=source_session_id,
        source_event_id=source_event_id,
        source_agent_id=AGENT_A,
    )


def test_cross_session_persistence_survives_fresh_runtime(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    first = service(JsonlMemoryStore(path))
    written = first.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Preferred language is Chinese.",
        provenance=provenance(source_session_id="session-a", source_event_id="2"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()

    restarted = service(JsonlMemoryStore(path))
    results = restarted.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="language",
        limit=5,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert [record.memory_id for record in results] == [written.memory_id]
    assert results[0].content == "Preferred language is Chinese."


def test_provenance_is_preserved_after_restart(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    first = service(JsonlMemoryStore(path))
    record = first.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="User prefers concise answers.",
        provenance=provenance(source_session_id="session-1", source_event_id="7"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()

    restarted = service(JsonlMemoryStore(path))
    restored = restarted.read(
        record.uri,
        agent_id=AGENT_A,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert restored.provenance.source == "session"
    assert restored.provenance.source_session_id == "session-1"
    assert restored.provenance.source_event_id == "7"


def test_supersede_keeps_old_record_durable_but_active_search_returns_new() -> None:
    memory = service()
    old = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Project uses Python 3.11.",
        provenance=provenance(),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    new = memory.supersede(
        agent_id=AGENT_A,
        old_memory_id=old.memory_id,
        content="Project uses Python 3.12.",
        provenance=provenance(),
        capability_evaluator=grants(
            AGENT_A,
            MEMORY_WRITE_ACTION,
            MEMORY_FORGET_ACTION,
        ),
    )

    active = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=5,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )
    old_projection = memory.read(
        old.memory_id,
        agent_id=AGENT_A,
        include_inactive=True,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert [record.memory_id for record in active] == [new.memory_id]
    assert old_projection.active is False
    assert old_projection.superseded_by_memory_id == new.memory_id
    assert old_projection.content == "Project uses Python 3.11."


def test_forget_is_semantic_inactive_after_restart(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    first = service(JsonlMemoryStore(path))
    record = first.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Temporary preference.",
        provenance=provenance(),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.forget(
        record.uri,
        agent_id=AGENT_A,
        capability_evaluator=grants(AGENT_A, MEMORY_FORGET_ACTION),
    )
    first.close()

    restarted = service(JsonlMemoryStore(path))
    assert restarted.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Temporary",
        limit=5,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    ) == ()
    inactive = restarted.read(
        record.uri,
        agent_id=AGENT_A,
        include_inactive=True,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert inactive.active is False
    assert inactive.forgotten_at is not None
    assert len(restarted.durable_events()) == 2


def test_capability_isolation_denies_known_memory_id_without_authority() -> None:
    memory = service()
    record = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Secret preference.",
        provenance=provenance(),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )

    with pytest.raises(MemoryAccessDenied):
        memory.read(
            record.memory_id,
            agent_id=AGENT_B,
            capability_evaluator=CapabilityEvaluator(()),
        )


def test_delegated_memory_read_uses_existing_capability_delegation() -> None:
    memory = service()
    parent_grant = CapabilityGrant(
        AGENT_A,
        MEMORY_READ_ACTION,
        memory_namespace_scope(AGENT_A, NAMESPACE),
    )
    record = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Delegated visible preference.",
        provenance=provenance(),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    decision = CapabilityDelegator().delegate(
        DelegateCapabilityRequest(
            parent_agent_id=AGENT_A,
            child_agent_id=AGENT_B,
            action=MEMORY_READ_ACTION,
            resource_scope=memory_namespace_scope(AGENT_A, NAMESPACE),
        ),
        parent_grants=(parent_grant,),
    )

    assert decision.allowed is True
    assert decision.delegated_grant is not None
    delegated = memory.read(
        record.uri,
        agent_id=AGENT_B,
        capability_evaluator=CapabilityEvaluator((decision.delegated_grant,)),
    )

    assert delegated.memory_id == record.memory_id


def test_context_projection_is_bounded_with_many_memories() -> None:
    memory = service()
    write = grants(AGENT_A, MEMORY_WRITE_ACTION)
    for index in range(1000):
        memory.remember(
            agent_id=AGENT_A,
            namespace=NAMESPACE,
            content=f"Memory number {index} about Python preference.",
            provenance=provenance(),
            capability_evaluator=write,
        )

    results = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python preference",
        limit=5,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )
    projection = project_memories_to_context_pages(
        results,
        top_k=5,
        total_memory_records=1000,
    )

    assert len(memory.list(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )) == 1000
    assert projection.total_memory_records == 1000
    assert projection.selected_count == 5
    assert len(projection.pages) == 5
    assert all("Long-term memory" in page.content for page in projection.pages)


def test_index_rebuild_preserves_search_from_durable_facts() -> None:
    memory = service()
    record = memory.remember(
        agent_id=AGENT_A,
        namespace=NAMESPACE,
        content="Index rebuild should find lexical content.",
        provenance=provenance(),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    memory.rebuild_index()
    memory.drop_index()
    memory.rebuild_index()

    results = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="lexical",
        limit=5,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert [item.memory_id for item in results] == [record.memory_id]
