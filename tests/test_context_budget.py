from __future__ import annotations

import pytest

from agentkernel import (
    ApproximateTokenEstimator,
    ContextBudget,
    ContextBudgetExceeded,
    ContextManager,
    ContextPolicyConfig,
    ContextProjector,
    DefaultContextPolicy,
    Session,
)

from tests.context_support import append_text_turn


def manager() -> ContextManager:
    return ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=1,
                pin_current_user=True,
            )
        ),
    )


def history() -> Session:
    session = Session("session-1")
    append_text_turn(session, 1, "O" * 25, "o" * 25)
    append_text_turn(session, 2, "R" * 20, "r" * 20)
    return session


def test_context_budget_reserves_output_capacity() -> None:
    budget = ContextBudget(max_tokens=128, reserved_output_tokens=16)

    assert budget.available_input_tokens == 112


def test_under_budget_preserves_full_projection() -> None:
    session = history()
    context = manager()

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(100),
        system_prompt="S" * 10,
    )

    assert working_set.metrics.projected_tokens == 100
    assert working_set.metrics.selected_tokens == 100
    assert working_set.metrics.evicted_pages == 0
    assert tuple(page.message for page in working_set.pages if page.message) == (
        *session.derive_messages(),
    )


def test_fifty_token_history_under_one_hundred_token_budget_is_unchanged() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "U" * 25, "A" * 25)

    working_set = manager().build_working_set(
        session,
        current_turn=1,
        budget=ContextBudget(100),
    )

    assert working_set.metrics.projected_tokens == 50
    assert working_set.metrics.selected_tokens == 50
    assert working_set.to_messages() == session.derive_messages()


def test_over_budget_prefers_system_and_recent_turn() -> None:
    session = history()
    context = manager()

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(60),
        system_prompt="S" * 10,
    )

    selected_contents = [page.content for page in working_set.pages]
    assert selected_contents == ["S" * 10, "R" * 20, "r" * 20]
    assert {page.content for page in working_set.evicted_pages} == {
        "O" * 25,
        "o" * 25,
    }
    assert working_set.metrics.selected_tokens == 50
    assert working_set.metrics.evicted_tokens == 50
    assert working_set.metrics.budget_tokens == 60


def test_pinned_pages_are_never_silently_evicted() -> None:
    session = history()
    context = manager()
    context.pin("session:session-1:event:2")

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(60),
        system_prompt="S" * 10,
    )

    assert "session:session-1:event:2" in {
        page.page_id for page in working_set.pages
    }
    assert "session:session-1:event:2" not in {
        page.page_id for page in working_set.evicted_pages
    }


def test_mandatory_pages_over_budget_fail_explicitly() -> None:
    session = history()
    context = manager()
    context.pin("session:session-1:event:2")

    with pytest.raises(ContextBudgetExceeded) as captured:
        context.build_working_set(
            session,
            current_turn=2,
            budget=ContextBudget(50),
            system_prompt="S" * 10,
        )

    assert captured.value.required_tokens == 55
    assert captured.value.available_tokens == 50


def test_manual_pin_can_be_removed_without_changing_policy_pins() -> None:
    session = history()
    context = manager()
    old_page = "session:session-1:event:2"
    context.pin(old_page)
    context.unpin(old_page)

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(50),
        system_prompt="S" * 10,
    )

    assert old_page in {page.page_id for page in working_set.evicted_pages}
    assert "session:session-1:system" in {
        page.page_id for page in working_set.pages if page.pinned
    }
