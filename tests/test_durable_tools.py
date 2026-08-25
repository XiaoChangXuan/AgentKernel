from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from agentkernel import (
    Agent,
    AgentState,
    DefaultAgentLoop,
    DurableToolExecutionError,
    DurableToolExecutor,
    ErrorCode,
    EventType,
    InMemorySessionPersistence,
    ModelRequest,
    ModelResponse,
    OperationRecoveryClassification,
    PromptService,
    ReconcileResult,
    ReconcileStatus,
    ScriptedLLM,
    Session,
    SessionPersistenceError,
    ToolCall,
    ToolDefinition,
    ToolEffectKind,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


class FakeOrderService:
    def __init__(self) -> None:
        self.orders: dict[str, JsonValue] = {}
        self.actual_execution_count = 0

    async def create(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        existing = self.orders.get(context.operation_id)
        if existing is not None:
            return existing
        self.actual_execution_count += 1
        result: JsonValue = {
            "order_id": f"order-{self.actual_execution_count}",
            "item": arguments["item"],
        }
        self.orders[context.operation_id] = result
        return result


def mutation_registry(
    service: FakeOrderService,
    effect_kind: ToolEffectKind = ToolEffectKind.IDEMPOTENT_MUTATION,
    *,
    timeout_seconds: float | None = None,
) -> ToolRegistry:
    async def reconcile(context: ToolExecutionContext) -> ReconcileResult:
        output = service.orders.get(context.operation_id)
        if output is None:
            return ReconcileResult(ReconcileStatus.NOT_FOUND)
        return ReconcileResult(ReconcileStatus.SUCCEEDED, output=output)

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema(
                "orders.create",
                "Create an order.",
                {
                    "type": "object",
                    "properties": {"item": {"type": "string"}},
                    "required": ["item"],
                },
            ),
            handler=service.create,
            required_capability="orders.create",
            timeout_seconds=timeout_seconds,
            effect_kind=effect_kind,
            reconcile_handler=(
                reconcile
                if effect_kind is ToolEffectKind.RECONCILABLE_MUTATION
                else None
            ),
        )
    )
    return tools


def begin_call(session: Session, call: ToolCall) -> None:
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "Order it."})
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


def test_default_loop_uses_durable_protocol_without_model_metadata() -> None:
    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("model-call-1", "orders.create", {"item": "book"})

    def finish(request: ModelRequest) -> ModelResponse:
        payload = json.loads(request.messages[-1].content)
        assert payload["output"]["order_id"] == "order-1"
        return ModelResponse(content="Created.")

    loop = DefaultAgentLoop(
        llm=ScriptedLLM([ModelResponse(tool_calls=(call,)), finish]),
        tools=tools,
        prompt=PromptService("Use tools."),
        tool_executor=DurableToolExecutor(
            tools,
            operation_id_factory=lambda: "kernel-operation-1",
        ),
    )

    assert asyncio.run(loop.run(agent, "Create an order.")) == "Created."
    assert service.actual_execution_count == 1
    event_types = [event.type for event in session.events]
    assert event_types[4:10] == [
        EventType.TOOL_CALL,
        EventType.TOOL_PREPARE,
        EventType.TOOL_DISPATCH,
        EventType.TOOL_COMMIT,
        EventType.TOOL_RESULT,
        EventType.STEP_END,
    ]
    prepare = next(
        event for event in session.events if event.type is EventType.TOOL_PREPARE
    )
    assert prepare.data["operation_id"] == "kernel-operation-1"
    assert prepare.data["operation_id"] != call.call_id
    assert call.arguments == {"item": "book"}
    schema = tools.model_schemas(agent.control)[0]
    projected = {
        "name": schema.name,
        "description": schema.description,
        "input_schema": schema.input_schema,
    }
    assert "operation_id" not in json.dumps(projected)
    assert "effect_kind" not in json.dumps(projected)


