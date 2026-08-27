from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from agentkernel.protocol import FinishReason, JsonValue, Message, MessageRole, ModelUsage, ToolCall, ToolSchema


@dataclass(frozen=True, slots=True)
class MiniCodeModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...]
    system_prompt: str | None = None
    tool_choice: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MiniCodeModelResponse:
    assistant_text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: ModelUsage | None = None
    model_cost: float = 0.0
    provider_latency_ms: int | None = None
    raw_diagnostics: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = "tool_calls" if self.tool_calls else "stop"
        if self.finish_reason not in {expected, "length", "content_filter", "error"}:
            raise ValueError("finish_reason does not match MiniCode response content")


class ModelAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(message)


class ModelAdapter(Protocol):
    provider: str
    model: str

    def complete(self, request: MiniCodeModelRequest) -> MiniCodeModelResponse: ...


ScriptCallback = Callable[[MiniCodeModelRequest, int], MiniCodeModelResponse]


class ScriptedModelAdapter:
    """Deterministic offline adapter driven by queued responses or a callback."""

    provider = "scripted"
    model = "scripted"

    def __init__(
        self,
        responses: Sequence[MiniCodeModelResponse | Mapping[str, object]] = (),
        *,
        callback: ScriptCallback | None = None,
    ) -> None:
        self._responses = [
            response if isinstance(response, MiniCodeModelResponse) else scripted_response(**response)
            for response in responses
        ]
        self._callback = callback
        self.requests: list[MiniCodeModelRequest] = []

    def complete(self, request: MiniCodeModelRequest) -> MiniCodeModelResponse:
        index = len(self.requests)
        self.requests.append(request)
        if self._callback is not None:
            return self._callback(request, index)
        if not self._responses:
            raise ModelAdapterError("script_exhausted", "ScriptedModelAdapter has no remaining responses")
        return self._responses.pop(0)


def scripted_response(
    *,
    text: str = "",
    tool_calls: Sequence[ToolCall | Mapping[str, object]] = (),
    usage: ModelUsage | Mapping[str, object] | None = None,
    finish_reason: str | None = None,
) -> MiniCodeModelResponse:
    calls = tuple(_tool_call_from_value(item) for item in tool_calls)
    normalized_usage = _usage_from_value(usage)
    return MiniCodeModelResponse(
        assistant_text=text,
        tool_calls=calls,
        finish_reason=finish_reason or ("tool_calls" if calls else "stop"),
        usage=normalized_usage,
    )


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 30.0
    enabled: bool = False


ProviderClient = Callable[[Mapping[str, object]], Mapping[str, object]]


