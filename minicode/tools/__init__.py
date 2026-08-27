from __future__ import annotations

from agentkernel.capabilities import CapabilityGrant, TOOL_EXECUTE_ACTION
from agentkernel.tools import ToolDefinition, ToolRegistry

from minicode.workspace import WorkspaceIdentity

from .list_files import list_files, list_files_handler
from .read_file import read_file, read_file_handler
from .schemas import (
    LIST_FILES_NAME,
    READ_FILE_NAME,
    SEARCH_FILES_NAME,
    WORKSPACE_READ_ACTION,
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


def register_read_only_tools(
    registry: ToolRegistry,
    workspace: WorkspaceIdentity,
) -> ToolRegistry:
    for definition in read_only_tool_definitions(workspace):
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


__all__ = [
    "LIST_FILES_NAME",
    "READ_FILE_NAME",
    "SEARCH_FILES_NAME",
    "WORKSPACE_READ_ACTION",
    "list_files",
    "read_file",
    "read_only_capability_grants",
    "read_only_tool_definitions",
    "register_read_only_tools",
    "search_files",
    "tool_resource",
    "workspace_scope",
]

