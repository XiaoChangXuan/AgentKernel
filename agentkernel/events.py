"""Durable event vocabulary for AgentKernel V0.1 sessions."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from .protocol import JsonValue


class EventType(StrEnum):
    """Closed V0.1 event vocabulary."""

    TURN_START = "turn/start"
    USER_MESSAGE = "user/message"
    STEP_START = "step/start"
    ASSISTANT_MESSAGE = "assistant/message"
    TOOL_CALL = "tool/call"
    TOOL_RESULT = "tool/result"
    STEP_END = "step/end"
    TURN_END = "turn/end"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One immutable entry in a session's append-only log."""

    seq: int
    type: EventType
    data: Mapping[str, JsonValue]
    time: float

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible representation."""

        return {
            "seq": self.seq,
            "type": self.type.value,
            "data": copy.deepcopy(dict(self.data)),
            "time": self.time,
        }

