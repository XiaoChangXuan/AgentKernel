from __future__ import annotations

from collections.abc import Iterable

from .models import Task
from .parser import parse_due_date
from .storage import JsonTaskStore


class TaskService:
    def __init__(self, store: JsonTaskStore) -> None:
        self.store = store

    def create_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str = "",
        due: str | None = None,
        priority: int = 3,
        tags: Iterable[str] = (),
    ) -> Task:
        tasks = self.store.load()
        task = Task(
            id=task_id,
            title=title,
            description=description,
            due_date=parse_due_date(due),
            priority=priority,
            tags=tuple(tags),
        )
        remaining = [existing for existing in tasks if existing.id != task.id]
        remaining.append(task)
        self.store.save(remaining)
        return task

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        due: str | None = None,
        priority: int | None = None,
        tags: Iterable[str] | None = None,
    ) -> Task:
        tasks = self.store.load()
        task = self._find(tasks, task_id)
        task.update(
            title=title,
            description=description,
            due_date=parse_due_date(due) if due is not None else None,
            priority=priority,
            tags=tuple(tags) if tags is not None else None,
        )
        self.store.save(tasks)
        return task

    def complete_task(self, task_id: str) -> Task:
        tasks = self.store.load()
        task = self._find(tasks, task_id)
        task.complete()
        self.store.save(tasks)
        return task

    def list_tasks(
        self,
        *,
        completed: bool | None = None,
        tag: str | None = None,
        sort_by: str = "id",
    ) -> list[Task]:
        tasks = self.store.load()
        if completed is not None:
            tasks = [task for task in tasks if task.completed is not completed]
        if tag is not None:
            wanted = tag.strip().lower()
            tasks = [task for task in tasks if wanted in task.tags]
        if sort_by == "id":
            return sorted(tasks, key=lambda task: task.id)
        if sort_by == "priority":
            return sorted(tasks, key=lambda task: (task.priority, task.id))
        if sort_by == "due":
            return sorted(tasks, key=lambda task: (task.due_date is None, task.due_date or ""), reverse=True)
        raise ValueError(f"unsupported sort: {sort_by}")

    @staticmethod
    def _find(tasks: list[Task], task_id: str) -> Task:
        for task in tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)
