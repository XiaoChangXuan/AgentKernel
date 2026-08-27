"""Codex-style patch parsing and workspace-safe application."""

from .applier import (
    PatchApplyResult,
    PatchFilePlan,
    PatchMutationPlan,
    apply_mutation_plan,
    apply_parsed_patch,
    apply_patch_text,
    hash_file_or_absent,
    plan_parsed_patch,
)
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
    "PatchFilePlan",
    "PatchMutationPlan",
    "PatchError",
    "PatchOperation",
    "UpdateFile",
    "apply_mutation_plan",
    "apply_parsed_patch",
    "apply_patch_text",
    "hash_file_or_absent",
    "plan_parsed_patch",
    "parse_patch",
]
