"""MemoryStore implementations for V0.9 persistent memory."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Mapping, Protocol, runtime_checkable

from ..protocol import JsonValue, is_json_value
from .model import MemoryCorruptionError, MemoryEvent

_RECORD_TYPE = "memory/event"
_FORMAT_VERSION = 1


@runtime_checkable
class MemoryStore(Protocol):
    """Append-only durable storage for memory lifecycle facts."""

    def append(self, event: MemoryEvent) -> None: ...

    def list_events(self) -> tuple[MemoryEvent, ...]: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class InMemoryMemoryStore:
    """Process-local store used by deterministic tests."""

    def __init__(self, events: Iterable[MemoryEvent] = ()) -> None:
        self._events = [_clone_event(event) for event in events]
        self._closed = False

    def append(self, event: MemoryEvent) -> None:
        self._require_open()
        self._events.append(_clone_event(event))

    def list_events(self) -> tuple[MemoryEvent, ...]:
        self._require_open()
        return tuple(_clone_event(event) for event in self._events)

    def flush(self) -> None:
        self._require_open()

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise MemoryCorruptionError("memory store is closed")


class JsonlMemoryStore:
    """Inspectable JSONL-backed append-only memory store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file: IO[str] | None = None
        self._closed = False

    def append(self, event: MemoryEvent) -> None:
        self._require_open()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._file is None:
                self._file = self.path.open("a", encoding="utf-8", newline="\n")
            self._file.write(_encode_record(event) + "\n")
            self._file.flush()
        except OSError as error:
            raise MemoryCorruptionError(f"could not append memory event: {error}") from error

    def list_events(self) -> tuple[MemoryEvent, ...]:
        self._require_open()
        if self._file is not None:
            self._file.flush()
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
        except OSError as error:
            raise MemoryCorruptionError(f"could not read memory store: {error}") from error
        if not raw:
            return ()
        events: list[MemoryEvent] = []
        for index, line in enumerate(raw.splitlines(), start=1):
            if not line:
                raise MemoryCorruptionError(f"blank memory JSONL record at line {index}")
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MemoryCorruptionError(
                    f"malformed memory JSONL record at line {index}"
                ) from error
            if not isinstance(payload, Mapping):
                raise MemoryCorruptionError(
                    f"memory JSONL record at line {index} must be an object"
                )
            if payload.get("record_type") != _RECORD_TYPE:
                raise MemoryCorruptionError(
                    f"memory JSONL record at line {index} has wrong record_type"
                )
            if payload.get("format_version") != _FORMAT_VERSION:
                raise MemoryCorruptionError(
                    f"memory JSONL record at line {index} has unsupported format"
                )
            event_payload = payload.get("event")
            if not isinstance(event_payload, Mapping):
                raise MemoryCorruptionError(
                    f"memory JSONL record at line {index} lacks event object"
                )
            try:
                events.append(MemoryEvent.from_dict(event_payload))
            except (TypeError, ValueError) as error:
                raise MemoryCorruptionError(
                    f"invalid memory event at line {index}: {error}"
                ) from error
        return tuple(events)

    def flush(self) -> None:
        self._require_open()
        if self._file is None:
            return
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError as error:
            raise MemoryCorruptionError(f"could not flush memory store: {error}") from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush()
        finally:
            if self._file is not None:
                self._file.close()
                self._file = None
            self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise MemoryCorruptionError("memory store is closed")


def _encode_record(event: MemoryEvent) -> str:
    record: dict[str, JsonValue] = {
        "record_type": _RECORD_TYPE,
        "format_version": _FORMAT_VERSION,
        "event": event.as_dict(),
    }
    if not is_json_value(record):
        raise MemoryCorruptionError("memory record must be lossless JSON")
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _clone_event(event: MemoryEvent) -> MemoryEvent:
    return MemoryEvent.from_dict(event.as_dict())
