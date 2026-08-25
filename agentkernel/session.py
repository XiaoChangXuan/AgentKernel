"""Append-only session log and model-history projection."""

from __future__ import annotations

import copy
import time
from typing import Mapping

from .events import EventType, SessionEvent
from .persistence import (
    InMemorySessionPersistence,
    SessionHeader,
    SessionPersistence,
    SessionPersistenceError,
)
from .protocol import JsonValue, Message, ToolCall, ToolResult, is_json_value
from .recovery import RecoveryAnalysis, analyze_recovery


class Session:
    """Own the authoritative event log for one agent conversation."""

    __slots__ = (
        "session_id",
        "header",
        "_closed",
        "_events",
        "_persistence",
        "_tail_truncated",
    )

    def __init__(
        self,
        session_id: str,
        persistence: SessionPersistence | None = None,
    ) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        self.session_id = session_id
        self.header = SessionHeader.create(session_id)
        self._events: list[SessionEvent] = []
        self._persistence = persistence or InMemorySessionPersistence()
        self._tail_truncated = False
        self._closed = False
        self._persistence.create(self.header)

    @classmethod
    def load(
        cls,
        session_id: str,
        persistence: SessionPersistence,
    ) -> "Session":
        """Load and validate without repairing or rewriting historical records."""

        persisted = persistence.load(session_id)
        try:
            analyze_recovery(
                persisted.events,
                tail_truncated=persisted.tail_truncated,
            )
        except BaseException:
            persistence.close()
            raise
        session = cls.__new__(cls)
        session.session_id = persisted.header.session_id
        session.header = persisted.header
        session._events = list(copy.deepcopy(persisted.events))
        session._persistence = persistence
        session._tail_truncated = persisted.tail_truncated
        session._closed = False
        return session

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        """Return a detached snapshot; callers cannot mutate the canonical log."""

        return tuple(copy.deepcopy(self._events))

    @property
    def recovery_analysis(self) -> RecoveryAnalysis:
        """Recompute semantic recovery facts without choosing a resume policy."""

        return analyze_recovery(
            tuple(self._events),
            tail_truncated=self._tail_truncated,
        )

    def append(
        self,
        event_type: EventType,
        data: Mapping[str, JsonValue],
    ) -> SessionEvent:
        """Append one validated JSON event with a contiguous sequence number."""

        if self._closed:
            raise SessionPersistenceError("session is closed")
        if self._tail_truncated:
            raise SessionPersistenceError(
                "cannot append after a truncated tail without explicit repair"
            )
        if not isinstance(data, Mapping):
            raise TypeError("session event data must be a mapping")
        snapshot = copy.deepcopy(dict(data))
        if not is_json_value(snapshot):
            raise TypeError("session event data must be lossless JSON")
        event = SessionEvent(
            seq=len(self._events) + 1,
            type=EventType(event_type),
            data=snapshot,
            time=time.time(),
        )
        self._persistence.append(event)
        self._events.append(event)
        return copy.deepcopy(event)

    def flush(self) -> None:
        """Establish the persistence driver's explicit durability boundary."""

        if self._closed:
            raise SessionPersistenceError("session is closed")
        self._persistence.flush()

    def close(self) -> None:
        """Close persistence resources; repeated calls are harmless."""

        if self._closed:
            return
        self._persistence.close()
        self._closed = True

    def derive_messages(self) -> tuple[Message, ...]:
        """Project provider-neutral model history from the event log."""

        messages: list[Message] = []
        for event in self._events:
            if event.type is EventType.USER_MESSAGE:
                messages.append(Message.user(str(event.data["content"])))
            elif event.type is EventType.ASSISTANT_MESSAGE:
                raw_calls = event.data.get("tool_calls", [])
                if not isinstance(raw_calls, list):
                    raise TypeError("stored assistant tool_calls must be a list")
                calls = tuple(
                    ToolCall.from_dict(call)
                    for call in raw_calls
                    if isinstance(call, Mapping)
                )
                if len(calls) != len(raw_calls):
                    raise TypeError("stored assistant tool call must be a mapping")
                messages.append(
                    Message.assistant(str(event.data.get("content", "")), calls)
                )
            elif event.type is EventType.TOOL_RESULT:
                messages.append(Message.tool(ToolResult.from_dict(event.data)))
        return tuple(messages)
