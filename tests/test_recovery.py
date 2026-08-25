from __future__ import annotations

import pytest

from agentkernel import (
    EventType,
    JsonlSessionPersistence,
    Session,
    SessionCorruptionError,
    SessionEvent,
    SessionPersistenceError,
    SessionStatus,
    ToolCall,
    ToolResult,
    analyze_recovery,
)


def event(seq: int, event_type: EventType, data: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=event_type, data=data, time=float(seq))


def tool_prefix() -> tuple[SessionEvent, ...]:
    call = ToolCall("call-1", "math.add", {"a": 20, "b": 22})
    result = ToolResult.success(call, 42)
    return (
        event(1, EventType.TURN_START, {"turn": 1}),
        event(
            2,
            EventType.USER_MESSAGE,
            {"turn": 1, "content": "Calculate."},
        ),
        event(3, EventType.STEP_START, {"turn": 1, "step": 1}),
        event(
            4,
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": "",
                "tool_calls": [call.as_dict()],
            },
        ),
        event(
            5,
            EventType.TOOL_CALL,
            {"turn": 1, "step": 1, **call.as_dict()},
        ),
        event(
            6,
            EventType.TOOL_RESULT,
            {"turn": 1, "step": 1, **result.as_dict()},
        ),
        event(
            7,
            EventType.STEP_END,
            {"turn": 1, "step": 1, "outcome": "tool_calls"},
        ),
    )


def test_crash_a_after_user_message() -> None:
    analysis = analyze_recovery(tool_prefix()[:2])

    assert analysis.status is SessionStatus.INTERRUPTED
    assert analysis.active_turn == 1
    assert analysis.active_step is None
    assert analysis.pending_tool_calls == ()


def test_crash_b_after_step_start() -> None:
    analysis = analyze_recovery(tool_prefix()[:3])

    assert analysis.status is SessionStatus.INTERRUPTED
    assert analysis.active_turn == 1
    assert analysis.active_step == 1
    assert analysis.pending_tool_calls == ()


def test_crash_c_after_tool_call_before_execution() -> None:
    analysis = analyze_recovery(tool_prefix()[:5])

    assert analysis.status is SessionStatus.INTERRUPTED
    assert [call.call_id for call in analysis.pending_tool_calls] == ["call-1"]
    assert analysis.has_ambiguous_tool_outcomes


def test_crash_d_after_external_effect_before_result_is_ambiguous() -> None:
    external_effects = ["effect happened outside the event log"]

    analysis = analyze_recovery(tool_prefix()[:5])

    assert external_effects
    assert analysis.status is SessionStatus.INTERRUPTED
    assert analysis.has_ambiguous_tool_outcomes
    assert analysis.completed_tool_call_ids == ()


def test_crash_e_after_tool_result_and_step_end_does_not_repeat_tool() -> None:
    analysis = analyze_recovery(tool_prefix())

    assert analysis.status is SessionStatus.INTERRUPTED
    assert analysis.active_turn == 1
    assert analysis.active_step is None
    assert analysis.pending_tool_calls == ()
    assert analysis.completed_tool_call_ids == ("call-1",)
    assert not analysis.has_ambiguous_tool_outcomes


def test_crash_f_after_final_assistant_before_turn_end() -> None:
    events = (
        event(1, EventType.TURN_START, {"turn": 1}),
        event(2, EventType.USER_MESSAGE, {"turn": 1, "content": "Hello"}),
        event(3, EventType.STEP_START, {"turn": 1, "step": 1}),
        event(
            4,
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": "Final answer",
                "tool_calls": [],
            },
        ),
    )

    analysis = analyze_recovery(events)

    assert analysis.status is SessionStatus.INTERRUPTED
    assert analysis.active_turn == 1
    assert analysis.active_step == 1
    assert analysis.has_unclosed_final_answer


