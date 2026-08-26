"""Cooperative process scheduling primitives for V0.7."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import uuid

from .accounting import UsageCollector, UsageLimitExceeded
from .agent import AgentControlBlock, AgentRegistry
from .events import EventType, SessionEvent
from .process import ProcessControlBlock, ProcessState
from .protocol import JsonValue
from .session import Session


class SchedulerError(RuntimeError):
    """Base class for cooperative scheduler failures."""


class ProcessAlreadyExists(SchedulerError):
    """Raised when a process id is registered twice."""


class ProcessNotFound(SchedulerError):
    """Raised when a process id is not present in the process table."""


class InvalidProcessParent(SchedulerError):
    """Raised when a process parent relation violates Process Tree rules."""


class ProcessTreeError(SchedulerError):
    """Raised when the Process Tree cannot satisfy a runtime invariant."""


class ProcessRegistryCorruptionError(ProcessTreeError):
    """Raised when durable Process creation facts cannot be replayed safely."""


class ProcessSessionConflict(SchedulerError):
    """Raised when dispatch would violate single-writer Session ownership."""


class NoRunnableProcess(SchedulerError):
    """Raised when the READY queue has no schedulable process."""


class ProcessPaused(SchedulerError):
    """Raised when a process reaches a cooperative pause safe point."""

    def __init__(self, process_id: str, safe_point: "SchedulerSafePoint") -> None:
        self.process_id = process_id
        self.safe_point = SchedulerSafePoint(safe_point)
        super().__init__(
            f"process {process_id} paused at safe point {self.safe_point.value}"
        )


class ProcessCancelled(SchedulerError):
    """Raised when a process reaches a cooperative cancellation safe point."""

    def __init__(self, process_id: str, safe_point: "SchedulerSafePoint") -> None:
        self.process_id = process_id
        self.safe_point = SchedulerSafePoint(safe_point)
        super().__init__(
            f"process {process_id} cancelled at safe point {self.safe_point.value}"
        )


class ProcessBudgetExceeded(SchedulerError):
    """Raised when a process exceeds a Kernel runtime resource budget."""

    def __init__(
        self,
        process_id: str,
        safe_point: "SchedulerSafePoint",
        exceeded: UsageLimitExceeded,
    ) -> None:
        self.process_id = process_id
        self.safe_point = SchedulerSafePoint(safe_point)
        self.exceeded = exceeded
        super().__init__(
            f"process {process_id} exceeded {exceeded.limit} "
            f"at safe point {self.safe_point.value}: "
            f"{exceeded.usage} > {exceeded.maximum}"
        )


class SchedulerSafePoint(StrEnum):
    """Named cooperative safe points exposed by the default Agent loop."""

    BEFORE_TURN_START = "turn_start.before"
    BEFORE_STEP_START = "step_start.before"
    BEFORE_LLM_CALL = "llm_call.before"
    AFTER_LLM_CALL = "llm_call.after"
    BEFORE_TOOL_CALL = "tool_call.before"
    AFTER_TOOL_CALL = "tool_call.after"
    BEFORE_DURABLE_DISPATCH = "durable_dispatch.before"
    AFTER_DURABLE_DISPATCH = "durable_dispatch.after"


@dataclass(frozen=True, slots=True)
class _ProcessCreationFact:
    process_id: str
    agent_id: str
    session_id: str
    parent_process_id: str | None
    creation_id: str


class ProcessManager:
    """Kernel-owned live process table."""

    def __init__(self, *, agent_registry: AgentRegistry | None = None) -> None:
        self._processes: dict[str, ProcessControlBlock] = {}
        self._children: dict[str, list[str]] = {}
        self._agent_registry = agent_registry

    def create_process(
        self,
        *,
        process_id: str,
        agent: AgentControlBlock,
        parent_process_id: str | None = None,
        priority: int = 0,
        admit: bool = True,
        record_session: Session | None = None,
        creation_id: str | None = None,
    ) -> ProcessControlBlock:
        """Create and optionally admit a process into READY state."""

        process = ProcessControlBlock.create(
            process_id=process_id,
            agent=agent,
            parent_process_id=parent_process_id,
            priority=priority,
        )
        self._validate_new_process(process)
        if record_session is not None:
            self._append_process_created(record_session, process, creation_id)
        self._insert(process)
        if admit:
            process.transition(ProcessState.READY)
        return process

    def create_child_process(
        self,
        *,
        parent_process_id: str,
        process_id: str,
        agent: AgentControlBlock,
        priority: int = 0,
        admit: bool = True,
        record_session: Session | None = None,
        creation_id: str | None = None,
    ) -> ProcessControlBlock:
        """Create a child process supervised by an existing parent process."""

        if not parent_process_id:
            raise InvalidProcessParent(
                "child process requires a non-empty parent_process_id"
            )
        return self.create_process(
            process_id=process_id,
            agent=agent,
            parent_process_id=parent_process_id,
            priority=priority,
            admit=admit,
            record_session=record_session,
            creation_id=creation_id,
        )

    def register(self, process: ProcessControlBlock) -> None:
        """Add a ProcessControlBlock to the live process table."""

        self._validate_new_process(process)
        self._insert(process)

    def record_process_created(
        self,
        session: Session,
        process: ProcessControlBlock,
        *,
        creation_id: str | None = None,
    ) -> SessionEvent:
        """Append one durable Process identity creation fact."""

        registered = self.get(process.process_id)
        if registered.agent_id != process.agent_id:
            raise ProcessTreeError("registered process agent_id does not match")
        if registered.session_id != process.session_id:
            raise ProcessTreeError("registered process session_id does not match")
        if registered.parent_process_id != process.parent_process_id:
            raise InvalidProcessParent(
                "registered process parent_process_id does not match"
            )
        return self._append_process_created(session, registered, creation_id)

    def get(self, process_id: str) -> ProcessControlBlock:
        """Return a process by id."""

        try:
            return self._processes[process_id]
        except KeyError as error:
            raise ProcessNotFound(f"process not found: {process_id}") from error

    def state(self, process_id: str) -> ProcessState:
        """Return the current process state."""

        return self.get(process_id).state

    def list_processes(self) -> tuple[ProcessControlBlock, ...]:
        """Return a stable snapshot of the live process table."""

        return tuple(self._processes.values())

    def parent_of(self, process_id: str) -> str | None:
        """Return the parent process id, or None for a root process."""

        return self.get(process_id).parent_process_id

    def children_of(self, process_id: str) -> tuple[str, ...]:
        """Return direct child process ids in creation order."""

        self.get(process_id)
        return tuple(self._children.get(process_id, ()))

    def root_of(self, process_id: str) -> str:
        """Return the root process id for the process tree."""

        return self.lineage(process_id)[0]

    def lineage(self, process_id: str) -> tuple[str, ...]:
        """Return process ancestry ordered from root to requested process."""

        self.get(process_id)
        reversed_lineage: list[str] = []
        current: str | None = process_id
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise ProcessTreeError(f"process tree cycle detected at {current}")
            seen.add(current)
            process = self.get(current)
            reversed_lineage.append(current)
            current = process.parent_process_id
        return tuple(reversed(reversed_lineage))

    def descendants_of(self, process_id: str) -> tuple[str, ...]:
        """Return all descendant process ids in deterministic creation order."""

        self.get(process_id)
        descendants: list[str] = []
        pending = list(reversed(self._children.get(process_id, ())))
        while pending:
            child_id = pending.pop()
            descendants.append(child_id)
            pending.extend(reversed(self._children.get(child_id, ())))
        return tuple(descendants)

    def depth(self, process_id: str) -> int:
        """Return process tree depth where roots have depth zero."""

        return len(self.lineage(process_id)) - 1

    def exited_children_of(self, process_id: str) -> tuple[ProcessControlBlock, ...]:
        """Return direct child processes that have reached EXITED."""

        return tuple(
            self.get(child_id)
            for child_id in self.children_of(process_id)
            if self.get(child_id).state is ProcessState.EXITED
        )

    def child_exit_statuses(self, process_id: str) -> dict[str, str | None]:
        """Return direct exited child ids mapped to their exit status."""

        return {
            child.process_id: child.exit_status
            for child in self.exited_children_of(process_id)
        }

    def delete_exited_process(self, process_id: str) -> ProcessControlBlock:
        """Remove one exited process from the process table."""

        process = self.get(process_id)
        if process.state is not ProcessState.EXITED:
            raise RuntimeError(
                f"cannot delete non-exited process {process_id}: {process.state}"
            )
        if self._children.get(process_id):
            raise RuntimeError(f"cannot delete process {process_id} with children")
        removed = self._processes.pop(process_id)
        self._children.pop(process_id, None)
        if process.parent_process_id is not None:
            siblings = self._children.get(process.parent_process_id)
            if siblings is not None:
                self._children[process.parent_process_id] = [
                    child_id for child_id in siblings if child_id != process_id
                ]
        return removed

    def delete_exited_processes(self) -> tuple[ProcessControlBlock, ...]:
        """Remove all exited processes from the process table."""

        deleted: list[ProcessControlBlock] = []
        while True:
            progressed = False
            for process_id, process in list(self._processes.items()):
                if process.state is not ProcessState.EXITED:
                    continue
                if self._children.get(process_id):
                    continue
                deleted.append(self.delete_exited_process(process_id))
                progressed = True
            if not progressed:
                break
        return tuple(deleted)

    @classmethod
    def reconstruct(
        cls,
        sessions: Iterable[Session],
        *,
        agent_registry: AgentRegistry,
    ) -> "ProcessManager":
        """Rebuild Process Tree metadata from durable process/created facts."""

        facts = _process_creation_facts_from_sessions(sessions)
        _validate_reconstructed_process_facts(facts, agent_registry)
        manager = cls(agent_registry=agent_registry)
        remaining = dict(facts)
        while remaining:
            progressed = False
            for process_id, fact in list(remaining.items()):
                if (
                    fact.parent_process_id is not None
                    and fact.parent_process_id not in manager._processes
                ):
                    continue
                process = ProcessControlBlock.create(
                    process_id=process_id,
                    agent=agent_registry.get(fact.agent_id),
                    parent_process_id=fact.parent_process_id,
                )
                manager.register(process)
                del remaining[process_id]
                progressed = True
            if not progressed:
                unresolved = ", ".join(sorted(remaining))
                raise ProcessRegistryCorruptionError(
                    "process tree contains unresolved parent relation or cycle: "
                    f"{unresolved}"
                )
        return manager

    @classmethod
    def from_sessions(
        cls,
        sessions: Iterable[Session],
        *,
        agent_registry: AgentRegistry,
    ) -> "ProcessManager":
        """Compatibility alias for reconstruct()."""

        return cls.reconstruct(sessions, agent_registry=agent_registry)

    def _validate_new_process(self, process: ProcessControlBlock) -> None:
        if process.process_id in self._processes:
            raise ProcessAlreadyExists(
                f"process already exists: {process.process_id}"
            )
        if process.parent_process_id is not None:
            if process.parent_process_id == process.process_id:
                raise InvalidProcessParent("process cannot be its own parent")
            if process.parent_process_id not in self._processes:
                raise InvalidProcessParent(
                    f"parent process not found: {process.parent_process_id}"
                )
        self._validate_agent_ownership(process)

    def _validate_agent_ownership(self, process: ProcessControlBlock) -> None:
        if self._agent_registry is None:
            return
        if not self._agent_registry.contains(process.agent_id):
            raise ProcessTreeError(f"owning agent not found: {process.agent_id}")
        agent = self._agent_registry.get(process.agent_id)
        if agent.session_id != process.session_id:
            raise ProcessTreeError(
                "process session_id does not match owning agent primary session"
            )

    def _insert(self, process: ProcessControlBlock) -> None:
        self._processes[process.process_id] = process
        self._children.setdefault(process.process_id, [])
        if process.parent_process_id is not None:
            self._children.setdefault(process.parent_process_id, []).append(
                process.process_id
            )

    @staticmethod
    def _append_process_created(
        session: Session,
        process: ProcessControlBlock,
        creation_id: str | None,
    ) -> SessionEvent:
        if session.session_id != process.session_id:
            raise ProcessTreeError(
                "process/created must be written to the owning Agent session"
            )
        creation = creation_id or f"process_create_{uuid.uuid4().hex}"
        if not creation:
            raise ValueError("creation_id must not be empty")
        return session.append(
            EventType.PROCESS_CREATED,
            {
                "process_id": process.process_id,
                "agent_id": process.agent_id,
                "session_id": process.session_id,
                "parent_process_id": process.parent_process_id,
                "creation_id": creation,
            },
        )


def _process_creation_facts_from_sessions(
    sessions: Iterable[Session],
) -> dict[str, _ProcessCreationFact]:
    facts: dict[str, _ProcessCreationFact] = {}
    facts_by_creation_id: dict[str, _ProcessCreationFact] = {}
    for session in sessions:
        for event in session.events:
            if event.type is not EventType.PROCESS_CREATED:
                continue
            fact = _process_creation_fact_from_event(event.data)
            existing_creation = facts_by_creation_id.get(fact.creation_id)
            if existing_creation is not None:
                if existing_creation != fact:
                    raise ProcessRegistryCorruptionError(
                        f"conflicting process creation_id: {fact.creation_id}"
                    )
                continue
            existing_process = facts.get(fact.process_id)
            if existing_process is not None:
                raise ProcessRegistryCorruptionError(
                    f"process {fact.process_id!r} has multiple creation facts"
                )
            facts_by_creation_id[fact.creation_id] = fact
            facts[fact.process_id] = fact
    return facts


def _process_creation_fact_from_event(
    data: Mapping[str, JsonValue],
) -> _ProcessCreationFact:
    expected = {
        "process_id",
        "agent_id",
        "session_id",
        "parent_process_id",
        "creation_id",
    }
    if set(data) != expected:
        raise ProcessRegistryCorruptionError(
            "process/created must contain exactly process_id, agent_id, "
            "session_id, parent_process_id, creation_id"
        )
    process_id = _required_string(data, "process_id")
    agent_id = _required_string(data, "agent_id")
    session_id = _required_string(data, "session_id")
    creation_id = _required_string(data, "creation_id")
    parent_process_id = data.get("parent_process_id")
    if parent_process_id is not None and (
        not isinstance(parent_process_id, str) or not parent_process_id
    ):
        raise ProcessRegistryCorruptionError(
            "process/created parent_process_id must be null or a non-empty string"
        )
    if parent_process_id == process_id:
        raise ProcessRegistryCorruptionError(
            "process/created cannot make a process its own parent"
        )
    return _ProcessCreationFact(
        process_id=process_id,
        agent_id=agent_id,
        session_id=session_id,
        parent_process_id=parent_process_id,
        creation_id=creation_id,
    )


def _validate_reconstructed_process_facts(
    facts: Mapping[str, _ProcessCreationFact],
    agent_registry: AgentRegistry,
) -> None:
    for process_id, fact in facts.items():
        if not agent_registry.contains(fact.agent_id):
            raise ProcessRegistryCorruptionError(
                f"process {process_id!r} references missing agent {fact.agent_id!r}"
            )
        agent = agent_registry.get(fact.agent_id)
        if agent.session_id != fact.session_id:
            raise ProcessRegistryCorruptionError(
                f"process {process_id!r} session does not match owning agent"
            )
        if fact.parent_process_id is not None and fact.parent_process_id not in facts:
            raise ProcessRegistryCorruptionError(
                f"process {process_id!r} references missing parent "
                f"{fact.parent_process_id!r}"
            )
    for process_id in facts:
        seen: set[str] = set()
        current: str | None = process_id
        while current is not None:
            if current in seen:
                raise ProcessRegistryCorruptionError(
                    f"process tree cycle detected at {current}"
                )
            seen.add(current)
            current = facts[current].parent_process_id


def _required_string(data: Mapping[str, JsonValue], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ProcessRegistryCorruptionError(
            f"process/created {name} must be a non-empty string"
        )
    return value


class CooperativeScheduler:
    """Kernel-owned cooperative scheduler over ProcessControlBlock state."""

    def __init__(
        self,
        manager: ProcessManager | None = None,
        *,
        usage_collector: UsageCollector | None = None,
    ) -> None:
        self._manager = manager or ProcessManager()
        self._usage_collector = usage_collector
        self._ready: deque[str] = deque()
        self._waiting: dict[str, str] = {}
        self._blocked: dict[str, str] = {}

    @property
    def manager(self) -> ProcessManager:
        """Return the backing process table."""

        return self._manager

    @property
    def usage_collector(self) -> UsageCollector | None:
        """Return the optional process usage collector."""

        return self._usage_collector

    @property
    def ready_queue(self) -> tuple[str, ...]:
        """Return the READY queue process ids in current scheduling order."""

        self._prune_ready_queue()
        return tuple(self._ready)

    @property
    def waiting_registry(self) -> dict[str, str]:
        """Return a copy of WAITING process reasons."""

        return dict(self._waiting)

    @property
    def blocked_registry(self) -> dict[str, str]:
        """Return a copy of BLOCKED process reasons."""

        return dict(self._blocked)

    def create_process(
        self,
        *,
        process_id: str,
        agent: AgentControlBlock,
        parent_process_id: str | None = None,
        priority: int = 0,
    ) -> ProcessControlBlock:
        """Create, admit, and enqueue a process."""

        process = self._manager.create_process(
            process_id=process_id,
            agent=agent,
            parent_process_id=parent_process_id,
            priority=priority,
            admit=True,
        )
        self._enqueue_ready(process.process_id)
        return process

    def add_process(self, process: ProcessControlBlock) -> None:
        """Register an existing process and index it by lifecycle state."""

        self._manager.register(process)
        self._index_process(process)

    def admit(self, process_id: str) -> ProcessControlBlock:
        """Move a CREATED process into READY state and enqueue it."""

        process = self._manager.get(process_id)
        if process.state is not ProcessState.CREATED:
            raise RuntimeError(
                f"only CREATED processes can be admitted, got {process.state}"
            )
        process.transition(ProcessState.READY)
        self._enqueue_ready(process_id)
        return process

    def schedule_next(self) -> ProcessControlBlock:
        """Select and dequeue the next READY process."""

        self._prune_ready_queue()
        if not self._ready:
            raise NoRunnableProcess("no READY process is available")
        process_id = self._pop_highest_priority_ready()
        return self._manager.get(process_id)

    def dispatch(self, process_id: str | None = None) -> ProcessControlBlock:
        """Transition one READY process to RUNNING."""

        process = (
            self._next_dispatchable_process()
            if process_id is None
            else self._manager.get(process_id)
        )
        if process.state is not ProcessState.READY:
            raise RuntimeError(
                f"only READY processes can be dispatched, got {process.state}"
            )
        self._ensure_session_writer_available(process)
        self._remove_from_ready(process.process_id)
        self._waiting.pop(process.process_id, None)
        self._blocked.pop(process.process_id, None)
        process.transition(ProcessState.RUNNING)
        return process

    def yield_process(
        self,
        process_id: str,
        target: ProcessState = ProcessState.READY,
        *,
        reason: str | None = None,
        exit_status: str | None = None,
    ) -> ProcessControlBlock:
        """Yield a running process back to the cooperative scheduler."""

        process = self._manager.get(process_id)
        target = ProcessState(target)
        self._cleanup_indexes(process_id)
        if target is ProcessState.READY:
            process.transition(ProcessState.READY)
            self._enqueue_ready(process_id)
        elif target is ProcessState.WAITING:
            process.transition(ProcessState.WAITING, wait_reason=reason)
            assert process.wait_reason is not None
            self._waiting[process_id] = process.wait_reason
        elif target is ProcessState.BLOCKED:
            process.transition(ProcessState.BLOCKED, blocked_reason=reason)
            assert process.blocked_reason is not None
            self._blocked[process_id] = process.blocked_reason
        elif target is ProcessState.PAUSED:
            process.transition(ProcessState.PAUSED)
        elif target is ProcessState.EXITED:
            process.transition(
                ProcessState.EXITED,
                exit_status=exit_status or reason,
            )
        else:
            raise RuntimeError(f"cannot yield process to {target}")
        return process

    def wake(self, process_id: str) -> ProcessControlBlock:
        """Move a WAITING process back to READY."""

        process = self._manager.get(process_id)
        if process.state is not ProcessState.WAITING:
            raise RuntimeError(
                f"only WAITING processes can be woken, got {process.state}"
            )
        self._waiting.pop(process_id, None)
        process.transition(ProcessState.READY)
        self._enqueue_ready(process_id)
        return process

    def unblock(self, process_id: str) -> ProcessControlBlock:
        """Move a BLOCKED process back to READY after recovery/policy action."""

        process = self._manager.get(process_id)
        if process.state is not ProcessState.BLOCKED:
            raise RuntimeError(
                f"only BLOCKED processes can be unblocked, got {process.state}"
            )
        self._blocked.pop(process_id, None)
        process.transition(ProcessState.READY)
        self._enqueue_ready(process_id)
        return process

    def request_pause(self, process_id: str) -> None:
        """Request cooperative pause at the next safe point."""

        self._manager.get(process_id).request_pause()

    def pause(self, process_id: str) -> ProcessControlBlock:
        """Pause a non-running process immediately or request running pause."""

        process = self._manager.get(process_id)
        process.request_pause()
        if process.state is ProcessState.RUNNING:
            return process
        if process.state is ProcessState.CREATED:
            return process
        self._cleanup_indexes(process_id)
        process.transition(ProcessState.PAUSED)
        return process

    def resume(self, process_id: str) -> ProcessControlBlock:
        """Resume a paused process into READY state."""

        process = self._manager.get(process_id)
        if process.state is not ProcessState.PAUSED:
            raise RuntimeError(
                f"only PAUSED processes can be resumed, got {process.state}"
            )
        process.clear_pause_request()
        process.transition(ProcessState.READY)
        self._enqueue_ready(process_id)
        return process

    def request_cancel(self, process_id: str) -> None:
        """Request cooperative cancellation at the next safe point."""

        self._manager.get(process_id).request_cancel()

    def cancel(self, process_id: str) -> None:
        """Alias for request_cancel."""

        self.request_cancel(process_id)

    def exit_process(
        self,
        process_id: str,
        *,
        exit_status: str,
    ) -> ProcessControlBlock:
        """Mark a process exited and remove it from scheduler indexes."""

        process = self._manager.get(process_id)
        if process.state is ProcessState.EXITED:
            return process
        self._cleanup_indexes(process_id)
        process.transition(ProcessState.EXITED, exit_status=exit_status)
        return process

    def safe_point(
        self,
        process_id: str,
        point: SchedulerSafePoint,
    ) -> ProcessControlBlock:
        """Apply pending cooperative controls at a named kernel safe point."""

        point = SchedulerSafePoint(point)
        process = self._manager.get(process_id)
        if process.cancel_requested:
            self.exit_process(process_id, exit_status="cancelled")
            raise ProcessCancelled(process_id, point)
        if process.pause_requested:
            self._cleanup_indexes(process_id)
            if process.state is not ProcessState.PAUSED:
                process.transition(ProcessState.PAUSED)
            raise ProcessPaused(process_id, point)
        exceeded = self.check_budget(process_id)
        if exceeded is not None:
            self._cleanup_indexes(process_id)
            if process.state is ProcessState.RUNNING:
                process.transition(
                    ProcessState.BLOCKED,
                    blocked_reason=f"budget_exceeded:{exceeded.limit}",
                )
                assert process.blocked_reason is not None
                self._blocked[process_id] = process.blocked_reason
            raise ProcessBudgetExceeded(process_id, point, exceeded)
        return process

    def check_budget(self, process_id: str) -> UsageLimitExceeded | None:
        """Evaluate process runtime usage against its optional budget."""

        if self._usage_collector is None:
            return None
        process = self._manager.get(process_id)
        return self._usage_collector.exceeded_budget(process_id, process.budget)

    def _index_process(self, process: ProcessControlBlock) -> None:
        if process.state is ProcessState.READY:
            self._enqueue_ready(process.process_id)
        elif process.state is ProcessState.WAITING and process.wait_reason is not None:
            self._waiting[process.process_id] = process.wait_reason
        elif (
            process.state is ProcessState.BLOCKED
            and process.blocked_reason is not None
        ):
            self._blocked[process.process_id] = process.blocked_reason

    def _enqueue_ready(self, process_id: str) -> None:
        process = self._manager.get(process_id)
        if process.state is not ProcessState.READY:
            raise RuntimeError(
                f"only READY processes can be enqueued, got {process.state}"
            )
        if process_id not in self._ready:
            self._ready.append(process_id)

    def _remove_from_ready(self, process_id: str) -> None:
        self._ready = deque(item for item in self._ready if item != process_id)

    def _cleanup_indexes(self, process_id: str) -> None:
        self._remove_from_ready(process_id)
        self._waiting.pop(process_id, None)
        self._blocked.pop(process_id, None)

    def _prune_ready_queue(self) -> None:
        kept: deque[str] = deque()
        seen: set[str] = set()
        for process_id in self._ready:
            if process_id in seen:
                continue
            seen.add(process_id)
            try:
                process = self._manager.get(process_id)
            except ProcessNotFound:
                continue
            if process.state is ProcessState.READY:
                kept.append(process_id)
        self._ready = kept

    def _pop_highest_priority_ready(self) -> str:
        ready = list(self._ready)
        best_index = 0
        best_priority = self._manager.get(ready[0]).priority
        for index, process_id in enumerate(ready[1:], start=1):
            priority = self._manager.get(process_id).priority
            if priority > best_priority:
                best_index = index
                best_priority = priority
        process_id = ready.pop(best_index)
        self._ready = deque(ready)
        return process_id

    def _next_dispatchable_process(self) -> ProcessControlBlock:
        self._prune_ready_queue()
        if not self._ready:
            raise NoRunnableProcess("no READY process is available")

        ready = list(self._ready)
        ordered = sorted(
            enumerate(ready),
            key=lambda item: (-self._manager.get(item[1]).priority, item[0]),
        )
        first_conflict: ProcessSessionConflict | None = None
        for _index, process_id in ordered:
            process = self._manager.get(process_id)
            try:
                self._ensure_session_writer_available(process)
            except ProcessSessionConflict as error:
                if first_conflict is None:
                    first_conflict = error
                continue
            return process
        assert first_conflict is not None
        raise first_conflict

    def _ensure_session_writer_available(
        self,
        process: ProcessControlBlock,
    ) -> None:
        for other in self._manager.list_processes():
            if other.process_id == process.process_id:
                continue
            if other.session_id != process.session_id:
                continue
            if other.state is ProcessState.RUNNING:
                raise ProcessSessionConflict(
                    "dispatch would create concurrent writers for session "
                    f"{process.session_id!r}: {other.process_id!r} is RUNNING"
                )
