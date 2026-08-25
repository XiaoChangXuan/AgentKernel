from __future__ import annotations

from agentkernel import EventType, Session, ToolCall, ToolEffectKind, ToolResult


def append_text_turn(
    session: Session,
    turn: int,
    user_content: str,
    assistant_content: str,
) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": turn, "content": user_content},
    )
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": turn,
            "step": 1,
            "content": assistant_content,
            "tool_calls": [],
        },
    )
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "completed"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def append_tool_turn(
    session: Session,
    turn: int,
    *,
    user_content: str = "Run tool.",
    output: str = "done",
    mutation_wal: bool = False,
) -> tuple[ToolCall, ToolResult]:
    call = ToolCall(f"call-{turn}", "demo.run", {"turn": turn})
    result = ToolResult.success(call, output)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": turn, "content": user_content},
    )
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": turn,
            "step": 1,
            "content": "",
            "tool_calls": [call.as_dict()],
        },
    )
    session.append(
        EventType.TOOL_CALL,
        {"turn": turn, "step": 1, **call.as_dict()},
    )
    if mutation_wal:
        operation_id = f"operation-{turn}"
        session.append(
            EventType.TOOL_PREPARE,
            {
                "turn": turn,
                "step": 1,
                "operation_id": operation_id,
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "effect_kind": ToolEffectKind.IDEMPOTENT_MUTATION.value,
            },
        )
        session.append(
            EventType.TOOL_DISPATCH,
            {
                "turn": turn,
                "step": 1,
                "operation_id": operation_id,
                "attempt": 1,
            },
        )
        session.append(
            EventType.TOOL_COMMIT,
            {
                "turn": turn,
                "step": 1,
                "operation_id": operation_id,
                "output": output,
            },
        )
    session.append(
        EventType.TOOL_RESULT,
        {"turn": turn, "step": 1, **result.as_dict()},
    )
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "tool_calls"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})
    return call, result
