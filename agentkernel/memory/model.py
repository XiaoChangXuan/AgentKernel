"""Persistent memory data model and URI helpers."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from ..protocol import JsonValue, is_json_value

MEMORY_READ_ACTION = "memory.read"
MEMORY_WRITE_ACTION = "memory.write"
MEMORY_FORGET_ACTION = "memory.forget"
MEMORY_RESOURCE_SCOPE = "memory://**"

MemoryLifecycleState = Literal["ACTIVE", "SUPERSEDED", "FORGOTTEN", "STALE"]
MemoryEventType = Literal[
    "memory/remembered",
    "memory/superseded",
    "memory/forgotten",
    "memory/stale",
    "memory/conflict",
]

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class MemoryError(RuntimeError):
    """Base class for persistent memory boundary failures."""


class MemoryInvalid(MemoryError):
    """The caller supplied invalid memory data or an invalid memory URI."""


class MemoryNotFound(MemoryError):
    """The requested memory does not exist in the projected durable facts."""


class MemoryAccessDenied(MemoryError):
    """The principal lacks current capability for the requested memory action."""


class MemoryCorruptionError(MemoryError):
    """Durable memory facts cannot be projected into a coherent state."""


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    """Minimal provenance for one remembered proposition."""

    source: str
    source_session_id: str | None = None
    source_event_id: str | None = None
    source_agent_id: str | None = None
    source_tool_name: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _require_identityish(self.source, "provenance source")
        for name in (
            "source_session_id",
            "source_event_id",
            "source_agent_id",
            "source_tool_name",
            "note",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be None or a non-empty string")

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "source": self.source,
            "source_session_id": self.source_session_id,
            "source_event_id": self.source_event_id,
            "source_agent_id": self.source_agent_id,
            "source_tool_name": self.source_tool_name,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MemoryProvenance":
        expected = {
            "source",
            "source_session_id",
            "source_event_id",
            "source_agent_id",
            "source_tool_name",
            "note",
        }
        if set(value) != expected:
            raise ValueError("memory provenance has unexpected fields")
        return cls(
            source=_required_string(value, "source"),
            source_session_id=_optional_string(value, "source_session_id"),
            source_event_id=_optional_string(value, "source_event_id"),
            source_agent_id=_optional_string(value, "source_agent_id"),
            source_tool_name=_optional_string(value, "source_tool_name"),
            note=_optional_string(value, "note"),
        )


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Projected durable memory record.

    `content` is a remembered proposition, not a Kernel-certified real-world fact.
    Lifecycle fields are projection state derived from append-only memory events.
    """

    memory_id: str
    owner_agent_id: str
    namespace: str
    content: str
    created_at: float
    provenance: MemoryProvenance
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    supersedes_memory_id: str | None = None
    active: bool = True
    superseded_by_memory_id: str | None = None
    forgotten_at: float | None = None
    lifecycle_state: MemoryLifecycleState = "ACTIVE"
    stale_at: float | None = None
    stale_reason: str | None = None
    stale_provenance: MemoryProvenance | None = None
    conflict_group_id: str | None = None
    conflicts_with_memory_ids: tuple[str, ...] = ()
    conflict_reason: str | None = None
    conflict_provenance: MemoryProvenance | None = None

    def __post_init__(self) -> None:
        _require_identityish(self.memory_id, "memory_id")
        _require_identityish(self.owner_agent_id, "owner_agent_id")
        _require_identityish(self.namespace, "namespace")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("memory content must be non-empty text")
        if not isinstance(self.created_at, (int, float)) or isinstance(
            self.created_at, bool
        ):
            raise TypeError("created_at must be a finite number")
        if not math.isfinite(float(self.created_at)):
            raise ValueError("created_at must be finite")
        object.__setattr__(self, "created_at", float(self.created_at))
        metadata = copy.deepcopy(dict(self.metadata))
        if not is_json_value(metadata):
            raise TypeError("memory metadata must be lossless JSON")
        object.__setattr__(self, "metadata", MappingProxyType(metadata))
        if self.supersedes_memory_id is not None:
            _require_identityish(self.supersedes_memory_id, "supersedes_memory_id")
        if self.superseded_by_memory_id is not None:
            _require_identityish(self.superseded_by_memory_id, "superseded_by_memory_id")
        if self.forgotten_at is not None:
            if not isinstance(self.forgotten_at, (int, float)) or isinstance(
                self.forgotten_at, bool
            ):
                raise TypeError("forgotten_at must be None or a finite number")
            if not math.isfinite(float(self.forgotten_at)):
                raise ValueError("forgotten_at must be finite")
            object.__setattr__(self, "forgotten_at", float(self.forgotten_at))
        if self.lifecycle_state not in {"ACTIVE", "SUPERSEDED", "FORGOTTEN", "STALE"}:
            raise ValueError("unsupported memory lifecycle_state")
        if self.stale_at is not None:
            if not isinstance(self.stale_at, (int, float)) or isinstance(
                self.stale_at, bool
            ):
                raise TypeError("stale_at must be None or a finite number")
            if not math.isfinite(float(self.stale_at)):
                raise ValueError("stale_at must be finite")
            object.__setattr__(self, "stale_at", float(self.stale_at))
        if self.stale_reason is not None and (
            not isinstance(self.stale_reason, str) or not self.stale_reason
        ):
            raise ValueError("stale_reason must be None or a non-empty string")
        if self.conflict_group_id is not None:
            _require_identityish(self.conflict_group_id, "conflict_group_id")
        conflicts = tuple(self.conflicts_with_memory_ids)
        for conflict_id in conflicts:
            _require_identityish(conflict_id, "conflicts_with_memory_ids")
        if self.memory_id in conflicts:
            raise ValueError("memory cannot conflict with itself")
        if len(set(conflicts)) != len(conflicts):
            raise ValueError("conflicts_with_memory_ids must be unique")
        object.__setattr__(self, "conflicts_with_memory_ids", tuple(sorted(conflicts)))
        if self.conflict_reason is not None and (
            not isinstance(self.conflict_reason, str) or not self.conflict_reason
        ):
            raise ValueError("conflict_reason must be None or a non-empty string")
        if not isinstance(self.active, bool):
            raise TypeError("active must be a boolean")
        expected_active = self.lifecycle_state == "ACTIVE"
        if self.active is not expected_active:
            raise ValueError("active must match lifecycle_state")
        if self.active and (
            self.superseded_by_memory_id is not None or self.forgotten_at is not None
        ):
            raise ValueError("active memory cannot be superseded or forgotten")
        if self.lifecycle_state == "SUPERSEDED" and self.superseded_by_memory_id is None:
            raise ValueError("SUPERSEDED memory requires superseded_by_memory_id")
        if self.lifecycle_state == "FORGOTTEN" and self.forgotten_at is None:
            raise ValueError("FORGOTTEN memory requires forgotten_at")
        if self.lifecycle_state == "STALE" and (
            self.stale_at is None
            or self.stale_reason is None
            or self.stale_provenance is None
        ):
            raise ValueError("STALE memory requires stale metadata")

    @property
    def uri(self) -> str:
        return memory_uri(self.owner_agent_id, self.namespace, self.memory_id)

    @property
    def status(self) -> MemoryLifecycleState:
        return self.lifecycle_state

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "memory_id": self.memory_id,
            "owner_agent_id": self.owner_agent_id,
            "namespace": self.namespace,
            "content": self.content,
            "created_at": self.created_at,
            "provenance": self.provenance.as_dict(),
            "metadata": copy.deepcopy(dict(self.metadata)),
            "supersedes_memory_id": self.supersedes_memory_id,
            "active": self.active,
            "superseded_by_memory_id": self.superseded_by_memory_id,
            "forgotten_at": self.forgotten_at,
            "lifecycle_state": self.lifecycle_state,
            "stale_at": self.stale_at,
            "stale_reason": self.stale_reason,
            "stale_provenance": (
                None if self.stale_provenance is None else self.stale_provenance.as_dict()
            ),
            "conflict_group_id": self.conflict_group_id,
            "conflicts_with_memory_ids": list(self.conflicts_with_memory_ids),
            "conflict_reason": self.conflict_reason,
            "conflict_provenance": (
                None
                if self.conflict_provenance is None
                else self.conflict_provenance.as_dict()
            ),
            "uri": self.uri,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MemoryRecord":
        expected = {
            "memory_id",
            "owner_agent_id",
            "namespace",
            "content",
            "created_at",
            "provenance",
            "metadata",
            "supersedes_memory_id",
            "active",
            "superseded_by_memory_id",
            "forgotten_at",
            "lifecycle_state",
            "stale_at",
            "stale_reason",
            "stale_provenance",
            "conflict_group_id",
            "conflicts_with_memory_ids",
            "conflict_reason",
            "conflict_provenance",
            "uri",
        }
        legacy_expected = expected - {
            "lifecycle_state",
            "stale_at",
            "stale_reason",
            "stale_provenance",
            "conflict_group_id",
            "conflicts_with_memory_ids",
            "conflict_reason",
            "conflict_provenance",
        }
        fields = set(value)
        if fields != expected and fields != legacy_expected:
            raise ValueError("memory record has unexpected fields")
        provenance = value["provenance"]
        if not isinstance(provenance, Mapping):
            raise TypeError("memory provenance must be an object")
        metadata = value["metadata"]
        if not isinstance(metadata, Mapping):
            raise TypeError("memory metadata must be an object")
        lifecycle_state = value.get("lifecycle_state")
        if lifecycle_state is None:
            if value.get("forgotten_at") is not None:
                lifecycle_state = "FORGOTTEN"
            elif value.get("superseded_by_memory_id") is not None:
                lifecycle_state = "SUPERSEDED"
            else:
                lifecycle_state = "ACTIVE"
        stale_provenance = value.get("stale_provenance")
        if stale_provenance is not None and not isinstance(stale_provenance, Mapping):
            raise TypeError("stale_provenance must be null or an object")
        conflict_provenance = value.get("conflict_provenance")
        if conflict_provenance is not None and not isinstance(conflict_provenance, Mapping):
            raise TypeError("conflict_provenance must be null or an object")
        conflicts = value.get("conflicts_with_memory_ids", ())
        if not isinstance(conflicts, (list, tuple)):
            raise TypeError("conflicts_with_memory_ids must be a list")
        record = cls(
            memory_id=_required_string(value, "memory_id"),
            owner_agent_id=_required_string(value, "owner_agent_id"),
            namespace=_required_string(value, "namespace"),
            content=_required_string(value, "content"),
            created_at=_finite_number(value, "created_at"),
            provenance=MemoryProvenance.from_dict(provenance),
            metadata=copy.deepcopy(dict(metadata)),
            supersedes_memory_id=_optional_string(value, "supersedes_memory_id"),
            active=_required_bool(value, "active"),
            superseded_by_memory_id=_optional_string(value, "superseded_by_memory_id"),
            forgotten_at=_optional_finite_number(value, "forgotten_at"),
            lifecycle_state=_required_lifecycle_state(lifecycle_state),
            stale_at=_optional_finite_number(value, "stale_at"),
            stale_reason=_optional_string(value, "stale_reason"),
            stale_provenance=(
                None
                if stale_provenance is None
                else MemoryProvenance.from_dict(stale_provenance)
            ),
            conflict_group_id=_optional_string(value, "conflict_group_id"),
            conflicts_with_memory_ids=tuple(
                _required_sequence_string(item, "conflicts_with_memory_ids")
                for item in conflicts
            ),
            conflict_reason=_optional_string(value, "conflict_reason"),
            conflict_provenance=(
                None
                if conflict_provenance is None
                else MemoryProvenance.from_dict(conflict_provenance)
            ),
        )
        if value["uri"] != record.uri:
            raise ValueError("memory record URI does not match identity fields")
        return record


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    """Append-only durable fact from which MemoryRecord state is projected."""

    event_id: str
    event_type: MemoryEventType
    agent_id: str
    memory_id: str
    owner_agent_id: str
    namespace: str
    created_at: float
    data: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_identityish(self.event_id, "event_id")
        if self.event_type not in {
            "memory/remembered",
            "memory/superseded",
            "memory/forgotten",
            "memory/stale",
            "memory/conflict",
        }:
            raise ValueError("unsupported memory event_type")
        for name in ("agent_id", "memory_id", "owner_agent_id", "namespace"):
            _require_identityish(getattr(self, name), name)
        if not isinstance(self.created_at, (int, float)) or isinstance(
            self.created_at, bool
        ):
            raise TypeError("created_at must be a finite number")
        if not math.isfinite(float(self.created_at)):
            raise ValueError("created_at must be finite")
        object.__setattr__(self, "created_at", float(self.created_at))
        data = copy.deepcopy(dict(self.data))
        if not is_json_value(data):
            raise TypeError("memory event data must be lossless JSON")
        object.__setattr__(self, "data", MappingProxyType(data))

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "memory_id": self.memory_id,
            "owner_agent_id": self.owner_agent_id,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "data": copy.deepcopy(dict(self.data)),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MemoryEvent":
        expected = {
            "event_id",
            "event_type",
            "agent_id",
            "memory_id",
            "owner_agent_id",
            "namespace",
            "created_at",
            "data",
        }
        if set(value) != expected:
            raise ValueError("memory event has unexpected fields")
        data = value["data"]
        if not isinstance(data, Mapping):
            raise TypeError("memory event data must be an object")
        return cls(
            event_id=_required_string(value, "event_id"),
            event_type=_required_string(value, "event_type"),  # type: ignore[arg-type]
            agent_id=_required_string(value, "agent_id"),
            memory_id=_required_string(value, "memory_id"),
            owner_agent_id=_required_string(value, "owner_agent_id"),
            namespace=_required_string(value, "namespace"),
            created_at=_finite_number(value, "created_at"),
            data=copy.deepcopy(dict(data)),
        )


