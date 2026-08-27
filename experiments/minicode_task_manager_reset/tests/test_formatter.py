from __future__ import annotations

from task_manager.formatter import format_task, format_tasks
from task_manager.models import Task


def test_format_pending_task() -> None:
    task = Task(id="a", title="Alpha", priority=2, due_date="2026-09-01", tags=("work",))

    assert format_task(task) == "[ ] a p2 due:2026-09-01 tags:work - Alpha"


def test_format_completed_task() -> None:
    task = Task(id="a", title="Alpha", completed=True)

    assert format_task(task).startswith("[x] a")


def test_format_multiple_tasks_one_per_line() -> None:
    output = format_tasks([Task(id="a", title="Alpha"), Task(id="b", title="Beta")])

    assert output.splitlines() == ["[ ] a p3 - Alpha", "[ ] b p3 - Beta"]
