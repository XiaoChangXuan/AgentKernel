from __future__ import annotations

from datetime import date

import pytest

from task_manager.parser import parse_due_date


BASE = date(2026, 8, 27)


def test_parse_none_due_date() -> None:
    assert parse_due_date(None, base_date=BASE) is None


def test_parse_today() -> None:
    assert parse_due_date("today", base_date=BASE) == "2026-08-27"


def test_parse_relative_days() -> None:
    assert parse_due_date("+3d", base_date=BASE) == "2026-08-30"


def test_parse_relative_zero_days_means_today() -> None:
    assert parse_due_date("+0d", base_date=BASE) == "2026-08-27"


def test_parse_iso_date() -> None:
    assert parse_due_date("2026-09-03", base_date=BASE) == "2026-09-03"


def test_reject_invalid_calendar_date() -> None:
    with pytest.raises(ValueError):
        parse_due_date("2026-02-30", base_date=BASE)

