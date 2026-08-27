from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence, TextIO, TypeVar

from agentkernel import JsonlSessionPersistence, Session

from .config import MiniCodeConfig, MiniCodeProjectConfig, load_environment_files, load_project_config
from .errors import MiniCodeError
from .loop import MiniCodeAgentLoop
from .model import (
    MiniCodeModelResponse,
    ModelAdapter,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
    ScriptedModelAdapter,
    scripted_response,
)
from .trace import render_session_trace
from .workspace import discover_workspace


PHASE_2G_NOT_IMPLEMENTED = "not_implemented_in_phase_2f"
T = TypeVar("T")
EXIT_COMMANDS = {"/exit", "/quit", ":q"}
CHAT_STATUS_INTERVAL_SECONDS = 0.25
COMMON_DEFAULTS = {
    "workspace": None,
    "config": None,
    "model": None,
    "script_json": None,
    "base_url": None,
    "model_name": None,
    "session_path": None,
    "max_turns": None,
    "timeout_ms": None,
    "approve": None,
    "trace_jsonl": None,
    "allow_network": None,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minicode",
        description="MiniCode local coding harness.",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--workspace", type=Path, default=None)
        command.add_argument("--config", type=Path, default=None)
        command.add_argument("--model", choices=["scripted", "openai-compatible"], default=None)
        command.add_argument("--script-json", type=Path, default=None)
        command.add_argument("--base-url", default=None)
        command.add_argument("--model-name", default=None)
        command.add_argument("--session-path", type=Path, default=None)
        command.add_argument("--max-turns", type=int, default=None)
        command.add_argument("--timeout-ms", type=int, default=None)
        command.add_argument("--approve", choices=["never", "on-mutation", "always"], default=None)
        command.add_argument("--trace-jsonl", type=Path, default=None)
        network = command.add_mutually_exclusive_group()
        network.add_argument("--allow-network", action="store_true", default=None)
        network.add_argument("--no-network", dest="allow_network", action="store_false")

    run = subparsers.add_parser("run", help="Start a coding task.")
    add_common_options(run)
    run.add_argument("task", nargs="*")

    chat = subparsers.add_parser("chat", help="Start an interactive MiniCode prompt.")
    add_common_options(chat)

    resume = subparsers.add_parser("resume", help="Resume a MiniCode session.")
    add_common_options(resume)
    resume.add_argument("session_id")

    trace = subparsers.add_parser("trace", help="Inspect an observable trace.")
    add_common_options(trace)
    trace.add_argument("session_id")

    bench = subparsers.add_parser("bench", help="Run MiniCode validation suites.")
    add_common_options(bench)
    bench.add_argument("--suite", choices=["integration", "phase2f"], default="integration")
    bench.add_argument("--json", action="store_true")
    bench.add_argument("--json-output", type=Path, default=None)
    bench.add_argument("--no-write", action="store_true")

    return parser


def main(
    argv: Sequence[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    inp = stdin if stdin is not None else sys.stdin
    parser = build_parser()

    args = parser.parse_args(argv)
    _ensure_common_args(args)
    if args.command is None:
        args.command = "chat"

    try:
        workspace = discover_workspace(explicit_workspace=args.workspace)
        env_file_values = load_environment_files(workspace.root)
        project_config = load_project_config(workspace.root, explicit_config=args.config)
        config = _config_from_args(args, project_config, env_file_values)
        config.validate()
    except MiniCodeError as exc:
        _print_minicode_error(exc, err, human=args.command == "chat")
        return 5

    if args.command == "bench":
        if args.suite == "phase2f":
            from benchmarks.minicode import (
                DEFAULT_OUTPUT,
                format_human_report,
                run_phase2f_validation,
                write_phase2f_validation,
            )

            document = run_phase2f_validation()
            payload = document.as_dict()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=out)
            else:
                print(format_human_report(document), file=out)
            if not args.no_write:
                write_phase2f_validation(document, args.json_output or DEFAULT_OUTPUT)
            return 0 if payload["summary"]["decision"] == "PASS" else 1

        payload = {
            "ok": False,
            "code": PHASE_2G_NOT_IMPLEMENTED,
            "command": args.command,
            "suite": args.suite,
            "workspace": workspace.to_dict(),
            "message": "MiniCode frozen IntegrationBench starts in Phase 2G. Use --suite phase2f for Phase 2F validation.",
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=out)
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
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=err,
            )
            return 5
        session = Session.load(args.session_id, JsonlSessionPersistence(args.session_path))
        print(render_session_trace(session.events), file=out)
        return 0

    try:
        model = _model_from_args(
            args,
            project_config,
            env_file_values,
            model_mode=config.model,
            allow_network=not config.no_network,
            timeout_ms=config.timeout_ms,
        )
    except MiniCodeError as exc:
        _print_minicode_error(exc, err, human=args.command == "chat")
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
                    ensure_ascii=False,
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
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True), file=out)
        return 0 if result.ok else 1

    if args.command == "chat":
        return _run_chat(
            args,
            config=config,
            workspace=workspace,
            model=model,
            stdin=inp,
            stdout=out,
            stderr=err,
        )

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
                    ensure_ascii=False,
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
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True), file=out)
        return 0 if result.ok else 1

    parser.print_help(err)
    return 5


