"""Projection from selected MemoryRecords into bounded Context VM pages."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import Literal

from ..context import (
    ApproximateTokenEstimator,
    ContextPage,
    ContextPageKind,
    ContextTemperature,
    ContextTrustLabel,
    TokenEstimator,
)
from ..protocol import Message
from .model import MemoryProposal, MemoryRecord


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
    include_inactive: bool = False,
    heading: str = "Long-term memory",
) -> MemoryContextProjection:
    """Project selected memories into ordinary ContextPages.

    This function never scans or injects a MemoryStore. Callers must retrieve and
    select bounded records first, then explicitly project that bounded selection.
    """

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    estimator = estimator or ApproximateTokenEstimator()
    filtered = tuple(
        record for record in records if include_inactive or record.lifecycle_state == "ACTIVE"
    )
    selected = filtered[:top_k]
    pages: list[ContextPage] = []
    for index, record in enumerate(selected, start=1):
        content = _format_memory_context(record, heading=heading)
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
    total = len(filtered) if total_memory_records is None else total_memory_records
    return MemoryContextProjection(
        selected_records=selected,
        pages=tuple(pages),
        total_memory_records=total,
    )


def project_conflicting_memories_to_context_pages(
    records: Iterable[MemoryRecord],
    *,
    total_memory_records: int | None = None,
    top_k: int,
    estimator: TokenEstimator | None = None,
    created_seq_start: int = 0,
) -> MemoryContextProjection:
    """Project active conflicting memories with explicit relation metadata."""

    conflicting = tuple(
        record
        for record in records
        if record.lifecycle_state == "ACTIVE" and record.conflict_group_id is not None
    )
    return project_memories_to_context_pages(
        conflicting,
        total_memory_records=total_memory_records,
        top_k=top_k,
        estimator=estimator,
        created_seq_start=created_seq_start,
        heading="Conflicting memory",
    )


def project_memory_proposals_to_context_pages(
    proposals: Iterable[MemoryProposal],
    *,
    total_proposals: int | None = None,
    top_k: int,
    estimator: TokenEstimator | None = None,
    created_seq_start: int = 0,
    heading: str = "Memory proposal audit",
) -> MemoryContextProjection:
    """Project proposal audit state only when callers explicitly request it."""

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    estimator = estimator or ApproximateTokenEstimator()
    selected = tuple(proposals)[:top_k]
    pages: list[ContextPage] = []
    for index, proposal in enumerate(selected, start=1):
        content = _format_proposal_context(proposal, heading=heading)
        pages.append(
            ContextPage(
                page_id=f"memory-proposal:{proposal.proposal_id}",
                kind=ContextPageKind.USER_MESSAGE,
                content=content,
                token_cost=estimator.count_text(content),
                priority=40,
                temperature=ContextTemperature.COLD,
                pinned=False,
                trust_label=ContextTrustLabel.KERNEL,
                created_seq=created_seq_start + index,
                turn=None,
                message=Message.user(content),
            )
        )
    total = len(selected) if total_proposals is None else total_proposals
    return MemoryContextProjection(
        selected_records=(),
        pages=tuple(pages),
        total_memory_records=total,
    )


def _format_memory_context(
    record: MemoryRecord,
    *,
    heading: Literal["Long-term memory", "Conflicting memory"] | str,
) -> str:
    lines = [
        f"[{heading}]",
        f"- content: {record.content}",
        f"  memory_id: {record.memory_id}",
        f"  status: {record.lifecycle_state}",
        f"  namespace: {record.namespace}",
        f"  source: {record.uri}",
        f"  provenance: {record.provenance.source}",
        f"  source_class: {record.provenance.source_class}",
    ]
    admitted_from = record.metadata.get("admitted_from_proposal_id")
    if isinstance(admitted_from, str):
        lines.append(f"  admitted_from_proposal_id: {admitted_from}")
    if record.provenance.source_session_id is not None:
        lines.append(f"  provenance_session: {record.provenance.source_session_id}")
    if record.provenance.source_event_id is not None:
        lines.append(f"  provenance_event: {record.provenance.source_event_id}")
    if record.provenance.source_tool_name is not None:
        lines.append(f"  provenance_tool: {record.provenance.source_tool_name}")
    if record.provenance.source_tool_call_id is not None:
        lines.append(f"  provenance_tool_call: {record.provenance.source_tool_call_id}")
    if record.provenance.source_resource is not None:
        lines.append(f"  provenance_resource: {record.provenance.source_resource}")
    if record.stale_reason is not None:
        lines.append(f"  stale_reason: {record.stale_reason}")
    if record.stale_provenance is not None:
        lines.append(f"  stale_evidence: {record.stale_provenance.source}")
    if record.supersedes_memory_id is not None:
        lines.append(f"  supersedes: {record.supersedes_memory_id}")
    if record.superseded_by_memory_id is not None:
        lines.append(f"  superseded_by: {record.superseded_by_memory_id}")
    if record.conflict_group_id is not None:
        lines.append(f"  conflict_group: {record.conflict_group_id}")
        lines.append(
            "  conflicts_with: "
            + ", ".join(record.conflicts_with_memory_ids)
        )
        if record.conflict_reason is not None:
            lines.append(f"  conflict_reason: {record.conflict_reason}")
        if record.conflict_provenance is not None:
            lines.append(f"  conflict_evidence: {record.conflict_provenance.source}")
        lines.append("  note: conflicting memories are preserved; Kernel does not choose truth")
    return "\n".join(lines)


def _format_proposal_context(
    proposal: MemoryProposal,
    *,
    heading: str,
) -> str:
    lines = [
        f"[{heading}]",
        f"- content: {proposal.content}",
        f"  proposal_id: {proposal.proposal_id}",
        f"  admission: {proposal.admission_state}",
        f"  namespace: {proposal.namespace}",
        f"  proposer_agent_id: {proposal.proposer_agent_id}",
        f"  source: {proposal.provenance.source}",
        f"  source_class: {proposal.provenance.source_class}",
        f"  has_untrusted_origin: {proposal.has_untrusted_origin}",
    ]
    if proposal.provenance.source_session_id is not None:
        lines.append(f"  source_session: {proposal.provenance.source_session_id}")
    if proposal.provenance.source_event_id is not None:
        lines.append(f"  source_event: {proposal.provenance.source_event_id}")
    if proposal.provenance.source_tool_name is not None:
        lines.append(f"  source_tool: {proposal.provenance.source_tool_name}")
    if proposal.provenance.source_tool_call_id is not None:
        lines.append(f"  source_tool_call: {proposal.provenance.source_tool_call_id}")
    if proposal.provenance.source_resource is not None:
        lines.append(f"  source_resource: {proposal.provenance.source_resource}")
    if proposal.admitted_memory_id is not None:
        lines.append(f"  admitted_memory_id: {proposal.admitted_memory_id}")
    lines.append("  note: proposal audit is explicit; default Context excludes proposals")
    return "\n".join(lines)
