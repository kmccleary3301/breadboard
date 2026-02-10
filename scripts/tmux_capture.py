#!/usr/bin/env python3
"""
Unified entrypoint for tmux capture tooling.

Why:
- We want one predictable CLI for polling, scenario execution, and PNG rendering.
- Defaults write under <ray_SCE>/docs_tmp so outputs do not get accidentally committed.

This script is intentionally thin: it delegates to the underlying tools so each one
can still be used directly.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_script(script_name: str, argv: list[str]) -> None:
    script_path = Path(__file__).resolve().parent / script_name
    subprocess.run([sys.executable, str(script_path), *argv], check=True)


def cmd_poll(ns: argparse.Namespace) -> None:
    argv: list[str] = [
        "--target",
        ns.target,
        "--interval",
        str(ns.interval),
        "--duration",
        str(ns.duration),
        "--capture-mode",
        ns.capture_mode,
        "--scenario",
        ns.scenario,
        "--settle-ms",
        str(ns.settle_ms),
        "--settle-attempts",
        str(ns.settle_attempts),
    ]
    if ns.frames and ns.frames > 0:
        argv += ["--frames", str(ns.frames)]
    if ns.out_root:
        argv += ["--out-root", ns.out_root]
    if ns.png:
        argv += ["--png"]
    else:
        argv += ["--no-png"]
    if ns.cols and ns.cols > 0:
        argv += ["--cols", str(ns.cols)]
    if ns.rows and ns.rows > 0:
        argv += ["--rows", str(ns.rows)]
    run_script("tmux_capture_poll.py", argv)


def cmd_scenario(ns: argparse.Namespace) -> None:
    argv: list[str] = [
        "--target",
        ns.target,
        "--scenario",
        ns.scenario,
        "--duration",
        str(ns.duration),
        "--interval",
        str(ns.interval),
        "--settle-ms",
        str(ns.settle_ms),
        "--settle-attempts",
        str(ns.settle_attempts),
    ]
    if ns.actions:
        argv += ["--actions", ns.actions]
    if ns.out_root:
        argv += ["--out-root", ns.out_root]
    if ns.no_png:
        argv += ["--no-png"]
    if ns.capture_label:
        argv += ["--capture-label", ns.capture_label]
    if ns.clear_before_send:
        argv += ["--clear-before-send"]
        argv += ["--clear-mode", ns.clear_mode]
    if ns.wait_idle_accept_active_timeout:
        argv += ["--wait-idle-accept-active-timeout"]
    if ns.wait_idle_accept_stable_active_after and ns.wait_idle_accept_stable_active_after > 0:
        argv += ["--wait-idle-accept-stable-active-after", str(ns.wait_idle_accept_stable_active_after)]
    run_script("run_tmux_capture_scenario.py", argv)


def cmd_render(ns: argparse.Namespace) -> None:
    argv: list[str] = []
    if ns.target:
        argv += ["--target", ns.target]
    if ns.ansi:
        argv += ["--ansi", ns.ansi, "--cols", str(ns.cols), "--rows", str(ns.rows)]
    if ns.out:
        argv += ["--out", ns.out]
    if ns.scrollback:
        argv += ["--scrollback"]
    run_script("tmux_capture_to_png.py", argv)


def cmd_preflight(ns: argparse.Namespace) -> None:
    argv: list[str] = ["--target", ns.target]
    if ns.provider:
        argv += ["--provider", ns.provider]
    if ns.strict:
        argv += ["--strict"]
    if ns.session_prefix_guard is not None:
        argv += ["--session-prefix-guard", ns.session_prefix_guard]
    if ns.protected_sessions:
        argv += ["--protected-sessions", ns.protected_sessions]
    run_script("tmux_capture_preflight.py", argv)


def cmd_validate(ns: argparse.Namespace) -> None:
    argv: list[str] = ["--run-dir", ns.run_dir]
    if ns.strict:
        argv += ["--strict"]
    run_script("validate_tmux_capture_run.py", argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tmux_capture", description="Unified tmux capture tooling.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    poll = sub.add_parser("poll", help="poll a tmux pane and write .txt/.ansi/.png frames")
    poll.add_argument("--target", required=True, help="tmux target (session:window.pane)")
    poll.add_argument("--refresh-interval", "--interval", dest="interval", type=float, default=0.5)
    poll.add_argument("--duration", type=float, default=60.0)
    poll.add_argument("--frames", type=int, default=0, help="if >0, capture exactly N frames (overrides duration)")
    poll.add_argument("--capture-mode", choices=("pane", "scrollback"), default="pane")
    poll.add_argument("--png", dest="png", action="store_true")
    poll.add_argument("--no-png", dest="png", action="store_false")
    poll.set_defaults(png=True)
    poll.add_argument("--cols", type=int, default=0)
    poll.add_argument("--rows", type=int, default=0)
    poll.add_argument("--out-root", default="", help="optional override for capture root")
    poll.add_argument("--scenario", default="capture", help="scenario folder name")
    poll.add_argument("--settle-ms", type=int, default=140)
    poll.add_argument("--settle-attempts", type=int, default=5)
    poll.set_defaults(func=cmd_poll)

    scenario = sub.add_parser("scenario", help="run a scripted interaction + capture frames")
    scenario.add_argument("--target", required=True, help="tmux target (session:window.pane)")
    scenario.add_argument("--scenario", required=True, help="scenario id (used as output folder)")
    scenario.add_argument("--actions", default="", help="path to actions.json (optional)")
    scenario.add_argument("--duration", type=float, default=180.0)
    scenario.add_argument("--interval", type=float, default=0.5)
    scenario.add_argument("--out-root", default="", help="optional override for capture root")
    scenario.add_argument("--settle-ms", type=int, default=140)
    scenario.add_argument("--settle-attempts", type=int, default=5)
    scenario.add_argument("--no-png", action="store_true", help="skip PNG rendering")
    scenario.add_argument("--capture-label", default="", help="label saved into run manifest/summary")
    scenario.add_argument("--clear-before-send", action="store_true")
    scenario.add_argument(
        "--clear-mode",
        default="auto",
        choices=("auto", "codex_auto", "ctrl_u", "ctrl_a_ctrl_k", "none", "escape"),
    )
    scenario.add_argument("--wait-idle-accept-active-timeout", action="store_true")
    scenario.add_argument("--wait-idle-accept-stable-active-after", type=float, default=0.0)
    scenario.set_defaults(func=cmd_scenario)

    render = sub.add_parser("render", help="render a tmux pane or ANSI file to PNG")
    render.add_argument("--target", default="", help="tmux target (session:window.pane)")
    render.add_argument("--ansi", default="", help="path to .ansi file")
    render.add_argument("--cols", type=int, default=0, help="required with --ansi")
    render.add_argument("--rows", type=int, default=0, help="required with --ansi")
    render.add_argument("--out", default="", help="output PNG path")
    render.add_argument("--scrollback", action="store_true")
    render.set_defaults(func=cmd_render)

    preflight = sub.add_parser("preflight", help="sanity checks before running automation against a target pane")
    preflight.add_argument("--target", required=True)
    preflight.add_argument("--provider", default="", help="claude|codex (optional)")
    preflight.add_argument("--strict", action="store_true")
    preflight.add_argument("--session-prefix-guard", default="breadboard_test_")
    preflight.add_argument("--protected-sessions", default="bb_tui_codex_dev,bb_engine_codex_dev,bb_atp")
    preflight.set_defaults(func=cmd_preflight)

    validate = sub.add_parser("validate", help="validate a run folder produced by scenario runner")
    validate.add_argument("--run-dir", required=True)
    validate.add_argument("--strict", action="store_true")
    validate.set_defaults(func=cmd_validate)

    return parser


def main() -> None:
    parser = build_parser()
    ns = parser.parse_args()
    ns.func(ns)


if __name__ == "__main__":
    main()