def test_capability_check_precedes_prepare_and_dispatch() -> None:
    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1")
    agent = Agent.create(agent_id="agent-1", session=session)
    call = ToolCall("call-1", "orders.create", {"item": "book"})
    begin_call(session, call)
    executor = DurableToolExecutor(
        tools,
        operation_id_factory=lambda: "must-not-be-created",
    )

    result = asyncio.run(
        executor.execute(call, agent.control, session, turn=1, step=1)
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert service.actual_execution_count == 0
    assert all(event.type is not EventType.TOOL_PREPARE for event in session.events)


def test_prepare_flush_failure_prevents_handler_execution() -> None:
    class FailingFlushPersistence(InMemorySessionPersistence):
        def flush(self) -> None:
            super().flush()
            raise SessionPersistenceError("durability unavailable")

    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1", FailingFlushPersistence())
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("call-1", "orders.create", {"item": "book"})
    begin_call(session, call)

    with pytest.raises(SessionPersistenceError, match="durability unavailable"):
        asyncio.run(
            DurableToolExecutor(tools).execute(
                call,
                agent.control,
                session,
                turn=1,
                step=1,
            )
        )

    assert service.actual_execution_count == 0
    assert session.events[-1].type is EventType.TOOL_PREPARE
    assert all(event.type is not EventType.TOOL_DISPATCH for event in session.events)


def test_loop_preserves_replayable_open_prefix_when_prepare_flush_fails() -> None:
    class FailingFlushPersistence(InMemorySessionPersistence):
        def flush(self) -> None:
            super().flush()
            raise SessionPersistenceError("durability unavailable")

    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1", FailingFlushPersistence())
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("call-1", "orders.create", {"item": "book"})
    loop = DefaultAgentLoop(
        llm=ScriptedLLM([ModelResponse(tool_calls=(call,))]),
        tools=tools,
        prompt=PromptService("Use tools."),
    )

    with pytest.raises(SessionPersistenceError, match="durability unavailable"):
        asyncio.run(loop.run(agent, "Create."))

    assert agent.control.state is AgentState.FAILED
    assert service.actual_execution_count == 0
    assert session.events[-1].type is EventType.TOOL_PREPARE
    analysis = session.recovery_analysis
    assert analysis.pending_tool_calls == (call,)
    assert analysis.status.value == "interrupted"


def test_dispatch_flush_failure_prevents_handler_execution() -> None:
    class SecondFlushFails(InMemorySessionPersistence):
        def __init__(self) -> None:
            super().__init__()
            self.flush_count = 0

        def flush(self) -> None:
            super().flush()
            self.flush_count += 1
            if self.flush_count == 2:
                raise SessionPersistenceError("dispatch durability unavailable")

    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1", SecondFlushFails())
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("call-1", "orders.create", {"item": "book"})
    begin_call(session, call)

    with pytest.raises(
        SessionPersistenceError,
        match="dispatch durability unavailable",
    ):
        asyncio.run(
            DurableToolExecutor(tools).execute(
                call,
                agent.control,
                session,
                turn=1,
                step=1,
            )
        )

    assert service.actual_execution_count == 0
    assert session.events[-1].type is EventType.TOOL_DISPATCH


def test_registry_refuses_direct_mutation_dispatch() -> None:
    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("call-1", "orders.create", {"item": "book"})

    result = asyncio.run(tools.execute(call, agent.control))

    assert not result.ok
    assert result.error is not None
    assert result.error.code is ErrorCode.EINVAL
    assert service.actual_execution_count == 0


def test_kernel_operation_id_cannot_equal_model_tool_call_id() -> None:
    service = FakeOrderService()
    tools = mutation_registry(service)
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("same-id", "orders.create", {"item": "book"})
    begin_call(session, call)

    with pytest.raises(DurableToolExecutionError, match="must differ"):
        asyncio.run(
            DurableToolExecutor(
                tools,
                operation_id_factory=lambda: "same-id",
            ).execute(call, agent.control, session, turn=1, step=1)
        )

    assert service.actual_execution_count == 0
    assert all(event.type is not EventType.TOOL_PREPARE for event in session.events)


@pytest.mark.parametrize(
    ("effect_kind", "classification"),
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
def test_mutation_timeout_is_classified_by_effect_kind(
    effect_kind: ToolEffectKind,
    classification: OperationRecoveryClassification,
) -> None:
    service = FakeOrderService()

    async def slow_create(
        _arguments: Mapping[str, JsonValue],
        _context: ToolExecutionContext,
    ) -> JsonValue:
        await asyncio.sleep(0.05)
        return {"late": True}

    async def reconcile(_context: ToolExecutionContext) -> ReconcileResult:
        return ReconcileResult(ReconcileStatus.UNKNOWN)

    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            schema=ToolSchema("orders.create", "Create.", {"type": "object"}),
            handler=slow_create,
            required_capability="orders.create",
            timeout_seconds=0.001,
            effect_kind=effect_kind,
            reconcile_handler=(
                reconcile
                if effect_kind is ToolEffectKind.RECONCILABLE_MUTATION
                else None
            ),
        )
    )
    session = Session("session-1")
    agent = Agent.create(
        agent_id="agent-1",
        session=session,
        capabilities={"orders.create"},
    )
    call = ToolCall("call-1", "orders.create", {})
    begin_call(session, call)

    result = asyncio.run(
        DurableToolExecutor(
            tools,
            operation_id_factory=lambda: "operation-1",
        ).execute(call, agent.control, session, turn=1, step=1)
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code is ErrorCode.ETIMEDOUT
    operation = session.recovery_analysis.durable_operations[0]
    assert operation.classification is classification
