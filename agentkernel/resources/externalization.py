"""Replaceable large Tool Result externalization policy and processor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from ..protocol import JsonValue, ToolCall, ToolResult
from ..tools import ToolExecutionContext
from ..truncation import retain_utf8_head_tail
from .model import ResourceOwner
from .service import ResourceError, ResourceService


@dataclass(frozen=True, slots=True)
class ExternalizationDecision:
    head_bytes: int
    tail_bytes: int


class ToolResultExternalizationPolicy(Protocol):
    def decide(
        self, call: ToolCall, result: ToolResult, size_bytes: int
    ) -> ExternalizationDecision | None: ...


@dataclass(frozen=True, slots=True)
class ThresholdExternalizationPolicy:
    threshold_bytes: int = 64 * 1024
    preview_head_bytes: int = 8 * 1024
    preview_tail_bytes: int = 4 * 1024
    excluded_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({"resource_read", "resource_stat"})
    )

    def __post_init__(self) -> None:
        for name in ("threshold_bytes", "preview_head_bytes", "preview_tail_bytes"):
            value = getattr(self, name)
            minimum = 1 if name == "threshold_bytes" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.preview_head_bytes + self.preview_tail_bytes >= self.threshold_bytes:
            raise ValueError("preview byte budgets must be below threshold_bytes")

    def decide(
        self, call: ToolCall, result: ToolResult, size_bytes: int
    ) -> ExternalizationDecision | None:
        if not result.ok or call.name in self.excluded_tools:
            return None
        if size_bytes <= self.threshold_bytes:
            return None
        return ExternalizationDecision(
            head_bytes=self.preview_head_bytes,
            tail_bytes=self.preview_tail_bytes,
        )


class ToolResultExternalizer:
    """Store full output and return only a preview plus safe handle."""

    def __init__(
        self,
        resources: ResourceService,
        policy: ToolResultExternalizationPolicy | None = None,
        *,
        fail_open: bool = True,
    ) -> None:
        self.resources = resources
        self.policy = policy or ThresholdExternalizationPolicy()
        self.fail_open = fail_open

    async def process(
        self,
        call: ToolCall,
        result: ToolResult,
        context: ToolExecutionContext,
    ) -> ToolResult:
        if not result.ok:
            return result
        data, media_type, original_type = _encode_output(result.output)
        decision = self.policy.decide(call, result, len(data))
        if decision is None:
            return result
        omitted = max(0, len(data) - decision.head_bytes - decision.tail_bytes)
        marker = f"\n\n... omitted {omitted} bytes; use resource_read ...\n\n"
        preview = retain_utf8_head_tail(
            data, decision.head_bytes, decision.tail_bytes, marker
        )
        try:
            handle = self.resources.create_artifact(
                data,
                owner=ResourceOwner(context.agent_id, context.session_id),
                media_type=media_type,
                encoding="utf-8",
                source_tool_name=call.name,
                source_tool_call_id=call.call_id,
                source_operation_id=context.operation_id,
            )
        except ResourceError:
            if self.fail_open:
                return result
            raise
        projected: dict[str, JsonValue] = {
            "externalized": True,
            "original_type": original_type,
            "preview": preview,
            "omitted_bytes": omitted,
            "resource": handle.as_dict(),
        }
        projected_bytes = len(
            json.dumps(projected, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        metrics = self.resources.metrics
        metrics.tool_results_externalized += 1
        metrics.preview_bytes += len(preview.encode("utf-8"))
        metrics.model_visible_bytes_saved += max(0, len(data) - projected_bytes)
        return ToolResult.success(call, projected)


def _encode_output(output: JsonValue) -> tuple[bytes, str, str]:
    if isinstance(output, str):
        return output.encode("utf-8"), "text/plain", "string"
    encoded = json.dumps(
        output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return encoded, "application/json", "json"
