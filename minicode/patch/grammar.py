"""MiniCode v0 supported apply_patch grammar subset."""

SUPPORTED_GRAMMAR = """\
start: begin_patch operation+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?

operation: add_file | delete_file | update_file
add_file: "*** Add File: " path LF add_line+
delete_file: "*** Delete File: " path LF
update_file: "*** Update File: " path LF hunk+
hunk: ("@@" | "@@ " header) LF hunk_line+
add_line: "+" text LF
hunk_line: (" " | "-" | "+") text LF
"""

SUPPORTED_OPERATIONS = ("add", "update", "delete")
