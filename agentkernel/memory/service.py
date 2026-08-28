"""Kernel-owned persistent memory service."""

from __future__ import annotations

import copy
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from ..capabilities import AuthorizationRequest, CapabilityEvaluator
from ..protocol import JsonValue
from .model import (
    MEMORY_FORGET_ACTION,
    MEMORY_READ_ACTION,
    MEMORY_WRITE_ACTION,
    MemoryAccessDenied,
    MemoryCorruptionError,
    MemoryEvent,
    MemoryNotFound,
    MemoryProvenance,
    MemoryRecord,
    memory_namespace_scope,
    memory_uri,
    parse_memory_uri,
)
from .store import InMemoryMemoryStore, MemoryStore

MemoryIdFactory = Callable[[], str]
MemoryEventIdFactory = Callable[[], str]


class MemoryService:
    """Authorize, persist, retrieve, supersede, and forget long-term memory."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        *,
        clock: Callable[[], float] = time.time,
        memory_id_factory: MemoryIdFactory | None = None,
        event_id_factory: MemoryEventIdFactory | None = None,
    ) -> None:
        self._store = store or InMemoryMemoryStore()
        self._clock = clock
        self._memory_id_factory = memory_id_factory or _new_memory_id
        self._event_id_factory = event_id_factory or _new_memory_event_id
        self._index: dict[str, set[str]] | None = None

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
            for record in self._project_by_id(include_inactive=include_inactive).values()
            if record.owner_agent_id == owner_agent_id
            and (namespace is None or record.namespace == namespace)
            and (include_inactive or record.active)
        ]
        records.sort(key=lambda item: (item.created_at, item.memory_id))
        return tuple(_clone_record(record) for record in records)

    def search(
        self,
        *,
        agent_id: str,
        owner_agent_id: str,
        namespace: str | None,
        query: str,
        limit: int,
        capability_evaluator: CapabilityEvaluator | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return deterministic lexical search results from active memories."""

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
            for record in self._project_by_id(include_inactive=False).values()
            if record.owner_agent_id == owner_agent_id
            and (namespace is None or record.namespace == namespace)
            and (indexed_ids is None or record.memory_id in indexed_ids)
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

        self._index = _build_index(self._project_by_id(include_inactive=False).values())

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

    def _project_by_id(self, *, include_inactive: bool) -> dict[str, MemoryRecord]:
        records = _project_records(self._store.list_events())
        if include_inactive:
            return records
        return {key: record for key, record in records.items() if record.active}

    def _ensure_index(self) -> None:
        if self._index is None:
            self.rebuild_index()


def _project_records(events: Iterable[MemoryEvent]) -> dict[str, MemoryRecord]:
    records: dict[str, MemoryRecord] = {}
    for event in events:
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
            records[old_id] = replace(old, active=False, superseded_by_memory_id=new_id)
            continue
        if event.event_type == "memory/forgotten":
            record = records.get(event.memory_id)
            if record is None:
                raise MemoryCorruptionError("forgotten event references missing memory")
            if record.active:
                records[event.memory_id] = replace(
                    record,
                    active=False,
                    forgotten_at=event.created_at,
                )
            continue
        raise MemoryCorruptionError(f"unsupported memory event: {event.event_type}")
    return records


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


def _clone_record(record: MemoryRecord) -> MemoryRecord:
    return MemoryRecord.from_dict(record.as_dict())
