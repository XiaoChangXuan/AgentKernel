from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run_tutorial(name: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "examples" / "tutorials" / name)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_v0_1_agent_spine_tutorial_runs() -> None:
    output = run_tutorial("v0_1_agent_spine.py")

    assert "V0.1 Agent Spine" in output
    assert "answer=final answer: 42" in output
    assert "tool/call" in output
    assert "tool/result" in output


def test_v0_2_recovery_tutorial_runs() -> None:
    output = run_tutorial("v0_2_recovery.py")

    assert "V0.2 Persistence / Recovery" in output
    assert "after_restart_status=completed" in output
    assert "lost_durable_facts=False" in output


def test_v0_3_durable_side_effect_tutorial_runs() -> None:
    output = run_tutorial("v0_3_durable_side_effect.py")

    assert "V0.3 Durable Tool WAL" in output
    assert "pre_recovery_classification=reconcile_required" in output
    assert "reconcile_status=succeeded" in output
    assert "external_effect_count=1" in output
    assert "committed=True" in output
