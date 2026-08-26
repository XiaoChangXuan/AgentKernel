"""Offline runtime validation for V0.6 capability enforcement boundaries."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Mapping
from pathlib import Path

from agentkernel import (
    Agent,
    AuthorizationDecision,
    CapabilityGrant,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    JsonlSessionPersistence,
    LocalResourceStore,
    OperationRecoveryClassification,
    RESOURCE_READ_ACTION,
    ReconcileResult,
    ReconcileStatus,
    ResourceOwner,
    ResourceService,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSchema,
    resource_tool_definitions,
)
from agentkernel.protocol import JsonValue
from agentkernel.tools import ToolAuthorization

from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records


BENCHMARK = "capability_runtime"
AGENT_ID = "agent-capability"
SESSION_ID = "session-capability"
PAYMENT_ACTION = "payment.charge"
PAYMENT_SCOPE = "payment://charges/**"
PAYMENT_RESOURCE = "payment://charges/invoice-001"


class FakePaymentService:
    """In-memory side-effect fixture keyed by durable operation id."""

    def __init__(self) -> None:
        self._payments: dict[str, JsonValue] = {}
        self.execution_count = 0
        self.reconcile_count = 0

    async def charge(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        existing = self._payments.get(context.operation_id)
        if existing is not None:
            return existing
        self.execution_count += 1
        payment: JsonValue = {
            "request_id": context.operation_id,
            "invoice_id": arguments["invoice_id"],
            "amount_cents": arguments["amount_cents"],
            "status": "succeeded",
        }
        self._payments[context.operation_id] = payment
        return payment

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        self.reconcile_count += 1
        payment = self._payments.get(context.operation_id)
        if payment is None:
            return ReconcileResult(ReconcileStatus.NOT_FOUND)
        return ReconcileResult(ReconcileStatus.SUCCEEDED, output=payment)

    @property
    def payment_count(self) -> int:
        return len(self._payments)


class DispatchDenyingRegistry(ToolRegistry):
    """Allow prepare authorization and deny the dispatch authorization check."""

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


async def _add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


def run() -> list[BenchmarkRecord]:
    return [
        _unauthorized_tool(),
        _unauthorized_resource_read(),
        _unauthorized_payment_dispatch(),
        _crash_after_prepare(),
        _legacy_tool(),
    ]


def _unauthorized_tool() -> BenchmarkRecord:
    timer = Timer()
    registry = ToolRegistry()
    registry.register(_math_tool())
    agent = Agent.create(agent_id=AGENT_ID, session=Session(SESSION_ID))

    result = asyncio.run(
        registry.execute(
            ToolCall("call-tool-denied", "math.add", {"left": 20, "right": 22}),
            agent.control,
        )
    )
    visible_tools = [schema.name for schema in registry.model_schemas(agent.control)]
    denied = (not result.ok) and result.error is not None

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="unauthorized_tool",
        strategy="tool_registry_capability",
        metrics={
            "expected": "DENY",
            "allowed": result.ok,
            "error_code": result.error.code.value if result.error is not None else None,
            "visible_tool_count": len(visible_tools),
            "model_visible": "math.add" in visible_tools,
            "latency_ms": timer.elapsed_ms(),
            "success": (
                denied
                and result.error is not None
                and result.error.code is ErrorCode.EACCES
                and not visible_tools
            ),
        },
    )


def _unauthorized_resource_read() -> BenchmarkRecord:
    timer = Timer()
    with tempfile.TemporaryDirectory(prefix="agentkernel-cap-resource-") as root:
        resources = ResourceService(
            LocalResourceStore(Path(root) / "resources"),
            resource_id_factory=lambda: "res_secret",
            handle_id_factory=lambda: "hdl_secret",
        )
        owner = ResourceOwner(AGENT_ID, SESSION_ID)
        handle = resources.create_artifact(
            b"secret artifact",
            owner=owner,
            media_type="text/plain",
            encoding="utf-8",
            source_tool_name="fixture",
            source_tool_call_id="call-source",
            source_operation_id="op-source",
        )
        registry = ToolRegistry()
        for definition in resource_tool_definitions(resources):
            registry.register(definition)
        agent = Agent.create(
            agent_id=AGENT_ID,
            session=Session(SESSION_ID),
            capability_grants=(
                CapabilityGrant(AGENT_ID, TOOL_EXECUTE_ACTION, "tool://resource.read"),
                CapabilityGrant(
                    AGENT_ID,
                    RESOURCE_READ_ACTION,
                    "artifact://res_other",
                ),
            ),
        )

        result = asyncio.run(
            registry.execute(
                ToolCall("call-resource-denied", "resource_read", {"uri": handle.uri}),
                agent.control,
            )
        )

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="unauthorized_resource_read",
        strategy="resource_service_capability",
        metrics={
            "expected": "DENY",
            "allowed": result.ok,
            "error_code": result.error.code.value if result.error is not None else None,
            "requested_resource": handle.uri,
            "resource_reads": resources.metrics.resource_reads,
            "latency_ms": timer.elapsed_ms(),
            "success": (
                not result.ok
                and result.error is not None
                and result.error.code is ErrorCode.EACCES
                and resources.metrics.resource_reads == 0
            ),
        },
    )


def _unauthorized_payment_dispatch() -> BenchmarkRecord:
    service = FakePaymentService()
    registry = _payment_registry(service, registry=DispatchDenyingRegistry())
    session = Session(SESSION_ID)
    agent = _payment_agent(session)
    call = _payment_call("call-payment-denied")
    _append_call_prefix(session, call)

    timer = Timer()
    result = asyncio.run(
        DurableToolExecutor(
            registry,
            operation_id_factory=lambda: "op_payment_denied",
        ).execute(call, agent.control, session, turn=1, step=1)
    )
    authorization_denied_events = [
        event
        for event in session.events
        if event.type is EventType.AUTHORIZATION_DENIED
    ]
    dispatch_events = [
        event for event in session.events if event.type is EventType.TOOL_DISPATCH
    ]
    operation = session.recovery_analysis.durable_operations[0]

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="unauthorized_payment_dispatch",
        strategy="durable_dispatch_capability",
        metrics={
            "expected": "DENY",
            "allowed": result.ok,
            "error_code": result.error.code.value if result.error is not None else None,
            "external_execution_count": service.execution_count,
            "authorization_denied_events": len(authorization_denied_events),
            "dispatch_events": len(dispatch_events),
            "operation_classification": operation.classification.value,
            "latency_ms": timer.elapsed_ms(),
            "success": (
                not result.ok
                and result.error is not None
                and result.error.code is ErrorCode.EACCES
                and service.execution_count == 0
                and len(authorization_denied_events) == 1
                and not dispatch_events
            ),
        },
    )


def _crash_after_prepare() -> BenchmarkRecord:
    service = FakePaymentService()
    registry = _payment_registry(service)
    timer = Timer()
    with tempfile.TemporaryDirectory(prefix="agentkernel-cap-crash-") as root:
        path = Path(root) / "session.jsonl"
        session = Session(SESSION_ID, JsonlSessionPersistence(path))
        call = _payment_call("call-payment-crash")
        _append_prepared_prefix(session, call)
        event_count_before_restart = len(session.events)
        session.close()

        restarted = Session.load(SESSION_ID, JsonlSessionPersistence(path))
        agent = _payment_agent(restarted)
        operation = restarted.recovery_analysis.durable_operations[0]
        retry_result = asyncio.run(
            DurableToolExecutor(registry).retry(operation, agent.control, restarted)
        )
        if retry_result.ok:
            restarted.append(
                EventType.TOOL_RESULT,
                {"turn": 1, "step": 1, **retry_result.as_dict()},
            )
            restarted.append(EventType.STEP_END, {"turn": 1, "step": 1})
            restarted.append(EventType.TURN_END, {"turn": 1, "reason": "completed"})
            restarted.flush()
        final = restarted.recovery_analysis
        current_operation = final.durable_operations[0]
        restarted.close()

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="crash_after_prepare",
        strategy="wal_recovery_capability",
        metrics={
            "expected": "Recovery without duplicate",
            "pre_recovery_classification": operation.classification.value,
            "retry_allowed": (
                operation.classification
                is OperationRecoveryClassification.SAFE_TO_RETRY
            ),
            "retry_ok": retry_result.ok,
            "external_execution_count": service.execution_count,
            "payment_count": service.payment_count,
            "event_count_before_restart": event_count_before_restart,
            "final_session_status": final.status.value,
            "authorization_metadata_present": current_operation.authorization
            is not None,
            "latency_ms": timer.elapsed_ms(),
            "success": (
                operation.classification
                is OperationRecoveryClassification.SAFE_TO_RETRY
                and retry_result.ok
                and service.execution_count == 1
                and service.payment_count == 1
                and final.status.value == "completed"
                and current_operation.authorization is not None
            ),
        },
    )


def _legacy_tool() -> BenchmarkRecord:
    timer = Timer()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema("legacy.echo", "Echo a value.", {"type": "object"}),
            handler=_echo,
            required_capability="legacy.echo",
        )
    )
    agent = Agent.create(
        agent_id=AGENT_ID,
        session=Session(SESSION_ID),
        capabilities={"legacy.echo"},
    )
    result = asyncio.run(
        registry.execute(
            ToolCall("call-legacy", "legacy.echo", {"value": "ok"}),
            agent.control,
        )
    )
    visible_tools = [schema.name for schema in registry.model_schemas(agent.control)]

    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case="legacy_tool",
        strategy="legacy_required_capability",
        metrics={
            "expected": "Backward compatible",
            "allowed": result.ok,
            "output": result.output if result.ok else None,
            "error_code": result.error.code.value if result.error is not None else None,
            "visible_tool_count": len(visible_tools),
            "latency_ms": timer.elapsed_ms(),
            "success": (
                result.ok
                and result.output == {"value": "ok"}
                and visible_tools == ["legacy.echo"]
            ),
        },
    )


def _math_tool() -> ToolDefinition:
    return ToolDefinition(
        schema=ToolSchema("math.add", "Add two integers.", {"type": "object"}),
        handler=_add,
        required_action=TOOL_EXECUTE_ACTION,
        required_resource="tool://math.add",
    )


async def _echo(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return {"value": arguments["value"]}


def _payment_registry(
    service: FakePaymentService,
    *,
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
                    "additionalProperties": False,
                },
            ),
            handler=service.charge,
            required_action=PAYMENT_ACTION,
            required_resource=PAYMENT_RESOURCE,
            effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
            reconcile_handler=service.reconcile,
        )
    )
    return tools


def _payment_agent(session: Session) -> Agent:
    return Agent.create(
        agent_id=AGENT_ID,
        session=session,
        capability_grants=(
            CapabilityGrant(AGENT_ID, PAYMENT_ACTION, PAYMENT_SCOPE),
        ),
    )


def _payment_call(call_id: str) -> ToolCall:
    return ToolCall(
        call_id,
        "payment.charge",
        {"invoice_id": "invoice-001", "amount_cents": 4200},
    )


def _append_call_prefix(session: Session, call: ToolCall) -> None:
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


def _append_prepared_prefix(session: Session, call: ToolCall) -> None:
    _append_call_prefix(session, call)
    authorization = _authorization_context()
    session.append(
        EventType.AUTHORIZATION_GRANTED,
        {
            "turn": 1,
            "step": 1,
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "operation_id": "op_payment_crash",
            "boundary": "prepare",
            **authorization,
        },
    )
    session.append(
        EventType.TOOL_PREPARE,
        {
            "turn": 1,
            "step": 1,
            "operation_id": "op_payment_crash",
            "tool_call_id": call.call_id,
            "tool_name": call.name,
            "effect_kind": ToolEffectKind.RECONCILABLE_MUTATION.value,
            "authorization": authorization,
        },
    )
    session.flush()


def _authorization_context() -> dict[str, JsonValue]:
    return {
        "agent_id": AGENT_ID,
        "action": PAYMENT_ACTION,
        "resource_scope": PAYMENT_RESOURCE,
        "reason": "allowed",
        "matched_grant": {
            "subject": AGENT_ID,
            "action": PAYMENT_ACTION,
            "resource_scope": PAYMENT_SCOPE,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="capability_runtime.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run()
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
