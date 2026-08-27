from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import MiniCodeError


ApprovalMode = Literal["never", "on-mutation", "always"]


@dataclass(frozen=True)
class MiniCodeConfig:
    """Small Phase 2A configuration object for future CLI/runtime wiring."""

    workspace: Path | None = None
    task_cwd: Path | None = None
    approve: ApprovalMode = "on-mutation"
    trace_jsonl: Path | None = None
    model: str = "scripted"
    max_turns: int = 20
    timeout_ms: int = 30_000
    no_network: bool = True

    def validate(self) -> None:
        if self.approve not in {"never", "on-mutation", "always"}:
            raise MiniCodeError(
                code="invalid_configuration",
                message=f"Unsupported approval mode: {self.approve}",
                retryable=False,
            )
        if self.max_turns <= 0:
            raise MiniCodeError(
                code="invalid_configuration",
                message="max_turns must be greater than zero",
                retryable=False,
            )
        if self.timeout_ms <= 0:
            raise MiniCodeError(
                code="invalid_configuration",
                message="timeout_ms must be greater than zero",
                retryable=False,
            )
