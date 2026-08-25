"""Crash-prefix construction for Session replay benchmarks."""

from __future__ import annotations

from dataclasses import dataclass

from agentkernel import EventType, Session, ToolCall, ToolEffectKind, ToolResult


@dataclass(frozen=True, slots=True)
class CrashPoint:
    name: str
    event_count: int
    external_success: bool = False


CRASH_POINTS = (
    CrashPoint("after_user_message", 2),
    CrashPoint("after_step_start", 3),
    CrashPoint("after_tool_call", 5),
    CrashPoint("after_tool_dispatch", 7),
    CrashPoint("before_commit", 7, external_success=True),
    CrashPoint("after_result", 9),
)


def append_until(session: Session, point: CrashPoint) -> None:
    """Append the deterministic payment turn prefix for one crash point."""

    call = ToolCall(
        "call-payment-1",
        "payment.charge",
        {"invoice_id": "invoice-001", "amount_cents": 4200},
    )
    events: tuple[tuple[EventType, dict[str, object]], ...] = (
        (EventType.TURN_START, {"turn": 1}),
        (
            EventType.USER_MESSAGE,
            {"turn": 1, "content": "Charge invoice-001 exactly once."},
        ),
        (EventType.STEP_START, {"turn": 1, "step": 1}),
        (
            EventType.ASSISTANT_MESSAGE,
            {
                "turn": 1,
                "step": 1,
                "content": "",
                "tool_calls": [call.as_dict()],
            },
        ),
        (EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()}),
        (
            EventType.TOOL_PREPARE,
            {
                "turn": 1,
                "step": 1,
                "operation_id": "op_payment_001",
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            },
        ),
        (
            EventType.TOOL_DISPATCH,
            {"turn": 1, "step": 1, "operation_id": "op_payment_001", "attempt": 1},
        ),
        (
            EventType.TOOL_COMMIT,
            {
                "turn": 1,
                "step": 1,
                "operation_id": "op_payment_001",
                "output": {
                    "request_id": "op_payment_001",
                    "invoice_id": "invoice-001",
                    "amount_cents": 4200,
                    "status": "succeeded",
                },
            },
        ),
        (
            EventType.TOOL_RESULT,
            {
                "turn": 1,
                "step": 1,
                **ToolResult.success(
                    call,
                    {
                        "request_id": "op_payment_001",
                        "invoice_id": "invoice-001",
                        "amount_cents": 4200,
                        "status": "succeeded",
                    },
                ).as_dict(),
            },
        ),
        (
            EventType.STEP_END,
            {"turn": 1, "step": 1, "outcome": "tool_calls"},
        ),
        (EventType.TURN_END, {"turn": 1, "reason": "completed"}),
    )
    for event_type, data in events[: point.event_count]:
        session.append(event_type, data)
    session.flush()