def memory_uri(owner_agent_id: str, namespace: str, memory_id: str) -> str:
    _require_identityish(owner_agent_id, "owner_agent_id")
    _require_identityish(namespace, "namespace")
    _require_identityish(memory_id, "memory_id")
    return f"memory://{owner_agent_id}/{namespace}/{memory_id}"


def memory_namespace_scope(owner_agent_id: str, namespace: str) -> str:
    _require_identityish(owner_agent_id, "owner_agent_id")
    _require_identityish(namespace, "namespace")
    return f"memory://{owner_agent_id}/{namespace}/**"


def parse_memory_uri(uri: str) -> tuple[str, str, str]:
    if not isinstance(uri, str):
        raise MemoryInvalid("memory URI must be a string")
    prefix = "memory://"
    if not uri.startswith(prefix):
        raise MemoryInvalid("memory URI must start with memory://")
    parts = uri[len(prefix) :].split("/")
    if len(parts) != 3:
        raise MemoryInvalid("memory URI must be memory://<agent>/<namespace>/<memory>")
    owner_agent_id, namespace, memory_id = parts
    return (
        _require_identityish(owner_agent_id, "owner_agent_id"),
        _require_identityish(namespace, "namespace"),
        _require_identityish(memory_id, "memory_id"),
    )


