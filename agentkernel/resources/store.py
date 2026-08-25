"""Replaceable ResourceStore seam and durable local artifact driver."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

from .model import ResourceMetadata


class ResourceStoreError(RuntimeError):
    pass


class ResourceNotFound(ResourceStoreError):
    pass


class ResourceStore(Protocol):
    def commit(self, metadata: ResourceMetadata, data: bytes) -> None: ...
    def stat(self, resource_id: str) -> ResourceMetadata: ...
    def read(self, resource_id: str, offset: int, limit: int) -> bytes: ...
    def list_metadata(self) -> Iterator[ResourceMetadata]: ...


class LocalResourceStore:
    """Single-host store that atomically publishes one directory per resource."""

    METADATA_FILE = "metadata.json"
    PAYLOAD_FILE = "payload.bin"

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        before_commit: Callable[[ResourceMetadata], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._before_commit = before_commit

    def commit(self, metadata: ResourceMetadata, data: bytes) -> None:
        final = self._resource_dir(metadata.resource_id)
        if final.exists():
            raise ResourceStoreError("resource identity already exists")
        staging = self.root / f".tmp-{metadata.resource_id}-{uuid.uuid4().hex}"
        staging.mkdir(mode=0o700)
        try:
            self._durable_write(staging / self.PAYLOAD_FILE, data)
            encoded = json.dumps(
                metadata.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self._durable_write(staging / self.METADATA_FILE, encoded)
            if self._before_commit is not None:
                self._before_commit(metadata)
            os.replace(staging, final)
            self._sync_directory(self.root)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def stat(self, resource_id: str) -> ResourceMetadata:
        path = self._resource_dir(resource_id) / self.METADATA_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ResourceNotFound(f"resource not found: {resource_id}") from error
        except (OSError, ValueError, TypeError) as error:
            raise ResourceStoreError(f"invalid resource metadata: {resource_id}") from error
        if not isinstance(value, dict):
            raise ResourceStoreError(f"invalid resource metadata: {resource_id}")
        try:
            metadata = ResourceMetadata.from_dict(value)
        except (KeyError, TypeError, ValueError) as error:
            raise ResourceStoreError(
                f"invalid resource metadata: {resource_id}"
            ) from error
        if metadata.resource_id != resource_id:
            raise ResourceStoreError("resource metadata identity mismatch")
        try:
            payload_size = (
                self._resource_dir(resource_id) / self.PAYLOAD_FILE
            ).stat().st_size
        except OSError as error:
            raise ResourceStoreError(
                f"resource payload is unavailable: {resource_id}"
            ) from error
        if payload_size != metadata.size_bytes:
            raise ResourceStoreError("resource payload size mismatch")
        return metadata

    def read(self, resource_id: str, offset: int, limit: int) -> bytes:
        path = self._resource_dir(resource_id) / self.PAYLOAD_FILE
        try:
            with path.open("rb") as payload:
                payload.seek(offset)
                return payload.read(limit)
        except FileNotFoundError as error:
            raise ResourceNotFound(f"resource not found: {resource_id}") from error
        except OSError as error:
            raise ResourceStoreError(f"resource read failed: {resource_id}") from error

    def list_metadata(self) -> Iterator[ResourceMetadata]:
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name.startswith(".tmp-"):
                continue
            try:
                yield self.stat(child.name)
            except ResourceStoreError:
                continue

    def _resource_dir(self, resource_id: str) -> Path:
        if not resource_id.startswith("res_") or not resource_id[4:].isalnum():
            raise ResourceNotFound("invalid resource identity")
        return self.root / resource_id

    @staticmethod
    def _durable_write(path: Path, data: bytes) -> None:
        with path.open("xb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())

    @staticmethod
    def _sync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
