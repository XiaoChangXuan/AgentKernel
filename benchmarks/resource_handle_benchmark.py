"""Deterministic Full vs V0.4 Pruning vs V0.5 Handle benchmark.

Default sizes stay laptop-friendly. Use ``--sizes-mb 10,100`` for the release
comparison requested by the V0.5 plan.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agentkernel import (
    ApproximateTokenEstimator,
    ContextPageKind,
    ContextProjector,
    JsonlSessionPersistence,
    LocalResourceStore,
    ResourceOwner,
    ResourceService,
    Session,
    ThresholdExternalizationPolicy,
    ToolCall,
    ToolExecutionContext,
    ToolResult,
    ToolResultExternalizer,
    ToolResultPruner,
    ToolResultPrunerConfig,
)
from agentkernel import EventType


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    strategy: str
    input_bytes: int
    model_visible_bytes: int
    estimated_context_tokens: int
    session_artifact_bytes: int
    resource_artifact_bytes: int
    raw_retained: bool
    restart_read_ok: bool
    read_latency_ms: float


def deterministic_payload(size_bytes: int) -> str:
    unit = "INFO request completed id=00000000 latency_ms=012\n"
    repeats, remainder = divmod(size_bytes, len(unit))
    return unit * repeats + unit[:remainder]


def run_benchmark(size_bytes: int, *, read_bytes: int = 64 * 1024) -> tuple[BenchmarkResult, ...]:
    payload = deterministic_payload(size_bytes)
    with tempfile.TemporaryDirectory(prefix="agentkernel-resource-bench-") as root:
        root_path = Path(root)
        full = _session_strategy(root_path / "full.jsonl", payload, prune=False)
        pruning = _session_strategy(root_path / "pruning.jsonl", payload, prune=True)
        handle = _handle_strategy(
            root_path / "handle.jsonl",
            root_path / "resources",
            payload,
            read_bytes=read_bytes,
        )
        return full, pruning, handle


def _session_strategy(path: Path, payload: str, *, prune: bool) -> BenchmarkResult:
    session = Session("bench-session", JsonlSessionPersistence(path))
    _append_tool_turn(session, payload)
    session.flush()
    page = next(
        page
        for page in ContextProjector(ApproximateTokenEstimator()).project(session)
        if page.kind is ContextPageKind.TOOL_RESULT
    )
    if prune:
        page = ToolResultPruner(
            ToolResultPrunerConfig(
                threshold_tokens=16_384,
                head_tokens=8_192,
                tail_tokens=4_096,
            )
        ).prune(page)
    content = page.message.content if page.message is not None else page.content
    artifact_bytes = path.stat().st_size
    raw_retained = json.loads(session.derive_messages()[-1].content)["output"] == payload
    session.close()
    restored = Session.load("bench-session", JsonlSessionPersistence(path))
    try:
        restart_read_ok = (
            json.loads(restored.derive_messages()[-1].content)["output"] == payload
        )
    finally:
        restored.close()
    return BenchmarkResult(
        strategy="V0.4 Pruning" if prune else "Full",
        input_bytes=len(payload.encode()),
        model_visible_bytes=len(content.encode()),
        estimated_context_tokens=ApproximateTokenEstimator().count_text(content),
        session_artifact_bytes=artifact_bytes,
        resource_artifact_bytes=0,
        raw_retained=raw_retained,
        restart_read_ok=restart_read_ok,
        read_latency_ms=0.0,
    )


def _handle_strategy(
    path: Path,
    resource_root: Path,
    payload: str,
    *,
    read_bytes: int,
) -> BenchmarkResult:
    resources = ResourceService(LocalResourceStore(resource_root))
    externalizer = ToolResultExternalizer(
        resources,
        ThresholdExternalizationPolicy(
            threshold_bytes=16_384,
            preview_head_bytes=8_192,
            preview_tail_bytes=4_096,
        ),
    )
    call = ToolCall("call-1", "benchmark.logs", {})
    context = ToolExecutionContext(
        "bench-agent", "bench-session", call.call_id, "op-benchmark"
    )
    projected = _run(externalizer.process(call, ToolResult.success(call, payload), context))
    session = Session("bench-session", JsonlSessionPersistence(path))
    _append_tool_turn(session, projected.output)
    session.flush()
    content = session.derive_messages()[-1].content
    session_bytes = path.stat().st_size
    session.close()
    uri = projected.output["resource"]["uri"]  # type: ignore[index]

    restarted = ResourceService(LocalResourceStore(resource_root))
    started = time.perf_counter()
    first = restarted.read(
        uri,
        owner=ResourceOwner("bench-agent", "bench-session"),
        limit=min(read_bytes, restarted.limits.max_read_bytes),
    )
    latency_ms = (time.perf_counter() - started) * 1_000
    resource_bytes = sum(
        item.stat().st_size for item in resource_root.rglob("*") if item.is_file()
    )
    return BenchmarkResult(
        strategy="V0.5 Handle",
        input_bytes=len(payload.encode()),
        model_visible_bytes=len(content.encode()),
        estimated_context_tokens=ApproximateTokenEstimator().count_text(content),
        session_artifact_bytes=session_bytes,
        resource_artifact_bytes=resource_bytes,
        raw_retained=(
            restarted.stat(
                uri, owner=ResourceOwner("bench-agent", "bench-session")
            ).size_bytes
            == len(payload.encode())
        ),
        restart_read_ok=first.data == payload.encode()[: len(first.data)],
        read_latency_ms=latency_ms,
    )


def _run(awaitable):  # type: ignore[no-untyped-def]
    import asyncio

    return asyncio.run(awaitable)


def _append_tool_turn(session: Session, output) -> None:  # type: ignore[no-untyped-def]
    call = ToolCall("call-1", "benchmark.logs", {})
    result = ToolResult.success(call, output)
    session.append(EventType.TURN_START, {"turn": 1})
    session.append(EventType.USER_MESSAGE, {"turn": 1, "content": "benchmark"})
    session.append(EventType.STEP_START, {"turn": 1, "step": 1})
    session.append(
        EventType.ASSISTANT_MESSAGE,
        {"turn": 1, "step": 1, "content": "", "tool_calls": [call.as_dict()]},
    )
    session.append(EventType.TOOL_CALL, {"turn": 1, "step": 1, **call.as_dict()})
    session.append(
        EventType.TOOL_RESULT,
        {"turn": 1, "step": 1, **result.as_dict()},
    )
    session.append(
        EventType.STEP_END,
        {"turn": 1, "step": 1, "outcome": "tool_calls"},
    )
    session.append(EventType.TURN_END, {"turn": 1, "reason": "completed"})


def _parse_sizes(value: str) -> tuple[int, ...]:
    sizes = tuple(int(float(part.strip()) * 1024 * 1024) for part in value.split(","))
    if not sizes or any(size < 1 for size in sizes):
        raise argparse.ArgumentTypeError("sizes must be positive MiB values")
    return sizes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes-mb",
        default="1,10",
        help="comma-separated payload sizes in MiB; use 10,100 for release runs",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = [
        result
        for size in _parse_sizes(args.sizes_mb)
        for result in run_benchmark(size)
    ]
    if args.json:
        print(json.dumps([asdict(row) for row in rows], indent=2))
        return
    print(
        "strategy | input | model bytes | est tokens | session bytes | "
        "resource bytes | raw | restart read | read ms"
    )
    for row in rows:
        print(
            f"{row.strategy} | {row.input_bytes} | {row.model_visible_bytes} | "
            f"{row.estimated_context_tokens} | {row.session_artifact_bytes} | "
            f"{row.resource_artifact_bytes} | {row.raw_retained} | "
            f"{row.restart_read_ok} | {row.read_latency_ms:.3f}"
        )


if __name__ == "__main__":
    main()
