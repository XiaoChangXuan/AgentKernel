from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from minicode.errors import MiniCodeError


LineKind: TypeAlias = Literal["context", "remove", "add"]


@dataclass(frozen=True, slots=True)
class PatchError(MiniCodeError):
    """Structured parser/applier error with optional bounded diagnostics."""

    diagnostics: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = super().to_dict()
        if self.diagnostics:
            payload["diagnostics"] = self.diagnostics
        return payload


@dataclass(frozen=True, slots=True)
class HunkLine:
    kind: LineKind
    text: str


@dataclass(frozen=True, slots=True)
class Hunk:
    header: str | None
    lines: tuple[HunkLine, ...]
    line_number: int

    @property
    def old_lines(self) -> tuple[str, ...]:
        return tuple(line.text for line in self.lines if line.kind in {"context", "remove"})

    @property
    def new_lines(self) -> tuple[str, ...]:
        return tuple(line.text for line in self.lines if line.kind in {"context", "add"})


@dataclass(frozen=True, slots=True)
class AddFile:
    path: str
    lines: tuple[str, ...]
    line_number: int

    @property
    def kind(self) -> Literal["add"]:
        return "add"


@dataclass(frozen=True, slots=True)
class DeleteFile:
    path: str
    line_number: int

    @property
    def kind(self) -> Literal["delete"]:
        return "delete"


@dataclass(frozen=True, slots=True)
class UpdateFile:
    path: str
    hunks: tuple[Hunk, ...]
    line_number: int

    @property
    def kind(self) -> Literal["update"]:
        return "update"


PatchOperation: TypeAlias = AddFile | DeleteFile | UpdateFile


@dataclass(frozen=True, slots=True)
class ParsedPatch:
    operations: tuple[PatchOperation, ...]

    @property
    def hunk_count(self) -> int:
        return sum(len(operation.hunks) for operation in self.operations if isinstance(operation, UpdateFile))

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(operation.path for operation in self.operations)


