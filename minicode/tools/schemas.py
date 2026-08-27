from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from agentkernel.protocol import JsonValue, ToolSchema


LIST_FILES_NAME: Final = "list_files"
SEARCH_FILES_NAME: Final = "search_files"
READ_FILE_NAME: Final = "read_file"
APPLY_PATCH_NAME: Final = "apply_patch"

MINICODE_TOOL_PREFIX: Final = "tool://minicode"
WORKSPACE_READ_ACTION: Final = "workspace.read"
WORKSPACE_WRITE_ACTION: Final = "workspace.write"

DEFAULT_MAX_LIST_ENTRIES: Final = 100
MAX_LIST_ENTRIES: Final = 1_000
DEFAULT_MAX_SEARCH_MATCHES: Final = 50
MAX_SEARCH_MATCHES: Final = 500
MAX_CONTEXT_LINES: Final = 5
DEFAULT_READ_MAX_BYTES: Final = 32_000
MAX_READ_BYTES: Final = 64_000
LINE_PREVIEW_CHARS: Final = 400

DEFAULT_IGNORED_NAMES: Final = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)


def tool_resource(tool_name: str) -> str:
    return f"{MINICODE_TOOL_PREFIX}/{tool_name}"


def workspace_scope(workspace_id: str) -> str:
    return f"workspace://{workspace_id}/**"


def workspace_resource(workspace_id: str, relative_path: str) -> str:
    if relative_path == ".":
        return f"workspace://{workspace_id}"
    return f"workspace://{workspace_id}/{relative_path}"


def list_files_schema() -> ToolSchema:
    return ToolSchema(
        name=LIST_FILES_NAME,
        description="List files and directories inside the MiniCode workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": False},
                "max_entries": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIST_ENTRIES,
                    "default": DEFAULT_MAX_LIST_ENTRIES,
                },
                "include_hidden": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    )


def search_files_schema() -> ToolSchema:
    return ToolSchema(
        name=SEARCH_FILES_NAME,
        description="Search UTF-8 text files inside the MiniCode workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "glob": {"type": "string"},
                "case_sensitive": {"type": "boolean", "default": False},
                "max_matches": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_MATCHES,
                    "default": DEFAULT_MAX_SEARCH_MATCHES,
                },
                "context_lines": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_CONTEXT_LINES,
                    "default": 0,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def read_file_schema() -> ToolSchema:
    return ToolSchema(
        name=READ_FILE_NAME,
        description="Read a bounded, line-numbered UTF-8 file range inside the MiniCode workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_READ_BYTES,
                    "default": DEFAULT_READ_MAX_BYTES,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )


def apply_patch_schema() -> ToolSchema:
    return ToolSchema(
        name=APPLY_PATCH_NAME,
        description="Apply a Codex-style patch inside the MiniCode workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "patch": {"type": "string"},
            },
            "required": ["patch"],
            "additionalProperties": False,
        },
    )


def error_result(code: str, message: str, *, retryable: bool = False) -> dict[str, JsonValue]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def success_result(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {"ok": True, **dict(payload)}

