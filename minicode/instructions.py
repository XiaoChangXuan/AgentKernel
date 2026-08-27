from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import MiniCodeError
from .workspace import WorkspaceIdentity, relative_display_path


@dataclass(frozen=True)
class InstructionSource:
    """A discovered AGENTS.md source in root-to-task order."""

    order: int
    absolute_path: Path
    relative_path: str
    content: str

    def to_dict(self) -> dict[str, object]:
        return {
            "order": self.order,
            "path": self.relative_path,
            "content": self.content,
        }


def discover_agent_instructions(workspace: WorkspaceIdentity) -> list[InstructionSource]:
    """Discover AGENTS.md from workspace root toward the active task cwd."""

    root = workspace.root
    task_cwd = workspace.task_cwd
    directories = _path_chain(root, task_cwd)
    sources: list[InstructionSource] = []
    for directory in directories:
        candidate = directory / "AGENTS.md"
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise MiniCodeError(
                code="unsupported_path",
                message=f"Instruction file is not valid UTF-8: {relative_display_path(root, candidate)}",
                retryable=False,
            ) from exc
        sources.append(
            InstructionSource(
                order=len(sources),
                absolute_path=candidate,
                relative_path=relative_display_path(root, candidate),
                content=content,
            )
        )
    return sources


def _path_chain(root: Path, leaf: Path) -> list[Path]:
    relative = Path(relative_display_path(root, leaf))
    if str(relative) == ".":
        return [root]
    current = root
    chain = [root]
    for part in relative.parts:
        current = current / part
        chain.append(current)
    return chain
