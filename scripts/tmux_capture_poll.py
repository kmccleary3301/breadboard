#!/usr/bin/env python3
"""
Poll a tmux pane and capture frames to .txt/.ansi/.png at a fixed interval.

Default behavior captures the *visible pane* (top to bottom) so landing headers
and input bars are consistently included.
PNG rendering is profile-locked by default for deterministic visuals.

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
from typing import Any, Optional, TextIO

from tmux_capture_time import run_timestamp
from tmux_capture_render_profile import (
    DEFAULT_RENDER_PROFILE_ID,
    SUPPORTED_RENDER_PROFILES,
    resolve_render_profile,
)


@dataclass
class CaptureConfig:
    target: str
    tmux_socket: str
    interval: float
    duration: float
    frames: int
    capture_mode: str
    tail_lines: int
    final_tail_lines: int
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
    fullpane_start_markers: tuple[str, ...]
    fullpane_max_lines: int
    fullpane_render_max_rows: int
    fullpane_png_source: str
    render_profile: str


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


def trim_fullpane_from_markers(
    ansi: str,
    txt: str,
    start_markers: tuple[str, ...],
    max_lines: int,
) -> tuple[str, str]:
    txt_lines = txt.splitlines()
    ansi_lines = ansi.splitlines()
    start_idx = 0
    # Prefer the last marker to avoid duplicate transcript windows after full-screen redraws.
    for i, line in enumerate(txt_lines):
        lowered = line.lower()
        if any(marker.lower() in lowered for marker in start_markers if marker):
            start_idx = i
    txt_lines = txt_lines[start_idx:]
    if start_idx < len(ansi_lines):
        ansi_lines = ansi_lines[start_idx:]
    if max_lines > 0:
        txt_lines = txt_lines[-max_lines:]
        ansi_lines = ansi_lines[-max_lines:]
    trimmed_txt = "\n".join(txt_lines)
    trimmed_ansi = "\n".join(ansi_lines)
    return trimmed_ansi, trimmed_txt


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


def capture_raw(
    target: str,
    capture_mode: str,
    socket_name: str,
    buffer_suffix: str,
    tail_lines: int,
    fullpane_start_markers: tuple[str, ...],
    fullpane_max_lines: int,
) -> tuple[str, str]:
    def _non_ws_count(s: str) -> int:
        return sum(1 for ch in s if not ch.isspace())

    def _line_score(text: str) -> int:
        return sum(1 for line in text.splitlines() if line.strip())

    def _looks_like_prompt(text: str) -> bool:
        # Heuristic only: use stable prompt/ready tokens that tend to appear in the visible UI.
        tokens = ("for shortcuts", "context left", "OpenAI Codex", "❯")
        return any(tok in text for tok in tokens)

    def _contains_fullpane_marker(text: str) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in fullpane_start_markers if marker)

    def _capture_one(
        alternate: bool,
        *,
        start_line: str | None = None,
        end_line: str | None = None,
    ) -> tuple[str, str]:
        a = "-a" if alternate else ""
        try:
            if capture_mode in {"scrollback", "tail"} or start_line is not None or end_line is not None:
                capture_start = start_line
                capture_end = end_line
                if capture_mode == "scrollback" and capture_start is None:
                    capture_start = "-"
                if capture_mode == "tail":
                    if capture_start is None:
                        capture_start = f"-{max(1, int(tail_lines))}"
                args_ansi = ["capture-pane"]
                if a:
                    args_ansi.append(a)
                args_ansi += ["-e", "-N", "-p"]
                if capture_start is not None:
                    args_ansi += ["-S", capture_start]
                if capture_end is not None:
                    args_ansi += ["-E", capture_end]
                args_ansi += ["-t", target]
                ansi_out = run_tmux(args_ansi, socket_name)

                args_txt = ["capture-pane"]
                if a:
                    args_txt.append(a)
                args_txt += ["-N", "-p"]
                if capture_start is not None:
                    args_txt += ["-S", capture_start]
                if capture_end is not None:
                    args_txt += ["-E", capture_end]
                args_txt += ["-t", target]
                txt_out = run_tmux(args_txt, socket_name)
                return ansi_out, txt_out

            args_ansi = ["capture-pane"]
            if a:
                args_ansi.append(a)
            args_ansi += ["-e", "-N", "-p", "-t", target]
            ansi_out = run_tmux(args_ansi, socket_name)

            args_txt = ["capture-pane"]
            if a:
                args_txt.append(a)
            args_txt += ["-N", "-p", "-t", target]
            txt_out = run_tmux(args_txt, socket_name)
            return ansi_out, txt_out
        except subprocess.CalledProcessError as exc:
            # `capture-pane -a` returns non-zero with "no alternate screen" when there
            # is no alternate buffer. Treat that as empty alt capture and fall back.
            if alternate and "no alternate screen" in (exc.stderr or "").lower():
                return "", ""
            raise

    def _capture_tail_window(alternate: bool) -> tuple[str, str]:
        # Capture only a bounded trailing window ending at the most recent line.
        return _capture_one(
            alternate,
            start_line=f"-{max(1, int(tail_lines))}",
        )

    ansi_norm, txt_norm = _capture_one(alternate=False)
    ansi_alt, txt_alt = _capture_one(alternate=True)

    if capture_mode == "fullpane":
        # Full-pane mode captures the full backing buffer and trims from a stable
        # start marker (typically the landing card) through the current bottom.
        full_ansi_norm, full_txt_norm = _capture_one(alternate=False, start_line="-")
        full_ansi_alt, full_txt_alt = _capture_one(alternate=True, start_line="-")
        selected_ansi = full_ansi_norm
        selected_txt = full_txt_norm
        if full_txt_alt.strip():
            alt_has_marker = _contains_fullpane_marker(full_txt_alt)
            norm_has_marker = _contains_fullpane_marker(full_txt_norm)
            if alt_has_marker and not norm_has_marker:
                selected_ansi, selected_txt = full_ansi_alt, full_txt_alt
            elif _looks_like_prompt(full_txt_alt) and not _looks_like_prompt(full_txt_norm):
                selected_ansi, selected_txt = full_ansi_alt, full_txt_alt
            elif _non_ws_count(full_txt_alt) > _non_ws_count(full_txt_norm):
                selected_ansi, selected_txt = full_ansi_alt, full_txt_alt
        return trim_fullpane_from_markers(
            selected_ansi,
            selected_txt,
            fullpane_start_markers,
            fullpane_max_lines,
        )

    # Prefer whichever buffer looks like an interactive prompt/ready UI; otherwise prefer the
    # buffer with more content. This makes Ink/alternate-screen TUIs capturable while keeping
    # classic shell panes unchanged.
    selected_ansi = ansi_norm
    selected_txt = txt_norm
    if txt_alt.strip() and (_looks_like_prompt(txt_alt) or (_non_ws_count(txt_alt) > _non_ws_count(txt_norm))):
        selected_ansi = ansi_alt
        selected_txt = txt_alt

    # Legacy adaptive mode: when visible-pane content is too sparse, swap in a
    # bounded trailing history window for better replay observability.
    if capture_mode == "pane-auto":
        hist_ansi_norm, hist_txt_norm = _capture_tail_window(alternate=False)
        hist_ansi_alt, hist_txt_alt = _capture_tail_window(alternate=True)
        hist_ansi = hist_ansi_norm
        hist_txt = hist_txt_norm
        if hist_txt_alt.strip() and (
            _looks_like_prompt(hist_txt_alt) or (_non_ws_count(hist_txt_alt) > _non_ws_count(hist_txt_norm))
        ):
            hist_ansi = hist_ansi_alt
            hist_txt = hist_txt_alt
        selected_non_ws = _non_ws_count(selected_txt)
        history_non_ws = _non_ws_count(hist_txt)
        selected_lines = _line_score(selected_txt)
        history_lines = _line_score(hist_txt)
        # Prefer bounded history when it is materially richer than the visible
        # viewport (common when replay output briefly renders then collapses).
        if history_non_ws >= (selected_non_ws + 20) or history_lines >= (selected_lines + 3):
            return hist_ansi, hist_txt

    return selected_ansi, selected_txt


def capture_pane(
    target: str,
    socket_name: str,
    capture_mode: str,
    tail_lines: int,
    ansi_path: Path,
    txt_path: Path,
    settle_ms: int,
    settle_attempts: int,
    buffer_suffix: str,
    fullpane_start_markers: tuple[str, ...],
    fullpane_max_lines: int,
) -> None:
    last_ansi = ""
    last_txt = ""
    for attempt in range(max(settle_attempts, 1)):
        ansi, txt = capture_raw(
            target,
            capture_mode,
            socket_name,
            buffer_suffix,
            tail_lines,
            fullpane_start_markers,
            fullpane_max_lines,
        )
        if attempt > 0 and ansi == last_ansi and txt == last_txt:
            break
        last_ansi = ansi
        last_txt = txt
        if attempt + 1 < max(settle_attempts, 1):
            time.sleep(max(settle_ms, 0) / 1000.0)
    ansi_path.write_text(last_ansi, encoding="utf-8")
    txt_path.write_text(last_txt, encoding="utf-8")


def render_png(
    ansi_path: Path,
    png_path: Path,
    cols: int,
    rows: int,
    script_dir: Path,
    render_profile: str,
    *,
    snapshot: bool,
) -> None:
    cmd = [
        sys.executable,
        str(script_dir / "tmux_capture_to_png.py"),
        "--ansi",
        str(ansi_path),
        "--cols",
        str(cols),
        "--rows",
        str(rows),
        "--render-profile",
        render_profile,
        "--out",
        str(png_path),
    ]
    if snapshot:
        cmd.append("--snapshot")
    subprocess.run(cmd, check=True)


def load_render_lock_quick_metrics(render_lock_path: Path) -> dict[str, int] | None:
    if not render_lock_path.exists():
        return None
    try:
        payload = json.loads(render_lock_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    row_occupancy = payload.get("row_occupancy")
    if not isinstance(row_occupancy, dict):
        return None
    try:
        return {
            "missing_count": int(row_occupancy.get("missing_count", 0)),
            "extra_count": int(row_occupancy.get("extra_count", 0)),
            "row_span_delta": int(row_occupancy.get("row_span_delta", 0)),
        }
    except Exception:
        return None


def load_row_parity_summary_quick_metrics(row_parity_summary_path: Path) -> dict[str, int] | None:
    if not row_parity_summary_path.exists():
        return None
    try:
        payload = json.loads(row_parity_summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    parity = payload.get("parity")
    if not isinstance(parity, dict):
        return None
    try:
        return {
            "missing_count": int(parity.get("missing_count", 0)),
            "extra_count": int(parity.get("extra_count", 0)),
            "row_span_delta": int(parity.get("row_span_delta", 0)),
        }
    except Exception:
        return None


def resolve_png_render_geometry(
    *,
    capture_mode: str,
    viewport_rows: int,
    frame_line_count: int,
    fullpane_render_max_rows: int,
) -> tuple[int, bool]:
    """
    Decide PNG render rows + snapshot mode.

    For fullpane captures, default PNGs should reflect terminal viewport behavior
    (fixed height, latest rows), not an ever-growing transcript canvas.
    """
    rows_for_render = max(1, int(viewport_rows))
    snapshot = True

    if capture_mode == "fullpane":
        # Render latest window for fullpane by default; avoid giant blank canvases.
        snapshot = False
        if fullpane_render_max_rows > 0:
            expanded_rows = max(rows_for_render, max(1, int(frame_line_count)))
            rows_for_render = min(expanded_rows, max(1, int(fullpane_render_max_rows)))

    return rows_for_render, snapshot


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
        choices=("pane", "pane-auto", "tail", "scrollback", "fullpane"),
        default="pane",
        help=(
            "pane=full visible pane top->bottom (default); "
            "pane-auto=visible pane with bounded-history fallback; "
            "tail=sliding tail window; "
            "scrollback=entire pane history; "
            "fullpane=trimmed full buffer from landing marker to current bottom"
        ),
    )
    parser.add_argument(
        "--scrollback",
        action="store_true",
        help="deprecated alias for --capture-mode scrollback",
    )
    parser.add_argument("--png", dest="render_png", action="store_true", help="render PNG for each frame")
    parser.add_argument("--no-png", dest="render_png", action="store_false", help="skip PNG render")
    parser.set_defaults(render_png=True)
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=120,
        help="line count for tail captures (capture-mode=tail and pane-auto fallback)",
    )
    parser.add_argument(
        "--final-tail-lines",
        type=int,
        default=0,
        help="optional: additionally capture a final tail snapshot with this many lines",
    )
    parser.add_argument(
        "--fullpane-start-markers",
        default="BreadBoard v,No conversation yet",
        help=(
            "comma-separated start markers for --capture-mode fullpane; capture starts "
            "at the last matching line and includes everything through current bottom"
        ),
    )
    parser.add_argument(
        "--fullpane-max-lines",
        type=int,
        default=0,
        help="optional max line count for fullpane captures (0 = unlimited)",
    )
    parser.add_argument(
        "--fullpane-render-max-rows",
        type=int,
        default=0,
        help="optional max rendered PNG rows for fullpane captures (0 = viewport rows)",
    )
    parser.add_argument(
        "--fullpane-png-source",
        choices=("fullpane", "pane"),
        default="pane",
        help=(
            "for --capture-mode fullpane, select PNG source buffer: "
            "pane=live visible pane (recommended), fullpane=trimmed full-buffer frame"
        ),
    )
    parser.add_argument(
        "--render-profile",
        default=DEFAULT_RENDER_PROFILE_ID,
        choices=SUPPORTED_RENDER_PROFILES,
        help=f"ANSI->PNG render profile lock (default: {DEFAULT_RENDER_PROFILE_ID})",
    )
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
    default_out = repo_root / "docs_tmp" / "tmux_captures"
    out_root = Path(args.out_root).expanduser() if args.out_root else default_out

    capture_mode = "scrollback" if args.scrollback else args.capture_mode
    resolved_profile = resolve_render_profile(args.render_profile)
    render_profile = resolved_profile.id if resolved_profile is not None else args.render_profile

    return CaptureConfig(
        target=args.target,
        tmux_socket=str(args.tmux_socket or "").strip(),
        interval=max(args.interval, 0.05),
        duration=max(args.duration, 0.0),
        frames=max(args.frames, 0),
        capture_mode=capture_mode,
        tail_lines=max(1, int(args.tail_lines)),
        final_tail_lines=max(0, int(args.final_tail_lines)),
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
        fullpane_start_markers=tuple(token.strip() for token in str(args.fullpane_start_markers).split(",") if token.strip()),
        fullpane_max_lines=max(0, int(args.fullpane_max_lines)),
        fullpane_render_max_rows=max(0, int(args.fullpane_render_max_rows)),
        fullpane_png_source=str(args.fullpane_png_source),
        render_profile=str(render_profile),
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
        "tail_lines": config.tail_lines,
        "final_tail_lines": config.final_tail_lines,
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
        "fullpane_start_markers": list(config.fullpane_start_markers),
        "fullpane_max_lines": config.fullpane_max_lines,
        "fullpane_render_max_rows": config.fullpane_render_max_rows,
        "fullpane_png_source": config.fullpane_png_source,
        "render_profile": config.render_profile,
        "started_at": ts,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    # Keep an exact one-shot snapshot of the first visible frame for easy QA.
    capture_pane(
        config.target,
        config.tmux_socket,
        config.capture_mode,
        config.tail_lines,
        run_dir / "initial.ansi",
        run_dir / "initial.txt",
        config.settle_ms,
        config.settle_attempts,
        buffer_suffix=run_folder,
        fullpane_start_markers=config.fullpane_start_markers,
        fullpane_max_lines=config.fullpane_max_lines,
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
            config.tail_lines,
            ansi_path,
            txt_path,
            config.settle_ms,
            config.settle_attempts,
            buffer_suffix=f"{run_folder}_{frame_id}",
            fullpane_start_markers=config.fullpane_start_markers,
            fullpane_max_lines=config.fullpane_max_lines,
        )
        if config.render_png:
            png_source_mode = config.capture_mode
            png_ansi_path = ansi_path
            png_txt_path = txt_path
            if config.capture_mode == "fullpane" and config.fullpane_png_source == "pane":
                # Render from the live visible pane to avoid full-buffer history deadspace.
                png_source_mode = "pane"
                png_ansi_path = frames_dir / f"{frame_id}.render_pane.ansi"
                png_txt_path = frames_dir / f"{frame_id}.render_pane.txt"
                capture_pane(
                    config.target,
                    config.tmux_socket,
                    "pane",
                    config.tail_lines,
                    png_ansi_path,
                    png_txt_path,
                    config.settle_ms,
                    config.settle_attempts,
                    buffer_suffix=f"{run_folder}_{frame_id}_render_pane",
                    fullpane_start_markers=config.fullpane_start_markers,
                    fullpane_max_lines=config.fullpane_max_lines,
                )

            frame_line_count = max(1, len(png_txt_path.read_text(encoding="utf-8", errors="replace").splitlines()))
            rows_for_render, snapshot_mode = resolve_png_render_geometry(
                capture_mode=png_source_mode,
                viewport_rows=rows,
                frame_line_count=frame_line_count,
                fullpane_render_max_rows=config.fullpane_render_max_rows,
            )
            render_png(
                png_ansi_path,
                png_path,
                cols,
                rows_for_render,
                script_dir,
                config.render_profile,
                snapshot=snapshot_mode,
            )

        render_lock_rel: str | None = None
        render_parity_summary_rel: str | None = None
        render_parity_quick: dict[str, int] | None = None
        if config.render_png:
            render_lock_path = png_path.with_suffix(".render_lock.json")
            if render_lock_path.exists():
                render_lock_rel = str(render_lock_path.relative_to(run_dir))
            row_parity_summary_path = png_path.with_suffix(".row_parity.json")
            if row_parity_summary_path.exists():
                render_parity_summary_rel = str(row_parity_summary_path.relative_to(run_dir))
                render_parity_quick = load_row_parity_summary_quick_metrics(row_parity_summary_path)
            if render_parity_quick is None and render_lock_path.exists():
                render_parity_quick = load_render_lock_quick_metrics(render_lock_path)

        record: dict[str, Any] = {
            "frame": i,
            "timestamp": time.time(),
            "cols": cols,
            "rows": rows,
            "ansi": str(ansi_path.relative_to(run_dir)),
            "text": str(txt_path.relative_to(run_dir)),
            "png": str(png_path.relative_to(run_dir)) if config.render_png else None,
            "render_lock": render_lock_rel,
            "render_parity_summary": render_parity_summary_rel,
            "render_parity": render_parity_quick,
        }
        index_file.write(json.dumps(record) + "\n")
        index_file.flush()

        next_tick += config.interval
        if stop_after_this_frame:
            break
    index_file.close()

    if config.final_tail_lines > 0:
        final_tail_ansi = run_dir / "final_tail.ansi"
        final_tail_txt = run_dir / "final_tail.txt"
        final_tail_png = run_dir / "final_tail.png"
        capture_pane(
            config.target,
            config.tmux_socket,
            "tail",
            config.final_tail_lines,
            final_tail_ansi,
            final_tail_txt,
            config.settle_ms,
            config.settle_attempts,
            buffer_suffix=f"{run_folder}_final_tail",
            fullpane_start_markers=config.fullpane_start_markers,
            fullpane_max_lines=config.fullpane_max_lines,
        )
        if config.render_png:
            # Keep final-tail PNG at viewport dimensions for quick visual review.
            render_png(
                final_tail_ansi,
                final_tail_png,
                cols,
                rows,
                script_dir,
                config.render_profile,
                snapshot=True,
            )

    print(f"[tmux-capture] wrote {i} frames -> {run_dir}")


if __name__ == "__main__":
    main()
