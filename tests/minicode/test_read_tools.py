from __future__ import annotations

import hashlib

from minicode.testing import make_minicode_workspace
from minicode.tools import list_files, read_file, search_files
from minicode.workspace import discover_workspace


def _workspace(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    return fixture, discover_workspace(cwd=fixture.root)


def test_list_files_root_listing_hides_default_ignored_entries(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    result = list_files(workspace, {})

    assert result["ok"] is True
    assert [entry["path"] for entry in result["entries"]] == [
        "AGENTS.md",
        "binary.dat",
        "calculator.py",
        "latin1.txt",
        "nested",
        "src",
        "tests",
    ]
    assert result["truncated"] is False


def test_list_files_nested_and_recursive_are_relative_and_sorted(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    nested = list_files(workspace, {"path": "src"})
    recursive = list_files(workspace, {"recursive": True})

    assert nested["ok"] is True
    assert [entry["path"] for entry in nested["entries"]] == [
        "src/app.py",
        "src/helpers.py",
        "src/pkg",
    ]
    recursive_paths = [entry["path"] for entry in recursive["entries"]]
    assert recursive_paths == sorted(recursive_paths, key=str.lower)
    assert "src/app.py" in recursive_paths
    assert all("\\" not in path for path in recursive_paths)


def test_list_files_max_entries_truncates(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    result = list_files(workspace, {"max_entries": 2})

    assert result["ok"] is True
    assert result["entry_count"] == 2
    assert result["truncated"] is True


def test_list_files_include_hidden_reveals_hidden_and_build_entries(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    result = list_files(workspace, {"include_hidden": True})
    paths = [entry["path"] for entry in result["entries"]]

    assert result["ok"] is True
    assert ".hidden_file" in paths
    assert ".git" in paths
    assert "build" in paths


def test_list_files_outside_workspace_and_missing_path_are_structured_errors(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    outside = list_files(workspace, {"path": str(fixture.outside / "secret.txt")})
    missing = list_files(workspace, {"path": "missing"})

    assert outside["ok"] is False
    assert outside["error"]["code"] == "outside_workspace"
    assert outside["error"]["retryable"] is False
    assert missing["ok"] is False
    assert missing["error"]["code"] == "path_not_found"


def test_list_files_file_path_is_not_directory(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    result = list_files(workspace, {"path": "calculator.py"})

    assert result["ok"] is False
    assert result["error"]["code"] == "not_directory"


def test_search_files_single_and_multiple_matches_are_deterministic(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    result = search_files(workspace, {"query": "search target"})

    assert result["ok"] is True
    assert [(match["path"], match["line"]) for match in result["matches"]] == [
        ("src/app.py", 4),
        ("src/helpers.py", 2),
    ]
    assert result["match_count"] == 2
    assert result["truncated"] is False


def test_search_files_case_sensitive_mode(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    insensitive = search_files(workspace, {"query": "minicode search target"})
    sensitive = search_files(
        workspace,
        {"query": "minicode search target", "case_sensitive": True},
    )

    assert insensitive["match_count"] == 1
    assert sensitive["match_count"] == 0


def test_search_files_glob_path_and_max_matches(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    globbed = search_files(workspace, {"query": "search target", "glob": "helpers.py"})
    scoped = search_files(workspace, {"query": "search target", "path": "src/helpers.py"})
    truncated = search_files(workspace, {"query": "search target", "max_matches": 1})

    assert [match["path"] for match in globbed["matches"]] == ["src/helpers.py"]
    assert [match["path"] for match in scoped["matches"]] == ["src/helpers.py"]
    assert truncated["match_count"] == 1
    assert truncated["truncated"] is True


def test_search_files_context_lines_and_no_matches(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    with_context = search_files(
        workspace,
        {"query": "return value", "path": "src/helpers.py", "context_lines": 1},
    )
    no_matches = search_files(workspace, {"query": "not present"})

    assert with_context["match_count"] == 1
    assert with_context["matches"][0]["context_before"] == [
        {"line": 2, "text": "    marker = 'search target'"}
    ]
    assert no_matches["ok"] is True
    assert no_matches["matches"] == []


def test_search_files_skips_binary_and_unsupported_text(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    result = search_files(workspace, {"query": "anything"})

    assert result["ok"] is True
    assert {"path": "binary.dat", "reason": "binary"} in result["skipped_files"]
    assert {"path": "latin1.txt", "reason": "unsupported_encoding"} in result["skipped_files"]


def test_search_files_outside_workspace_is_structured_error(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    result = search_files(
        workspace,
        {"query": "outside", "path": str(fixture.outside / "secret.txt")},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "outside_workspace"


def test_read_file_whole_file_line_numbers_and_sha(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    result = read_file(workspace, {"path": "calculator.py"})

    assert result["ok"] is True
    assert result["path"] == "calculator.py"
    assert result["start_line"] == 1
    assert result["end_line"] == 4
    assert result["total_lines"] == 4
    assert result["content"].splitlines()[0] == "1: def divide(a, b):"
    assert result["sha256"] == hashlib.sha256(fixture.calculator.read_bytes()).hexdigest()


def test_read_file_line_ranges_first_final_and_absolute_in_workspace(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    first = read_file(workspace, {"path": "calculator.py", "start_line": 1, "end_line": 1})
    final = read_file(workspace, {"path": "calculator.py", "start_line": 4, "end_line": 4})
    absolute = read_file(workspace, {"path": str(fixture.calculator), "start_line": 2, "end_line": 2})

    assert first["content"] == "1: def divide(a, b):"
    assert final["content"] == "4:     return a / b"
    assert absolute["path"] == "calculator.py"
    assert absolute["content"] == "2:     if b == 0:"


def test_read_file_invalid_range_and_max_byte_truncation(tmp_path):
    _fixture, workspace = _workspace(tmp_path)

    invalid = read_file(workspace, {"path": "calculator.py", "start_line": 3, "end_line": 2})
    too_large = read_file(workspace, {"path": "calculator.py", "start_line": 99})
    truncated = read_file(workspace, {"path": "calculator.py", "max_bytes": 12})

    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_range"
    assert too_large["ok"] is False
    assert too_large["error"]["code"] == "invalid_range"
    assert truncated["ok"] is True
    assert truncated["truncated"] is True
    assert truncated["end_line"] == 1


def test_read_file_missing_directory_binary_encoding_and_outside_errors(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    missing = read_file(workspace, {"path": "missing.py"})
    directory = read_file(workspace, {"path": "src"})
    binary = read_file(workspace, {"path": "binary.dat"})
    encoding = read_file(workspace, {"path": "latin1.txt"})
    outside = read_file(workspace, {"path": str(fixture.outside / "secret.txt")})

    assert missing["error"]["code"] == "path_not_found"
    assert directory["error"]["code"] == "is_directory"
    assert binary["error"]["code"] == "binary_file"
    assert encoding["error"]["code"] == "unsupported_encoding"
    assert outside["error"]["code"] == "outside_workspace"
