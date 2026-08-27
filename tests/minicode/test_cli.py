from __future__ import annotations

import io
import json

from agentkernel import EventType, JsonlSessionPersistence, Session
from minicode.cli import PHASE_2G_NOT_IMPLEMENTED, main
from minicode.testing import make_minicode_workspace


def test_cli_run_requires_script_json(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    stderr = io.StringIO()

    exit_code = main(
        ["run", "--workspace", str(fixture.root), "fix calculator.py"],
        stderr=stderr,
    )

    assert exit_code == 5
    assert "missing_script_json" in stderr.getvalue()


def test_cli_scripted_run_completes(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    script = tmp_path / "script.json"
    session_path = tmp_path / "session.jsonl"
    script.write_text(json.dumps([{"text": "done"}]), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--script-json",
            str(script),
            "--session-path",
            str(session_path),
            "fix calculator.py",
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["status"] == "completed"


def test_cli_invalid_workspace_returns_configuration_error(tmp_path):
    stderr = io.StringIO()

    exit_code = main(["run", "--workspace", str(tmp_path / "missing"), "fix"], stderr=stderr)

    assert exit_code == 5
    assert "invalid_workspace" in stderr.getvalue()


def test_cli_trace_requires_session_path(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    stderr = io.StringIO()

    exit_code = main(
        ["trace", "--workspace", str(fixture.root), "session-1"],
        stderr=stderr,
    )

    assert exit_code == 5
    assert "missing_session_path" in stderr.getvalue()


def test_cli_trace_renders_session_events(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    session_path = tmp_path / "session.jsonl"
    session = Session("session-1", JsonlSessionPersistence(session_path))
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "hello"})
    session.append(EventType.TURN_END, {"turn": 1, "reason": "test"})
    session.close()
    stdout = io.StringIO()

    exit_code = main(
        [
            "trace",
            "--workspace",
            str(fixture.root),
            "--session-path",
            str(session_path),
            "session-1",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    assert "user/message" in stdout.getvalue()
    assert "hello" in stdout.getvalue()


def test_cli_bench_is_phase_2g_stub(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    stdout = io.StringIO()

    exit_code = main(["bench", "--workspace", str(fixture.root)], stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["code"] == PHASE_2G_NOT_IMPLEMENTED
    assert payload["suite"] == "integration"


def test_cli_bench_phase2f_runs_validation_suite(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    output = tmp_path / "phase2f.json"
    stdout = io.StringIO()

    exit_code = main(
        [
            "bench",
            "--workspace",
            str(fixture.root),
            "--suite",
            "phase2f",
            "--json-output",
            str(output),
        ],
        stdout=stdout,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert "MiniCode Phase 2F Validation" in stdout.getvalue()
    assert payload["suite"] == "minicode_phase2f_validation"
    assert payload["summary"] == {"decision": "PASS", "failed": 0, "passed": 8, "total": 8}


def test_cli_bench_phase2f_json_no_write(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    output = tmp_path / "phase2f.json"
    stdout = io.StringIO()

    exit_code = main(
        [
            "bench",
            "--workspace",
            str(fixture.root),
            "--suite",
            "phase2f",
            "--json",
            "--json-output",
            str(output),
            "--no-write",
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["suite"] == "minicode_phase2f_validation"
    assert output.exists() is False
