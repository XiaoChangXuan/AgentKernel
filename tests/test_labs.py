from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from types import CodeType


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LABS_ROOT = REPOSITORY_ROOT / "examples" / "labs"

EXPECTED_NOTEBOOKS = (
    "v0_1_agent_execution_lab.ipynb",
    "v0_2_recovery_lab.ipynb",
    "v0_3_durable_side_effect_lab.ipynb",
    "v0_4_context_vm_lab.ipynb",
    "v0_5_resource_handle_lab.ipynb",
    "v0_6_capability_lab.ipynb",
    "v0_7_process_runtime_lab.ipynb",
    "v0_8_multi_agent_runtime_lab.ipynb",
    "real_model_tool_trace_lab.ipynb",
)

DETERMINISTIC_NOTEBOOKS = EXPECTED_NOTEBOOKS[:-1]


def load_notebook(name: str) -> dict[str, object]:
    return json.loads((LABS_ROOT / name).read_text(encoding="utf-8"))


def notebook_text(name: str) -> str:
    return (LABS_ROOT / name).read_text(encoding="utf-8")


def code_cells(notebook: dict[str, object]) -> list[str]:
    cells = notebook["cells"]
    assert isinstance(cells, list)
    sources: list[str] = []
    for cell in cells:
        assert isinstance(cell, dict)
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            sources.append("".join(str(line) for line in source))
        else:
            sources.append(str(source))
    return sources


def markdown_text(notebook: dict[str, object]) -> str:
    chunks: list[str] = []
    cells = notebook["cells"]
    assert isinstance(cells, list)
    for cell in cells:
        assert isinstance(cell, dict)
        if cell.get("cell_type") != "markdown":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            chunks.append("".join(str(line) for line in source))
        else:
            chunks.append(str(source))
    return "\n".join(chunks)


def compile_notebook_cell(source: str, name: str, index: int) -> CodeType:
    return compile(source, f"{name}::cell-{index}", "exec")


def execute_notebook(name: str, *, monkeypatch) -> str:
    monkeypatch.chdir(REPOSITORY_ROOT)
    monkeypatch.delenv("AGENTKERNEL_RUN_REAL_MODEL", raising=False)
    monkeypatch.delenv("AGENTKERNEL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTKERNEL_LLM_MODEL", raising=False)
    monkeypatch.delenv("AGENTKERNEL_LLM_API_KEY", raising=False)
    notebook = load_notebook(name)
    namespace: dict[str, object] = {"__name__": f"lab_{name.replace('.', '_')}"}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        for index, source in enumerate(code_cells(notebook), start=1):
            exec(compile_notebook_cell(source, name, index), namespace)
    return output.getvalue()


def test_all_interactive_lab_notebooks_exist_and_are_valid_json() -> None:
    assert sorted(path.name for path in LABS_ROOT.glob("*.ipynb")) == sorted(
        EXPECTED_NOTEBOOKS
    )
    for name in EXPECTED_NOTEBOOKS:
        notebook = load_notebook(name)
        assert notebook["nbformat"] == 4
        assert notebook["cells"]


def test_labs_have_teaching_contract_sections() -> None:
    for name in EXPECTED_NOTEBOOKS:
        text = markdown_text(load_notebook(name))
        assert "WHAT THIS DEMONSTRATES" in text
        assert "WHAT THIS DOES NOT DEMONSTRATE" in text
        assert "Invariant" in text or "Observable trajectory" in text


def test_labs_do_not_contain_local_paths_or_secret_literals() -> None:
    forbidden_fragments = (
        r"D:\Users\\",
        r"D:\\Users\\",
        "/Users/",
        "sk-",
        "BEGIN OPENAI",
        "BEGIN PRIVATE KEY",
    )
    for name in EXPECTED_NOTEBOOKS:
        text = notebook_text(name)
        for fragment in forbidden_fragments:
            assert fragment not in text


def test_deterministic_labs_execute_offline(monkeypatch) -> None:
    for name in DETERMINISTIC_NOTEBOOKS:
        output = execute_notebook(name, monkeypatch=monkeypatch)
        assert "WHAT THIS" not in output
        assert "Traceback" not in output


def test_real_model_lab_skips_without_opt_in(monkeypatch) -> None:
    output = execute_notebook("real_model_tool_trace_lab.ipynb", monkeypatch=monkeypatch)
    assert "SKIPPED" in output
    assert "no network/provider call will be made" in output
