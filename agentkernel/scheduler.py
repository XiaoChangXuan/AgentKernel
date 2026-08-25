"""Cooperative process scheduling primitives for V0.7."""

from __future__ import annotations

from collections import deque
from enum import StrEnum

from .accounting import UsageCollector, UsageLimitExceeded
from .agent import AgentControlBlock
from .process import ProcessControlBlock, ProcessState


class SchedulerError(RuntimeError):
    """Base class for cooperative scheduler failures."""


class ProcessAlreadyExists(SchedulerError):
    """Raised when a process id is registered twice."""


class ProcessNotFound(SchedulerError):
    """Raised when a process id is not present in the process table."""


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


class ProcessManager:
    """Kernel-owned live process table."""

    def __init__(self) -> None:
        self._processes: dict[str, ProcessControlBlock] = {}

    def create_process(
        self,
        *,
        process_id: str,
        agent: AgentControlBlock,
        parent_process_id: str | None = None,
        priority: int = 0,
        admit: bool = True,
    ) -> ProcessControlBlock:
        """Create and optionally admit a process into READY state."""

        process = ProcessControlBlock.create(
            process_id=process_id,
            agent=agent,
            parent_process_id=parent_process_id,
            priority=priority,
        )
        self.register(process)
        if admit:
            process.transition(ProcessState.READY)
        return process

    def register(self, process: ProcessControlBlock) -> None:
        """Add a ProcessControlBlock to the live process table."""

        if process.process_id in self._processes:
            raise ProcessAlreadyExists(
                f"process already exists: {process.process_id}"
            )
        self._processes[process.process_id] = process

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

    def delete_exited_process(self, process_id: str) -> ProcessControlBlock:
        """Remove one exited process from the process table."""

        process = self.get(process_id)
        if process.state is not ProcessState.EXITED:
            raise RuntimeError(
                f"cannot delete non-exited process {process_id}: {process.state}"
            )
        return self._processes.pop(process_id)

    def delete_exited_processes(self) -> tuple[ProcessControlBlock, ...]:
        """Remove all exited processes from the process table."""

        deleted: list[ProcessControlBlock] = []
        for process_id, process in list(self._processes.items()):
            if process.state is ProcessState.EXITED:
                deleted.append(process)
                del self._processes[process_id]
        return tuple(deleted)


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
            self.schedule_next()
            if process_id is None
            else self._manager.get(process_id)
        )
        if process.state is not ProcessState.READY:
            raise RuntimeError(
                f"only READY processes can be dispatched, got {process.state}"
            )
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
