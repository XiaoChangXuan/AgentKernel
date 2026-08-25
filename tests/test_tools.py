from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Mapping

import pytest

from agentkernel import (
    Agent,
    CapabilityBoundError,
    ErrorCode,
    Session,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


def math_tool() -> ToolDefinition:
    return ToolDefinition(
        schema=ToolSchema(
            name="math.add",
            description="Add two integers.",
            input_schema={"type": "object"},
        ),
        handler=add,
        required_capability="math.add",
    )


def test_tool_succeeds_with_capability() -> None:
    registry = ToolRegistry()
    registry.register(math_tool())
    agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"math.add"},
    )
    call = ToolCall("call-1", "math.add", {"left": 20, "right": 22})

    result = asyncio.run(registry.execute(call, agent.control))

    assert result.ok is True
    assert result.output == 42
    assert [schema.name for schema in registry.model_schemas(agent.control)] == [
        "math.add"
    ]


def test_tool_is_denied_without_capability() -> None:
    registry = ToolRegistry()
    registry.register(math_tool())
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))
    call = ToolCall("call-1", "math.add", {"left": 20, "right": 22})

    result = asyncio.run(registry.execute(call, agent.control))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert registry.model_schemas(agent.control) == ()


def test_missing_and_failing_tools_return_stable_codes() -> None:
    registry = ToolRegistry()
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))
    missing = ToolCall("call-1", "missing", {})

    missing_result = asyncio.run(registry.execute(missing, agent.control))

    assert missing_result.error is not None
    assert missing_result.error.code is ErrorCode.ENOENT

    async def fail(
        _arguments: Mapping[str, JsonValue],
        _context: ToolExecutionContext,
    ) -> JsonValue:
        raise RuntimeError("boom")

    registry.register(
        ToolDefinition(
            schema=ToolSchema("fail", "Fail.", {"type": "object"}),
            handler=fail,
        )
    )
    failure_result = asyncio.run(
        registry.execute(ToolCall("call-2", "fail", {}), agent.control)
    )

    assert failure_result.error is not None
    assert failure_result.error.code is ErrorCode.EIO


def test_capability_bounding_set_is_enforced_and_immutable() -> None:
    with pytest.raises(CapabilityBoundError):
        Agent.create(
            agent_id="agent-1",
            session=Session("session-1"),
            capabilities={"math.add"},
            capability_bounding_set=set(),
        )

    agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"math.add"},
    )
    with pytest.raises(FrozenInstanceError):
        agent.control.capabilities = frozenset({"admin"})  # type: ignore[misc]


def test_tool_output_must_be_lossless_json() -> None:
    async def return_tuple(
        _arguments: Mapping[str, JsonValue],
        _context: ToolExecutionContext,
    ) -> JsonValue:
        return (1, 2)  # type: ignore[return-value]

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema("tuple", "Return a tuple.", {"type": "object"}),
            handler=return_tuple,
        )
    )
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))

    result = asyncio.run(
        registry.execute(ToolCall("call-1", "tuple", {}), agent.control)
    )

    assert result.error is not None
    assert result.error.code is ErrorCode.EIO
