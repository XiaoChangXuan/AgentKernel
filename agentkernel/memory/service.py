"""Kernel-owned persistent memory service."""

from __future__ import annotations

import copy
import math
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from ..capabilities import AuthorizationRequest, CapabilityEvaluator
from ..protocol import JsonValue
from .model import (
    MEMORY_ADMIT_ACTION,
    MEMORY_FORGET_ACTION,
    MEMORY_PROPOSE_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    MemoryAdmissionDecision,
    MemoryAdmissionRecord,
    MemoryAdmissionState,
    MemoryAccessDenied,
    MemoryCorruptionError,
    MemoryEvent,
    MemoryInvalid,
    MemoryNotFound,
    MemoryProposal,
    MemoryProvenance,
    MemoryRecord,
    memory_namespace_scope,
    memory_uri,
    parse_memory_uri,
)
from .store import InMemoryMemoryStore, MemoryStore

MemoryIdFactory = Callable[[], str]
MemoryEventIdFactory = Callable[[], str]
MemoryProposalIdFactory = Callable[[], str]
MemoryDecisionIdFactory = Callable[[], str]


class MemoryService:
    """Authorize, persist, retrieve, supersede, and forget long-term memory."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        *,
        clock: Callable[[], float] = time.time,
        memory_id_factory: MemoryIdFactory | None = None,
        event_id_factory: MemoryEventIdFactory | None = None,
        proposal_id_factory: MemoryProposalIdFactory | None = None,
        decision_id_factory: MemoryDecisionIdFactory | None = None,
    ) -> None:
        self._store = store or InMemoryMemoryStore()
        self._clock = clock
        self._memory_id_factory = memory_id_factory or _new_memory_id
        self._event_id_factory = event_id_factory or _new_memory_event_id
        self._proposal_id_factory = proposal_id_factory or _new_memory_proposal_id
        self._decision_id_factory = decision_id_factory or _new_memory_decision_id
        self._index: dict[str, set[str]] | None = None

    def propose(
        self,
        *,
        agent_id: str,
        namespace: str,
        content: str,
        provenance: MemoryProvenance,
        owner_agent_id: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
        proposal_id: str | None = None,
        has_untrusted_origin: bool | None = None,
    ) -> MemoryProposal:
        """Persist a memory proposal without admitting it as active memory."""

        owner = owner_agent_id or agent_id
        resource = memory_namespace_scope(owner, namespace)
        self._authorize(agent_id, MEMORY_PROPOSE_ACTION, resource, capability_evaluator)
        proposal = MemoryProposal(
            proposal_id=proposal_id or self._proposal_id_factory(),
            proposer_agent_id=agent_id,
            owner_agent_id=owner,
            namespace=namespace,
            content=content,
            provenance=provenance,
            created_at=self._clock(),
            metadata={} if metadata is None else copy.deepcopy(dict(metadata)),
            has_untrusted_origin=(
                _has_untrusted_origin(provenance)
                if has_untrusted_origin is None
                else has_untrusted_origin
            ),
        )
        if proposal.proposal_id in self._project_proposals_by_id():
            raise MemoryCorruptionError(f"memory proposal already exists: {proposal.proposal_id}")
        self._store.append(
            self._event_for_identity(
                "memory/proposed",
                agent_id=agent_id,
                memory_id=proposal.proposal_id,
                owner_agent_id=owner,
                namespace=namespace,
                data={"proposal": proposal.as_dict()},
            )
        )
        self._store.flush()
        return proposal

    def read_proposal(
        self,
        proposal_id: str,
        *,
        agent_id: str,
        capability_evaluator: CapabilityEvaluator | None = None,
    ) -> MemoryProposal:
        """Read one proposal for audit/debug after memory.read authorization."""

        proposal = self._resolve_proposal(proposal_id)
        self._authorize(
            agent_id,
            MEMORY_READ_ACTION,
            memory_namespace_scope(proposal.owner_agent_id, proposal.namespace),
            capability_evaluator,
        )
        return _clone_proposal(proposal)

    def list_proposals(
        self,
        *,
        agent_id: str,
        owner_agent_id: str,
        namespace: str | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
        include_states: Iterable[MemoryAdmissionState] | None = None,
    ) -> tuple[MemoryProposal, ...]:
        """List durable proposals for explicit audit/debug views."""

        scope = (
            f"memory://{owner_agent_id}/**"
            if namespace is None
            else memory_namespace_scope(owner_agent_id, namespace)
        )
        self._authorize(agent_id, MEMORY_READ_ACTION, scope, capability_evaluator)
        states = None if include_states is None else set(include_states)
        proposals = [
            proposal
            for proposal in self._project_proposals_by_id().values()
            if proposal.owner_agent_id == owner_agent_id
            and (namespace is None or proposal.namespace == namespace)
            and (states is None or proposal.admission_state in states)
        ]
        proposals.sort(key=lambda item: (item.created_at, item.proposal_id))
        return tuple(_clone_proposal(proposal) for proposal in proposals)

    def admission_history(
        self,
        proposal_id: str | None = None,
        *,
        agent_id: str,
        owner_agent_id: str,
        namespace: str | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
    ) -> tuple[MemoryAdmissionRecord, ...]:
        """Return durable admission decisions after memory.read authorization."""

        scope = (
            f"memory://{owner_agent_id}/**"
            if namespace is None
            else memory_namespace_scope(owner_agent_id, namespace)
        )
        self._authorize(agent_id, MEMORY_READ_ACTION, scope, capability_evaluator)
        proposal_ids = {
            proposal.proposal_id
            for proposal in self._project_proposals_by_id().values()
            if proposal.owner_agent_id == owner_agent_id
            and (namespace is None or proposal.namespace == namespace)
        }
        records = [
            record
            for record in self._project_admission_records()
            if record.proposal_id in proposal_ids
            and (proposal_id is None or record.proposal_id == proposal_id)
        ]
        records.sort(key=lambda item: (item.decided_at, item.decision_id))
        return tuple(_clone_admission(record) for record in records)

    def admit(
        self,
        proposal_id: str,
        *,
        agent_id: str,
        reason: str,
        evidence_provenance: MemoryProvenance,
        capability_evaluator: CapabilityEvaluator | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        """Admit an existing proposal as active memory with durable audit."""

        proposal = self._resolve_proposal(proposal_id)
        if proposal.admission_state == "ADMITTED":
            raise MemoryInvalid("proposal is already admitted")
        if proposal.admission_state == "REJECTED":
            raise MemoryInvalid("rejected proposal cannot be admitted")
        self._authorize(
            agent_id,
            MEMORY_ADMIT_ACTION,
            memory_namespace_scope(proposal.owner_agent_id, proposal.namespace),
            capability_evaluator,
        )
        record = MemoryRecord(
            memory_id=memory_id or self._memory_id_factory(),
            owner_agent_id=proposal.owner_agent_id,
            namespace=proposal.namespace,
            content=proposal.content,
            created_at=self._clock(),
            provenance=proposal.provenance,
            metadata={
                **copy.deepcopy(dict(proposal.metadata)),
                **({} if metadata is None else copy.deepcopy(dict(metadata))),
                "admitted_from_proposal_id": proposal.proposal_id,
            },
        )
        if record.memory_id in self._project_by_id(include_inactive=True):
            raise MemoryCorruptionError(f"memory already exists: {record.memory_id}")
        decision = self._admission_record(
            proposal=proposal,
            decision="ADMIT",
            agent_id=agent_id,
            reason=reason,
            evidence_provenance=evidence_provenance,
            resulting_memory_id=record.memory_id,
            metadata=metadata,
        )
        self._store.append(
            self._event_for_identity(
                "memory/admission",
                agent_id=agent_id,
                memory_id=proposal.proposal_id,
                owner_agent_id=proposal.owner_agent_id,
                namespace=proposal.namespace,
                data={"admission": decision.as_dict()},
            )
        )
        self._store.append(
            self._event(
                "memory/remembered",
                agent_id=agent_id,
                record=record,
                data={"record": record.as_dict()},
            )
        )
        self._store.flush()
        self._index = None
        return record

    def quarantine(
        self,
        proposal_id: str,
        *,
        agent_id: str,
        reason: str,
        evidence_provenance: MemoryProvenance,
        capability_evaluator: CapabilityEvaluator | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> MemoryProposal:
        """Keep a proposal durable for audit while excluding it from retrieval."""

        return self._decide_without_memory(
            proposal_id,
            agent_id=agent_id,
            decision="QUARANTINE",
            reason=reason,
            evidence_provenance=evidence_provenance,
            capability_evaluator=capability_evaluator,
            metadata=metadata,
        )

    def reject(
        self,
        proposal_id: str,
        *,
        agent_id: str,
        reason: str,
        evidence_provenance: MemoryProvenance,
        capability_evaluator: CapabilityEvaluator | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> MemoryProposal:
        """Reject a proposal durably without creating active memory."""

        return self._decide_without_memory(
            proposal_id,
            agent_id=agent_id,
            decision="REJECT",
            reason=reason,
            evidence_provenance=evidence_provenance,
            capability_evaluator=capability_evaluator,
            metadata=metadata,
        )

    def remember(
        self,
        *,
        agent_id: str,
        owner_agent_id: str | None = None,
        namespace: str,
        content: str,
        provenance: MemoryProvenance,
        metadata: Mapping[str, JsonValue] | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        """Append a durable memory write fact after current capability checks."""

        owner = owner_agent_id or agent_id
        resource = memory_namespace_scope(owner, namespace)
        self._authorize(agent_id, MEMORY_WRITE_ACTION, resource, capability_evaluator)
        record = MemoryRecord(
            memory_id=memory_id or self._memory_id_factory(),
            owner_agent_id=owner,
            namespace=namespace,
            content=content,
            created_at=self._clock(),
            provenance=provenance,
            metadata={} if metadata is None else copy.deepcopy(dict(metadata)),
        )
        if record.memory_id in self._project_by_id(include_inactive=True):
            raise MemoryCorruptionError(f"memory already exists: {record.memory_id}")
        event = self._event(
            "memory/remembered",
            agent_id=agent_id,
            record=record,
            data={"record": record.as_dict()},
        )
        self._store.append(event)
        self._store.flush()
        self._index = None
        return record

    def read(
        self,
        uri_or_memory_id: str,
        *,
        agent_id: str,
        capability_evaluator: CapabilityEvaluator | None = None,
        include_inactive: bool = False,
    ) -> MemoryRecord:
        """Read one memory by id or URI after current capability checks."""

        record = self._resolve(uri_or_memory_id, include_inactive=include_inactive)
        self._authorize(agent_id, MEMORY_READ_ACTION, record.uri, capability_evaluator)
        return _clone_record(record)

    def list(
        self,
        *,
        agent_id: str,
        owner_agent_id: str,
        namespace: str | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
        include_inactive: bool = False,
        include_stale: bool = False,
        include_superseded: bool = False,
        include_forgotten: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        """List projected memories for one owner, optionally namespace-filtered."""

        scope = (
            f"memory://{owner_agent_id}/**"
            if namespace is None
            else memory_namespace_scope(owner_agent_id, namespace)
        )
        self._authorize(agent_id, MEMORY_READ_ACTION, scope, capability_evaluator)
        records = [
            record
            for record in self._project_by_id(include_inactive=True).values()
            if record.owner_agent_id == owner_agent_id
            and (namespace is None or record.namespace == namespace)
            and _visible_for_options(
                record,
                include_inactive=include_inactive,
                include_stale=include_stale,
                include_superseded=include_superseded,
                include_forgotten=include_forgotten,
            )
        ]
        records.sort(key=lambda item: (item.created_at, item.memory_id))
        return tuple(_clone_record(record) for record in records)

    def history(
        self,
        *,
        agent_id: str,
        owner_agent_id: str,
        namespace: str | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return all lifecycle states for audit/debug after memory.read checks."""

        return self.list(
            agent_id=agent_id,
            owner_agent_id=owner_agent_id,
            namespace=namespace,
            capability_evaluator=capability_evaluator,
            include_inactive=True,
        )

    def search(
        self,
        *,
        agent_id: str,
        owner_agent_id: str,
        namespace: str | None,
        query: str,
        limit: int,
        capability_evaluator: CapabilityEvaluator | None = None,
        include_stale: bool = False,
        include_superseded: bool = False,
        include_forgotten: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        """Return deterministic lexical search results.

        By default this searches only active/usable memories. Stale,
        superseded, and forgotten records remain durable but require explicit
        retrieval options or `history()`.
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("search limit must be a positive integer")
        if not isinstance(query, str):
            raise TypeError("search query must be text")
        scope = (
            f"memory://{owner_agent_id}/**"
            if namespace is None
            else memory_namespace_scope(owner_agent_id, namespace)
        )
        self._authorize(agent_id, MEMORY_READ_ACTION, scope, capability_evaluator)
        self._ensure_index()
        query_tokens = _tokens(query)
        indexed_ids: set[str] | None = None
        if query_tokens:
            indexed_ids = set()
            assert self._index is not None
            for token in query_tokens:
                indexed_ids.update(self._index.get(token, set()))
        candidates = [
            record
            for record in self._project_by_id(include_inactive=True).values()
            if record.owner_agent_id == owner_agent_id
            and (namespace is None or record.namespace == namespace)
            and (indexed_ids is None or record.memory_id in indexed_ids)
            and _visible_for_options(
                record,
                include_inactive=False,
                include_stale=include_stale,
                include_superseded=include_superseded,
                include_forgotten=include_forgotten,
            )
        ]
        ranked = [
            (_score(record, query, query_tokens), -record.created_at, record.memory_id, record)
            for record in candidates
        ]
        ranked = [item for item in ranked if item[0] > 0 or not query_tokens]
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return tuple(_clone_record(item[3]) for item in ranked[:limit])

    def supersede(
        self,
        *,
        agent_id: str,
        old_memory_id: str,
        content: str,
        provenance: MemoryProvenance,
        metadata: Mapping[str, JsonValue] | None = None,
        capability_evaluator: CapabilityEvaluator | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        """Append a new memory and explicit old->new supersession fact."""

        old = self._resolve(old_memory_id)
        self._authorize(agent_id, MEMORY_WRITE_ACTION, old.uri, capability_evaluator)
        self._authorize(agent_id, MEMORY_FORGET_ACTION, old.uri, capability_evaluator)
        new = MemoryRecord(
            memory_id=memory_id or self._memory_id_factory(),
            owner_agent_id=old.owner_agent_id,
            namespace=old.namespace,
            content=content,
            created_at=self._clock(),
            provenance=provenance,
            metadata={} if metadata is None else copy.deepcopy(dict(metadata)),
            supersedes_memory_id=old.memory_id,
        )
        if new.memory_id in self._project_by_id(include_inactive=True):
            raise MemoryCorruptionError(f"memory already exists: {new.memory_id}")
        self._store.append(
            self._event(
                "memory/remembered",
                agent_id=agent_id,
                record=new,
                data={"record": new.as_dict()},
            )
        )
        self._store.append(
            self._event(
                "memory/superseded",
                agent_id=agent_id,
                record=old,
                data={
                    "old_memory_id": old.memory_id,
                    "new_memory_id": new.memory_id,
                    "old_uri": old.uri,
                    "new_uri": new.uri,
                },
            )
        )
        self._store.flush()
        self._index = None
        return new

    def mark_stale(
        self,
        uri_or_memory_id: str,
        *,
        agent_id: str,
        reason: str,
        evidence_provenance: MemoryProvenance,
        capability_evaluator: CapabilityEvaluator | None = None,
        observed_at: float | None = None,
    ) -> MemoryRecord:
        """Mark one memory stale with explicit freshness evidence provenance."""

        if not isinstance(reason, str) or not reason:
            raise MemoryInvalid("stale reason must be a non-empty string")
        if observed_at is not None and (
            isinstance(observed_at, bool)
            or not isinstance(observed_at, (int, float))
            or not math.isfinite(float(observed_at))
        ):
            raise MemoryInvalid("observed_at must be None or a finite number")
        record = self._resolve(uri_or_memory_id)
        self._authorize(agent_id, MEMORY_WRITE_ACTION, record.uri, capability_evaluator)
        self._store.append(
            self._event(
                "memory/stale",
                agent_id=agent_id,
                record=record,
                data={
                    "memory_id": record.memory_id,
                    "uri": record.uri,
                    "reason": reason,
                    "evidence_provenance": evidence_provenance.as_dict(),
                    "observed_at": observed_at,
                },
            )
        )
        self._store.flush()
        self._index = None
        return _clone_record(
            self._resolve(record.memory_id, include_inactive=True)
        )

    def mark_conflict(
        self,
        *,
        agent_id: str,
        memory_ids: Iterable[str],
        reason: str,
        evidence_provenance: MemoryProvenance,
        capability_evaluator: CapabilityEvaluator | None = None,
        conflict_group_id: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Persist an explicit conflict relation without resolving belief."""

        ids = tuple(dict.fromkeys(memory_ids))
        if len(ids) < 2:
            raise MemoryInvalid("conflict relation requires at least two memories")
        if not isinstance(reason, str) or not reason:
            raise MemoryInvalid("conflict reason must be a non-empty string")
        records = tuple(self._resolve(memory_id) for memory_id in ids)
        owner_namespace = {(record.owner_agent_id, record.namespace) for record in records}
        if len(owner_namespace) != 1:
            raise MemoryInvalid("conflict relation must stay within one owner namespace")
        for record in records:
            self._authorize(agent_id, MEMORY_WRITE_ACTION, record.uri, capability_evaluator)
        group_id = conflict_group_id or _stable_conflict_group_id(ids)
        event = self._event(
            "memory/conflict",
            agent_id=agent_id,
            record=records[0],
            data={
                "memory_ids": list(ids),
                "conflict_group_id": group_id,
                "reason": reason,
                "evidence_provenance": evidence_provenance.as_dict(),
            },
        )
        self._store.append(event)
        self._store.flush()
        self._index = None
        projected = self._project_by_id(include_inactive=True)
        return tuple(_clone_record(projected[memory_id]) for memory_id in ids)

    def forget(
        self,
        uri_or_memory_id: str,
        *,
        agent_id: str,
        capability_evaluator: CapabilityEvaluator | None = None,
    ) -> MemoryRecord:
        """Mark a memory inactive for default retrieval without rewriting history."""

        record = self._resolve(uri_or_memory_id)
        self._authorize(agent_id, MEMORY_FORGET_ACTION, record.uri, capability_evaluator)
        self._store.append(
            self._event(
                "memory/forgotten",
                agent_id=agent_id,
                record=record,
                data={"memory_id": record.memory_id, "uri": record.uri},
            )
        )
        self._store.flush()
        self._index = None
        return _clone_record(
            self._resolve(record.memory_id, include_inactive=True)
        )

    def durable_events(self) -> tuple[MemoryEvent, ...]:
        """Return detached durable memory facts."""

        return self._store.list_events()

    def rebuild_index(self) -> None:
        """Rebuild the lexical search projection from durable facts."""

        self._index = _build_index(self._project_by_id(include_inactive=True).values())

    def drop_index(self) -> None:
        """Discard the rebuildable search index projection."""

        self._index = None

    def close(self) -> None:
        self._store.close()

    def _authorize(
        self,
        agent_id: str,
        action: str,
        resource: str,
        capability_evaluator: CapabilityEvaluator | None,
    ) -> None:
        if capability_evaluator is None:
            raise MemoryAccessDenied(f"agent lacks {action} capability for memory")
        decision = capability_evaluator.authorize(
            AuthorizationRequest(agent_id=agent_id, action=action, resource=resource)
        )
        if not decision.allowed:
            raise MemoryAccessDenied(f"agent lacks {action} capability for memory")

    def _resolve(self, uri_or_memory_id: str, *, include_inactive: bool = False) -> MemoryRecord:
        requested_memory_id = uri_or_memory_id
        if uri_or_memory_id.startswith("memory://"):
            _owner, _namespace, requested_memory_id = parse_memory_uri(uri_or_memory_id)
        records = self._project_by_id(include_inactive=True)
        record = records.get(requested_memory_id)
        if record is None or (not include_inactive and not record.active):
            raise MemoryNotFound(f"memory not found: {uri_or_memory_id}")
        if uri_or_memory_id.startswith("memory://") and record.uri != uri_or_memory_id:
            raise MemoryNotFound(f"memory URI does not match projected record: {uri_or_memory_id}")
        return record

    def _event(
        self,
        event_type: str,
        *,
        agent_id: str,
        record: MemoryRecord,
        data: Mapping[str, JsonValue],
    ) -> MemoryEvent:
        return MemoryEvent(
            event_id=self._event_id_factory(),
            event_type=event_type,  # type: ignore[arg-type]
            agent_id=agent_id,
            memory_id=record.memory_id,
            owner_agent_id=record.owner_agent_id,
            namespace=record.namespace,
            created_at=self._clock(),
            data=copy.deepcopy(dict(data)),
        )

    def _event_for_identity(
        self,
        event_type: str,
        *,
        agent_id: str,
        memory_id: str,
        owner_agent_id: str,
        namespace: str,
        data: Mapping[str, JsonValue],
    ) -> MemoryEvent:
        return MemoryEvent(
            event_id=self._event_id_factory(),
            event_type=event_type,  # type: ignore[arg-type]
            agent_id=agent_id,
            memory_id=memory_id,
            owner_agent_id=owner_agent_id,
            namespace=namespace,
            created_at=self._clock(),
            data=copy.deepcopy(dict(data)),
        )

    def _project_by_id(self, *, include_inactive: bool) -> dict[str, MemoryRecord]:
        records = _project_records(self._store.list_events())
        if include_inactive:
            return records
        return {key: record for key, record in records.items() if record.active}

    def _ensure_index(self) -> None:
        if self._index is None:
            self.rebuild_index()

    def _admission_record(
        self,
        *,
        proposal: MemoryProposal,
        decision: MemoryAdmissionDecision,
        agent_id: str,
        reason: str,
        evidence_provenance: MemoryProvenance,
        resulting_memory_id: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> MemoryAdmissionRecord:
        return MemoryAdmissionRecord(
            decision_id=self._decision_id_factory(),
            proposal_id=proposal.proposal_id,
            decision=decision,
            decided_by_agent_id=agent_id,
            reason=reason,
            evidence_provenance=evidence_provenance,
            decided_at=self._clock(),
            resulting_memory_id=resulting_memory_id,
            metadata={} if metadata is None else copy.deepcopy(dict(metadata)),
        )

    def _decide_without_memory(
        self,
        proposal_id: str,
        *,
        agent_id: str,
        decision: MemoryAdmissionDecision,
        reason: str,
        evidence_provenance: MemoryProvenance,
        capability_evaluator: CapabilityEvaluator | None,
        metadata: Mapping[str, JsonValue] | None,
    ) -> MemoryProposal:
        proposal = self._resolve_proposal(proposal_id)
        if proposal.admission_state == "ADMITTED":
            raise MemoryInvalid("admitted proposal cannot be changed without lifecycle APIs")
        if proposal.admission_state == "REJECTED":
            raise MemoryInvalid("rejected proposal is terminal")
        self._authorize(
            agent_id,
            MEMORY_ADMIT_ACTION,
            memory_namespace_scope(proposal.owner_agent_id, proposal.namespace),
            capability_evaluator,
        )
        record = self._admission_record(
            proposal=proposal,
            decision=decision,
            agent_id=agent_id,
            reason=reason,
            evidence_provenance=evidence_provenance,
            metadata=metadata,
        )
        self._store.append(
            self._event_for_identity(
                "memory/admission",
                agent_id=agent_id,
                memory_id=proposal.proposal_id,
                owner_agent_id=proposal.owner_agent_id,
                namespace=proposal.namespace,
                data={"admission": record.as_dict()},
            )
        )
        self._store.flush()
        return _clone_proposal(self._resolve_proposal(proposal.proposal_id))

    def _resolve_proposal(self, proposal_id: str) -> MemoryProposal:
        proposals = self._project_proposals_by_id()
        proposal = proposals.get(proposal_id)
        if proposal is None:
            raise MemoryNotFound(f"memory proposal not found: {proposal_id}")
        return proposal

    def _project_proposals_by_id(self) -> dict[str, MemoryProposal]:
        return _project_proposals(self._store.list_events())

    def _project_admission_records(self) -> tuple[MemoryAdmissionRecord, ...]:
        return _project_admissions(self._store.list_events())


def _project_records(events: Iterable[MemoryEvent]) -> dict[str, MemoryRecord]:
    records: dict[str, MemoryRecord] = {}
    for event in events:
        if event.event_type in {"memory/proposed", "memory/admission"}:
            continue
        if event.event_type == "memory/remembered":
            raw = event.data.get("record")
            if not isinstance(raw, Mapping):
                raise MemoryCorruptionError("remembered event lacks record")
            record = MemoryRecord.from_dict(raw)
            if record.memory_id in records:
                raise MemoryCorruptionError(f"duplicate memory id: {record.memory_id}")
            if (
                record.memory_id != event.memory_id
                or record.owner_agent_id != event.owner_agent_id
                or record.namespace != event.namespace
            ):
                raise MemoryCorruptionError("remembered event identity mismatch")
            records[record.memory_id] = record
            continue
        if event.event_type == "memory/superseded":
            old_id = event.data.get("old_memory_id")
            new_id = event.data.get("new_memory_id")
            if not isinstance(old_id, str) or not isinstance(new_id, str):
                raise MemoryCorruptionError("superseded event lacks old/new ids")
            old = records.get(old_id)
            new = records.get(new_id)
            if old is None or new is None:
                raise MemoryCorruptionError("superseded event references missing memory")
            if not old.active:
                raise MemoryCorruptionError("superseded event targets inactive memory")
            if new.supersedes_memory_id != old.memory_id:
                raise MemoryCorruptionError("new memory does not declare supersession")
            if _would_create_supersede_cycle(records, old_id, new_id):
                raise MemoryCorruptionError("superseded event would create a cycle")
            records[old_id] = replace(
                old,
                active=False,
                lifecycle_state="SUPERSEDED",
                superseded_by_memory_id=new_id,
            )
            continue
        if event.event_type == "memory/forgotten":
            record = records.get(event.memory_id)
            if record is None:
                raise MemoryCorruptionError("forgotten event references missing memory")
            if record.active:
                records[event.memory_id] = replace(
                    record,
                    active=False,
                    lifecycle_state="FORGOTTEN",
                    forgotten_at=event.created_at,
                )
            continue
        if event.event_type == "memory/stale":
            record = records.get(event.memory_id)
            if record is None:
                raise MemoryCorruptionError("stale event references missing memory")
            if not record.active:
                raise MemoryCorruptionError("stale event targets inactive memory")
            reason = event.data.get("reason")
            evidence = event.data.get("evidence_provenance")
            observed_at = event.data.get("observed_at")
            if not isinstance(reason, str) or not reason:
                raise MemoryCorruptionError("stale event lacks reason")
            if not isinstance(evidence, Mapping):
                raise MemoryCorruptionError("stale event lacks evidence provenance")
            stale_at = event.created_at if observed_at is None else observed_at
            records[event.memory_id] = replace(
                record,
                active=False,
                lifecycle_state="STALE",
                stale_at=stale_at,
                stale_reason=reason,
                stale_provenance=MemoryProvenance.from_dict(evidence),
            )
            continue
        if event.event_type == "memory/conflict":
            raw_ids = event.data.get("memory_ids")
            group_id = event.data.get("conflict_group_id")
            reason = event.data.get("reason")
            evidence = event.data.get("evidence_provenance")
            if not isinstance(raw_ids, list) or len(raw_ids) < 2:
                raise MemoryCorruptionError("conflict event lacks memory_ids")
            if not all(isinstance(item, str) and item for item in raw_ids):
                raise MemoryCorruptionError("conflict memory_ids must be strings")
            if not isinstance(group_id, str) or not group_id:
                raise MemoryCorruptionError("conflict event lacks conflict_group_id")
            if not isinstance(reason, str) or not reason:
                raise MemoryCorruptionError("conflict event lacks reason")
            if not isinstance(evidence, Mapping):
                raise MemoryCorruptionError("conflict event lacks evidence provenance")
            ids = tuple(raw_ids)
            present = [records.get(memory_id) for memory_id in ids]
            if any(record is None for record in present):
                raise MemoryCorruptionError("conflict event references missing memory")
            assert all(record is not None for record in present)
            owner_namespace = {
                (record.owner_agent_id, record.namespace)
                for record in present
                if record is not None
            }
            if len(owner_namespace) != 1:
                raise MemoryCorruptionError("conflict relation crosses owner namespace")
            provenance = MemoryProvenance.from_dict(evidence)
            for memory_id in ids:
                record = records[memory_id]
                merged_conflicts = tuple(
                    sorted(
                        {
                            *record.conflicts_with_memory_ids,
                            *(other_id for other_id in ids if other_id != memory_id),
                        }
                    )
                )
                records[memory_id] = replace(
                    record,
                    conflict_group_id=group_id,
                    conflicts_with_memory_ids=merged_conflicts,
                    conflict_reason=reason,
                    conflict_provenance=provenance,
                )
            continue
        raise MemoryCorruptionError(f"unsupported memory event: {event.event_type}")
    return records


def _project_proposals(events: Iterable[MemoryEvent]) -> dict[str, MemoryProposal]:
    proposals: dict[str, MemoryProposal] = {}
    for event in events:
        if event.event_type == "memory/proposed":
            raw = event.data.get("proposal")
            if not isinstance(raw, Mapping):
                raise MemoryCorruptionError("proposal event lacks proposal")
            proposal = MemoryProposal.from_dict(raw)
            if proposal.proposal_id in proposals:
                raise MemoryCorruptionError(f"duplicate memory proposal id: {proposal.proposal_id}")
            if (
                proposal.proposal_id != event.memory_id
                or proposal.owner_agent_id != event.owner_agent_id
                or proposal.namespace != event.namespace
            ):
                raise MemoryCorruptionError("proposal event identity mismatch")
            proposals[proposal.proposal_id] = proposal
            continue
        if event.event_type == "memory/admission":
            raw = event.data.get("admission")
            if not isinstance(raw, Mapping):
                raise MemoryCorruptionError("admission event lacks admission")
            admission = MemoryAdmissionRecord.from_dict(raw)
            proposal = proposals.get(admission.proposal_id)
            if proposal is None:
                raise MemoryCorruptionError("admission event references missing proposal")
            if proposal.admission_state == "ADMITTED":
                raise MemoryCorruptionError("admitted proposal cannot receive another decision")
            if proposal.admission_state == "REJECTED":
                raise MemoryCorruptionError("rejected proposal cannot receive another decision")
            if admission.decision == "ADMIT":
                if admission.resulting_memory_id is None:
                    raise MemoryCorruptionError("ADMIT decision lacks resulting memory id")
                proposals[proposal.proposal_id] = replace(
                    proposal,
                    admission_state="ADMITTED",
                    admitted_memory_id=admission.resulting_memory_id,
                    latest_decision_id=admission.decision_id,
                )
                continue
            if admission.decision == "QUARANTINE":
                proposals[proposal.proposal_id] = replace(
                    proposal,
                    admission_state="QUARANTINED",
                    latest_decision_id=admission.decision_id,
                )
                continue
            if admission.decision == "REJECT":
                proposals[proposal.proposal_id] = replace(
                    proposal,
                    admission_state="REJECTED",
                    latest_decision_id=admission.decision_id,
                )
                continue
        continue
    return proposals


def _project_admissions(events: Iterable[MemoryEvent]) -> tuple[MemoryAdmissionRecord, ...]:
    records: list[MemoryAdmissionRecord] = []
    seen: set[str] = set()
    for event in events:
        if event.event_type != "memory/admission":
            continue
        raw = event.data.get("admission")
        if not isinstance(raw, Mapping):
            raise MemoryCorruptionError("admission event lacks admission")
        record = MemoryAdmissionRecord.from_dict(raw)
        if record.decision_id in seen:
            raise MemoryCorruptionError(f"duplicate admission decision id: {record.decision_id}")
        seen.add(record.decision_id)
        records.append(record)
    return tuple(records)


def _build_index(records: Iterable[MemoryRecord]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for record in records:
        for token in _tokens(f"{record.namespace} {record.content}"):
            index.setdefault(token, set()).add(record.memory_id)
    return index


def _score(record: MemoryRecord, query: str, query_tokens: set[str]) -> int:
    haystack = f"{record.namespace} {record.content}".lower()
    score = 0
    for token in query_tokens:
        if token in haystack:
            score += 2
    if query and query.lower() in haystack:
        score += 3
    return score


def _tokens(value: str) -> set[str]:
    return {token for token in _split_words(value.lower()) if token}


def _split_words(value: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    for char in value:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current.append(char)
            continue
        if current:
            result.append("".join(current))
            current = []
    if current:
        result.append("".join(current))
    return result


def _new_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex}"


def _new_memory_event_id() -> str:
    return f"mev_{uuid.uuid4().hex}"


def _new_memory_proposal_id() -> str:
    return f"mpr_{uuid.uuid4().hex}"


def _new_memory_decision_id() -> str:
    return f"mad_{uuid.uuid4().hex}"


def _clone_record(record: MemoryRecord) -> MemoryRecord:
    return MemoryRecord.from_dict(record.as_dict())


def _clone_proposal(proposal: MemoryProposal) -> MemoryProposal:
    return MemoryProposal.from_dict(proposal.as_dict())


def _clone_admission(record: MemoryAdmissionRecord) -> MemoryAdmissionRecord:
    return MemoryAdmissionRecord.from_dict(record.as_dict())


def _has_untrusted_origin(provenance: MemoryProvenance) -> bool:
    return provenance.source_class in {"TOOL_DERIVED", "EXTERNAL_UNTRUSTED"}


def _visible_for_options(
    record: MemoryRecord,
    *,
    include_inactive: bool,
    include_stale: bool,
    include_superseded: bool,
    include_forgotten: bool,
) -> bool:
    if record.lifecycle_state == "ACTIVE":
        return True
    if include_inactive:
        return True
    if record.lifecycle_state == "STALE":
        return include_stale
    if record.lifecycle_state == "SUPERSEDED":
        return include_superseded
    if record.lifecycle_state == "FORGOTTEN":
        return include_forgotten
    return False


def _stable_conflict_group_id(memory_ids: Iterable[str]) -> str:
    return "conflict_" + "_".join(sorted(memory_ids))


def _would_create_supersede_cycle(
    records: Mapping[str, MemoryRecord],
    old_id: str,
    new_id: str,
) -> bool:
    cursor: str | None = old_id
    seen: set[str] = set()
    while cursor is not None:
        if cursor == new_id:
            return True
        if cursor in seen:
            return True
        seen.add(cursor)
        record = records.get(cursor)
        cursor = None if record is None else record.supersedes_memory_id
    return False
