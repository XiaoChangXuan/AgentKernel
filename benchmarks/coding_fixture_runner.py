"""Prepare an isolated coding benchmark workspace without agent orchestration."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "coding_agent_bug"


def prepare_workspace() -> dict[str, str]:
    target = Path(tempfile.mkdtemp(prefix="agentkernel-coding-benchmark-"))
    shutil.copytree(FIXTURE, target, dirs_exist_ok=True)
    task = json.loads((target / "task.json").read_text(encoding="utf-8"))
    return {
        "workspace": str(target),
        "task": task["task"],
        "constraint": task["constraint"],
        "test_command": task["test_command"],
        "status": "runner_seam_ready",
    }


if __name__ == "__main__":
    print(json.dumps(prepare_workspace(), ensure_ascii=False, indent=2))