def _run_chat(
    args: argparse.Namespace,
    *,
    config: MiniCodeConfig,
    workspace,
    model: ModelAdapter,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    print("MiniCode interactive", file=stdout)
    print(f"workspace: {workspace.root}", file=stdout)
    print("type /exit or /quit, or press Esc, to leave", file=stdout)
    print("", file=stdout)

    session: Session | None = None
    while True:
        try:
            task = _read_interactive_line("minicode> ", stdin=stdin, stdout=stdout)
        except KeyboardInterrupt:
            print("", file=stdout)
            print("interrupted", file=stdout)
            return 130
        if task is None:
            print("bye", file=stdout)
            return 0
        task = task.strip()
        if not task:
            continue
        if task.lower() in EXIT_COMMANDS:
            print("bye", file=stdout)
            return 0

        loop = MiniCodeAgentLoop(
            model=model,
            config=config,
            workspace=workspace,
            session=session,
            session_path=args.session_path,
        )
        result = _run_chat_task_with_status(
            loop,
            task,
            stdin=stdin,
            stdout=stdout,
        )
        session = loop.session
        print(_format_human_result(result), file=stdout)
        print("", file=stdout)
        if not result.ok:
            print(
                f"MiniCode stopped with {result.status.value}"
                + (f" ({result.reason})" if result.reason else ""),
                file=stderr,
            )


def _run_chat_task_with_status(
    loop: MiniCodeAgentLoop,
    task: str,
    *,
    stdin: TextIO,
    stdout: TextIO,
):
    renderer = _ChatStatusRenderer(
        stdout=stdout,
        interactive=stdin is sys.stdin and stdout is sys.stdout and _has_tty(stdout),
    )
    started = time.monotonic()
    interrupt_requested = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(loop.run_async(task)))
        renderer.render(
            elapsed_seconds=0,
            message="starting task",
            interrupt_requested=False,
        )
        while not future.done():
            interrupt_requested = _maybe_request_running_interrupt(
                loop,
                stdin=stdin,
                stdout=stdout,
                already_requested=interrupt_requested,
            )
            for seq, message in _new_chat_progress_messages(loop, renderer.last_trace_seq):
                renderer.last_trace_seq = seq
                renderer.render(
                    elapsed_seconds=int(time.monotonic() - started),
                    message=message,
                    interrupt_requested=interrupt_requested,
                )
            if renderer.should_refresh(time.monotonic()):
                renderer.render(
                    elapsed_seconds=int(time.monotonic() - started),
                    message=renderer.current_message,
                    interrupt_requested=interrupt_requested,
                )
            time.sleep(min(CHAT_STATUS_INTERVAL_SECONDS, 0.05))
        result = future.result()

    for seq, message in _new_chat_progress_messages(loop, renderer.last_trace_seq):
        renderer.last_trace_seq = seq
        renderer.render(
            elapsed_seconds=int(time.monotonic() - started),
            message=message,
            interrupt_requested=interrupt_requested,
        )
    renderer.finish(elapsed_seconds=int(time.monotonic() - started), success=result.ok)
    return result


