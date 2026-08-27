from __future__ import annotations

import json

from agentkernel import EventType, Session
from minicode.trace import TraceRecorder, render_session_trace


def test_trace_recorder_writes_jsonl_and_redacts_secrets(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(jsonl_path=path)

    event = recorder.record(
        "model/request",
        {
            "authorization": "Bearer token",
            "nested": {"api_key": "sk-test"},
            "message": "hello",
        },
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert event.seq == 1
    assert rows[0]["data"]["authorization"] == "<redacted>"
    assert rows[0]["data"]["nested"]["api_key"] == "<redacted>"
    assert rows[0]["data"]["message"] == "hello"


def test_trace_human_text_is_observable_summary():
    recorder = TraceRecorder()
    recorder.record("tool/call", {"tool": "read_file", "call_id": "call-1"})

    text = recorder.human_text()

    assert "[Tool]" in text
    assert "read_file" in text
    assert "call-1" in text


def test_render_session_trace():
    session = Session("session-1")
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "fix"})
    session.append(
        EventType.TOOL_RESULT,
        {"call_id": "call-1", "name": "read_file", "ok": True, "output": "ok"},
    )

    rendered = render_session_trace(session.events)

    assert "user/message" in rendered
    assert "tool/result" in rendered
    assert "call-1" in rendered
