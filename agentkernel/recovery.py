"""Pure replay validation and recovery-state analysis for durable sessions."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Mapping, NoReturn

from .events import EventType, SessionEvent
from .persistence import SessionCorruptionError
from .protocol import ErrorCode, JsonValue, ToolCall, ToolResult
from .tool_effects import ReconcileStatus, ToolEffectKind


class SessionStatus(StrEnum):
    """Semantic state reconstructed from a durable event-log prefix."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CORRUPTED = "corrupted"


class OperationRecoveryClassification(StrEnum):
    """Mechanism-level recovery facts; callers still choose policy."""

    SAFE_TO_RETRY = "safe_to_retry"
    IDEMPOTENT_RETRY_ALLOWED = "idempotent_retry_allowed"
    RECONCILE_REQUIRED = "reconcile_required"
    COMPLETED = "completed"
    MANUAL_REQUIRED = "manual_required"


@dataclass(frozen=True, slots=True)
class DurableOperationRecovery:
    """Reconstructed state for one Kernel-owned external operation."""

    operation_id: str
    tool_call: ToolCall
    effect_kind: ToolEffectKind
    turn: int
    step: int
    dispatch_attempts: int
    classification: OperationRecoveryClassification
    committed: bool
    aborted: bool
    result_persisted: bool
    last_reconcile_status: ReconcileStatus | None = None
    output: JsonValue = None
    error_code: ErrorCode | None = None


