"""Minimal Kernel-owned capability evaluation primitives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


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
            "constraints",
            MappingProxyType(dict(self.constraints)),
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


class CapabilityEvaluator:
    """Evaluate exact actions and simple resource-scope matches."""

    def __init__(self, grants: Iterable[CapabilityGrant] = ()) -> None:
        self._grants = tuple(grants)

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
            if _scope_matches(grant.resource_scope, request.resource):
                return AuthorizationDecision(
                    allowed=True,
                    reason="allowed",
                    matched_grant=grant,
                )
        return AuthorizationDecision(
            allowed=False,
            reason="no_matching_grant",
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


def _legacy_capability_grants(
    agent_id: str,
    capability: str,
) -> tuple[CapabilityGrant, ...]:
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


def _scope_matches(grant_scope: str, requested_resource: str) -> bool:
    if grant_scope == requested_resource:
        return True
    if not grant_scope.endswith("/**"):
        return False
    prefix = grant_scope[:-2]
    return requested_resource.startswith(prefix) or requested_resource == prefix[:-1]
