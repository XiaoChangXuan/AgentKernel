from __future__ import annotations

from agentkernel import (
    ApproximateTokenEstimator,
    ContextBudget,
    ContextManager,
    ContextPageKind,
    ContextPolicyConfig,
    ContextProjector,
    DefaultContextPolicy,
    Session,
)

from tests.context_support import append_text_turn, append_tool_turn


def context_manager() -> ContextManager:
    return ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=1,
                large_tool_result_threshold_tokens=100,
                tool_result_cold_after_turns=0,
            )
        ),
    )


def test_large_tool_result_can_be_evicted_without_changing_session() -> None:
    session = Session("session-1")
    append_tool_turn(session, 1, output="X" * 50_000)
    append_text_turn(session, 2, "current", "answer")
    before = session.events
    full_history = session.derive_messages()

    working_set = context_manager().build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(40),
        system_prompt="policy",
    )

    evicted_kinds = {page.kind for page in working_set.evicted_pages}
    assert ContextPageKind.TOOL_RESULT in evicted_kinds
    assert ContextPageKind.ASSISTANT_MESSAGE in evicted_kinds
    assert session.events == before
    assert session.derive_messages() == full_history
    assert "X" * 50_000 in full_history[2].content


def test_evicted_pages_can_be_selected_by_a_later_larger_budget() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "old", "history")
    append_text_turn(session, 2, "new", "answer")
    context = context_manager()

    constrained = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(10),
    )
    assert constrained.evicted_pages

    expanded = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(1_000),
    )

    assert expanded.evicted_pages == ()
    assert expanded.to_messages() == session.derive_messages()