@dataclass(slots=True)
class _OperationState:
    operation_id: str
    tool_call: ToolCall
    effect_kind: ToolEffectKind
    turn: int
    step: int
    dispatch_attempts: int = 0
    committed: bool = False
    aborted: bool = False
    result_persisted: bool = False
    last_reconcile_status: ReconcileStatus | None = None
    output: JsonValue = None
    error_code: ErrorCode | None = None


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
    durable_operations: tuple[DurableOperationRecovery, ...] = ()
    warnings: tuple[str, ...] = ()
    corruption: str | None = None

    @property
    def has_ambiguous_tool_outcomes(self) -> bool:
        """Whether a dispatched Tool lacks a durable result at this prefix."""

        completed_mutation_calls = {
            operation.tool_call.call_id
            for operation in self.durable_operations
            if operation.classification
            is OperationRecoveryClassification.COMPLETED
        }
        return any(
            call.call_id not in completed_mutation_calls
            for call in self.pending_tool_calls
        ) or any(
            operation.dispatch_attempts > 0
            and operation.classification
            is not OperationRecoveryClassification.COMPLETED
            for operation in self.durable_operations
        )


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
    operations: dict[str, _OperationState] = {}
    operation_by_call: dict[str, str] = {}
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
            durable_operations=_freeze_operations(operations),
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

        if event.type is EventType.TOOL_PREPARE:
            _require_step(data, active_turn, active_step, fail)
            operation_id = _non_empty_string(data, "operation_id", fail)
            tool_call_id = _non_empty_string(data, "tool_call_id", fail)
            tool_name = _non_empty_string(data, "tool_name", fail)
            call = pending_calls.get(tool_call_id)
            if call is None:
                fail("tool/prepare must reference a pending tool/call")
            if call.name != tool_name:
                fail("tool/prepare tool_name must match its tool/call")
            if operation_id in operations:
                fail(f"duplicate operation_id: {operation_id}")
            if tool_call_id in operation_by_call:
                fail(f"tool/call already has a prepared operation: {tool_call_id}")
            try:
                effect_kind = ToolEffectKind(
                    _non_empty_string(data, "effect_kind", fail)
                )
            except ValueError as error:
                fail(f"invalid tool/prepare effect_kind: {error}")
            if effect_kind is ToolEffectKind.READ_ONLY:
                fail("READ_ONLY tools must not create durable prepare records")
            assert active_turn is not None
            assert active_step is not None
            operations[operation_id] = _OperationState(
                operation_id=operation_id,
                tool_call=call,
                effect_kind=effect_kind,
                turn=active_turn,
                step=active_step,
            )
            operation_by_call[tool_call_id] = operation_id
            continue

        if event.type is EventType.TOOL_DISPATCH:
            _require_step(data, active_turn, active_step, fail)
            state = _operation_for_event(data, operations, fail)
            _require_operation_step(state, active_turn, active_step, fail)
            if state.committed:
                fail("tool/dispatch cannot follow tool/commit")
            attempt = _positive_int(data, "attempt", fail)
            if attempt != state.dispatch_attempts + 1:
                fail(
                    f"tool/dispatch attempt must be {state.dispatch_attempts + 1}, "
                    f"got {attempt}"
                )
            if state.dispatch_attempts > 0 and not (
                state.effect_kind is ToolEffectKind.IDEMPOTENT_MUTATION
                or state.last_reconcile_status is ReconcileStatus.NOT_FOUND
            ):
                fail("re-dispatch is not allowed by the durable operation state")
            state.dispatch_attempts = attempt
            state.aborted = False
            state.error_code = None
            state.last_reconcile_status = None
            continue

        if event.type is EventType.TOOL_COMMIT:
            _require_step(data, active_turn, active_step, fail)
            state = _operation_for_event(data, operations, fail)
            _require_operation_step(state, active_turn, active_step, fail)
            if state.committed:
                fail("duplicate tool/commit")
            if (
                state.dispatch_attempts == 0
                and state.last_reconcile_status is not ReconcileStatus.SUCCEEDED
            ):
                fail("tool/commit requires dispatch or successful reconciliation")
            if "output" not in data:
                fail("tool/commit must contain durable output")
            state.output = copy.deepcopy(data["output"])
            state.committed = True
            state.aborted = False
            state.error_code = None
            continue

        if event.type is EventType.TOOL_ABORT:
            _require_step(data, active_turn, active_step, fail)
            state = _operation_for_event(data, operations, fail)
            _require_operation_step(state, active_turn, active_step, fail)
            if state.committed:
                fail("tool/abort cannot follow tool/commit")
            if state.dispatch_attempts == 0:
                fail("tool/abort requires a prior dispatch")
            try:
                error_code = ErrorCode(
                    _non_empty_string(data, "error_code", fail)
                )
            except ValueError as error:
                fail(f"invalid tool/abort error_code: {error}")
            _non_empty_string(data, "message", fail)
            state.aborted = True
            state.error_code = error_code
            continue

        if event.type is EventType.TOOL_RECONCILE:
            _require_step(data, active_turn, active_step, fail)
            state = _operation_for_event(data, operations, fail)
            _require_operation_step(state, active_turn, active_step, fail)
            if state.effect_kind is not ToolEffectKind.RECONCILABLE_MUTATION:
                fail("tool/reconcile requires RECONCILABLE_MUTATION")
            if state.dispatch_attempts == 0:
                fail("tool/reconcile requires a prior dispatch")
            if state.committed:
                fail("tool/reconcile cannot follow tool/commit")
            try:
                observed = ReconcileStatus(
                    _non_empty_string(data, "observed_status", fail)
                )
            except ValueError as error:
                fail(f"invalid tool/reconcile observed_status: {error}")
            if observed is ReconcileStatus.SUCCEEDED:
                if "output" not in data:
                    fail("successful tool/reconcile must contain output")
                state.output = copy.deepcopy(data["output"])
            if "message" in data and not isinstance(data["message"], str):
                fail("tool/reconcile message must be a string")
            state.last_reconcile_status = observed
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
            operation_id = operation_by_call.get(call_id)
            if operation_id is not None:
                state = operations[operation_id]
                if result.ok:
                    if not (
                        state.committed
                        or state.last_reconcile_status is ReconcileStatus.SUCCEEDED
                    ):
                        fail("successful mutation tool/result requires tool/commit")
                    if result.output != state.output:
                        fail("tool/result output differs from durable operation output")
                elif not (
                    state.aborted
                    or state.last_reconcile_status is ReconcileStatus.FAILED
                ):
                    fail("failed mutation tool/result requires tool/abort")
                state.result_persisted = True
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
    durable_operations = _freeze_operations(operations)
    unresolved_operations = any(
        operation.classification
        is not OperationRecoveryClassification.COMPLETED
        for operation in durable_operations
    )
    interrupted = (
        tail_truncated
        or active_turn is not None
        or active_step is not None
        or bool(pending_calls)
        or unresolved_operations
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
        durable_operations=durable_operations,
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


def _non_empty_string(
    data: Mapping[str, JsonValue],
    name: str,
    fail: Callable[[str], NoReturn],
) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        fail(f"event {name} must be a non-empty string")
    return value


def _operation_for_event(
    data: Mapping[str, JsonValue],
    operations: Mapping[str, _OperationState],
    fail: Callable[[str], NoReturn],
) -> _OperationState:
    operation_id = _non_empty_string(data, "operation_id", fail)
    state = operations.get(operation_id)
    if state is None:
        fail(f"WAL event references unknown operation_id: {operation_id}")
    return state


def _require_operation_step(
    state: _OperationState,
    active_turn: int | None,
    active_step: int | None,
    fail: Callable[[str], NoReturn],
) -> None:
    if state.turn != active_turn or state.step != active_step:
        fail("WAL event must remain in its prepared Turn and Step")


def _classify_operation(
    state: _OperationState,
) -> OperationRecoveryClassification:
    if state.committed or state.last_reconcile_status in {
        ReconcileStatus.SUCCEEDED,
        ReconcileStatus.FAILED,
    }:
        return OperationRecoveryClassification.COMPLETED
    if state.dispatch_attempts == 0:
        return OperationRecoveryClassification.SAFE_TO_RETRY
    if state.last_reconcile_status is ReconcileStatus.NOT_FOUND:
        return OperationRecoveryClassification.SAFE_TO_RETRY
    if state.effect_kind is ToolEffectKind.IDEMPOTENT_MUTATION:
        return OperationRecoveryClassification.IDEMPOTENT_RETRY_ALLOWED
    if state.effect_kind is ToolEffectKind.RECONCILABLE_MUTATION:
        return OperationRecoveryClassification.RECONCILE_REQUIRED
    return OperationRecoveryClassification.MANUAL_REQUIRED


def _freeze_operations(
    operations: Mapping[str, _OperationState],
) -> tuple[DurableOperationRecovery, ...]:
    return tuple(
        DurableOperationRecovery(
            operation_id=state.operation_id,
            tool_call=copy.deepcopy(state.tool_call),
            effect_kind=state.effect_kind,
            turn=state.turn,
            step=state.step,
            dispatch_attempts=state.dispatch_attempts,
            classification=_classify_operation(state),
            committed=state.committed,
            aborted=state.aborted,
            result_persisted=state.result_persisted,
            last_reconcile_status=state.last_reconcile_status,
            output=copy.deepcopy(state.output),
            error_code=state.error_code,
        )
        for state in operations.values()
    )


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
