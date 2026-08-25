"""Pure replay validation and recovery-state analysis for V0.2 sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Mapping, NoReturn

from .events import EventType, SessionEvent
from .persistence import SessionCorruptionError
from .protocol import JsonValue, ToolCall, ToolResult


class SessionStatus(StrEnum):
    """Semantic state reconstructed from a durable event-log prefix."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CORRUPTED = "corrupted"


@dataclass(frozen=True, slots=True)
class RecoveryAnalysis:
    """Facts recovered from replay; policy decides what action follows."""

    status: SessionStatus
    active_turn: int | None
    active_step: int | None
    pending_tool_calls: tuple[ToolCall, ...]
    completed_tool_call_ids: tuple[str, ...]
    last_event_seq: int
    last_event_type: EventType | None
    last_turn_reason: str | None
    has_unclosed_final_answer: bool
    tail_truncated: bool
    warnings: tuple[str, ...] = ()
    corruption: str | None = None

    @property
    def has_ambiguous_tool_outcomes(self) -> bool:
        """Whether a dispatched Tool lacks a durable result at this prefix."""

        return bool(self.pending_tool_calls)


def analyze_recovery(
    events: tuple[SessionEvent, ...],
    *,
    tail_truncated: bool = False,
) -> RecoveryAnalysis:
    """Validate one complete event prefix and report its recovery position."""

    active_turn: int | None = None
    active_step: int | None = None
    next_turn = 1
    next_step = 1
    assistant_calls: dict[str, ToolCall] = {}
    dispatched_calls: set[str] = set()
    seen_call_ids: set[str] = set()
    pending_calls: dict[str, ToolCall] = {}
    completed_calls: list[str] = []
    assistant_seen = False
    has_unclosed_final_answer = False
    last_turn_reason: str | None = None
    last_type: EventType | None = None
    last_seq = 0

    def fail(message: str) -> NoReturn:
        analysis = RecoveryAnalysis(
            status=SessionStatus.CORRUPTED,
            active_turn=active_turn,
            active_step=active_step,
            pending_tool_calls=tuple(pending_calls.values()),
            completed_tool_call_ids=tuple(completed_calls),
            last_event_seq=last_seq,
            last_event_type=last_type,
            last_turn_reason=last_turn_reason,
            has_unclosed_final_answer=has_unclosed_final_answer,
            tail_truncated=tail_truncated,
            corruption=message,
        )
        raise SessionCorruptionError(message, analysis=analysis)

    for expected_seq, event in enumerate(events, start=1):
        if event.seq != expected_seq:
            fail(
                f"session event seq must be contiguous from 1; "
                f"expected {expected_seq}, got {event.seq}"
            )
        last_seq = event.seq
        last_type = event.type
        data = event.data

        if event.type is EventType.TURN_START:
            if active_turn is not None:
                fail("turn/start cannot occur while another turn is active")
            turn = _positive_int(data, "turn", fail)
            if turn != next_turn:
                fail(f"expected turn {next_turn}, got {turn}")
            active_turn = turn
            active_step = None
            next_step = 1
            has_unclosed_final_answer = False
            continue

        if event.type is EventType.USER_MESSAGE:
            _require_turn(data, active_turn, fail)
            if not isinstance(data.get("content"), str):
                fail("user/message content must be a string")
            continue

        if event.type is EventType.STEP_START:
            _require_turn(data, active_turn, fail)
            if active_step is not None:
                fail("step/start cannot occur while another step is active")
            step = _positive_int(data, "step", fail)
            if step != next_step:
                fail(f"expected step {next_step}, got {step}")
            active_step = step
            assistant_calls = {}
            dispatched_calls = set()
            assistant_seen = False
            has_unclosed_final_answer = False
            continue

        if event.type is EventType.ASSISTANT_MESSAGE:
            _require_step(data, active_turn, active_step, fail)
            if assistant_seen:
                fail("a step cannot contain multiple assistant/message events")
            if not isinstance(data.get("content", ""), str):
                fail("assistant/message content must be a string")
            raw_calls = data.get("tool_calls", [])
            if not isinstance(raw_calls, list):
                fail("assistant/message tool_calls must be a list")
            parsed: dict[str, ToolCall] = {}
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    fail("assistant/message tool call must be an object")
                try:
                    call = ToolCall.from_dict(raw_call)
                except (KeyError, TypeError, ValueError) as error:
                    fail(f"invalid assistant tool call: {error}")
                if call.call_id in parsed:
                    fail(f"duplicate assistant tool call id: {call.call_id}")
                parsed[call.call_id] = call
            assistant_calls = parsed
            assistant_seen = True
            has_unclosed_final_answer = not assistant_calls
            continue

        if event.type is EventType.TOOL_CALL:
            _require_step(data, active_turn, active_step, fail)
            if not assistant_seen:
                fail("tool/call must follow assistant/message in the same step")
            try:
                call = ToolCall.from_dict(data)
            except (KeyError, TypeError, ValueError) as error:
                fail(f"invalid tool/call: {error}")
            announced = assistant_calls.get(call.call_id)
            if announced is None:
                fail(f"tool/call {call.call_id!r} was not announced by the assistant")
            if announced != call:
                fail(f"tool/call {call.call_id!r} differs from assistant declaration")
            if call.call_id in dispatched_calls:
                fail(f"duplicate tool/call id: {call.call_id}")
            if call.call_id in seen_call_ids:
                fail(f"duplicate tool/call id across session: {call.call_id}")
            dispatched_calls.add(call.call_id)
            seen_call_ids.add(call.call_id)
            pending_calls[call.call_id] = call
            continue

        if event.type is EventType.TOOL_RESULT:
            _require_step(data, active_turn, active_step, fail)
            call_id = data.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                fail("tool/result call_id must be a non-empty string")
            call = pending_calls.get(call_id)
            if call is None:
                if call_id in completed_calls:
                    fail(f"duplicate tool/result for call id: {call_id}")
                fail(f"tool/result has no preceding pending tool/call: {call_id}")
            if not isinstance(data.get("ok"), bool):
                fail("tool/result ok must be a boolean")
            try:
                result = ToolResult.from_dict(data)
            except (KeyError, TypeError, ValueError) as error:
                fail(f"invalid tool/result: {error}")
            if result.name != call.name:
                fail(f"tool/result name does not match tool/call {call_id!r}")
            del pending_calls[call_id]
            completed_calls.append(call_id)
            continue

        if event.type is EventType.STEP_END:
            _require_step(data, active_turn, active_step, fail)
            if pending_calls:
                fail("step/end cannot close a step with pending tool calls")
            active_step = None
            next_step += 1
            assistant_calls = {}
            dispatched_calls = set()
            assistant_seen = False
            has_unclosed_final_answer = False
            continue

        if event.type is EventType.TURN_END:
            _require_turn(data, active_turn, fail)
            if active_step is not None:
                fail("turn/end cannot occur while a step is active")
            if pending_calls:
                fail("turn/end cannot occur with pending tool calls")
            reason = data.get("reason")
            if not isinstance(reason, str) or not reason:
                fail("turn/end reason must be a non-empty string")
            last_turn_reason = reason
            active_turn = None
            next_turn += 1
            continue

        fail(f"unknown required session event type: {event.type}")

    warnings = (
        ("truncated final JSONL record ignored; source artifact was not modified",)
        if tail_truncated
        else ()
    )
    interrupted = (
        tail_truncated
        or active_turn is not None
        or active_step is not None
        or bool(pending_calls)
    )
    return RecoveryAnalysis(
        status=SessionStatus.INTERRUPTED if interrupted else SessionStatus.COMPLETED,
        active_turn=active_turn,
        active_step=active_step,
        pending_tool_calls=tuple(pending_calls.values()),
        completed_tool_call_ids=tuple(completed_calls),
        last_event_seq=last_seq,
        last_event_type=last_type,
        last_turn_reason=last_turn_reason,
        has_unclosed_final_answer=has_unclosed_final_answer,
        tail_truncated=tail_truncated,
        warnings=warnings,
    )


def _positive_int(
    data: Mapping[str, JsonValue],
    name: str,
    fail: Callable[[str], NoReturn],
) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        fail(f"event {name} must be a positive integer")
    return value


def _require_turn(
    data: Mapping[str, JsonValue],
    active_turn: int | None,
    fail: Callable[[str], NoReturn],
) -> None:
    if active_turn is None:
        fail("event requires an active turn")
    turn = _positive_int(data, "turn", fail)
    if turn != active_turn:
        fail(f"event turn {turn} does not match active turn {active_turn}")


def _require_step(
    data: Mapping[str, JsonValue],
    active_turn: int | None,
    active_step: int | None,
    fail: Callable[[str], NoReturn],
) -> None:
    _require_turn(data, active_turn, fail)
    if active_step is None:
        fail("event requires an active step")
    step = _positive_int(data, "step", fail)
    if step != active_step:
        fail(f"event step {step} does not match active step {active_step}")
