"""Provider-neutral protocol shared by the AgentKernel spine."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, TypeAlias

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def is_json_value(value: object, seen: set[int] | None = None) -> bool:
    """Return whether a value is lossless JSON without coercion or cycles."""

    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if not isinstance(value, (list, dict)):
        return False
    active = seen if seen is not None else set()
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        if isinstance(value, list):
            return all(is_json_value(item, active) for item in value)
        return all(
            isinstance(key, str) and is_json_value(item, active)
            for key, item in value.items()
        )
    finally:
        active.remove(identity)


class MessageRole(StrEnum):
    """Roles supported by the V0.1 model protocol."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Provider-neutral reason a model response ended."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"


class ErrorCode(StrEnum):
    """Stable errno-like failure codes for tool execution."""

    ENOENT = "ENOENT"
    EACCES = "EACCES"
    EINVAL = "EINVAL"
    EIO = "EIO"
    ETIMEDOUT = "ETIMEDOUT"
    ECANCELED = "ECANCELED"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One provider-neutral tool invocation requested by a model."""

    call_id: str
    name: str
    arguments: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("tool call_id must not be empty")
        if not self.name:
            raise ValueError("tool name must not be empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool arguments must be a mapping")
        if not is_json_value(dict(self.arguments)):
            raise TypeError("tool arguments must be lossless JSON")

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a JSON-compatible event representation."""

        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": copy.deepcopy(dict(self.arguments)),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ToolCall":
        """Reconstruct a call from a session event payload."""

        arguments = value.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise TypeError("stored tool arguments must be a mapping")
        return cls(
            call_id=str(value["call_id"]),
            name=str(value["name"]),
            arguments=copy.deepcopy(dict(arguments)),
        )


@dataclass(frozen=True, slots=True)
class ToolSchema:
    """The model-visible portion of a registered tool."""

    name: str
    description: str
    input_schema: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool schema name must not be empty")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("tool input_schema must be a mapping")
        if not is_json_value(dict(self.input_schema)):
            raise TypeError("tool input_schema must be lossless JSON")


@dataclass(frozen=True, slots=True)
class ToolError:
    """Structured failure returned across the tool boundary."""

    code: ErrorCode
    message: str

    def as_dict(self) -> dict[str, JsonValue]:
        return {"code": self.code.value, "message": self.message}


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Structured result of one tool invocation."""

    call_id: str
    name: str
    ok: bool
    output: JsonValue = None
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if self.ok and self.error is not None:
            raise ValueError("a successful tool result cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("a failed tool result must contain an error")
        if self.ok and not is_json_value(self.output):
            raise TypeError("tool output must be lossless JSON")

    @classmethod
    def success(cls, call: ToolCall, output: JsonValue) -> "ToolResult":
        return cls(call_id=call.call_id, name=call.name, ok=True, output=output)

    @classmethod
    def failure(
        cls,
        call: ToolCall,
        code: ErrorCode,
        message: str,
    ) -> "ToolResult":
        return cls(
            call_id=call.call_id,
            name=call.name,
            ok=False,
            error=ToolError(code=code, message=message),
        )

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the canonical durable event payload."""

        result: dict[str, JsonValue] = {
            "call_id": self.call_id,
            "name": self.name,
            "ok": self.ok,
        }
        if self.ok:
            result["output"] = self.output
        else:
            assert self.error is not None
            result["error"] = self.error.as_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ToolResult":
        """Reconstruct a result from a session event payload."""

        call_id = str(value["call_id"])
        name = str(value["name"])
        ok = bool(value["ok"])
        if ok:
            return cls(call_id=call_id, name=name, ok=True, output=value.get("output"))
        raw_error = value.get("error")
        if not isinstance(raw_error, Mapping):
            raise TypeError("stored failed tool result must contain an error mapping")
        return cls(
            call_id=call_id,
            name=name,
            ok=False,
            error=ToolError(
                code=ErrorCode(str(raw_error["code"])),
                message=str(raw_error["message"]),
            ),
        )

    def to_model_content(self) -> str:
        """Render the structured result for a provider-neutral tool message."""

        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class Message:
    """One message projected from the session event log."""

    role: MessageRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    is_error: bool = False

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=MessageRole.USER, content=content)

    @classmethod
    def assistant(
        cls,
        content: str,
        tool_calls: tuple[ToolCall, ...] = (),
    ) -> "Message":
        return cls(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls)

    @classmethod
    def tool(cls, result: ToolResult) -> "Message":
        return cls(
            role=MessageRole.TOOL,
            content=result.to_model_content(),
            tool_call_id=result.call_id,
            name=result.name,
            is_error=not result.ok,
        )


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Fully assembled request passed to an LLM provider."""

    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Provider-reported token usage for one successful response."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens cannot be below input plus output tokens")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Provider-neutral response consumed by the default loop."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason | None = None
    usage: ModelUsage | None = None

    def __post_init__(self) -> None:
        expected = FinishReason.TOOL_CALLS if self.tool_calls else FinishReason.STOP
        if self.finish_reason is None:
            object.__setattr__(self, "finish_reason", expected)
        elif self.finish_reason is not expected:
            raise ValueError("finish_reason does not match the response content")
