from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agentkernel import (
    Agent,
    AuthorizationDecision,
    CapabilityGrant,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    OperationRecoveryClassification,
    ReconcileResult,
    ReconcileStatus,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSchema,
)
from agentkernel.protocol import JsonValue
from agentkernel.tools import ToolAuthorization


PAYMENT_ACTION = "payment.charge"
PAYMENT_SCOPE = "payment://charges/**"


class FakePaymentService:
    def __init__(self) -> None:
        self.charges: dict[str, JsonValue] = {}
        self.actual_execution_count = 0
        self.reconciled_operation_ids: list[str] = []

    async def charge(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        existing = self.charges.get(context.operation_id)
        if existing is not None:
            return existing
        self.actual_execution_count += 1
        result: JsonValue = {
            "receipt": f"receipt-{self.actual_execution_count}",
            "invoice_id": arguments["invoice_id"],
            "amount_cents": arguments["amount_cents"],
        }
        self.charges[context.operation_id] = result
        return result

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        self.reconciled_operation_ids.append(context.operation_id)
        output = self.charges.get(context.operation_id)
        if output is None:
            return ReconcileResult(ReconcileStatus.NOT_FOUND)
        return ReconcileResult(ReconcileStatus.SUCCEEDED, output=output)


class DispatchDenyingRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.authorization_checks = 0

    def authorization_for_definition(
        self,
        definition: ToolDefinition,
        agent,
    ) -> ToolAuthorization:
        self.authorization_checks += 1
        authorization = super().authorization_for_definition(definition, agent)
        if self.authorization_checks >= 2:
            return ToolAuthorization(
                definition=authorization.definition,
                request=authorization.request,
                decision=AuthorizationDecision(
                    allowed=False,
                    reason="dispatch_authorization_denied",
                ),
            )
        return authorization


def payment_registry(
    service: FakePaymentService,
    *,
    effect_kind: ToolEffectKind = ToolEffectKind.RECONCILABLE_MUTATION,
    legacy: bool = False,
    registry: ToolRegistry | None = None,
) -> ToolRegistry:
    tools = registry or ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                "payment.charge",
                "Charge a fake invoice.",
                {
                    "type": "object",
                    "properties": {
                        "invoice_id": {"type": "string"},
                        "amount_cents": {"type": "integer"},
                    },
                    "required": ["invoice_id", "amount_cents"],
                },
            ),
            handler=service.charge,
            required_capability=("payment.charge" if legacy else None),
            required_action=(None if legacy else PAYMENT_ACTION),
            required_resource=(None if legacy else PAYMENT_SCOPE),
            effect_kind=effect_kind,
            reconcile_handler=(
                service.reconcile
                if effect_kind is ToolEffectKind.RECONCILABLE_MUTATION
                else None
            ),
        )
    )
    return tools


def payment_agent(session: Session, *, legacy: bool = False) -> Agent:
    if legacy:
        return Agent.create(
            agent_id="agent-1",
            session=session,
            capabilities={"payment.charge"},
        )
    return Agent.create(
        agent_id="agent-1",
        session=session,
        capability_grants=(
            CapabilityGrant("agent-1", PAYMENT_ACTION, PAYMENT_SCOPE),
        ),
    )


def payment_call() -> ToolCall:
    return ToolCall(
        "call-payment-1",
        "payment.charge",
        {"invoice_id": "invoice-001", "amount_cents": 4200},
    )


def begin_call(session: Session, call: ToolCall) -> None:
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(
        EventType.USER_MESSAGE,
        {"turn": 1, "content": "Charge invoice-001."},
    )
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})


def append_dispatched_prefix(session: Session, call: ToolCall) -> None:
    begin_call(session, call)
    authorization = _authorization_context()
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "op-payment-1",
            "boundary": "prepare",
            **authorization,
        },
    )
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "op-payment-1",
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            "authorization": authorization,
        },
    )
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "op-payment-1",
            "boundary": "dispatch",
            **authorization,
        },
    )
    session.append(
        EventType.TOOL_DISPATCH,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "op-payment-1",
            "attempt": 1,
            "authorization": authorization,
        },
    )
    session.flush()


def _authorization_context() -> dict[str, JsonValue]:
    return {
        "agent_id": "agent-1",
        "action": PAYMENT_ACTION,
        "resource_scope": PAYMENT_SCOPE,
        "reason": "allowed",
        "matched_grant": {
            "subject": "agent-1",
            "action": PAYMENT_ACTION,
            "resource_scope": PAYMENT_SCOPE,
        },
    }