def _require_identityish(value: object, name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise ValueError(f"{name} must match {_IDENTITY.pattern}")
    if value in {".", ".."}:
        raise ValueError(f"{name} must not be path traversal")
    return value


def _required_string(value: Mapping[str, object], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{name} must be a non-empty string")
    return item


def _optional_string(value: Mapping[str, object], name: str) -> str | None:
    item = value.get(name)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{name} must be null or a non-empty string")
    return item


def _required_bool(value: Mapping[str, object], name: str) -> bool:
    item = value.get(name)
    if not isinstance(item, bool):
        raise TypeError(f"{name} must be a boolean")
    return item


def _finite_number(value: Mapping[str, object], name: str) -> float:
    item = value.get(name)
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(item)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_finite_number(value: Mapping[str, object], name: str) -> float | None:
    item = value.get(name)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise TypeError(f"{name} must be null or a finite number")
    result = float(item)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _required_lifecycle_state(value: object) -> MemoryLifecycleState:
    if value not in {"ACTIVE", "SUPERSEDED", "FORGOTTEN", "STALE"}:
        raise ValueError("lifecycle_state must be ACTIVE, SUPERSEDED, FORGOTTEN, or STALE")
    return value  # type: ignore[return-value]


def _required_sequence_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} entries must be non-empty strings")
    return value
