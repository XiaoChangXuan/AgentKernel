"""LLM service seam and deterministic scripted implementation."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import TypeAlias

from .protocol import ModelRequest, ModelResponse
from .token_accounting import ModelContextLimits, RequestTokenAccounting


class LLMErrorKind(StrEnum):
    """Stable kernel-facing classification for model service failures."""

    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    PROTOCOL = "protocol"
    UNKNOWN = "unknown"


class LLMServiceError(RuntimeError):
    """Provider-normalized failure consumed by kernel orchestration."""

    def __init__(
        self,
        kind: LLMErrorKind,
        message: str,
        *,
        status: int | None = None,
    ) -> None:
        self.kind = LLMErrorKind(kind)
        self.status = status
        super().__init__(message)


class LLMService(ABC):
    """Provider-neutral model generation interface."""

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate exactly one response for a fully assembled request."""

    @property
    def token_accounting(self) -> RequestTokenAccounting | None:
        """Return provider-aware request accounting when the adapter has one."""

        return None

    @property
    def context_limits(self) -> ModelContextLimits | None:
        """Return provider/model limits when configured or discovered."""

        return None


ScriptResponder: TypeAlias = Callable[
    [ModelRequest], ModelResponse | Awaitable[ModelResponse]
]
ScriptStep: TypeAlias = ModelResponse | ScriptResponder


class ScriptedLLM(LLMService):
    """Return deterministic responses without a network or API key."""

    def __init__(self, steps: Sequence[ScriptStep]) -> None:
        if not steps:
            raise ValueError("ScriptedLLM requires at least one step")
        self._steps = tuple(steps)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        index = len(self.requests)
        if index >= len(self._steps):
            raise RuntimeError("ScriptedLLM exhausted its configured responses")
        self.requests.append(request)
        step = self._steps[index]
        response = step(request) if callable(step) else step
        if inspect.isawaitable(response):
            response = await response
        if not isinstance(response, ModelResponse):
            raise TypeError("scripted response must be a ModelResponse")
        return response
