"""Tests for file operations tracking and path manipulation."""

import pytest

from breadboard_engine.compaction.file_ops import (
    compute_file_lists,
    create_file_ops,
    extract_file_operations,
    extract_file_ops_from_message,
    format_file_operations,
    is_url_scheme_path,
    strip_read_selector,
)


def _read_call(call_id: str, path: str):
    return {"type": "function", "id": call_id, "function": {"name": "read", "arguments": {"path": path}}}


def _write_call(call_id: str, path: str):
    return {"type": "function", "id": call_id, "function": {"name": "write", "arguments": {"path": path}}}


def _assistant_msg(tool_calls):
    return {"role": "assistant", "content": "", "tool_calls": tool_calls}


def test_strip_read_selector_shapes():
    assert strip_read_selector("src/foo.ts:50") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:50-") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:50-200") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:50+150") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:5-16,960-973") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:2724..2727") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:raw") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:conflicts") == "src/foo.ts"
    # Compound raw+range, either order
    assert strip_read_selector("src/foo.ts:100-170:raw") == "src/foo.ts"
    assert strip_read_selector("src/foo.ts:raw:2-4") == "src/foo.ts"


def test_strip_read_selector_archive_members():
    assert strip_read_selector("archive.zip:dir/file.ts:50-60") == "archive.zip:dir/file.ts"
    assert strip_read_selector("archive.zip:dir/file.ts") == "archive.zip:dir/file.ts"


def test_strip_read_selector_leaves_non_selector_colons():
    assert strip_read_selector("db.sqlite:users") == "db.sqlite:users"
    assert strip_read_selector("local://ctx.md") == "local://ctx.md"
    assert strip_read_selector("https://example.com/page") == "https://example.com/page"
    assert strip_read_selector("src/foo.ts") == "src/foo.ts"


def test_extract_file_ops_dedupes_read_selectors():
    file_ops = create_file_ops()
    msg = _assistant_msg([
        _read_call("r1", "docs/compaction.md:100-170:raw"),
        _read_call("r2", "docs/compaction.md:8-16,128-139,384-388"),
        _read_call("r3", "docs/compaction.md:raw"),
        _read_call("r4", "docs/compaction.md"),
    ])
    extract_file_ops_from_message(msg, file_ops)
    assert file_ops.read == {"docs/compaction.md"}


def test_extract_file_ops_matches_read_against_modified():
    file_ops = create_file_ops()
    msg = _assistant_msg([
        _read_call("r1", "src/login.ts:30-80"),
        {"type": "function", "id": "w1", "function": {"name": "write", "arguments": {"path": "src/login.ts"}}},
    ])
    extract_file_ops_from_message(msg, file_ops)
    read_files, modified_files = compute_file_lists(file_ops)
    assert read_files == []
    assert modified_files == ["src/login.ts"]


def test_extract_file_ops_skips_urls():
    file_ops = create_file_ops()
    msg = _assistant_msg([
        _read_call("r1", "src/keep.ts"),
        _read_call("r2", "artifact://7"),
        _read_call("r3", "local://ctx.md"),
        _read_call("r4", "https://example.com/page"),
        _write_call("w1", "conflict://1"),
        _write_call("w2", "conflict://*"),
        _write_call("w3", "src/login.ts:conflict://3"),
        {"type": "function", "id": "e1", "function": {"name": "edit", "arguments": {"path": "agent://abc"}}},
    ])
    extract_file_ops_from_message(msg, file_ops)
    read_files, modified_files = compute_file_lists(file_ops)
    assert read_files == ["src/keep.ts"]
    assert modified_files == []


def test_compute_file_lists_drops_scheme_urls():
    file_ops = create_file_ops()
    file_ops.read.add("src/read-only.ts")
    file_ops.read.add("artifact://7")
    file_ops.edited.add("src/edited.ts")
    file_ops.edited.add("conflict://1")
    file_ops.written.add("local://ctx.md")
    read_files, modified_files = compute_file_lists(file_ops)
    assert read_files == ["src/read-only.ts"]
    assert modified_files == ["src/edited.ts"]


def test_format_file_operations_grouped():
    rendered = format_file_operations(
        ["src/a.ts", "src/b.ts"],
        ["src/c.ts", "src/d.ts"],
        {"src/a.ts", "src/b.ts", "src/c.ts"},
    )
    expected = "\n".join([
        "<files>",
        "# src/",
        "a.ts (Read)",
        "b.ts (Read)",
        "c.ts (RW)",
        "d.ts (Write)",
        "</files>",
    ])
    assert rendered == expected


def test_format_file_operations_write_without_read_set():
    rendered = format_file_operations([], ["c.ts"])
    assert rendered == "\n".join(["<files>", "c.ts (Write)", "</files>"])


def test_is_url_scheme_path():
    assert is_url_scheme_path("conflict://1") is True
    assert is_url_scheme_path("conflict://*") is True
    assert is_url_scheme_path("artifact://7") is True
    assert is_url_scheme_path("local://ctx.md") is True
    assert is_url_scheme_path("history://AuthLoader") is True
    assert is_url_scheme_path("https://example.com/page") is True
    assert is_url_scheme_path("src/login.ts:conflict://3") is True

    assert is_url_scheme_path("src/foo.ts") is False
    assert is_url_scheme_path("C:/Users/me/file.ts") is False
    assert is_url_scheme_path("db.sqlite:users") is False
    assert is_url_scheme_path("archive.zip:dir/file.ts") is False
    assert is_url_scheme_path("docs/compaction.md:100-170:raw") is False


def test_breadboard_tool_names_and_args():
    file_ops = create_file_ops()
    msg = _assistant_msg([
        {"id": "c1", "function": {"name": "read_file", "arguments": {"path": "src/core.py:10-50"}}},
        {"id": "c2", "function": {"name": "create_file_from_block", "arguments": {"filePath": "src/new.py", "content": "hello"}}},
        {"id": "c3", "function": {"name": "apply_search_replace", "arguments": {"file_name": "src/edit.py", "search": "a", "replace": "b"}}},
        {
            "id": "c4",
            "function": {
                "name": "apply_patch",
                "arguments": {
                    "input": "*** Begin Patch\n*** Add File: src/added.py\n+x = 1\n*** Update File: src/patched.py\n@@ ...\n*** End Patch"
                },
            },
        },
    ])
    extract_file_ops_from_message(msg, file_ops)
    read_files, modified_files = compute_file_lists(file_ops)
    assert read_files == ["src/core.py"]
    assert "src/new.py" in modified_files
    assert "src/edit.py" in modified_files
    assert "src/added.py" in modified_files
    assert "src/patched.py" in modified_files
