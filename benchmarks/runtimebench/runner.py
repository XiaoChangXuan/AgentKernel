"""Unified RuntimeBench V0.7 runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.common.reporter import RESULTS_DIR
from benchmarks.runtimebench.adapters import run_v0_7_families
from benchmarks.runtimebench.environment import (
    collect_environment,
    current_commit,
    generated_at,
)
from benchmarks.runtimebench.schema import RuntimeBenchDocument


DEFAULT_OUTPUT = RESULTS_DIR / "runtimebench_v0.7.json"


def run_runtimebench() -> RuntimeBenchDocument:
    return RuntimeBenchDocument(
        commit=current_commit(),
        generated_at=generated_at(),
        environment=collect_environment(),
        benchmarks=run_v0_7_families(),
    )


def write_runtimebench(
    document: RuntimeBenchDocument,
    output: str | Path = DEFAULT_OUTPUT,
) -> Path:
    path = Path(output)
    if not path.is_absolute():
        path = RESULTS_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    document = run_runtimebench()
    payload = document.as_dict()
    if args.no_write:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    output = write_runtimebench(document, args.output)
    print(output)


if __name__ == "__main__":
    main()
