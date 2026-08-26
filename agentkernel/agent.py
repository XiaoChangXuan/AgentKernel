"""Agent process state, capability bounds, and turn budgets."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable, Mapping

from .capabilities import (
    CapabilityDelegator,
    CapabilityGrant,
    DelegateCapabilityRequest,
    DelegationDecision,
    DelegationProvenance,
    capability_grant_from_payload,
    grant_fingerprint,
    legacy_capability_grants,
)
from .events import EventType, SessionEvent
from .protocol import JsonValue
from .session import Session


class AgentState(StrEnum):
    """Lifecycle states reserved by the Agent process model."""

    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    EXITED = "EXITED"


@dataclass(frozen=True, slots=True)
class AgentBudget:
    """Hard per-turn and optional process runtime limits."""

    max_steps_per_turn: int = 8
    max_tool_calls_per_turn: int = 16
    max_token_usage: int | None = None
    max_model_cost: float | None = None
    max_total_tool_calls: int | None = None
    max_resource_reads: int | None = None
    max_resource_bytes: int | None = None
    max_wall_time_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_steps_per_turn", "max_tool_calls_per_turn"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be at least 1")
        for name in (
            "max_token_usage",
            "max_total_tool_calls",
            "max_resource_reads",
            "max_resource_bytes",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_model_cost", "max_wall_time_seconds"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative number")


class CapabilityBoundError(ValueError):
    """Raised when effective capabilities exceed the bounding set."""


class AgentRegistryError(RuntimeError):
    """Base class for Kernel-owned Agent registry failures."""


class AgentAlreadyExists(AgentRegistryError):
    """Raised when a registry mutation would replace an Agent identity."""


class AgentNotFound(AgentRegistryError):
    """Raised when a requested Agent identity is absent from the registry."""


class InvalidAgentParent(AgentRegistryError):
    """Raised when a parent/child relation violates Agent Tree rules."""


class AgentTreeError(AgentRegistryError):
    """Raised when the Agent Tree cannot satisfy an identity invariant."""


class AgentRegistryCorruptionError(AgentRegistryError):
    """Raised when durable Agent identity facts cannot be replayed safely."""


class CapabilityDelegationError(AgentRegistryError):
    """Raised when delegation cannot be durably installed."""


_ALLOWED_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.NEW: frozenset({AgentState.READY, AgentState.FAILED}),
    AgentState.READY: frozenset(
        {AgentState.RUNNING, AgentState.PAUSED, AgentState.EXITED}
    ),
    AgentState.RUNNING: frozenset(
        {
            AgentState.READY,
            AgentState.WAITING,
            AgentState.PAUSED,
            AgentState.FAILED,
            AgentState.EXITED,
        }
    ),
    AgentState.WAITING: frozenset(
        {AgentState.RUNNING, AgentState.PAUSED, AgentState.FAILED, AgentState.EXITED}
    ),
    AgentState.PAUSED: frozenset(
        {AgentState.READY, AgentState.FAILED, AgentState.EXITED}
    ),
    AgentState.FAILED: frozenset({AgentState.EXITED}),
    AgentState.EXITED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class AgentControlBlock:
    """Kernel-owned metadata for one Agent capability principal."""

    agent_id: str
    session_id: str
    state: AgentState
    parent_agent_id: str | None
    capabilities: frozenset[str]
    capability_bounding_set: frozenset[str]
    budget: AgentBudget
    capability_grants: tuple[CapabilityGrant, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(
            self,
            "capability_bounding_set",
            frozenset(self.capability_bounding_set),
        )
        object.__setattr__(self, "capability_grants", tuple(self.capability_grants))
        if not self.agent_id or not self.session_id:
            raise ValueError("agent_id and session_id must not be empty")
        if self.parent_agent_id is not None:
            if not isinstance(self.parent_agent_id, str) or not self.parent_agent_id:
                raise ValueError("parent_agent_id must be None or a non-empty string")
            if self.parent_agent_id == self.agent_id:
                raise ValueError("agent cannot be its own parent")
        if not self.capabilities <= self.capability_bounding_set:
            excess = sorted(self.capabilities - self.capability_bounding_set)
            raise CapabilityBoundError(
                f"effective capabilities exceed bounding set: {', '.join(excess)}"
            )
        for grant in self.capability_grants:
            if not isinstance(grant, CapabilityGrant):
                raise TypeError("capability_grants must contain CapabilityGrant values")
            if grant.subject != self.agent_id:
                raise CapabilityBoundError(
                    "capability grant subject must match agent_id"
                )

    def has_capability(self, capability: str) -> bool:
        """Check one exact effective capability."""

        return capability in self.capabilities

    def transition(self, target: AgentState) -> None:
        """Perform one valid kernel lifecycle transition."""

        if target is self.state:
            return
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise RuntimeError(f"invalid agent transition: {self.state} -> {target}")
        object.__setattr__(self, "state", target)


@dataclass(slots=True)
class Agent:
    """Live handle combining an AgentControlBlock with its session."""

    control: AgentControlBlock
    session: Session

    def __post_init__(self) -> None:
        if self.control.session_id != self.session.session_id:
            raise ValueError("agent control block and session ids must match")

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        session: Session,
        capabilities: Iterable[str] = (),
        capability_bounding_set: Iterable[str] | None = None,
        capability_grants: Iterable[CapabilityGrant] = (),
        budget: AgentBudget | None = None,
        parent_agent_id: str | None = None,
    ) -> "Agent":
        """Create a ready agent whose effective capabilities are bounded."""

        effective = frozenset(capabilities)
        bounding = (
            effective
            if capability_bounding_set is None
            else frozenset(capability_bounding_set)
        )
        control = AgentControlBlock(
            agent_id=agent_id,
            session_id=session.session_id,
            state=AgentState.NEW,
            parent_agent_id=parent_agent_id,
            capabilities=effective,
            capability_bounding_set=bounding,
            budget=budget or AgentBudget(),
            capability_grants=tuple(capability_grants),
        )
        control.transition(AgentState.READY)
        return cls(control=control, session=session)


@dataclass(frozen=True, slots=True)
class _AgentCreationFact:
    agent_id: str
    parent_agent_id: str | None
    session_id: str
    creation_id: str


@dataclass(frozen=True, slots=True)
class _CapabilityDelegationFact:
    grant: CapabilityGrant
    provenance: DelegationProvenance
    payload: dict[str, JsonValue]


class AgentRegistry:
    """Kernel-owned registry for Agent identity and parent-child relations."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentControlBlock] = {}
        self._children: dict[str, list[str]] = {}
        self._delegations: dict[str, _CapabilityDelegationFact] = {}
        self._delegation_by_child_grant: dict[str, str] = {}
        self._delegation_provenance_by_grant: dict[str, DelegationProvenance] = {}
        self._delegator = CapabilityDelegator()

    def create_root(
        self,
        *,
        agent_id: str,
        session: Session,
        capabilities: Iterable[str] = (),
        capability_bounding_set: Iterable[str] | None = None,
        capability_grants: Iterable[CapabilityGrant] = (),
        budget: AgentBudget | None = None,
        creation_id: str | None = None,
        record: bool = True,
    ) -> Agent:
        """Create and register a root Agent principal."""

        agent = Agent.create(
            agent_id=agent_id,
            session=session,
            capabilities=capabilities,
            capability_bounding_set=capability_bounding_set,
            capability_grants=capability_grants,
            budget=budget,
        )
        self._validate_root_candidate(agent.control)
        if record:
            self._append_agent_created(session, agent.control, creation_id)
        self._insert(agent.control)
        return agent

    def create_child(
        self,
        *,
        parent_agent_id: str,
        agent_id: str,
        session: Session,
        budget: AgentBudget | None = None,
        creation_id: str | None = None,
        record: bool = True,
        record_session: Session | None = None,
    ) -> Agent:
        """Create and register a deny-by-default child Agent principal."""

        self._require_parent(parent_agent_id)
        agent = Agent.create(
            agent_id=agent_id,
            session=session,
            capabilities=frozenset(),
            capability_bounding_set=frozenset(),
            capability_grants=(),
            budget=budget,
            parent_agent_id=parent_agent_id,
        )
        self._validate_child_candidate(agent.control, parent_agent_id)
        if record:
            self._append_agent_created(
                record_session or session,
                agent.control,
                creation_id,
            )
        self._insert(agent.control)
        return agent

    def register_root(self, agent: Agent | AgentControlBlock) -> AgentControlBlock:
        """Register an existing root Agent identity without writing Session facts."""

        control = _agent_control(agent)
        self._validate_root_candidate(control)
        self._insert(control)
        return control

    def register_child(
        self,
        agent: Agent | AgentControlBlock,
        *,
        parent_agent_id: str | None = None,
    ) -> AgentControlBlock:
        """Register an existing child Agent identity without granting authority."""

        control = _agent_control(agent)
        expected_parent = parent_agent_id or control.parent_agent_id
        if expected_parent is None:
            raise InvalidAgentParent("child agent requires a parent_agent_id")
        self._validate_child_candidate(control, expected_parent)
        self._insert(control)
        return control

    def record_agent_created(
        self,
        session: Session,
        agent: Agent | AgentControlBlock,
        *,
        creation_id: str | None = None,
    ) -> SessionEvent:
        """Append one durable Agent identity creation fact."""

        control = _agent_control(agent)
        registered = self.get(control.agent_id)
        if registered.parent_agent_id != control.parent_agent_id:
            raise InvalidAgentParent("registered parent does not match agent")
        if registered.session_id != control.session_id:
            raise AgentTreeError("registered session does not match agent")
        return self._append_agent_created(session, control, creation_id)

    def get(self, agent_id: str) -> AgentControlBlock:
        """Return one registered AgentControlBlock by Agent id."""

        try:
            return self._agents[agent_id]
        except KeyError as error:
            raise AgentNotFound(f"agent not found: {agent_id}") from error

    def contains(self, agent_id: str) -> bool:
        """Return whether the Agent id exists in the registry."""

        return agent_id in self._agents

    def parent_of(self, agent_id: str) -> str | None:
        """Return the parent Agent id, or None for a root Agent."""

        return self.get(agent_id).parent_agent_id

    def children_of(self, agent_id: str) -> tuple[str, ...]:
        """Return direct child Agent ids in creation order."""

        self.get(agent_id)
        return tuple(self._children.get(agent_id, ()))

    def root_of(self, agent_id: str) -> str:
        """Return the root Agent id for the Agent's tree."""

        return self.lineage(agent_id)[0]

    def lineage(self, agent_id: str) -> tuple[str, ...]:
        """Return Agent ancestry ordered from root to the requested Agent."""

        self.get(agent_id)
        reversed_lineage: list[str] = []
        current: str | None = agent_id
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise AgentTreeError(f"agent tree cycle detected at {current}")
            seen.add(current)
            control = self.get(current)
            reversed_lineage.append(current)
            current = control.parent_agent_id
        return tuple(reversed(reversed_lineage))

    def install_capability_grants(
        self,
        agent_id: str,
        grants: Iterable[CapabilityGrant],
    ) -> AgentControlBlock:
        """Install Host-provided current grants without writing durable facts."""

        control = self.get(agent_id)
        additions = tuple(grants)
        for grant in additions:
            if not isinstance(grant, CapabilityGrant):
                raise TypeError("grants must contain CapabilityGrant values")
            if grant.subject != agent_id:
                raise CapabilityBoundError(
                    "capability grant subject must match agent_id"
                )
        merged = list(control.capability_grants)
        fingerprints = {grant_fingerprint(grant) for grant in merged}
        for grant in additions:
            fingerprint = grant_fingerprint(grant)
            if fingerprint in fingerprints:
                continue
            merged.append(grant)
            fingerprints.add(fingerprint)
        updated = replace(control, capability_grants=tuple(merged))
        self._agents[agent_id] = updated
        return updated

    def delegation_provenance(
        self,
        delegation_id: str,
    ) -> DelegationProvenance:
        """Return the durable provenance for one installed delegation."""

        try:
            return self._delegations[delegation_id].provenance
        except KeyError as error:
            raise CapabilityDelegationError(
                f"delegation not found: {delegation_id}"
            ) from error

    def delegate_capability(
        self,
        request: DelegateCapabilityRequest,
        *,
        record_session: Session | None = None,
        record: bool = True,
    ) -> DelegationDecision:
        """Delegate narrowed authority from a direct parent Agent to its child."""

        if request.parent_agent_id not in self._agents:
            return DelegationDecision(False, "parent_agent_not_found")
        if request.child_agent_id not in self._agents:
            return DelegationDecision(False, "child_agent_not_found")
        parent = self._agents[request.parent_agent_id]
        child = self._agents[request.child_agent_id]
        if child.parent_agent_id != parent.agent_id:
            return DelegationDecision(False, "not_direct_child")
        if record:
            if record_session is None:
                raise CapabilityDelegationError(
                    "record_session is required when recording a delegation"
                )
            if record_session.session_id != child.session_id:
                raise AgentTreeError(
                    "capability/delegated must be written to the child Agent session"
                )
        decision = self._delegator.delegate(
            request,
            parent_grants=_effective_capability_grants(parent),
            parent_provenance_by_grant=self._delegation_provenance_by_grant,
        )
        if not decision.allowed:
            return decision
        assert decision.delegated_grant is not None
        assert decision.provenance is not None
        payload = decision.provenance.as_payload(decision.delegated_grant)
        if self._delegation_is_already_installed(
            decision.delegated_grant,
            decision.provenance,
            payload,
        ):
            return decision
        if record:
            assert record_session is not None
            record_session.append(EventType.CAPABILITY_DELEGATED, payload)
        self._install_delegation(
            decision.delegated_grant,
            decision.provenance,
            payload,
        )
        return decision

    def replay_delegations(
        self,
        sessions: Iterable[Session],
    ) -> tuple[DelegationDecision, ...]:
        """Replay durable delegation facts against current parent authority."""

        facts = _capability_delegation_facts_from_sessions(sessions)
        decisions: list[DelegationDecision] = []
        for fact in sorted(
            facts.values(),
            key=lambda item: (
                item.provenance.depth,
                item.provenance.delegation_id,
            ),
        ):
            if fact.provenance.parent_agent_id not in self._agents:
                raise AgentRegistryCorruptionError(
                    "capability/delegated references missing parent Agent: "
                    f"{fact.provenance.parent_agent_id}"
                )
            if fact.provenance.child_agent_id not in self._agents:
                raise AgentRegistryCorruptionError(
                    "capability/delegated references missing child Agent: "
                    f"{fact.provenance.child_agent_id}"
                )
            if (
                self._agents[fact.provenance.child_agent_id].parent_agent_id
                != fact.provenance.parent_agent_id
            ):
                raise AgentRegistryCorruptionError(
                    "capability/delegated parent does not match Agent tree"
                )
            decision = self.delegate_capability(
                DelegateCapabilityRequest(
                    parent_agent_id=fact.provenance.parent_agent_id,
                    child_agent_id=fact.provenance.child_agent_id,
                    action=fact.provenance.action,
                    resource_scope=fact.provenance.resource_scope,
                    constraints=fact.provenance.constraints,
                    parent_grant_fingerprint=(
                        fact.provenance.parent_grant_fingerprint
                    ),
                    delegation_id=fact.provenance.delegation_id,
                    correlation_id=fact.provenance.correlation_id,
                    expires_at=fact.provenance.expires_at,
                ),
                record=False,
            )
            if decision.allowed:
                assert decision.delegated_grant is not None
                assert decision.provenance is not None
                if (
                    decision.provenance.as_payload(decision.delegated_grant)
                    != fact.payload
                ):
                    raise AgentRegistryCorruptionError(
                        "capability/delegated replay changed provenance"
                    )
            decisions.append(decision)
        return tuple(decisions)

    @classmethod
    def reconstruct(
        cls,
        sessions: Iterable[Session],
        *,
        current_capability_grants: Mapping[str, Iterable[CapabilityGrant]]
        | None = None,
    ) -> "AgentRegistry":
        """Rebuild an AgentRegistry from durable agent/created facts."""

        session_tuple = tuple(sessions)
        facts = _agent_creation_facts_from_sessions(session_tuple)
        registry = cls()
        remaining = dict(facts)
        while remaining:
            progressed = False
            for agent_id, fact in list(remaining.items()):
                control = _control_from_creation_fact(fact)
                if fact.parent_agent_id is None:
                    registry.register_root(control)
                elif fact.parent_agent_id in registry._agents:
                    registry.register_child(control)
                else:
                    continue
                del remaining[agent_id]
                progressed = True
            if not progressed:
                unresolved = ", ".join(sorted(remaining))
                raise AgentRegistryCorruptionError(
                    "agent tree contains unresolved parent relation or cycle: "
                    f"{unresolved}"
                )
        for agent_id, grants in (current_capability_grants or {}).items():
            registry.install_capability_grants(agent_id, grants)
        registry.replay_delegations(session_tuple)
        return registry

    @classmethod
    def from_sessions(
        cls,
        sessions: Iterable[Session],
        *,
        current_capability_grants: Mapping[str, Iterable[CapabilityGrant]]
        | None = None,
    ) -> "AgentRegistry":
        """Compatibility alias for reconstruct()."""

        return cls.reconstruct(
            sessions,
            current_capability_grants=current_capability_grants,
        )

    def _validate_root_candidate(self, control: AgentControlBlock) -> None:
        if control.parent_agent_id is not None:
            raise InvalidAgentParent("root agent cannot have a parent_agent_id")
        self._validate_new_identity(control)

    def _validate_child_candidate(
        self,
        control: AgentControlBlock,
        parent_agent_id: str,
    ) -> None:
        if not parent_agent_id:
            raise InvalidAgentParent("child agent requires a non-empty parent_agent_id")
        if control.parent_agent_id != parent_agent_id:
            raise InvalidAgentParent("child parent_agent_id does not match request")
        if control.agent_id == parent_agent_id:
            raise InvalidAgentParent("agent cannot be its own parent")
        self._require_parent(parent_agent_id)
        self._validate_new_identity(control)

    def _validate_new_identity(self, control: AgentControlBlock) -> None:
        if control.agent_id in self._agents:
            raise AgentAlreadyExists(f"agent already exists: {control.agent_id}")
        for existing in self._agents.values():
            if existing.session_id == control.session_id:
                raise AgentTreeError(
                    f"session {control.session_id!r} already belongs to "
                    f"agent {existing.agent_id!r}"
                )

    def _require_parent(self, parent_agent_id: str) -> AgentControlBlock:
        if parent_agent_id not in self._agents:
            raise InvalidAgentParent(f"parent agent not found: {parent_agent_id}")
        return self._agents[parent_agent_id]

    def _insert(self, control: AgentControlBlock) -> None:
        self._agents[control.agent_id] = control
        self._children.setdefault(control.agent_id, [])
        if control.parent_agent_id is not None:
            self._children.setdefault(control.parent_agent_id, []).append(
                control.agent_id
            )

    def _delegation_is_already_installed(
        self,
        grant: CapabilityGrant,
        provenance: DelegationProvenance,
        payload: Mapping[str, JsonValue],
    ) -> bool:
        existing = self._delegations.get(provenance.delegation_id)
        if existing is not None:
            if existing.payload != dict(payload):
                raise AgentRegistryCorruptionError(
                    "conflicting capability delegation id: "
                    f"{provenance.delegation_id}"
                )
            return True
        grant_fpr = grant_fingerprint(grant)
        existing_delegation_id = self._delegation_by_child_grant.get(grant_fpr)
        if existing_delegation_id is not None:
            raise AgentRegistryCorruptionError(
                "conflicting capability delegation child grant provenance: "
                f"{existing_delegation_id}, {provenance.delegation_id}"
            )
        return False

    def _install_delegation(
        self,
        grant: CapabilityGrant,
        provenance: DelegationProvenance,
        payload: Mapping[str, JsonValue],
    ) -> None:
        if self._delegation_is_already_installed(grant, provenance, payload):
            return
        self.install_capability_grants(provenance.child_agent_id, (grant,))
        fact = _CapabilityDelegationFact(
            grant=grant,
            provenance=provenance,
            payload=dict(payload),
        )
        grant_fpr = grant_fingerprint(grant)
        self._delegations[provenance.delegation_id] = fact
        self._delegation_by_child_grant[grant_fpr] = provenance.delegation_id
        self._delegation_provenance_by_grant[grant_fpr] = provenance

    @staticmethod
    def _append_agent_created(
        session: Session,
        control: AgentControlBlock,
        creation_id: str | None,
    ) -> SessionEvent:
        creation = creation_id or f"agent_create_{uuid.uuid4().hex}"
        if not creation:
            raise ValueError("creation_id must not be empty")
        return session.append(
            EventType.AGENT_CREATED,
            {
                "agent_id": control.agent_id,
                "parent_agent_id": control.parent_agent_id,
                "session_id": control.session_id,
                "creation_id": creation,
            },
        )


