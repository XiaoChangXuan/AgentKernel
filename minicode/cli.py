from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from .config import MiniCodeConfig
from .errors import MiniCodeError
from .workspace import discover_workspace


NOT_IMPLEMENTED = "not_implemented_in_phase_2a"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicode",
        description="MiniCode local coding harness. Phase 2A provides workspace validation only.",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", type=Path, default=None)
        command.add_argument("--model", default="scripted")
        command.add_argument("--max-turns", type=int, default=20)
        command.add_argument("--timeout-ms", type=int, default=30_000)
        command.add_argument("--approve", choices=["never", "on-mutation", "always"], default="on-mutation")
        command.add_argument("--trace-jsonl", type=Path, default=None)
        command.add_argument("--no-network", action="store_true", default=True)

    run = subparsers.add_parser("run", help="Start a coding task.")
    add_common_options(run)
    run.add_argument("task", nargs="*")

    resume = subparsers.add_parser("resume", help="Resume a MiniCode session.")
    add_common_options(resume)
    resume.add_argument("session_id")

    trace = subparsers.add_parser("trace", help="Inspect an observable trace.")
    add_common_options(trace)
    trace.add_argument("session_id")

    bench = subparsers.add_parser("bench", help="Run MiniCode IntegrationBench.")
    add_common_options(bench)

    return parser


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = build_parser()

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(err)
        return 5

    config = MiniCodeConfig(
        workspace=args.workspace,
        model=args.model,
        max_turns=args.max_turns,
        timeout_ms=args.timeout_ms,
        approve=args.approve,
        trace_jsonl=args.trace_jsonl,
        no_network=args.no_network,
    )

    try:
        config.validate()
        workspace = discover_workspace(explicit_workspace=config.workspace)
    except MiniCodeError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, sort_keys=True), file=err)
        return 5

    payload = {
        "ok": False,
        "code": NOT_IMPLEMENTED,
        "command": args.command,
        "workspace": workspace.to_dict(),
        "message": "MiniCode Phase 2A only implements package, CLI parsing, and workspace core.",
    }
    print(json.dumps(payload, sort_keys=True), file=out)
    return 1
