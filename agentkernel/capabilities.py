"""Minimal Kernel-owned capability evaluation primitives."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from .protocol import JsonValue, is_json_value


TOOL_EXECUTE_ACTION = "tool.execute"
RESOURCE_READ_ACTION = "resource.read"
RESOURCE_STAT_ACTION = "resource.stat"
ARTIFACT_RESOURCE_SCOPE = "artifact://**"


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Positive authority granted to one agent subject."""

    subject: str
    action: str
    resource_scope: str
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subject or not self.action or not self.resource_scope:
            raise ValueError("capability grant fields must not be empty")
        object.__setattr__(
            self,
            "resource_scope",
            canonicalize_resource_scope(self.resource_scope),
        )
        object.__setattr__(
            self,
            "constraints",
            MappingProxyType(_canonical_json_mapping(self.constraints)),
        )


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Kernel authorization request for one proposed action."""

    agent_id: str
    action: str
    resource: str

    def __post_init__(self) -> None:
        if not self.agent_id or not self.action or not self.resource:
            raise ValueError("authorization request fields must not be empty")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Authorization result with the grant that satisfied the request."""

    allowed: bool
    reason: str
    matched_grant: CapabilityGrant | None = None


@dataclass(frozen=True, slots=True)
class DelegateCapabilityRequest:
    """Kernel request to derive a narrowed child Agent grant."""

    parent_agent_id: str
    child_agent_id: str
    action: str
    resource_scope: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    parent_grant_fingerprint: str | None = None
    delegation_id: str | None = None
    correlation_id: str | None = None
    expires_at: JsonValue = None
    max_depth: int | None = None

    def __post_init__(self) -> None:
        if not self.parent_agent_id or not self.child_agent_id:
            raise ValueError("delegation agent ids must not be empty")
        if self.parent_agent_id == self.child_agent_id:
            raise ValueError("delegation parent and child must differ")
        if not self.action or not self.resource_scope:
            raise ValueError("delegation action and resource_scope must not be empty")
        object.__setattr__(
            self,
            "resource_scope",
            canonicalize_resource_scope(self.resource_scope),
        )
        object.__setattr__(
            self,
            "constraints",
            MappingProxyType(_canonical_json_mapping(self.constraints)),
        )
        for name in ("parent_grant_fingerprint", "delegation_id", "correlation_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be None or a non-empty string")
        if self.max_depth is not None and (
            isinstance(self.max_depth, bool)
            or not isinstance(self.max_depth, int)
            or self.max_depth < 0
        ):
            raise ValueError("max_depth must be None or a non-negative integer")
        if not is_json_value(self.expires_at):
            raise TypeError("expires_at must be lossless JSON")


@dataclass(frozen=True, slots=True)
class DelegationProvenance:
    """Auditable lineage for one delegated child grant."""

    delegation_id: str
    parent_agent_id: str
    child_agent_id: str
    parent_grant_fingerprint: str
    action: str
    resource_scope: str
    constraints: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    depth: int = 1
    parent_delegation_id: str | None = None
    expires_at: JsonValue = None

    def __post_init__(self) -> None:
        for name in (
            "delegation_id",
            "parent_agent_id",
            "child_agent_id",
            "parent_grant_fingerprint",
            "action",
            "resource_scope",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.parent_agent_id == self.child_agent_id:
            raise ValueError("delegation parent and child must differ")
        object.__setattr__(
            self,
            "resource_scope",
            canonicalize_resource_scope(self.resource_scope),
        )
        object.__setattr__(
            self,
            "constraints",
            MappingProxyType(_canonical_json_mapping(self.constraints)),
        )
        if (
            isinstance(self.depth, bool)
            or not isinstance(self.depth, int)
            or self.depth < 1
        ):
            raise ValueError("delegation depth must be a positive integer")
        for name in ("correlation_id", "parent_delegation_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be None or a non-empty string")
        if not is_json_value(self.expires_at):
            raise TypeError("expires_at must be lossless JSON")

    def as_payload(self, grant: CapabilityGrant) -> dict[str, JsonValue]:
        """Return the durable payload for a capability/delegated event."""

        if grant.subject != self.child_agent_id:
            raise ValueError("delegation payload grant subject must be the child")
        if grant.action != self.action or grant.resource_scope != self.resource_scope:
            raise ValueError("delegation payload grant must match provenance")
        if _canonical_json_mapping(grant.constraints) != dict(self.constraints):
            raise ValueError("delegation payload grant constraints must match")
        constraints = _canonical_json_mapping(self.constraints)
        return {
            "delegation_id": self.delegation_id,
            "parent_agent_id": self.parent_agent_id,
            "child_agent_id": self.child_agent_id,
            "parent_grant_fingerprint": self.parent_grant_fingerprint,
            "action": self.action,
            "resource_scope": self.resource_scope,
            "constraints": constraints,
            "child_grant": capability_grant_payload(grant),
            "correlation_id": self.correlation_id or self.delegation_id,
            "delegation_depth": self.depth,
            "parent_delegation_id": self.parent_delegation_id,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "DelegationProvenance":
        """Decode the provenance portion of a capability/delegated event."""

        expected = {
            "delegation_id",
            "parent_agent_id",
            "child_agent_id",
            "parent_grant_fingerprint",
            "action",
            "resource_scope",
            "constraints",
            "child_grant",
            "correlation_id",
            "delegation_depth",
            "parent_delegation_id",
            "expires_at",
        }
        if set(payload) != expected:
            raise ValueError("capability/delegated payload has unexpected fields")
        constraints = payload["constraints"]
        if not isinstance(constraints, Mapping):
            raise TypeError("delegation constraints must be an object")
        depth = payload["delegation_depth"]
        if isinstance(depth, bool) or not isinstance(depth, int):
            raise TypeError("delegation_depth must be an integer")
        return cls(
            delegation_id=_required_payload_string(payload, "delegation_id"),
            parent_agent_id=_required_payload_string(payload, "parent_agent_id"),
            child_agent_id=_required_payload_string(payload, "child_agent_id"),
            parent_grant_fingerprint=_required_payload_string(
                payload,
                "parent_grant_fingerprint",
            ),
            action=_required_payload_string(payload, "action"),
            resource_scope=_required_payload_string(payload, "resource_scope"),
            constraints=copy.deepcopy(dict(constraints)),
            correlation_id=_optional_payload_string(payload, "correlation_id"),
            depth=depth,
            parent_delegation_id=_optional_payload_string(
                payload,
                "parent_delegation_id",
            ),
            expires_at=copy.deepcopy(payload["expires_at"]),
        )


@dataclass(frozen=True, slots=True)
class DelegationDecision:
    """Delegation validation result; normal denial is not an exception."""

    allowed: bool
    reason: str
    delegation_id: str | None = None
    delegated_grant: CapabilityGrant | None = None
    provenance: DelegationProvenance | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("delegation decision allowed must be a boolean")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("delegation decision reason must be non-empty")
        if self.allowed:
            if (
                self.delegation_id is None
                or self.delegated_grant is None
                or self.provenance is None
            ):
                raise ValueError(
                    "allowed delegation decisions require id, grant, and provenance"
                )
        elif self.delegated_grant is not None or self.provenance is not None:
            raise ValueError("denied delegation decisions cannot include authority")


class CapabilityEvaluator:
    """Evaluate exact actions and simple resource-scope matches."""

    def __init__(self, grants: Iterable[CapabilityGrant] = ()) -> None:
        self._grants = tuple(grants)

    @property
    def grants(self) -> tuple[CapabilityGrant, ...]:
        """Return the immutable grant set evaluated by this instance."""

        return self._grants

    @classmethod
    def from_legacy_capabilities(
        cls,
        *,
        agent_id: str,
        capabilities: Iterable[str],
    ) -> "CapabilityEvaluator":
        """Build compatibility grants from V0.1-V0.5 capability strings."""

        grants: list[CapabilityGrant] = []
        for capability in capabilities:
            grants.extend(_legacy_capability_grants(agent_id, capability))
        return cls(grants)

    @classmethod
    def from_agent_capabilities(
        cls,
        *,
        agent_id: str,
        capabilities: Iterable[str],
        capability_grants: Iterable[CapabilityGrant] = (),
    ) -> "CapabilityEvaluator":
        """Build one evaluator from legacy strings plus structured grants."""

        legacy_grants: list[CapabilityGrant] = []
        for capability in capabilities:
            legacy_grants.extend(_legacy_capability_grants(agent_id, capability))
        return cls((*legacy_grants, *tuple(capability_grants)))

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Authorize one request against positive grants."""

        for grant in self._grants:
            if grant.subject != request.agent_id:
                continue
            if grant.action != request.action:
                continue
            if scope_matches(grant.resource_scope, request.resource):
                return AuthorizationDecision(
                    allowed=True,
                    reason="allowed",
                    matched_grant=grant,
                )
        return AuthorizationDecision(
            allowed=False,
            reason="no_matching_grant",
        )

    def covering_grants(
        self,
        *,
        agent_id: str,
        action: str,
        resource_scope: str,
    ) -> tuple[CapabilityGrant, ...]:
        """Return grants whose authority covers a proposed delegated scope."""

        child_scope = canonicalize_resource_scope(resource_scope)
        return tuple(
            grant
            for grant in self._grants
            if grant.subject == agent_id
            and grant.action == action
            and is_scope_narrower_or_equal(child_scope, grant.resource_scope)
        )


class CapabilityDelegator:
    """Pure delegation validator that derives child CapabilityGrant values."""

    def delegate(
        self,
        request: DelegateCapabilityRequest,
        *,
        parent_grants: Iterable[CapabilityGrant],
        parent_bounding_grants: Iterable[CapabilityGrant] = (),
        parent_provenance_by_grant: Mapping[str, DelegationProvenance] | None = None,
    ) -> DelegationDecision:
        """Validate narrowing and produce a child grant without installing it."""

        parent_grants = tuple(parent_grants)
        parent_bounding_grants = tuple(parent_bounding_grants)
        parent_provenance_by_grant = parent_provenance_by_grant or {}
        candidates = [
            grant
            for grant in parent_grants
            if grant.subject == request.parent_agent_id
            and grant.action == request.action
            and is_scope_narrower_or_equal(request.resource_scope, grant.resource_scope)
            and constraints_narrower_or_equal(request.constraints, grant.constraints)
        ]
        if request.parent_grant_fingerprint is not None:
            candidates = [
                grant
                for grant in candidates
                if grant_fingerprint(grant) == request.parent_grant_fingerprint
            ]
        if not candidates:
            return DelegationDecision(False, "parent_authority_not_found")
        if parent_bounding_grants and not any(
            grant.subject == request.parent_agent_id
            and grant.action == request.action
            and is_scope_narrower_or_equal(request.resource_scope, grant.resource_scope)
            and constraints_narrower_or_equal(request.constraints, grant.constraints)
            for grant in parent_bounding_grants
        ):
            return DelegationDecision(False, "outside_parent_bounding_set")
        parent_grant = sorted(
            candidates,
            key=lambda grant: (
                -len(_canonical_scope(grant.resource_scope).segments),
                grant_fingerprint(grant),
            ),
        )[0]
        parent_fingerprint = grant_fingerprint(parent_grant)
        parent_provenance = parent_provenance_by_grant.get(parent_fingerprint)
        depth = 1 if parent_provenance is None else parent_provenance.depth + 1
        max_depth = request.max_depth
        if max_depth is not None and depth > max_depth:
            return DelegationDecision(False, "delegation_depth_exceeded")
        grant = CapabilityGrant(
            subject=request.child_agent_id,
            action=request.action,
            resource_scope=request.resource_scope,
            constraints=request.constraints,
        )
        delegation_id = request.delegation_id or stable_delegation_id(
            parent_agent_id=request.parent_agent_id,
            child_agent_id=request.child_agent_id,
            parent_grant_fingerprint=parent_fingerprint,
            action=request.action,
            resource_scope=request.resource_scope,
            constraints=request.constraints,
            correlation_id=request.correlation_id,
            expires_at=request.expires_at,
        )
        provenance = DelegationProvenance(
            delegation_id=delegation_id,
            parent_agent_id=request.parent_agent_id,
            child_agent_id=request.child_agent_id,
            parent_grant_fingerprint=parent_fingerprint,
            action=request.action,
            resource_scope=request.resource_scope,
            constraints=request.constraints,
            correlation_id=request.correlation_id or delegation_id,
            depth=depth,
            parent_delegation_id=(
                None if parent_provenance is None else parent_provenance.delegation_id
            ),
            expires_at=request.expires_at,
        )
        return DelegationDecision(
            allowed=True,
            reason="allowed",
            delegation_id=delegation_id,
            delegated_grant=grant,
            provenance=provenance,
        )


def legacy_tool_scope(required_capability: str) -> str:
    """Map a legacy required capability string onto the tool namespace."""

    if not required_capability:
        raise ValueError("required capability must not be empty")
    return f"tool://{required_capability}"


def legacy_tool_request(
    *,
    agent_id: str,
    required_capability: str,
) -> AuthorizationRequest:
    """Create a compatibility request for a legacy tool requirement."""

    return AuthorizationRequest(
        agent_id=agent_id,
        action=TOOL_EXECUTE_ACTION,
        resource=legacy_tool_scope(required_capability),
    )


def legacy_capability_grants(
    agent_id: str,
    capability: str,
) -> tuple[CapabilityGrant, ...]:
    """Map one legacy capability string to structured compatibility grants."""

    grants = [
        CapabilityGrant(
            subject=agent_id,
            action=TOOL_EXECUTE_ACTION,
            resource_scope=legacy_tool_scope(capability),
        )
    ]
    if capability == RESOURCE_READ_ACTION:
        grants.extend(
            (
                CapabilityGrant(
                    subject=agent_id,
                    action=RESOURCE_READ_ACTION,
                    resource_scope=ARTIFACT_RESOURCE_SCOPE,
                ),
                CapabilityGrant(
                    subject=agent_id,
                    action=RESOURCE_STAT_ACTION,
                    resource_scope=ARTIFACT_RESOURCE_SCOPE,
                ),
            )
        )
    elif capability == RESOURCE_STAT_ACTION:
        grants.append(
            CapabilityGrant(
                subject=agent_id,
                action=RESOURCE_STAT_ACTION,
                resource_scope=ARTIFACT_RESOURCE_SCOPE,
            )
        )
    return tuple(grants)


def _legacy_capability_grants(
    agent_id: str,
    capability: str,
) -> tuple[CapabilityGrant, ...]:
    return legacy_capability_grants(agent_id, capability)


def capability_grant_payload(grant: CapabilityGrant) -> dict[str, JsonValue]:
    """Return a canonical JSON payload for a structured grant."""

    return {
        "subject": grant.subject,
        "action": grant.action,
        "resource_scope": canonicalize_resource_scope(grant.resource_scope),
        "constraints": _canonical_json_mapping(grant.constraints),
    }


def capability_grant_from_payload(payload: Mapping[str, object]) -> CapabilityGrant:
    """Reconstruct a grant from a durable canonical JSON payload."""

    expected = {"subject", "action", "resource_scope", "constraints"}
    if set(payload) != expected:
        raise ValueError(
            "capability grant payload must contain exactly subject, action, "
            "resource_scope, constraints"
        )
    constraints = payload["constraints"]
    if not isinstance(constraints, Mapping):
        raise TypeError("capability grant constraints must be an object")
    return CapabilityGrant(
        subject=_required_payload_string(payload, "subject"),
        action=_required_payload_string(payload, "action"),
        resource_scope=_required_payload_string(payload, "resource_scope"),
        constraints=copy.deepcopy(dict(constraints)),
    )


def grant_fingerprint(grant: CapabilityGrant) -> str:
    """Return a stable SHA-256 fingerprint over a canonical grant payload."""

    payload = capability_grant_payload(grant)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def stable_delegation_id(
    *,
    parent_agent_id: str,
    child_agent_id: str,
    parent_grant_fingerprint: str,
    action: str,
    resource_scope: str,
    constraints: Mapping[str, Any],
    correlation_id: str | None = None,
    expires_at: JsonValue = None,
) -> str:
    """Derive an idempotent delegation id from canonical delegation identity."""

    payload: dict[str, JsonValue] = {
        "parent_agent_id": parent_agent_id,
        "child_agent_id": child_agent_id,
        "parent_grant_fingerprint": parent_grant_fingerprint,
        "action": action,
        "resource_scope": canonicalize_resource_scope(resource_scope),
        "constraints": _canonical_json_mapping(constraints),
        "correlation_id": correlation_id,
        "expires_at": copy.deepcopy(expires_at),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"delegation_{hashlib.sha256(encoded).hexdigest()[:32]}"


def scope_matches(grant_scope: str, requested_resource: str) -> bool:
    """Return whether one grant scope covers a requested resource URI."""

    try:
        return is_scope_narrower_or_equal(requested_resource, grant_scope)
    except ValueError:
        return False


def is_scope_narrower_or_equal(child_scope: str, parent_scope: str) -> bool:
    """Return whether child_scope is equal to or narrower than parent_scope."""

    child = _canonical_scope(child_scope)
    parent = _canonical_scope(parent_scope)
    if child.scheme != parent.scheme:
        return False
    if parent.wildcard and not parent.segments:
        return True
    if not parent.wildcard:
        return not child.wildcard and child.segments == parent.segments
    if len(child.segments) < len(parent.segments):
        return False
    return child.segments[: len(parent.segments)] == parent.segments


def canonicalize_resource_scope(scope: str) -> str:
    """Canonicalize the minimal URI scope grammar used by CapabilityGrant."""

    parsed = _canonical_scope(scope)
    if parsed.wildcard and not parsed.segments:
        return f"{parsed.scheme}://**"
    body = "/".join(parsed.segments)
    if parsed.wildcard:
        return f"{parsed.scheme}://{body}/**"
    return f"{parsed.scheme}://{body}"


def constraints_narrower_or_equal(
    child_constraints: Mapping[str, Any],
    parent_constraints: Mapping[str, Any],
) -> bool:
    """Validate equal-or-stricter child constraint semantics."""

    try:
        child = _canonical_json_mapping(child_constraints)
        parent = _canonical_json_mapping(parent_constraints)
    except (TypeError, ValueError):
        return False
    for key, parent_value in parent.items():
        if key not in child:
            return False
        child_value = child[key]
        if _is_known_upper_bound(key):
            if not _numeric_leq(child_value, parent_value):
                return False
            continue
        if _is_known_expiry(key):
            if not _expiry_leq(child_value, parent_value):
                return False
            continue
        if child_value != parent_value:
            return False
    for key, child_value in child.items():
        if key in parent:
            continue
        if _is_known_upper_bound(key):
            if not _valid_number(child_value):
                return False
            continue
        if _is_known_expiry(key):
            if not _valid_expiry(child_value):
                return False
            continue
        return False
    return True


@dataclass(frozen=True, slots=True)
class _CanonicalScope:
    scheme: str
    segments: tuple[str, ...]
    wildcard: bool


_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")


def _canonical_scope(scope: str) -> _CanonicalScope:
    if not isinstance(scope, str) or not scope:
        raise ValueError("resource scope must be a non-empty string")
    if "%" in scope:
        raise ValueError("encoded resource scopes are not accepted")
    parsed = urlsplit(scope)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("resource scope must be scheme://name")
    if not _SCHEME.fullmatch(parsed.scheme):
        raise ValueError("resource scope scheme is invalid")
    if parsed.query or parsed.fragment:
        raise ValueError("resource scope must not contain query or fragment")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc
    path = parsed.path
    if netloc == "**":
        if path not in {"", "/"}:
            raise ValueError("scheme-wide wildcard cannot have a path")
        return _CanonicalScope(scheme=scheme, segments=(), wildcard=True)
    if "**" in netloc:
        raise ValueError("wildcard must be a complete final path segment")
    segments = [netloc]
    wildcard = False
    if path:
        if not path.startswith("/"):
            raise ValueError("resource scope path must be absolute")
        raw_segments = path[1:].split("/")
        if any(segment == "" for segment in raw_segments):
            raise ValueError("resource scope must not contain duplicate/trailing slash")
        for index, segment in enumerate(raw_segments):
            if segment == "**":
                if index != len(raw_segments) - 1:
                    raise ValueError("wildcard must be the final path segment")
                wildcard = True
                break
            segments.append(segment)
    for segment in segments:
        if segment in {".", ".."}:
            raise ValueError("resource scope must not contain path traversal")
    return _CanonicalScope(scheme=scheme, segments=tuple(segments), wildcard=wildcard)


def _canonical_json_mapping(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError("constraints must be a mapping")
    canonical = _canonical_json_value(copy.deepcopy(dict(value)))
    if not isinstance(canonical, dict):
        raise TypeError("constraints must be a JSON object")
    return canonical


def _canonical_json_value(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError("constraint keys must be strings")
            result[key] = _canonical_json_value(value[key])
        return result
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("constraint floats must be finite")
        return value
    raise TypeError("constraints must be lossless JSON")


def _is_known_upper_bound(key: str) -> bool:
    return key == "max_bytes" or key.startswith("max_")


def _is_known_expiry(key: str) -> bool:
    return key in {"expiry", "expires_at", "expires_at_epoch", "lease_expires_at"}


def _numeric_leq(child: JsonValue, parent: JsonValue) -> bool:
    if not _valid_number(child) or not _valid_number(parent):
        return False
    assert isinstance(child, (int, float))
    assert isinstance(parent, (int, float))
    return child <= parent


def _valid_number(value: JsonValue) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _expiry_leq(child: JsonValue, parent: JsonValue) -> bool:
    if isinstance(child, str) and isinstance(parent, str):
        return child <= parent
    return _numeric_leq(child, parent)


def _valid_expiry(value: JsonValue) -> bool:
    return isinstance(value, str) or _valid_number(value)


def _required_payload_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_payload_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be null or a non-empty string")
    return value
