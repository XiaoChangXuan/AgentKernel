from __future__ import annotations

import re
from datetime import date, timedelta


RELATIVE_DUE_RE = re.compile(r"^\+(?P<days>\d+)d$")


def parse_due_date(value: str | None, *, base_date: date | None = None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    today = base_date or date.today()
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    match = RELATIVE_DUE_RE.match(text)
    if match:
        days = max(1, int(match.group("days")))
        return (today + timedelta(days=days)).isoformat()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    raise ValueError(f"unsupported due date: {value}")
