from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MiniCodeWorkspaceFixture:
    root: Path
    task_cwd: Path
    outside: Path
    calculator: Path
    root_agents: Path
    nested_agents: Path


def make_minicode_workspace(tmp_path: Path, *, git: bool = True, nested_agents: bool = True) -> MiniCodeWorkspaceFixture:
    """Create a tiny deterministic workspace tree for MiniCode tests."""

    root = tmp_path / "workspace"
    task_cwd = root / "src" / "pkg"
    tests_dir = root / "tests"
    outside = tmp_path / "outside"

    task_cwd.mkdir(parents=True)
    tests_dir.mkdir()
    outside.mkdir()
    if git:
        (root / ".git").mkdir()

    root_agents = root / "AGENTS.md"
    root_agents.write_text("Root instructions\n", encoding="utf-8")

    nested_file = task_cwd / "AGENTS.md"
    if nested_agents:
        nested_file.write_text("Nested instructions\n", encoding="utf-8")

    calculator = root / "calculator.py"
    calculator.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    (tests_dir / "test_calculator.py").write_text("from calculator import divide\n", encoding="utf-8")
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")

    return MiniCodeWorkspaceFixture(
        root=root,
        task_cwd=task_cwd,
        outside=outside,
        calculator=calculator,
        root_agents=root_agents,
        nested_agents=nested_file,
    )
