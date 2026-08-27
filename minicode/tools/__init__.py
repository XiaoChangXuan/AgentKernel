from __future__ import annotations

from agentkernel.session import Session
from agentkernel.resources import ResourceService
from agentkernel.tool_effects import ReconcileResult, ReconcileStatus, ToolEffectKind
from agentkernel.capabilities import CapabilityGrant, TOOL_EXECUTE_ACTION
from agentkernel.tools import ToolDefinition, ToolRegistry

from minicode.workspace import WorkspaceIdentity

from .apply_patch import apply_patch, apply_patch_handler
from .list_files import list_files, list_files_handler
from .read_file import read_file, read_file_handler
from .run_command import (
    CommandRunner,
    DefaultShellHostPolicy,
    ShellHostPolicy,
    run_command,
    run_command_handler,
)
from .schemas import (
    APPLY_PATCH_NAME,
    LIST_FILES_NAME,
    READ_FILE_NAME,
    RUN_COMMAND_NAME,
    SEARCH_FILES_NAME,
    SHELL_EXECUTE_ACTION,
    WORKSPACE_READ_ACTION,
    WORKSPACE_WRITE_ACTION,
    apply_patch_schema,
    list_files_schema,
    read_file_schema,
    run_command_schema,
    search_files_schema,
    shell_scope,
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


def apply_patch_tool_definition(
    workspace: WorkspaceIdentity,
    *,
    session: Session | None = None,
) -> ToolDefinition:
    async def reconcile(context) -> ReconcileResult:  # type: ignore[no-untyped-def]
        if session is None:
            return ReconcileResult(
                ReconcileStatus.UNKNOWN,
                message="durable patch session is unavailable for reconciliation",
            )
        from minicode.durable_patch import durable_apply_patch_reconcile_handler

        return await durable_apply_patch_reconcile_handler(workspace, session, context)

    return ToolDefinition(
        schema=apply_patch_schema(),
        handler=lambda arguments, context: apply_patch_handler(workspace, arguments, context),
        required_action=TOOL_EXECUTE_ACTION,
        required_resource=tool_resource(APPLY_PATCH_NAME),
        effect_kind=ToolEffectKind.RECONCILABLE_MUTATION,
        reconcile_handler=reconcile,
    )


def run_command_tool_definition(
    workspace: WorkspaceIdentity,
    *,
    resources: ResourceService | None = None,
    policy: ShellHostPolicy | None = None,
    runner: CommandRunner | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        schema=run_command_schema(),
        handler=lambda arguments, context: run_command_handler(
            workspace,
            arguments,
            context,
            resources=resources,
            policy=policy,
            runner=runner,
        ),
        required_action=TOOL_EXECUTE_ACTION,
        required_resource=tool_resource(RUN_COMMAND_NAME),
    )


def minicode_tool_definitions(
    workspace: WorkspaceIdentity,
    *,
    session: Session | None = None,
    resources: ResourceService | None = None,
    policy: ShellHostPolicy | None = None,
    runner: CommandRunner | None = None,
) -> tuple[ToolDefinition, ...]:
    return (
        *read_only_tool_definitions(workspace),
        apply_patch_tool_definition(workspace, session=session),
        run_command_tool_definition(
            workspace,
            resources=resources,
            policy=policy,
            runner=runner,
        ),
    )


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
    *,
    session: Session | None = None,
) -> ToolRegistry:
    registry.register(apply_patch_tool_definition(workspace, session=session))
    return registry


def register_run_command_tool(
    registry: ToolRegistry,
    workspace: WorkspaceIdentity,
    *,
    resources: ResourceService | None = None,
    policy: ShellHostPolicy | None = None,
    runner: CommandRunner | None = None,
) -> ToolRegistry:
    registry.register(
        run_command_tool_definition(
            workspace,
            resources=resources,
            policy=policy,
            runner=runner,
        )
    )
    return registry


def register_minicode_tools(
    registry: ToolRegistry,
    workspace: WorkspaceIdentity,
    *,
    session: Session | None = None,
    resources: ResourceService | None = None,
    policy: ShellHostPolicy | None = None,
    runner: CommandRunner | None = None,
) -> ToolRegistry:
    for definition in minicode_tool_definitions(
        workspace,
        session=session,
        resources=resources,
        policy=policy,
        runner=runner,
    ):
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


def run_command_capability_grants(
    *,
    agent_id: str,
    workspace: WorkspaceIdentity,
) -> tuple[CapabilityGrant, ...]:
    return (
        CapabilityGrant(agent_id, TOOL_EXECUTE_ACTION, tool_resource(RUN_COMMAND_NAME)),
        CapabilityGrant(agent_id, SHELL_EXECUTE_ACTION, shell_scope(workspace.workspace_id)),
    )


def minicode_capability_grants(
    *,
    agent_id: str,
    workspace: WorkspaceIdentity,
) -> tuple[CapabilityGrant, ...]:
    return (
        *read_only_capability_grants(agent_id=agent_id, workspace=workspace),
        *apply_patch_capability_grants(agent_id=agent_id, workspace=workspace),
        *run_command_capability_grants(agent_id=agent_id, workspace=workspace),
    )


__all__ = [
    "APPLY_PATCH_NAME",
    "CommandRunner",
    "DefaultShellHostPolicy",
    "LIST_FILES_NAME",
    "READ_FILE_NAME",
    "RUN_COMMAND_NAME",
    "SEARCH_FILES_NAME",
    "SHELL_EXECUTE_ACTION",
    "ShellHostPolicy",
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
    "register_run_command_tool",
    "run_command",
    "run_command_capability_grants",
    "run_command_tool_definition",
    "search_files",
    "shell_scope",
    "tool_resource",
    "workspace_scope",
]

