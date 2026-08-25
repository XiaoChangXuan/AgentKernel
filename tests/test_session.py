from __future__ import annotations

import json

import pytest

from agentkernel import EventType, MessageRole, Session, ToolCall, ToolResult


def test_event_log_derives_model_history() -> None:
    session = Session("session-1")
    call = ToolCall(
        call_id="call-1",
        name="math.add",
        arguments={"left": 20, "right": 22},
    )
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

    messages = session.derive_messages()

    assert [message.role for message in messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    assert messages[0].content == "What is 20 + 22?"
    assert messages[1].tool_calls == (call,)
    assert json.loads(messages[2].content)["output"] == 42
    assert [event.seq for event in session.events] == list(range(1, 7))


def test_appended_data_and_returned_events_are_detached() -> None:
    session = Session("session-1")
    payload = {"turn": 1, "content": "original"}
    session.append(EventType.USER_MESSAGE, payload)
    payload["content"] = "mutated"

    returned = session.events[0]
    returned.data["content"] = "also mutated"  # type: ignore[index]

    assert session.derive_messages()[0].content == "original"


def test_derived_nested_tool_arguments_cannot_mutate_the_event_log() -> None:
    session = Session("session-1")
    call = ToolCall(
        "call-1",
        "nested",
        {"payload": {"items": [1, 2]}},
    )
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"content": "", "tool_calls": [call.as_dict()]},
    )

    projected = session.derive_messages()[0].tool_calls[0]
    projected.arguments["payload"]["items"].append(3)  # type: ignore[index,union-attr]

    reprojected = session.derive_messages()[0].tool_calls[0]
    assert reprojected.arguments == {"payload": {"items": [1, 2]}}


def test_session_rejects_values_json_would_coerce() -> None:
    session = Session("session-1")

    with pytest.raises(TypeError, match="lossless JSON"):
        session.append(
            EventType.USER_MESSAGE,
            {"content": ("tuple", "would", "become", "array")},  # type: ignore[dict-item]
        )
