"""JSON output helpers for offline benchmark runners."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .metrics import BenchmarkRecord, records_as_dicts


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def write_json_records(
    filename: str | Path,
    records: Iterable[BenchmarkRecord],
) -> Path:
    """Write records as a deterministic JSON array and return the path."""

    path = Path(filename)
    if not path.is_absolute():
        path = RESULTS_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = records_as_dicts(records)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def print_json_records(records: Iterable[BenchmarkRecord]) -> None:
    print(
        json.dumps(
            records_as_dicts(records),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