def parse_patch(patch: str) -> ParsedPatch:
    """Parse the MiniCode v0 Codex-style apply_patch subset."""

    if not isinstance(patch, str) or not patch:
        raise PatchError("invalid_patch", "patch must be a non-empty string", False)

    lines = patch.splitlines()
    if not lines:
        raise PatchError("invalid_patch", "patch must be a non-empty string", False)
    if lines[0] != "*** Begin Patch":
        raise PatchError("invalid_patch", "patch must start with *** Begin Patch", False)
    if lines[-1] != "*** End Patch":
        raise PatchError("invalid_patch", "patch must end with *** End Patch", False)

    operations: list[PatchOperation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith("*** Add File: "):
            operation, index = _parse_add(lines, index)
        elif line.startswith("*** Delete File: "):
            operation, index = _parse_delete(lines, index)
        elif line.startswith("*** Update File: "):
            operation, index = _parse_update(lines, index)
        elif line.startswith("*** "):
            raise PatchError(
                "unsupported_patch_operation",
                f"unsupported patch directive on line {index + 1}: {line}",
                False,
                {"line": index + 1},
            )
        else:
            raise PatchError(
                "invalid_patch",
                f"expected patch operation on line {index + 1}",
                False,
                {"line": index + 1},
            )
        operations.append(operation)

    if not operations:
        raise PatchError("invalid_patch", "patch must modify at least one file", False)
    return ParsedPatch(tuple(operations))


def _parse_add(lines: list[str], index: int) -> tuple[AddFile, int]:
    path = _path_after(lines[index], "*** Add File: ", index)
    index += 1
    added: list[str] = []
    while index < len(lines) - 1 and not _is_operation_header(lines[index]):
        line = lines[index]
        if not line.startswith("+"):
            raise PatchError(
                "malformed_hunk",
                f"add file line {index + 1} must start with +",
                False,
                {"file": path, "line": index + 1},
            )
        added.append(line[1:])
        index += 1
    if not added:
        raise PatchError(
            "malformed_hunk",
            f"add file operation for {path} must contain at least one + line",
            False,
            {"file": path},
        )
    return AddFile(path=path, lines=tuple(added), line_number=index), index


def _parse_delete(lines: list[str], index: int) -> tuple[DeleteFile, int]:
    path = _path_after(lines[index], "*** Delete File: ", index)
    return DeleteFile(path=path, line_number=index + 1), index + 1


def _parse_update(lines: list[str], index: int) -> tuple[UpdateFile, int]:
    operation_line = index + 1
    path = _path_after(lines[index], "*** Update File: ", index)
    index += 1
    hunks: list[Hunk] = []
    while index < len(lines) - 1 and not _is_operation_header(lines[index]):
        line = lines[index]
        if line == "*** End of File":
            raise PatchError(
                "unsupported_patch_operation",
                "*** End of File marker is not supported in MiniCode v0",
                False,
                {"file": path, "line": index + 1},
            )
        if not line.startswith("@@"):
            raise PatchError(
                "malformed_hunk",
                f"expected @@ hunk header on line {index + 1}",
                False,
                {"file": path, "line": index + 1},
            )
        hunk, index = _parse_hunk(lines, index, path)
        hunks.append(hunk)
    if not hunks:
        raise PatchError(
            "malformed_hunk",
            f"update file operation for {path} must contain at least one hunk",
            False,
            {"file": path, "line": operation_line},
        )
    return UpdateFile(path=path, hunks=tuple(hunks), line_number=operation_line), index


def _parse_hunk(lines: list[str], index: int, path: str) -> tuple[Hunk, int]:
    header_line = lines[index]
    header = None
    if header_line == "@@":
        header = None
    elif header_line.startswith("@@ "):
        header = header_line[3:]
        if not header:
            raise PatchError("malformed_hunk", "empty hunk header", False, {"file": path, "line": index + 1})
    else:
        raise PatchError("malformed_hunk", "invalid hunk header", False, {"file": path, "line": index + 1})
    hunk_line_number = index + 1
    index += 1
    hunk_lines: list[HunkLine] = []
    while index < len(lines) - 1 and not _is_operation_header(lines[index]) and not lines[index].startswith("@@"):
        line = lines[index]
        if line == "*** End of File":
            break
        if not line:
            raise PatchError(
                "malformed_hunk",
                f"hunk line {index + 1} must start with space, -, or +",
                False,
                {"file": path, "line": index + 1},
            )
        prefix = line[0]
        if prefix == " ":
            kind: LineKind = "context"
        elif prefix == "-":
            kind = "remove"
        elif prefix == "+":
            kind = "add"
        else:
            raise PatchError(
                "malformed_hunk",
                f"hunk line {index + 1} must start with space, -, or +",
                False,
                {"file": path, "line": index + 1},
            )
        hunk_lines.append(HunkLine(kind, line[1:]))
        index += 1
    if not hunk_lines:
        raise PatchError("malformed_hunk", "hunk must contain at least one line", False, {"file": path})
    if not any(line.kind == "remove" for line in hunk_lines):
        raise PatchError(
            "malformed_hunk",
            "update hunk must contain at least one removed line in MiniCode v0",
            False,
            {"file": path, "line": hunk_line_number},
        )
    return Hunk(header=header, lines=tuple(hunk_lines), line_number=hunk_line_number), index


def _path_after(line: str, prefix: str, index: int) -> str:
    path = line[len(prefix) :]
    if not path or path.strip() != path:
        raise PatchError(
            "invalid_patch",
            f"missing or invalid file path on line {index + 1}",
            False,
            {"line": index + 1},
        )
    return path


def _is_operation_header(line: str) -> bool:
    return (
        line.startswith("*** Add File: ")
        or line.startswith("*** Delete File: ")
        or line.startswith("*** Update File: ")
        or line.startswith("*** ")
    )
