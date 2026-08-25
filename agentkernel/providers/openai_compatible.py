"""Non-streaming adapter for OpenAI-compatible Chat Completions APIs."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request as HTTPRequest
from urllib.request import urlopen

from ..llm import LLMErrorKind, LLMService, LLMServiceError
from ..protocol import (
    FinishReason,
    JsonValue,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolSchema,
    is_json_value,
)
from ..token_accounting import (
    ApproximateRequestTokenAccounting,
    ModelContextLimits,
    RequestTokenAccounting,
)

_BASE_URL_ENV = "AGENTKERNEL_LLM_BASE_URL"
_API_KEY_ENV = "AGENTKERNEL_LLM_API_KEY"
_MODEL_ENV = "AGENTKERNEL_LLM_MODEL"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_ERROR_DETAIL_CHARS = 1_000


class OpenAICompatibleError(LLMServiceError):
    """Base class for safe Provider-boundary failures."""

    def __init__(
        self,
        message: str,
        *,
        kind: LLMErrorKind = LLMErrorKind.UNKNOWN,
        status: int | None = None,
    ) -> None:
        super().__init__(kind, message, status=status)


class OpenAICompatibleConfigurationError(OpenAICompatibleError):
    """Required endpoint configuration is missing or invalid."""


class OpenAICompatibleTransportError(OpenAICompatibleError):
    """The HTTP request could not reach or read the configured service."""

    def __init__(self, message: str, *, kind: LLMErrorKind) -> None:
        super().__init__(message, kind=kind)


class OpenAICompatibleHTTPError(OpenAICompatibleError):
    """The service returned a non-successful HTTP response."""

    def __init__(self, status: int, detail: str, *, kind: LLMErrorKind) -> None:
        self.status = status
        self.detail = detail
        super().__init__(
            f"OpenAI-compatible API returned HTTP {status}: {detail}",
            kind=kind,
            status=status,
        )


class OpenAICompatibleProtocolError(OpenAICompatibleError):
    """The service response did not satisfy the supported wire contract."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind=LLMErrorKind.PROTOCOL)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    """Configuration for one OpenAI-compatible Chat Completions endpoint."""

    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 60.0
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    output_reserve_tokens: int | None = None

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        model = self.model.strip()
        api_key = self.api_key.strip() if self.api_key else None
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OpenAICompatibleConfigurationError(
                "AGENTKERNEL_LLM_BASE_URL must be an absolute http(s) URL"
            )
        if parsed.username is not None or parsed.password is not None:
            raise OpenAICompatibleConfigurationError(
                "AGENTKERNEL_LLM_BASE_URL must not contain credentials"
            )
        if parsed.query or parsed.fragment:
            raise OpenAICompatibleConfigurationError(
                "AGENTKERNEL_LLM_BASE_URL must not contain a query or fragment"
            )
        if not model:
            raise OpenAICompatibleConfigurationError(
                "AGENTKERNEL_LLM_MODEL must not be empty"
            )
        if self.timeout_seconds <= 0:
            raise OpenAICompatibleConfigurationError(
                "OpenAI-compatible timeout_seconds must be positive"
            )
        optional_limits = (
            self.context_window_tokens,
            self.max_output_tokens,
            self.output_reserve_tokens,
        )
        if any(value is not None for value in optional_limits) and any(
            value is None for value in optional_limits
        ):
            raise OpenAICompatibleConfigurationError(
                "context_window_tokens, max_output_tokens, and "
                "output_reserve_tokens must be configured together"
            )
        if self.context_window_tokens is not None:
            ModelContextLimits(
                provider="openai-compatible",
                model=model,
                context_window_tokens=self.context_window_tokens,
                max_output_tokens=self.max_output_tokens,  # type: ignore[arg-type]
                output_reserve_tokens=self.output_reserve_tokens,  # type: ignore[arg-type]
            )
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "api_key", api_key)

    @property
    def chat_completions_url(self) -> str:
        """Return the single V0.1 API endpoint used by this adapter."""

        return f"{self.base_url}/chat/completions"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 60.0,
    ) -> "OpenAICompatibleConfig":
        """Load only AgentKernel-specific environment variables."""

        source = os.environ if environ is None else environ
        missing = [
            name
            for name in (_BASE_URL_ENV, _MODEL_ENV)
            if not source.get(name, "").strip()
        ]
        if missing:
            raise OpenAICompatibleConfigurationError(
                "missing required environment variable(s): " + ", ".join(missing)
            )
        return cls(
            base_url=source[_BASE_URL_ENV],
            model=source[_MODEL_ENV],
            api_key=source.get(_API_KEY_ENV) or None,
            timeout_seconds=timeout_seconds,
        )


class OpenAICompatibleLLM(LLMService):
    """Translate AgentKernel Protocol to non-streaming Chat Completions HTTP."""

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self._config = config
        self._token_accounting = ApproximateRequestTokenAccounting(
            provider="openai-compatible",
            model=config.model,
        )

    @property
    def token_accounting(self) -> RequestTokenAccounting:
        return self._token_accounting

    @property
    def context_limits(self) -> ModelContextLimits | None:
        if self._config.context_window_tokens is None:
            return None
        return ModelContextLimits(
            provider="openai-compatible",
            model=self._config.model,
            context_window_tokens=self._config.context_window_tokens,
            max_output_tokens=self._config.max_output_tokens,  # type: ignore[arg-type]
            output_reserve_tokens=self._config.output_reserve_tokens,  # type: ignore[arg-type]
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Execute one non-streaming Chat Completions request."""

        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: ModelRequest) -> ModelResponse:
        body = _request_body(self._config.model, request)
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AgentKernel/0.1 OpenAI-Compatible",
        }
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        http_request = HTTPRequest(
            self._config.chat_completions_url,
            data=encoded,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self._config.timeout_seconds) as response:
                raw = _read_bounded(response)
        except HTTPError as error:
            raw_error = error.read(_MAX_RESPONSE_BYTES + 1)
            detail = _http_error_detail(raw_error, self._config.api_key)
            kind = _classify_http_error(error.code, raw_error)
            raise OpenAICompatibleHTTPError(error.code, detail, kind=kind) from error
        except (TimeoutError, asyncio.TimeoutError) as error:
            detail = _redact(str(error), self._config.api_key)
            raise OpenAICompatibleTransportError(
                f"OpenAI-compatible request failed: {detail}",
                kind=LLMErrorKind.TIMEOUT,
            ) from error
        except (URLError, OSError) as error:
            detail = _redact(str(error), self._config.api_key)
            raise OpenAICompatibleTransportError(
                f"OpenAI-compatible request failed: {detail}",
                kind=LLMErrorKind.SERVICE_UNAVAILABLE,
            ) from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenAICompatibleProtocolError(
                "OpenAI-compatible response was not valid UTF-8 JSON"
            ) from error
        return _parse_response(payload)


