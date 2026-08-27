from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_demo_without_provider(name: str) -> str:
    env = os.environ.copy()
    env["AGENTKERNEL_RUN_REAL_MODEL"] = "0"
    env.pop("AGENTKERNEL_LLM_BASE_URL", None)
    env.pop("AGENTKERNEL_LLM_MODEL", None)
    env.pop("AGENTKERNEL_LLM_API_KEY", None)
    completed = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "examples" / "real_agent" / name)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout


def test_real_agent_demos_skip_without_explicit_opt_in() -> None:
    for name in (
        "basic_tool_trace.py",
        "capability_denial_trace.py",
        "resource_handle_trace.py",
    ):
        output = run_demo_without_provider(name)

        assert "SKIPPED: real-model demos are opt-in." in output
        assert "AGENTKERNEL_RUN_REAL_MODEL=1" in output
