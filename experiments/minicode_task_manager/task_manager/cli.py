from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from .formatter import format_task, format_tasks
from .service import TaskService
from .storage import JsonTaskStore
from .utils import parse_tags


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="task-manager")
    parser.add_argument("--store", type=Path, default=Path("tasks.json"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("id")
    create.add_argument("title")
    create.add_argument("--description", default="")
    create.add_argument("--due")
    create.add_argument("--priority", type=int, default=3)
    create.add_argument("--tags", default="")

    update = subparsers.add_parser("update")
    update.add_argument("id")
    update.add_argument("--title")
    update.add_argument("--description")
    update.add_argument("--due")
    update.add_argument("--priority", type=int)
    update.add_argument("--tags")

    complete = subparsers.add_parser("complete")
    complete.add_argument("id")

    list_cmd = subparsers.add_parser("list")
    list_cmd.add_argument("--completed", action="store_true")
    list_cmd.add_argument("--pending", action="store_true")
    list_cmd.add_argument("--tag")
    list_cmd.add_argument("--sort", choices=["id", "priority", "due"], default="id")
    return parser


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    service = TaskService(JsonTaskStore(args.store))
    try:
        if args.command == "create":
            task = service.create_task(
                task_id=args.id,
                title=args.title,
                description=args.description,
                due=args.due,
                priority=args.priority,
                tags=parse_tags(args.tags),
            )
            print(format_task(task), file=out)
            return 0
        if args.command == "update":
            task = service.update_task(
                args.id,
                title=args.title,
                description=args.description,
                due=args.due,
                priority=args.priority,
                tags=parse_tags(args.tags) if args.tags is not None else None,
            )
            print(format_task(task), file=out)
            return 0
        if args.command == "complete":
            task = service.complete_task(args.id)
            print(format_task(task), file=out)
            return 0
        if args.command == "list":
            completed = True if args.completed else False if args.pending else None
            tasks = service.list_tasks(completed=completed, tag=args.tag, sort_by=args.sort)
            print(format_tasks(tasks), file=out)
            return 0
    except ValueError as error:
        print(f"error: {error}", file=err)
        return 2
    except KeyError as error:
        print(f"error: task not found: {error.args[0]}", file=err)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
