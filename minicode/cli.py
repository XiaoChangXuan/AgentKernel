from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from agentkernel import JsonlSessionPersistence, Session

from .config import MiniCodeConfig
from .errors import MiniCodeError
from .loop import MiniCodeAgentLoop
from .model import MiniCodeModelResponse, ScriptedModelAdapter, scripted_response
from .trace import render_session_trace
from .workspace import discover_workspace


PHASE_2G_NOT_IMPLEMENTED = "not_implemented_in_phase_2f"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicode",
        description="MiniCode local coding harness.",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", type=Path, default=None)
        command.add_argument("--model", default="scripted")
        command.add_argument("--script-json", type=Path, default=None)
        command.add_argument("--session-path", type=Path, default=None)
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

    if args.command == "bench":
        payload = {
            "ok": False,
            "code": PHASE_2G_NOT_IMPLEMENTED,
            "command": args.command,
            "workspace": workspace.to_dict(),
            "message": "MiniCode IntegrationBench starts in Phase 2G.",
        }
        print(json.dumps(payload, sort_keys=True), file=out)
        return 1

    if args.command == "trace":
        if args.session_path is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_session_path",
                            "message": "trace requires --session-path",
                            "retryable": False,
                        },
                    },
                    sort_keys=True,
                ),
                file=err,
            )
            return 5
        session = Session.load(args.session_id, JsonlSessionPersistence(args.session_path))
        print(render_session_trace(session.events), file=out)
        return 0

    try:
        model = _model_from_args(args)
    except MiniCodeError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, sort_keys=True), file=err)
        return 5

    if args.command == "run":
        task = " ".join(args.task).strip()
        if not task:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_task",
                            "message": "run requires a task",
                            "retryable": False,
                        },
                    },
                    sort_keys=True,
                ),
                file=err,
            )
            return 5
        loop = MiniCodeAgentLoop(
            model=model,
            config=config,
            workspace=workspace,
            session_path=args.session_path,
        )
        result = asyncio.run(loop.run_async(task))
        print(json.dumps(result.to_dict(), sort_keys=True), file=out)
        return 0 if result.ok else 1

    if args.command == "resume":
        if args.session_path is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "missing_session_path",
                            "message": "resume requires --session-path",
                            "retryable": False,
                        },
                    },
                    sort_keys=True,
                ),
                file=err,
            )
            return 5
        loop = MiniCodeAgentLoop.resume(
            model=model,
            config=config,
            workspace=workspace,
            session_path=args.session_path,
            session_id=args.session_id,
        )
        result = asyncio.run(loop.run_async(None))
        print(json.dumps(result.to_dict(), sort_keys=True), file=out)
        return 0 if result.ok else 1

    parser.print_help(err)
    return 5


def _model_from_args(args: argparse.Namespace) -> ScriptedModelAdapter:
    if args.model != "scripted":
        raise MiniCodeError(
            "model_not_configured",
            "CLI Phase 2F supports deterministic scripted models only",
            retryable=False,
        )
    if args.script_json is None:
        raise MiniCodeError(
            "missing_script_json",
            "scripted CLI runs require --script-json",
            retryable=False,
        )
    try:
        raw = json.loads(args.script_json.read_text(encoding="utf-8"))
    except OSError as error:
        raise MiniCodeError(
            "script_not_found",
            f"could not read script: {args.script_json}",
            retryable=False,
        ) from error
    except json.JSONDecodeError as error:
        raise MiniCodeError("invalid_script", str(error), retryable=False) from error
    if not isinstance(raw, list):
        raise MiniCodeError("invalid_script", "script must be a list", retryable=False)
    responses: list[MiniCodeModelResponse] = []
    for item in raw:
        if not isinstance(item, dict):
            raise MiniCodeError(
                "invalid_script",
                "script entries must be objects",
                retryable=False,
            )
        responses.append(scripted_response(**item))
    return ScriptedModelAdapter(responses)
