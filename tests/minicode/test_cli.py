from __future__ import annotations

import io

from minicode.cli import main
from minicode.testing import make_minicode_workspace


def test_cli_reports_phase_2a_not_implemented(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    stdout = io.StringIO()

    exit_code = main(["run", "--workspace", str(fixture.root), "fix calculator.py"], stdout=stdout)

    assert exit_code == 1
    output = stdout.getvalue()
    assert "not_implemented_in_phase_2a" in output
    assert "workspace_id" in output


def test_cli_invalid_workspace_returns_configuration_error(tmp_path):
    stderr = io.StringIO()

    exit_code = main(["run", "--workspace", str(tmp_path / "missing"), "fix"], stderr=stderr)

    assert exit_code == 5
    assert "invalid_workspace" in stderr.getvalue()
