from __future__ import annotations

from pathlib import Path

import pytest

from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.tail_index import _TailLineIndexCache


def _write_lines(path: Path, lines: list[str], *, trailing_newline: bool = False) -> None:
    text = "\n".join(lines)
    if trailing_newline:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def test_tail_index_returns_last_lines(tmp_path: Path) -> None:
    target = tmp_path / "log.jsonl"
    _write_lines(target, ["a", "b", "c", "d"], trailing_newline=False)

    cache = _TailLineIndexCache(chunk_size=8)
    text, meta = cache.read_tail_text(target, tail_lines=2, max_bytes=64)
    assert text.split("\n") == ["c", "d"]
    assert meta["total_bytes"] == target.stat().st_size
    assert meta["start_offset"] > 0


def test_tail_index_tracks_appends(tmp_path: Path) -> None:
    target = tmp_path / "append.jsonl"
    _write_lines(target, [f"line-{idx}" for idx in range(50)], trailing_newline=True)

    cache = _TailLineIndexCache(chunk_size=64)
    first, _ = cache.read_tail_text(target, tail_lines=5, max_bytes=256)
    assert first.split("\n")[-1] == ""  # file ends with newline

    # Append additional content.
    with target.open("a", encoding="utf-8") as handle:
        handle.write("new-1\nnew-2\n")

    second, _ = cache.read_tail_text(target, tail_lines=3, max_bytes=256)
    assert second.split("\n")[-3:] == ["new-1", "new-2", ""]


def test_read_snippet_tail_only_when_head_lines_zero(tmp_path: Path) -> None:
    target = tmp_path / "snippet.jsonl"
    _write_lines(target, ["x", "y", "z"], trailing_newline=False)

    snippet, returned_bytes = SessionService._read_snippet(target, head_lines=0, tail_lines=2, max_bytes=64)
    assert snippet.split("\n") == ["y", "z"]
    assert returned_bytes > 0


@pytest.mark.parametrize("tail_lines", [0, 1, 3])
def test_read_snippet_head_tail_smoke(tmp_path: Path, tail_lines: int) -> None:
    target = tmp_path / "classic.txt"
    _write_lines(target, [f"row-{idx}" for idx in range(200)], trailing_newline=True)

    snippet, returned_bytes = SessionService._read_snippet(target, head_lines=3, tail_lines=tail_lines, max_bytes=256)
    assert isinstance(snippet, str)
    assert returned_bytes > 0