def _request_body(model: str, request: ModelRequest) -> dict[str, JsonValue]:
    messages: list[JsonValue] = []
    if request.system_prompt is not None:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.extend(_message_to_wire(message) for message in request.messages)
    body: dict[str, JsonValue] = {"model": model, "messages": messages}
    if request.tools:
        body["tools"] = [_tool_schema_to_wire(schema) for schema in request.tools]
        body["tool_choice"] = "auto"
    return body


def _message_to_wire(message: Message) -> dict[str, JsonValue]:
    if message.role is MessageRole.USER:
        return {"role": "user", "content": message.content}
    if message.role is MessageRole.ASSISTANT:
        wire: dict[str, JsonValue] = {
            "role": "assistant",
            "content": message.content if message.content or not message.tool_calls else None,
        }
        if message.tool_calls:
            wire["tool_calls"] = [_tool_call_to_wire(call) for call in message.tool_calls]
        return wire
    if message.role is MessageRole.TOOL:
        if not message.tool_call_id:
            raise OpenAICompatibleProtocolError(
                "AgentKernel tool message is missing tool_call_id"
            )
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    raise OpenAICompatibleProtocolError(
        f"unsupported AgentKernel message role: {message.role}"
    )


def _tool_call_to_wire(call: ToolCall) -> dict[str, JsonValue]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(
                dict(call.arguments),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ),
        },
    }


def _tool_schema_to_wire(schema: ToolSchema) -> dict[str, JsonValue]:
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": dict(schema.input_schema),
        },
    }


def _parse_response(payload: object) -> ModelResponse:
    root = _require_mapping(payload, "response")
    choices = root.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenAICompatibleProtocolError(
            "OpenAI-compatible response choices must be a non-empty list"
        )
    choice = _require_mapping(choices[0], "response.choices[0]")
    message = _require_mapping(choice.get("message"), "response.choices[0].message")
    role = message.get("role")
    if role not in {None, "assistant"}:
        raise OpenAICompatibleProtocolError(
            "OpenAI-compatible response message role must be assistant"
        )
    raw_content = message.get("content")
    if raw_content is None:
        content = ""
    elif isinstance(raw_content, str):
        content = raw_content
    else:
        raise OpenAICompatibleProtocolError(
            "OpenAI-compatible assistant content must be string or null"
        )
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise OpenAICompatibleProtocolError(
            "OpenAI-compatible assistant tool_calls must be a list"
        )
    calls = tuple(
        _parse_tool_call(value, index) for index, value in enumerate(raw_calls)
    )
    raw_finish = choice.get("finish_reason")
    if raw_finish is not None and not isinstance(raw_finish, str):
        raise OpenAICompatibleProtocolError(
            "OpenAI-compatible finish_reason must be a string or null"
        )
    expected_wire_finish = "tool_calls" if calls else "stop"
    if raw_finish is not None and raw_finish != expected_wire_finish:
        raise OpenAICompatibleProtocolError(
            "unsupported or inconsistent OpenAI-compatible finish_reason: "
            f"{raw_finish}"
        )
    return ModelResponse(
        content=content,
        tool_calls=calls,
        finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        usage=_parse_usage(root.get("usage")),
    )