def _agent_control(agent: Agent | AgentControlBlock) -> AgentControlBlock:
    if isinstance(agent, Agent):
        return agent.control
    if isinstance(agent, AgentControlBlock):
        return agent
    raise TypeError("agent must be an Agent or AgentControlBlock")


def _agent_creation_facts_from_sessions(
    sessions: Iterable[Session],
) -> dict[str, _AgentCreationFact]:
    facts: dict[str, _AgentCreationFact] = {}
    facts_by_creation_id: dict[str, _AgentCreationFact] = {}
    for session in sessions:
        for event in session.events:
            if event.type is not EventType.AGENT_CREATED:
                continue
            fact = _agent_creation_fact_from_event(event.data)
            existing_creation = facts_by_creation_id.get(fact.creation_id)
            if existing_creation is not None:
                if existing_creation != fact:
                    raise AgentRegistryCorruptionError(
                        f"conflicting agent creation_id: {fact.creation_id}"
                    )
                continue
            existing_agent = facts.get(fact.agent_id)
            if existing_agent is not None:
                raise AgentRegistryCorruptionError(
                    f"agent {fact.agent_id!r} has multiple creation facts"
                )
            facts_by_creation_id[fact.creation_id] = fact
            facts[fact.agent_id] = fact
    _validate_reconstructed_facts(facts)
    return facts


