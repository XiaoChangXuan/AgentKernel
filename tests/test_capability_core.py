from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agentkernel import (
    Agent,
    AuthorizationRequest,
    CapabilityEvaluator,
    CapabilityGrant,
    ErrorCode,
    Session,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
)
from agentkernel.protocol import JsonValue


def test_allow_basic_capability() -> None:
    grant = CapabilityGrant(
        subject="agent-1",
        action="tool.execute",
        resource_scope="tool://math.add",
    )
    evaluator = CapabilityEvaluator([grant])

    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id="agent-1",
            action="tool.execute",
            resource="tool://math.add",
        )
    )

    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.matched_grant == grant


def test_deny_missing_capability() -> None:
    evaluator = CapabilityEvaluator(
        [
            CapabilityGrant(
                subject="agent-1",
                action="resource.read",
                resource_scope="artifact://project-a/**",
            )
        ]
    )

    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id="agent-1",
            action="resource.write",
            resource="artifact://project-a/file.txt",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "no_matching_grant"
    assert decision.matched_grant is None


def test_resource_scope_isolation() -> None:
    evaluator = CapabilityEvaluator(
        [
            CapabilityGrant(
                subject="agent-1",
                action="resource.read",
                resource_scope="artifact://project-a/**",
            )
        ]
    )

    allowed = evaluator.authorize(
        AuthorizationRequest(
            agent_id="agent-1",
            action="resource.read",
            resource="artifact://project-a/file.txt",
        )
    )
    denied = evaluator.authorize(
        AuthorizationRequest(
            agent_id="agent-1",
            action="resource.read",
            resource="artifact://project-b/file.txt",
        )
    )

    assert allowed.allowed is True
    assert denied.allowed is False


def test_constraint_placeholder_is_preserved() -> None:
    grant = CapabilityGrant(
        subject="agent-1",
        action="resource.read",
        resource_scope="artifact://project-a/**",
        constraints={"max_bytes": 1024},
    )
    evaluator = CapabilityEvaluator([grant])

    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id="agent-1",
            action="resource.read",
            resource="artifact://project-a/file.txt",
        )
    )

    assert decision.allowed is True
    assert decision.matched_grant is not None
    assert decision.matched_grant.constraints["max_bytes"] == 1024
    with pytest.raises(TypeError):
        decision.matched_grant.constraints["max_bytes"] = 2048  # type: ignore[index]


def test_legacy_required_capability_compatibility() -> None:
    async def add(
        arguments: Mapping[str, JsonValue],
        _context: ToolExecutionContext,
    ) -> JsonValue:
        return int(arguments["left"]) + int(arguments["right"])

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema(
                name="math.add",
                description="Add two integers.",
                input_schema={"type": "object"},
            ),
            handler=add,
            required_capability="math.add",
        )
    )
    allowed_agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"math.add"},
    )
    denied_agent = Agent.create(
        agent_id="agent-2",
        session=Session("session-2"),
        capabilities={"math.subtract"},
    )
    call = ToolCall("call-1", "math.add", {"left": 20, "right": 22})

    allowed_result = asyncio.run(registry.execute(call, allowed_agent.control))
    denied_result = asyncio.run(registry.execute(call, denied_agent.control))

    assert allowed_result.ok is True
    assert allowed_result.output == 42
    assert [schema.name for schema in registry.model_schemas(allowed_agent.control)] == [
        "math.add"
    ]
    assert denied_result.ok is False
    assert denied_result.error is not None
    assert denied_result.error.code is ErrorCode.EACCES
    assert registry.model_schemas(denied_agent.control) == ()
