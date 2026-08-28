from __future__ import annotations

import pytest

from agentkernel import (
    CapabilityEvaluator,
    CapabilityGrant,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    MEMORY_FORGET_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    MemoryAccessDenied,
    MemoryCorruptionError,
    MemoryInvalid,
    MemoryProvenance,
    MemoryService,
    memory_namespace_scope,
    project_conflicting_memories_to_context_pages,
    project_memories_to_context_pages,
)


AGENT_A = "agent-a"
AGENT_B = "agent-b"
NAMESPACE = "project"
OTHER_NAMESPACE = "other-project"


def service(store=None) -> MemoryService:
    ids = iter(f"mem_{index:04d}" for index in range(5000))
    event_ids = iter(f"mev_{index:04d}" for index in range(10000))
    ticks = iter(float(index) for index in range(10000))
    return MemoryService(
        store or InMemoryMemoryStore(),
        memory_id_factory=lambda: next(ids),
        event_id_factory=lambda: next(event_ids),
        clock=lambda: next(ticks),
    )


def grants(agent_id: str, *actions: str, namespace: str = NAMESPACE) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, memory_namespace_scope(AGENT_A, namespace))
        for action in actions
    )


def all_namespace_grants(agent_id: str, *actions: str) -> CapabilityEvaluator:
    return CapabilityEvaluator(
        CapabilityGrant(agent_id, action, f"memory://{AGENT_A}/**")
        for action in actions
    )


def provenance(
    source: str = "host",
    *,
    source_session_id: str | None = None,
    source_event_id: str | None = None,
    source_tool_name: str | None = None,
    note: str | None = None,
) -> MemoryProvenance:
    return MemoryProvenance(
        source=source,
        source_session_id=source_session_id,
        source_event_id=source_event_id,
        source_agent_id=AGENT_A,
        source_tool_name=source_tool_name,
        note=note,
    )


def remember(
    memory: MemoryService,
    content: str,
    *,
    namespace: str = NAMESPACE,
    capability_evaluator: CapabilityEvaluator | None = None,
) -> object:
    return memory.remember(
        agent_id=AGENT_A,
        namespace=namespace,
        content=content,
        provenance=provenance(),
        capability_evaluator=capability_evaluator
        or grants(AGENT_A, MEMORY_WRITE_ACTION, namespace=namespace),
    )


def test_c1_stale_memory_hidden_by_default_visible_in_history_after_restart(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    first = service(JsonlMemoryStore(path))
    record = remember(first, "Project requires Python 3.11.")
    stale = first.mark_stale(
        record.memory_id,
        agent_id=AGENT_A,
        reason="pyproject.toml now requires Python >=3.12",
        evidence_provenance=provenance(
            "tool",
            source_session_id="session-b",
            source_event_id="read-pyproject",
            source_tool_name="read_file",
        ),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    first.close()

    restarted = service(JsonlMemoryStore(path))
    active = restarted.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=5,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )
    explicit_stale = restarted.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=5,
        include_stale=True,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )
    history = restarted.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert active == ()
    assert [item.memory_id for item in explicit_stale] == [record.memory_id]
    assert stale.lifecycle_state == "STALE"
    assert [item.memory_id for item in history] == [record.memory_id]
    assert history[0].lifecycle_state == "STALE"
    assert history[0].stale_provenance is not None
    assert history[0].stale_provenance.source_tool_name == "read_file"


def test_c2_supersede_chain_returns_only_latest_active_and_history_explains_chain() -> None:
    memory = service()
    m1 = remember(memory, "Project uses Python 3.10.")
    m2 = memory.supersede(
        agent_id=AGENT_A,
        old_memory_id=m1.memory_id,
        content="Project uses Python 3.11.",
        provenance=provenance(source_session_id="session-2"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION),
    )
    m3 = memory.supersede(
        agent_id=AGENT_A,
        old_memory_id=m2.memory_id,
        content="Project uses Python 3.12.",
        provenance=provenance(source_session_id="session-3"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION),
    )

    active = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=10,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )
    history = {item.memory_id: item for item in memory.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )}

    assert [item.memory_id for item in active] == [m3.memory_id]
    assert history[m1.memory_id].lifecycle_state == "SUPERSEDED"
    assert history[m1.memory_id].superseded_by_memory_id == m2.memory_id
    assert history[m2.memory_id].lifecycle_state == "SUPERSEDED"
    assert history[m2.memory_id].supersedes_memory_id == m1.memory_id
    assert history[m2.memory_id].superseded_by_memory_id == m3.memory_id
    assert history[m3.memory_id].lifecycle_state == "ACTIVE"
    assert history[m3.memory_id].supersedes_memory_id == m2.memory_id


