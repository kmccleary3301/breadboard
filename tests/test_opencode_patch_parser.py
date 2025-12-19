from kylecode.opencode_patch import PatchParseError, parse_opencode_patch, apply_update_hunks


def test_parse_update_hunk_accepts_end_of_file_marker() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
 line1
-line2
+line2 changed
*** End of File
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    assert len(ops) == 1
    assert ops[0].kind == "update"
    assert ops[0].file_path == "foo.txt"
    assert ops[0].hunks is not None
    assert len(ops[0].hunks) == 1


def test_parse_update_hunk_accepts_move_to() -> None:
    patch = """*** Begin Patch
*** Update File: old/name.txt
*** Move to: renamed/dir/name.txt
@@
 old content
-line2
+line2 changed
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    assert len(ops) == 1
    assert ops[0].kind == "update"
    assert ops[0].file_path == "old/name.txt"
    assert ops[0].move_to == "renamed/dir/name.txt"


def test_apply_update_hunk_ignores_end_of_file_marker_changes() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
 line1
-line2
+line2 changed
*** End of File
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    original = "line1\nline2\n"
    updated = apply_update_hunks(original, ops[0].hunks or [])
    assert "line2 changed" in updated


def test_parse_update_hunk_with_only_context_is_allowed() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@ def f
 line1
*** End of File
*** End Patch
"""
    ops = parse_opencode_patch(patch)
    assert len(ops) == 1
    assert ops[0].hunks is not None
    assert len(ops[0].hunks) == 1


def test_parse_rejects_empty_update_hunk_even_with_end_of_file() -> None:
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
*** End of File
*** End Patch
"""
    try:
        parse_opencode_patch(patch)
    except PatchParseError:
        return
    raise AssertionError("expected PatchParseError")


def test_parse_rejects_no_patch_block() -> None:
    try:
        parse_opencode_patch("not a patch")
    except PatchParseError:
        return
    raise AssertionError("expected PatchParseError")

