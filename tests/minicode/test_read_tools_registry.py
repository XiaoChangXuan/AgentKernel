from __future__ import annotations

import asyncio

from agentkernel import (
    Agent,
    CapabilityGrant,
    ErrorCode,
    Session,
    TOOL_EXECUTE_ACTION,
    ToolCall,
    ToolRegistry,
)
from minicode.testing import make_minicode_workspace
from minicode.tools import (
    LIST_FILES_NAME,
    READ_FILE_NAME,
    SEARCH_FILES_NAME,
    WORKSPACE_READ_ACTION,
    read_only_capability_grants,
    register_read_only_tools,
    tool_resource,
    workspace_scope,
)
from minicode.workspace import discover_workspace


def _registry_and_workspace(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    registry = register_read_only_tools(ToolRegistry(), workspace)
    return fixture, workspace, registry


def _agent(agent_id: str, grants: tuple[CapabilityGrant, ...] = ()):
    return Agent.create(
        agent_id=agent_id,
        session=Session(f"session-{agent_id}"),
        capability_grants=grants,
    )


def test_read_only_tools_register_model_schemas(tmp_path):
    _fixture, workspace, registry = _registry_and_workspace(tmp_path)
    agent = _agent(
        "agent-1",
        read_only_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    schemas = registry.model_schemas(agent.control)

    assert [schema.name for schema in schemas] == [
        LIST_FILES_NAME,
        SEARCH_FILES_NAME,
        READ_FILE_NAME,
    ]
    assert schemas[0].input_schema["additionalProperties"] is False


def test_missing_tool_execute_grant_hides_tools(tmp_path):
    _fixture, workspace, registry = _registry_and_workspace(tmp_path)
    agent = _agent(
        "agent-1",
        (
            CapabilityGrant(
                "agent-1",
                WORKSPACE_READ_ACTION,
                workspace_scope(workspace.workspace_id),
            ),
        ),
    )

    assert registry.model_schemas(agent.control) == ()


def test_authorized_execution_succeeds_through_tool_registry(tmp_path):
    _fixture, workspace, registry = _registry_and_workspace(tmp_path)
    agent = _agent(
        "agent-1",
        read_only_capability_grants(agent_id="agent-1", workspace=workspace),
    )

    result = asyncio.run(
        registry.execute(
            ToolCall("call-read", READ_FILE_NAME, {"path": "src/app.py", "start_line": 3, "end_line": 4}),
            agent.control,
        )
    )

    assert result.ok is True
    assert result.output["ok"] is True  # type: ignore[index]
    assert result.output["path"] == "src/app.py"  # type: ignore[index]


def test_unauthorized_tool_execution_is_denied_by_registry(tmp_path):
    _fixture, _workspace, registry = _registry_and_workspace(tmp_path)
    agent = _agent("agent-1")

    result = asyncio.run(
        registry.execute(ToolCall("call-list", LIST_FILES_NAME, {}), agent.control)
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_workspace_read_grant_is_required_at_handler_boundary(tmp_path):
    _fixture, _workspace, registry = _registry_and_workspace(tmp_path)
    agent = _agent(
        "agent-1",
        (
            CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(LIST_FILES_NAME)),
            CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(SEARCH_FILES_NAME)),
            CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(READ_FILE_NAME)),
        ),
    )

    result = asyncio.run(
        registry.execute(ToolCall("call-read", READ_FILE_NAME, {"path": "calculator.py"}), agent.control)
    )

    assert [schema.name for schema in registry.model_schemas(agent.control)] == [
        LIST_FILES_NAME,
        SEARCH_FILES_NAME,
        READ_FILE_NAME,
    ]
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_workspace_scope_isolates_other_workspaces(tmp_path):
    _fixture, workspace, registry = _registry_and_workspace(tmp_path)
    agent = _agent(
        "agent-1",
        (
            CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(READ_FILE_NAME)),
            CapabilityGrant(
                "agent-1",
                WORKSPACE_READ_ACTION,
                "workspace://other-workspace/**",
            ),
        ),
    )

    result = asyncio.run(
        registry.execute(ToolCall("call-read", READ_FILE_NAME, {"path": "calculator.py"}), agent.control)
    )

    assert workspace.workspace_id != "other-workspace"
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES

