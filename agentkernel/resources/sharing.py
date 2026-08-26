"""Explicit cross-Agent Resource sharing grants."""

from __future__ import annotations

import copy
import math
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

from ..capabilities import RESOURCE_READ_ACTION, RESOURCE_STAT_ACTION
from ..events import EventType
from ..protocol import JsonValue, is_json_value
from .model import ResourceMetadata

if TYPE_CHECKING:
    from ..session import Session


SHAREABLE_RESOURCE_ACTIONS = frozenset(
    {
        RESOURCE_READ_ACTION,
        RESOURCE_STAT_ACTION,
    }
)


class AgentDirectory(Protocol):
    def contains(self, agent_id: str) -> bool: ...


class ResourceShareError(RuntimeError):
    """Base class for resource sharing failures."""


class ResourceShareConflict(ResourceShareError):
    """Raised when a stable share identity is reused for different facts."""


class ResourceShareCorruptionError(ResourceShareError):
    """Raised when durable resource/share facts cannot be replayed."""


@dataclass(frozen=True, slots=True)
class ResourceShareGrant:
    """Owner consent for one grantee Agent to access one exact Resource."""

    share_id: str
    resource_id: str
    owner_agent_id: str
    grantee_agent_id: str
    allowed_actions: tuple[str, ...]
    created_at: float
    correlation_id: str
    expires_at: JsonValue = None

    def __post_init__(self) -> None:
        _validate_share_id(self.share_id)
        _validate_resource_id(self.resource_id)
        _validate_agent_id(self.owner_agent_id, "owner_agent_id")
        _validate_agent_id(self.grantee_agent_id, "grantee_agent_id")
        if self.owner_agent_id == self.grantee_agent_id:
            raise ValueError("resource share owner and grantee must differ")
        actions = _canonical_actions(self.allowed_actions)
        unsupported = [action for action in actions if action not in SHAREABLE_RESOURCE_ACTIONS]
        if unsupported:
            raise ValueError(f"unsupported resource share action: {unsupported[0]}")
        if (
            isinstance(self.created_at, bool)
            or not isinstance(self.created_at, (int, float))
            or not math.isfinite(float(self.created_at))
        ):
            raise ValueError("resource share created_at must be finite")
        if not isinstance(self.correlation_id, str) or not self.correlation_id:
            raise ValueError("resource share correlation_id must be non-empty")
        if not is_json_value(self.expires_at):
            raise TypeError("resource share expires_at must be lossless JSON")
        object.__setattr__(self, "allowed_actions", actions)
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "expires_at", copy.deepcopy(self.expires_at))

    def as_payload(self) -> dict[str, JsonValue]:
        """Return the canonical durable resource/shared payload."""

        return {
            "share_id": self.share_id,
            "resource_id": self.resource_id,
            "owner_agent_id": self.owner_agent_id,
            "grantee_agent_id": self.grantee_agent_id,
            "allowed_actions": list(self.allowed_actions),
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
            "expires_at": copy.deepcopy(self.expires_at),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ResourceShareGrant":
        """Decode a durable resource/shared payload."""

        expected = {
            "share_id",
            "resource_id",
            "owner_agent_id",
            "grantee_agent_id",
            "allowed_actions",
            "created_at",
            "correlation_id",
            "expires_at",
        }
        if set(payload) != expected:
            raise ValueError("resource/shared payload has unexpected fields")
        actions = payload["allowed_actions"]
        if not isinstance(actions, list):
            raise TypeError("resource/shared allowed_actions must be a list")
        return cls(
            share_id=_required_string(payload, "share_id"),
            resource_id=_required_string(payload, "resource_id"),
            owner_agent_id=_required_string(payload, "owner_agent_id"),
            grantee_agent_id=_required_string(payload, "grantee_agent_id"),
            allowed_actions=tuple(copy.deepcopy(actions)),  # type: ignore[arg-type]
            created_at=payload["created_at"],  # type: ignore[arg-type]
            correlation_id=_required_string(payload, "correlation_id"),
            expires_at=copy.deepcopy(payload["expires_at"]),
        )


@dataclass(frozen=True, slots=True)
class ResourceShareRequest:
    """Kernel request to create one explicit resource share."""

    owner_agent_id: str
    grantee_agent_id: str
    resource_id: str
    allowed_actions: tuple[str, ...]
    share_id: str | None = None
    correlation_id: str | None = None
    expires_at: JsonValue = None

    def __post_init__(self) -> None:
        _validate_agent_id(self.owner_agent_id, "owner_agent_id")
        _validate_agent_id(self.grantee_agent_id, "grantee_agent_id")
        _validate_resource_id(self.resource_id)
        object.__setattr__(
            self,
            "allowed_actions",
            _canonical_actions(self.allowed_actions),
        )
        if self.share_id is not None:
            _validate_share_id(self.share_id)
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str) or not self.correlation_id
        ):
            raise ValueError("resource share correlation_id must be non-empty")
        if not is_json_value(self.expires_at):
            raise TypeError("resource share expires_at must be lossless JSON")