def _capability_delegation_facts_from_sessions(
    sessions: Iterable[Session],
) -> dict[str, _CapabilityDelegationFact]:
    facts: dict[str, _CapabilityDelegationFact] = {}
    facts_by_grant: dict[str, _CapabilityDelegationFact] = {}
    for session in sessions:
        for event in session.events:
            if event.type is not EventType.CAPABILITY_DELEGATED:
                continue
            fact = _capability_delegation_fact_from_event(event.data)
            existing = facts.get(fact.provenance.delegation_id)
            if existing is not None:
                if existing.payload != fact.payload:
                    raise AgentRegistryCorruptionError(
                        "conflicting capability delegation id: "
                        f"{fact.provenance.delegation_id}"
                    )
                continue
            grant_fpr = grant_fingerprint(fact.grant)
            existing_grant = facts_by_grant.get(grant_fpr)
            if existing_grant is not None:
                raise AgentRegistryCorruptionError(
                    "conflicting capability delegation child grant provenance: "
                    f"{existing_grant.provenance.delegation_id}, "
                    f"{fact.provenance.delegation_id}"
                )
            facts[fact.provenance.delegation_id] = fact
            facts_by_grant[grant_fpr] = fact
    return facts


def _capability_delegation_fact_from_event(
    data: Mapping[str, JsonValue],
) -> _CapabilityDelegationFact:
    try:
        provenance = DelegationProvenance.from_payload(data)
        raw_grant = data.get("child_grant")
        if not isinstance(raw_grant, Mapping):
            raise TypeError("child_grant must be an object")
        grant = capability_grant_from_payload(raw_grant)
        payload = provenance.as_payload(grant)
    except (TypeError, ValueError) as error:
        raise AgentRegistryCorruptionError(
            f"invalid capability/delegated event: {error}"
        ) from error
    if payload != dict(data):
        raise AgentRegistryCorruptionError(
            "capability/delegated payload is not canonical"
        )
    return _CapabilityDelegationFact(
        grant=grant,
        provenance=provenance,
        payload=payload,
    )