class _ChatStatusRenderer:
    def __init__(self, *, stdout: TextIO, interactive: bool) -> None:
        self.stdout = stdout
        self.interactive = interactive
        self.last_trace_seq = 0
        self.current_message = "starting task"
        self._last_rendered_at = 0.0
        self._last_line_width = 0

    def should_refresh(self, now: float) -> bool:
        return now - self._last_rendered_at >= CHAT_STATUS_INTERVAL_SECONDS

    def render(
        self,
        *,
        elapsed_seconds: int,
        message: str,
        interrupt_requested: bool,
    ) -> None:
        self.current_message = message
        self._last_rendered_at = time.monotonic()
        text = _chat_status_text(
            elapsed_seconds=elapsed_seconds,
            message=message,
            interrupt_requested=interrupt_requested,
            interactive=self.interactive,
        )
        if self.interactive:
            padded = text.ljust(self._last_line_width)
            self._last_line_width = max(self._last_line_width, len(text))
            print("\r" + padded, end="", file=self.stdout, flush=True)
            return
        print(text, file=self.stdout, flush=True)

    def finish(self, *, elapsed_seconds: int, success: bool) -> None:
        label = "Done" if success else "Failed"
        text = f"{label} ({elapsed_seconds}s)"
        if self.interactive:
            padded = text.ljust(self._last_line_width)
            print("\r" + padded, file=self.stdout, flush=True)
            return
        print(text, file=self.stdout, flush=True)


def _new_chat_progress_messages(loop: MiniCodeAgentLoop, after_seq: int) -> list[tuple[int, str]]:
    messages: list[tuple[int, str]] = []
    for event in loop.trace.events:
        if event.seq <= after_seq:
            continue
        messages.append((event.seq, _chat_progress_message(event.type, event.data)))
    return messages


def _chat_progress_message(event_type: str, data: object) -> str:
    if not isinstance(data, dict):
        data = {}
    turn = data.get("turn")
    step = data.get("step")
    suffix = _turn_step_suffix(turn, step)
    if event_type == "task/start":
        return "starting task"
    if event_type == "model/request":
        return f"asking model{suffix}"
    if event_type == "model/response":
        tool_calls = data.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return f"model requested tools: {', '.join(str(tool) for tool in tool_calls)}"
        return f"model answered{suffix}"
    if event_type == "model/error":
        code = data.get("code") or "provider_error"
        return f"model error: {code}"
    if event_type == "tool/call":
        tool = data.get("tool") or "tool"
        detail = _tool_progress_detail(data.get("arguments"))
        return f"running tool: {tool}{detail}{suffix}"
    if event_type == "tool/result":
        tool = data.get("tool") or "tool"
        ok = "ok" if data.get("ok") is True else "failed"
        return f"tool finished: {tool} ({ok})"
    if event_type == "task/completed":
        return "final answer ready"
    if event_type == "recovery/required":
        return "recovery required"
    return event_type.replace("/", ": ")


def _turn_step_suffix(turn: object, step: object) -> str:
    if isinstance(turn, int) and isinstance(step, int):
        return f" (turn {turn}, step {step})"
    if isinstance(turn, int):
        return f" (turn {turn})"
    return ""


def _tool_progress_detail(arguments: object) -> str:
    if not isinstance(arguments, dict):
        return ""
    if "command" in arguments:
        parts = [f"command={arguments.get('command')}"]
        cwd = arguments.get("cwd")
        if cwd not in {None, "", "."}:
            parts.append(f"cwd={cwd}")
        mutation = arguments.get("mutation_intent")
        if mutation not in {None, "", "read_only"}:
            parts.append(f"intent={mutation}")
        return ": " + ", ".join(parts)
    if "path" in arguments and "query" in arguments:
        return f": query={arguments.get('query')}, path={arguments.get('path')}"
    if "path" in arguments:
        detail = f": path={arguments.get('path')}"
        start = arguments.get("start_line")
        end = arguments.get("end_line")
        if start is not None or end is not None:
            detail += f", lines={start or ''}-{end or ''}"
        return detail
    if "patch_chars" in arguments:
        return f": patch_chars={arguments.get('patch_chars')}"
    keys = arguments.get("keys")
    if isinstance(keys, list) and keys:
        return f": args={','.join(str(key) for key in keys[:6])}"
    return ""


def _chat_status_text(
    *,
    elapsed_seconds: int,
    message: str,
    interrupt_requested: bool,
    interactive: bool,
) -> str:
    if interrupt_requested:
        hint = "interrupt requested"
    elif interactive and os.name == "nt":
        hint = "Esc to interrupt"
    else:
        hint = "Ctrl+C to interrupt"
    return f"Working ({elapsed_seconds}s • {hint}) - {message}"


