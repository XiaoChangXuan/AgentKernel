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
    SummaryProvenance,
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
            if event.type is EventType.USER_MESSAGE:
                turn = _event_turn(data.get("turn"))
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
                turn = _event_turn(data.get("turn"))
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
                turn = _event_turn(data.get("turn"))
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
        return _apply_completed_summaries(session, tuple(pages))

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


def _apply_completed_summaries(
    session: Session,
    raw_pages: tuple[ContextPage, ...],
) -> tuple[ContextPage, ...]:
    """Fold durable completed checkpoints over raw model-visible Pages."""

    visible = list(raw_pages)
    created: dict[str, object] = {}
    for event in session.events:
        if event.type is EventType.CONTEXT_SUMMARY_CREATED:
            compaction_id = event.data.get("compaction_id")
            if isinstance(compaction_id, str) and compaction_id:
                created[compaction_id] = event
            continue
        if event.type is not EventType.CONTEXT_COMPACTION_COMPLETED:
            continue
        compaction_id = event.data.get("compaction_id")
        if not isinstance(compaction_id, str):
            raise ContextProtocolError("completed compaction lacks compaction_id")
        summary_event = created.get(compaction_id)
        if summary_event is None:
            raise ContextProtocolError(
                f"completed compaction lacks durable summary: {compaction_id}"
            )
        data = summary_event.data  # type: ignore[union-attr]
        source_page_ids = _string_tuple(data.get("source_page_ids"), "source_page_ids")
        positions = []
        by_id = {page.page_id: index for index, page in enumerate(visible)}
        for page_id in source_page_ids:
            position = by_id.get(page_id)
            if position is None:
                raise ContextProtocolError(
                    f"summary source page is not visible during replay: {page_id}"
                )
            positions.append(position)
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ContextProtocolError("summary source pages are not a contiguous range")
        sources = visible[positions[0] : positions[-1] + 1]
        summary_page_id = _non_empty_string(data.get("summary_page_id"), "summary_page_id")
        content = _non_empty_string(data.get("content"), "summary content")
        summary_cost = _non_negative_int(data.get("summary_token_cost"), "summary_token_cost")
        provenance = SummaryProvenance(
            compaction_id=compaction_id,
            source_start_seq=_positive_int(data.get("source_start_seq"), "source_start_seq"),
            source_end_seq=_positive_int(data.get("source_end_seq"), "source_end_seq"),
            source_page_ids=source_page_ids,
            source_event_seqs=_positive_int_tuple(
                data.get("source_event_seqs"), "source_event_seqs"
            ),
            source_token_cost=_non_negative_int(
                data.get("source_token_cost"), "source_token_cost"
            ),
            original_source_token_cost=_non_negative_int(
                data.get("original_source_token_cost"),
                "original_source_token_cost",
            ),
            summary_token_cost=summary_cost,
            created_at=_finite_number(data.get("created_at"), "created_at"),
            source_fingerprint=_non_empty_string(
                data.get("source_fingerprint"), "source_fingerprint"
            ),
            parent_summary_page_ids=_string_tuple(
                data.get("parent_summary_page_ids", []),
                "parent_summary_page_ids",
                allow_empty=True,
            ),
            model=_optional_string(data.get("model"), "model"),
            provider=_optional_string(data.get("provider"), "provider"),
        )
        turns = [page.turn for page in sources if page.turn is not None]
        summary_page = ContextPage(
            page_id=summary_page_id,
            kind=ContextPageKind.SUMMARY,
            content=content,
            token_cost=summary_cost,
            priority=0,
            temperature=ContextTemperature.WARM,
            pinned=False,
            trust_label=ContextTrustLabel.EXTERNAL,
            created_seq=provenance.source_start_seq,
            turn=max(turns) if turns else None,
            message=Message.user(content),
            summary=provenance,
        )
        visible[positions[0] : positions[-1] + 1] = [summary_page]
    return tuple(visible)


def _non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContextProtocolError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _non_empty_string(value, name)


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContextProtocolError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: object, name: str) -> int:
    result = _non_negative_int(value, name)
    if result < 1:
        raise ContextProtocolError(f"{name} must be a positive integer")
    return result


def _string_tuple(
    value: object, name: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or (
        not allow_empty and not value
    ) or any(not isinstance(item, str) or not item for item in value):
        raise ContextProtocolError(f"{name} must be a list of non-empty strings")
    return tuple(value)


def _positive_int_tuple(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ContextProtocolError(f"{name} must be a non-empty integer list")
    return tuple(_positive_int(item, name) for item in value)


def _finite_number(value: object, name: str) -> float:
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContextProtocolError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ContextProtocolError(f"{name} must be a finite number")
    return result
