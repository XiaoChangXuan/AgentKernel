from __future__ import annotations

from agentkernel import (
    ApproximateTokenEstimator,
    ContextBudget,
    ContextManager,
    ContextPageKind,
    ContextPolicyConfig,
    ContextProjector,
    DefaultContextPolicy,
    MessageRole,
    Session,
)

from tests.context_support import append_text_turn, append_tool_turn


def manager() -> ContextManager:
    return ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(
            ContextPolicyConfig(
                recent_turns=1,
                large_tool_result_threshold_tokens=1,
                tool_result_cold_after_turns=0,
            )
        ),
    )


def test_tool_call_and_result_are_evicted_as_one_atomic_group() -> None:
    session = Session("session-1")
    append_tool_turn(session, 1, output="old result")
    append_text_turn(session, 2, "new", "answer")
    context = manager()
    projected = context.projector.project(session)
    old_tool_pages = [page for page in projected if page.atomic_group]
    current_cost = sum(page.token_cost for page in projected if page.turn == 2)
    tool_group_cost = sum(page.token_cost for page in old_tool_pages)

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(current_cost + tool_group_cost - 1),
    )

    selected_ids = {page.page_id for page in working_set.pages}
    assert all(page.page_id not in selected_ids for page in old_tool_pages)
    assert {
        page.kind for page in working_set.evicted_pages if page.atomic_group
    } == {
        ContextPageKind.ASSISTANT_MESSAGE,
        ContextPageKind.TOOL_RESULT,
    }
    assert all(message.role is not MessageRole.TOOL for message in working_set.to_messages())


def test_page_in_of_tool_result_also_pages_in_assistant_tool_call() -> None:
    session = Session("session-1")
    append_tool_turn(session, 1, output="old result")
    append_text_turn(session, 2, "new", "answer")
    context = manager()
    projected = context.projector.project(session)
    old_tool_pages = [page for page in projected if page.atomic_group]
    tool_result = next(
        page for page in old_tool_pages if page.kind is ContextPageKind.TOOL_RESULT
    )
    current_cost = sum(page.token_cost for page in projected if page.turn == 2)
    group_cost = sum(page.token_cost for page in old_tool_pages)
    context.request_page(tool_result.page_id)

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(current_cost + group_cost),
    )

    selected_ids = {page.page_id for page in working_set.pages}
    assert all(page.page_id in selected_ids for page in old_tool_pages)
    messages = working_set.to_messages()
    assistant_index = next(
        index for index, message in enumerate(messages) if message.tool_calls
    )
    result_index = next(
        index for index, message in enumerate(messages) if message.role is MessageRole.TOOL
    )
    assert assistant_index < result_index
    assert messages[result_index].tool_call_id == messages[assistant_index].tool_calls[0].call_id
