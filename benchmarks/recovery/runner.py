"""Benchmark V0.2 Session replay recovery across crash prefixes."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from agentkernel import JsonlSessionPersistence, Session

from benchmarks.common.metrics import BenchmarkRecord, Timer
from benchmarks.common.reporter import print_json_records, write_json_records
from benchmarks.recovery.crash_injector import CRASH_POINTS, CrashPoint, append_until


BENCHMARK = "recovery"


def run() -> list[BenchmarkRecord]:
    return [_run_point(point) for point in CRASH_POINTS]


def _run_point(point: CrashPoint) -> BenchmarkRecord:
    with tempfile.TemporaryDirectory(prefix="agentkernel-recovery-bench-") as root:
        path = Path(root) / "session.jsonl"
        session = Session("bench-session", JsonlSessionPersistence(path))
        append_until(session, point)
        event_count = len(session.events)
        session.close()

        timer = Timer()
        restarted = Session.load("bench-session", JsonlSessionPersistence(path))
        analysis = restarted.recovery_analysis
        replay_ms = timer.elapsed_ms()
        durable_classification = (
            analysis.durable_operations[0].classification.value
            if analysis.durable_operations
            else None
        )
        duplicate_action = (
            point.external_success
            and durable_classification not in {"reconcile_required", "completed"}
        )
        lost_events = max(0, event_count - analysis.last_event_seq)
        success = (
            lost_events == 0
            and not duplicate_action
            and analysis.status.value in {"interrupted", "completed"}
        )
        restarted.close()
    return BenchmarkRecord(
        benchmark=BENCHMARK,
        case=point.name,
        strategy="agentkernel_replay",
        metrics={
            "lost_events": lost_events,
            "recovery_status": analysis.status.value,
            "last_event_seq": analysis.last_event_seq,
            "last_event_type": (
                analysis.last_event_type.value
                if analysis.last_event_type is not None
                else None
            ),
            "active_turn": analysis.active_turn,
            "active_step": analysis.active_step,
            "pending_tool_calls": len(analysis.pending_tool_calls),
            "durable_operation_classification": durable_classification,
            "replay_time_ms": replay_ms,
            "duplicate_action": duplicate_action,
            "success": success,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="recovery.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    records = run()
    if not args.no_write:
        write_json_records(args.output, records)
    print_json_records(records)


if __name__ == "__main__":
    main()
