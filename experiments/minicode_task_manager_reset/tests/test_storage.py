from __future__ import annotations

from task_manager.models import Task
from task_manager.storage import JsonTaskStore


def test_load_missing_store_returns_empty_list(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks.json")

    assert store.load() == []


def test_save_and_load_roundtrip_basic_task(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks.json")
    task = Task(id="a", title="Write docs", due_date="2026-08-30", priority=2)

    store.save([task])

    assert store.load()[0].to_dict() == task.to_dict()


def test_save_uses_utf8_json(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks.json")
    store.save([Task(id="plain", title="Plain title")])

    raw = (tmp_path / "tasks.json").read_text(encoding="utf-8")

    assert "\\u" not in raw
    assert "Plain title" in raw


def test_roundtrip_preserves_tags(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks.json")
    task = Task(id="tagged", title="Review release", tags=("release", "docs"))

    store.save([task])

    assert store.load()[0].tags == ("release", "docs")
