from __future__ import annotations

from collections.abc import Mapping

from agentkernel.protocol import JsonValue
from agentkernel.tools import ToolExecutionContext

from minicode.errors import MiniCodeError
from minicode.patch import PatchError, apply_parsed_patch, parse_patch
from minicode.workspace import WorkspaceIdentity

from .common import argument_string, require_workspace_write, tool_error_from_minicode
from .schemas import error_result, success_result


def apply_patch(workspace: WorkspaceIdentity, arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        patch_text = argument_string(dict(arguments), "patch")
        result = apply_parsed_patch(workspace, parse_patch(patch_text))
        return success_result(result.to_dict())
    except PatchError as error:
        return _patch_error_result(error)
    except MiniCodeError as error:
        return error_result(error.code, error.message, retryable=error.retryable)


async def apply_patch_handler(
    workspace: WorkspaceIdentity,
    arguments: Mapping[str, JsonValue],
    context: ToolExecutionContext,
) -> JsonValue:
    try:
        patch_text = argument_string(dict(arguments), "patch")
        parsed = parse_patch(patch_text)
        for path in parsed.paths:
            normalized = workspace.normalize_path(path, must_exist=False)
            require_workspace_write(
                workspace=workspace,
                relative_path=normalized.relative_path,
                context=context,
            )
        result = apply_parsed_patch(workspace, parsed)
    except PatchError as error:
        return _patch_error_result(error)
    except MiniCodeError as error:
        raise tool_error_from_minicode(error) from error
    return success_result(result.to_dict())


def _patch_error_result(error: PatchError) -> dict[str, JsonValue]:
    payload = error.to_dict()
    return {
        "ok": False,
        "applied": False,
        "error": payload,  # type: ignore[dict-item]
    }
