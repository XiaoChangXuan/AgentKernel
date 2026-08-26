"""Kernel-owned local IPC channels for Agent/Process communication."""

from __future__ import annotations

import copy
import json
import math
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .agent import AgentNotFound, AgentRegistry
from .events import EventType
from .process import ProcessState
from .protocol import JsonValue, is_json_value
from .scheduler import CooperativeScheduler, ProcessManager, ProcessNotFound
from .session import Session


class IPCError(RuntimeError):
    """Base class for Kernel IPC failures."""


class IPCChannelAlreadyExists(IPCError):
    """Raised when a channel id already exists with different metadata."""


class IPCChannelNotFound(IPCError):
    """Raised when an IPC channel is absent."""


class IPCParticipantError(IPCError):
    """Raised when sender or receiver identity does not match the channel."""


class IPCPayloadError(IPCError):
    """Raised when IPC data is not lossless JSON."""


class IPCMessageConflict(IPCError):
    """Raised when a stable message id is reused for different data."""


class IPCBackpressureError(IPCError):
    """Raised when a bounded channel cannot accept another live message."""

    def __init__(self, channel_id: str, process_id: str, reason: str) -> None:
        self.channel_id = channel_id
        self.process_id = process_id
        self.reason = reason
        super().__init__(
            f"channel {channel_id!r} is full for sender process "
            f"{process_id!r}: {reason}"
        )


class IPCStateTransitionError(IPCError):
    """Raised when a durable IPC state transition is invalid."""


class IPCPersistenceError(IPCError):
    """Base class for IPC persistence errors."""


class IPCCorruptionError(IPCPersistenceError):
    """Raised when durable IPC records cannot be replayed safely."""


