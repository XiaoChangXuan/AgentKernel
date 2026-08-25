"""Run the offline AgentKernel runtime benchmark suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.common.metrics import BenchmarkRecord, records_as_dicts
from benchmarks.common.reporter import RESULTS_DIR, write_json_records
from benchmarks.context_vm.runner import run as run_context_vm
from benchmarks.durable_tool.runner import run as run_durable_tool
from benchmarks.recovery.runner import run as run_recovery
from benchmarks.resource_handle.runner import DEFAULT_SIZES_MB, run as run_resource


def run_all(*, resource_sizes_mb: tuple[int, ...] = DEFAULT_SIZES_MB) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    resource = run_resource(resource_sizes_mb)
    durable = run_durable_tool()
    recovery = run_recovery()
    context = run_context_vm()
    records.extend(resource)
    records.extend(durable)
    records.extend(recovery)
    records.extend(context)
    write_json_records("resource.json", resource)
    write_json_records("durable_tool.json", durable)
    write_json_records("recovery.json", recovery)
    write_json_records("context_vm.json", context)
    return records


def _parse_sizes(value: str) -> tuple[int, ...]:
    sizes = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not sizes or any(size <= 0 for size in sizes):
        raise argparse.ArgumentTypeError("sizes must be positive MiB integers")
    return sizes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-sizes-mb", type=_parse_sizes, default=DEFAULT_SIZES_MB)
    parser.add_argument("--output", default=str(RESULTS_DIR / "all.json"))
    args = parser.parse_args()

    records = run_all(resource_sizes_mb=tuple(args.resource_sizes_mb))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(records_as_dicts(records), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
