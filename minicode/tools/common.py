from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable

from agentkernel.capabilities import AuthorizationRequest
from agentkernel.protocol import ErrorCode, JsonValue
from agentkernel.tools import ToolExecutionContext, ToolExecutionError

from minicode.errors import MiniCodeError
from minicode.workspace import WorkspaceIdentity

from .schemas import (
    DEFAULT_IGNORED_NAMES,
    WORKSPACE_READ_ACTION,
    WORKSPACE_WRITE_ACTION,
    workspace_resource,
)


def argument_string(arguments: dict[str, JsonValue], name: str, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str) or not value:
        raise MiniCodeError(
            code="invalid_argument",
            message=f"{name} must be a non-empty string",
            retryable=False,
        )
    return value


def optional_int(
    arguments: dict[str, JsonValue],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MiniCodeError(
            code="invalid_argument",
            message=f"{name} must be an integer",
            retryable=False,
        )
    if value < minimum:
        raise MiniCodeError(
            code="invalid_argument",
            message=f"{name} must be at least {minimum}",
            retryable=False,
        )
    return min(value, maximum)


def optional_bool(arguments: dict[str, JsonValue], name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise MiniCodeError(
            code="invalid_argument",
            message=f"{name} must be a boolean",
            retryable=False,
        )
    return value


def require_workspace_read(
    *,
    workspace: WorkspaceIdentity,
    relative_path: str,
    context: ToolExecutionContext,
) -> None:
    evaluator = context.capability_evaluator
    if evaluator is None:
        raise ToolExecutionError(ErrorCode.EACCES, "workspace read authorization unavailable")
    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=context.agent_id,
            action=WORKSPACE_READ_ACTION,
            resource=workspace_resource(workspace.workspace_id, relative_path),
        )
    )
    if not decision.allowed:
        raise ToolExecutionError(ErrorCode.EACCES, decision.reason)


def require_workspace_write(
    *,
    workspace: WorkspaceIdentity,
    relative_path: str,
    context: ToolExecutionContext,
) -> None:
    evaluator = context.capability_evaluator
    if evaluator is None:
        raise ToolExecutionError(ErrorCode.EACCES, "workspace write authorization unavailable")
    decision = evaluator.authorize(
        AuthorizationRequest(
            agent_id=context.agent_id,
            action=WORKSPACE_WRITE_ACTION,
            resource=workspace_resource(workspace.workspace_id, relative_path),
        )
    )
    if not decision.allowed:
        raise ToolExecutionError(ErrorCode.EACCES, decision.reason)


def should_hide_path(path: Path, root: Path, *, include_hidden: bool) -> bool:
    if include_hidden:
        return False
    relative_parts = path.relative_to(root).parts
    return any(part.startswith(".") or part in DEFAULT_IGNORED_NAMES for part in relative_parts)


def iter_search_files(root: Path, target: Path, *, glob: str | None) -> Iterable[Path]:
    if target.is_file():
        files = [target]
    else:
        files = [path for path in target.rglob("*") if path.is_file() and not path.is_symlink()]
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if should_hide_path(path, root, include_hidden=False):
            continue
        if glob is not None and not _glob_matches(relative, glob):
            continue
        yield path


def _glob_matches(relative_path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(relative_path, pattern) or fnmatch.fnmatchcase(
        Path(relative_path).name,
        pattern,
    )


def is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def bounded_line_preview(line: str, *, limit: int) -> tuple[str, bool]:
    if len(line) <= limit:
        return line, False
    return line[:limit], True


def tool_error_from_minicode(error: MiniCodeError) -> ToolExecutionError:
    code = ErrorCode.EACCES if error.code == "outside_workspace" else ErrorCode.EINVAL
    if error.code == "path_not_found":
        code = ErrorCode.ENOENT
    return ToolExecutionError(code, error.message)

