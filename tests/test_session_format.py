from __future__ import annotations

import json

import pytest

from agentkernel import (
    SESSION_FORMAT_VERSION,
    EventType,
    JsonlSessionPersistence,
    Session,
    SessionCorruptionError,
    SessionEvent,
    SessionHeader,
    UnsupportedSessionFormatError,
)


def test_header_and_event_have_distinct_deterministic_jsonl_records(tmp_path) -> None:
    path = tmp_path / "session-1.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    event = session.append(EventType.TURN_START, {"turn": 1})
    session.flush()
    session.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    stored_event = json.loads(lines[1])

    assert header == {
        "record_type": "session/header",
        "format_version": SESSION_FORMAT_VERSION,
        "session_id": "session-1",
        "created_at": session.header.created_at,
    }
    assert stored_event == {"record_type": "session/event", **event.as_dict()}
    assert lines[0] == json.dumps(
        header,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_header_and_event_round_trip_without_semantic_loss() -> None:
    header = SessionHeader.create("session-1")
    event = SessionEvent(
        seq=1,
        type=EventType.ASSISTANT_MESSAGE,
        data={
            "turn": 1,
            "step": 1,
            "content": "",
            "tool_calls": [
                {
                    "call_id": "call-1",
                    "name": "nested",
                    "arguments": {"payload": {"items": [1, 2, None]}},
                }
            ],
        },
        time=123.5,
    )

    assert SessionHeader.from_dict(header.as_dict()) == header
    assert SessionEvent.from_dict(event.as_dict()) == event


def test_future_format_is_refused_before_current_header_shape_validation(
    tmp_path,
) -> None:
    path = tmp_path / "future.jsonl"
    path.write_text(
        json.dumps(
            {
                "record_type": "session/header",
                "format_version": SESSION_FORMAT_VERSION + 1,
                "future_shape": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(UnsupportedSessionFormatError) as captured:
        JsonlSessionPersistence(path).load("session-1")

    assert captured.value.found == SESSION_FORMAT_VERSION + 1


def test_requested_session_id_must_match_header(tmp_path) -> None:
    path = tmp_path / "session-a.jsonl"
    session = Session("session-b", JsonlSessionPersistence(path))
    session.close()

    with pytest.raises(SessionCorruptionError, match="does not match header"):
        Session.load("session-a", JsonlSessionPersistence(path))


def test_unknown_required_event_type_is_corruption(tmp_path) -> None:
    path = tmp_path / "unknown.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    session.close()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "record_type": "session/event",
                    "seq": 1,
                    "type": "future/required-event",
                    "data": {},
                    "time": 1.0,
                }
            )
            + "\n"
        )

    with pytest.raises(SessionCorruptionError, match="unknown session event type"):
        Session.load("session-1", JsonlSessionPersistence(path))


def test_malformed_non_tail_record_is_corruption(tmp_path) -> None:
    path = tmp_path / "malformed.jsonl"
    session = Session("session-1", JsonlSessionPersistence(path))
    session.close()
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"record_type":"session/event"\n')
        stream.write("{}\n")

    with pytest.raises(SessionCorruptionError, match="malformed JSONL record"):
        Session.load("session-1", JsonlSessionPersistence(path))
