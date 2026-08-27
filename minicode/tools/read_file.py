from __future__ import annotations

import hashlib
from collections.abc import Mapping

from agentkernel.protocol import JsonValue
from agentkernel.tools import ToolExecutionContext

from minicode.errors import MiniCodeError
from minicode.workspace import WorkspaceIdentity

from .common import (
    argument_string,
    is_probably_binary,
    optional_int,
    require_workspace_read,
    tool_error_from_minicode,
)
from .schemas import (
    DEFAULT_READ_MAX_BYTES,
    MAX_READ_BYTES,
    error_result,
    success_result,
)


def read_file(workspace: WorkspaceIdentity, arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        return _read_file(workspace, dict(arguments))
    except MiniCodeError as error:
        return error_result(error.code, error.message, retryable=error.retryable)


async def read_file_handler(
    workspace: WorkspaceIdentity,
    arguments: Mapping[str, JsonValue],
    context: ToolExecutionContext,
) -> JsonValue:
    try:
        normalized = workspace.normalize_path(
            argument_string(dict(arguments), "path"),
            must_exist=True,
        )
        require_workspace_read(
            workspace=workspace,
            relative_path=normalized.relative_path,
            context=context,
        )
    except MiniCodeError as error:
        raise tool_error_from_minicode(error) from error
    return _read_file(workspace, dict(arguments))


def _read_file(workspace: WorkspaceIdentity, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    requested_path = argument_string(arguments, "path")
    max_bytes = optional_int(
        arguments,
        "max_bytes",
        DEFAULT_READ_MAX_BYTES,
        minimum=1,
        maximum=MAX_READ_BYTES,
    )
    normalized = workspace.normalize_path(requested_path, must_exist=True)
    path = normalized.absolute_path
    if path.is_dir():
        raise MiniCodeError(
            code="is_directory",
            message=f"Path is a directory: {normalized.relative_path}",
            retryable=False,
        )
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if is_probably_binary(raw):
        raise MiniCodeError(
            code="binary_file",
            message=f"File is binary: {normalized.relative_path}",
            retryable=False,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MiniCodeError(
            code="unsupported_encoding",
            message=f"File is not valid UTF-8: {normalized.relative_path}",
            retryable=False,
        ) from exc

    lines = text.splitlines()
    total_lines = len(lines)
    start_line = _line_argument(arguments, "start_line", 1)
    end_line = _line_argument(arguments, "end_line", total_lines if total_lines else 1)
    if total_lines == 0:
        start_line = 1
        end_line = 0
    elif start_line > total_lines or end_line > total_lines or start_line > end_line:
        raise MiniCodeError(
            code="invalid_range",
            message="line range must be 1-based, inclusive, and inside the file",
            retryable=False,
        )

    selected = lines[start_line - 1 : end_line] if total_lines else []
    content, returned_end_line, truncated = _number_lines(
        selected,
        start_line=start_line,
        max_bytes=max_bytes,
    )

    return success_result(
        {
            "path": normalized.relative_path,
            "start_line": start_line,
            "end_line": returned_end_line,
            "requested_end_line": end_line,
            "total_lines": total_lines,
            "content": content,
            "truncated": truncated,
            "encoding": "utf-8",
            "sha256": digest,
            "metadata": {
                "tool_name": "read_file",
                "normalized_path": normalized.relative_path,
                "max_bytes": max_bytes,
            },
        }
    )


def _number_lines(lines: list[str], *, start_line: int, max_bytes: int) -> tuple[str, int, bool]:
    output: list[str] = []
    used = 0
    returned_end = start_line - 1
    for offset, line in enumerate(lines):
        numbered = f"{start_line + offset}: {line}\n"
        encoded = numbered.encode("utf-8")
        if used + len(encoded) > max_bytes:
            remaining = max_bytes - used
            if remaining > 0:
                output.append(encoded[:remaining].decode("utf-8", errors="ignore"))
                returned_end = start_line + offset
            return "".join(output).rstrip("\n"), returned_end, True
        output.append(numbered)
        used += len(encoded)
        returned_end = start_line + offset
    return "".join(output).rstrip("\n"), returned_end, False


def _line_argument(arguments: dict[str, JsonValue], name: str, default: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MiniCodeError(
            code="invalid_range",
            message=f"{name} must be a positive 1-based line number",
            retryable=False,
        )
    return value
