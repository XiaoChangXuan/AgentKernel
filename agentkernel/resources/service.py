"""Kernel-owned Resource identity, authorization, validation, and metrics."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from ..events import EventType, SessionEvent
from .model import (
    ResourceHandle,
    ResourceKind,
    ResourceLimits,
    ResourceMetadata,
    ResourceMetrics,
    ResourceOwner,
    ResourceRead,
)
from .store import ResourceNotFound, ResourceStore, ResourceStoreError

if TYPE_CHECKING:
    from ..session import Session


class ResourceError(RuntimeError):
    pass


class ResourceInvalid(ResourceError):
    pass


class ResourceAccessDenied(ResourceError):
    pass


class ResourceUnknown(ResourceError):
    pass


ResourceIdFactory = Callable[[], str]
HandleIdFactory = Callable[[], str]
_URI = re.compile(r"^artifact://(res_[A-Za-z0-9]+)$")


class ResourceService:
    """Trusted boundary above a replaceable byte store."""

    def __init__(
        self,
        store: ResourceStore,
        *,
        limits: ResourceLimits | None = None,
        metrics: ResourceMetrics | None = None,
        resource_id_factory: ResourceIdFactory | None = None,
        handle_id_factory: HandleIdFactory | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self.limits = limits or ResourceLimits()
        self.metrics = metrics or ResourceMetrics()
        self._resource_id_factory = resource_id_factory or _new_resource_id
        self._handle_id_factory = handle_id_factory or _new_handle_id
        self._clock = clock

    def create_artifact(
        self,
        data: bytes,
        *,
        owner: ResourceOwner,
        media_type: str,
        encoding: str,
        source_tool_name: str,
        source_tool_call_id: str,
        source_operation_id: str,
    ) -> ResourceHandle:
        if not isinstance(data, bytes):
            raise TypeError("resource data must be bytes")
        if len(data) > self.limits.max_resource_bytes:
            raise ResourceInvalid(
                f"resource exceeds {self.limits.max_resource_bytes} byte limit"
            )
        if not media_type or not encoding:
            raise ResourceInvalid("media_type and encoding must not be empty")
        resource_id = self._validated_identity(
            self._resource_id_factory(), "res_", "resource_id"
        )
        handle_id = self._validated_identity(
            self._handle_id_factory(), "hdl_", "handle_id"
        )
        metadata = ResourceMetadata(
            format_version=1,
            resource_id=resource_id,
            handle_id=handle_id,
            kind=ResourceKind.ARTIFACT,
            size_bytes=len(data),
            media_type=media_type,
            encoding=encoding,
            sha256=hashlib.sha256(data).hexdigest(),
            owner=owner,
            created_at=self._clock(),
            source_tool_name=source_tool_name,
            source_tool_call_id=source_tool_call_id,
            source_operation_id=source_operation_id,
        )
        try:
            self._store.commit(metadata, data)
        except ResourceStoreError as error:
            raise ResourceError("resource commit failed") from error
        self.metrics.resources_created += 1
        self.metrics.resource_bytes_stored += len(data)
        return metadata.to_handle()

    def stat(self, uri: str, *, owner: ResourceOwner) -> ResourceHandle:
        return self._resolve(uri, owner).to_handle()

    def read(
        self,
        uri: str,
        *,
        owner: ResourceOwner,
        offset: int = 0,
        limit: int | None = None,
    ) -> ResourceRead:
        metadata = self._resolve(uri, owner)
        selected_limit = self.limits.max_read_bytes if limit is None else limit
        self._validate_range(metadata, offset, selected_limit)
        try:
            data = self._store.read(metadata.resource_id, offset, selected_limit)
        except ResourceNotFound as error:
            raise ResourceUnknown("resource payload is unavailable") from error
        except ResourceStoreError as error:
            raise ResourceError("resource read failed") from error
        expected = min(selected_limit, metadata.size_bytes - offset)
        if len(data) != expected:
            raise ResourceError("resource payload ended before its durable size")
        self.metrics.resource_reads += 1
        self.metrics.resource_bytes_read += len(data)
        return ResourceRead(metadata.to_handle(), offset, data)

    def orphaned_resources(self, session: "Session") -> tuple[ResourceMetadata, ...]:
        """Identify committed owner resources absent from durable Tool Results."""

        referenced = _referenced_resource_ids(session.events)
        return tuple(
            metadata
            for metadata in self._store.list_metadata()
            if metadata.owner.session_id == session.session_id
            and metadata.resource_id not in referenced
        )

    def _resolve(self, uri: str, owner: ResourceOwner) -> ResourceMetadata:
        resource_id = self._parse_uri(uri)
        try:
            metadata = self._store.stat(resource_id)
        except ResourceNotFound as error:
            raise ResourceUnknown("resource handle is unknown") from error
        except ResourceStoreError as error:
            raise ResourceError("resource metadata is unavailable") from error
        if metadata.owner != owner:
            raise ResourceAccessDenied("resource owner does not match caller")
        return metadata

    def _validate_range(
        self, metadata: ResourceMetadata, offset: int, limit: int
    ) -> None:
        for name, value in (("offset", offset), ("limit", limit)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ResourceInvalid(f"{name} must be a non-negative integer")
        if limit < 1:
            raise ResourceInvalid("limit must be positive")
        if limit > self.limits.max_read_bytes:
            raise ResourceInvalid(
                f"limit exceeds {self.limits.max_read_bytes} byte read maximum"
            )
        if offset > metadata.size_bytes:
            raise ResourceInvalid("offset exceeds resource size")

    @staticmethod
    def _parse_uri(uri: str) -> str:
        if not isinstance(uri, str):
            raise ResourceInvalid("resource URI must be a string")
        matched = _URI.fullmatch(uri)
        if matched is None:
            raise ResourceInvalid("resource URI must be artifact://<resource_id>")
        return matched.group(1)

    @staticmethod
    def _validated_identity(value: str, prefix: str, name: str) -> str:
        if (
            not isinstance(value, str)
            or not value.startswith(prefix)
            or not value[len(prefix) :].isalnum()
        ):
            raise ResourceInvalid(f"{name} factory returned an invalid identity")
        return value


def _referenced_resource_ids(events: Iterable[SessionEvent]) -> set[str]:
    found: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, str):
            match = _URI.fullmatch(value)
            if match is not None:
                found.add(match.group(1))
        elif isinstance(value, Mapping):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for event in events:
        if event.type is EventType.TOOL_RESULT:
            visit(event.data)
    return found


def _new_resource_id() -> str:
    return f"res_{uuid.uuid4().hex}"


def _new_handle_id() -> str:
    return f"hdl_{uuid.uuid4().hex}"
