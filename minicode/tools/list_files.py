from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from agentkernel.protocol import JsonValue
from agentkernel.tools import ToolExecutionContext

from minicode.errors import MiniCodeError
from minicode.workspace import WorkspaceIdentity

from .common import (
    argument_string,
    optional_bool,
    optional_int,
    require_workspace_read,
    should_hide_path,
    tool_error_from_minicode,
)
from .schemas import (
    DEFAULT_MAX_LIST_ENTRIES,
    MAX_LIST_ENTRIES,
    error_result,
    success_result,
)


def list_files(workspace: WorkspaceIdentity, arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        return _list_files(workspace, dict(arguments))
    except MiniCodeError as error:
        return error_result(error.code, error.message, retryable=error.retryable)


async def list_files_handler(
    workspace: WorkspaceIdentity,
    arguments: Mapping[str, JsonValue],
    context: ToolExecutionContext,
) -> JsonValue:
    try:
        normalized = workspace.normalize_path(
            argument_string(dict(arguments), "path", "."),
            must_exist=True,
        )
        require_workspace_read(
            workspace=workspace,
            relative_path=normalized.relative_path,
            context=context,
        )
    except MiniCodeError as error:
        raise tool_error_from_minicode(error) from error
    return _list_files(workspace, dict(arguments))


def _list_files(workspace: WorkspaceIdentity, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    requested_path = argument_string(arguments, "path", ".")
    recursive = optional_bool(arguments, "recursive", False)
    max_entries = optional_int(
        arguments,
        "max_entries",
        DEFAULT_MAX_LIST_ENTRIES,
        minimum=1,
        maximum=MAX_LIST_ENTRIES,
    )
    include_hidden = optional_bool(arguments, "include_hidden", False)
    normalized = workspace.normalize_path(requested_path, must_exist=True)
    target = normalized.absolute_path
    if not target.is_dir():
        raise MiniCodeError(
            code="not_directory",
            message=f"Path is not a directory: {normalized.relative_path}",
            retryable=False,
        )

    candidates = _recursive_entries(workspace.root, target) if recursive else _direct_entries(target)
    entries: list[dict[str, JsonValue]] = []
    truncated = False
    for entry in candidates:
        if should_hide_path(entry, workspace.root, include_hidden=include_hidden):
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
        entries.append(_entry_payload(workspace.root, entry))

    return success_result(
        {
            "root": workspace.workspace_id,
            "path": normalized.relative_path,
            "entries": entries,
            "truncated": truncated,
            "entry_count": len(entries),
            "metadata": {
                "tool_name": "list_files",
                "normalized_path": normalized.relative_path,
                "recursive": recursive,
                "include_hidden": include_hidden,
                "max_entries": max_entries,
            },
        }
    )


def _direct_entries(target: Path) -> list[Path]:
    return sorted(target.iterdir(), key=lambda item: item.name.lower())


def _recursive_entries(root: Path, target: Path) -> list[Path]:
    entries = [path for path in target.rglob("*")]
    return sorted(entries, key=lambda item: item.relative_to(root).as_posix().lower())


def _entry_payload(root: Path, path: Path) -> dict[str, JsonValue]:
    stat = path.lstat()
    if path.is_symlink():
        entry_type = "symlink"
    elif path.is_dir():
        entry_type = "directory"
    elif path.is_file():
        entry_type = "file"
    else:
        entry_type = "other"
    payload: dict[str, JsonValue] = {
        "path": path.relative_to(root).as_posix(),
        "type": entry_type,
    }
    if entry_type == "file":
        payload["size"] = stat.st_size
    return payload