def _parse_usage(value: object) -> ModelUsage | None:
    if value is None:
        return None
    usage = _require_mapping(value, "response.usage")
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    for name, item in (
        ("input tokens", prompt),
        ("output tokens", completion),
        ("total tokens", total),
    ):
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise OpenAICompatibleProtocolError(
                f"OpenAI-compatible usage {name} must be a non-negative integer"
            )
    return ModelUsage(
        input_tokens=prompt,
        output_tokens=completion,
        total_tokens=total,
    )


def _parse_tool_call(value: object, index: int) -> ToolCall:
    call = _require_mapping(value, f"assistant.tool_calls[{index}]")
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id:
        raise OpenAICompatibleProtocolError(
            f"assistant.tool_calls[{index}].id must be a non-empty string"
        )
    call_type = call.get("type")
    if call_type not in {None, "function"}:
        raise OpenAICompatibleProtocolError(
            f"assistant.tool_calls[{index}].type must be function"
        )
    function = _require_mapping(
        call.get("function"),
        f"assistant.tool_calls[{index}].function",
    )
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise OpenAICompatibleProtocolError(
            f"assistant.tool_calls[{index}].function.name must be a non-empty string"
        )
    raw_arguments = function.get("arguments")
    if not isinstance(raw_arguments, str):
        raise OpenAICompatibleProtocolError(
            f"assistant.tool_calls[{index}].function.arguments must be a JSON string"
        )
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        raise OpenAICompatibleProtocolError(
            f"assistant.tool_calls[{index}] contains invalid JSON arguments"
        ) from error
    if not isinstance(arguments, dict) or not is_json_value(arguments):
        raise OpenAICompatibleProtocolError(
            f"assistant.tool_calls[{index}] arguments must decode to a JSON object"
        )
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def _require_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OpenAICompatibleProtocolError(f"{path} must be an object")
    return value


def _read_bounded(response: Any) -> bytes:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OpenAICompatibleProtocolError(
            f"OpenAI-compatible response exceeded {_MAX_RESPONSE_BYTES} bytes"
        )
    return raw


def _http_error_detail(raw: bytes, api_key: str | None) -> str:
    if len(raw) > _MAX_RESPONSE_BYTES:
        return "response body exceeded the safe diagnostic limit"
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        detail = message if isinstance(message, str) else text
    detail = " ".join(detail.split())[:_MAX_ERROR_DETAIL_CHARS] or "no error detail"
    return _redact(detail, api_key)


_CONTEXT_ERROR_CODES = {
    "context_length_exceeded",
    "context_window_exceeded",
    "input_too_long",
    "prompt_too_long",
    "too_many_tokens",
}
_CONTEXT_ERROR_PATTERNS = (
    "context length exceeded",
    "context window exceeded",
    "exceeds the context window",
    "maximum context length",
    "prompt is too long",
    "input is too long",
    "input tokens exceed",
    "input length and",
    "request too large",
    "too many tokens",
)


def _classify_http_error(status: int, raw: bytes) -> LLMErrorKind:
    """Normalize only at the adapter boundary; callers never parse provider text."""

    if status in {401, 403}:
        return LLMErrorKind.AUTHENTICATION
    if status == 408:
        return LLMErrorKind.TIMEOUT
    if status == 429:
        return LLMErrorKind.RATE_LIMIT
    if status >= 500:
        return LLMErrorKind.SERVICE_UNAVAILABLE
    code = ""
    message = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        pass
    else:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raw_code = error.get("code", error.get("type", ""))
            code = str(raw_code).strip().lower()
            raw_message = error.get("message")
            if isinstance(raw_message, str):
                message = raw_message
    lowered = message.lower()
    if code in _CONTEXT_ERROR_CODES or any(
        pattern in lowered for pattern in _CONTEXT_ERROR_PATTERNS
    ):
        return LLMErrorKind.CONTEXT_OVERFLOW
    if 400 <= status < 500:
        return LLMErrorKind.INVALID_REQUEST
    return LLMErrorKind.UNKNOWN


def _redact(value: str, api_key: str | None) -> str:
    if api_key:
        return value.replace(api_key, "***")
    return value
