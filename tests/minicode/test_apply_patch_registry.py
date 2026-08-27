from __future__ import annotations

import asyncio

from agentkernel import Agent, CapabilityGrant, ErrorCode, Session, TOOL_EXECUTE_ACTION, ToolCall, ToolRegistry
from minicode.testing import make_minicode_workspace
from minicode.tools import (
    APPLY_PATCH_NAME,
    WORKSPACE_WRITE_ACTION,
    apply_patch_capability_grants,
    register_apply_patch_tool,
    tool_resource,
    workspace_scope,
)
from minicode.workspace import discover_workspace


def _agent(agent_id: str, grants: tuple[CapabilityGrant, ...] = ()):
    return Agent.create(
        agent_id=agent_id,
        session=Session(f"session-{agent_id}"),
        capability_grants=grants,
    )


def _registry_workspace_agent(tmp_path, grants=()):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    registry = register_apply_patch_tool(ToolRegistry(), workspace)
    agent = _agent("agent-1", tuple(grants))
    return fixture, workspace, registry, agent


def test_apply_patch_schema_is_model_visible_when_authorized(tmp_path):
    _fixture, workspace, registry, _agent_without = _registry_workspace_agent(tmp_path)
    agent = _agent("agent-1", apply_patch_capability_grants(agent_id="agent-1", workspace=workspace))

    schemas = registry.model_schemas(agent.control)

    assert [schema.name for schema in schemas] == [APPLY_PATCH_NAME]
    assert schemas[0].input_schema["required"] == ["patch"]


def test_apply_patch_authorized_execution_succeeds(tmp_path):
    fixture, workspace, registry, _agent_without = _registry_workspace_agent(tmp_path)
    agent = _agent("agent-1", apply_patch_capability_grants(agent_id="agent-1", workspace=workspace))

    result = asyncio.run(
        registry.execute(
            ToolCall(
                "call-patch",
                APPLY_PATCH_NAME,
                {
                    "patch": (
                        "*** Begin Patch\n"
                        "*** Update File: calculator.py\n"
                        "@@\n"
                        "-    return a / b\n"
                        "+    return a // b\n"
                        "*** End Patch"
                    )
                },
            ),
            agent.control,
        )
    )

    assert result.ok is True
    assert result.output["ok"] is True  # type: ignore[index]
    assert result.output["changed_files"] == ["calculator.py"]  # type: ignore[index]
    assert "a // b" in fixture.calculator.read_text(encoding="utf-8")


def test_apply_patch_missing_tool_grant_hides_and_denies_execution(tmp_path):
    _fixture, workspace, registry, _agent_without = _registry_workspace_agent(tmp_path)
    agent = _agent(
        "agent-1",
        (CapabilityGrant("agent-1", WORKSPACE_WRITE_ACTION, workspace_scope(workspace.workspace_id)),),
    )

    result = asyncio.run(
        registry.execute(
            ToolCall("call-patch", APPLY_PATCH_NAME, {"patch": "*** Begin Patch\n*** Add File: x.txt\n+x\n*** End Patch"}),
            agent.control,
        )
    )

    assert registry.model_schemas(agent.control) == ()
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES


def test_apply_patch_workspace_write_grant_required_at_handler_boundary(tmp_path):
    fixture, _workspace, registry, _agent_without = _registry_workspace_agent(tmp_path)
    agent = _agent(
        "agent-1",
        (CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(APPLY_PATCH_NAME)),),
    )

    result = asyncio.run(
        registry.execute(
            ToolCall(
                "call-patch",
                APPLY_PATCH_NAME,
                {"patch": "*** Begin Patch\n*** Add File: denied.txt\n+nope\n*** End Patch"},
            ),
            agent.control,
        )
    )

    assert [schema.name for schema in registry.model_schemas(agent.control)] == [APPLY_PATCH_NAME]
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert not (fixture.root / "denied.txt").exists()


def test_apply_patch_workspace_scope_isolates_other_workspaces(tmp_path):
    fixture, _workspace, registry, _agent_without = _registry_workspace_agent(tmp_path)
    agent = _agent(
        "agent-1",
        (
            CapabilityGrant("agent-1", TOOL_EXECUTE_ACTION, tool_resource(APPLY_PATCH_NAME)),
            CapabilityGrant("agent-1", WORKSPACE_WRITE_ACTION, "workspace://other-workspace/**"),
        ),
    )

    result = asyncio.run(
        registry.execute(
            ToolCall(
                "call-patch",
                APPLY_PATCH_NAME,
                {"patch": "*** Begin Patch\n*** Add File: denied.txt\n+nope\n*** End Patch"},
            ),
            agent.control,
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.EACCES
    assert not (fixture.root / "denied.txt").exists()
