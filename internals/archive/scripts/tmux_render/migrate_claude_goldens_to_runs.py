#!/usr/bin/env python3
"""
Migrate Claude Code golden captures into per-run directories.

Legacy layout (single run stored directly under scenario dir):
  misc/claude_code_runs/goldens/<version>/<scenario>/{provider_dumps,normalized,workspace,home,stdout.json,...}

New layout (multiple runs per scenario):
  misc/claude_code_runs/goldens/<version>/<scenario>/runs/<run_id>/...
  misc/claude_code_runs/goldens/<version>/<scenario>/current -> runs/<run_id>

This script is intentionally conservative: it only moves known capture artifacts
and leaves any unknown files in place.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

CAPTURE_ENTRIES = (
    "provider_dumps",
    "normalized",
    "workspace",
    "home",
    "stdout.json",
    "stderr.txt",
    "exit_code.txt",
    "scenario.json",
)


@dataclass(frozen=True)
class ScenarioDir:
    path: Path
    version: str
    scenario: str


def _safe_timestamp_id(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.strftime("%Y%m%d_%H%M%S")


def _read_run_id_from_scenario_json(path: Path) -> Optional[str]:
    scenario_json = path / "scenario.json"
    if not scenario_json.exists():
        return None
    try:
        payload = json.loads(scenario_json.read_text(encoding="utf-8"))
    except Exception:
        return None
    run_id = payload.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    captured_at = payload.get("captured_at_utc")
    if isinstance(captured_at, str) and captured_at.endswith("Z"):
        try:
            dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")
        except Exception:
            return None
    return None


def _iter_scenarios(goldens_root: Path, *, version: Optional[str]) -> Iterable[ScenarioDir]:
    if version:
        versions = [goldens_root / version]
    else:
        versions = [p for p in goldens_root.iterdir() if p.is_dir()]
    for ver_dir in sorted(versions):
        for scenario_dir in sorted(ver_dir.iterdir()):
            if not scenario_dir.is_dir():
                continue
            if scenario_dir.name in {"runs", "current"}:
                continue
            yield ScenarioDir(path=scenario_dir, version=ver_dir.name, scenario=scenario_dir.name)


def _has_legacy_artifacts(scenario_dir: Path) -> bool:
    return any((scenario_dir / name).exists() for name in CAPTURE_ENTRIES)


def _ensure_current_symlink(scenario_dir: Path, run_dir: Path, *, dry_run: bool) -> None:
    current = scenario_dir / "current"
    if current.exists() and not current.is_symlink():
        if not dry_run:
            if current.is_dir():
                for child in current.iterdir():
                    raise RuntimeError(f"Refusing to replace non-empty current dir: {current} (found {child})")
            current.unlink()
    if not dry_run:
        if current.is_symlink() or current.exists():
            current.unlink()
        current.symlink_to(run_dir)


def migrate_scenario(scenario: ScenarioDir, *, dry_run: bool) -> bool:
    base = scenario.path
    runs_dir = base / "runs"
    if not _has_legacy_artifacts(base):
        return False

    run_id = _read_run_id_from_scenario_json(base) or _safe_timestamp_id(base)
    target_run = runs_dir / run_id
    if target_run.exists():
        target_run = runs_dir / f"legacy_{run_id}"
    if target_run.exists():
        raise RuntimeError(f"Refusing to overwrite existing run dir: {target_run}")

    if not dry_run:
        target_run.mkdir(parents=True, exist_ok=True)

    moved_any = False
    for name in CAPTURE_ENTRIES:
        src = base / name
        if not src.exists():
            continue
        dst = target_run / name
        moved_any = True
        if dry_run:
            continue
        src.rename(dst)

    if moved_any:
        _ensure_current_symlink(base, target_run, dry_run=dry_run)
    return moved_any


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Claude Code goldens into per-run directories.")
    parser.add_argument(
        "--goldens-root",
        default="misc/claude_code_runs/goldens",
        help="Root directory containing version/scenario golden captures.",
    )
    parser.add_argument(
        "--version",
        default="",
        help="Optional version label to migrate (e.g. 2.0.72). Defaults to all versions.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without moving files.")
    args = parser.parse_args()

    goldens_root = (REPO_ROOT / args.goldens_root).resolve()
    if not goldens_root.exists():
        raise SystemExit(f"[migrate-claude-goldens] missing: {goldens_root}")

    count = 0
    for scenario in _iter_scenarios(goldens_root, version=(args.version or None)):
        changed = migrate_scenario(scenario, dry_run=args.dry_run)
        if changed:
            count += 1

    mode = "dry-run" if args.dry_run else "migrated"
    print(f"[migrate-claude-goldens] {mode} {count} scenario(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

