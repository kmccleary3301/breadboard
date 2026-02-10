#!/usr/bin/env python3
"""
Run paired tmux capture scenarios (e.g. Claude Code vs Breadboard Ink TUI) and produce a basic diff report.

This is meant to be an iteration tool:
- Captures frequent panel frames (txt/ansi/png) for each target.
- Produces a simple report comparing the final frame (text-level diff) for each pair.

It intentionally does NOT:
- launch tmux sessions for you
- log you into providers
- hard-gate CI

Use `scripts/run_tmux_capture_scenario.py` to create deterministic capture folders.
This script orchestrates two runs (claude + breadboard) per scenario and writes a comparison report.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def strip_ansi(value: str) -> str:
    # Fast, minimal strip (enough for structural diffs).
    import re

    return re.sub(r"\x1b\\[[0-9;]*m", "", value)


def find_latest_frame(frames_dir: Path, suffix: str) -> Path:
    candidates = sorted(frames_dir.glob(f"frame_*.{suffix}"))
    if not candidates:
        raise FileNotFoundError(f"no frames with suffix .{suffix} under {frames_dir}")
    return candidates[-1]


def render_unified_diff(expected: str, actual: str, from_name: str, to_name: str) -> str:
    exp_lines = expected.splitlines(keepends=True)
    act_lines = actual.splitlines(keepends=True)
    diff = difflib.unified_diff(exp_lines, act_lines, fromfile=from_name, tofile=to_name)
    return "".join(diff)


@dataclass
class TargetSpec:
    target: str
    actions: Path


@dataclass
class ScenarioSpec:
    id: str
    claude: TargetSpec
    breadboard: TargetSpec


@dataclass
class Defaults:
    duration: float
    interval: float
    cols: int
    rows: int
    settle_ms: int
    settle_attempts: int
    no_png: bool


def parse_manifest(path: Path) -> tuple[Defaults, list[ScenarioSpec]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults_raw = payload.get("defaults", {}) if isinstance(payload, dict) else {}

    defaults = Defaults(
        duration=float(defaults_raw.get("duration", 30)),
        interval=float(defaults_raw.get("interval", 0.5)),
        cols=int(defaults_raw.get("cols", 120)),
        rows=int(defaults_raw.get("rows", 40)),
        settle_ms=int(defaults_raw.get("settle_ms", 140)),
        settle_attempts=int(defaults_raw.get("settle_attempts", 5)),
        no_png=bool(defaults_raw.get("no_png", False)),
    )

    scenarios: list[ScenarioSpec] = []
    raw_scenarios = payload.get("scenarios", []) if isinstance(payload, dict) else []
    if not isinstance(raw_scenarios, list):
        raise ValueError("manifest scenarios must be an array")

    for item in raw_scenarios:
        if not isinstance(item, dict):
            raise ValueError("each scenario must be an object")
        sid = str(item.get("id", "")).strip()
        if not sid:
            raise ValueError("scenario missing id")

        def parse_target(key: str) -> TargetSpec:
            raw = item.get(key)
            if not isinstance(raw, dict):
                raise ValueError(f"scenario {sid}: missing {key} object")
            target = str(raw.get("target", "")).strip()
            actions = str(raw.get("actions", "")).strip()
            if not target or not actions:
                raise ValueError(f"scenario {sid}: {key} requires target and actions")
            return TargetSpec(target=target, actions=Path(actions).expanduser().resolve())

        scenarios.append(
            ScenarioSpec(
                id=sid,
                claude=parse_target("claude"),
                breadboard=parse_target("breadboard"),
            )
        )

    return defaults, scenarios


def default_out_root(repo_root: Path) -> Path:
    # Workspace-level docs_tmp (not the repo-local docs_tmp).
    return (repo_root.parent / "docs_tmp" / "claude_alignment").resolve()


def run_capture_scenario(
    repo_root: Path,
    *,
    target: str,
    scenario: str,
    actions: Path,
    out_root: Path,
    duration: float,
    interval: float,
    cols: int,
    rows: int,
    settle_ms: int,
    settle_attempts: int,
    no_png: bool,
) -> Path:
    cmd = [
        sys.executable,
        str(repo_root / "scripts" / "run_tmux_capture_scenario.py"),
        "--target",
        target,
        "--scenario",
        scenario,
        "--actions",
        str(actions),
        "--duration",
        str(duration),
        "--interval",
        str(interval),
        "--out-root",
        str(out_root),
        "--cols",
        str(cols),
        "--rows",
        str(rows),
        "--resize-pane",
        "--settle-ms",
        str(settle_ms),
        "--settle-attempts",
        str(settle_attempts),
    ]
    if no_png:
        cmd.append("--no-png")
    run(cmd)

    # run_tmux_capture_scenario writes a stable run folder; we find the newest under out_root/scenario.
    scenario_dir = out_root / scenario
    run_dirs = sorted([p for p in scenario_dir.iterdir() if p.is_dir()])
    if not run_dirs:
        raise RuntimeError(f"capture produced no run directories under {scenario_dir}")
    return run_dirs[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run paired Claude vs Breadboard tmux captures.")
    parser.add_argument("--manifest", required=True, help="YAML manifest with scenario pairs")
    parser.add_argument("--out-root", default="", help="output root (default: <repo_root>/../docs_tmp/claude_alignment)")
    parser.add_argument("--strip-ansi", action="store_true", help="strip ANSI before text diffing (recommended)")
    parser.add_argument("--png-diff", action="store_true", help="also compute a pixel diff of the final PNG frames")
    parser.add_argument(
        "--png-mask-rect",
        action="append",
        default=[],
        help="mask rect x,y,w,h applied to pixel diffs (repeatable)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_root = Path(args.out_root).expanduser().resolve() if args.out_root else default_out_root(repo_root)
    manifest_path = Path(args.manifest).expanduser().resolve()

    defaults, scenarios = parse_manifest(manifest_path)
    out_root.mkdir(parents=True, exist_ok=True)

    run_index: list[dict[str, Any]] = []

    for scenario in scenarios:
        scenario_out = out_root / scenario.id
        scenario_out.mkdir(parents=True, exist_ok=True)

        claude_run = run_capture_scenario(
            repo_root,
            target=scenario.claude.target,
            scenario=f"{scenario.id}/claude",
            actions=scenario.claude.actions,
            out_root=out_root,
            duration=defaults.duration,
            interval=defaults.interval,
            cols=defaults.cols,
            rows=defaults.rows,
            settle_ms=defaults.settle_ms,
            settle_attempts=defaults.settle_attempts,
            no_png=defaults.no_png,
        )
        breadboard_run = run_capture_scenario(
            repo_root,
            target=scenario.breadboard.target,
            scenario=f"{scenario.id}/breadboard",
            actions=scenario.breadboard.actions,
            out_root=out_root,
            duration=defaults.duration,
            interval=defaults.interval,
            cols=defaults.cols,
            rows=defaults.rows,
            settle_ms=defaults.settle_ms,
            settle_attempts=defaults.settle_attempts,
            no_png=defaults.no_png,
        )

        claude_txt = find_latest_frame(claude_run / "frames", "txt").read_text(encoding="utf-8")
        breadboard_txt = find_latest_frame(breadboard_run / "frames", "txt").read_text(encoding="utf-8")
        if args.strip_ansi:
            claude_txt = strip_ansi(claude_txt)
            breadboard_txt = strip_ansi(breadboard_txt)

        diff_text = render_unified_diff(
            expected=claude_txt,
            actual=breadboard_txt,
            from_name="claude",
            to_name="breadboard",
        )
        (scenario_out / "final_frame.diff.txt").write_text(diff_text, encoding="utf-8")

        diff_png_path = ""
        diff_png_summary = ""
        if args.png_diff:
            claude_png = find_latest_frame(claude_run / "frames", "png")
            breadboard_png = find_latest_frame(breadboard_run / "frames", "png")
            diff_png = scenario_out / "final_frame.diff.png"
            diff_summary = scenario_out / "final_frame.diff.summary.json"
            cmd = [
                sys.executable,
                str(repo_root / "scripts" / "pixel_diff_png.py"),
                "--a",
                str(claude_png),
                "--b",
                str(breadboard_png),
                "--out",
                str(diff_png),
                "--summary-out",
                str(diff_summary),
            ]
            for rect in args.png_mask_rect:
                cmd.extend(["--mask-rect", str(rect)])
            run(cmd)
            diff_png_path = str(diff_png)
            diff_png_summary = str(diff_summary)

        run_index.append(
            {
                "id": scenario.id,
                "claude_run": str(claude_run),
                "breadboard_run": str(breadboard_run),
                "diff": str(scenario_out / "final_frame.diff.txt"),
                "diff_png": diff_png_path or None,
                "diff_png_summary": diff_png_summary or None,
            }
        )

    (out_root / "index.json").write_text(json.dumps(run_index, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