def test_fully_closed_turn_is_completed() -> None:
    events = tool_prefix() + (
        event(8, EventType.STEP_START, {"turn": 1, "step": 2}),
        event(
            9,
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 2,
                "content": "42",
                "tool_calls": [],
            },
        ),
        event(
            10,
            EventType.STEP_END,
            {"turn": 1, "step": 2, "outcome": "completed"},
        ),
        event(11, EventType.TURN_END, {"turn": 1, "reason": "completed"}),
    )

    analysis = analyze_recovery(events)

    assert analysis.status is SessionStatus.COMPLETED
    assert analysis.active_turn is None
    assert analysis.last_turn_reason == "completed"


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            (
                event(1, EventType.TURN_START, {"turn": 1}),
                event(3, EventType.USER_MESSAGE, {"turn": 1, "content": "gap"}),
            ),
            "expected 2, got 3",
        ),
        (
            (event(1, EventType.TURN_END, {"turn": 1, "reason": "completed"}),),
            "requires an active turn",
        ),
        (
            (event(1, EventType.STEP_START, {"turn": 1, "step": 1}),),
            "requires an active turn",
        ),
        (
            (
                event(1, EventType.TURN_START, {"turn": 1}),
                event(2, EventType.TURN_START, {"turn": 2}),
            ),
            "another turn is active",
        ),
        (
            (
                event(1, EventType.TURN_START, {"turn": 1}),
                event(2, EventType.STEP_START, {"turn": 1, "step": 1}),
                event(
                    3,
                    EventType.TOOL_RESULT,
                    {
                        "turn": 1,
                        "step": 1,
                        "call_id": "missing",
                        "name": "math.add",
                        "ok": True,
                        "output": 42,
                    },
                ),
            ),
            "no preceding pending tool/call",
        ),
    ],
)
def test_corruption_rules_return_corrupted_analysis(events, message) -> None:
    with pytest.raises(SessionCorruptionError, match=message) as captured:
        analyze_recovery(events)

    assert captured.value.analysis.status is SessionStatus.CORRUPTED


def test_duplicate_tool_result_is_corruption() -> None:
    duplicate = tool_prefix()[:6] + (
        event(
            7,
            EventType.TOOL_RESULT,
            {
                "turn": 1,
                "step": 1,
                "call_id": "call-1",
                "name": "math.add",
                "ok": True,
                "output": 42,
            },
        ),
    )

    with pytest.raises(SessionCorruptionError, match="duplicate tool/result"):
        analyze_recovery(duplicate)


def test_tool_call_id_cannot_be_reused_in_a_later_step() -> None:
    first = tool_prefix()
    reused = ToolCall("call-1", "math.add", {"a": 1, "b": 2})
    events = first + (
        event(8, EventType.STEP_START, {"turn": 1, "step": 2}),
        event(
            9,
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 2,
                "content": "",
                "tool_calls": [reused.as_dict()],
            },
        ),
        event(
            10,
            EventType.TOOL_CALL,
            {"turn": 1, "step": 2, **reused.as_dict()},
        ),
    )

    with pytest.raises(SessionCorruptionError, match="duplicate tool/call id"):
        analyze_recovery(events)


def test_truncated_tail_is_recovered_with_warning_without_rewriting(tmp_path) -> None:
    path = tmp_path / "truncated.jsonl"
    writer = JsonlSessionPersistence(path)
    session = Session("session-1", writer)
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "Hello"})
    session.close()
    with path.open("ab") as stream:
        stream.write(b'{"record_type":"session/event","seq":3,')
    damaged = path.read_bytes()

    reloaded = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        analysis = reloaded.recovery_analysis
        assert analysis.status is SessionStatus.INTERRUPTED
        assert analysis.tail_truncated
        assert analysis.last_event_seq == 2
        assert analysis.warnings
        assert path.read_bytes() == damaged
        with pytest.raises(SessionPersistenceError, match="truncated tail"):
            reloaded.append(EventType.STEP_START, {"turn": 1, "step": 1})
    finally:
        reloaded.close()


def test_loading_interrupted_session_does_not_synthesize_closing_events(
    tmp_path,
) -> None:
    path = tmp_path / "interrupted.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "Hello"})
    session.close()
    original = path.read_bytes()

    restored = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        assert restored.recovery_analysis.status is SessionStatus.INTERRUPTED
        assert [item.type for item in restored.events] == [
            EventType.TURN_START,
            EventType.USER_MESSAGE,
        ]
    finally:
        restored.close()

    assert path.read_bytes() == original
