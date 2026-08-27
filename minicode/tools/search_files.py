from __future__ import annotations

from collections.abc import Mapping

from agentkernel.protocol import JsonValue
from agentkernel.tools import ToolExecutionContext

from minicode.errors import MiniCodeError
from minicode.workspace import WorkspaceIdentity

from .common import (
    argument_string,
    bounded_line_preview,
    is_probably_binary,
    iter_search_files,
    optional_bool,
    optional_int,
    require_workspace_read,
    tool_error_from_minicode,
)
from .schemas import (
    DEFAULT_MAX_SEARCH_MATCHES,
    LINE_PREVIEW_CHARS,
    MAX_CONTEXT_LINES,
    MAX_SEARCH_MATCHES,
    error_result,
    success_result,
)


def search_files(workspace: WorkspaceIdentity, arguments: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        return _search_files(workspace, dict(arguments))
    except MiniCodeError as error:
        return error_result(error.code, error.message, retryable=error.retryable)


async def search_files_handler(
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
    return _search_files(workspace, dict(arguments))


def _search_files(workspace: WorkspaceIdentity, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    query = argument_string(arguments, "query")
    requested_path = argument_string(arguments, "path", ".")
    glob = arguments.get("glob")
    if glob is not None and (not isinstance(glob, str) or not glob):
        raise MiniCodeError(
            code="invalid_argument",
            message="glob must be a non-empty string",
            retryable=False,
        )
    case_sensitive = optional_bool(arguments, "case_sensitive", False)
    max_matches = optional_int(
        arguments,
        "max_matches",
        DEFAULT_MAX_SEARCH_MATCHES,
        minimum=1,
        maximum=MAX_SEARCH_MATCHES,
    )
    context_lines = optional_int(
        arguments,
        "context_lines",
        0,
        minimum=0,
        maximum=MAX_CONTEXT_LINES,
    )
    normalized = workspace.normalize_path(requested_path, must_exist=True)
    target = normalized.absolute_path
    if not target.is_dir() and not target.is_file():
        raise MiniCodeError(
            code="invalid_path",
            message=f"Path is neither file nor directory: {normalized.relative_path}",
            retryable=False,
        )

    needle = query if case_sensitive else query.casefold()
    matches: list[dict[str, JsonValue]] = []
    skipped_files: list[dict[str, JsonValue]] = []
    scanned_files = 0
    truncated = False
    for file_path in iter_search_files(workspace.root, target, glob=glob):
        relative_path = file_path.relative_to(workspace.root).as_posix()
        raw = file_path.read_bytes()
        if is_probably_binary(raw):
            skipped_files.append({"path": relative_path, "reason": "binary"})
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped_files.append({"path": relative_path, "reason": "unsupported_encoding"})
            continue
        scanned_files += 1
        lines = text.splitlines()
        for index, line in enumerate(lines):
            haystack = line if case_sensitive else line.casefold()
            if needle not in haystack:
                continue
            if len(matches) >= max_matches:
                truncated = True
                break
            preview, line_truncated = bounded_line_preview(
                line,
                limit=LINE_PREVIEW_CHARS,
            )
            matches.append(
                {
                    "path": relative_path,
                    "line": index + 1,
                    "text": preview,
                    "line_truncated": line_truncated,
                    "context_before": _context(lines, index, -context_lines, 0),
                    "context_after": _context(lines, index, 1, context_lines + 1),
                }
            )
        if truncated:
            break

    return success_result(
        {
            "searched_path": normalized.relative_path,
            "query": query,
            "literal": True,
            "case_sensitive": case_sensitive,
            "glob": glob,
            "matches": matches,
            "truncated": truncated,
            "match_count": len(matches),
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "metadata": {
                "tool_name": "search_files",
                "normalized_path": normalized.relative_path,
                "max_matches": max_matches,
                "context_lines": context_lines,
            },
        }
    )


def _context(
    lines: list[str],
    match_index: int,
    start_delta: int,
    end_delta: int,
) -> list[dict[str, JsonValue]]:
    if start_delta == end_delta:
        return []
    start = max(0, match_index + start_delta)
    end = min(len(lines), match_index + end_delta)
    if start >= end:
        return []
    return [
        {"line": line_index + 1, "text": bounded_line_preview(lines[line_index], limit=LINE_PREVIEW_CHARS)[0]}
        for line_index in range(start, end)
        if line_index != match_index
    ]

