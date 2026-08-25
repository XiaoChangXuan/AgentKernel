"""Agent process state, capability bounds, and turn budgets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .capabilities import CapabilityGrant
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
    """Hard per-turn limits enforced by the default loop."""

    max_steps_per_turn: int = 8
    max_tool_calls_per_turn: int = 16

    def __post_init__(self) -> None:
        if self.max_steps_per_turn < 1:
            raise ValueError("max_steps_per_turn must be at least 1")
        if self.max_tool_calls_per_turn < 1:
            raise ValueError("max_tool_calls_per_turn must be at least 1")


class CapabilityBoundError(ValueError):
    """Raised when effective capabilities exceed the bounding set."""


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
    """Kernel-owned process metadata for one agent."""

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
