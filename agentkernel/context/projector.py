"""Session Event Log to Context Page projection."""

from __future__ import annotations

import json
from dataclasses import replace

from ..events import EventType
from ..protocol import Message, ToolCall, ToolResult
from ..session import Session
from .model import (
    ContextPage,
    ContextPageKind,
    ContextProtocolError,
    ContextTemperature,
    ContextTrustLabel,
)
from .tokens import ApproximateTokenEstimator, TokenEstimator


class ContextProjector:
    """Project Session facts and the current host system prompt into pages."""

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self.estimator = estimator or ApproximateTokenEstimator()

    def project(
        self,
        session: Session,
        *,
        system_prompt: str | None = None,
    ) -> tuple[ContextPage, ...]:
        pages: list[ContextPage] = []
        if system_prompt is not None:
            pages.append(
                ContextPage(
                    page_id=f"session:{session.session_id}:system",
                    kind=ContextPageKind.SYSTEM,
                    content=system_prompt,
                    token_cost=self.estimator.count_text(system_prompt),
                    priority=0,
                    temperature=ContextTemperature.WARM,
                    pinned=False,
                    trust_label=ContextTrustLabel.KERNEL,
                    created_seq=0,
                    turn=None,
                )
            )

        call_to_assistant: dict[str, str] = {}
        completed_call_ids: set[str] = set()
        result_pages_by_assistant: dict[str, list[str]] = {}
        assistant_indexes: dict[str, int] = {}
        for event in session.events:
            data = event.data
            turn = _event_turn(data.get("turn"))
            if event.type is EventType.USER_MESSAGE:
                content = str(data["content"])
                pages.append(
                    self._message_page(
                        session.session_id,
                        event.seq,
                        turn,
                        ContextPageKind.USER_MESSAGE,
                        Message.user(content),
                        ContextTrustLabel.USER,
                    )
                )
            elif event.type is EventType.ASSISTANT_MESSAGE:
                raw_calls = data.get("tool_calls", [])
                if not isinstance(raw_calls, list):
                    raise ContextProtocolError("assistant tool_calls must be a list")
                try:
                    calls = tuple(ToolCall.from_dict(item) for item in raw_calls)
                except (AttributeError, KeyError, TypeError, ValueError) as error:
                    raise ContextProtocolError(
                        f"invalid assistant tool call in Context projection: {error}"
                    ) from error
                message = Message.assistant(str(data.get("content", "")), calls)
                page_id = _event_page_id(session.session_id, event.seq)
                atomic_group = f"tool:{page_id}" if calls else None
                page = self._message_page(
                    session.session_id,
                    event.seq,
                    turn,
                    ContextPageKind.ASSISTANT_MESSAGE,
                    message,
                    ContextTrustLabel.EXTERNAL,
                    atomic_group=atomic_group,
                )
                assistant_indexes[page_id] = len(pages)
                pages.append(page)
                for call in calls:
                    if call.call_id in call_to_assistant:
                        raise ContextProtocolError(
                            f"duplicate tool call in context projection: {call.call_id}"
                        )
                    call_to_assistant[call.call_id] = page_id
                if calls:
                    result_pages_by_assistant[page_id] = []
            elif event.type is EventType.TOOL_RESULT:
                try:
                    result = ToolResult.from_dict(data)
                except (KeyError, TypeError, ValueError) as error:
                    raise ContextProtocolError(
                        f"invalid Tool Result in Context projection: {error}"
                    ) from error
                assistant_id = call_to_assistant.get(result.call_id)
                if assistant_id is None:
                    raise ContextProtocolError(
                        f"tool result has no projected assistant call: {result.call_id}"
                    )
                if result.call_id in completed_call_ids:
                    raise ContextProtocolError(
                        f"duplicate Tool Result in Context projection: {result.call_id}"
                    )
                completed_call_ids.add(result.call_id)
                page = self._message_page(
                    session.session_id,
                    event.seq,
                    turn,
                    ContextPageKind.TOOL_RESULT,
                    Message.tool(result),
                    ContextTrustLabel.TOOL,
                    dependencies=(assistant_id,),
                    atomic_group=f"tool:{assistant_id}",
                )
                pages.append(page)
                result_pages_by_assistant[assistant_id].append(page.page_id)

        for assistant_id, result_ids in result_pages_by_assistant.items():
            index = assistant_indexes[assistant_id]
            assistant = pages[index]
            assert assistant.message is not None
            expected = len(assistant.message.tool_calls)
            if len(result_ids) != expected:
                raise ContextProtocolError(
                    f"assistant page {assistant_id} has {expected} tool calls but "
                    f"{len(result_ids)} results"
                )
            pages[index] = replace(assistant, dependencies=tuple(result_ids))
        return tuple(pages)

    def _message_page(
        self,
        session_id: str,
        seq: int,
        turn: int,
        kind: ContextPageKind,
        message: Message,
        trust_label: ContextTrustLabel,
        *,
        dependencies: tuple[str, ...] = (),
        atomic_group: str | None = None,
    ) -> ContextPage:
        cost = self.estimator.count_text(message.content)
        if message.tool_calls:
            encoded_calls = json.dumps(
                [call.as_dict() for call in message.tool_calls],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            cost += self.estimator.count_text(encoded_calls)
        return ContextPage(
            page_id=_event_page_id(session_id, seq),
            kind=kind,
            content=message.content,
            token_cost=cost,
            priority=0,
            temperature=ContextTemperature.WARM,
            pinned=False,
            trust_label=trust_label,
            created_seq=seq,
            turn=turn,
            dependencies=dependencies,
            atomic_group=atomic_group,
            message=message,
        )


def _event_page_id(session_id: str, seq: int) -> str:
    return f"session:{session_id}:event:{seq}"


def _event_turn(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContextProtocolError("model-facing Session event has invalid turn")
    return value