class IPCMessageState(StrEnum):
    """Durable delivery state for one IPC message envelope."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    ACKED = "ACKED"


class IPCRecordType(StrEnum):
    """Append-only IPC persistence record vocabulary."""

    CHANNEL_CREATED = "ipc/channel_created"
    MESSAGE_SENT = "ipc/message_sent"
    MESSAGE_DELIVERED = "ipc/message_delivered"
    MESSAGE_ACKED = "ipc/message_acked"


@dataclass(frozen=True, slots=True)
class IPCRecord:
    """One append-only durable IPC persistence record."""

    seq: int
    type: IPCRecordType
    data: Mapping[str, JsonValue]
    time: float

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise ValueError("IPC record seq must be a positive integer")
        record_type = IPCRecordType(self.type)
        snapshot = copy.deepcopy(dict(self.data))
        if not is_json_value(snapshot):
            raise TypeError("IPC record data must be lossless JSON")
        if (
            isinstance(self.time, bool)
            or not isinstance(self.time, (int, float))
            or not math.isfinite(float(self.time))
        ):
            raise ValueError("IPC record time must be a finite number")
        object.__setattr__(self, "type", record_type)
        object.__setattr__(self, "data", snapshot)
        object.__setattr__(self, "time", float(self.time))

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a detached JSON-compatible record representation."""

        return {
            "seq": self.seq,
            "type": self.type.value,
            "data": copy.deepcopy(dict(self.data)),
            "time": self.time,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IPCRecord":
        """Decode one strict durable IPC record."""

        expected = {"seq", "type", "data", "time"}
        if set(value) != expected:
            raise ValueError("IPC record must contain exactly seq, type, data, time")
        record_type = value["type"]
        data = value["data"]
        if not isinstance(record_type, str):
            raise TypeError("IPC record type must be a string")
        if not isinstance(data, Mapping):
            raise TypeError("IPC record data must be an object")
        return cls(
            seq=value["seq"],  # type: ignore[arg-type]
            type=IPCRecordType(record_type),
            data=copy.deepcopy(dict(data)),
            time=value["time"],  # type: ignore[arg-type]
        )


@runtime_checkable
class IPCPersistence(Protocol):
    """Append-only durable store for IPC facts."""

    def append(
        self,
        record_type: IPCRecordType,
        data: Mapping[str, JsonValue],
    ) -> IPCRecord: ...

    def load(self) -> tuple[IPCRecord, ...]: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class InMemoryIPCPersistence:
    """Process-local IPC persistence useful for deterministic tests."""

    __slots__ = ("_closed", "_records")

    def __init__(self) -> None:
        self._records: list[IPCRecord] = []
        self._closed = False

    def append(
        self,
        record_type: IPCRecordType,
        data: Mapping[str, JsonValue],
    ) -> IPCRecord:
        self._require_open()
        record = IPCRecord(
            seq=len(self._records) + 1,
            type=record_type,
            data=copy.deepcopy(dict(data)),
            time=time.time(),
        )
        self._records.append(record)
        return copy.deepcopy(record)

    def load(self) -> tuple[IPCRecord, ...]:
        self._require_open()
        return tuple(copy.deepcopy(self._records))

    def flush(self) -> None:
        self._require_open()

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise IPCPersistenceError("IPC persistence is closed")


class JsonlIPCPersistence:
    """Inspectable append-only JSONL IPC persistence."""

    __slots__ = ("path", "_closed")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._closed = False

    def append(
        self,
        record_type: IPCRecordType,
        data: Mapping[str, JsonValue],
    ) -> IPCRecord:
        self._require_open()
        records = self.load()
        record = IPCRecord(
            seq=len(records) + 1,
            type=record_type,
            data=copy.deepcopy(dict(data)),
            time=time.time(),
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(_encode_record(record.as_dict()) + "\n")
                file.flush()
        except OSError as error:
            raise IPCPersistenceError(
                f"could not append IPC record to {self.path}: {error}"
            ) from error
        return copy.deepcopy(record)

    def load(self) -> tuple[IPCRecord, ...]:
        self._require_open()
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_bytes()
        except OSError as error:
            raise IPCPersistenceError(
                f"could not read IPC artifact {self.path}: {error}"
            ) from error
        if not raw:
            return ()
        records: list[IPCRecord] = []
        for index, physical in enumerate(raw.splitlines(), start=1):
            if not physical:
                raise IPCCorruptionError(
                    f"blank IPC JSONL record at line {index}: {self.path}"
                )
            try:
                decoded = json.loads(physical.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise IPCCorruptionError(
                    f"malformed IPC JSONL record at line {index}: {self.path}"
                ) from error
            if not isinstance(decoded, Mapping):
                raise IPCCorruptionError(
                    f"IPC JSONL record at line {index} must be an object"
                )
            try:
                record = IPCRecord.from_dict(decoded)
            except (TypeError, ValueError) as error:
                raise IPCCorruptionError(
                    f"invalid IPC record at line {index}: {error}"
                ) from error
            if record.seq != index:
                raise IPCCorruptionError(
                    f"IPC record seq must be contiguous; expected {index}, "
                    f"got {record.seq}"
                )
            records.append(record)
        return tuple(records)

    def flush(self) -> None:
        self._require_open()

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise IPCPersistenceError("IPC persistence is closed")


@dataclass(frozen=True, slots=True)
class IPCChannel:
    """Kernel-owned local point-to-point IPC channel."""

    channel_id: str
    sender_agent_id: str
    receiver_agent_id: str
    receiver_process_id: str | None = None
    max_messages: int = 128
    max_bytes: int = 1_048_576
    created_at: float = 0.0
    metadata: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        for name in ("channel_id", "sender_agent_id", "receiver_agent_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.receiver_process_id is not None and (
            not isinstance(self.receiver_process_id, str)
            or not self.receiver_process_id
        ):
            raise ValueError("receiver_process_id must be null or a non-empty string")
        for name in ("max_messages", "max_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.created_at, bool)
            or not isinstance(self.created_at, (int, float))
            or not math.isfinite(float(self.created_at))
        ):
            raise ValueError("created_at must be a finite number")
        metadata = {} if self.metadata is None else copy.deepcopy(dict(self.metadata))
        if not is_json_value(metadata):
            raise TypeError("channel metadata must be lossless JSON")
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "metadata", metadata)

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the durable channel creation payload."""

        return {
            "channel_id": self.channel_id,
            "sender_agent_id": self.sender_agent_id,
            "receiver_agent_id": self.receiver_agent_id,
            "receiver_process_id": self.receiver_process_id,
            "max_messages": self.max_messages,
            "max_bytes": self.max_bytes,
            "created_at": self.created_at,
            "metadata": copy.deepcopy(dict(self.metadata or {})),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IPCChannel":
        """Decode a durable channel creation payload."""

        expected = {
            "channel_id",
            "sender_agent_id",
            "receiver_agent_id",
            "receiver_process_id",
            "max_messages",
            "max_bytes",
            "created_at",
            "metadata",
        }
        if set(value) != expected:
            raise ValueError("IPC channel payload has unexpected fields")
        metadata = value["metadata"]
        if not isinstance(metadata, Mapping):
            raise TypeError("IPC channel metadata must be an object")
        return cls(
            channel_id=_required_string(value, "channel_id"),
            sender_agent_id=_required_string(value, "sender_agent_id"),
            receiver_agent_id=_required_string(value, "receiver_agent_id"),
            receiver_process_id=_optional_string(value, "receiver_process_id"),
            max_messages=_required_positive_int(value, "max_messages"),
            max_bytes=_required_positive_int(value, "max_bytes"),
            created_at=_required_finite_number(value, "created_at"),
            metadata=copy.deepcopy(dict(metadata)),
        )


@dataclass(frozen=True, slots=True)
class IPCMessageEnvelope:
    """Durable structured data envelope delivered over Kernel IPC."""

    message_id: str
    channel_id: str
    sender_agent_id: str
    sender_process_id: str
    receiver_agent_id: str
    receiver_process_id: str | None
    payload: JsonValue
    resource_refs: tuple[str, ...]
    sequence: int
    correlation_id: str
    created_at: float
    delivery_state: IPCMessageState = IPCMessageState.PENDING
    payload_bytes: int = 0
    delivery_attempts: int = 0
    last_delivered_at: float | None = None
    acked_at: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "message_id",
            "channel_id",
            "sender_agent_id",
            "sender_process_id",
            "receiver_agent_id",
            "correlation_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.receiver_process_id is not None and (
            not isinstance(self.receiver_process_id, str)
            or not self.receiver_process_id
        ):
            raise ValueError("receiver_process_id must be null or a non-empty string")
        payload = _canonical_json_value(self.payload)
        resource_refs = _canonical_resource_refs(self.resource_refs)
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("IPC message sequence must be positive")
        if (
            isinstance(self.created_at, bool)
            or not isinstance(self.created_at, (int, float))
            or not math.isfinite(float(self.created_at))
        ):
            raise ValueError("IPC message created_at must be finite")
        state = IPCMessageState(self.delivery_state)
        if (
            isinstance(self.payload_bytes, bool)
            or not isinstance(self.payload_bytes, int)
            or self.payload_bytes < 0
        ):
            raise ValueError("payload_bytes must be a non-negative integer")
        if (
            isinstance(self.delivery_attempts, bool)
            or not isinstance(self.delivery_attempts, int)
            or self.delivery_attempts < 0
        ):
            raise ValueError("delivery_attempts must be a non-negative integer")
        for name in ("last_delivered_at", "acked_at"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{name} must be null or a finite number")
        if state is IPCMessageState.PENDING:
            if self.delivery_attempts != 0:
                raise ValueError("PENDING messages cannot have delivery attempts")
            if self.last_delivered_at is not None or self.acked_at is not None:
                raise ValueError("PENDING messages cannot have delivery timestamps")
        if state is IPCMessageState.DELIVERED and self.delivery_attempts < 1:
            raise ValueError("DELIVERED messages require a delivery attempt")
        if state is IPCMessageState.ACKED:
            if self.delivery_attempts < 1:
                raise ValueError("ACKED messages require a delivery attempt")
            if self.acked_at is None:
                raise ValueError("ACKED messages require acked_at")
        size = self.payload_bytes or canonical_message_bytes(payload, resource_refs)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "resource_refs", resource_refs)
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "delivery_state", state)
        object.__setattr__(self, "payload_bytes", size)
        object.__setattr__(
            self,
            "last_delivered_at",
            None
            if self.last_delivered_at is None
            else float(self.last_delivered_at),
        )
        object.__setattr__(
            self,
            "acked_at",
            None if self.acked_at is None else float(self.acked_at),
        )

    def as_dict(self) -> dict[str, JsonValue]:
        """Return a detached durable envelope representation."""

        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "sender_agent_id": self.sender_agent_id,
            "sender_process_id": self.sender_process_id,
            "receiver_agent_id": self.receiver_agent_id,
            "receiver_process_id": self.receiver_process_id,
            "payload": copy.deepcopy(self.payload),
            "resource_refs": list(self.resource_refs),
            "sequence": self.sequence,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "delivery_state": self.delivery_state.value,
            "payload_bytes": self.payload_bytes,
            "delivery_attempts": self.delivery_attempts,
            "last_delivered_at": self.last_delivered_at,
            "acked_at": self.acked_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IPCMessageEnvelope":
        """Decode a durable message envelope."""

        expected = {
            "message_id",
            "channel_id",
            "sender_agent_id",
            "sender_process_id",
            "receiver_agent_id",
            "receiver_process_id",
            "payload",
            "resource_refs",
            "sequence",
            "correlation_id",
            "created_at",
            "delivery_state",
            "payload_bytes",
            "delivery_attempts",
            "last_delivered_at",
            "acked_at",
        }
        if set(value) != expected:
            raise ValueError("IPC message envelope has unexpected fields")
        refs = value["resource_refs"]
        if not isinstance(refs, list):
            raise TypeError("IPC message resource_refs must be a list")
        return cls(
            message_id=_required_string(value, "message_id"),
            channel_id=_required_string(value, "channel_id"),
            sender_agent_id=_required_string(value, "sender_agent_id"),
            sender_process_id=_required_string(value, "sender_process_id"),
            receiver_agent_id=_required_string(value, "receiver_agent_id"),
            receiver_process_id=_optional_string(value, "receiver_process_id"),
            payload=copy.deepcopy(value["payload"]),  # type: ignore[arg-type]
            resource_refs=tuple(copy.deepcopy(refs)),  # type: ignore[arg-type]
            sequence=_required_positive_int(value, "sequence"),
            correlation_id=_required_string(value, "correlation_id"),
            created_at=_required_finite_number(value, "created_at"),
            delivery_state=IPCMessageState(_required_string(value, "delivery_state")),
            payload_bytes=_required_non_negative_int(value, "payload_bytes"),
            delivery_attempts=_required_non_negative_int(
                value,
                "delivery_attempts",
            ),
            last_delivered_at=_optional_finite_number(value, "last_delivered_at"),
            acked_at=_optional_finite_number(value, "acked_at"),
        )


class KernelIPC:
    """Kernel-owned local IPC service with durable envelope replay."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        process_manager: ProcessManager,
        scheduler: CooperativeScheduler | None = None,
        persistence: IPCPersistence | None = None,
        sessions: Mapping[str, Session] | None = None,
        channel_id_factory: object | None = None,
        message_id_factory: object | None = None,
        time_fn: object | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._process_manager = process_manager
        self._scheduler = scheduler
        self._persistence = persistence or InMemoryIPCPersistence()
        self._channels: dict[str, IPCChannel] = {}
        self._messages: dict[str, IPCMessageEnvelope] = {}
        self._message_fingerprints: dict[str, str] = {}
        self._messages_by_channel: dict[str, list[str]] = {}
        self._next_sequence: dict[str, int] = {}
        self._blocked_by_channel: dict[str, set[str]] = {}
        self._sessions_by_agent: dict[str, Session] = {}
        self._channel_id_factory = channel_id_factory
        self._message_id_factory = message_id_factory
        self._time_fn = time_fn
        self._replay(self._persistence.load())
        for agent_id, session in (sessions or {}).items():
            self.bind_session(agent_id, session)

    @classmethod
    def reconstruct(
        cls,
        *,
        agent_registry: AgentRegistry,
        process_manager: ProcessManager,
        persistence: IPCPersistence,
        scheduler: CooperativeScheduler | None = None,
        sessions: Mapping[str, Session] | None = None,
    ) -> "KernelIPC":
        """Reconstruct live IPC runtime state from durable records."""

        return cls(
            agent_registry=agent_registry,
            process_manager=process_manager,
            scheduler=scheduler,
            persistence=persistence,
            sessions=sessions,
        )

    @property
    def persistence(self) -> IPCPersistence:
        """Return the durable IPC persistence boundary."""

        return self._persistence

    def bind_session(self, agent_id: str, session: Session) -> None:
        """Bind an Agent's local Session for IPC audit events."""

        control = self._agent_registry.get(agent_id)
        if session.session_id != control.session_id:
            raise IPCParticipantError(
                "bound IPC audit session must match the Agent primary session"
            )
        self._sessions_by_agent[agent_id] = session

    def create_channel(
        self,
        *,
        sender_agent_id: str,
        receiver_agent_id: str,
        channel_id: str | None = None,
        receiver_process_id: str | None = None,
        max_messages: int = 128,
        max_bytes: int = 1_048_576,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> IPCChannel:
        """Create a durable point-to-point channel between two Agent principals."""

        channel = IPCChannel(
            channel_id=channel_id or self._new_channel_id(),
            sender_agent_id=sender_agent_id,
            receiver_agent_id=receiver_agent_id,
            receiver_process_id=receiver_process_id,
            max_messages=max_messages,
            max_bytes=max_bytes,
            created_at=self._now(),
            metadata=metadata,
        )
        self._validate_channel_participants(channel, validate_process=True)
        existing = self._channels.get(channel.channel_id)
        if existing is not None:
            if existing.as_dict() != channel.as_dict():
                raise IPCChannelAlreadyExists(
                    f"conflicting channel id: {channel.channel_id}"
                )
            return copy.deepcopy(existing)
        self._persistence.append(IPCRecordType.CHANNEL_CREATED, channel.as_dict())
        self._apply_channel_created(channel)
        return copy.deepcopy(channel)

    def get_channel(self, channel_id: str) -> IPCChannel:
        """Return one registered IPC channel."""

        return copy.deepcopy(self._require_channel(channel_id))

    def list_channels(self) -> tuple[IPCChannel, ...]:
        """Return all known channels in deterministic creation order."""

        return tuple(copy.deepcopy(channel) for channel in self._channels.values())

    def send(
        self,
        *,
        channel_id: str,
        sender_process_id: str,
        payload: JsonValue,
        resource_refs: tuple[str, ...] | list[str] = (),
        message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> IPCMessageEnvelope:
        """Durably enqueue one JSON-compatible message from a sender Process."""

        channel = self._require_channel(channel_id)
        try:
            sender = self._process_manager.get(sender_process_id)
        except ProcessNotFound as error:
            raise IPCParticipantError(
                f"sender Process not found: {sender_process_id}"
            ) from error
        if sender.agent_id != channel.sender_agent_id:
            raise IPCParticipantError(
                "sender Process agent_id does not match channel sender Agent"
            )
        if sender.state is ProcessState.EXITED:
            raise IPCParticipantError("exited sender Process cannot send IPC")
        payload_snapshot = _canonical_json_value(payload)
        refs = _canonical_resource_refs(resource_refs)
        stable_id = message_id or self._new_message_id()
        if not stable_id:
            raise ValueError("message_id must not be empty")
        correlation = correlation_id or stable_id
        if not correlation:
            raise ValueError("correlation_id must not be empty")
        existing = self._messages.get(stable_id)
        if existing is not None:
            fingerprint = _sent_fingerprint(
                channel_id=channel.channel_id,
                sender_agent_id=sender.agent_id,
                sender_process_id=sender.process_id,
                receiver_agent_id=channel.receiver_agent_id,
                receiver_process_id=channel.receiver_process_id,
                payload=payload_snapshot,
                resource_refs=refs,
                correlation_id=correlation,
            )
            if self._message_fingerprints[stable_id] != fingerprint:
                raise IPCMessageConflict(
                    f"conflicting IPC message id: {stable_id}"
                )
            return copy.deepcopy(existing)
        payload_bytes = canonical_message_bytes(payload_snapshot, refs)
        if not self._has_capacity(channel, payload_bytes):
            reason = self._capacity_reason(channel, payload_bytes)
            self._block_sender(channel.channel_id, sender_process_id, reason)
            raise IPCBackpressureError(channel.channel_id, sender_process_id, reason)
        envelope = IPCMessageEnvelope(
            message_id=stable_id,
            channel_id=channel.channel_id,
            sender_agent_id=sender.agent_id,
            sender_process_id=sender.process_id,
            receiver_agent_id=channel.receiver_agent_id,
            receiver_process_id=channel.receiver_process_id,
            payload=payload_snapshot,
            resource_refs=refs,
            sequence=self._next_sequence[channel.channel_id],
            correlation_id=correlation,
            created_at=self._now(),
            delivery_state=IPCMessageState.PENDING,
            payload_bytes=payload_bytes,
        )
        self._persistence.append(IPCRecordType.MESSAGE_SENT, envelope.as_dict())
        self._apply_message_sent(envelope)
        self._append_ipc_audit(EventType.IPC_SEND, envelope)
        return copy.deepcopy(envelope)

    def receive(
        self,
        *,
        channel_id: str,
        receiver_agent_id: str,
        receiver_process_id: str | None = None,
    ) -> IPCMessageEnvelope | None:
        """Observe the next unacked message for the receiver."""

        channel = self._require_channel(channel_id)
        self._validate_receiver(
            channel,
            receiver_agent_id=receiver_agent_id,
            receiver_process_id=receiver_process_id,
        )
        message = self._next_deliverable(channel.channel_id)
        if message is None:
            return None
        delivered = replace(
            message,
            delivery_state=IPCMessageState.DELIVERED,
            delivery_attempts=message.delivery_attempts + 1,
            last_delivered_at=self._now(),
        )
        record = {
            "message_id": delivered.message_id,
            "channel_id": delivered.channel_id,
            "delivery_attempts": delivered.delivery_attempts,
            "delivered_at": delivered.last_delivered_at,
        }
        self._persistence.append(IPCRecordType.MESSAGE_DELIVERED, record)
        self._apply_message_delivered(record)
        current = self._messages[delivered.message_id]
        self._append_ipc_audit(EventType.IPC_RECEIVE, current)
        return copy.deepcopy(current)

    def ack(
        self,
        *,
        channel_id: str,
        message_id: str,
        receiver_agent_id: str,
        receiver_process_id: str | None = None,
    ) -> IPCMessageEnvelope:
        """Durably acknowledge one delivered message."""

        channel = self._require_channel(channel_id)
        self._validate_receiver(
            channel,
            receiver_agent_id=receiver_agent_id,
            receiver_process_id=receiver_process_id,
        )
        message = self._require_message(message_id)
        if message.channel_id != channel.channel_id:
            raise IPCParticipantError("message does not belong to channel")
        if message.receiver_agent_id != receiver_agent_id:
            raise IPCParticipantError("receiver Agent cannot ack this message")
        if message.delivery_state is IPCMessageState.ACKED:
            return copy.deepcopy(message)
        if message.delivery_state is not IPCMessageState.DELIVERED:
            raise IPCStateTransitionError("IPC ack requires DELIVERED state")
        record = {
            "message_id": message.message_id,
            "channel_id": message.channel_id,
            "acked_at": self._now(),
        }
        self._persistence.append(IPCRecordType.MESSAGE_ACKED, record)
        self._apply_message_acked(record)
        current = self._messages[message.message_id]
        self._append_ipc_audit(EventType.IPC_ACK, current)
        self._release_blocked_senders(channel.channel_id)
        return copy.deepcopy(current)

    def get_message(self, message_id: str) -> IPCMessageEnvelope:
        """Return one IPC message envelope by id."""

        return copy.deepcopy(self._require_message(message_id))

    def list_messages(
        self,
        channel_id: str | None = None,
    ) -> tuple[IPCMessageEnvelope, ...]:
        """Return durable message envelopes ordered by channel sequence."""

        if channel_id is None:
            ordered = sorted(
                self._messages.values(),
                key=lambda item: (item.channel_id, item.sequence),
            )
            return tuple(copy.deepcopy(item) for item in ordered)
        self._require_channel(channel_id)
        return tuple(
            copy.deepcopy(self._messages[message_id])
            for message_id in self._messages_by_channel.get(channel_id, ())
        )

    def live_occupancy(self, channel_id: str) -> dict[str, int]:
        """Return current unacked message and byte counts for one channel."""

        self._require_channel(channel_id)
        live = self._live_messages(channel_id)
        return {
            "messages": len(live),
            "bytes": sum(message.payload_bytes for message in live),
        }

    def _replay(self, records: tuple[IPCRecord, ...]) -> None:
        for expected_seq, record in enumerate(records, start=1):
            if record.seq != expected_seq:
                raise IPCCorruptionError(
                    "IPC records must be contiguous from 1; "
                    f"expected {expected_seq}, got {record.seq}"
                )
            try:
                if record.type is IPCRecordType.CHANNEL_CREATED:
                    self._apply_channel_created(
                        IPCChannel.from_dict(record.data),
                        replay=True,
                    )
                elif record.type is IPCRecordType.MESSAGE_SENT:
                    self._apply_message_sent(
                        IPCMessageEnvelope.from_dict(record.data),
                        replay=True,
                    )
                elif record.type is IPCRecordType.MESSAGE_DELIVERED:
                    self._apply_message_delivered(record.data, replay=True)
                elif record.type is IPCRecordType.MESSAGE_ACKED:
                    self._apply_message_acked(record.data, replay=True)
                else:
                    raise IPCCorruptionError(f"unknown IPC record type: {record.type}")
            except (IPCError, TypeError, ValueError) as error:
                raise IPCCorruptionError(
                    f"invalid IPC record {record.seq} ({record.type.value}): {error}"
                ) from error

    def _apply_channel_created(
        self,
        channel: IPCChannel,
        *,
        replay: bool = False,
    ) -> None:
        self._validate_channel_participants(channel, validate_process=not replay)
        existing = self._channels.get(channel.channel_id)
        if existing is not None:
            if existing.as_dict() != channel.as_dict():
                raise IPCChannelAlreadyExists(
                    f"conflicting channel id: {channel.channel_id}"
                )
            return
        self._channels[channel.channel_id] = channel
        self._messages_by_channel.setdefault(channel.channel_id, [])
        self._next_sequence.setdefault(channel.channel_id, 1)

    def _apply_message_sent(
        self,
        envelope: IPCMessageEnvelope,
        *,
        replay: bool = False,
    ) -> None:
        channel = self._require_channel(envelope.channel_id)
        if envelope.delivery_state is not IPCMessageState.PENDING:
            raise IPCStateTransitionError("message/sent must record PENDING state")
        if envelope.delivery_attempts != 0:
            raise IPCStateTransitionError("message/sent cannot include deliveries")
        if envelope.sender_agent_id != channel.sender_agent_id:
            raise IPCParticipantError("message sender does not match channel")
        if envelope.receiver_agent_id != channel.receiver_agent_id:
            raise IPCParticipantError("message receiver does not match channel")
        if envelope.receiver_process_id != channel.receiver_process_id:
            raise IPCParticipantError("message receiver process does not match channel")
        fingerprint = _sent_fingerprint_from_envelope(envelope)
        existing = self._messages.get(envelope.message_id)
        if existing is not None:
            if self._message_fingerprints[envelope.message_id] != fingerprint:
                raise IPCMessageConflict(
                    f"conflicting IPC message id: {envelope.message_id}"
                )
            return
        expected_sequence = self._next_sequence[channel.channel_id]
        if envelope.sequence != expected_sequence:
            raise IPCStateTransitionError(
                f"message sequence must be {expected_sequence}, "
                f"got {envelope.sequence}"
            )
        if replay and not self._has_capacity(channel, envelope.payload_bytes):
            raise IPCStateTransitionError("replayed message exceeds channel bounds")
        self._messages[envelope.message_id] = envelope
        self._message_fingerprints[envelope.message_id] = fingerprint
        self._messages_by_channel.setdefault(channel.channel_id, []).append(
            envelope.message_id
        )
        self._next_sequence[channel.channel_id] = expected_sequence + 1

    def _apply_message_delivered(
        self,
        data: Mapping[str, JsonValue],
        *,
        replay: bool = False,
    ) -> None:
        expected = {"message_id", "channel_id", "delivery_attempts", "delivered_at"}
        if set(data) != expected:
            raise IPCStateTransitionError("message/delivered has unexpected fields")
        message_id = _required_string(data, "message_id")
        channel_id = _required_string(data, "channel_id")
        attempts = _required_positive_int(data, "delivery_attempts")
        delivered_at = _required_finite_number(data, "delivered_at")
        message = self._require_message(message_id)
        if message.channel_id != channel_id:
            raise IPCParticipantError("message/delivered channel mismatch")
        if message.delivery_state is IPCMessageState.ACKED:
            raise IPCStateTransitionError("cannot deliver an ACKED IPC message")
        if attempts != message.delivery_attempts + 1:
            raise IPCStateTransitionError(
                "message/delivered delivery_attempts must advance by one"
            )
        self._messages[message_id] = replace(
            message,
            delivery_state=IPCMessageState.DELIVERED,
            delivery_attempts=attempts,
            last_delivered_at=delivered_at,
        )

    def _apply_message_acked(
        self,
        data: Mapping[str, JsonValue],
        *,
        replay: bool = False,
    ) -> None:
        expected = {"message_id", "channel_id", "acked_at"}
        if set(data) != expected:
            raise IPCStateTransitionError("message/acked has unexpected fields")
        message_id = _required_string(data, "message_id")
        channel_id = _required_string(data, "channel_id")
        acked_at = _required_finite_number(data, "acked_at")
        message = self._require_message(message_id)
        if message.channel_id != channel_id:
            raise IPCParticipantError("message/acked channel mismatch")
        if message.delivery_state is IPCMessageState.ACKED:
            return
        if message.delivery_state is not IPCMessageState.DELIVERED:
            raise IPCStateTransitionError("message/acked requires DELIVERED state")
        self._messages[message_id] = replace(
            message,
            delivery_state=IPCMessageState.ACKED,
            acked_at=acked_at,
        )

    def _validate_channel_participants(
        self,
        channel: IPCChannel,
        *,
        validate_process: bool,
    ) -> None:
        try:
            self._agent_registry.get(channel.sender_agent_id)
            self._agent_registry.get(channel.receiver_agent_id)
        except AgentNotFound as error:
            raise IPCParticipantError(str(error)) from error
        if channel.receiver_process_id is None or not validate_process:
            return
        try:
            receiver_process = self._process_manager.get(channel.receiver_process_id)
        except ProcessNotFound as error:
            raise IPCParticipantError(
                f"receiver Process not found: {channel.receiver_process_id}"
            ) from error
        if receiver_process.agent_id != channel.receiver_agent_id:
            raise IPCParticipantError(
                "receiver Process does not belong to receiver Agent"
            )

    def _validate_receiver(
        self,
        channel: IPCChannel,
        *,
        receiver_agent_id: str,
        receiver_process_id: str | None,
    ) -> None:
        if receiver_agent_id != channel.receiver_agent_id:
            raise IPCParticipantError("receiver Agent does not match channel")
        try:
            self._agent_registry.get(receiver_agent_id)
        except AgentNotFound as error:
            raise IPCParticipantError(str(error)) from error
        if channel.receiver_process_id is not None:
            if receiver_process_id != channel.receiver_process_id:
                raise IPCParticipantError(
                    "receiver Process does not match process-targeted channel"
                )
        if receiver_process_id is None:
            return
        try:
            receiver_process = self._process_manager.get(receiver_process_id)
        except ProcessNotFound as error:
            raise IPCParticipantError(
                f"receiver Process not found: {receiver_process_id}"
            ) from error
        if receiver_process.agent_id != receiver_agent_id:
            raise IPCParticipantError("receiver Process does not belong to Agent")

    def _append_ipc_audit(
        self,
        event_type: EventType,
        message: IPCMessageEnvelope,
    ) -> None:
        if event_type is EventType.IPC_SEND:
            agent_id = message.sender_agent_id
        else:
            agent_id = message.receiver_agent_id
        session = self._sessions_by_agent.get(agent_id)
        if session is None:
            return
        payload = _audit_payload(message)
        if event_type is EventType.IPC_RECEIVE:
            payload["delivery_attempts"] = message.delivery_attempts
        session.append(event_type, payload)

    def _next_deliverable(self, channel_id: str) -> IPCMessageEnvelope | None:
        for message_id in self._messages_by_channel.get(channel_id, ()):
            message = self._messages[message_id]
            if message.delivery_state is not IPCMessageState.ACKED:
                return message
        return None

    def _require_channel(self, channel_id: str) -> IPCChannel:
        try:
            return self._channels[channel_id]
        except KeyError as error:
            raise IPCChannelNotFound(f"IPC channel not found: {channel_id}") from error

    def _require_message(self, message_id: str) -> IPCMessageEnvelope:
        try:
            return self._messages[message_id]
        except KeyError as error:
            raise IPCStateTransitionError(
                f"IPC message not found: {message_id}"
            ) from error

    def _live_messages(self, channel_id: str) -> tuple[IPCMessageEnvelope, ...]:
        return tuple(
            self._messages[message_id]
            for message_id in self._messages_by_channel.get(channel_id, ())
            if self._messages[message_id].delivery_state is not IPCMessageState.ACKED
        )

    def _has_capacity(self, channel: IPCChannel, new_payload_bytes: int = 0) -> bool:
        live = self._live_messages(channel.channel_id)
        return (
            len(live) + 1 <= channel.max_messages
            and sum(message.payload_bytes for message in live) + new_payload_bytes
            <= channel.max_bytes
        )

    def _capacity_reason(
        self,
        channel: IPCChannel,
        new_payload_bytes: int,
    ) -> str:
        live = self._live_messages(channel.channel_id)
        if len(live) + 1 > channel.max_messages:
            return f"max_messages:{channel.max_messages}"
        live_bytes = sum(message.payload_bytes for message in live)
        if live_bytes + new_payload_bytes > channel.max_bytes:
            return f"max_bytes:{channel.max_bytes}"
        return "channel_full"

    def _block_sender(
        self,
        channel_id: str,
        process_id: str,
        reason: str,
    ) -> None:
        if self._scheduler is None:
            return
        try:
            process = self._scheduler.manager.get(process_id)
        except ProcessNotFound:
            return
        if process.state is ProcessState.RUNNING:
            self._scheduler.yield_process(
                process_id,
                ProcessState.BLOCKED,
                reason=f"ipc_backpressure:{channel_id}:{reason}",
            )
            self._blocked_by_channel.setdefault(channel_id, set()).add(process_id)

    def _release_blocked_senders(self, channel_id: str) -> None:
        if self._scheduler is None:
            return
        remaining: set[str] = set()
        for process_id in self._blocked_by_channel.get(channel_id, set()):
            try:
                process = self._scheduler.manager.get(process_id)
            except ProcessNotFound:
                continue
            if process.state is not ProcessState.BLOCKED:
                continue
            if not (process.blocked_reason or "").startswith(
                f"ipc_backpressure:{channel_id}:"
            ):
                remaining.add(process_id)
                continue
            self._scheduler.unblock(process_id)
        if remaining:
            self._blocked_by_channel[channel_id] = remaining
        else:
            self._blocked_by_channel.pop(channel_id, None)

    def _new_channel_id(self) -> str:
        if callable(self._channel_id_factory):
            value = self._channel_id_factory()
        else:
            value = f"ipc_channel_{uuid.uuid4().hex}"
        if not isinstance(value, str) or not value:
            raise ValueError("channel id factory must return a non-empty string")
        return value

    def _new_message_id(self) -> str:
        if callable(self._message_id_factory):
            value = self._message_id_factory()
        else:
            value = f"ipc_msg_{uuid.uuid4().hex}"
        if not isinstance(value, str) or not value:
            raise ValueError("message id factory must return a non-empty string")
        return value

    def _now(self) -> float:
        if callable(self._time_fn):
            value = self._time_fn()
        else:
            value = time.time()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("IPC time function must return a finite number")
        return float(value)


def canonical_message_bytes(
    payload: JsonValue,
    resource_refs: tuple[str, ...] | list[str] = (),
) -> int:
    """Return deterministic byte size for bounded IPC queue accounting."""

    value = {
        "payload": _canonical_json_value(payload),
        "resource_refs": list(_canonical_resource_refs(resource_refs)),
    }
    return len(_encode_record(value).encode("utf-8"))


def _audit_payload(message: IPCMessageEnvelope) -> dict[str, JsonValue]:
    return {
        "message_id": message.message_id,
        "channel_id": message.channel_id,
        "sender_agent_id": message.sender_agent_id,
        "sender_process_id": message.sender_process_id,
        "receiver_agent_id": message.receiver_agent_id,
        "receiver_process_id": message.receiver_process_id,
        "sequence": message.sequence,
        "correlation_id": message.correlation_id,
    }


def _sent_fingerprint_from_envelope(message: IPCMessageEnvelope) -> str:
    return _sent_fingerprint(
        channel_id=message.channel_id,
        sender_agent_id=message.sender_agent_id,
        sender_process_id=message.sender_process_id,
        receiver_agent_id=message.receiver_agent_id,
        receiver_process_id=message.receiver_process_id,
        payload=message.payload,
        resource_refs=message.resource_refs,
        correlation_id=message.correlation_id,
    )


def _sent_fingerprint(
    *,
    channel_id: str,
    sender_agent_id: str,
    sender_process_id: str,
    receiver_agent_id: str,
    receiver_process_id: str | None,
    payload: JsonValue,
    resource_refs: tuple[str, ...],
    correlation_id: str,
) -> str:
    value: dict[str, JsonValue] = {
        "channel_id": channel_id,
        "sender_agent_id": sender_agent_id,
        "sender_process_id": sender_process_id,
        "receiver_agent_id": receiver_agent_id,
        "receiver_process_id": receiver_process_id,
        "payload": copy.deepcopy(payload),
        "resource_refs": list(resource_refs),
        "correlation_id": correlation_id,
    }
    return _encode_record(value)


def _canonical_resource_refs(
    value: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise IPCPayloadError("resource_refs must be a list or tuple of strings")
    refs = tuple(value)
    if any(not isinstance(item, str) or not item for item in refs):
        raise IPCPayloadError("resource_refs must contain non-empty strings")
    return refs


def _canonical_json_value(value: object) -> JsonValue:
    snapshot = copy.deepcopy(value)
    if not is_json_value(snapshot):
        raise IPCPayloadError("IPC payload must be lossless JSON")
    return snapshot  # type: ignore[return-value]


def _encode_record(record: Mapping[str, JsonValue]) -> str:
    snapshot = copy.deepcopy(dict(record))
    if not is_json_value(snapshot):
        raise IPCPersistenceError("IPC record must be lossless JSON")
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be null or a non-empty string")
    return value


def _required_positive_int(data: Mapping[str, object], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _required_non_negative_int(data: Mapping[str, object], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _required_finite_number(data: Mapping[str, object], name: str) -> float:
    value = data.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _optional_finite_number(data: Mapping[str, object], name: str) -> float | None:
    value = data.get(name)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{name} must be null or a finite number")
    return float(value)
