from kylecode.opencode_patch import (
    PatchChange,
    PatchHunk,
    apply_update_hunks_codex,
    seek_sequence_codex,
)


def test_seek_sequence_codex_prefers_eof_start() -> None:
    lines = ["x", "target", "y", "z", "target"]
    # When eof=True, Codex anchors the match to the end-of-file start index.
    assert seek_sequence_codex(lines, ["target"], start=0, eof=True) == 4


def test_apply_update_hunks_codex_matches_with_trim() -> None:
    original = "foo   \nbar\n"
    hunks = [
        PatchHunk(
            context="",
            changes=[
                PatchChange("keep", "foo"),
                PatchChange("remove", "bar"),
                PatchChange("add", "baz"),
            ],
        )
    ]
    updated = apply_update_hunks_codex(original, hunks, file_label="test.txt")
    assert "baz" in updated


def test_apply_update_hunks_codex_matches_with_unicode_normalisation() -> None:
    # Original contains an em dash, patch uses ASCII hyphen.
    original = "a—b\nkeep\n"
    hunks = [
        PatchHunk(
            context="",
            changes=[
                PatchChange("remove", "a-b"),
                PatchChange("add", "a-b-updated"),
            ],
            is_end_of_file=False,
        )
    ]
    updated = apply_update_hunks_codex(original, hunks, file_label="unicode.txt")
    assert "a-b-updated" in updated


def test_apply_update_hunks_codex_respects_end_of_file_marker() -> None:
    original = "target\nmid\ntarget\n"
    hunks = [
        PatchHunk(
            context="",
            changes=[
                PatchChange("remove", "target"),
                PatchChange("add", "target2"),
            ],
            is_end_of_file=True,
        )
    ]
    updated = apply_update_hunks_codex(original, hunks, file_label="eof.txt")
    # Only the last occurrence should change when EOF is requested.
    assert updated.splitlines()[0] == "target"
    assert updated.splitlines()[2] == "target2"

