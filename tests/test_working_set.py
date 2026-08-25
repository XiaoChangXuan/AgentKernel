from __future__ import annotations

import asyncio

from agentkernel import (
    Agent,
    ApproximateTokenEstimator,
    ContextBudget,
    ContextManager,
    ContextPolicyConfig,
    ContextProjector,
    DefaultAgentLoop,
    DefaultContextPolicy,
    ModelRequest,
    ModelResponse,
    PromptService,
    ScriptedLLM,
    Session,
    ToolRegistry,
)

from tests.context_support import append_text_turn


def test_selected_pages_are_restored_to_causal_order() -> None:
    session = Session("session-1")
    append_text_turn(session, 1, "old-user", "old-assistant")
    append_text_turn(session, 2, "new-user", "new-assistant")
    context = ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(ContextPolicyConfig(recent_turns=1)),
    )
    context.pin("session:session-1:event:2")

    working_set = context.build_working_set(
        session,
        current_turn=2,
        budget=ContextBudget(30),
    )

    assert [page.created_seq for page in working_set.pages] == sorted(
        page.created_seq for page in working_set.pages
    )
    assert [message.content for message in working_set.to_messages()] == [
        "old-user",
        "new-user",
        "new-assistant",
    ]


def test_default_loop_builds_requests_from_working_set_not_full_history() -> None:
    session = Session("session-1")
    agent = Agent.create(agent_id="agent-1", session=session)
    context = ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(ContextPolicyConfig(recent_turns=1)),
    )

    def second_turn(request: ModelRequest) -> ModelResponse:
        assert [message.content for message in request.messages] == ["b" * 20]
        return ModelResponse(content="B" * 20)

    loop = DefaultAgentLoop(
        llm=ScriptedLLM([ModelResponse(content="A" * 20), second_turn]),
        tools=ToolRegistry(),
        prompt=PromptService(),
        context=context,
        context_budget=ContextBudget(20),
    )

    assert asyncio.run(loop.run(agent, "a" * 20)) == "A" * 20
    assert asyncio.run(loop.run(agent, "b" * 20)) == "B" * 20
    assert [message.content for message in session.derive_messages()] == [
        "a" * 20,
        "A" * 20,
        "b" * 20,
        "B" * 20,
    ]


def test_synthetic_long_history_retains_pinned_constraint_and_recent_context() -> None:
    session = Session("session-1")
    for turn in range(1, 101):
        append_text_turn(
            session,
            turn,
            f"user-{turn:03d}",
            f"assistant-{turn:03d}",
        )
    context = ContextManager(
        projector=ContextProjector(ApproximateTokenEstimator(1)),
        policy=DefaultContextPolicy(ContextPolicyConfig(recent_turns=2)),
    )
    context.pin("session:session-1:event:2")

    working_set = context.build_working_set(
        session,
        current_turn=100,
        budget=ContextBudget(80),
        system_prompt="kernel-policy",
    )

    contents = {page.content for page in working_set.pages}
    assert "kernel-policy" in contents
    assert "user-001" in contents
    assert "user-100" in contents
    assert "assistant-100" in contents
    assert working_set.metrics.projected_pages == 201
    assert working_set.metrics.selected_tokens <= 80
    assert working_set.metrics.evicted_pages > 0
