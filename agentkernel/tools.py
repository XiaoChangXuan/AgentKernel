"""Tool registry, model schema projection, and guarded execution."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, TypeAlias

from .agent import AgentControlBlock
from .capabilities import (
    CapabilityEvaluator,
    legacy_tool_request,
)
from .protocol import (
    ErrorCode,
    JsonValue,
    ToolCall,
    ToolResult,
    ToolSchema,
    is_json_value,
)
from .tool_effects import ReconcileResult, ReconcileStatus, ToolEffectKind


class ToolConcurrency(StrEnum):
    """Reserved execution classification; V0.1 dispatch remains sequential."""

    PARALLEL = "parallel"
    EXCLUSIVE = "exclusive"


class ToolExecutionError(RuntimeError):
    """Typed handler failure preserved across the Tool syscall boundary."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = ErrorCode(code)


class ToolResultProcessor(Protocol):
    async def process(
        self,
        call: ToolCall,
        result: ToolResult,
        context: "ToolExecutionContext",
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Kernel metadata passed to a tool implementation, never to the model."""

    agent_id: str
    session_id: str
    tool_call_id: str
    operation_id: str
    attempt: int = 1

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.agent_id,
                self.session_id,
                self.tool_call_id,
                self.operation_id,
            )
        ):
            raise ValueError("tool execution identities must be non-empty strings")
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt < 1
        ):
            raise ValueError("tool execution attempt must be a positive integer")


ToolHandler: TypeAlias = Callable[
    [Mapping[str, JsonValue], ToolExecutionContext], Awaitable[JsonValue]
]
ReconcileHandler: TypeAlias = Callable[
    [ToolExecutionContext], Awaitable[ReconcileResult]
]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Host runtime definition paired with a model-visible schema."""

    schema: ToolSchema
    handler: ToolHandler
    required_capability: str | None = None
    timeout_seconds: float | None = None
    concurrency: ToolConcurrency = ToolConcurrency.PARALLEL
    effect_kind: ToolEffectKind = ToolEffectKind.READ_ONLY
    reconcile_handler: ReconcileHandler | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effect_kind", ToolEffectKind(self.effect_kind))
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds must be positive")
        if (
            self.effect_kind is ToolEffectKind.RECONCILABLE_MUTATION
            and self.reconcile_handler is None
        ):
            raise ValueError(
                "RECONCILABLE_MUTATION requires a reconcile_handler"
            )
        if (
            self.effect_kind is not ToolEffectKind.RECONCILABLE_MUTATION
            and self.reconcile_handler is not None
        ):
            raise ValueError(
                "reconcile_handler is only valid for RECONCILABLE_MUTATION"
            )

    async def execute(
        self,
        arguments: Mapping[str, JsonValue],
        context: ToolExecutionContext,
    ) -> JsonValue:
        """Invoke the host handler under its optional timeout."""

        if self.timeout_seconds is None:
            return await self.handler(arguments, context)
        async with asyncio.timeout(self.timeout_seconds):
            return await self.handler(arguments, context)

    async def reconcile(self, context: ToolExecutionContext) -> ReconcileResult:
        """Query external state through a Tool-owned reconciliation contract."""

        if self.reconcile_handler is None:
            raise RuntimeError(
                f'tool "{self.schema.name}" does not support reconciliation'
            )
        if self.timeout_seconds is None:
            result = await self.reconcile_handler(context)
        else:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self.reconcile_handler(context)
        if not isinstance(result, ReconcileResult):
            raise TypeError("reconcile handler must return ReconcileResult")
        return result


class ToolRegistry:
    """Own model projection and the enforced tool/syscall boundary."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """Register one unique tool and snapshot its model schema."""

        name = definition.schema.name
        if name in self._definitions:
            raise ValueError(f'tool "{name}" is already registered')
        schema = ToolSchema(
            name=name,
            description=definition.schema.description,
            input_schema=copy.deepcopy(dict(definition.schema.input_schema)),
        )
        self._definitions[name] = ToolDefinition(
            schema=schema,
            handler=definition.handler,
            required_capability=definition.required_capability,
            timeout_seconds=definition.timeout_seconds,
            concurrency=definition.concurrency,
            effect_kind=definition.effect_kind,
            reconcile_handler=definition.reconcile_handler,
        )

    def model_schemas(self, agent: AgentControlBlock) -> tuple[ToolSchema, ...]:
        """Project only model-facing fields for tools the agent may execute."""

        schemas: list[ToolSchema] = []
        evaluator = CapabilityEvaluator.from_legacy_capabilities(
            agent_id=agent.agent_id,
            capabilities=agent.capabilities,
        )
        for definition in self._definitions.values():
            capability = definition.required_capability
            if capability is not None and not evaluator.authorize(
                legacy_tool_request(
                    agent_id=agent.agent_id,
                    required_capability=capability,
                )
            ).allowed:
                continue
            schemas.append(
                ToolSchema(
                    name=definition.schema.name,
                    description=definition.schema.description,
                    input_schema=copy.deepcopy(dict(definition.schema.input_schema)),
                )
            )
        return tuple(schemas)

    async def execute(
        self,
        call: ToolCall,
        agent: AgentControlBlock,
    ) -> ToolResult:
        """Resolve, authorize, execute, and normalize one tool call."""

        resolved = self.resolve_for_execution(call, agent)
        if isinstance(resolved, ToolResult):
            return resolved
        definition = resolved
        if definition.effect_kind is not ToolEffectKind.READ_ONLY:
            return ToolResult.failure(
                call,
                ErrorCode.EINVAL,
                f'mutation tool "{call.name}" requires DurableToolExecutor',
            )
        context = ToolExecutionContext(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            tool_call_id=call.call_id,
            operation_id=f"op_{uuid.uuid4().hex}",
        )
        return await self.invoke(resolved, call, context)

    def resolve_for_execution(
        self,
        call: ToolCall,
        agent: AgentControlBlock,
    ) -> ToolDefinition | ToolResult:
        """Resolve and authorize before any durable intent is created."""

        definition = self._definitions.get(call.name)
        if definition is None:
            return ToolResult.failure(
                call,
                ErrorCode.ENOENT,
                f'tool "{call.name}" is not registered',
            )
        capability = definition.required_capability
        if capability is not None:
            evaluator = CapabilityEvaluator.from_legacy_capabilities(
                agent_id=agent.agent_id,
                capabilities=agent.capabilities,
            )
            decision = evaluator.authorize(
                legacy_tool_request(
                    agent_id=agent.agent_id,
                    required_capability=capability,
                )
            )
            if not decision.allowed:
                return ToolResult.failure(
                    call,
                    ErrorCode.EACCES,
                    f'agent lacks required capability "{capability}"',
                )
        return definition

    async def invoke(
        self,
        definition: ToolDefinition,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Invoke one already-authorized Tool and normalize runtime failures."""

        try:
            output = await definition.execute(call.arguments, context)
            if not is_json_value(output):
                raise TypeError("tool returned a value that is not lossless JSON")
        except ToolExecutionError as error:
            return ToolResult.failure(call, error.code, str(error))
        except TimeoutError:
            return ToolResult.failure(
                call,
                ErrorCode.ETIMEDOUT,
                f'tool "{call.name}" exceeded its timeout',
            )
        except Exception as error:
            return ToolResult.failure(
                call,
                ErrorCode.EIO,
                f'tool "{call.name}" failed: {error}',
            )
        return ToolResult.success(call, output)
