from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agentkernel.protocol import JsonValue, is_json_value


@dataclass(frozen=True, slots=True)
class TraceEvent:
    seq: int
    type: str
    data: Mapping[str, JsonValue]
    time: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "seq": self.seq,
            "type": self.type,
            "data": dict(self.data),
            "time": self.time,
        }


class TraceRecorder:
    """MiniCode observable trace. It is not a semantic ledger."""

    def __init__(self, *, jsonl_path: Path | None = None) -> None:
        self._events: list[TraceEvent] = []
        self._jsonl_path = jsonl_path
        if jsonl_path is not None:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            jsonl_path.write_text("", encoding="utf-8")

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def record(self, event_type: str, data: Mapping[str, JsonValue] | None = None) -> TraceEvent:
        payload = _sanitize(dict(data or {}))
        if not is_json_value(payload):
            raise TypeError("trace data must be JSON")
        event = TraceEvent(len(self._events) + 1, event_type, payload)
        self._events.append(event)
        if self._jsonl_path is not None:
            with self._jsonl_path.open("a", encoding="utf-8", newline="\n") as sink:
                sink.write(json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def human_text(self) -> str:
        lines: list[str] = []
        for event in self._events:
            label = _label(event.type)
            details = _compact_details(event.data)
            lines.append(f"[{label}] {details}" if details else f"[{label}]")
        return "\n".join(lines)


def render_session_trace(events: object) -> str:
    lines: list[str] = []
    for event in events:
        event_type = getattr(event, "type", None)
        value = getattr(event_type, "value", str(event_type))
        data = getattr(event, "data", {})
        lines.append(f"[Session] {value} {_compact_details(data)}".rstrip())
    return "\n".join(lines)


def _sanitize(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            lowered = key.lower()
            if "api_key" in lowered or "authorization" in lowered or "secret" in lowered:
                result[key] = "<redacted>"
            elif isinstance(item, (dict, list)):
                result[key] = _sanitize(item)
            else:
                result[key] = item
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _label(event_type: str) -> str:
    prefix = event_type.split("/", 1)[0]
    return {
        "task": "Task",
        "workspace": "Workspace",
        "instructions": "Instructions",
        "model": "Model",
        "tool": "Tool",
        "authorization": "Kernel",
        "resource": "Resource",
        "process": "Process",
        "recovery": "Recovery",
    }.get(prefix, prefix.title())


def _compact_details(data: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if len(encoded) > 160:
                encoded = encoded[:157] + "..."
            parts.append(f"{key}={encoded}")
        else:
            text = str(value)
            if len(text) > 160:
                text = text[:157] + "..."
            parts.append(f"{key}={text}")
    return " ".join(parts)
