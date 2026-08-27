from __future__ import annotations

import json

import pytest

from task_manager.service import TaskService
from task_manager.storage import JsonTaskStore


def make_service(tmp_path) -> TaskService:
    return TaskService(JsonTaskStore(tmp_path / "tasks.json"))


def seed_tasks(service: TaskService) -> None:
    service.create_task(task_id="b", title="Beta", due="2026-09-02", priority=3, tags=("work",))
    service.create_task(task_id="a", title="Alpha", due="2026-09-01", priority=1, tags=("home",))
    service.create_task(task_id="c", title="Gamma", priority=5, tags=("work", "urgent"))
    service.complete_task("b")


def test_create_task_persists_task(tmp_path) -> None:
    service = make_service(tmp_path)

    service.create_task(task_id="a", title="Alpha", due="2026-09-01", priority=1)

    tasks = service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == "a"


def test_duplicate_task_id_is_rejected(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create_task(task_id="a", title="Alpha")

    with pytest.raises(ValueError):
        service.create_task(task_id="a", title="Replacement")


def test_update_task_changes_existing_task(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create_task(task_id="a", title="Alpha")

    updated = service.update_task("a", title="Alpha updated", priority=2)

    assert updated.title == "Alpha updated"
    assert updated.priority == 2


def test_complete_task_marks_only_requested_task(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create_task(task_id="a", title="Alpha")
    service.create_task(task_id="b", title="Beta")

    service.complete_task("b")
    states = {task.id: task.completed for task in service.list_tasks()}

    assert states == {"a": False, "b": True}


def test_completed_filter_returns_completed_tasks(tmp_path) -> None:
    service = make_service(tmp_path)
    seed_tasks(service)

    assert [task.id for task in service.list_tasks(completed=True)] == ["b"]


def test_pending_filter_returns_pending_tasks(tmp_path) -> None:
    service = make_service(tmp_path)
    seed_tasks(service)

    assert [task.id for task in service.list_tasks(completed=False)] == ["a", "c"]


def test_tag_filter_is_case_insensitive(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "title": "Alpha", "tags": ["home"]},
                {"id": "b", "title": "Beta", "tags": ["work"]},
                {"id": "c", "title": "Gamma", "tags": ["work", "urgent"]},
            ]
        ),
        encoding="utf-8",
    )
    service = TaskService(JsonTaskStore(path))

    assert [task.id for task in service.list_tasks(tag="WORK")] == ["b", "c"]


def test_sort_by_priority_ascending_then_id(tmp_path) -> None:
    service = make_service(tmp_path)
    seed_tasks(service)

    assert [task.id for task in service.list_tasks(sort_by="priority")] == ["a", "b", "c"]


def test_sort_by_due_date_ascending_with_none_last(tmp_path) -> None:
    service = make_service(tmp_path)
    seed_tasks(service)

    assert [task.id for task in service.list_tasks(sort_by="due")] == ["a", "b", "c"]


def test_unknown_task_raises_key_error(tmp_path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(KeyError):
        service.complete_task("missing")
