from __future__ import annotations

import hashlib

import pytest

from minicode.patch import PatchError, apply_patch_text
from minicode.testing import make_minicode_workspace
from minicode.workspace import discover_workspace


def _workspace(tmp_path):
    fixture = make_minicode_workspace(tmp_path)
    return fixture, discover_workspace(cwd=fixture.root)


def test_apply_patch_updates_exact_match_and_hashes(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    before = fixture.calculator.read_bytes()

    result = apply_patch_text(
        workspace,
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-        raise ZeroDivisionError('division by zero')\n"
        "+        return 0\n"
        "*** End Patch",
    )

    after = fixture.calculator.read_bytes()
    assert result.applied is True
    assert result.changed_files == ("calculator.py",)
    assert result.hunk_count == 1
    assert result.preimage_hashes["calculator.py"] == hashlib.sha256(before).hexdigest()
    assert result.postimage_hashes["calculator.py"] == hashlib.sha256(after).hexdigest()
    assert "return 0" in after.decode("utf-8")


def test_apply_patch_adds_and_deletes_files(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    result = apply_patch_text(
        workspace,
        "*** Begin Patch\n"
        "*** Add File: src/new_helper.py\n"
        "+def created():\n"
        "+    return True\n"
        "*** Delete File: nested/notes.txt\n"
        "*** End Patch",
    )

    assert result.changed_files == ("src/new_helper.py", "nested/notes.txt")
    assert (fixture.root / "src" / "new_helper.py").read_text(encoding="utf-8") == (
        "def created():\n"
        "    return True\n"
    )
    assert not (fixture.root / "nested" / "notes.txt").exists()
    assert result.preimage_hashes["src/new_helper.py"] is None
    assert result.postimage_hashes["nested/notes.txt"] is None


def test_apply_patch_multi_file_validates_before_mutating(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    calculator_before = fixture.calculator.read_text(encoding="utf-8")
    helper_before = fixture.helpers.read_text(encoding="utf-8")

    with pytest.raises(PatchError) as exc:
        apply_patch_text(
            workspace,
            "*** Begin Patch\n"
            "*** Update File: calculator.py\n"
            "@@\n"
            "-    return a / b\n"
            "+    return a // b\n"
            "*** Update File: src/helpers.py\n"
            "@@\n"
            "-missing line\n"
            "+patched line\n"
            "*** End Patch",
        )

    assert exc.value.code == "hunk_not_found"
    assert fixture.calculator.read_text(encoding="utf-8") == calculator_before
    assert fixture.helpers.read_text(encoding="utf-8") == helper_before


def test_apply_patch_rejects_ambiguous_hunk(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    repeated = fixture.root / "repeated.txt"
    repeated.write_text("same\nsame\n", encoding="utf-8")

    with pytest.raises(PatchError) as exc:
        apply_patch_text(
            workspace,
            "*** Begin Patch\n"
            "*** Update File: repeated.txt\n"
            "@@\n"
            "-same\n"
            "+changed\n"
            "*** End Patch",
        )

    assert exc.value.code == "hunk_ambiguous"
    assert repeated.read_text(encoding="utf-8") == "same\nsame\n"


def test_apply_patch_denies_workspace_escape_and_absolute_outside_path(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    with pytest.raises(PatchError) as parent_exc:
        apply_patch_text(
            workspace,
            "*** Begin Patch\n"
            "*** Add File: ../outside/created.txt\n"
            "+bad\n"
            "*** End Patch",
        )

    assert parent_exc.value.code == "outside_workspace"
    assert not (fixture.outside / "created.txt").exists()

    with pytest.raises(PatchError) as absolute_exc:
        apply_patch_text(
            workspace,
            f"*** Begin Patch\n*** Add File: {fixture.outside / 'created.txt'}\n+bad\n*** End Patch",
        )

    assert absolute_exc.value.code == "outside_workspace"


def test_apply_patch_denies_binary_and_unsupported_encoding_targets(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    with pytest.raises(PatchError) as binary_exc:
        apply_patch_text(
            workspace,
            "*** Begin Patch\n"
            "*** Delete File: binary.dat\n"
            "*** End Patch",
        )
    with pytest.raises(PatchError) as encoding_exc:
        apply_patch_text(
            workspace,
            "*** Begin Patch\n"
            "*** Update File: latin1.txt\n"
            "@@\n"
            "-cafe\n"
            "+cafe\n"
            "*** End Patch",
        )

    assert binary_exc.value.code == "binary_file"
    assert encoding_exc.value.code == "encoding_error"
    assert fixture.binary_file.exists()


def test_apply_patch_rejects_invalid_file_preconditions(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    with pytest.raises(PatchError) as add_exc:
        apply_patch_text(workspace, "*** Begin Patch\n*** Add File: calculator.py\n+new\n*** End Patch")
    with pytest.raises(PatchError) as missing_update_exc:
        apply_patch_text(workspace, "*** Begin Patch\n*** Update File: missing.py\n@@\n-old\n+new\n*** End Patch")
    with pytest.raises(PatchError) as missing_delete_exc:
        apply_patch_text(workspace, "*** Begin Patch\n*** Delete File: missing.py\n*** End Patch")
    with pytest.raises(PatchError) as dir_exc:
        apply_patch_text(workspace, "*** Begin Patch\n*** Delete File: src\n*** End Patch")

    assert add_exc.value.code == "file_already_exists"
    assert missing_update_exc.value.code == "file_not_found"
    assert missing_delete_exc.value.code == "file_not_found"
    assert dir_exc.value.code == "is_directory"


def test_apply_patch_preserves_crlf_for_changed_lines(tmp_path):
    fixture, workspace = _workspace(tmp_path)

    apply_patch_text(
        workspace,
        "*** Begin Patch\n"
        "*** Update File: crlf.txt\n"
        "@@\n"
        "-beta\n"
        "+BETA\n"
        "*** End Patch",
    )

    assert fixture.crlf_file.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_apply_patch_handles_utf8_content(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    target = fixture.root / "utf8.txt"
    target.write_text("hello\n", encoding="utf-8")

    apply_patch_text(
        workspace,
        "*** Begin Patch\n"
        "*** Update File: utf8.txt\n"
        "@@\n"
        "-hello\n"
        "+hello café\n"
        "*** End Patch",
    )

    assert target.read_text(encoding="utf-8") == "hello café\n"


def test_apply_patch_denies_symlink_escape_when_supported(tmp_path):
    fixture, workspace = _workspace(tmp_path)
    link = fixture.root / "linked-secret.txt"
    try:
        link.symlink_to(fixture.outside / "secret.txt")
    except OSError:
        pytest.skip("Symlink creation is not available for this Windows test environment")

    with pytest.raises(PatchError) as exc:
        apply_patch_text(
            workspace,
            "*** Begin Patch\n"
            "*** Update File: linked-secret.txt\n"
            "@@\n"
            "-outside\n"
            "+changed\n"
            "*** End Patch",
        )

    assert exc.value.code == "outside_workspace"
    assert (fixture.outside / "secret.txt").read_text(encoding="utf-8") == "outside\n"
