#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _find_first_existing(source_dirs: list[Path], names: list[str]) -> Path | None:
    for source_dir in source_dirs:
        for name in names:
            candidate = source_dir / name
            if candidate.exists():
                return candidate
    return None


def _copy_if_exists(src: Path, dst: Path) -> str | None:
    if not src.exists():
        return None
    if src.resolve() == dst.resolve():
        return str(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return str(dst)


def _copy_first_existing(source_dirs: list[Path], names: list[str], dst_dir: Path) -> str | None:
    candidate = _find_first_existing(source_dirs, names)
    if candidate is None:
        return None
    return _copy_if_exists(candidate, dst_dir / candidate.name)


def _build_source_dirs(artifact_dir: Path, archive_day_dir: Path, extra_source_dirs: list[Path] | None = None) -> list[Path]:
    source_dirs: list[Path] = [artifact_dir]
    if extra_source_dirs:
        source_dirs.extend(extra_source_dirs)
    source_dirs.append(archive_day_dir)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in source_dirs:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _copy_plan() -> dict[str, list[str]]:
    return {
        "bench": ["atp_firecracker_repl_bench.json", "atp_firecracker_repl_bench.latest.json"],
        "load": ["atp_firecracker_repl_load.latest.json", "atp_firecracker_repl_load.json"],
        "ops_digest": [
            "atp_ops_digest.ops_nightly.local.json",
            "atp_ops_digest.latest.json",
            "atp_ops_digest.local.json",
            "atp_ops_digest.json",
        ],
        "seven_day": [
            "atp_seven_day_stability_report.ops_nightly.local.json",
            "atp_seven_day_stability_report.latest.json",
        ],
        "stability": [
            "atp_snapshot_pool_stability_summary.ops_nightly.local.json",
            "atp_snapshot_pool_stability_summary.latest.json",
        ],
        "recovery": ["atp_state_ref_recovery_report.latest.json"],
        "evolake": [
            "evolake_toy_campaign_nightly.json",
            "evolake_toy_campaign_nightly.latest.json",
            "evolake_toy_campaign_nightly.local.json",
            "evolake_toy_campaign_nightly.ops_nightly.local.json",
        ],
    }


def _evolake_payload_ok(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    if "ok" in payload:
        return bool(payload.get("ok"))
    runs_failed = payload.get("runs_failed")
    runs_requested = payload.get("runs_requested")
    runs_passed = payload.get("runs_passed")
    if isinstance(runs_failed, int) and runs_failed == 0:
        if isinstance(runs_requested, int) and isinstance(runs_passed, int):
            return runs_passed == runs_requested
        return True
    return str(payload.get("classification", "")).lower() == "stable_pass"


def _read_atp_ok(source_dirs: list[Path]) -> bool:
    candidate = _find_first_existing(
        source_dirs,
        [
            "atp_ops_digest.ops_nightly.local.json",
            "atp_ops_digest.latest.json",
            "atp_ops_digest.local.json",
            "atp_ops_digest.json",
        ],
    )
    if not candidate:
        return False
    payload = _load_json(candidate)
    if not payload:
        return False
    if "overall_ok" in payload:
        return bool(payload.get("overall_ok"))
    if "decision_state" in payload:
        return str(payload.get("decision_state")).lower() == "green"
    return False


def _read_evolake_ok(source_dirs: list[Path]) -> bool:
    candidate = _find_first_existing(
        source_dirs,
        [
            "evolake_toy_campaign_nightly.json",
            "evolake_toy_campaign_nightly.latest.json",
            "evolake_toy_campaign_nightly.local.json",
            "evolake_toy_campaign_nightly.ops_nightly.local.json",
            "evolake_toy_campaign_local.latest.json",
        ],
    )
    if candidate and _evolake_payload_ok(_load_json(candidate)):
        return True

    for source_dir in source_dirs:
        local_dir = source_dir / "evolake_toy_campaign_local"
        if not local_dir.exists() or not local_dir.is_dir():
            continue
        local_jsons = sorted(local_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for json_path in local_jsons[:20]:
            if _evolake_payload_ok(_load_json(json_path)):
                return True
    return False


def _materialize_evolake_local_dir(source_dirs: list[Path], archive_day_dir: Path) -> str | None:
    for source_dir in source_dirs:
        local_dir = source_dir / "evolake_toy_campaign_local"
        if not local_dir.exists() or not local_dir.is_dir():
            continue
        dst_dir = archive_day_dir / "evolake_toy_campaign_local"
        dst_dir.mkdir(parents=True, exist_ok=True)
        copied_any = False
        for json_path in local_dir.glob("*.json"):
            copied = _copy_if_exists(json_path, dst_dir / json_path.name)
            copied_any = copied_any or copied is not None
        if copied_any:
            return str(dst_dir)
    return None


def _write_tracker_markdown(tracker: dict[str, Any], tracker_md: Path) -> None:
    lines = [
        "# Passive 4-Day Tracker",
        "",
        "| day | atp_ok | evolake_ok | ok |",
        "|---|---:|---:|---:|",
    ]
    days = tracker.get("days", [])
    for row in days:
        day = row.get("day", "")
        atp_ok = "true" if row.get("atp_ok") else "false"
        evolake_ok = "true" if row.get("evolake_ok") else "false"
        ok = "true" if row.get("ok") else "false"
        lines.append(f"| {day} | {atp_ok} | {evolake_ok} | {ok} |")
    tracker_md.parent.mkdir(parents=True, exist_ok=True)
    tracker_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_passive_archive(
    artifact_dir: Path,
    archive_root: Path,
    tracker_json: Path,
    tracker_md: Path,
    day: str,
    require_green: bool,
    allow_day_fail: bool,
    source_dirs_override: list[Path] | None = None,
) -> tuple[int, dict[str, Any]]:
    archive_day_dir = archive_root / day
    archive_day_dir.mkdir(parents=True, exist_ok=True)
    source_dirs = _build_source_dirs(artifact_dir, archive_day_dir, source_dirs_override)

    copied_artifacts: dict[str, str] = {}
    for key, names in _copy_plan().items():
        copied = _copy_first_existing(source_dirs, names, archive_day_dir)
        if copied:
            copied_artifacts[key] = copied
    evolake_local_dir = _materialize_evolake_local_dir(source_dirs, archive_day_dir)
    if evolake_local_dir:
        copied_artifacts["evolake_local_dir"] = evolake_local_dir

    atp_ok = _read_atp_ok(source_dirs)
    evolake_ok = _read_evolake_ok(source_dirs)
    ok = atp_ok and evolake_ok

    tracker = _load_json(tracker_json)
    if not isinstance(tracker, dict):
        tracker = {}
    rows = tracker.get("days")
    if not isinstance(rows, list):
        rows = []

    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("day"), str):
            by_day[row["day"]] = row
    by_day[day] = {
        "day": day,
        "atp_ok": atp_ok,
        "evolake_ok": evolake_ok,
        "ok": ok,
        "updated_at": time.time(),
    }
    tracker_days = [by_day[key] for key in sorted(by_day)]
    tracker_payload = {
        "schema": "breadboard.passive_4day_tracker.v1",
        "updated_at": time.time(),
        "days": tracker_days,
    }
    _ensure_parent(tracker_json)
    tracker_json.write_text(json.dumps(tracker_payload, indent=2) + "\n", encoding="utf-8")
    _write_tracker_markdown(tracker_payload, tracker_md)

    summary = {
        "schema": "breadboard.passive_window_day_summary.v1",
        "generated_at": time.time(),
        "day": day,
        "atp_ok": atp_ok,
        "evolake_ok": evolake_ok,
        "ok": ok,
        "archive_dir": str(archive_day_dir),
        "copied_artifacts": copied_artifacts,
    }
    summary_out = artifact_dir / "cert_02_03_passive_window_day_summary.local.json"
    _ensure_parent(summary_out)
    summary_out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    rc = 0
    if require_green and not ok:
        rc = 0 if allow_day_fail else 2
    return rc, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--archive-root", default="artifacts/nightly_archive")
    parser.add_argument("--tracker-json", default="artifacts/nightly_archive/passive_4day_tracker.json")
    parser.add_argument("--tracker-md", default="artifacts/nightly_archive/passive_4day_tracker.md")
    parser.add_argument("--day", default=None)
    parser.add_argument("--require-green", action="store_true")
    parser.add_argument("--allow-day-fail", action="store_true")
    parser.add_argument("--source-dir", action="append", default=[])
    args = parser.parse_args()

    day = args.day or datetime.now().strftime("%Y-%m-%d")
    rc, summary = run_passive_archive(
        artifact_dir=Path(args.artifact_dir).resolve(),
        archive_root=Path(args.archive_root).resolve(),
        tracker_json=Path(args.tracker_json).resolve(),
        tracker_md=Path(args.tracker_md).resolve(),
        day=day,
        require_green=args.require_green,
        allow_day_fail=args.allow_day_fail,
        source_dirs_override=[Path(path).resolve() for path in args.source_dir],
    )
    print(json.dumps(summary, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
