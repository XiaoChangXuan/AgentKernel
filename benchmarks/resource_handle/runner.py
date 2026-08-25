"""Benchmark V0.5 Artifact Handle behavior for large tool results."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from agentkernel import (
    AgentBudget,
    AgentControlBlock,
    AgentState,
    LocalResourceStore,
    ResourceLimits,
    ResourceOwner,
    ResourceService,
    ToolCall,
    ToolRegistry,
    resource_tool_definitions,
)

from benchmarks.common.fixtures import (
    estimate_tokens_from_bytes,
    mib,
    repeated_ascii_payload,
    retained_by_head_tail,
)
from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records
from benchmarks.resource_handle.fixture import ResourceCase, make_resource_case


BENCHMARK = "resource_handle"
DEFAULT_SIZES_MB = (10, 100, 500)
PREVIEW_HEAD_BYTES = 8 * 1024
PREVIEW_TAIL_BYTES = 4 * 1024
READ_LIMIT = 64
OWNER = ResourceOwner("bench-agent", "bench-session")


def run(sizes_mb: tuple[int, ...] = DEFAULT_SIZES_MB) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for size_mb in sizes_mb:
        case = make_resource_case(mib(size_mb))
        records.extend(
            (
                _full_tool_result(case),
                _pruned_tool_result(case),
                _artifact_handle(case),
            )
        )
    return records


def _full_tool_result(case: ResourceCase) -> BenchmarkRecord:
    context_bytes = _tool_result_context_bytes(case.size_bytes)
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case=case.name,
        strategy="full_tool_result",
        metrics={
            "context_bytes": context_bytes,
            "estimated_tokens": estimate_tokens_from_bytes(context_bytes),
            "resource_bytes": 0,
            "read_latency_ms": 0.0,
            "restart_recovery": False,
            "success": True,
        },
    )


def _pruned_tool_result(case: ResourceCase) -> BenchmarkRecord:
    retained = [
        retained_by_head_tail(
            size_bytes=case.size_bytes,
            offset=offset,
            head_bytes=PREVIEW_HEAD_BYTES,
            tail_bytes=PREVIEW_TAIL_BYTES,
        )
        for offset in case.required_offsets
    ]
    omitted = max(0, case.size_bytes - PREVIEW_HEAD_BYTES - PREVIEW_TAIL_BYTES)
    marker = f"\n\n... omitted {omitted} bytes ...\n\n".encode("utf-8")
    context_bytes = _tool_result_context_bytes(
        min(case.size_bytes, PREVIEW_HEAD_BYTES + PREVIEW_TAIL_BYTES)
        + len(marker)
    )
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case=case.name,
        strategy="v0_4_pruning",
        metrics={
            "context_bytes": context_bytes,
            "estimated_tokens": estimate_tokens_from_bytes(context_bytes),
            "resource_bytes": 0,
            "retained_information_correct": all(retained),
            "retained_required_offsets": sum(1 for item in retained if item),
            "required_offsets": len(retained),
            "success": all(retained),
        },
    )


def _artifact_handle(case: ResourceCase) -> BenchmarkRecord:
    with tempfile.TemporaryDirectory(prefix="agentkernel-resource-bench-") as root:
        root_path = Path(root)
        limits = ResourceLimits(
            max_resource_bytes=case.size_bytes,
            max_read_bytes=64 * 1024,
        )
        resources = ResourceService(LocalResourceStore(root_path), limits=limits)
        payload = repeated_ascii_payload(case.size_bytes)
        handle = resources.create_artifact(
            payload,
            owner=OWNER,
            media_type="application/octet-stream",
            encoding="binary",
            source_tool_name="benchmark.large_result",
            source_tool_call_id="call-resource-benchmark",
            source_operation_id="op-resource-benchmark",
        )
        del payload

        projection = {
            "externalized": True,
            "preview": _preview_text(case.size_bytes),
            "omitted_bytes": max(
                0, case.size_bytes - PREVIEW_HEAD_BYTES - PREVIEW_TAIL_BYTES
            ),
            "resource": handle.as_dict(),
        }
        context_bytes = _tool_result_context_bytes(
            len(json.dumps(projection, sort_keys=True).encode("utf-8"))
        )

        read_timer = Timer()
        first_read_ok = _read_required_offsets(resources, handle.uri, case)
        read_latency_ms = read_timer.elapsed_ms()

        restarted = ResourceService(LocalResourceStore(root_path), limits=limits)
        restart_ok = _read_required_offsets(restarted, handle.uri, case)
        stored_bytes = sum(
            item.stat().st_size for item in root_path.rglob("*") if item.is_file()
        )
        metrics = resources.metrics.snapshot()
        return BenchmarkRecord(
            benchmark=BENCHMARK,
            case=case.name,
            strategy="artifact_handle",
            metrics={
                "context_bytes": context_bytes,
                "estimated_tokens": estimate_tokens_from_bytes(context_bytes),
                "resource_bytes": metrics.resource_bytes_stored,
                "store_bytes": stored_bytes,
                "read_latency_ms": read_latency_ms,
                "restart_recovery": restart_ok,
                "resource_reads": metrics.resource_reads,
                "resource_bytes_read": metrics.resource_bytes_read,
                "success": first_read_ok and restart_ok,
            },
        )


def _read_required_offsets(
    resources: ResourceService,
    uri: str,
    case: ResourceCase,
) -> bool:
    agent = AgentControlBlock(
        agent_id=OWNER.agent_id,
        session_id=OWNER.session_id,
        state=AgentState.READY,
        parent_agent_id=None,
        capabilities=frozenset({"resource.read"}),
        capability_bounding_set=frozenset({"resource.read"}),
        budget=AgentBudget(),
    )
    tools = ToolRegistry()
    for definition in resource_tool_definitions(resources):
        tools.register(definition)
    for index, offset in enumerate(case.required_offsets, start=1):
        call = ToolCall(
            f"call-resource-read-{index}",
            "resource_read",
            {"uri": uri, "offset": offset, "limit": READ_LIMIT},
        )
        result = asyncio.run(tools.execute(call, agent))
        if not result.ok or not isinstance(result.output, dict):
            return False
        if result.output.get("returned_bytes") != min(READ_LIMIT, case.size_bytes - offset):
            return False
    return True


def _tool_result_context_bytes(output_bytes: int) -> int:
    envelope_bytes = len(
        json.dumps(
            {
                "role": "tool",
                "tool_call_id": "call-resource-benchmark",
                "name": "benchmark.large_result",
                "content": "",
            },
            sort_keys=True,
        ).encode("utf-8")
    )
    return envelope_bytes + output_bytes


def _preview_text(size_bytes: int) -> str:
    omitted = max(0, size_bytes - PREVIEW_HEAD_BYTES - PREVIEW_TAIL_BYTES)
    return (
        "x" * PREVIEW_HEAD_BYTES
        + f"\n\n... omitted {omitted} bytes; use resource_read ...\n\n"
        + "x" * PREVIEW_TAIL_BYTES
    )


def _parse_sizes(value: str) -> tuple[int, ...]:
    sizes = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not sizes or any(size <= 0 for size in sizes):
        raise argparse.ArgumentTypeError("sizes must be positive MiB integers")
    return sizes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes-mb", type=_parse_sizes, default=DEFAULT_SIZES_MB)
    parser.add_argument("--output", default="resource.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run(tuple(args.sizes_mb))
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
