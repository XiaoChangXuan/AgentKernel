"""Host-only Tool effect and reconciliation protocol values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .protocol import JsonValue, is_json_value


class ToolEffectKind(StrEnum):
    """External effect semantics, never projected to the model."""

    READ_ONLY = "read_only"
    IDEMPOTENT_MUTATION = "idempotent_mutation"
    RECONCILABLE_MUTATION = "reconcilable_mutation"
    OPAQUE_MUTATION = "opaque_mutation"


class ReconcileStatus(StrEnum):
    """One external system's observation of a durable operation identity."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Structured result returned by an optional Tool reconciliation callback."""

    status: ReconcileStatus
    output: JsonValue = None
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ReconcileStatus(self.status))
        if self.status is ReconcileStatus.SUCCEEDED and not is_json_value(
            self.output
        ):
            raise TypeError("successful reconciliation output must be lossless JSON")
        if self.message is not None and not isinstance(self.message, str):
            raise TypeError("reconciliation message must be a string")
