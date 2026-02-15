#!/usr/bin/env python3
"""
Poll a tmux pane and capture frames to .txt/.ansi/.png at a fixed interval.

Default behavior captures the *visible pane* (top to bottom) so landing headers
and input bars are consistently included.

Examples:
  python scripts/tmux_capture_poll.py --target breadboard_test_ci_soft_gate:0.0
  python scripts/tmux_capture_poll.py --target breadboard_test_claude:0.0 --duration 30 --interval 0.25 --png
  python scripts/tmux_capture_poll.py --target breadboard_test_claude:0.0 --frames 120 --png --scrollback

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
import signal
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
    tmux_socket: str
    interval: float
    duration: float
    frames: int
    capture_mode: str
    render_png: bool
    cols: Optional[int]
    rows: Optional[int]
    resize_pane: bool
    out_root: str
    scenario: str
    run_id: str
    settle_ms: int
    settle_attempts: int
    session_prefix_guard: str
    protected_sessions: str
    allow_any_session: bool


_stop_requested = False


def _request_stop(signum: int, frame: object | None) -> None:  # pragma: no cover - signal handler
    # Graceful shutdown: finish the current frame (including PNG) and exit cleanly.
    global _stop_requested
    _stop_requested = True


def tmux_base_args(socket_name: str) -> list[str]:
    socket_name = (socket_name or "").strip()
    if not socket_name:
        return ["tmux"]
    return ["tmux", "-L", socket_name]


def run_tmux(cmd: list[str], socket_name: str) -> str:
    result = subprocess.run(
        [*tmux_base_args(socket_name), *cmd],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def ensure_target_exists(target: str, socket_name: str) -> None:
    try:
        run_tmux(["display-message", "-p", "-t", target, "#{pane_id}"], socket_name)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"tmux target '{target}' is not available{detail}") from exc


def get_pane_size(target: str, socket_name: str) -> tuple[int, int]:
    raw = run_tmux(["display-message", "-p", "-t", target, "#{pane_width} #{pane_height}"], socket_name).strip()
    parts = raw.split()
    if len(parts) != 2:
        raise RuntimeError(f"unable to resolve pane size for target '{target}' (raw={raw!r})")
    return int(parts[0]), int(parts[1])


def window_target_from_pane_target(target: str) -> str:
    """
    Convert session:window.pane -> session:window.
    If already session:window, returns unchanged.
    """
    # tmux targets can be session:window.pane or session:window
    if "." not in target:
        return target
    # Strip trailing .<pane>
    return target.rsplit(".", 1)[0]


def session_from_target(target: str) -> str:
    if ":" not in target:
        return ""
    return target.split(":", 1)[0].strip()


def capture_raw(target: str, capture_mode: str, socket_name: str, buffer_suffix: str) -> tuple[str, str]:
    def _non_ws_count(s: str) -> int:
        return sum(1 for ch in s if not ch.isspace())

    def _looks_like_prompt(text: str) -> bool:
        # Heuristic only: use stable prompt/ready tokens that tend to appear in the visible UI.
        tokens = ("for shortcuts", "context left", "OpenAI Codex", "❯")
        return any(tok in text for tok in tokens)

    def _capture_one(alternate: bool) -> tuple[str, str]:
        a = "-a" if alternate else ""
        if capture_mode == "scrollback":
            safe = "".join(ch if ch.isalnum() else "_" for ch in (buffer_suffix or "capture"))
            suffix = "alt" if alternate else "norm"
            buf_ansi = f"capture_{safe}_{suffix}"
            buf_txt = f"capture_txt_{safe}_{suffix}"
            args_ansi = ["capture-pane"]
            if a:
                args_ansi.append(a)
            args_ansi += ["-e", "-p", "-S", "-", "-t", target, "-b", buf_ansi]
            run_tmux(args_ansi, socket_name)
            ansi_out = run_tmux(["save-buffer", "-b", buf_ansi, "-"], socket_name)
            run_tmux(["delete-buffer", "-b", buf_ansi], socket_name)

            args_txt = ["capture-pane"]
            if a:
                args_txt.append(a)
            args_txt += ["-p", "-S", "-", "-t", target, "-b", buf_txt]
            run_tmux(args_txt, socket_name)
            txt_out = run_tmux(["save-buffer", "-b", buf_txt, "-"], socket_name)
            run_tmux(["delete-buffer", "-b", buf_txt], socket_name)
            return ansi_out, txt_out

        args_ansi = ["capture-pane"]
        if a:
            args_ansi.append(a)
        args_ansi += ["-e", "-p", "-t", target]
        ansi_out = run_tmux(args_ansi, socket_name)

        args_txt = ["capture-pane"]
        if a:
            args_txt.append(a)
        args_txt += ["-p", "-t", target]
        txt_out = run_tmux(args_txt, socket_name)
        return ansi_out, txt_out

    ansi_norm, txt_norm = _capture_one(alternate=False)
    ansi_alt, txt_alt = _capture_one(alternate=True)

    # Prefer whichever buffer looks like an interactive prompt/ready UI; otherwise prefer the
    # buffer with more content. This makes Ink/alternate-screen TUIs capturable while keeping
    # classic shell panes unchanged.
    if txt_alt.strip() and (_looks_like_prompt(txt_alt) or (_non_ws_count(txt_alt) > _non_ws_count(txt_norm))):
        return ansi_alt, txt_alt
    return ansi_norm, txt_norm


def capture_pane(
    target: str,
    socket_name: str,
    capture_mode: str,
    ansi_path: Path,
    txt_path: Path,
    settle_ms: int,
    settle_attempts: int,
    buffer_suffix: str,
) -> None:
    last_ansi = ""
    last_txt = ""
    for attempt in range(max(settle_attempts, 1)):
        ansi, txt = capture_raw(target, capture_mode, socket_name, buffer_suffix)
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
    parser.add_argument(
        "--tmux-socket",
        default="",
        help="tmux socket name (tmux -L <name>). Use this for isolated capture servers.",
    )
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between frames")
    parser.add_argument("--duration", type=float, default=60.0, help="total duration in seconds")
    parser.add_argument("--frames", type=int, default=0, help="number of frames (overrides duration if >0)")
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
    parser.add_argument(
        "--resize-pane",
        action="store_true",
        help="resize the tmux pane to --cols/--rows before capturing (only allowed for breadboard_test_* sessions)",
    )
    parser.add_argument("--out-root", default=None, help="output root directory")
    parser.add_argument("--scenario", default="capture", help="scenario name for output folder")
    parser.add_argument(
        "--run-id",
        default="",
        help="optional stable run folder name. If omitted, a timestamp is used.",
    )
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
    parser.add_argument(
        "--session-prefix-guard",
        default="breadboard_test_",
        help="require target session name to start with this prefix (set empty to disable).",
    )
    parser.add_argument(
        "--protected-sessions",
        default="bb_tui_codex_dev,bb_engine_codex_dev,bb_atp",
        help="comma-separated protected sessions (always blocked unless --allow-any-session).",
    )
    parser.add_argument(
        "--allow-any-session",
        action="store_true",
        help="allow targeting sessions that do not match --session-prefix-guard (and protected sessions).",
    )

    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    default_out = repo_root.parent / "docs_tmp" / "tmux_captures"
    out_root = Path(args.out_root).expanduser() if args.out_root else default_out

    capture_mode = "scrollback" if args.scrollback else args.capture_mode

    return CaptureConfig(
        target=args.target,
        tmux_socket=str(args.tmux_socket or "").strip(),
        interval=max(args.interval, 0.05),
        duration=max(args.duration, 0.0),
        frames=max(args.frames, 0),
        capture_mode=capture_mode,
        render_png=bool(args.render_png),
        cols=args.cols if args.cols > 0 else None,
        rows=args.rows if args.rows > 0 else None,
        resize_pane=bool(args.resize_pane),
        out_root=str(out_root),
        scenario=args.scenario,
        run_id=str(args.run_id or "").strip(),
        settle_ms=max(args.settle_ms, 0),
        settle_attempts=max(args.settle_attempts, 1),
        session_prefix_guard=str(args.session_prefix_guard or "").strip(),
        protected_sessions=str(args.protected_sessions or "").strip(),
        allow_any_session=bool(args.allow_any_session),
    )


def main() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    config = parse_args()
    script_dir = Path(__file__).resolve().parent
    out_root = Path(config.out_root)
    ts = run_timestamp()
    run_folder = config.run_id or ts
    run_dir = out_root / config.scenario / run_folder
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    session = session_from_target(config.target)
    # Safety: default to allowing only breadboard_test_* automation sessions and blocking
    # known protected sessions unless the caller explicitly opts out.
    protected = {
        token.strip()
        for token in (getattr(config, "protected_sessions", "") or "").split(",")
        if token.strip()
    }
    session_prefix_guard = str(getattr(config, "session_prefix_guard", "") or "").strip()
    allow_any = bool(getattr(config, "allow_any_session", False))
    if not allow_any:
        if session_prefix_guard and (not session or not session.startswith(session_prefix_guard)):
            raise RuntimeError(
                f"refusing to capture from non-test session (target={config.target!r}); "
                f"expected session prefix {session_prefix_guard!r}. Pass --allow-any-session to override."
            )
        if session and session in protected:
            raise RuntimeError(
                f"refusing to capture from protected session (target={config.target!r}). "
                "Pass --allow-any-session to override."
            )

    ensure_target_exists(config.target, config.tmux_socket)

    requested_cols = config.cols
    requested_rows = config.rows
    if config.resize_pane:
        session_name = config.target.split(":", 1)[0].strip()
        if not session_name.startswith("breadboard_test_"):
            raise RuntimeError(
                f"--resize-pane is only allowed for breadboard_test_* sessions (target={config.target!r})"
            )
        if config.cols is None or config.rows is None:
            raise RuntimeError("--resize-pane requires both --cols and --rows")
        # For single-pane windows, resize-pane is often a no-op because tmux
        # forces the pane to fill the entire window. We resize the window first,
        # then resize the pane as a best-effort for split layouts.
        window_target = window_target_from_pane_target(config.target)
        run_tmux(
            ["resize-window", "-t", window_target, "-x", str(int(config.cols)), "-y", str(int(config.rows))],
            config.tmux_socket,
        )
        run_tmux(
            ["resize-pane", "-t", config.target, "-x", str(int(config.cols)), "-y", str(int(config.rows))],
            config.tmux_socket,
        )
        # Give the UI a moment to settle after a resize before we start polling.
        time.sleep(0.25)

    base_cols, base_rows = get_pane_size(config.target, config.tmux_socket)
    # If we resized, always trust the *actual* pane size for rendering.
    if config.resize_pane:
        cols, rows = base_cols, base_rows
    else:
        cols = config.cols or base_cols
        rows = config.rows or base_rows
    dynamic_size = config.cols is None and config.rows is None

    meta = {
        "target": config.target,
        "tmux_socket": config.tmux_socket,
        "interval": config.interval,
        "duration": config.duration,
        "frames": config.frames,
        "capture_mode": config.capture_mode,
        "render_png": config.render_png,
        "cols": cols,
        "rows": rows,
        "resize_pane": config.resize_pane,
        "requested_cols": requested_cols,
        "requested_rows": requested_rows,
        "dynamic_size": dynamic_size,
        "scenario": config.scenario,
        "run_id": config.run_id or None,
        "settle_ms": config.settle_ms,
        "settle_attempts": config.settle_attempts,
        "session_prefix_guard": config.session_prefix_guard,
        "protected_sessions": config.protected_sessions,
        "allow_any_session": config.allow_any_session,
        "started_at": ts,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    # Keep an exact one-shot snapshot of the first visible frame for easy QA.
    capture_pane(
        config.target,
        config.tmux_socket,
        config.capture_mode,
        run_dir / "initial.ansi",
        run_dir / "initial.txt",
        config.settle_ms,
        config.settle_attempts,
        buffer_suffix=run_folder,
    )

    index_path = run_dir / "index.jsonl"
    frame_count = config.frames
    end_time = time.monotonic() + config.duration if config.frames == 0 else None
    next_tick = time.monotonic()

    i = 0
    index_file: TextIO
    index_file = index_path.open("a", encoding="utf-8")
    while True:
        # Stop is handled gracefully: we finish the current loop iteration
        # (capture + optional PNG) then exit after writing to index.jsonl.
        stop_after_this_frame = bool(_stop_requested)
        now = time.monotonic()
        if end_time is not None and now >= end_time:
            break
        if frame_count and i >= frame_count:
            break
        if now < next_tick:
            time.sleep(max(0.0, next_tick - now))

        i += 1
        if dynamic_size:
            cols, rows = get_pane_size(config.target, config.tmux_socket)
        frame_id = f"frame_{i:04d}"
        ansi_path = frames_dir / f"{frame_id}.ansi"
        txt_path = frames_dir / f"{frame_id}.txt"
        png_path = frames_dir / f"{frame_id}.png"

        capture_pane(
            config.target,
            config.tmux_socket,
            config.capture_mode,
            ansi_path,
            txt_path,
            config.settle_ms,
            config.settle_attempts,
            buffer_suffix=f"{run_folder}_{frame_id}",
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
        if stop_after_this_frame:
            break
    index_file.close()

    print(f"[tmux-capture] wrote {i} frames -> {run_dir}")


if __name__ == "__main__":
    main()
