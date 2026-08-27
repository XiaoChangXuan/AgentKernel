from __future__ import annotations

from .models import Task


def format_task(task: Task) -> str:
    status = "x" if task.completed else " "
    due = f" due:{task.due_date}" if task.due_date else ""
    tags = f" tags:{','.join(task.tags)}" if task.tags else ""
    return f"[{status}] {task.id} p{task.priority}{due}{tags} - {task.title}"


def format_tasks(tasks: list[Task]) -> str:
    if not tasks:
        return "No tasks"
    return "\n".join(format_task(task) for task in tasks)
