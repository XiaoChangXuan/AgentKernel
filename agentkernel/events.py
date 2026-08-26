"""Durable event vocabulary for AgentKernel sessions."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from .protocol import JsonValue, is_json_value


class EventType(StrEnum):
    """Closed durable Session event vocabulary."""

    TURN_START = "turn/start"
    USER_MESSAGE = "user/message"
    STEP_START = "step/start"
    ASSISTANT_MESSAGE = "assistant/message"
    TOOL_CALL = "tool/call"
    TOOL_PREPARE = "tool/prepare"
    TOOL_DISPATCH = "tool/dispatch"
    TOOL_COMMIT = "tool/commit"
    TOOL_ABORT = "tool/abort"
    TOOL_RECONCILE = "tool/reconcile"
    AUTHORIZATION_GRANTED = "authorization/granted"
    AUTHORIZATION_DENIED = "authorization/denied"
    TOOL_RESULT = "tool/result"
    CONTEXT_COMPACTION_REQUESTED = "context/compaction-requested"
    CONTEXT_COMPACTION_STARTED = "context/compaction-started"
    CONTEXT_SUMMARY_CREATED = "context/summary-created"
    CONTEXT_COMPACTION_COMPLETED = "context/compaction-completed"
    CONTEXT_COMPACTION_ABORTED = "context/compaction-aborted"
    STEP_END = "step/end"
    TURN_END = "turn/end"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One immutable entry in a session's append-only log."""

    seq: int
    type: EventType
    data: Mapping[str, JsonValue]
    time: float

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise ValueError("session event seq must be a positive integer")
        try:
            event_type = EventType(self.type)
        except ValueError as error:
            raise ValueError(f"unknown session event type: {self.type}") from error
        if not isinstance(self.data, Mapping):
            raise TypeError("session event data must be a mapping")
        snapshot = copy.deepcopy(dict(self.data))
        if not is_json_value(snapshot):
            raise TypeError("session event data must be lossless JSON")
        if (
            isinstance(self.time, bool)
            or not isinstance(self.time, (int, float))
            or not math.isfinite(self.time)
        ):
            raise ValueError("session event time must be a finite number")
        object.__setattr__(self, "type", event_type)
        object.__setattr__(self, "data", snapshot)
        object.__setattr__(self, "time", float(self.time))

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible representation."""

        return {
            "seq": self.seq,
            "type": self.type.value,
            "data": copy.deepcopy(dict(self.data)),
            "time": self.time,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SessionEvent":
        """Decode one strict, language-neutral event representation."""

        expected = {"seq", "type", "data", "time"}
        if set(value) != expected:
            raise ValueError("session event must contain exactly seq, type, data, time")
        raw_type = value["type"]
        raw_data = value["data"]
        if not isinstance(raw_type, str):
            raise TypeError("session event type must be a string")
        if not isinstance(raw_data, Mapping):
            raise TypeError("session event data must be an object")
        return cls(
            seq=value["seq"],  # type: ignore[arg-type]
            type=raw_type,  # type: ignore[arg-type]
            data=copy.deepcopy(dict(raw_data)),
            time=value["time"],  # type: ignore[arg-type]
        )
