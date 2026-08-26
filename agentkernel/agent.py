"""Agent process state, capability bounds, and turn budgets."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping

from .capabilities import CapabilityGrant
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


class AgentRegistry:
    """Kernel-owned registry for Agent identity and parent-child relations."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentControlBlock] = {}
        self._children: dict[str, list[str]] = {}

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

    @classmethod
    def reconstruct(cls, sessions: Iterable[Session]) -> "AgentRegistry":
        """Rebuild an AgentRegistry from durable agent/created facts."""

        facts = _agent_creation_facts_from_sessions(sessions)
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
        return registry

    @classmethod
    def from_sessions(cls, sessions: Iterable[Session]) -> "AgentRegistry":
        """Compatibility alias for reconstruct()."""

        return cls.reconstruct(sessions)

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


def _required_string(data: Mapping[str, JsonValue], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise AgentRegistryCorruptionError(
            f"agent/created {name} must be a non-empty string"
        )
    return value
