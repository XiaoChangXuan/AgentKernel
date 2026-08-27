from __future__ import annotations

import os

import pytest

from minicode.errors import MiniCodeError
from minicode.instructions import discover_agent_instructions
from minicode.testing import make_minicode_workspace
from minicode.workspace import discover_workspace


def test_nearest_git_root_from_nested_cwd(tmp_path):
    fixture = make_minicode_workspace(tmp_path)

    workspace = discover_workspace(cwd=fixture.task_cwd)

    assert workspace.root == fixture.root.resolve()
    assert workspace.task_cwd == fixture.task_cwd.resolve()


def test_fallback_to_cwd_without_git(tmp_path):
    fixture = make_minicode_workspace(tmp_path, git=False)

    workspace = discover_workspace(cwd=fixture.task_cwd)

    assert workspace.root == fixture.task_cwd.resolve()
    assert workspace.task_cwd == fixture.task_cwd.resolve()


def test_explicit_workspace_root(tmp_path):
    fixture = make_minicode_workspace(tmp_path)

    workspace = discover_workspace(explicit_workspace=fixture.root)

    assert workspace.root == fixture.root.resolve()
    assert workspace.task_cwd == fixture.root.resolve()


def test_explicit_workspace_with_nested_task_cwd(tmp_path):
    fixture = make_minicode_workspace(tmp_path)

    workspace = discover_workspace(explicit_workspace=fixture.root, task_cwd=fixture.task_cwd)

    assert workspace.root == fixture.root.resolve()
    assert workspace.task_cwd == fixture.task_cwd.resolve()


def test_normal_relative_path(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)

    normalized = workspace.normalize_path("calculator.py", must_exist=True)

    assert normalized.relative_path == "calculator.py"
    assert normalized.absolute_path == fixture.calculator.resolve()
    assert normalized.exists is True


def test_absolute_in_workspace_path_normalizes_to_relative_identity(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)

    normalized = workspace.normalize_path(fixture.calculator, must_exist=True)

    assert normalized.relative_path == "calculator.py"


def test_parent_traversal_escape_is_denied(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)

    with pytest.raises(MiniCodeError) as exc:
        workspace.normalize_path("../outside/secret.txt")

    assert exc.value.code == "outside_workspace"


def test_absolute_outside_workspace_is_denied(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)

    with pytest.raises(MiniCodeError) as exc:
        workspace.normalize_path(fixture.outside / "secret.txt")

    assert exc.value.code == "outside_workspace"


def test_symlink_escape_is_denied_when_supported(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)
    link = fixture.root / "linked-secret.txt"
    target = fixture.outside / "secret.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is not available for this Windows test environment")

    with pytest.raises(MiniCodeError) as exc:
        workspace.normalize_path("linked-secret.txt", must_exist=True)

    assert exc.value.code == "outside_workspace"


def test_missing_path_behavior(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)

    with pytest.raises(MiniCodeError) as exc:
        workspace.normalize_path("missing.py", must_exist=True)

    assert exc.value.code == "path_not_found"


def test_root_agents_md_discovery(tmp_path):
    fixture = make_minicode_workspace(tmp_path, nested_agents=False)
    workspace = discover_workspace(cwd=fixture.root)

    sources = discover_agent_instructions(workspace)

    assert [source.relative_path for source in sources] == ["AGENTS.md"]
    assert sources[0].content == "Root instructions\n"


def test_nested_agents_md_ordering(tmp_path):
    fixture = make_minicode_workspace(tmp_path, nested_agents=True)
    workspace = discover_workspace(cwd=fixture.task_cwd)

    sources = discover_agent_instructions(workspace)

    assert [source.relative_path for source in sources] == ["AGENTS.md", "src/pkg/AGENTS.md"]
    assert [source.order for source in sources] == [0, 1]


def test_no_agents_md(tmp_path):
    fixture = make_minicode_workspace(tmp_path, nested_agents=False)
    fixture.root_agents.unlink()
    workspace = discover_workspace(cwd=fixture.root)

    assert discover_agent_instructions(workspace) == []


def test_normalized_representation_is_deterministic(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    workspace = discover_workspace(cwd=fixture.root)

    normalized = workspace.normalize_path(os.path.join("src", "pkg", "..", "..", "calculator.py"))

    assert normalized.relative_path == "calculator.py"
    assert "\\" not in normalized.relative_path