@dataclass(frozen=True, slots=True)
class ResourceShareDecision:
    """Result of a resource share creation attempt."""

    allowed: bool
    reason: str
    grant: ResourceShareGrant | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("resource share decision allowed must be a boolean")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("resource share decision reason must be non-empty")
        if self.allowed and self.grant is None:
            raise ValueError("allowed resource share decisions require a grant")
        if not self.allowed and self.grant is not None:
            raise ValueError("denied resource share decisions cannot include a grant")


@dataclass(frozen=True, slots=True)
class _ResourceShareFact:
    grant: ResourceShareGrant
    session_id: str


class ResourceShareRegistry:
    """Kernel-owned index of explicit ResourceShareGrant facts."""

    def __init__(
        self,
        *,
        agent_registry: AgentDirectory | None = None,
        clock: Callable[[], float] = time.time,
        share_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._agent_registry = agent_registry
        self._clock = clock
        self._share_id_factory = share_id_factory or _new_share_id
        self._shares: dict[str, ResourceShareGrant] = {}
        self._shares_by_resource: dict[str, list[str]] = {}
        self._shares_by_grantee: dict[str, list[str]] = {}

    def create_share(
        self,
        request: ResourceShareRequest,
        *,
        resource_metadata: ResourceMetadata,
        record_session: "Session" | None = None,
        record: bool = True,
    ) -> ResourceShareDecision:
        """Validate owner consent and install one exact resource share."""

        if request.owner_agent_id == request.grantee_agent_id:
            return ResourceShareDecision(False, "self_share_denied")
        unsupported = [
            action
            for action in request.allowed_actions
            if action not in SHAREABLE_RESOURCE_ACTIONS
        ]
        if unsupported:
            return ResourceShareDecision(False, "unsupported_action")
        if self._agent_registry is not None:
            if not self._agent_registry.contains(request.owner_agent_id):
                return ResourceShareDecision(False, "owner_agent_not_found")
            if not self._agent_registry.contains(request.grantee_agent_id):
                return ResourceShareDecision(False, "grantee_agent_not_found")
        if resource_metadata.resource_id != request.resource_id:
            return ResourceShareDecision(False, "resource_not_found")
        if resource_metadata.owner.agent_id != request.owner_agent_id:
            return ResourceShareDecision(False, "not_resource_owner")
        if record:
            if record_session is None:
                raise ResourceShareError(
                    "record_session is required when recording resource share"
                )
            if record_session.session_id != resource_metadata.owner.session_id:
                raise ResourceShareError(
                    "resource/shared must be written to the owner Session"
                )

        share_id = request.share_id or self._share_id_factory()
        _validate_share_id(share_id)
        existing = self._shares.get(share_id)
        if existing is not None:
            if _existing_share_matches_request(existing, request):
                return ResourceShareDecision(True, "already_exists", existing)
            raise ResourceShareConflict(f"conflicting resource share id: {share_id}")

        grant = ResourceShareGrant(
            share_id=share_id,
            resource_id=request.resource_id,
            owner_agent_id=request.owner_agent_id,
            grantee_agent_id=request.grantee_agent_id,
            allowed_actions=request.allowed_actions,
            created_at=self._clock(),
            correlation_id=request.correlation_id or share_id,
            expires_at=request.expires_at,
        )
        if record:
            assert record_session is not None
            record_session.append(EventType.RESOURCE_SHARED, grant.as_payload())
        self._install(grant)
        return ResourceShareDecision(True, "allowed", grant)

    def get_share(self, share_id: str) -> ResourceShareGrant:
        """Return one installed share by id."""

        try:
            return self._shares[share_id]
        except KeyError as error:
            raise ResourceShareError(f"resource share not found: {share_id}") from error

    def shares_for_resource(self, resource_id: str) -> tuple[ResourceShareGrant, ...]:
        """Return installed shares for one exact Resource in stable order."""

        _validate_resource_id(resource_id)
        return tuple(
            self._shares[share_id]
            for share_id in sorted(self._shares_by_resource.get(resource_id, ()))
        )

    def shares_for_grantee(self, agent_id: str) -> tuple[ResourceShareGrant, ...]:
        """Return installed shares for one grantee Agent in stable order."""

        _validate_agent_id(agent_id, "agent_id")
        return tuple(
            self._shares[share_id]
            for share_id in sorted(self._shares_by_grantee.get(agent_id, ()))
        )

    def is_shared_with(
        self,
        *,
        resource_id: str,
        grantee_agent_id: str,
        action: str,
        owner_agent_id: str | None = None,
    ) -> bool:
        """Return whether an active share permits this exact resource action."""

        if action not in SHAREABLE_RESOURCE_ACTIONS:
            return False
        _validate_resource_id(resource_id)
        _validate_agent_id(grantee_agent_id, "grantee_agent_id")
        if owner_agent_id is not None:
            _validate_agent_id(owner_agent_id, "owner_agent_id")
        for share in self.shares_for_resource(resource_id):
            if share.grantee_agent_id != grantee_agent_id:
                continue
            if owner_agent_id is not None and share.owner_agent_id != owner_agent_id:
                continue
            if action in share.allowed_actions:
                return True
        return False

    def replay_shares(
        self,
        sessions: Iterable["Session"],
        *,
        resource_lookup: Callable[[str], ResourceMetadata] | None = None,
    ) -> tuple[ResourceShareGrant, ...]:
        """Replay resource/shared facts from owner Sessions."""

        facts = _resource_share_facts_from_sessions(sessions)
        for grant in sorted(
            (fact.grant for fact in facts.values()),
            key=lambda item: (item.created_at, item.share_id),
        ):
            if self._agent_registry is not None:
                if not self._agent_registry.contains(grant.owner_agent_id):
                    raise ResourceShareCorruptionError(
                        "resource/shared references missing owner Agent: "
                        f"{grant.owner_agent_id}"
                    )
                if not self._agent_registry.contains(grant.grantee_agent_id):
                    raise ResourceShareCorruptionError(
                        "resource/shared references missing grantee Agent: "
                        f"{grant.grantee_agent_id}"
                    )
            if resource_lookup is not None:
                try:
                    metadata = resource_lookup(grant.resource_id)
                except Exception as error:  # pragma: no cover - wrapped for callers
                    raise ResourceShareCorruptionError(
                        "resource/shared references missing resource: "
                        f"{grant.resource_id}"
                    ) from error
                if metadata.owner.agent_id != grant.owner_agent_id:
                    raise ResourceShareCorruptionError(
                        "resource/shared owner does not match resource metadata"
                    )
                if facts[grant.share_id].session_id != metadata.owner.session_id:
                    raise ResourceShareCorruptionError(
                        "resource/shared fact was not stored in the owner Session"
                    )
            existing = self._shares.get(grant.share_id)
            if existing is not None:
                if existing != grant:
                    raise ResourceShareCorruptionError(
                        f"conflicting resource share id: {grant.share_id}"
                    )
                continue
            self._install(grant)
        return tuple(
            self._shares[share_id]
            for share_id in sorted(self._shares)
        )

    @classmethod
    def reconstruct(
        cls,
        sessions: Iterable["Session"],
        *,
        agent_registry: AgentDirectory | None = None,
        resource_lookup: Callable[[str], ResourceMetadata] | None = None,
        clock: Callable[[], float] = time.time,
        share_id_factory: Callable[[], str] | None = None,
    ) -> "ResourceShareRegistry":
        """Build a share registry from durable resource/shared facts."""

        registry = cls(
            agent_registry=agent_registry,
            clock=clock,
            share_id_factory=share_id_factory,
        )
        registry.replay_shares(sessions, resource_lookup=resource_lookup)
        return registry

    def _install(self, grant: ResourceShareGrant) -> None:
        self._shares[grant.share_id] = grant
        self._shares_by_resource.setdefault(grant.resource_id, []).append(
            grant.share_id
        )
        self._shares_by_grantee.setdefault(grant.grantee_agent_id, []).append(
            grant.share_id
        )


def _resource_share_facts_from_sessions(
    sessions: Iterable["Session"],
) -> dict[str, _ResourceShareFact]:
    facts: dict[str, _ResourceShareFact] = {}
    for session in sessions:
        for event in session.events:
            if event.type is not EventType.RESOURCE_SHARED:
                continue
            try:
                grant = ResourceShareGrant.from_payload(event.data)
            except (TypeError, ValueError) as error:
                raise ResourceShareCorruptionError(
                    f"invalid resource/shared event: {error}"
                ) from error
            if grant.as_payload() != dict(event.data):
                raise ResourceShareCorruptionError(
                    "resource/shared payload is not canonical"
                )
            existing = facts.get(grant.share_id)
            if existing is not None and existing.grant != grant:
                raise ResourceShareCorruptionError(
                    f"conflicting resource share id: {grant.share_id}"
                )
            if existing is not None and existing.session_id != session.session_id:
                raise ResourceShareCorruptionError(
                    "resource/shared share_id appears in multiple Sessions"
                )
            facts[grant.share_id] = _ResourceShareFact(
                grant=grant,
                session_id=session.session_id,
            )
    return facts


def _existing_share_matches_request(
    grant: ResourceShareGrant,
    request: ResourceShareRequest,
) -> bool:
    if grant.resource_id != request.resource_id:
        return False
    if grant.owner_agent_id != request.owner_agent_id:
        return False
    if grant.grantee_agent_id != request.grantee_agent_id:
        return False
    if grant.allowed_actions != request.allowed_actions:
        return False
    if grant.expires_at != request.expires_at:
        return False
    return request.correlation_id is None or grant.correlation_id == request.correlation_id


def _canonical_actions(actions: Iterable[str]) -> tuple[str, ...]:
    if isinstance(actions, str):
        raise TypeError("resource share actions must be an iterable of strings")
    values: list[str] = []
    for action in actions:
        if not isinstance(action, str) or not action:
            raise ValueError("resource share actions must be non-empty strings")
        values.append(action)
    if not values:
        raise ValueError("resource share actions must not be empty")
    return tuple(sorted(set(values)))


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_agent_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _validate_resource_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("res_")
        or not value[len("res_") :].isalnum()
    ):
        raise ValueError("resource_id must be res_<alnum>")


def _validate_share_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("share_")
        or not value[len("share_") :]
        or any(
            not (char.isalnum() or char in {"_", "-"})
            for char in value[len("share_") :]
        )
    ):
        raise ValueError("share_id must be share_<slug>")


def _new_share_id() -> str:
    return f"share_{uuid.uuid4().hex}"
