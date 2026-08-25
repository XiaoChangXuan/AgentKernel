from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agentkernel import (
    Agent,
    DurableToolExecutionError,
    DurableToolExecutor,
    EventType,
    JsonlSessionPersistence,
    OperationRecoveryClassification,
    Session,
    SessionPersistence,
    SessionStatus,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


class FakePaymentService:
    def __init__(self) -> None:
        self.results: dict[str, JsonValue] = {}
        self.actual_execution_count = 0

    async def charge(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        if context.operation_id in self.results:
            return self.results[context.operation_id]
        self.actual_execution_count += 1
        output: JsonValue = {
            "receipt": f"receipt-{self.actual_execution_count}",
            "amount": arguments["amount"],
        }
        self.results[context.operation_id] = output
        return output


def registry(service: FakePaymentService, effect: ToolEffectKind) -> ToolRegistry:
    async def reconcile(_context: ToolExecutionContext):  # type: ignore[no-untyped-def]
        raise AssertionError("reconciliation is not used by WAL classification tests")

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema("payments.charge", "Charge.", {"type": "object"}),
            handler=service.charge,
            required_capability="payments.charge",
            effect_kind=effect,
            reconcile_handler=(
                reconcile if effect is ToolEffectKind.RECONCILABLE_MUTATION else None
            ),
        )
    )
    return tools


def prefix(
    effect: ToolEffectKind,
    *,
    prepare: bool = False,
    dispatch: bool = False,
    commit: bool = False,
    result: bool = False,
    persistence: SessionPersistence | None = None,
) -> tuple[Session, ToolCall]:
    session = Session("session-1", persistence)
    call = ToolCall("model-call-1", "payments.charge", {"amount": 42})
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "Pay."})
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
    if prepare:
        session.append(
            EventType.TOOL_PREPARE,
            {
                "turn": 1,
                "step": 1,
                "operation_id": "kernel-op-1",
                "tool_call_id": call.call_id,
                "tool_name": call.name,
                "effect_kind": effect.value,
            },
        )
        session.flush()
    if dispatch:
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
    output: JsonValue = {"receipt": "receipt-1", "amount": 42}
    if commit:
        session.append(
            EventType.TOOL_COMMIT,
            {
                "turn": 1,
                "step": 1,
                "operation_id": "kernel-op-1",
                "output": output,
            },
        )
        session.flush()
    if result:
        tool_result = ToolResult.success(call, output)
        session.append(
            EventType.TOOL_RESULT,
            {"turn": 1, "step": 1, **tool_result.as_dict()},
        )
        session.append(
            EventType.STEP_END,
            {"turn": 1, "step": 1, "outcome": "tool_calls"},
        )
        session.append(EventType.TURN_END, {"turn": 1, "reason": "completed"})
    return session, call


def test_crash_a_before_prepare_has_no_durable_operation() -> None:
    session, _ = prefix(ToolEffectKind.OPAQUE_MUTATION)

    analysis = session.recovery_analysis

    assert analysis.status is SessionStatus.INTERRUPTED
    assert analysis.durable_operations == ()
    assert analysis.has_ambiguous_tool_outcomes


def test_crash_b_after_prepare_is_safe_to_retry() -> None:
    service = FakePaymentService()
    tools = registry(service, ToolEffectKind.OPAQUE_MUTATION)
    session, _ = prefix(ToolEffectKind.OPAQUE_MUTATION, prepare=True)
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"payments.charge"},
    )
    operation = session.recovery_analysis.durable_operations[0]

    result = asyncio.run(
        DurableToolExecutor(tools).retry(operation, agent.control, session)
    )

    assert result.ok
    assert service.actual_execution_count == 1
    recovered = session.recovery_analysis.durable_operations[0]
    assert recovered.operation_id == "kernel-op-1"
    assert recovered.classification is OperationRecoveryClassification.COMPLETED

    with pytest.raises(DurableToolExecutionError, match="not retryable"):
        asyncio.run(
            DurableToolExecutor(tools).retry(operation, agent.control, session)
        )
    assert service.actual_execution_count == 1


@pytest.mark.parametrize(
    ("effect", "classification"),
    [
        (
            ToolEffectKind.IDEMPOTENT_MUTATION,
            OperationRecoveryClassification.IDEMPOTENT_RETRY_ALLOWED,
        ),
        (
            ToolEffectKind.RECONCILABLE_MUTATION,
            OperationRecoveryClassification.RECONCILE_REQUIRED,
        ),
        (
            ToolEffectKind.OPAQUE_MUTATION,
            OperationRecoveryClassification.MANUAL_REQUIRED,
        ),
    ],
)
def test_crash_c_after_dispatch_is_classified_by_effect(
    effect: ToolEffectKind,
    classification: OperationRecoveryClassification,
) -> None:
    session, _ = prefix(effect, prepare=True, dispatch=True)

    operation = session.recovery_analysis.durable_operations[0]

    assert operation.dispatch_attempts == 1
    assert operation.classification is classification