def _maybe_request_running_interrupt(
    loop: MiniCodeAgentLoop,
    *,
    stdin: TextIO,
    stdout: TextIO,
    already_requested: bool,
) -> bool:
    if already_requested:
        return True
    if not (stdin is sys.stdin and stdout is sys.stdout and _is_windows_tty(stdin)):
        return False
    import msvcrt

    while msvcrt.kbhit():
        char = msvcrt.getwch()
        if char == "\x1b":
            loop.scheduler.request_cancel(loop.process_id, "user_interrupt")
            return True
        if char == "\x03":
            loop.scheduler.request_cancel(loop.process_id, "user_interrupt")
            return True
        if char in {"\x00", "\xe0"} and msvcrt.kbhit():
            msvcrt.getwch()
    return False


def _read_interactive_line(prompt: str, *, stdin: TextIO, stdout: TextIO) -> str | None:
    if stdin is sys.stdin and stdout is sys.stdout and _is_windows_tty(stdin):
        return _read_windows_console_line(prompt)

    print(prompt, end="", file=stdout, flush=True)
    line = stdin.readline()
    if line == "":
        return None
    if line.strip() == "\x1b":
        return None
    return line.rstrip("\r\n")


def _read_windows_console_line(prompt: str) -> str | None:
    import msvcrt

    print(prompt, end="", flush=True)
    chars: list[str] = []
    while True:
        char = msvcrt.getwch()
        if char == "\x1b":
            print("")
            return None
        if char in {"\r", "\n"}:
            print("")
            return "".join(chars)
        if char == "\x03":
            raise KeyboardInterrupt
        if char in {"\x00", "\xe0"}:
            msvcrt.getwch()
            continue
        if char == "\b":
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        chars.append(char)
        print(char, end="", flush=True)


def _is_windows_tty(stdin: TextIO) -> bool:
    return os.name == "nt" and hasattr(stdin, "isatty") and stdin.isatty()


def _has_tty(stream: TextIO) -> bool:
    return hasattr(stream, "isatty") and stream.isatty()


def _format_human_result(result) -> str:
    if result.ok:
        return result.final_message or "(completed with no final message)"
    message = f"MiniCode error: {result.status.value}"
    if result.reason:
        message += f" ({result.reason})"
    if getattr(result, "error_detail", None):
        message += f"\nprovider detail: {result.error_detail}"
    return message


def _print_minicode_error(exc: MiniCodeError, stderr: TextIO, *, human: bool) -> None:
    if human:
        print(f"MiniCode configuration error: {exc.code}: {exc}", file=stderr)
        return
    print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, sort_keys=True), file=stderr)


def _ensure_common_args(args: argparse.Namespace) -> None:
    for name, value in COMMON_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def _config_from_args(
    args: argparse.Namespace,
    project_config: MiniCodeProjectConfig,
    env_file_values: Mapping[str, str],
) -> MiniCodeConfig:
    env_model = _env_str("MINICODE_MODEL", env_file_values)
    if env_model is None and _has_openai_compatible_env_config(env_file_values):
        env_model = "openai-compatible"
    allow_network = _first_present(
        args.allow_network,
        _first_present(_env_bool("MINICODE_ALLOW_NETWORK", env_file_values), project_config.allow_network, False),
        False,
    )
    return MiniCodeConfig(
        workspace=args.workspace,
        model=_first_present(args.model, _first_present(env_model, project_config.model, "scripted"), "scripted"),
        max_turns=_first_present(
            args.max_turns,
            _first_present(_env_int("MINICODE_MAX_TURNS", env_file_values), project_config.max_turns, 20),
            20,
        ),
        timeout_ms=_first_present(
            args.timeout_ms,
            _first_present(_env_int("MINICODE_TIMEOUT_MS", env_file_values), project_config.timeout_ms, 30_000),
            30_000,
        ),
        approve=_first_present(
            args.approve,
            _first_present(_env_approval("MINICODE_APPROVE", env_file_values), project_config.approve, "on-mutation"),
            "on-mutation",
        ),
        trace_jsonl=args.trace_jsonl,
        no_network=not allow_network,
    )


