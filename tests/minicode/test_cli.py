from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.error

from agentkernel import EventType, JsonlSessionPersistence, Session
from minicode.cli import PHASE_2G_NOT_IMPLEMENTED, main
from minicode.testing import make_minicode_workspace


def _pytest_command() -> str:
    return subprocess.list2cmdline([sys.executable, "-m", "pytest", "-q"])


def _openai_response(*, text: str = "", tool_calls: list[dict[str, object]] | None = None) -> dict[str, object]:
    calls = tool_calls or []
    return {
        "choices": [
            {
                "finish_reason": "tool_calls" if calls else "stop",
                "message": {"content": text, "tool_calls": calls},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def _openai_tool_call(call_id: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False, sort_keys=True),
        },
    }


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


def test_cli_run_requires_script_json(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    stderr = io.StringIO()

    exit_code = main(
        ["run", "--workspace", str(fixture.root), "fix calculator.py"],
        stderr=stderr,
    )

    assert exit_code == 5
    assert "missing_script_json" in stderr.getvalue()


def test_cli_openai_compatible_requires_network_opt_in(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    stderr = io.StringIO()
    monkeypatch.setenv("MINICODE_LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("MINICODE_LLM_MODEL", "demo-model")

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "fix calculator.py",
        ],
        stderr=stderr,
    )

    assert exit_code == 5
    assert "network_not_allowed" in stderr.getvalue()


def test_cli_openai_compatible_requires_base_url_and_model(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    stderr = io.StringIO()
    monkeypatch.delenv("MINICODE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MINICODE_LLM_MODEL", raising=False)

    missing_base = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "--allow-network",
            "--model-name",
            "demo-model",
            "fix calculator.py",
        ],
        stderr=stderr,
    )

    assert missing_base == 5
    assert "MINICODE_LLM_BASE_URL" in stderr.getvalue()

    stderr = io.StringIO()
    missing_model = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "--allow-network",
            "--base-url",
            "https://provider.example/v1",
            "fix calculator.py",
        ],
        stderr=stderr,
    )

    assert missing_model == 5
    assert "MINICODE_LLM_MODEL" in stderr.getvalue()


