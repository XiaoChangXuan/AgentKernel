"""Process lifecycle primitives for cooperative AgentKernel execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .agent import AgentBudget, AgentControlBlock
from .capabilities import CapabilityGrant
from .recovery import (
    OperationRecoveryClassification,
    RecoveryAnalysis,
    SessionStatus,
)


class ProcessState(StrEnum):
    """Kernel-owned lifecycle states for a schedulable process."""

    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    EXITED = "EXITED"


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Point-in-time authority view inherited from an Agent principal."""

    agent_id: str
    capabilities: frozenset[str]
    capability_bounding_set: frozenset[str]
    capability_grants: tuple[CapabilityGrant, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(
            self,
            "capability_bounding_set",
            frozenset(self.capability_bounding_set),
        )
        object.__setattr__(self, "capability_grants", tuple(self.capability_grants))
        if not self.agent_id:
            raise ValueError("capability snapshot agent_id must not be empty")
        if not self.capabilities <= self.capability_bounding_set:
            raise ValueError("capability snapshot exceeds bounding set")
        for grant in self.capability_grants:
            if not isinstance(grant, CapabilityGrant):
                raise TypeError("capability_grants must contain CapabilityGrant values")
            if grant.subject != self.agent_id:
                raise ValueError("capability grant subject must match agent_id")

    @classmethod
    def from_agent(cls, agent: AgentControlBlock) -> "CapabilitySnapshot":
        """Capture an Agent-owned capability view for process accounting."""

        return cls(
            agent_id=agent.agent_id,
            capabilities=agent.capabilities,
            capability_bounding_set=agent.capability_bounding_set,
            capability_grants=agent.capability_grants,
        )


_ALLOWED_TRANSITIONS: dict[ProcessState, frozenset[ProcessState]] = {
    ProcessState.CREATED: frozenset({ProcessState.READY, ProcessState.EXITED}),
    ProcessState.READY: frozenset(
        {ProcessState.RUNNING, ProcessState.PAUSED, ProcessState.EXITED}
    ),
    ProcessState.RUNNING: frozenset(
        {
            ProcessState.READY,
            ProcessState.WAITING,
            ProcessState.BLOCKED,
            ProcessState.PAUSED,
            ProcessState.EXITED,
        }
    ),
    ProcessState.WAITING: frozenset(
        {ProcessState.READY, ProcessState.PAUSED, ProcessState.EXITED}
    ),
    ProcessState.BLOCKED: frozenset(
        {ProcessState.READY, ProcessState.PAUSED, ProcessState.EXITED}
    ),
    ProcessState.PAUSED: frozenset({ProcessState.READY, ProcessState.EXITED}),
    ProcessState.EXITED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ProcessControlBlock:
    """Kernel-owned process metadata without Scheduler implementation."""

    process_id: str
    agent_id: str
    session_id: str
    state: ProcessState
    parent_process_id: str | None = None
    priority: int = 0
    budget: AgentBudget = field(default_factory=AgentBudget)
    wait_reason: str | None = None
    blocked_reason: str | None = None
    cancel_requested: bool = False
    pause_requested: bool = False
    capability_snapshot: CapabilitySnapshot | None = None
    exit_status: str | None = None

    def __post_init__(self) -> None:
        state = ProcessState(self.state)
        object.__setattr__(self, "state", state)
        self._validate_identity()
        self._validate_lifecycle_fields(state)
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if not isinstance(self.budget, AgentBudget):
            raise TypeError("budget must be an AgentBudget")
        if not isinstance(self.cancel_requested, bool):
            raise TypeError("cancel_requested must be a boolean")
        if not isinstance(self.pause_requested, bool):
            raise TypeError("pause_requested must be a boolean")
        if self.capability_snapshot is None:
            raise ValueError("capability_snapshot is required")
        if not isinstance(self.capability_snapshot, CapabilitySnapshot):
            raise TypeError("capability_snapshot must be a CapabilitySnapshot")
        if self.capability_snapshot.agent_id != self.agent_id:
            raise ValueError("capability snapshot must match agent_id")

    @classmethod
    def create(
        cls,
        *,
        process_id: str,
        agent: AgentControlBlock,
        parent_process_id: str | None = None,
        priority: int = 0,
    ) -> "ProcessControlBlock":
        """Create a process in CREATED state from an Agent principal."""

        return cls(
            process_id=process_id,
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            state=ProcessState.CREATED,
            parent_process_id=parent_process_id,
            priority=priority,
            budget=agent.budget,
            capability_snapshot=CapabilitySnapshot.from_agent(agent),
        )

    @classmethod
    def from_recovery(
        cls,
        *,
        process_id: str,
        agent: AgentControlBlock,
        recovery: RecoveryAnalysis,
        parent_process_id: str | None = None,
        priority: int = 0,
    ) -> "ProcessControlBlock":
        """Reconstruct live process state from durable recovery facts."""

        recovered = _state_from_recovery(recovery)
        return cls(
            process_id=process_id,
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            state=recovered.state,
            parent_process_id=parent_process_id,
            priority=priority,
            budget=agent.budget,
            blocked_reason=recovered.blocked_reason,
            capability_snapshot=CapabilitySnapshot.from_agent(agent),
            exit_status=recovered.exit_status,
        )

    def transition(
        self,
        target: ProcessState,
        *,
        wait_reason: str | None = None,
        blocked_reason: str | None = None,
        exit_status: str | None = None,
    ) -> None:
        """Perform one valid process lifecycle transition."""

        target = ProcessState(target)
        if target is self.state:
            return
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise RuntimeError(f"invalid process transition: {self.state} -> {target}")
        self._validate_target_fields(
            target,
            wait_reason=wait_reason,
            blocked_reason=blocked_reason,
            exit_status=exit_status,
        )
        object.__setattr__(self, "state", target)
        object.__setattr__(
            self,
            "wait_reason",
            wait_reason if target is ProcessState.WAITING else None,
        )
        object.__setattr__(
            self,
            "blocked_reason",
            blocked_reason if target is ProcessState.BLOCKED else None,
        )
        object.__setattr__(
            self,
            "exit_status",
            exit_status if target is ProcessState.EXITED else None,
        )

    def request_cancel(self) -> None:
        """Record a sticky cancellation request checked by future schedulers."""

        if self.state is ProcessState.EXITED:
            raise RuntimeError("cannot cancel an exited process")
        object.__setattr__(self, "cancel_requested", True)

    def request_pause(self) -> None:
        """Record a sticky pause request checked by future schedulers."""

        if self.state is ProcessState.EXITED:
            raise RuntimeError("cannot pause an exited process")
        object.__setattr__(self, "pause_requested", True)

    def clear_pause_request(self) -> None:
        """Clear the pause request after host policy resumes a process."""

        object.__setattr__(self, "pause_requested", False)

    def _validate_identity(self) -> None:
        if not self.process_id or not self.agent_id or not self.session_id:
            raise ValueError("process_id, agent_id, and session_id must not be empty")
        if self.parent_process_id == "":
            raise ValueError("parent_process_id must not be empty")
        if self.parent_process_id == self.process_id:
            raise ValueError("process cannot be its own parent")

    def _validate_lifecycle_fields(self, state: ProcessState) -> None:
        self._validate_target_fields(
            state,
            wait_reason=self.wait_reason,
            blocked_reason=self.blocked_reason,
            exit_status=self.exit_status,
        )

    @staticmethod
    def _validate_target_fields(
        target: ProcessState,
        *,
        wait_reason: str | None,
        blocked_reason: str | None,
        exit_status: str | None,
    ) -> None:
        if target is ProcessState.WAITING:
            _require_non_empty(wait_reason, "wait_reason")
        elif wait_reason is not None:
            raise ValueError("wait_reason is only valid for WAITING processes")

        if target is ProcessState.BLOCKED:
            _require_non_empty(blocked_reason, "blocked_reason")
        elif blocked_reason is not None:
            raise ValueError("blocked_reason is only valid for BLOCKED processes")

        if target is ProcessState.EXITED:
            _require_non_empty(exit_status, "exit_status")
        elif exit_status is not None:
            raise ValueError("exit_status is only valid for EXITED processes")


@dataclass(frozen=True, slots=True)
class _RecoveredProcessState:
    state: ProcessState
    blocked_reason: str | None = None
    exit_status: str | None = None


def _state_from_recovery(recovery: RecoveryAnalysis) -> _RecoveredProcessState:
    if recovery.status is SessionStatus.COMPLETED:
        return _RecoveredProcessState(
            state=ProcessState.EXITED,
            exit_status="completed",
        )
    if recovery.status is SessionStatus.CORRUPTED:
        return _RecoveredProcessState(
            state=ProcessState.EXITED,
            exit_status="corrupted",
        )
    if recovery.active_compaction_id is not None:
        return _RecoveredProcessState(
            state=ProcessState.BLOCKED,
            blocked_reason="context_compaction_recovery",
        )
    for operation in recovery.durable_operations:
        if operation.classification in {
            OperationRecoveryClassification.RECONCILE_REQUIRED,
            OperationRecoveryClassification.MANUAL_REQUIRED,
        }:
            return _RecoveredProcessState(
                state=ProcessState.BLOCKED,
                blocked_reason="durable_operation_recovery",
            )
    return _RecoveredProcessState(state=ProcessState.READY)


def _require_non_empty(value: str | None, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
