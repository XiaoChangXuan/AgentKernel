"""Benchmark V0.3 durable Tool WAL recovery for side effects."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from agentkernel import (
    Agent,
    AgentBudget,
    AgentState,
    DurableToolExecutor,
    EventType,
    JsonlSessionPersistence,
    OperationRecoveryClassification,
    Session,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolSchema,
    ToolEffectKind,
    ToolRegistry,
)

from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records
from benchmarks.durable_tool.fake_payment import FakePaymentService


BENCHMARK = "durable_tool"


def run() -> list[BenchmarkRecord]:
    return [_ordinary_tool(), _agentkernel_wal()]


def _ordinary_tool() -> BenchmarkRecord:
    service = FakePaymentService()
    timer = Timer()
    service.charge(
        request_id="plain-request-1",
        invoice_id="invoice-001",
        amount_cents=4200,
    )
    service.charge(
        request_id="plain-request-2",
        invoice_id="invoice-001",
        amount_cents=4200,
    )
    duplicate = service.count_invoice_charges("invoice-001") > 1
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="payment_success_then_crash",
        strategy="ordinary_tool",
        metrics={
            "duplicate_execution": duplicate,
            "recovery_result": "retried_with_new_request_id",
            "operation_count": service.operation_count,
            "latency_ms": timer.elapsed_ms(),
            "success": not duplicate,
        },
    )


def _agentkernel_wal() -> BenchmarkRecord:
    service = FakePaymentService()
    timer = Timer()
    with tempfile.TemporaryDirectory(prefix="agentkernel-durable-tool-bench-") as root:
        path = Path(root) / "session.jsonl"
        session = Session("bench-session", JsonlSessionPersistence(path))
        agent = Agent.create(
            agent_id="bench-agent",
            session=session,
            capabilities={"payment.charge"},
            capability_bounding_set={"payment.charge"},
            budget=AgentBudget(),
        )
        call = ToolCall(
            "call-payment-1",
            "payment.charge",
            {"invoice_id": "invoice-001", "amount_cents": 4200},
        )
        operation_id = "op_payment_001"
        _append_payment_prefix(session, call, operation_id)
        service.charge(
            request_id=operation_id,
            invoice_id="invoice-001",
            amount_cents=4200,
        )
        session.close()

        restarted = Session.load("bench-session", JsonlSessionPersistence(path))
        restarted_agent = Agent(
            control=agent.control,
            session=restarted,
        )
        restarted_agent.control.transition(AgentState.RUNNING)
        analysis = restarted.recovery_analysis
        operation = analysis.durable_operations[0]
        tools = _payment_tools(service)
        executor = DurableToolExecutor(tools)
        observed = asyncio.run(
            executor.reconcile(operation, restarted_agent.control, restarted)
        )
        current = restarted.recovery_analysis.durable_operations[0]
        output = current.output
        restarted.append(
            EventType.TOOL_RESULT,
            {
                "turn": 1,
                "step": 1,
                **ToolResult.success(call, output).as_dict(),
            },
        )
        restarted.append(
            EventType.STEP_END,
            {"turn": 1, "step": 1, "outcome": "tool_calls"},
        )
        restarted.append(EventType.TURN_END, {"turn": 1, "reason": "completed"})
        restarted.flush()
        final = restarted.recovery_analysis
        restarted.close()

    duplicate = service.count_invoice_charges("invoice-001") > 1
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="payment_success_then_crash",
        strategy="agentkernel_wal",
        metrics={
            "duplicate_execution": duplicate,
            "pre_recovery_classification": operation.classification.value,
            "recovery_result": observed.status.value,
            "operation_count": service.operation_count,
            "final_session_status": final.status.value,
            "latency_ms": timer.elapsed_ms(),
            "success": (
                not duplicate
                and operation.classification
                is OperationRecoveryClassification.RECONCILE_REQUIRED
                and final.status.value == "completed"
            ),
        },
    )


def _append_payment_prefix(
    session: Session,
    call: ToolCall,
    operation_id: str,
) -> None:
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": 1, "content": "Charge invoice-001 exactly once."},
    )
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": operation_id,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
        },
    )
    session.append(
        EventType.TOOL_DISPATCH,
        {"turn": 1, "step": 1, "operation_id": operation_id, "attempt": 1},
    )
    session.flush()


def _payment_tools(service: FakePaymentService) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema(
                "payment.charge",
                "Charge a fake benchmark invoice.",
                {
                    "type": "object",
                    "properties": {
                        "invoice_id": {"type": "string"},
                        "amount_cents": {"type": "integer"},
                    },
                    "required": ["invoice_id", "amount_cents"],
                    "additionalProperties": False,
                },
            ),
            handler=service.handler,
            required_capability="payment.charge",
            effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
            reconcile_handler=service.reconcile,
        )
    )
    return registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="durable_tool.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run()
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