def _agent_creation_fact_from_event(
    data: Mapping[str, JsonValue],
) -> _AgentCreationFact:
    expected = {"agent_id", "parent_agent_id", "session_id", "creation_id"}
    if set(data) != expected:
        raise AgentRegistryCorruptionError(
            "agent/created must contain exactly agent_id, parent_agent_id, "
            "session_id, creation_id"
        )
    agent_id = _required_string(data, "agent_id")
    session_id = _required_string(data, "session_id")
    creation_id = _required_string(data, "creation_id")
    parent_agent_id = data.get("parent_agent_id")
    if parent_agent_id is not None and (
        not isinstance(parent_agent_id, str) or not parent_agent_id
    ):
        raise AgentRegistryCorruptionError(
            "agent/created parent_agent_id must be null or a non-empty string"
        )
    if parent_agent_id == agent_id:
        raise AgentRegistryCorruptionError(
            "agent/created cannot make an agent its own parent"
        )
    return _AgentCreationFact(
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
        session_id=session_id,
        creation_id=creation_id,
    )


def _validate_reconstructed_facts(
    facts: Mapping[str, _AgentCreationFact],
) -> None:
    session_owner: dict[str, str] = {}
    for agent_id, fact in facts.items():
        owner = session_owner.get(fact.session_id)
        if owner is not None and owner != agent_id:
            raise AgentRegistryCorruptionError(
                f"session {fact.session_id!r} belongs to multiple agents"
            )
        session_owner[fact.session_id] = agent_id
        if fact.parent_agent_id is not None and fact.parent_agent_id not in facts:
            raise AgentRegistryCorruptionError(
                f"agent {agent_id!r} references missing parent "
                f"{fact.parent_agent_id!r}"
            )
    for agent_id in facts:
        seen: set[str] = set()
        current: str | None = agent_id
        while current is not None:
            if current in seen:
                raise AgentRegistryCorruptionError(
                    f"agent tree cycle detected at {current}"
                )
            seen.add(current)
            current = facts[current].parent_agent_id


def _control_from_creation_fact(fact: _AgentCreationFact) -> AgentControlBlock:
    control = AgentControlBlock(
        agent_id=fact.agent_id,
        session_id=fact.session_id,
        state=AgentState.NEW,
        parent_agent_id=fact.parent_agent_id,
        capabilities=frozenset(),
        capability_bounding_set=frozenset(),
        budget=AgentBudget(),
        capability_grants=(),
    )
    control.transition(AgentState.READY)
    return control


def _effective_capability_grants(
    control: AgentControlBlock,
) -> tuple[CapabilityGrant, ...]:
    grants: list[CapabilityGrant] = list(control.capability_grants)
    for capability in sorted(control.capabilities):
        grants.extend(legacy_capability_grants(control.agent_id, capability))
    return tuple(grants)


def _required_string(data: Mapping[str, JsonValue], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise AgentRegistryCorruptionError(
            f"agent/created {name} must be a non-empty string"
        )
    return value
