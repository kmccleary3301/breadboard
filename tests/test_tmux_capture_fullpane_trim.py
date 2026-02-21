from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_trim_fullpane_uses_last_marker_to_avoid_duplicate_windows():
    module = _load_module("tmux_capture_poll", "scripts/tmux_capture_poll.py")
    ansi = "\n".join(
        [
            "BreadBoard v0.2.0",
            "old transcript line",
            "BreadBoard v0.2.0",
            "new transcript line",
            '❯ Try "fix typecheck errors"',
        ],
    )
    txt = ansi
    out_ansi, out_txt = module.trim_fullpane_from_markers(ansi, txt, ("BreadBoard v", "No conversation yet"), 0)
    assert out_txt.splitlines()[0] == "BreadBoard v0.2.0"
    assert "old transcript line" not in out_txt
    assert "new transcript line" in out_txt
    assert out_ansi == out_txt


def test_trim_fullpane_honors_line_cap_after_marker_trim():
    module = _load_module("tmux_capture_poll", "scripts/tmux_capture_poll.py")
    ansi = "\n".join(["BreadBoard v0.2.0", "line-1", "line-2", "line-3"])
    txt = ansi
    _, out_txt = module.trim_fullpane_from_markers(ansi, txt, ("BreadBoard v",), 2)
    assert out_txt.splitlines() == ["line-2", "line-3"]


def test_fullpane_png_defaults_to_viewport_rows_without_snapshot():
    module = _load_module("tmux_capture_poll", "scripts/tmux_capture_poll.py")
    rows, snapshot = module.resolve_png_render_geometry(
        capture_mode="fullpane",
        viewport_rows=45,
        frame_line_count=78,
        fullpane_render_max_rows=0,
    )
    assert rows == 45
    assert snapshot is False


def test_fullpane_png_honors_max_rows_cap():
    module = _load_module("tmux_capture_poll", "scripts/tmux_capture_poll.py")
    rows, snapshot = module.resolve_png_render_geometry(
        capture_mode="fullpane",
        viewport_rows=45,
        frame_line_count=78,
        fullpane_render_max_rows=60,
    )
    assert rows == 60
    assert snapshot is False


def test_non_fullpane_png_keeps_snapshot_mode():
    module = _load_module("tmux_capture_poll", "scripts/tmux_capture_poll.py")
    rows, snapshot = module.resolve_png_render_geometry(
        capture_mode="pane",
        viewport_rows=45,
        frame_line_count=78,
        fullpane_render_max_rows=0,
    )
    assert rows == 45
    assert snapshot is True