def _model_from_args(
    args: argparse.Namespace,
    project_config: MiniCodeProjectConfig,
    env_file_values: Mapping[str, str],
    *,
    model_mode: str,
    allow_network: bool,
    timeout_ms: int,
) -> ModelAdapter:
    if model_mode == "openai-compatible":
        return _openai_compatible_model_from_args(
            args,
            project_config,
            env_file_values,
            allow_network=allow_network,
            timeout_ms=timeout_ms,
        )
    if model_mode != "scripted":
        raise MiniCodeError("unsupported_model", f"unsupported model: {model_mode}", retryable=False)
    return _scripted_model_from_args(args)


def _scripted_model_from_args(args: argparse.Namespace) -> ScriptedModelAdapter:
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


def _openai_compatible_model_from_args(
    args: argparse.Namespace,
    project_config: MiniCodeProjectConfig,
    env_file_values: Mapping[str, str],
    *,
    allow_network: bool,
    timeout_ms: int,
) -> OpenAICompatibleAdapter:
    if not allow_network:
        raise MiniCodeError(
            "network_not_allowed",
            "openai-compatible model runs require explicit --allow-network, MINICODE_ALLOW_NETWORK=true, or .minicode/config.json allow_network",
            retryable=False,
        )
    base_url = (
        args.base_url
        or _env_str("MINICODE_LLM_BASE_URL", env_file_values)
        or _env_str("AGENTKERNEL_LLM_BASE_URL", env_file_values)
        or project_config.base_url
    )
    model = (
        args.model_name
        or _env_str("MINICODE_LLM_MODEL", env_file_values)
        or _env_str("AGENTKERNEL_LLM_MODEL", env_file_values)
        or project_config.model_name
    )
    if not base_url:
        raise MiniCodeError(
            "missing_model_config",
            "openai-compatible model requires --base-url, MINICODE_LLM_BASE_URL, AGENTKERNEL_LLM_BASE_URL, or .minicode/config.json openai_compatible.base_url",
            retryable=False,
        )
    if not model:
        raise MiniCodeError(
            "missing_model_config",
            "openai-compatible model requires --model-name, MINICODE_LLM_MODEL, AGENTKERNEL_LLM_MODEL, or .minicode/config.json openai_compatible.model",
            retryable=False,
        )
    return OpenAICompatibleAdapter(
        OpenAICompatibleConfig(
            base_url=base_url,
            model=model,
            api_key=(
                _env_str("MINICODE_LLM_API_KEY", env_file_values)
                or _env_str("AGENTKERNEL_LLM_API_KEY", env_file_values)
                or project_config.api_key
            ),
            timeout_seconds=timeout_ms / 1000,
            enabled=True,
        )
    )


def _first_present(first: T | None, second: T | None, third: T) -> T:
    if first is not None:
        return first
    if second is not None:
        return second
    return third


def _env_str(name: str, env_file_values: Mapping[str, str]) -> str | None:
    value = os.environ.get(name)
    if value is None:
        value = env_file_values.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str, env_file_values: Mapping[str, str]) -> bool | None:
    value = _env_str(name, env_file_values)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MiniCodeError(
        "invalid_configuration",
        f"{name} must be true or false",
        retryable=False,
    )


def _env_int(name: str, env_file_values: Mapping[str, str]) -> int | None:
    value = _env_str(name, env_file_values)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise MiniCodeError(
            "invalid_configuration",
            f"{name} must be a positive integer",
            retryable=False,
        ) from exc
    if parsed <= 0:
        raise MiniCodeError(
            "invalid_configuration",
            f"{name} must be a positive integer",
            retryable=False,
        )
    return parsed


def _env_approval(name: str, env_file_values: Mapping[str, str]) -> str | None:
    value = _env_str(name, env_file_values)
    if value is None:
        return None
    if value in {"never", "on-mutation", "always"}:
        return value
    raise MiniCodeError(
        "invalid_configuration",
        f"{name} must be never, on-mutation, or always",
        retryable=False,
    )


def _has_openai_compatible_env_config(env_file_values: Mapping[str, str]) -> bool:
    return bool(
        (_env_str("MINICODE_LLM_BASE_URL", env_file_values) or _env_str("AGENTKERNEL_LLM_BASE_URL", env_file_values))
        and (_env_str("MINICODE_LLM_MODEL", env_file_values) or _env_str("AGENTKERNEL_LLM_MODEL", env_file_values))
    )


if __name__ == "__main__":
    raise SystemExit(main())
