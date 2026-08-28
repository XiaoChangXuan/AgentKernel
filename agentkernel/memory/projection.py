"""Projection from selected MemoryRecords into bounded Context VM pages."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from ..context import (
    ApproximateTokenEstimator,
    ContextPage,
    ContextPageKind,
    ContextTemperature,
    ContextTrustLabel,
    TokenEstimator,
)
from ..protocol import Message
from .model import MemoryRecord


@dataclass(frozen=True, slots=True)
class MemoryContextProjection:
    """Bounded model-visible projection of selected long-term memories."""

    selected_records: tuple[MemoryRecord, ...]
    pages: tuple[ContextPage, ...]
    total_memory_records: int

    @property
    def selected_count(self) -> int:
        return len(self.selected_records)


def project_memories_to_context_pages(
    records: Iterable[MemoryRecord],
    *,
    total_memory_records: int | None = None,
    top_k: int,
    estimator: TokenEstimator | None = None,
    created_seq_start: int = 0,
) -> MemoryContextProjection:
    """Project selected memories into ordinary ContextPages.

    This function never scans or injects a MemoryStore. Callers must retrieve and
    select bounded records first, then explicitly project that bounded selection.
    """

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    estimator = estimator or ApproximateTokenEstimator()
    selected = tuple(records)[:top_k]
    pages: list[ContextPage] = []
    for index, record in enumerate(selected, start=1):
        content = (
            "[Long-term memory]\n"
            f"- {record.content}\n"
            f"  source={record.uri}\n"
            f"  provenance={record.provenance.source}"
        )
        pages.append(
            ContextPage(
                page_id=f"memory:{record.memory_id}",
                kind=ContextPageKind.USER_MESSAGE,
                content=content,
                token_cost=estimator.count_text(content),
                priority=100,
                temperature=ContextTemperature.WARM,
                pinned=False,
                trust_label=ContextTrustLabel.KERNEL,
                created_seq=created_seq_start + index,
                turn=None,
                message=Message.user(content),
            )
        )
    total = len(selected) if total_memory_records is None else total_memory_records
    return MemoryContextProjection(
        selected_records=selected,
        pages=tuple(pages),
        total_memory_records=total,
    )
