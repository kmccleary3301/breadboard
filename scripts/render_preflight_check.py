#!/usr/bin/env python3
"""
Preflight checks for screenshot rendering reproducibility.

Checks:
1) required tmux targets exist and match expected pane geometry,
2) locked render profile font checksums are valid,
3) emits machine-readable JSON report and non-zero exit on failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tmux_capture_render_profile import PROFILES_BY_ID
from tmux_capture_render_profile import validate_render_profile_lock


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
DEFAULT_BASELINE = WORKSPACE_ROOT / "docs_tmp" / "cli_phase_5" / "RENDER_BASELINE_LOCK_V1.json"


@dataclass(frozen=True)
class PaneInfo:
    session: str
    pane: str
    width: int
    height: int


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _list_tmux_panes(tmux_socket: str) -> list[PaneInfo]:
    cmd = [
        "tmux",
        "list-panes",
        "-a",
        "-F",
        "#{session_name}\t#{window_index}.#{pane_index}\t#{pane_width}\t#{pane_height}",
    ]
    if tmux_socket:
        cmd[1:1] = ["-L", tmux_socket]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    panes: list[PaneInfo] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) != 4:
            continue
        panes.append(
            PaneInfo(
                session=parts[0],
                pane=parts[1],
                width=int(parts[2]),
                height=int(parts[3]),
            )
        )
    return panes


def _check_tmux_targets(
    panes: list[PaneInfo], expected_targets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    by_session = {pane.session: pane for pane in panes}
    details: list[dict[str, Any]] = []
    errors: list[str] = []
    for target in expected_targets:
        session = str(target.get("session", "")).strip()
        expected_width = int(target.get("expected_cols", 0))
        expected_height = int(target.get("expected_rows", 0))
        pane = by_session.get(session)
        if pane is None:
            details.append(
                {
                    "session": session,
                    "status": "missing",
                    "expected_cols": expected_width,
                    "expected_rows": expected_height,
                }
            )
            errors.append(f"missing required tmux session: {session}")
            continue
        size_ok = pane.width == expected_width and pane.height == expected_height
        details.append(
            {
                "session": session,
                "status": "ok" if size_ok else "size_mismatch",
                "pane": pane.pane,
                "actual_cols": pane.width,
                "actual_rows": pane.height,
                "expected_cols": expected_width,
                "expected_rows": expected_height,
            }
        )
        if not size_ok:
            errors.append(
                f"session {session} size mismatch: expected {expected_width}x{expected_height}, got {pane.width}x{pane.height}"
            )
    return details, errors


def _check_render_profiles(profile_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    details: list[dict[str, Any]] = []
    errors: list[str] = []
    for profile_id in profile_ids:
        profile = PROFILES_BY_ID.get(profile_id)
        if profile is None:
            details.append({"profile_id": profile_id, "status": "unsupported"})
            errors.append(f"unsupported profile in baseline: {profile_id}")
            continue
        lock_errors = validate_render_profile_lock(profile)
        details.append(
            {
                "profile_id": profile_id,
                "status": "ok" if not lock_errors else "lock_check_failed",
                "errors": lock_errors,
            }
        )
        errors.extend(lock_errors)
    return details, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--tmux-socket", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    baseline_path = Path(args.baseline).expanduser().resolve()
    if not baseline_path.exists():
        raise FileNotFoundError(f"baseline manifest not found: {baseline_path}")
    baseline = _read_json(baseline_path)

    panes = _list_tmux_panes(args.tmux_socket)
    expected_targets = list(baseline.get("ground_truth_targets", []))
    profile_ids = list(baseline.get("profile_lock_checks", []))

    target_details, target_errors = _check_tmux_targets(panes, expected_targets)
    profile_details, profile_errors = _check_render_profiles(profile_ids)
    all_errors = [*target_errors, *profile_errors]

    report = {
        "baseline_manifest": str(baseline_path),
        "tmux_socket": args.tmux_socket,
        "checks": {
            "tmux_targets": target_details,
            "profile_locks": profile_details,
        },
        "ok": not all_errors,
        "errors": all_errors,
    }

    payload = json.dumps(report, indent=2)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
    print(payload)
    if all_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
