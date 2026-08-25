"""Tool registry, model schema projection, and guarded execution."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, TypeAlias

from .agent import AgentControlBlock
from .protocol import (
    ErrorCode,
    JsonValue,
    ToolCall,
    ToolResult,
    ToolSchema,
    is_json_value,
)


class ToolConcurrency(StrEnum):
    """Reserved execution classification; V0.1 dispatch remains sequential."""

    PARALLEL = "parallel"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Kernel metadata passed to a tool implementation, never to the model."""

    agent_id: str
    session_id: str
    call_id: str


ToolHandler: TypeAlias = Callable[
    [Mapping[str, JsonValue], ToolExecutionContext], Awaitable[JsonValue]
]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Host runtime definition paired with a model-visible schema."""

    schema: ToolSchema
    handler: ToolHandler
    required_capability: str | None = None
    timeout_seconds: float | None = None
    concurrency: ToolConcurrency = ToolConcurrency.PARALLEL

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds must be positive")

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
        )

    def model_schemas(self, agent: AgentControlBlock) -> tuple[ToolSchema, ...]:
        """Project only model-facing fields for tools the agent may execute."""

        schemas: list[ToolSchema] = []
        for definition in self._definitions.values():
            capability = definition.required_capability
            if capability is not None and not agent.has_capability(capability):
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

        definition = self._definitions.get(call.name)
        if definition is None:
            return ToolResult.failure(
                call,
                ErrorCode.ENOENT,
                f'tool "{call.name}" is not registered',
            )
        capability = definition.required_capability
        if capability is not None and not agent.has_capability(capability):
            return ToolResult.failure(
                call,
                ErrorCode.EACCES,
                f'agent lacks required capability "{capability}"',
            )
        context = ToolExecutionContext(
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            call_id=call.call_id,
        )
        try:
            output = await definition.execute(call.arguments, context)
            if not is_json_value(output):
                raise TypeError("tool returned a value that is not lossless JSON")
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
