from __future__ import annotations

import pytest

from agentkernel import (
    ApproximateTokenEstimator,
    ContextBudget,
    ContextBudgetExceeded,
    ContextManager,
    ContextPageNotFound,
    ContextPolicyConfig,
    ContextProjector,
    DefaultContextPolicy,
    Session,
)

from tests.context_support import append_text_turn


def manager() -> ContextManager:
    return ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(ContextPolicyConfig(recent_turns=1)),
    )


def test_explicit_page_in_reintroduces_one_evicted_page_for_next_working_set() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "O" * 25, "o" * 25)
    append_text_turn(session, 2, "R" * 20, "r" * 20)
    context = manager()
    budget = ContextBudget(60)

    first = context.build_working_set(
        session,
        current_turn=2,
        budget=budget,
        system_prompt="S" * 10,
    )
    page_id = "session:session-1:event:2"
    assert page_id in {page.page_id for page in first.evicted_pages}

    context.request_page(page_id)
    second = context.build_working_set(
        session,
        current_turn=2,
        budget=budget,
        system_prompt="S" * 10,
    )
    assert page_id in {page.page_id for page in second.pages}
    assert second.metrics.selected_tokens == 55

    third = context.build_working_set(
        session,
        current_turn=2,
        budget=budget,
        system_prompt="S" * 10,
    )
    assert page_id in {page.page_id for page in third.evicted_pages}


def test_page_in_obeys_budget_instead_of_overcommitting() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "old-page", "old-answer")
    append_text_turn(session, 2, "current-page", "current-answer")
    context = manager()
    context.request_page("session:session-1:event:2")

    with pytest.raises(ContextBudgetExceeded):
        context.build_working_set(
            session,
            current_turn=2,
            budget=ContextBudget(15),
            system_prompt="policy",
        )


def test_page_in_rejects_unknown_page_identity() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "hello", "world")
    context = manager()
    context.request_page("session:session-1:event:999")

    with pytest.raises(ContextPageNotFound, match="event:999"):
        context.build_working_set(
            session,
            current_turn=1,
            budget=ContextBudget(100),
        )
