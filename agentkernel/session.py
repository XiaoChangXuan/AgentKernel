"""Append-only session log and model-history projection."""

from __future__ import annotations

import copy
import time
from typing import Mapping

from .events import EventType, SessionEvent
from .protocol import JsonValue, Message, ToolCall, ToolResult, is_json_value


class Session:
    """Own the authoritative event log for one agent conversation."""

    __slots__ = ("session_id", "_events")

    def __init__(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        self.session_id = session_id
        self._events: list[SessionEvent] = []

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        """Return a detached snapshot; callers cannot mutate the canonical log."""

        return tuple(copy.deepcopy(self._events))

    def append(
        self,
        event_type: EventType,
        data: Mapping[str, JsonValue],
    ) -> SessionEvent:
        """Append one validated JSON event with a contiguous sequence number."""

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
        self._events.append(event)
        return copy.deepcopy(event)

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
