"""Provider-neutral request token accounting with deterministic fallback."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .protocol import Message, ModelRequest, ToolCall, ToolSchema


@runtime_checkable
class TokenEstimator(Protocol):
    """Replaceable text token-cost estimator."""

    def count_text(self, text: str) -> int: ...


@dataclass(frozen=True, slots=True)
class ApproximateTokenEstimator:
    """Offline deterministic Unicode-code-point approximation."""

    characters_per_token: int = 4

    def __post_init__(self) -> None:
        if (
            isinstance(self.characters_per_token, bool)
            or not isinstance(self.characters_per_token, int)
            or self.characters_per_token < 1
        ):
            raise ValueError("characters_per_token must be a positive integer")

    def count_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("token estimator input must be text")
        if not text:
            return 0
        return math.ceil(len(text) / self.characters_per_token)


@dataclass(frozen=True, slots=True)
class ModelContextLimits:
    """Provider/model capacity and the output space reserved from that capacity."""

    provider: str
    model: str
    context_window_tokens: int
    max_output_tokens: int
    output_reserve_tokens: int
    supports_exact_token_count: bool = False

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("provider and model must not be empty")
        for name in (
            "context_window_tokens",
            "max_output_tokens",
            "output_reserve_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.context_window_tokens < 1:
            raise ValueError("context_window_tokens must be positive")
        if self.max_output_tokens > self.context_window_tokens:
            raise ValueError("max_output_tokens cannot exceed the context window")
        if self.output_reserve_tokens > self.max_output_tokens:
            raise ValueError("output reserve cannot exceed max_output_tokens")
        if not isinstance(self.supports_exact_token_count, bool):
            raise TypeError("supports_exact_token_count must be a boolean")


@dataclass(frozen=True, slots=True)
class RequestTokenEstimate:
    """Explainable token estimate for one complete provider request."""

    system_prompt_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    envelope_tokens: int
    provider: str | None = None
    model: str | None = None
    exact: bool = False

    def __post_init__(self) -> None:
        for name in (
            "system_prompt_tokens",
            "message_tokens",
            "tool_schema_tokens",
            "envelope_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def total_tokens(self) -> int:
        return (
            self.system_prompt_tokens
            + self.message_tokens
            + self.tool_schema_tokens
            + self.envelope_tokens
        )


@runtime_checkable
class RequestTokenAccounting(Protocol):
    """Provider replaceable accounting for the complete model request."""

    def estimate_request(self, request: ModelRequest) -> RequestTokenEstimate: ...


@dataclass(frozen=True, slots=True)
class ApproximateRequestTokenAccounting:
    """Stable fallback that counts every request component, including tools."""

    estimator: TokenEstimator = ApproximateTokenEstimator()
    provider: str | None = None
    model: str | None = None
    request_overhead_tokens: int = 3
    message_overhead_tokens: int = 4
    tool_schema_overhead_tokens: int = 4

    def __post_init__(self) -> None:
        for name in (
            "request_overhead_tokens",
            "message_overhead_tokens",
            "tool_schema_overhead_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def estimate_request(self, request: ModelRequest) -> RequestTokenEstimate:
        system_tokens = (
            self.estimator.count_text(request.system_prompt)
            + self.message_overhead_tokens
            if request.system_prompt is not None
            else 0
        )
        message_tokens = sum(self._count_message(item) for item in request.messages)
        tool_tokens = sum(self._count_tool_schema(item) for item in request.tools)
        return RequestTokenEstimate(
            system_prompt_tokens=system_tokens,
            message_tokens=message_tokens,
            tool_schema_tokens=tool_tokens,
            envelope_tokens=self.request_overhead_tokens,
            provider=self.provider,
            model=self.model,
            exact=False,
        )

    def _count_message(self, message: Message) -> int:
        cost = self.message_overhead_tokens
        cost += self.estimator.count_text(message.role.value)
        cost += self.estimator.count_text(message.content)
        if message.tool_call_id is not None:
            cost += self.estimator.count_text(message.tool_call_id)
        if message.name is not None:
            cost += self.estimator.count_text(message.name)
        cost += sum(self._count_tool_call(item) for item in message.tool_calls)
        return cost

    def _count_tool_call(self, call: ToolCall) -> int:
        return self.estimator.count_text(call.call_id) + self.estimator.count_text(
            call.name
        ) + self.estimator.count_text(_canonical_json(dict(call.arguments)))

    def _count_tool_schema(self, schema: ToolSchema) -> int:
        return (
            self.tool_schema_overhead_tokens
            + self.estimator.count_text(schema.name)
            + self.estimator.count_text(schema.description)
            + self.estimator.count_text(_canonical_json(dict(schema.input_schema)))
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