def test_crash_d_idempotent_retry_reuses_identity_without_duplicate_effect() -> None:
    service = FakePaymentService()
    tools = registry(service, ToolEffectKind.IDEMPOTENT_MUTATION)
    session, call = prefix(
        ToolEffectKind.IDEMPOTENT_MUTATION,
        prepare=True,
        dispatch=True,
    )
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"payments.charge"},
    )
    first_context = ToolExecutionContext(
        agent_id="agent-1",
        session_id="session-1",
        tool_call_id=call.call_id,
        operation_id="kernel-op-1",
    )
    first_output = asyncio.run(service.charge(call.arguments, first_context))
    assert service.actual_execution_count == 1
    operation = session.recovery_analysis.durable_operations[0]

    retried = asyncio.run(
        DurableToolExecutor(tools).retry(operation, agent.control, session)
    )

    assert retried.ok
    assert retried.output == first_output
    assert service.actual_execution_count == 1
    dispatches = [
        event
        for event in session.events
        if event.type is EventType.TOOL_DISPATCH
    ]
    assert [event.data["attempt"] for event in dispatches] == [1, 2]
    assert {
        event.data["operation_id"]
        for event in session.events
        if "operation_id" in event.data
    } == {"kernel-op-1"}


def test_crash_d_opaque_mutation_requires_manual_action_and_refuses_retry() -> None:
    service = FakePaymentService()
    tools = registry(service, ToolEffectKind.OPAQUE_MUTATION)
    session, call = prefix(
        ToolEffectKind.OPAQUE_MUTATION,
        prepare=True,
        dispatch=True,
    )
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"payments.charge"},
    )
    context = ToolExecutionContext(
        agent_id="agent-1",
        session_id="session-1",
        tool_call_id=call.call_id,
        operation_id="kernel-op-1",
    )
    asyncio.run(service.charge(call.arguments, context))
    operation = session.recovery_analysis.durable_operations[0]

    assert operation.classification is OperationRecoveryClassification.MANUAL_REQUIRED
    with pytest.raises(DurableToolExecutionError, match="not retryable"):
        asyncio.run(
            DurableToolExecutor(tools).retry(operation, agent.control, session)
        )
    assert service.actual_execution_count == 1


def test_crash_e_commit_is_completed_and_cannot_be_reexecuted() -> None:
    service = FakePaymentService()
    tools = registry(service, ToolEffectKind.IDEMPOTENT_MUTATION)
    session, _ = prefix(
        ToolEffectKind.IDEMPOTENT_MUTATION,
        prepare=True,
        dispatch=True,
        commit=True,
    )
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"payments.charge"},
    )
    operation = session.recovery_analysis.durable_operations[0]

    assert operation.classification is OperationRecoveryClassification.COMPLETED
    assert operation.committed
    assert not operation.result_persisted
    assert not session.recovery_analysis.has_ambiguous_tool_outcomes
    with pytest.raises(DurableToolExecutionError, match="not retryable"):
        asyncio.run(
            DurableToolExecutor(tools).retry(operation, agent.control, session)
        )
    assert service.actual_execution_count == 0


def test_crash_f_after_result_is_a_completed_session() -> None:
    session, _ = prefix(
        ToolEffectKind.IDEMPOTENT_MUTATION,
        prepare=True,
        dispatch=True,
        commit=True,
        result=True,
    )

    analysis = session.recovery_analysis
    operation = analysis.durable_operations[0]

    assert analysis.status is SessionStatus.COMPLETED
    assert operation.classification is OperationRecoveryClassification.COMPLETED
    assert operation.result_persisted
    assert not analysis.has_ambiguous_tool_outcomes


def test_jsonl_restart_preserves_operation_identity_for_idempotent_retry(
    tmp_path,
) -> None:
    path = tmp_path / "durable-operation.jsonl"
    service = FakePaymentService()
    tools = registry(service, ToolEffectKind.IDEMPOTENT_MUTATION)
    session, call = prefix(
        ToolEffectKind.IDEMPOTENT_MUTATION,
        prepare=True,
        dispatch=True,
        persistence=JsonlSessionPersistence(path),
    )
    first_context = ToolExecutionContext(
        agent_id="agent-1",
        session_id="session-1",
        tool_call_id=call.call_id,
        operation_id="kernel-op-1",
    )
    asyncio.run(service.charge(call.arguments, first_context))
    session.close()

    restored = Session.load("session-1", JsonlSessionPersistence(path))
    agent = Agent.create(
        agent_id="agent-1",
        session=restored,
        capabilities={"payments.charge"},
    )
    operation = restored.recovery_analysis.durable_operations[0]
    result = asyncio.run(
        DurableToolExecutor(tools).retry(operation, agent.control, restored)
    )
    restored.close()

    assert result.ok
    assert service.actual_execution_count == 1
    final = Session.load("session-1", JsonlSessionPersistence(path))
    try:
        recovered = final.recovery_analysis.durable_operations[0]
        assert recovered.operation_id == "kernel-op-1"
        assert recovered.dispatch_attempts == 2
        assert recovered.classification is OperationRecoveryClassification.COMPLETED
    finally:
        final.close()