class OpenAICompatibleAdapter:
    """Small opt-in adapter for OpenAI-compatible chat completion APIs."""

    provider = "openai-compatible"

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: ProviderClient | None = None,
    ) -> None:
        self.config = config
        self.model = config.model
        self._client = client

    def complete(self, request: MiniCodeModelRequest) -> MiniCodeModelResponse:
        if not self.config.enabled and self._client is None:
            raise ModelAdapterError(
                "provider_not_configured",
                "OpenAI-compatible adapter requires explicit opt-in configuration",
            )
        payload = self._request_payload(request)
        started = time.monotonic()
        try:
            raw = self._client(payload) if self._client is not None else self._http_complete(payload)
        except ModelAdapterError:
            raise
        except Exception as error:
            raise ModelAdapterError("provider_error", _redact(str(error), self.config.api_key)) from error
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        response = self._parse_response(raw)
        return MiniCodeModelResponse(
            assistant_text=response.assistant_text,
            tool_calls=response.tool_calls,
            finish_reason=response.finish_reason,
            usage=response.usage,
            provider_latency_ms=latency_ms,
            raw_diagnostics=response.raw_diagnostics,
        )

    def _request_payload(self, request: MiniCodeModelRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [_message_payload(message) for message in request.messages],
            "tools": [_tool_payload(tool) for tool in request.tools],
        }
        if request.system_prompt:
            payload["messages"] = [
                {"role": "system", "content": request.system_prompt},
                *payload["messages"],  # type: ignore[list-item]
            ]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        return payload

    def _http_complete(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        if not self.config.base_url:
            raise ModelAdapterError("provider_not_configured", "base_url is required")
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = response.read()
        except urllib.error.HTTPError as error:
            detail = _http_error_detail(error.read(64 * 1024 + 1), self.config.api_key)
            raise ModelAdapterError("provider_http_error", f"HTTP {error.code}: {detail}") from error
        except TimeoutError as error:
            raise ModelAdapterError("provider_timeout", _redact(str(error), self.config.api_key)) from error
        except urllib.error.URLError as error:
            raise ModelAdapterError(
                "provider_connection_error",
                _redact(str(error), self.config.api_key),
            ) from error
        decoded = json.loads(data.decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise ModelAdapterError("provider_error", "provider response must be an object")
        return decoded

    @staticmethod
    def _parse_response(raw: Mapping[str, object]) -> MiniCodeModelResponse:
        try:
            choices = raw["choices"]
            if not isinstance(choices, list) or not choices:
                raise TypeError("choices must be a non-empty list")
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise TypeError("choice must be an object")
            message = choice.get("message", {})
            if not isinstance(message, Mapping):
                raise TypeError("message must be an object")
            content = message.get("content") or ""
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise TypeError("tool_calls must be a list")
            calls = tuple(_openai_tool_call(item) for item in raw_calls)
            finish_reason = str(choice.get("finish_reason") or ("tool_calls" if calls else "stop"))
            return MiniCodeModelResponse(
                assistant_text=str(content),
                tool_calls=calls,
                finish_reason=finish_reason,
                usage=_usage_from_value(raw.get("usage")),
                raw_diagnostics={"choice_count": len(choices)},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ModelAdapterError("malformed_provider_response", str(error)) from error


def redact_secret(value: str | None) -> str | None:
    if value is None:
        return None
    return "<redacted>" if value else value


def _http_error_detail(raw: bytes, api_key: str | None) -> str:
    if len(raw) > 64 * 1024:
        return "response body exceeded diagnostic limit"
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        error = payload.get("error") if isinstance(payload, Mapping) else None
        message = error.get("message") if isinstance(error, Mapping) else None
        detail = message if isinstance(message, str) else text
    detail = " ".join(detail.split())[:1000] or "no error detail"
    return _redact(detail, api_key)


def _redact(value: str, api_key: str | None) -> str:
    if api_key:
        return value.replace(api_key, "<redacted>")
    return value


def _message_payload(message: Message) -> dict[str, object]:
    payload: dict[str, object] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, sort_keys=True),
                },
            }
            for call in message.tool_calls
        ]
    if message.role is MessageRole.TOOL:
        payload["tool_call_id"] = message.tool_call_id
        payload["name"] = message.name
    return payload


def _tool_payload(tool: ToolSchema) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _openai_tool_call(value: object) -> ToolCall:
    if not isinstance(value, Mapping):
        raise TypeError("tool call must be an object")
    function = value.get("function")
    if not isinstance(function, Mapping):
        raise TypeError("tool call function must be an object")
    raw_arguments = function.get("arguments") or "{}"
    arguments = json.loads(str(raw_arguments))
    if not isinstance(arguments, Mapping):
        raise TypeError("tool call arguments must decode to an object")
    return ToolCall(
        call_id=str(value.get("id") or f"call_{len(str(raw_arguments))}"),
        name=str(function["name"]),
        arguments=dict(arguments),
    )


def _tool_call_from_value(value: ToolCall | Mapping[str, object]) -> ToolCall:
    if isinstance(value, ToolCall):
        return value
    return ToolCall.from_dict(value)


def _usage_from_value(value: ModelUsage | Mapping[str, object] | None) -> ModelUsage | None:
    if value is None or isinstance(value, ModelUsage):
        return value
    prompt = int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0)
    completion = int(value.get("completion_tokens", value.get("output_tokens", 0)) or 0)
    total = int(value.get("total_tokens", prompt + completion) or 0)
    if total < prompt + completion:
        total = prompt + completion
    return ModelUsage(input_tokens=prompt, output_tokens=completion, total_tokens=total)
