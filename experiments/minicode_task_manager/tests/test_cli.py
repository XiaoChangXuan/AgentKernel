from __future__ import annotations

import io
from task_manager.cli import main


def test_cli_create_outputs_task(tmp_path) -> None:
    stdout = io.StringIO()
    exit_code = main(
        ["--store", str(tmp_path / "tasks.json"), "create", "a", "Alpha", "--priority", "2"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert "[ ] a p2 - Alpha" in stdout.getvalue()


def test_cli_list_outputs_persisted_tasks(tmp_path) -> None:
    store = tmp_path / "tasks.json"
    main(["--store", str(store), "create", "a", "Alpha"], stdout=io.StringIO())
    stdout = io.StringIO()

    exit_code = main(["--store", str(store), "list"], stdout=stdout)

    assert exit_code == 0
    assert "Alpha" in stdout.getvalue()


def test_cli_complete_outputs_completed_task(tmp_path) -> None:
    store = tmp_path / "tasks.json"
    main(["--store", str(store), "create", "a", "Alpha"], stdout=io.StringIO())
    stdout = io.StringIO()

    exit_code = main(["--store", str(store), "complete", "a"], stdout=stdout)

    assert exit_code == 0
    assert stdout.getvalue().startswith("[x] a")


def test_cli_missing_task_returns_nonzero(tmp_path) -> None:
    stderr = io.StringIO()

    exit_code = main(["--store", str(tmp_path / "tasks.json"), "complete", "missing"], stderr=stderr)

    assert exit_code != 0
    assert "task not found" in stderr.getvalue()


def test_cli_invalid_priority_returns_error(tmp_path) -> None:
    stderr = io.StringIO()

    exit_code = main(
        ["--store", str(tmp_path / "tasks.json"), "create", "a", "Alpha", "--priority", "9"],
        stderr=stderr,
    )

    assert exit_code == 2
    assert "priority" in stderr.getvalue()

