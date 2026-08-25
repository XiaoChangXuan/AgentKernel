from __future__ import annotations

import asyncio

import pytest

from agentkernel import (
    Agent,
    AgentState,
    ApproximateRequestTokenAccounting,
    ApproximateTokenEstimator,
    ContextBudget,
    ContextManager,
    ContextOverflowRecoveryError,
    ContextProjector,
    DefaultAgentLoop,
    EventType,
    LLMErrorKind,
    LLMService,
    LLMServiceError,
    Message,
    ModelRequest,
    ModelResponse,
    PromptService,
    Session,
    ToolRegistry,
    ToolSchema,
)

from tests.context_support import append_text_turn, append_tool_turn


class ClassifiedFakeProvider(LLMService):
    def __init__(self, outcomes: list[LLMErrorKind | ModelResponse]) -> None:
        self.outcomes = outcomes
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        index = len(self.requests)
        self.requests.append(request)
        outcome = self.outcomes[index]
        if isinstance(outcome, LLMErrorKind):
            raise LLMServiceError(outcome, f"fake {outcome.value}")
        return outcome


class ContextLimitFakeProvider(LLMService):
    """Deterministically rejects requests whose complete estimate exceeds limit."""

    def __init__(self, limit: int, response: ModelResponse) -> None:
        self.limit = limit
        self.response = response
        self.requests: list[ModelRequest] = []
        self.accounting = ApproximateRequestTokenAccounting(
            estimator=ApproximateTokenEstimator(characters_per_token=1)
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.accounting.estimate_request(request).total_tokens > self.limit:
            raise LLMServiceError(
                LLMErrorKind.CONTEXT_OVERFLOW,
                "fixture request exceeds its context limit",
            )
        return self.response


def _history_session(session_id: str) -> Session:
    session = Session(session_id)
    for turn in range(1, 8):
        append_text_turn(
            session,
            turn,
            f"user-{turn}:" + ("u" * 115),
            f"assistant-{turn}:" + ("a" * 110),
        )
    return session


def _loop(llm: LLMService, *, budget: ContextBudget | None = None) -> DefaultAgentLoop:
    estimator = ApproximateTokenEstimator(characters_per_token=1)
    accounting = ApproximateRequestTokenAccounting(estimator=estimator)
    return DefaultAgentLoop(
        llm=llm,
        tools=ToolRegistry(),
        prompt=PromptService("keep working"),
        context=ContextManager(projector=ContextProjector(estimator)),
        context_budget=budget or ContextBudget(2_200, 200),
        token_accounting=accounting,
    )


def test_request_accounting_includes_tools_and_protocol_overhead() -> None:
    accounting = ApproximateRequestTokenAccounting(
        estimator=ApproximateTokenEstimator(characters_per_token=1),
        provider="fixture",
        model="fixture-model",
    )
    base = accounting.estimate_request(
        ModelRequest(messages=(Message.user("hello"),), system_prompt="system")
    )
    with_tool = accounting.estimate_request(
        ModelRequest(
            messages=(Message.user("hello"),),
            system_prompt="system",
            tools=(
                ToolSchema(
                    "files.read",
                    "Read a file.",
                    {"type": "object", "properties": {"path": {"type": "string"}}},
                ),
            ),
        )
    )

    assert base.system_prompt_tokens > len("system")
    assert base.message_tokens > len("hello")
    assert base.envelope_tokens > 0
    assert base.tool_schema_tokens == 0
    assert with_tool.tool_schema_tokens > 0
    assert with_tool.total_tokens > base.total_tokens
    assert accounting.estimate_request(
        ModelRequest(messages=(Message.user("hello"),), system_prompt="system")
    ) == base


def test_provider_overflow_reclaims_and_retries_exactly_once() -> None:
    llm = ContextLimitFakeProvider(1_700, ModelResponse("recovered"))
    session = _history_session("overflow-success")
    agent = Agent.create(agent_id="agent", session=session)
    loop = _loop(llm)

    answer = asyncio.run(loop.run(agent, "continue the current task"))

    assert answer == "recovered"
    assert len(llm.requests) == 2
    accounting = ApproximateRequestTokenAccounting(
        estimator=ApproximateTokenEstimator(characters_per_token=1)
    )
    before = accounting.estimate_request(llm.requests[0]).total_tokens
    after = accounting.estimate_request(llm.requests[1]).total_tokens
    assert before > llm.limit
    assert after <= llm.limit < before
    assert llm.requests[1].messages[-1].content == "continue the current task"
    assert loop.last_context_recovery is not None
    assert loop.last_context_recovery.provider_attempts == 2
    assert loop.last_context_recovery.after_tokens < loop.last_context_recovery.before_tokens
    assert sum(e.type is EventType.ASSISTANT_MESSAGE for e in session.events) == 8
    assert sum(e.type is EventType.TOOL_CALL for e in session.events) == 0
    assert agent.control.state is AgentState.READY


def test_second_provider_overflow_stops_without_a_third_call() -> None:
    llm = ClassifiedFakeProvider(
        [LLMErrorKind.CONTEXT_OVERFLOW, LLMErrorKind.CONTEXT_OVERFLOW]
    )
    session = _history_session("overflow-twice")
    agent = Agent.create(agent_id="agent", session=session)

    with pytest.raises(ContextOverflowRecoveryError, match="after one reclaimed retry"):
        asyncio.run(_loop(llm).run(agent, "continue the current task"))

    assert len(llm.requests) == 2
    assert sum(e.type is EventType.ASSISTANT_MESSAGE for e in session.events) == 7
    assert sum(e.type is EventType.TOOL_CALL for e in session.events) == 0
    assert agent.control.state is AgentState.FAILED


def test_non_overflow_provider_failure_is_never_retried() -> None:
    llm = ClassifiedFakeProvider([LLMErrorKind.RATE_LIMIT])
    session = Session("rate-limit")
    agent = Agent.create(agent_id="agent", session=session)

    with pytest.raises(LLMServiceError) as captured:
        asyncio.run(_loop(llm).run(agent, "hello"))

    assert captured.value.kind is LLMErrorKind.RATE_LIMIT
    assert len(llm.requests) == 1


def test_forced_reclaim_fails_clearly_when_only_pinned_pages_remain() -> None:
    llm = ClassifiedFakeProvider([LLMErrorKind.CONTEXT_OVERFLOW])
    session = Session("pinned-only")
    agent = Agent.create(agent_id="agent", session=session)

    with pytest.raises(ContextOverflowRecoveryError, match="no measurable progress"):
        asyncio.run(
            _loop(llm, budget=ContextBudget(200, 0)).run(agent, "short input")
        )

    assert len(llm.requests) == 1
    assert sum(e.type is EventType.TOOL_CALL for e in session.events) == 0


def test_overflow_after_tool_result_preserves_atomic_protocol_and_raw_events() -> None:
    session = Session("overflow-tool")
    append_tool_turn(session, 1, output="header\n" + ("log line\n" * 180) + "fatal")
    for turn in range(2, 8):
        append_text_turn(session, turn, "u" * 120, "a" * 115)
    original_tool_calls = sum(e.type is EventType.TOOL_CALL for e in session.events)
    original_tool_results = sum(e.type is EventType.TOOL_RESULT for e in session.events)
    llm = ClassifiedFakeProvider(
        [LLMErrorKind.CONTEXT_OVERFLOW, ModelResponse("recovered safely")]
    )
    agent = Agent.create(agent_id="agent", session=session)

    answer = asyncio.run(_loop(llm).run(agent, "continue"))

    assert answer == "recovered safely"
    assert len(llm.requests) == 2
    assert sum(e.type is EventType.TOOL_CALL for e in session.events) == original_tool_calls
    assert sum(e.type is EventType.TOOL_RESULT for e in session.events) == original_tool_results
    for request in llm.requests:
        outstanding: set[str] = set()
        for message in request.messages:
            outstanding.update(call.call_id for call in message.tool_calls)
            if message.tool_call_id is not None:
                assert message.tool_call_id in outstanding
                outstanding.remove(message.tool_call_id)
        assert not outstanding