def test_cli_openai_compatible_reads_environment_config(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    stdout = io.StringIO()
    seen_requests: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        seen_requests.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return _FakeHTTPResponse(_openai_response(text="done"))

    monkeypatch.setenv("MINICODE_LLM_BASE_URL", "https://env-provider.example/v1")
    monkeypatch.setenv("MINICODE_LLM_MODEL", "env-model")
    monkeypatch.delenv("MINICODE_LLM_API_KEY", raising=False)
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "--allow-network",
            "finish immediately",
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    body = seen_requests[0]["body"]
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert seen_requests[0]["url"] == "https://env-provider.example/v1/chat/completions"
    assert seen_requests[0]["authorization"] is None
    assert seen_requests[0]["timeout"] == 30.0
    assert body["model"] == "env-model"  # type: ignore[index]
    assert body["messages"][-1] == {"role": "user", "content": "finish immediately"}  # type: ignore[index]


def test_cli_openai_compatible_reads_project_config(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    config_dir = fixture.root / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "model": "openai-compatible",
                "allow_network": True,
                "openai_compatible": {
                    "base_url": "https://project-provider.example/v1",
                    "model": "project-model",
                },
                "defaults": {
                    "timeout_ms": 12_000,
                    "max_turns": 3,
                    "approve": "never",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    seen_requests: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        seen_requests.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return _FakeHTTPResponse(_openai_response(text="configured"))

    monkeypatch.delenv("MINICODE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MINICODE_LLM_MODEL", raising=False)
    monkeypatch.delenv("MINICODE_LLM_API_KEY", raising=False)
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "finish from project config",
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    body = seen_requests[0]["body"]
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["final_message"] == "configured"
    assert seen_requests[0]["url"] == "https://project-provider.example/v1/chat/completions"
    assert seen_requests[0]["timeout"] == 12.0
    assert body["model"] == "project-model"  # type: ignore[index]


def test_cli_project_config_must_not_contain_api_key(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    config_dir = fixture.root / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "model": "openai-compatible",
                "allow_network": True,
                "openai_compatible": {
                    "base_url": "https://project-provider.example/v1",
                    "model": "project-model",
                    "api_key": "sk-do-not-store-this",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    stderr = io.StringIO()

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "finish from project config",
        ],
        stderr=stderr,
    )

    assert exit_code == 5
    assert "config_contains_secret" in stderr.getvalue()
    assert "sk-do-not-store-this" not in stderr.getvalue()


def test_cli_openai_compatible_malformed_provider_response_is_model_error(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    stdout = io.StringIO()

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        return _FakeHTTPResponse({"not_choices": []})

    monkeypatch.setenv("MINICODE_LLM_BASE_URL", "https://env-provider.example/v1")
    monkeypatch.setenv("MINICODE_LLM_MODEL", "env-model")
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "--allow-network",
            "finish immediately",
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["status"] == "model_error"
    assert payload["reason"] == "malformed_provider_response"


def test_cli_openai_compatible_connection_error_is_structured_model_error(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    stdout = io.StringIO()

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("connection refused")

    monkeypatch.setenv("MINICODE_LLM_BASE_URL", "https://env-provider.example/v1")
    monkeypatch.setenv("MINICODE_LLM_MODEL", "env-model")
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "--allow-network",
            "finish immediately",
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["status"] == "model_error"
    assert payload["reason"] == "provider_error"
    assert "Traceback" not in stdout.getvalue()


def test_cli_openai_compatible_runs_fake_provider_tool_loop(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    (fixture.root / "tests" / "test_calculator.py").write_text(
        "from calculator import divide\n\n"
        "def test_divide():\n"
        "    assert divide(8, 2) == 4\n\n"
        "def test_zero_division_returns_none():\n"
        "    assert divide(1, 0) is None\n",
        encoding="utf-8",
    )
    session_path = tmp_path / "session.jsonl"
    trace_path = tmp_path / "trace.jsonl"
    stdout = io.StringIO()
    stderr = io.StringIO()
    responses = [
        _openai_response(
            tool_calls=[
                _openai_tool_call("call-read", "read_file", {"path": "calculator.py"}),
            ]
        ),
        _openai_response(
            tool_calls=[
                _openai_tool_call(
                    "call-patch",
                    "apply_patch",
                    {
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Update File: calculator.py\n"
                            "@@\n"
                            "-    if b == 0:\n"
                            "-        raise ZeroDivisionError('division by zero')\n"
                            "-    return a / b\n"
                            "+    if b == 0:\n"
                            "+        return None\n"
                            "+    return a / b\n"
                            "*** End Patch"
                        )
                    },
                ),
            ]
        ),
        _openai_response(
            tool_calls=[
                _openai_tool_call(
                    "call-test",
                    "run_command",
                    {"command": _pytest_command(), "mutation_intent": "read_only"},
                ),
            ]
        ),
        _openai_response(text="fixed calculator.py"),
    ]
    seen_requests: list[dict[str, object]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        body = json.loads(request.data.decode("utf-8"))
        seen_requests.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "body": body,
                "timeout": timeout,
            }
        )
        return _FakeHTTPResponse(responses.pop(0))

    monkeypatch.setenv("MINICODE_LLM_BASE_URL", "https://env-provider.example/v1")
    monkeypatch.setenv("MINICODE_LLM_MODEL", "env-model")
    monkeypatch.setenv("MINICODE_LLM_API_KEY", "sk-minicode-test-secret")
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--session-path",
            str(session_path),
            "--trace-jsonl",
            str(trace_path),
            "--model",
            "openai-compatible",
            "--allow-network",
            "--base-url",
            "https://override-provider.example/v1",
            "--model-name",
            "override-model",
            "--approve",
            "always",
            "修复 calculator.py 并运行测试",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    payload = json.loads(stdout.getvalue())
    combined_output = stdout.getvalue() + stderr.getvalue()
    session_text = session_path.read_text(encoding="utf-8")
    trace_text = trace_path.read_text(encoding="utf-8")
    request_models = [request["body"]["model"] for request in seen_requests]  # type: ignore[index]

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["final_message"] == "fixed calculator.py"
    assert "return None" in fixture.calculator.read_text(encoding="utf-8")
    assert seen_requests[0]["url"] == "https://override-provider.example/v1/chat/completions"
    assert seen_requests[0]["authorization"] == "Bearer sk-minicode-test-secret"
    assert request_models == ["override-model"] * 4
    assert {"read_file", "apply_patch", "run_command"} <= {
        call["function"]["name"]
        for request in seen_requests
        for call in request["body"]["tools"]  # type: ignore[index]
    }
    assert "sk-minicode-test-secret" not in combined_output
    assert "sk-minicode-test-secret" not in session_text
    assert "sk-minicode-test-secret" not in trace_text
    assert responses == []


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


def test_cli_run_preserves_chinese_output(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"text": "MiniCode 已连接。"}], ensure_ascii=False), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        [
            "run",
            "--workspace",
            str(fixture.root),
            "--script-json",
            str(script),
            "测试中文输出",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    assert "MiniCode 已连接。" in stdout.getvalue()
    assert "\\u" not in stdout.getvalue()


def test_cli_chat_outputs_human_readable_chinese(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"text": "MiniCode 已连接。"}], ensure_ascii=False), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin = io.StringIO("你好\n/exit\n")

    exit_code = main(
        [
            "chat",
            "--workspace",
            str(fixture.root),
            "--script-json",
            str(script),
        ],
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "MiniCode interactive" in output
    assert "minicode> " in output
    assert "MiniCode 已连接。" in output
    assert "bye" in output
    assert '"final_message"' not in output
    assert "\\u" not in output
    assert stderr.getvalue() == ""


def test_cli_chat_reports_live_progress_while_running(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        time.sleep(0.05)
        return _FakeHTTPResponse(_openai_response(text="MiniCode 已完成。"))

    monkeypatch.setattr("minicode.cli.CHAT_STATUS_INTERVAL_SECONDS", 0.01)
    monkeypatch.setenv("MINICODE_LLM_BASE_URL", "https://env-provider.example/v1")
    monkeypatch.setenv("MINICODE_LLM_MODEL", "env-model")
    monkeypatch.delenv("MINICODE_LLM_API_KEY", raising=False)
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main(
        [
            "chat",
            "--workspace",
            str(fixture.root),
            "--model",
            "openai-compatible",
            "--allow-network",
        ],
        stdout=stdout,
        stderr=stderr,
        stdin=io.StringIO("你好\n/exit\n"),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Working (" in output
    assert "asking model" in output
    assert "Done (" in output
    assert "MiniCode 已完成。" in output
    assert '"final_message"' not in output
    assert stderr.getvalue() == ""


def test_cli_chat_exits_on_esc_from_stdin(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"text": "unused"}]), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        [
            "chat",
            "--workspace",
            str(fixture.root),
            "--script-json",
            str(script),
        ],
        stdout=stdout,
        stdin=io.StringIO("\x1b\n"),
    )

    assert exit_code == 0
    assert "bye" in stdout.getvalue()
    assert "unused" not in stdout.getvalue()


def test_cli_without_command_defaults_to_interactive_chat(tmp_path, monkeypatch):
    fixture = make_minicode_workspace(tmp_path)
    config_dir = fixture.root / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "model": "openai-compatible",
                "allow_network": True,
                "openai_compatible": {
                    "base_url": "https://project-provider.example/v1",
                    "model": "project-model",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        return _FakeHTTPResponse(_openai_response(text="默认进入交互模式。"))

    monkeypatch.chdir(fixture.root)
    monkeypatch.delenv("MINICODE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MINICODE_LLM_MODEL", raising=False)
    monkeypatch.delenv("MINICODE_LLM_API_KEY", raising=False)
    monkeypatch.setattr("minicode.model.urllib.request.urlopen", fake_urlopen)

    exit_code = main([], stdout=stdout, stdin=io.StringIO("你好\n/exit\n"))

    output = stdout.getvalue()
    assert exit_code == 0
    assert "默认进入交互模式。" in output
    assert '"ok"' not in output


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
