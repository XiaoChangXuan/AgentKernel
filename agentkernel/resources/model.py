"""Resource identity, host metadata, projections, limits, and metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from ..protocol import JsonValue


class ResourceKind(StrEnum):
    ARTIFACT = "artifact"


@dataclass(frozen=True, slots=True)
class ResourceOwner:
    agent_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not self.agent_id or not self.session_id:
            raise ValueError("resource owner identities must not be empty")


@dataclass(frozen=True, slots=True)
class ResourceHandle:
    """Safe model-facing projection; it deliberately contains no store path."""

    handle_id: str
    uri: str
    kind: ResourceKind
    size_bytes: int
    media_type: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.handle_id.startswith("hdl_") or not self.handle_id[4:].isalnum():
            raise ValueError("invalid ResourceHandle handle_id")
        if (
            not self.uri.startswith("artifact://res_")
            or not self.uri[len("artifact://res_") :].isalnum()
        ):
            raise ValueError("invalid ResourceHandle URI")
        if isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise ValueError("ResourceHandle size_bytes must be non-negative")
        if not self.media_type or not _is_sha256(self.sha256):
            raise ValueError("invalid ResourceHandle media type or checksum")

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "handle_id": self.handle_id,
            "uri": self.uri,
            "kind": self.kind.value,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ResourceMetadata:
    """Host-only durable facts used to resolve and authorize a handle."""

    format_version: int
    resource_id: str
    handle_id: str
    kind: ResourceKind
    size_bytes: int
    media_type: str
    encoding: str
    sha256: str
    owner: ResourceOwner
    created_at: float
    source_tool_name: str
    source_tool_call_id: str
    source_operation_id: str

    def __post_init__(self) -> None:
        if self.format_version != 1:
            raise ValueError("unsupported resource metadata format")
        if not self.resource_id.startswith("res_") or not self.resource_id[4:].isalnum():
            raise ValueError("invalid resource_id")
        if not self.handle_id.startswith("hdl_") or not self.handle_id[4:].isalnum():
            raise ValueError("invalid handle_id")
        if isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise ValueError("resource size_bytes must be non-negative")
        if not self.media_type or not self.encoding or not _is_sha256(self.sha256):
            raise ValueError("invalid resource media, encoding, or checksum")
        if not math.isfinite(self.created_at):
            raise ValueError("resource created_at must be finite")
        if not all(
            (self.source_tool_name, self.source_tool_call_id, self.source_operation_id)
        ):
            raise ValueError("resource source identities must not be empty")

    @property
    def uri(self) -> str:
        return f"artifact://{self.resource_id}"

    def to_handle(self) -> ResourceHandle:
        return ResourceHandle(
            handle_id=self.handle_id,
            uri=self.uri,
            kind=self.kind,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
            sha256=self.sha256,
        )

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "format_version": self.format_version,
            "resource_id": self.resource_id,
            "handle_id": self.handle_id,
            "kind": self.kind.value,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "encoding": self.encoding,
            "sha256": self.sha256,
            "owner": {
                "agent_id": self.owner.agent_id,
                "session_id": self.owner.session_id,
            },
            "created_at": self.created_at,
            "source_tool_name": self.source_tool_name,
            "source_tool_call_id": self.source_tool_call_id,
            "source_operation_id": self.source_operation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ResourceMetadata":
        raw_owner = value.get("owner")
        if not isinstance(raw_owner, Mapping):
            raise TypeError("resource metadata owner must be a mapping")
        return cls(
            format_version=int(value["format_version"]),
            resource_id=str(value["resource_id"]),
            handle_id=str(value["handle_id"]),
            kind=ResourceKind(str(value["kind"])),
            size_bytes=int(value["size_bytes"]),
            media_type=str(value["media_type"]),
            encoding=str(value["encoding"]),
            sha256=str(value["sha256"]),
            owner=ResourceOwner(
                agent_id=str(raw_owner["agent_id"]),
                session_id=str(raw_owner["session_id"]),
            ),
            created_at=float(value["created_at"]),
            source_tool_name=str(value["source_tool_name"]),
            source_tool_call_id=str(value["source_tool_call_id"]),
            source_operation_id=str(value["source_operation_id"]),
        )


@dataclass(frozen=True, slots=True)
class ResourceRead:
    handle: ResourceHandle
    offset: int
    data: bytes

    def __post_init__(self) -> None:
        if isinstance(self.offset, bool) or not isinstance(self.offset, int) or self.offset < 0:
            raise ValueError("ResourceRead offset must be a non-negative integer")
        if not isinstance(self.data, bytes):
            raise TypeError("ResourceRead data must be bytes")

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.data)

    @property
    def has_more(self) -> bool:
        return self.next_offset < self.handle.size_bytes


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    max_resource_bytes: int = 128 * 1024 * 1024
    max_read_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        for name in ("max_resource_bytes", "max_read_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class ResourceMetricsSnapshot:
    resources_created: int
    resource_bytes_stored: int
    resource_reads: int
    resource_bytes_read: int
    tool_results_externalized: int
    preview_bytes: int
    model_visible_bytes_saved: int


class ResourceMetrics:
    """Reconstructable process-local counters; never Session truth."""

    def __init__(self) -> None:
        self.resources_created = 0
        self.resource_bytes_stored = 0
        self.resource_reads = 0
        self.resource_bytes_read = 0
        self.tool_results_externalized = 0
        self.preview_bytes = 0
        self.model_visible_bytes_saved = 0

    def snapshot(self) -> ResourceMetricsSnapshot:
        return ResourceMetricsSnapshot(
            resources_created=self.resources_created,
            resource_bytes_stored=self.resource_bytes_stored,
            resource_reads=self.resource_reads,
            resource_bytes_read=self.resource_bytes_read,
            tool_results_externalized=self.tool_results_externalized,
            preview_bytes=self.preview_bytes,
            model_visible_bytes_saved=self.model_visible_bytes_saved,
        )


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
