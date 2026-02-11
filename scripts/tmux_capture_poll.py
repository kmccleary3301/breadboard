#!/usr/bin/env python3
"""
Poll a tmux pane and capture frames to .txt/.ansi/.png at a fixed interval.

Default behavior captures the *visible pane* (top to bottom) so landing headers
and input bars are consistently included.

Examples:
  python scripts/tmux_capture_poll.py --target bb_tui_codex_dev:0.0
  python scripts/tmux_capture_poll.py --target claude:0.0 --duration 30 --interval 0.25 --png
  python scripts/tmux_capture_poll.py --target claude:0.0 --frames 120 --png --scrollback

Outputs:
  <out_root>/<scenario>/<timestamp>/
    meta.json
    frames/
      frame_0001.txt
      frame_0001.ansi
      frame_0001.png
      ...
    index.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TextIO

from tmux_capture_time import run_timestamp


@dataclass
class CaptureConfig:
    target: str
    interval: float
    duration: float
    frames: int
    capture_mode: str
    render_png: bool
    cols: Optional[int]
    rows: Optional[int]
    out_root: str
    scenario: str
    run_id: str
    settle_ms: int
    settle_attempts: int


def run_tmux(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout


def ensure_target_exists(target: str) -> None:
    try:
        run_tmux(["tmux", "display-message", "-p", "-t", target, "#{pane_id}"])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"tmux target '{target}' is not available{detail}") from exc


def get_pane_size(target: str) -> tuple[int, int]:
    raw = run_tmux(["tmux", "list-panes", "-t", target, "-F", "#{pane_width} #{pane_height}"]).strip()
    first = raw.splitlines()[0] if raw else ""
    parts = first.split()
    if len(parts) < 2:
        raise RuntimeError(f"unable to resolve pane size for target '{target}'")
    cols = int(parts[0])
    rows = int(parts[1])
    return cols, rows


def capture_raw(target: str, capture_mode: str) -> tuple[str, str]:
    if capture_mode == "scrollback":
        # Full scrollback
        run_tmux(["tmux", "capture-pane", "-ep", "-S", "-", "-t", target, "-b", "capture"])
        ansi = run_tmux(["tmux", "save-buffer", "-b", "capture", "-"])
        run_tmux(["tmux", "delete-buffer", "-b", "capture"])
        run_tmux(["tmux", "capture-pane", "-p", "-S", "-", "-t", target, "-b", "capture_txt"])
        txt = run_tmux(["tmux", "save-buffer", "-b", "capture_txt", "-"])
        run_tmux(["tmux", "delete-buffer", "-b", "capture_txt"])
    else:
        # Visible pane only (full panel): top-to-bottom pane contents.
        ansi = run_tmux(["tmux", "capture-pane", "-ep", "-t", target])
        txt = run_tmux(["tmux", "capture-pane", "-p", "-t", target])
    return ansi, txt


def capture_pane(
    target: str,
    capture_mode: str,
    ansi_path: Path,
    txt_path: Path,
    settle_ms: int,
    settle_attempts: int,
) -> None:
    last_ansi = ""
    last_txt = ""
    for attempt in range(max(settle_attempts, 1)):
        ansi, txt = capture_raw(target, capture_mode)
        if attempt > 0 and ansi == last_ansi and txt == last_txt:
            break
        last_ansi = ansi
        last_txt = txt
        if attempt + 1 < max(settle_attempts, 1):
            time.sleep(max(settle_ms, 0) / 1000.0)
    ansi_path.write_text(last_ansi, encoding="utf-8")
    txt_path.write_text(last_txt, encoding="utf-8")


def render_png(ansi_path: Path, png_path: Path, cols: int, rows: int, script_dir: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(script_dir / "tmux_capture_to_png.py"),
            "--ansi",
            str(ansi_path),
            "--cols",
            str(cols),
            "--rows",
            str(rows),
            "--out",
            str(png_path),
        ],
        check=True,
    )


def parse_args() -> CaptureConfig:
    parser = argparse.ArgumentParser(description="Poll a tmux pane and capture frames.")
    parser.add_argument("--target", required=True, help="tmux target (session:window.pane)")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between frames")
    parser.add_argument("--duration", type=float, default=60.0, help="total duration in seconds")
    parser.add_argument("--frames", type=int, default=0, help="number of frames (overrides duration if >0)")
    parser.add_argument(
        "--run-id",
        default="",
        help="explicit run id folder name (default: generated timestamp). Useful for scenario runners.",
    )
    parser.add_argument(
        "--capture-mode",
        choices=("pane", "scrollback"),
        default="pane",
        help="pane=full visible panel (default); scrollback=entire pane history",
    )
    parser.add_argument(
        "--scrollback",
        action="store_true",
        help="deprecated alias for --capture-mode scrollback",
    )
    parser.add_argument("--png", dest="render_png", action="store_true", help="render PNG for each frame")
    parser.add_argument("--no-png", dest="render_png", action="store_false", help="skip PNG render")
    parser.set_defaults(render_png=True)
    parser.add_argument("--cols", type=int, default=0, help="override columns (default: tmux pane width)")
    parser.add_argument("--rows", type=int, default=0, help="override rows (default: tmux pane height)")
    parser.add_argument("--out-root", default=None, help="output root directory")
    parser.add_argument("--scenario", default="capture", help="scenario name for output folder")
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=140,
        help="delay between verification reads to reduce mid-redraw tearing",
    )
    parser.add_argument(
        "--settle-attempts",
        type=int,
        default=5,
        help="max capture attempts per frame before accepting latest read",
    )

    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    # Default outside the git repo to avoid accidental commits.
    # Repo layout in this workspace is: <ray_SCE>/<repo_or_worktree>/...
    default_out = repo_root.parent.parent / "docs_tmp" / "tmux_captures"
    out_root = Path(args.out_root).expanduser() if args.out_root else default_out

    capture_mode = "scrollback" if args.scrollback else args.capture_mode

    return CaptureConfig(
        target=args.target,
        interval=max(args.interval, 0.05),
        duration=max(args.duration, 0.0),
        frames=max(args.frames, 0),
        capture_mode=capture_mode,
        render_png=bool(args.render_png),
        cols=args.cols if args.cols > 0 else None,
        rows=args.rows if args.rows > 0 else None,
        out_root=str(out_root),
        scenario=args.scenario,
        run_id=str(args.run_id or "").strip(),
        settle_ms=max(args.settle_ms, 0),
        settle_attempts=max(args.settle_attempts, 1),
    )


def main() -> None:
    config = parse_args()
    script_dir = Path(__file__).resolve().parent
    out_root = Path(config.out_root)
    ts = config.run_id or run_timestamp()
    run_dir = out_root / config.scenario / ts
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    ensure_target_exists(config.target)
    base_cols, base_rows = get_pane_size(config.target)
    cols = config.cols or base_cols
    rows = config.rows or base_rows
    dynamic_size = config.cols is None and config.rows is None

    meta = {
        "target": config.target,
        "interval": config.interval,
        "duration": config.duration,
        "frames": config.frames,
        "capture_mode": config.capture_mode,
        "render_png": config.render_png,
        "cols": cols,
        "rows": rows,
        "dynamic_size": dynamic_size,
        "scenario": config.scenario,
        "settle_ms": config.settle_ms,
        "settle_attempts": config.settle_attempts,
        "started_at": ts,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    # Keep an exact one-shot snapshot of the first visible frame for easy QA.
    capture_pane(
        config.target,
        config.capture_mode,
        run_dir / "initial.ansi",
        run_dir / "initial.txt",
        config.settle_ms,
        config.settle_attempts,
    )

    index_path = run_dir / "index.jsonl"
    frame_count = config.frames
    end_time = time.monotonic() + config.duration if config.frames == 0 else None
    next_tick = time.monotonic()

    i = 0
    index_file: TextIO
    index_file = index_path.open("a", encoding="utf-8")
    while True:
        now = time.monotonic()
        if end_time is not None and now >= end_time:
            break
        if frame_count and i >= frame_count:
            break
        if now < next_tick:
            time.sleep(max(0.0, next_tick - now))

        i += 1
        if dynamic_size:
            cols, rows = get_pane_size(config.target)
        frame_id = f"frame_{i:04d}"
        ansi_path = frames_dir / f"{frame_id}.ansi"
        txt_path = frames_dir / f"{frame_id}.txt"
        png_path = frames_dir / f"{frame_id}.png"

        capture_pane(
            config.target,
            config.capture_mode,
            ansi_path,
            txt_path,
            config.settle_ms,
            config.settle_attempts,
        )
        if config.render_png:
            render_png(ansi_path, png_path, cols, rows, script_dir)

        record = {
            "frame": i,
            "timestamp": time.time(),
            "cols": cols,
            "rows": rows,
            "ansi": str(ansi_path.relative_to(run_dir)),
            "text": str(txt_path.relative_to(run_dir)),
            "png": str(png_path.relative_to(run_dir)) if config.render_png else None,
        }
        index_file.write(json.dumps(record) + "\n")
        index_file.flush()

        next_tick += config.interval
    index_file.close()

    print(f"[tmux-capture] wrote {i} frames -> {run_dir}")


if __name__ == "__main__":
    main()
