"""Durable storage seam and V0.2 in-memory/JSONL implementations."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Mapping, Protocol, runtime_checkable

from .events import SessionEvent
from .protocol import JsonValue, is_json_value

SESSION_FORMAT_VERSION = 1
_HEADER_RECORD_TYPE = "session/header"
_EVENT_RECORD_TYPE = "session/event"


class SessionPersistenceError(RuntimeError):
    """Base class for persistence-boundary failures."""


class SessionNotFoundError(SessionPersistenceError):
    """The requested persisted session does not exist."""


class SessionAlreadyExistsError(SessionPersistenceError):
    """Creating a session would replace an existing durable artifact."""


class SessionCorruptionError(SessionPersistenceError):
    """Stored bytes cannot be reconstructed with valid session semantics."""

    def __init__(self, message: str, *, analysis: object | None = None) -> None:
        self.analysis = analysis
        super().__init__(message)


class UnsupportedSessionFormatError(SessionPersistenceError):
    """The stored format version is not supported by this runtime."""

    def __init__(self, found: int) -> None:
        self.found = found
        self.supported = SESSION_FORMAT_VERSION
        super().__init__(
            f"unsupported session format version {found}; "
            f"this runtime supports {SESSION_FORMAT_VERSION}"
        )


@dataclass(frozen=True, slots=True)
class SessionHeader:
    """Immutable storage metadata written before a session's event records."""

    format_version: int
    session_id: str
    created_at: str

    def __post_init__(self) -> None:
        if isinstance(self.format_version, bool) or not isinstance(
            self.format_version, int
        ):
            raise TypeError("session format_version must be an integer")
        if self.format_version != SESSION_FORMAT_VERSION:
            raise UnsupportedSessionFormatError(self.format_version)
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session header session_id must not be empty")
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ValueError("session header created_at must not be empty")
        try:
            parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("session header created_at must be ISO 8601") from error
        if parsed.tzinfo is None:
            raise ValueError("session header created_at must include a timezone")

    @classmethod
    def create(cls, session_id: str) -> "SessionHeader":
        return cls(
            format_version=SESSION_FORMAT_VERSION,
            session_id=session_id,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "format_version": self.format_version,
            "session_id": self.session_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SessionHeader":
        raw_version = value.get("format_version")
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            raise TypeError("session header format_version must be an integer")
        if raw_version != SESSION_FORMAT_VERSION:
            raise UnsupportedSessionFormatError(raw_version)
        expected = {"format_version", "session_id", "created_at"}
        if set(value) != expected:
            raise ValueError(
                "session header must contain exactly format_version, session_id, created_at"
            )
        return cls(
            format_version=raw_version,
            session_id=value["session_id"],  # type: ignore[arg-type]
            created_at=value["created_at"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class PersistedSession:
    """Detached values read from one persistence implementation."""

    header: SessionHeader
    events: tuple[SessionEvent, ...]
    tail_truncated: bool = False


@runtime_checkable
class SessionPersistence(Protocol):
    """Single-writer persistence mechanism for one Session."""

    def create(self, header: SessionHeader) -> None: ...

    def load(self, session_id: str) -> PersistedSession: ...

    def append(self, event: SessionEvent) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class InMemorySessionPersistence:
    """Process-local implementation preserving the V0.1 development path."""

    __slots__ = ("_closed", "_events", "_header")

    def __init__(self) -> None:
        self._header: SessionHeader | None = None
        self._events: list[SessionEvent] = []
        self._closed = False

    def create(self, header: SessionHeader) -> None:
        self._require_open()
        if self._header is not None:
            raise SessionAlreadyExistsError(
                f"session already exists: {self._header.session_id}"
            )
        self._header = copy.deepcopy(header)

    def load(self, session_id: str) -> PersistedSession:
        self._require_open()
        if self._header is None:
            raise SessionNotFoundError(f"session not found: {session_id}")
        if self._header.session_id != session_id:
            raise SessionCorruptionError(
                f"requested session {session_id!r} does not match header "
                f"{self._header.session_id!r}"
            )
        return PersistedSession(
            header=copy.deepcopy(self._header),
            events=tuple(copy.deepcopy(self._events)),
        )

    def append(self, event: SessionEvent) -> None:
        self._require_open()
        if self._header is None:
            raise SessionPersistenceError("session persistence has not been created")
        expected = len(self._events) + 1
        if event.seq != expected:
            raise SessionPersistenceError(
                f"append seq {event.seq} does not match next seq {expected}"
            )
        self._events.append(copy.deepcopy(event))

    def flush(self) -> None:
        self._require_open()

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise SessionPersistenceError("session persistence is closed")


class JsonlSessionPersistence:
    """Inspectible append-only JSONL storage with single-writer semantics."""

    __slots__ = ("path", "_closed", "_file", "_header", "_next_seq")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file: IO[str] | None = None
        self._header: SessionHeader | None = None
        self._next_seq = 1
        self._closed = False

    def create(self, header: SessionHeader) -> None:
        self._require_open()
        if self._header is not None or self.path.exists():
            raise SessionAlreadyExistsError(
                f"session artifact already exists: {self.path}"
            )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("x", encoding="utf-8", newline="\n")
            self._file.write(_encode_header(header) + "\n")
            self._file.flush()
        except OSError as error:
            self._close_file()
            raise SessionPersistenceError(
                f"could not create session artifact {self.path}: {error}"
            ) from error
        self._header = copy.deepcopy(header)
        self._next_seq = 1

    def load(self, session_id: str) -> PersistedSession:
        self._require_open()
        if not self.path.exists():
            raise SessionNotFoundError(f"session artifact not found: {self.path}")
        if self._file is not None:
            self._file.flush()
        try:
            raw = self.path.read_bytes()
        except OSError as error:
            raise SessionPersistenceError(
                f"could not read session artifact {self.path}: {error}"
            ) from error
        header, events, tail_truncated = _decode_jsonl(raw, self.path)
        if header.session_id != session_id:
            raise SessionCorruptionError(
                f"requested session {session_id!r} does not match header "
                f"{header.session_id!r}"
            )
        self._header = copy.deepcopy(header)
        self._next_seq = len(events) + 1
        return PersistedSession(
            header=header,
            events=events,
            tail_truncated=tail_truncated,
        )

    def append(self, event: SessionEvent) -> None:
        self._require_open()
        if self._header is None:
            raise SessionPersistenceError(
                "load or create the JSONL session before appending"
            )
        if event.seq != self._next_seq:
            raise SessionPersistenceError(
                f"append seq {event.seq} does not match next seq {self._next_seq}"
            )
        if self._file is None:
            try:
                self._file = self.path.open("a", encoding="utf-8", newline="\n")
            except OSError as error:
                raise SessionPersistenceError(
                    f"could not open session artifact {self.path}: {error}"
                ) from error
        try:
            self._file.write(_encode_event(event) + "\n")
            self._file.flush()
        except OSError as error:
            raise SessionPersistenceError(
                f"could not append session event to {self.path}: {error}"
            ) from error
        self._next_seq += 1

    def flush(self) -> None:
        self._require_open()
        if self._file is None:
            return
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError as error:
            raise SessionPersistenceError(
                f"could not flush session artifact {self.path}: {error}"
            ) from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush()
        finally:
            self._close_file()
            self._closed = True

    def _close_file(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _require_open(self) -> None:
        if self._closed:
            raise SessionPersistenceError("session persistence is closed")


def _encode_header(header: SessionHeader) -> str:
    return _encode_record({"record_type": _HEADER_RECORD_TYPE, **header.as_dict()})


def _encode_event(event: SessionEvent) -> str:
    return _encode_record({"record_type": _EVENT_RECORD_TYPE, **event.as_dict()})


def _encode_record(record: Mapping[str, JsonValue]) -> str:
    detached = copy.deepcopy(dict(record))
    if not is_json_value(detached):
        raise SessionPersistenceError("session record must be lossless JSON")
    return json.dumps(
        detached,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_jsonl(
    raw: bytes,
    path: Path,
) -> tuple[SessionHeader, tuple[SessionEvent, ...], bool]:
    if not raw:
        raise SessionCorruptionError(f"session artifact is empty: {path}")
    lines = raw.splitlines(keepends=True)
    header: SessionHeader | None = None
    events: list[SessionEvent] = []
    tail_truncated = False
    for index, physical in enumerate(lines):
        is_last = index == len(lines) - 1
        terminated = physical.endswith((b"\n", b"\r"))
        content = physical.rstrip(b"\r\n")
        if not content:
            raise SessionCorruptionError(
                f"blank JSONL record at line {index + 1}: {path}"
            )
        try:
            text = content.decode("utf-8")
            value = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            if is_last and not terminated and index > 0:
                tail_truncated = True
                break
            raise SessionCorruptionError(
                f"malformed JSONL record at line {index + 1}: {path}"
            ) from error
        if not isinstance(value, Mapping):
            raise SessionCorruptionError(
                f"JSONL record at line {index + 1} must be an object: {path}"
            )
        record_type = value.get("record_type")
        payload = {key: item for key, item in value.items() if key != "record_type"}
        try:
            if index == 0:
                if record_type != _HEADER_RECORD_TYPE:
                    raise ValueError("first record must be session/header")
                header = SessionHeader.from_dict(payload)
            else:
                if record_type != _EVENT_RECORD_TYPE:
                    raise ValueError("non-header record must be session/event")
                events.append(SessionEvent.from_dict(payload))
        except UnsupportedSessionFormatError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise SessionCorruptionError(
                f"invalid {record_type!r} record at line {index + 1}: {error}"
            ) from error
    if header is None:
        raise SessionCorruptionError(f"session header is missing: {path}")
    return header, tuple(events), tail_truncated
