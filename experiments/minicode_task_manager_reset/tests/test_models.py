from __future__ import annotations

import pytest

from task_manager.models import Task


def test_task_requires_id() -> None:
    with pytest.raises(ValueError):
        Task(id=" ", title="Write release notes")


def test_task_requires_non_empty_title() -> None:
    with pytest.raises(ValueError):
        Task(id="docs", title="   ")


def test_task_preserves_unicode_title() -> None:
    task = Task(id="i18n", title="修复登录")

    assert task.title == "修复登录"


def test_task_normalizes_internal_whitespace() -> None:
    task = Task(id="docs", title="  Write   release   notes  ")

    assert task.title == "Write release notes"


def test_task_rejects_invalid_priority() -> None:
    with pytest.raises(ValueError):
        Task(id="urgent", title="Fix outage", priority=0)


def test_task_complete_sets_status() -> None:
    task = Task(id="ship", title="Ship CLI")

    task.complete()

    assert task.completed is True
