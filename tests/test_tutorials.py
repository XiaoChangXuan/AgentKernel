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


def test_v0_4_context_vm_tutorial_runs() -> None:
    output = run_tutorial("v0_4_context_vm.py")

    assert "V0.4 Context VM" in output
    assert "durable_messages=14" in output
    assert "evicted_pages=" in output
    assert "model_messages=2" in output
    assert "context_equals_truth=False" in output


def test_v0_5_resource_handle_tutorial_runs() -> None:
    output = run_tutorial("v0_5_resource_handle.py")

    assert "V0.5 Resource Handle" in output
    assert "handle_uri=artifact://res_" in output
    assert "resource_bytes=32000" in output
    assert "has_more=True" in output
    assert "restart_read_success=True" in output


def test_v0_6_capability_core_tutorial_runs() -> None:
    output = run_tutorial("v0_6_capability_core.py")

    assert "V0.6 Capability Core" in output
    assert "visible_tools_allowed=math.add" in output
    assert "visible_tools_denied=0" in output
    assert "allowed_result=42" in output
    assert "denied_ok=False" in output
    assert "matches_eacces=True" in output


def test_v0_7_process_runtime_tutorial_runs() -> None:
    output = run_tutorial("v0_7_process_runtime.py")

    assert "V0.7 Process Runtime" in output
    assert "capability_principal=tutorial-agent" in output
    assert "budget_blocked=True" in output
    assert "observed_tokens=6" in output
    assert "after_unblock_state=READY" in output


def test_v0_8_multi_agent_runtime_tutorial_runs() -> None:
    output = run_tutorial("v0_8_multi_agent_runtime.py")

    assert "V0.8 Multi-Agent Runtime" in output
    assert "lineage=agent-parent/agent-child" in output
    assert "child_initial_grants=0" in output
    assert "before_delegation_ok=False" in output
    assert "delegation_allowed=True" in output
    assert "after_delegation_result=5" in output
