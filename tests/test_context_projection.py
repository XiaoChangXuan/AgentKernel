from __future__ import annotations

from dataclasses import replace

import pytest

from agentkernel import (
    ApproximateTokenEstimator,
    ContextBudget,
    ContextPageKind,
    ContextManager,
    ContextProtocolError,
    ContextProjector,
    ContextTemperature,
    ContextTrustLabel,
    ContextTrustLabel,
    EventType,
    JsonlSessionPersistence,
    Session,
)

from tests.context_support import append_text_turn, append_tool_turn


def test_projection_derives_pages_and_excludes_kernel_only_events() -> None:
    session = Session("session-1")
    append_tool_turn(
        session,
        1,
        output="durable output",
        mutation_wal=True,
    )
    projector = ContextProjector(ApproximateTokenEstimator(1))

    pages = projector.project(session, system_prompt="policy")

    assert [page.kind for page in pages] == [
        ContextPageKind.SYSTEM,
        ContextPageKind.USER_MESSAGE,
        ContextPageKind.ASSISTANT_MESSAGE,
        ContextPageKind.TOOL_RESULT,
    ]
    assert [page.message for page in pages if page.message is not None] == list(
        session.derive_messages()
    )
    event_page_seqs = {page.created_seq for page in pages if page.created_seq}
    wal_seqs = {
        event.seq
        for event in session.events
        if event.type
        in {
            EventType.TOOL_PREPARE,
            EventType.TOOL_DISPATCH,
            EventType.TOOL_COMMIT,
        }
    }
    assert event_page_seqs.isdisjoint(wal_seqs)
    assert pages[0].trust_label is ContextTrustLabel.KERNEL
    assert pages[1].trust_label is ContextTrustLabel.USER
    assert pages[2].trust_label is ContextTrustLabel.EXTERNAL
    assert pages[3].trust_label is ContextTrustLabel.TOOL
    assert all(page.temperature is ContextTemperature.WARM for page in pages)


def test_page_identity_and_projection_are_deterministic() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "hello", "world")
    projector = ContextProjector(ApproximateTokenEstimator(1))

    first = projector.project(session, system_prompt="policy")
    second = projector.project(session, system_prompt="policy")

    assert first == second
    assert [page.page_id for page in first] == [
        "session:session-1:system",
        "session:session-1:event:2",
        "session:session-1:event:4",
    ]


def test_jsonl_reload_rebuilds_identical_context_projection(tmp_path) -> None:
    path = tmp_path / "context-recovery.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    append_text_turn(session, 1, "first user", "first assistant")
    append_tool_turn(session, 2, output="tool output", mutation_wal=True)
    projector = ContextProjector(ApproximateTokenEstimator(1))
    before = projector.project(session, system_prompt="same policy")
    before_working_set = ContextManager(projector=projector).build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(1_000),
        system_prompt="same policy",
    )
    session.close()

    restored = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        after = projector.project(restored, system_prompt="same policy")
        after_working_set = ContextManager(projector=projector).build_working_set(
            restored,
            current_turn=2,
            budget=ContextBudget(1_000),
            system_prompt="same policy",
        )
        assert after == before
        assert after_working_set == before_working_set
        assert after_working_set.to_messages() == before_working_set.to_messages()
        assert restored.recovery_analysis.status.value == "completed"
    finally:
        restored.close()


def test_policy_cannot_mutate_projected_facts() -> None:
    class FactMutatingPolicy:
        def apply(self, pages, *, current_turn):  # type: ignore[no-untyped-def]
            del current_turn
            return (
                replace(pages[0], trust_label=ContextTrustLabel.KERNEL),
                *pages[1:],
            )

    session = Session("session-1")
    append_text_turn(session, 1, "truth", "answer")
    context = ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=FactMutatingPolicy(),
    )

    with pytest.raises(ContextProtocolError, match="may change only"):
        context.build_working_set(
            session,
            current_turn=1,
            budget=ContextBudget(100),
        )
