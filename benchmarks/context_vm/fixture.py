"""1000-turn deterministic Context VM benchmark fixture."""

from __future__ import annotations

from dataclasses import dataclass

from agentkernel import EventType, Session, ToolCall, ToolResult


EARLY_CONSTRAINT = "EARLY_CONSTRAINT: never edit production.yaml."
MIDDLE_DECISION = "MIDDLE_DECISION: the database is healthy."
LARGE_TOOL_TAIL = "LARGE_TOOL_TAIL: root cause is permission denied."
RECENT_TASK = "RECENT_TASK: answer from early, middle, and tool-tail facts."
SYSTEM_PROMPT = "You are evaluating runtime context retention. Return concise facts."


@dataclass(frozen=True, slots=True)
class ContextFixture:
    session: Session
    markers: tuple[str, ...]


def build_context_fixture(turns: int = 1000) -> ContextFixture:
    if turns < 10:
        raise ValueError("context benchmark requires at least 10 turns")
    session = Session("bench-context-vm")
    middle_turn = turns // 2
    tool_turn = turns - 100
    for turn in range(1, turns + 1):
        if turn == 1:
            _append_text_turn(session, turn, EARLY_CONSTRAINT)
        elif turn == middle_turn:
            _append_text_turn(session, turn, MIDDLE_DECISION)
        elif turn == tool_turn:
            _append_large_tool_turn(session, turn)
        elif turn == turns:
            _append_text_turn(session, turn, RECENT_TASK)
        else:
            _append_text_turn(
                session,
                turn,
                f"routine turn {turn}: diagnostic noise " + ("n" * 80),
            )
    return ContextFixture(
        session=session,
        markers=(EARLY_CONSTRAINT, MIDDLE_DECISION, LARGE_TOOL_TAIL, RECENT_TASK),
    )


def durable_summary() -> str:
    return " ".join((EARLY_CONSTRAINT, MIDDLE_DECISION, LARGE_TOOL_TAIL, RECENT_TASK))


def _append_text_turn(session: Session, turn: int, user_text: str) -> None:
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": user_text})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {
            "turn": turn,
            "step": 1,
            "content": f"ack {turn}: " + ("a" * 60),
            "tool_calls": [],
        },
    )
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "completed"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})


def _append_large_tool_turn(session: Session, turn: int) -> None:
    call = ToolCall(f"call-log-{turn}", "logs.read", {"path": "service.log"})
    output = "LOG BEGIN\n" + ("INFO request completed\n" * 40_000) + LARGE_TOOL_TAIL
    result = ToolResult.success(call, output)
    session.append(EventType.TURN_START, {"turn": turn})
    session.append(EventType.USER_MESSAGE, {"turn": turn, "content": "Read service.log."})
    session.append(EventType.STEP_START, {"turn": turn, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": turn, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": turn, "step": 1, **call.as_dict()})
    session.append(EventType.TOOL_RESULT, {"turn": turn, "step": 1, **result.as_dict()})
    session.append(
        EventType.STEP_END,
        {"turn": turn, "step": 1, "outcome": "tool_calls"},
    )
    session.append(EventType.TURN_END, {"turn": turn, "reason": "completed"})
