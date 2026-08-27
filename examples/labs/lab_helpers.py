"""Small display helpers for AgentKernel interactive labs.

The labs avoid notebook-only dependencies such as pandas.  These helpers keep
the teaching cells compact while still showing structured runtime facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def print_table(rows: Iterable[Mapping[str, Any]]) -> None:
    """Print a tiny deterministic Markdown table."""

    materialized = [dict(row) for row in rows]
    if not materialized:
        print("(no rows)")
        return
    headers = list(materialized[0])
    widths = {
        header: max(
            len(str(header)),
            *(len(_cell(row.get(header, ""))) for row in materialized),
        )
        for header in headers
    }
    print("| " + " | ".join(header.ljust(widths[header]) for header in headers) + " |")
    print("| " + " | ".join("-" * widths[header] for header in headers) + " |")
    for row in materialized:
        print(
            "| "
            + " | ".join(_cell(row.get(header, "")).ljust(widths[header]) for header in headers)
            + " |"
        )


def event_rows(session: Any) -> list[dict[str, Any]]:
    """Return Session events as compact rows for teaching notebooks."""

    return [
        {
            "seq": event.seq,
            "type": event.type.value,
            "data": _compact(event.data),
        }
        for event in session.events
    ]


def process_row(process: Any) -> dict[str, Any]:
    """Return one ProcessControlBlock as a table row."""

    return {
        "process_id": process.process_id,
        "agent_id": process.agent_id,
        "session_id": process.session_id,
        "state": process.state.value,
        "blocked_reason": process.blocked_reason or "",
        "parent_process_id": process.parent_process_id or "",
    }


def grant_rows(grants: Iterable[Any]) -> list[dict[str, Any]]:
    """Return CapabilityGrant values as compact rows."""

    return [
        {
            "subject": grant.subject,
            "action": grant.action,
            "scope": grant.resource_scope,
            "constraints": dict(grant.constraints),
        }
        for grant in grants
    ]


def trajectory(*steps: str) -> None:
    """Print an observable runtime trajectory."""

    for index, step in enumerate(steps):
        if index:
            print("↓")
        print(step)


def _cell(value: Any) -> str:
    return str(value).replace("\n", " ")


def _compact(value: Any, limit: int = 120) -> str:
    text = _cell(value)
    if len(text) <= limit:
        return text
    return text[: limit - 15] + " ... [cut]"