def test_authorized_payment_flow_records_authorization_lifecycle() -> None:
    service = FakePaymentService()
    tools = payment_registry(service)
    session = Session("session-1")
    agent = payment_agent(session)
    call = payment_call()
    begin_call(session, call)

    result = asyncio.run(
        DurableToolExecutor(
            tools,
            operation_id_factory=lambda: "op-payment-1",
        ).execute(call, agent.control, session, turn=1, step=1)
    )

    assert result.ok
    assert service.actual_execution_count == 1
    event_types = [event.type for event in session.events]
    assert event_types[5:10] == [
        EventType.AUTHORIZATION_GRANTED,
        EventType.TOOL_PREPARE,
        EventType.AUTHORIZATION_GRANTED,
        EventType.TOOL_DISPATCH,
        EventType.TOOL_COMMIT,
    ]
    prepare = next(
        event for event in session.events if event.type is EventType.TOOL_PREPARE
    )
    dispatch = next(
        event for event in session.events if event.type is EventType.TOOL_DISPATCH
    )
    assert prepare.data["authorization"] == dispatch.data["authorization"]
    assert prepare.data["authorization"]["agent_id"] == "agent-1"  # type: ignore[index]
    assert prepare.data["authorization"]["action"] == PAYMENT_ACTION  # type: ignore[index]
    assert prepare.data["authorization"]["resource_scope"] == PAYMENT_SCOPE  # type: ignore[index]


def test_unauthorized_dispatch_is_denied_before_external_effect() -> None:
    service = FakePaymentService()
    tools = payment_registry(service, registry=DispatchDenyingRegistry())
    session = Session("session-1")
    agent = payment_agent(session)
    call = payment_call()
    begin_call(session, call)

    result = asyncio.run(
        DurableToolExecutor(
            tools,
            operation_id_factory=lambda: "op-payment-1",
        ).execute(call, agent.control, session, turn=1, step=1)
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert service.actual_execution_count == 0
    assert any(
        event.type is EventType.AUTHORIZATION_DENIED
        and event.data["boundary"] == "dispatch"
        for event in session.events
    )
    assert all(event.type is not EventType.TOOL_DISPATCH for event in session.events)
    operation = session.recovery_analysis.durable_operations[0]
    assert operation.aborted
    assert operation.classification is OperationRecoveryClassification.COMPLETED


def test_crash_after_dispatch_requires_reconcile_without_duplicate_effect() -> None:
    service = FakePaymentService()
    tools = payment_registry(service)
    session = Session("session-1")
    agent = payment_agent(session)
    call = payment_call()
    append_dispatched_prefix(session, call)
    first_context = ToolExecutionContext(
        agent_id="agent-1",
        session_id="session-1",
        tool_call_id=call.call_id,
        operation_id="op-payment-1",
    )
    first_output = asyncio.run(service.charge(call.arguments, first_context))
    operation = session.recovery_analysis.durable_operations[0]

    assert operation.classification is OperationRecoveryClassification.RECONCILE_REQUIRED
    observed = asyncio.run(
        DurableToolExecutor(tools).reconcile(
            operation,
            agent.control,
            session,
        )
    )

    assert observed.status is ReconcileStatus.SUCCEEDED
    assert observed.output == first_output
    assert service.actual_execution_count == 1
    assert service.reconciled_operation_ids == ["op-payment-1"]


def test_operation_retains_authorization_identity_after_replay() -> None:
    session = Session("session-1")
    call = payment_call()
    append_dispatched_prefix(session, call)

    operation = session.recovery_analysis.durable_operations[0]

    assert operation.authorization is not None
    assert operation.authorization["agent_id"] == "agent-1"
    assert operation.authorization["action"] == PAYMENT_ACTION
    assert operation.authorization["resource_scope"] == PAYMENT_SCOPE
    assert operation.authorization["matched_grant"]["subject"] == "agent-1"  # type: ignore[index]


def test_legacy_durable_tool_capability_records_authorization_context() -> None:
    service = FakePaymentService()
    tools = payment_registry(
        service,
        effect_kind=ToolEffectKind.IDEMPOTENT_MUTATION,
        legacy=True,
    )
    session = Session("session-1")
    agent = payment_agent(session, legacy=True)
    call = payment_call()
    begin_call(session, call)

    result = asyncio.run(
        DurableToolExecutor(
            tools,
            operation_id_factory=lambda: "op-payment-1",
        ).execute(call, agent.control, session, turn=1, step=1)
    )

    assert result.ok
    operation = session.recovery_analysis.durable_operations[0]
    assert operation.authorization is not None
    assert operation.authorization["agent_id"] == "agent-1"
    assert operation.authorization["action"] == TOOL_EXECUTE_ACTION
    assert operation.authorization["resource_scope"] == "tool://payment.charge"
