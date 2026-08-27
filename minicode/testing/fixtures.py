from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MiniCodeWorkspaceFixture:
    root: Path
    task_cwd: Path
    outside: Path
    calculator: Path
    app: Path
    helpers: Path
    hidden_file: Path
    build_output: Path
    binary_file: Path
    latin1_file: Path
    root_agents: Path
    nested_agents: Path


def make_minicode_workspace(tmp_path: Path, *, git: bool = True, nested_agents: bool = True) -> MiniCodeWorkspaceFixture:
    """Create a tiny deterministic workspace tree for MiniCode tests."""

    root = tmp_path / "workspace"
    task_cwd = root / "src" / "pkg"
    src_dir = root / "src"
    build_dir = root / "build"
    tests_dir = root / "tests"
    nested_dir = root / "nested"
    outside = tmp_path / "outside"

    task_cwd.mkdir(parents=True)
    build_dir.mkdir()
    tests_dir.mkdir()
    nested_dir.mkdir()
    outside.mkdir()
    if git:
        (root / ".git").mkdir()

    root_agents = root / "AGENTS.md"
    root_agents.write_text("Root instructions\n", encoding="utf-8")

    nested_file = task_cwd / "AGENTS.md"
    if nested_agents:
        nested_file.write_text("Nested instructions\n", encoding="utf-8")

    calculator = root / "calculator.py"
    calculator.write_text(
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ZeroDivisionError('division by zero')\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    app = src_dir / "app.py"
    app.write_text(
        "from src.helpers import normalize\n\n"
        "def main():\n"
        "    return normalize('MiniCode Search Target')\n",
        encoding="utf-8",
    )
    helpers = src_dir / "helpers.py"
    helpers.write_text(
        "def normalize(value):\n"
        "    marker = 'search target'\n"
        "    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (tests_dir / "test_calculator.py").write_text(
        "from calculator import divide\n\n"
        "def test_divide():\n"
        "    assert divide(8, 2) == 4\n",
        encoding="utf-8",
    )
    hidden_file = root / ".hidden_file"
    hidden_file.write_text("hidden search target\n", encoding="utf-8")
    build_output = build_dir / "generated.txt"
    build_output.write_text("generated search target\n", encoding="utf-8")
    (nested_dir / "notes.txt").write_text("nested note\n", encoding="utf-8")
    binary_file = root / "binary.dat"
    binary_file.write_bytes(b"\x00\x01binary")
    latin1_file = root / "latin1.txt"
    latin1_file.write_bytes("caf\xe9".encode("latin-1"))
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")

    return MiniCodeWorkspaceFixture(
        root=root,
        task_cwd=task_cwd,
        outside=outside,
        calculator=calculator,
        app=app,
        helpers=helpers,
        hidden_file=hidden_file,
        build_output=build_output,
        binary_file=binary_file,
        latin1_file=latin1_file,
        root_agents=root_agents,
        nested_agents=nested_file,
    )
