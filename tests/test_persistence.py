from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from agentkernel import (
    Agent,
    DefaultAgentLoop,
    EventType,
    InMemorySessionPersistence,
    JsonlSessionPersistence,
    MessageRole,
    ModelRequest,
    ModelResponse,
    PromptService,
    ScriptedLLM,
    Session,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionPersistenceError,
    SessionStatus,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


def append_completed_tool_turn(session: Session) -> None:
    call = ToolCall("call-1", "math.add", {"a": 20, "b": 22})
    result = ToolResult.success(call, 42)
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": 1, "content": "What is 20 + 22?"},
    )
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": 1,
            "step": 1,
            "content": "",
            "tool_calls": [call.as_dict()],
        },
    )
    session.append(
        EventType.TOOL_CALL,
        {"turn": 1, "step": 1, **call.as_dict()},
    )
    session.append(
        EventType.TOOL_RESULT,
        {"turn": 1, "step": 1, **result.as_dict()},
    )
    session.append(
        EventType.STEP_END,
        {"turn": 1, "step": 1, "outcome": "tool_calls"},
    )
    session.append(EventType.STEP_START, {"turn": 1, "step": 2})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": 1,
            "step": 2,
            "content": "The result is 42.",
            "tool_calls": [],
        },
    )
    session.append(
        EventType.STEP_END,
        {"turn": 1, "step": 2, "outcome": "completed"},
    )
    session.append(EventType.TURN_END, {"turn": 1, "reason": "completed"})


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["a"]) + int(arguments["b"])


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema("math.add", "Add numbers.", {"type": "object"}),
            handler=add,
            required_capability="math.add",
        )
    )
    return tools


def test_jsonl_reload_matches_in_memory_model_history(tmp_path) -> None:
    memory = Session("session-memory", InMemorySessionPersistence())
    append_completed_tool_turn(memory)

    path = tmp_path / "session-jsonl.jsonl"
    durable = Session("session-jsonl", JsonlSessionPersistence(path))
    append_completed_tool_turn(durable)
    durable.flush()
    durable.close()

    reloaded = Session.load("session-jsonl", JsonlSessionPersistence(path))
    try:
        assert reloaded.derive_messages() == memory.derive_messages()
        assert [message.role for message in reloaded.derive_messages()] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
        ]
        assert reloaded.recovery_analysis.status is SessionStatus.COMPLETED
        assert reloaded.recovery_analysis.last_turn_reason == "completed"
    finally:
        reloaded.close()


def test_in_memory_driver_supports_the_same_load_contract() -> None:
    persistence = InMemorySessionPersistence()
    original = Session("session-1", persistence)
    append_completed_tool_turn(original)
    before = original.derive_messages()

    restored = Session.load("session-1", persistence)
    try:
        assert restored.derive_messages() == before
        assert restored.recovery_analysis.status is SessionStatus.COMPLETED
    finally:
        restored.close()


def test_default_loop_writes_a_replayable_jsonl_session(tmp_path) -> None:
    path = tmp_path / "loop.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"math.add"},
    )

    def finish(request: ModelRequest) -> ModelResponse:
        assert json.loads(request.messages[-1].content)["output"] == 42
        return ModelResponse(content="The result is 42.")

    loop = DefaultAgentLoop(
        llm=ScriptedLLM(
            [
                ModelResponse(
                    tool_calls=(ToolCall("call-1", "math.add", {"a": 20, "b": 22}),)
                ),
                finish,
            ]
        ),
        tools=registry(),
        prompt=PromptService("Use tools."),
    )

    assert asyncio.run(loop.run(agent, "Calculate.")) == "The result is 42."
    before = session.derive_messages()
    session.close()

    reloaded = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        assert reloaded.derive_messages() == before
        assert reloaded.recovery_analysis.status is SessionStatus.COMPLETED
        assert len(reloaded.events) == 11
    finally:
        reloaded.close()


def test_reloaded_session_only_appends_and_preserves_original_bytes(tmp_path) -> None:
    path = tmp_path / "append-only.jsonl"
    original = Session("session-1", JsonlSessionPersistence(path))
    append_completed_tool_turn(original)
    original.close()
    prefix = path.read_bytes()

    reloaded = Session.load("session-1", JsonlSessionPersistence(path))
    reloaded.append(EventType.TURN_START, {"turn": 2})
    reloaded.append(EventType.TURN_END, {"turn": 2, "reason": "completed"})
    reloaded.close()

    assert path.read_bytes().startswith(prefix)
    final = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        assert [event.seq for event in final.events] == list(range(1, 14))
        assert final.recovery_analysis.status is SessionStatus.COMPLETED
    finally:
        final.close()


def test_persistence_failure_does_not_enter_session_event_log() -> None:
    class FailingPersistence(InMemorySessionPersistence):
        def append(self, event) -> None:  # type: ignore[no-untyped-def]
            raise SessionPersistenceError("disk unavailable")

    session = Session("session-1", FailingPersistence())

    with pytest.raises(SessionPersistenceError, match="disk unavailable"):
        session.append(EventType.TURN_START, {"turn": 1})

    assert session.events == ()


def test_flush_and_close_have_explicit_behavior(tmp_path) -> None:
    path = tmp_path / "closed.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    session.flush()
    session.close()
    session.close()

    with pytest.raises(SessionPersistenceError, match="session is closed"):
        session.append(EventType.TURN_START, {"turn": 1})

    reloaded = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        assert reloaded.events == ()
        assert reloaded.recovery_analysis.status is SessionStatus.COMPLETED
    finally:
        reloaded.close()


def test_jsonl_driver_reports_not_found_and_refuses_overwrite(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    with pytest.raises(SessionNotFoundError):
        Session.load("session-1", JsonlSessionPersistence(path))

    session = Session("session-1", JsonlSessionPersistence(path))
    session.close()
    with pytest.raises(SessionAlreadyExistsError):
        Session("session-1", JsonlSessionPersistence(path))
