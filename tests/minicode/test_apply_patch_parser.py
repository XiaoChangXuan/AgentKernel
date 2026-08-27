from __future__ import annotations

import pytest

from minicode.patch import AddFile, DeleteFile, PatchError, UpdateFile, parse_patch


def test_parse_valid_single_update():
    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-    return a / b\n"
        "+    return a // b\n"
        "*** End Patch"
    )

    assert len(parsed.operations) == 1
    operation = parsed.operations[0]
    assert isinstance(operation, UpdateFile)
    assert operation.path == "calculator.py"
    assert parsed.hunk_count == 1
    assert operation.hunks[0].old_lines == ("    return a / b",)
    assert operation.hunks[0].new_lines == ("    return a // b",)


def test_parse_valid_add_delete_and_multi_file_patch():
    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Add File: notes.txt\n"
        "+hello\n"
        "*** Delete File: obsolete.txt\n"
        "*** Update File: calculator.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )

    assert [type(operation) for operation in parsed.operations] == [AddFile, DeleteFile, UpdateFile]
    assert parsed.paths == ("notes.txt", "obsolete.txt", "calculator.py")


def test_parse_multiple_hunks_is_deterministic():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/helpers.py\n"
        "@@\n"
        "-    marker = 'search target'\n"
        "+    marker = 'patched target'\n"
        "@@ optional header\n"
        "-    return value.strip().lower()\n"
        "+    return value.strip().upper()\n"
        "*** End Patch"
    )

    assert parse_patch(patch) == parse_patch(patch)
    parsed = parse_patch(patch)
    operation = parsed.operations[0]
    assert isinstance(operation, UpdateFile)
    assert len(operation.hunks) == 2
    assert operation.hunks[1].header == "optional header"


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ("", "invalid_patch"),
        ("*** Update File: a\n@@\n-a\n+b\n*** End Patch", "invalid_patch"),
        ("*** Begin Patch\n*** End Patch", "invalid_patch"),
        ("*** Begin Patch\n*** Frobnicate File: a\n*** End Patch", "unsupported_patch_operation"),
        ("*** Begin Patch\n*** Add File: \n+hi\n*** End Patch", "invalid_patch"),
        ("*** Begin Patch\n*** Update File: a\n*** End Patch", "malformed_hunk"),
        ("*** Begin Patch\n*** Update File: a\n@@\n context\n*** End Patch", "malformed_hunk"),
        ("*** Begin Patch\n*** Add File: a\nnot-plus\n*** End Patch", "malformed_hunk"),
        ("*** Begin Patch\n*** Update File: a\n@@\n+floating\n*** End Patch", "malformed_hunk"),
    ],
)
def test_parse_rejects_malformed_patches(patch, code):
    with pytest.raises(PatchError) as exc:
        parse_patch(patch)

    assert exc.value.code == code
