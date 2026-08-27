from __future__ import annotations

from agentkernel.capabilities import CapabilityGrant, TOOL_EXECUTE_ACTION
from agentkernel.tools import ToolDefinition, ToolRegistry

from minicode.workspace import WorkspaceIdentity

from .apply_patch import apply_patch, apply_patch_handler
from .list_files import list_files, list_files_handler
from .read_file import read_file, read_file_handler
from .schemas import (
    APPLY_PATCH_NAME,
    LIST_FILES_NAME,
    READ_FILE_NAME,
    SEARCH_FILES_NAME,
    WORKSPACE_READ_ACTION,
    WORKSPACE_WRITE_ACTION,
    apply_patch_schema,
    list_files_schema,
    read_file_schema,
    search_files_schema,
    tool_resource,
    workspace_scope,
)
from .search_files import search_files, search_files_handler


def read_only_tool_definitions(workspace: WorkspaceIdentity) -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            schema=list_files_schema(),
            handler=lambda arguments, context: list_files_handler(workspace, arguments, context),
            required_action=TOOL_EXECUTE_ACTION,
            required_resource=tool_resource(LIST_FILES_NAME),
        ),
        ToolDefinition(
            schema=search_files_schema(),
            handler=lambda arguments, context: search_files_handler(workspace, arguments, context),
            required_action=TOOL_EXECUTE_ACTION,
            required_resource=tool_resource(SEARCH_FILES_NAME),
        ),
        ToolDefinition(
            schema=read_file_schema(),
            handler=lambda arguments, context: read_file_handler(workspace, arguments, context),
            required_action=TOOL_EXECUTE_ACTION,
            required_resource=tool_resource(READ_FILE_NAME),
        ),
    )


def apply_patch_tool_definition(workspace: WorkspaceIdentity) -> ToolDefinition:
    return ToolDefinition(
        schema=apply_patch_schema(),
        handler=lambda arguments, context: apply_patch_handler(workspace, arguments, context),
        required_action=TOOL_EXECUTE_ACTION,
        required_resource=tool_resource(APPLY_PATCH_NAME),
    )


def minicode_tool_definitions(workspace: WorkspaceIdentity) -> tuple[ToolDefinition, ...]:
    return (*read_only_tool_definitions(workspace), apply_patch_tool_definition(workspace))


def register_read_only_tools(
    registry: ToolRegistry,
    workspace: WorkspaceIdentity,
) -> ToolRegistry:
    for definition in read_only_tool_definitions(workspace):
        registry.register(definition)
    return registry


def register_apply_patch_tool(
    registry: ToolRegistry,
    workspace: WorkspaceIdentity,
) -> ToolRegistry:
    registry.register(apply_patch_tool_definition(workspace))
    return registry


def register_minicode_tools(
    registry: ToolRegistry,
    workspace: WorkspaceIdentity,
) -> ToolRegistry:
    for definition in minicode_tool_definitions(workspace):
        registry.register(definition)
    return registry


def read_only_capability_grants(
    *,
    agent_id: str,
    workspace: WorkspaceIdentity,
) -> tuple[CapabilityGrant, ...]:
    return (
        CapabilityGrant(agent_id, TOOL_EXECUTE_ACTION, tool_resource(LIST_FILES_NAME)),
        CapabilityGrant(agent_id, TOOL_EXECUTE_ACTION, tool_resource(SEARCH_FILES_NAME)),
        CapabilityGrant(agent_id, TOOL_EXECUTE_ACTION, tool_resource(READ_FILE_NAME)),
        CapabilityGrant(agent_id, WORKSPACE_READ_ACTION, workspace_scope(workspace.workspace_id)),
    )


def apply_patch_capability_grants(
    *,
    agent_id: str,
    workspace: WorkspaceIdentity,
) -> tuple[CapabilityGrant, ...]:
    return (
        CapabilityGrant(agent_id, TOOL_EXECUTE_ACTION, tool_resource(APPLY_PATCH_NAME)),
        CapabilityGrant(agent_id, WORKSPACE_WRITE_ACTION, workspace_scope(workspace.workspace_id)),
    )


def minicode_capability_grants(
    *,
    agent_id: str,
    workspace: WorkspaceIdentity,
) -> tuple[CapabilityGrant, ...]:
    return (
        *read_only_capability_grants(agent_id=agent_id, workspace=workspace),
        *apply_patch_capability_grants(agent_id=agent_id, workspace=workspace),
    )


__all__ = [
    "APPLY_PATCH_NAME",
    "LIST_FILES_NAME",
    "READ_FILE_NAME",
    "SEARCH_FILES_NAME",
    "WORKSPACE_READ_ACTION",
    "WORKSPACE_WRITE_ACTION",
    "apply_patch",
    "apply_patch_capability_grants",
    "apply_patch_tool_definition",
    "list_files",
    "minicode_capability_grants",
    "minicode_tool_definitions",
    "read_file",
    "read_only_capability_grants",
    "read_only_tool_definitions",
    "register_apply_patch_tool",
    "register_minicode_tools",
    "register_read_only_tools",
    "search_files",
    "tool_resource",
    "workspace_scope",
]

