"""Small notification seam for observable loop lifecycle points."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from .protocol import ToolCall, ToolResult


class HookPoint(StrEnum):
    """Lifecycle notifications available in V0.1."""

    BEFORE_STEP = "before_step"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"


@dataclass(frozen=True, slots=True)
class HookEvent:
    """Read-only notification delivered to a hook listener."""

    point: HookPoint
    agent_id: str
    turn: int
    step: int
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None


HookListener: TypeAlias = Callable[[HookEvent], None | Awaitable[None]]


class HookManager:
    """Register and emit notifications without a plugin runtime."""

    def __init__(self) -> None:
        self._listeners: dict[HookPoint, list[HookListener]] = {
            point: [] for point in HookPoint
        }

    def subscribe(self, point: HookPoint, listener: HookListener) -> Callable[[], None]:
        """Register a listener and return an idempotent unsubscribe function."""

        listeners = self._listeners[point]
        listeners.append(listener)

        def unsubscribe() -> None:
            if listener in listeners:
                listeners.remove(listener)

        return unsubscribe

    async def notify(self, event: HookEvent) -> None:
        """Notify a stable snapshot of listeners in registration order."""

        for listener in tuple(self._listeners[event.point]):
            result = listener(event)
            if inspect.isawaitable(result):
                await result

