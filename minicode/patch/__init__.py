"""Codex-style patch parsing and workspace-safe application."""

from .applier import PatchApplyResult, apply_parsed_patch, apply_patch_text
from .parser import (
    AddFile,
    DeleteFile,
    Hunk,
    HunkLine,
    ParsedPatch,
    PatchError,
    PatchOperation,
    UpdateFile,
    parse_patch,
)

__all__ = [
    "AddFile",
    "DeleteFile",
    "Hunk",
    "HunkLine",
    "ParsedPatch",
    "PatchApplyResult",
    "PatchError",
    "PatchOperation",
    "UpdateFile",
    "apply_parsed_patch",
    "apply_patch_text",
    "parse_patch",
]
