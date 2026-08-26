"""Environment metadata for RuntimeBench reports."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def generated_at() -> str:
    return datetime.now(UTC).isoformat()


def current_commit(repo_root: Path = REPO_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def collect_environment() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
