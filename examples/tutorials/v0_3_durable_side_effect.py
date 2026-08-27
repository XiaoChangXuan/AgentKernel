"""V0.3 tutorial: reconcile a durable side effect after a crash.

Run from the repository root:

    python examples/tutorials/v0_3_durable_side_effect.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentkernel import (  # noqa: E402
    Agent,
    DurableToolExecutor,
    EventType,
    JsonlSessionPersistence,
    OperationRecoveryClassification,
    ReconcileResult,
    ReconcileStatus,
    Session,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue  # noqa: E402


class FakePaymentService:
    def __init__(self) -> None:
        self.effects: dict[str, JsonValue] = {}
        self.external_effect_count = 0

    async def charge(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        existing = self.effects.get(context.operation_id)
        if existing is not None:
            return existing
        self.external_effect_count += 1
        output: JsonValue = {
            "payment_id": f"pay-{self.external_effect_count}",
            "amount": arguments["amount"],
            "currency": arguments["currency"],
        }
        self.effects[context.operation_id] = output
        return output

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        output = self.effects.get(context.operation_id)
        if output is None:
            return ReconcileResult(ReconcileStatus.NOT_FOUND)
        return ReconcileResult(ReconcileStatus.SUCCEEDED, output=output)


def payment_registry(service: FakePaymentService) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                "payment.charge",
                "Charge a fake payment.",
                {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                        "currency": {"type": "string"},
                    },
                    "required": ["amount", "currency"],
                },
            ),
            handler=service.charge,
            required_capability="payment.charge",
            effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
            reconcile_handler=service.reconcile,
        )
    )
    return tools


def authorization_context() -> dict[str, JsonValue]:
    return {
        "agent_id": "tutorial-agent",
        "action": "tool.execute",
        "resource_scope": "tool://payment.charge",
        "reason": "allowed",
        "matched_grant": {
            "subject": "tutorial-agent",
            "action": "tool.execute",
            "resource_scope": "tool://payment.charge",
        },
    }


def append_dispatched_prefix(session: Session, call: ToolCall) -> None:
    auth = authorization_context()
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": 1, "content": "Charge the fake invoice."},
    )
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            **auth,
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "payment-op-1",
            "boundary": "prepare",
        },
    )
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "payment-op-1",
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            "authorization": auth,
        },
    )
    session.flush()
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            **auth,
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "payment-op-1",
            "boundary": "dispatch",
        },
    )
    session.append(
        EventType.TOOL_DISPATCH,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "payment-op-1",
            "attempt": 1,
            "authorization": auth,
        },
    )
    session.flush()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agentkernel-v0-3-") as directory:
        path = Path(directory) / "payment-session.jsonl"
        service = FakePaymentService()
        call = ToolCall(
            "model-call-payment-1",
            "payment.charge",
            {"amount": 42, "currency": "USD"},
        )

        session = Session("tutorial-v0-3-session", JsonlSessionPersistence(path))
        append_dispatched_prefix(session, call)
        await service.charge(
            call.arguments,
            ToolExecutionContext(
                agent_id="tutorial-agent",
                session_id=session.session_id,
                tool_call_id=call.call_id,
                operation_id="payment-op-1",
            ),
        )
        session.close()

        restored = Session.load(
            "tutorial-v0-3-session",
            JsonlSessionPersistence(path),
        )
        try:
            agent = Agent.create(
                agent_id="tutorial-agent",
                session=restored,
                capabilities={"payment.charge"},
            )
            operation = restored.recovery_analysis.durable_operations[0]
            assert (
                operation.classification
                is OperationRecoveryClassification.RECONCILE_REQUIRED
            )

            observed = await DurableToolExecutor(
                payment_registry(service)
            ).reconcile(operation, agent.control, restored)

            final = restored.recovery_analysis.durable_operations[0]
            print("V0.3 Durable Tool WAL")
            print("crash_point=after_dispatch_before_commit")
            print(f"pre_recovery_classification={operation.classification.value}")
            print(f"reconcile_status={observed.status.value}")
            print(f"final_classification={final.classification.value}")
            print(f"external_effect_count={service.external_effect_count}")
            print(f"committed={final.committed}")
            print()
            print("本实验验证什么 / WHAT THIS DEMONSTRATES")
            print("- WAL records prepare and dispatch before local completion.")
            print("- Crash after dispatch is classified as reconcile_required.")
            print("- Reconciliation commits the observed fake external effect once.")
            print()
            print("本实验不证明什么 / WHAT THIS DOES NOT DEMONSTRATE")
            print("- It does not use a real payment provider.")
            print("- It does not prove universal exactly-once semantics.")
            print("- It does not prove arbitrary external-system atomicity.")
        finally:
            restored.close()


if __name__ == "__main__":
    asyncio.run(main())