def test_c3_supersede_cycle_rejection_leaves_durable_state_unchanged() -> None:
    memory = service()
    m1 = remember(memory, "Project uses Python 3.11.")
    m2 = memory.supersede(
        agent_id=AGENT_A,
        old_memory_id=m1.memory_id,
        content="Project uses Python 3.12.",
        provenance=provenance(),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION),
    )
    before = memory.durable_events()

    with pytest.raises(MemoryCorruptionError):
        memory.supersede(
            agent_id=AGENT_A,
            old_memory_id=m2.memory_id,
            memory_id=m1.memory_id,
            content="Project uses Python 3.11 again.",
            provenance=provenance(),
            capability_evaluator=grants(
                AGENT_A,
                MEMORY_WRITE_ACTION,
                MEMORY_FORGET_ACTION,
            ),
        )

    assert memory.durable_events() == before


def test_c4_explicit_conflict_preserves_both_active_memories_with_relation_metadata() -> None:
    memory = service()
    m1 = remember(memory, "Project requires Python 3.11.")
    m2 = remember(memory, "Project requires Python 3.12.")

    conflicted = memory.mark_conflict(
        agent_id=AGENT_A,
        memory_ids=(m1.memory_id, m2.memory_id),
        reason="Two remembered Python versions disagree.",
        evidence_provenance=provenance("host", source_session_id="review-session"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    active = memory.search(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        query="Python",
        limit=10,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert {item.memory_id for item in active} == {m1.memory_id, m2.memory_id}
    assert len({item.conflict_group_id for item in conflicted}) == 1
    assert conflicted[0].conflicts_with_memory_ids == (m2.memory_id,)
    assert conflicted[1].conflicts_with_memory_ids == (m1.memory_id,)
    assert all(item.lifecycle_state == "ACTIVE" for item in conflicted)


def test_c5_scope_separation_has_no_automatic_conflict_and_cross_scope_conflict_is_rejected() -> None:
    memory = service()
    evaluator = all_namespace_grants(AGENT_A, MEMORY_WRITE_ACTION)
    m1 = remember(
        memory,
        "Preferred implementation language is Python.",
        namespace=NAMESPACE,
        capability_evaluator=evaluator,
    )
    m2 = remember(
        memory,
        "Preferred implementation language is Rust.",
        namespace=OTHER_NAMESPACE,
        capability_evaluator=evaluator,
    )
    history = memory.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        capability_evaluator=all_namespace_grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert all(item.conflict_group_id is None for item in history)
    with pytest.raises(MemoryInvalid):
        memory.mark_conflict(
            agent_id=AGENT_A,
            memory_ids=(m1.memory_id, m2.memory_id),
            reason="Different namespaces must not be merged by Kernel.",
            evidence_provenance=provenance(),
            capability_evaluator=evaluator,
        )


def test_c6_current_evidence_marks_memory_stale_with_restart_provenance() -> None:
    store = InMemoryMemoryStore()
    first = service(store)
    m1 = remember(first, "Project requires Python 3.11.")
    first.mark_stale(
        m1.memory_id,
        agent_id=AGENT_A,
        reason="Current pyproject.toml says requires-python >=3.12",
        evidence_provenance=provenance(
            "current-observation",
            source_session_id="session-current",
            source_event_id="tool-result-9",
            source_tool_name="read_file",
            note="pyproject.toml requires-python >=3.12",
        ),
        observed_at=99.0,
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    restarted = service(InMemoryMemoryStore(first.durable_events()))
    restored = restarted.read(
        m1.memory_id,
        agent_id=AGENT_A,
        include_inactive=True,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )

    assert restored.lifecycle_state == "STALE"
    assert restored.stale_at == 99.0
    assert restored.stale_reason == "Current pyproject.toml says requires-python >=3.12"
    assert restored.stale_provenance is not None
    assert restored.stale_provenance.source == "current-observation"
    assert restored.stale_provenance.source_event_id == "tool-result-9"


def test_c7_read_only_agent_cannot_mark_stale() -> None:
    memory = service()
    record = remember(memory, "Private preference.")
    read_only = CapabilityEvaluator(
        [CapabilityGrant(AGENT_B, MEMORY_READ_ACTION, memory_namespace_scope(AGENT_A, NAMESPACE))]
    )

    with pytest.raises(MemoryAccessDenied):
        memory.mark_stale(
            record.memory_id,
            agent_id=AGENT_B,
            reason="read-only agents cannot mutate lifecycle",
            evidence_provenance=provenance(),
            capability_evaluator=read_only,
        )


def test_c8_context_projection_filters_to_active_memories_by_default() -> None:
    memory = service()
    mutation = grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION)
    write = grants(AGENT_A, MEMORY_WRITE_ACTION)
    forget = grants(AGENT_A, MEMORY_FORGET_ACTION)
    for index in range(100):
        remember(memory, f"Active memory {index}.")
        stale = remember(memory, f"Stale memory {index}.")
        memory.mark_stale(
            stale.memory_id,
            agent_id=AGENT_A,
            reason="current evidence invalidated this remembered proposition",
            evidence_provenance=provenance("current-observation"),
            capability_evaluator=write,
        )
        old = remember(memory, f"Superseded memory {index}.")
        memory.supersede(
            agent_id=AGENT_A,
            old_memory_id=old.memory_id,
            content=f"Superseding active memory {index}.",
            provenance=provenance(),
            capability_evaluator=mutation,
        )
        forgotten = remember(memory, f"Forgotten memory {index}.")
        memory.forget(
            forgotten.memory_id,
            agent_id=AGENT_A,
            capability_evaluator=forget,
        )

    history = memory.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )
    projection = project_memories_to_context_pages(history, top_k=250)

    assert {record.lifecycle_state for record in projection.selected_records} == {"ACTIVE"}
    assert all("status: ACTIVE" in page.content for page in projection.pages)
    assert not any("status: STALE" in page.content for page in projection.pages)
    assert not any("status: SUPERSEDED" in page.content for page in projection.pages)
    assert not any("status: FORGOTTEN" in page.content for page in projection.pages)


def test_c9_conflict_projection_includes_both_memories_status_provenance_and_relation() -> None:
    memory = service()
    m1 = remember(memory, "Preferred implementation language is Python.")
    m2 = remember(memory, "Preferred implementation language is Rust.")
    conflicted = memory.mark_conflict(
        agent_id=AGENT_A,
        memory_ids=(m1.memory_id, m2.memory_id),
        reason="User preference memories disagree.",
        evidence_provenance=provenance("host", source_session_id="review"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
        conflict_group_id="conflict_language",
    )

    projection = project_conflicting_memories_to_context_pages(conflicted, top_k=10)
    content = "\n".join(page.content for page in projection.pages)

    assert projection.selected_count == 2
    assert "Preferred implementation language is Python." in content
    assert "Preferred implementation language is Rust." in content
    assert "status: ACTIVE" in content
    assert "conflict_group: conflict_language" in content
    assert f"conflicts_with: {m2.memory_id}" in content
    assert f"conflicts_with: {m1.memory_id}" in content
    assert "provenance: host" in content
    assert "Kernel does not choose truth" in content


def test_c10_durable_correctness_lifecycle_recovers_in_fresh_runtime(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    first = service(JsonlMemoryStore(path))
    stale = remember(first, "Project requires Python 3.11.")
    first.mark_stale(
        stale.memory_id,
        agent_id=AGENT_A,
        reason="pyproject now says >=3.12",
        evidence_provenance=provenance("current-observation", source_event_id="read-1"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    c1 = remember(first, "Preferred implementation language is Python.")
    c2 = remember(first, "Preferred implementation language is Rust.")
    first.mark_conflict(
        agent_id=AGENT_A,
        memory_ids=(c1.memory_id, c2.memory_id),
        reason="conflicting user preference memories",
        evidence_provenance=provenance("host", source_event_id="review-1"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION),
    )
    s1 = remember(first, "Project uses Python 3.10.")
    s2 = first.supersede(
        agent_id=AGENT_A,
        old_memory_id=s1.memory_id,
        content="Project uses Python 3.12.",
        provenance=provenance("session", source_session_id="session-new"),
        capability_evaluator=grants(AGENT_A, MEMORY_WRITE_ACTION, MEMORY_FORGET_ACTION),
    )
    first.close()

    restarted = service(JsonlMemoryStore(path))
    history = {item.memory_id: item for item in restarted.history(
        agent_id=AGENT_A,
        owner_agent_id=AGENT_A,
        namespace=NAMESPACE,
        capability_evaluator=grants(AGENT_A, MEMORY_READ_ACTION),
    )}

    assert history[stale.memory_id].lifecycle_state == "STALE"
    assert history[stale.memory_id].stale_provenance is not None
    assert history[c1.memory_id].conflicts_with_memory_ids == (c2.memory_id,)
    assert history[c2.memory_id].conflicts_with_memory_ids == (c1.memory_id,)
    assert history[s1.memory_id].lifecycle_state == "SUPERSEDED"
    assert history[s1.memory_id].superseded_by_memory_id == s2.memory_id
    assert history[s2.memory_id].lifecycle_state == "ACTIVE"
