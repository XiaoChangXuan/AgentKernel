from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agentkernel import (
    Agent,
    DurableToolExecutionError,
    DurableToolExecutor,
    EventType,
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
from agentkernel.protocol import JsonValue


class FakeShipmentService:
    def __init__(self) -> None:
        self.shipments: dict[str, JsonValue] = {}
        self.status_overrides: dict[str, ReconcileStatus] = {}
        self.actual_execution_count = 0
        self.reconciled_operation_ids: list[str] = []

    async def dispatch(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        existing = self.shipments.get(context.operation_id)
        if existing is not None:
            return existing
        self.actual_execution_count += 1
        output: JsonValue = {
            "shipment_id": f"shipment-{self.actual_execution_count}",
            "address": arguments["address"],
        }
        self.shipments[context.operation_id] = output
        return output

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        self.reconciled_operation_ids.append(context.operation_id)
        override = self.status_overrides.get(context.operation_id)
        if override is not None:
            return ReconcileResult(override, message=f"observed {override.value}")
        output = self.shipments.get(context.operation_id)
        if output is None:
            return ReconcileResult(ReconcileStatus.NOT_FOUND)
        return ReconcileResult(ReconcileStatus.SUCCEEDED, output=output)


def registry(service: FakeShipmentService) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema("shipping.dispatch", "Ship.", {"type": "object"}),
            handler=service.dispatch,
            required_capability="shipping.dispatch",
            effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
            reconcile_handler=service.reconcile,
        )
    )
    return tools


def dispatched_session() -> tuple[Session, ToolCall]:
    session = Session("session-1")
    call = ToolCall(
        "model-call-1",
        "shipping.dispatch",
        {"address": "Shanghai"},
    )
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "Ship it."})
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
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "kernel-op-1",
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
        },
    )
    session.flush()
    session.append(
        EventType.TOOL_DISPATCH,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "kernel-op-1",
            "attempt": 1,
        },
    )
    session.flush()
    return session, call


def agent_for(session: Session) -> Agent:
    return Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"shipping.dispatch"},
    )


def external_context(call: ToolCall) -> ToolExecutionContext:
    return ToolExecutionContext(
        agent_id="agent-1",
        session_id="session-1",
        tool_call_id=call.call_id,
        operation_id="kernel-op-1",
    )


def test_successful_reconciliation_commits_without_duplicate_dispatch() -> None:
    service = FakeShipmentService()
    tools = registry(service)
    session, call = dispatched_session()
    agent = agent_for(session)
    output = asyncio.run(service.dispatch(call.arguments, external_context(call)))
    assert service.actual_execution_count == 1
    operation = session.recovery_analysis.durable_operations[0]
    assert operation.classification is OperationRecoveryClassification.RECONCILE_REQUIRED

    observed = asyncio.run(
        DurableToolExecutor(tools).reconcile(operation, agent.control, session)
    )

    assert observed.status is ReconcileStatus.SUCCEEDED
    assert observed.output == output
    assert service.actual_execution_count == 1
    assert service.reconciled_operation_ids == ["kernel-op-1"]
    recovered = session.recovery_analysis.durable_operations[0]
    assert recovered.classification is OperationRecoveryClassification.COMPLETED
    assert recovered.committed
    assert recovered.output == output
    assert [
        event.type
        for event in session.events
        if event.type in {EventType.TOOL_RECONCILE, EventType.TOOL_COMMIT}
    ] == [EventType.TOOL_RECONCILE, EventType.TOOL_COMMIT]


@pytest.mark.parametrize(
    "status",
    [ReconcileStatus.IN_PROGRESS, ReconcileStatus.UNKNOWN],
)
def test_nonterminal_reconciliation_remains_reconcile_required(
    status: ReconcileStatus,
) -> None:
    service = FakeShipmentService()
    service.status_overrides["kernel-op-1"] = status
    tools = registry(service)
    session, _ = dispatched_session()
    agent = agent_for(session)
    operation = session.recovery_analysis.durable_operations[0]

    observed = asyncio.run(
        DurableToolExecutor(tools).reconcile(operation, agent.control, session)
    )

    assert observed.status is status
    recovered = session.recovery_analysis.durable_operations[0]
    assert recovered.last_reconcile_status is status
    assert recovered.classification is OperationRecoveryClassification.RECONCILE_REQUIRED
    assert not recovered.committed


def test_failed_reconciliation_records_abort_and_completes_known_failure() -> None:
    service = FakeShipmentService()
    service.status_overrides["kernel-op-1"] = ReconcileStatus.FAILED
    tools = registry(service)
    session, _ = dispatched_session()
    agent = agent_for(session)
    operation = session.recovery_analysis.durable_operations[0]

    observed = asyncio.run(
        DurableToolExecutor(tools).reconcile(operation, agent.control, session)
    )

    assert observed.status is ReconcileStatus.FAILED
    recovered = session.recovery_analysis.durable_operations[0]
    assert recovered.classification is OperationRecoveryClassification.COMPLETED
    assert recovered.aborted
    assert session.events[-1].type is EventType.TOOL_ABORT


def test_not_found_makes_redispatch_safe_with_same_operation_identity() -> None:
    service = FakeShipmentService()
    tools = registry(service)
    session, _ = dispatched_session()
    agent = agent_for(session)
    operation = session.recovery_analysis.durable_operations[0]

    observed = asyncio.run(
        DurableToolExecutor(tools).reconcile(operation, agent.control, session)
    )
    assert observed.status is ReconcileStatus.NOT_FOUND
    safe = session.recovery_analysis.durable_operations[0]
    assert safe.classification is OperationRecoveryClassification.SAFE_TO_RETRY

    result = asyncio.run(
        DurableToolExecutor(tools).retry(safe, agent.control, session)
    )

    assert result.ok
    assert service.actual_execution_count == 1
    assert session.recovery_analysis.durable_operations[0].committed
    assert {
        event.data["operation_id"]
        for event in session.events
        if "operation_id" in event.data
    } == {"kernel-op-1"}


def test_reconcile_rejects_operations_without_reconcile_required_state() -> None:
    service = FakeShipmentService()
    tools = registry(service)
    session, _ = dispatched_session()
    agent = agent_for(session)
    session.append(
        EventType.TOOL_COMMIT,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "kernel-op-1",
            "output": {"shipment_id": "already-done"},
        },
    )
    operation = session.recovery_analysis.durable_operations[0]

    with pytest.raises(DurableToolExecutionError, match="does not require"):
        asyncio.run(
            DurableToolExecutor(tools).reconcile(
                operation,
                agent.control,
                session,
            )
        )
