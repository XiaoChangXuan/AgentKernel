from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agentkernel import (
    Agent,
    CapabilityGrant,
    ErrorCode,
    LocalResourceStore,
    RESOURCE_READ_ACTION,
    ResourceOwner,
    ResourceService,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolSchema,
    resource_tool_definitions,
)
from agentkernel.protocol import JsonValue


async def add(
    arguments: Mapping[str, JsonValue],
    _context: ToolExecutionContext,
) -> JsonValue:
    return int(arguments["left"]) + int(arguments["right"])


def structured_math_tool() -> ToolDefinition:
    return ToolDefinition(
        schema=ToolSchema(
            name="math.add",
            description="Add two integers.",
            input_schema={"type": "object"},
        ),
        handler=add,
        required_action=TOOL_EXECUTE_ACTION,
        required_resource="tool://math.add",
    )


def test_authorized_structured_tool_is_visible_and_executable() -> None:
    registry = ToolRegistry()
    registry.register(structured_math_tool())
    agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capability_grants=(
            CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, "tool://math.add"),
        ),
    )

    assert [schema.name for schema in registry.model_schemas(agent.control)] == [
        "math.add"
    ]
    result = asyncio.run(
        registry.execute(
            ToolCall("call-1", "math.add", {"left": 20, "right": 22}),
            agent.control,
        )
    )

    assert result.ok is True
    assert result.output == 42


def test_unauthorized_structured_tool_is_hidden() -> None:
    registry = ToolRegistry()
    registry.register(structured_math_tool())
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))

    assert registry.model_schemas(agent.control) == ()


def test_unauthorized_structured_tool_execution_is_denied() -> None:
    registry = ToolRegistry()
    registry.register(structured_math_tool())
    agent = Agent.create(agent_id="agent-1", session=Session("session-1"))

    result = asyncio.run(
        registry.execute(
            ToolCall("call-1", "math.add", {"left": 20, "right": 22}),
            agent.control,
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_authorized_resource_read_is_checked_by_resource_service(tmp_path) -> None:
    resources = ResourceService(
        LocalResourceStore(tmp_path / "resources"),
        resource_id_factory=lambda: "res_allowed",
        handle_id_factory=lambda: "hdl_allowed",
    )
    owner = ResourceOwner("agent-1", "session-1")
    handle = resources.create_artifact(
        b"hello capability",
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="logs",
        source_tool_call_id="call-source",
        source_operation_id="op-source",
    )
    registry = ToolRegistry()
    for definition in resource_tool_definitions(resources):
        registry.register(definition)
    agent = Agent.create(
        agent_id=owner.agent_id,
        session=Session(owner.session_id),
        capability_grants=(
            CapabilityGrant(owner.agent_id, TOOL_EXECUTE_ACTION, "tool://resource.read"),
            CapabilityGrant(owner.agent_id, RESOURCE_READ_ACTION, handle.uri),
        ),
    )

    result = asyncio.run(
        registry.execute(
            ToolCall(
                "call-read",
                "resource_read",
                {"uri": handle.uri, "offset": 6, "limit": 10},
            ),
            agent.control,
        )
    )

    assert result.ok is True
    assert result.output["content"] == "capability"  # type: ignore[index]


def test_resource_scope_denial_happens_after_owner_check(tmp_path) -> None:
    resources = ResourceService(
        LocalResourceStore(tmp_path / "resources"),
        resource_id_factory=lambda: "res_denied",
        handle_id_factory=lambda: "hdl_denied",
    )
    owner = ResourceOwner("agent-1", "session-1")
    handle = resources.create_artifact(
        b"secret",
        owner=owner,
        media_type="text/plain",
        encoding="utf-8",
        source_tool_name="logs",
        source_tool_call_id="call-source",
        source_operation_id="op-source",
    )
    registry = ToolRegistry()
    for definition in resource_tool_definitions(resources):
        registry.register(definition)
    agent = Agent.create(
        agent_id=owner.agent_id,
        session=Session(owner.session_id),
        capability_grants=(
            CapabilityGrant(owner.agent_id, TOOL_EXECUTE_ACTION, "tool://resource.read"),
            CapabilityGrant(
                owner.agent_id,
                RESOURCE_READ_ACTION,
                "artifact://res_other",
            ),
        ),
    )

    result = asyncio.run(
        registry.execute(
            ToolCall("call-read", "resource_read", {"uri": handle.uri}),
            agent.control,
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_legacy_required_capability_still_authorizes_tool_execution() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            schema=ToolSchema("math.add", "Add two integers.", {"type": "object"}),
            handler=add,
            required_capability="math.add",
        )
    )
    agent = Agent.create(
        agent_id="agent-1",
        session=Session("session-1"),
        capabilities={"math.add"},
    )

    result = asyncio.run(
        registry.execute(
            ToolCall("call-1", "math.add", {"left": 20, "right": 22}),
            agent.control,
        )
    )

    assert result.ok is True
    assert result.output == 42
    assert [schema.name for schema in registry.model_schemas(agent.control)] == [
        "math.add"
    ]
