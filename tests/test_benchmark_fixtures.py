from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from benchmarks.coding_fixture_runner import prepare_workspace
from benchmarks.context_real_provider_benchmark import CASES, run


def test_context_benchmark_offline_is_deterministic_and_network_free() -> None:
    first = asyncio.run(run(real=False))
    second = asyncio.run(run(real=False))

    assert first["status"] == "offline_completed"
    assert len(first["resource_metrics"]) == len(CASES) * 3
    assert first["quality"] == second["quality"]
    for row in first["resource_metrics"]:
        assert row["actual_input_tokens"] is None
        assert row["total_actual_input_tokens"] is None
        assert row["summary_actual_input_tokens"] is None
        assert row["request_estimated_tokens"] > 0
    phase23 = [
        row for row in first["resource_metrics"] if row["mode"] == "phase23"
    ]
    assert all(row["selected_tokens"] < row["projected_tokens"] for row in phase23)
    assert all(row["success"] for row in phase23)


def test_coding_fixture_runner_prepares_an_isolated_reproducible_task() -> None:
    prepared = prepare_workspace()
    workspace = Path(prepared["workspace"])
    try:
        task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))

        assert prepared["status"] == "runner_seam_ready"
        assert workspace.is_dir()
        assert (workspace / "calculator.py").is_file()
        assert (workspace / "test_calculator.py").is_file()
        assert task["expected_initial_result"] == "failed"
        assert task["expected_final_result"] == "passed"
    finally:
        shutil.rmtree(workspace)
