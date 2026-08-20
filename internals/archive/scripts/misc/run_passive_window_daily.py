#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from atp_evolake_passive_window import run_passive_archive
from atp_seven_day_stability_report import build_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--archive-root", default="artifacts/nightly_archive")
    parser.add_argument("--tracker-json", default="artifacts/nightly_archive/passive_4day_tracker.json")
    parser.add_argument("--tracker-md", default="artifacts/nightly_archive/passive_4day_tracker.md")
    parser.add_argument("--threshold-policy", default="config/atp_threshold_policy.json")
    parser.add_argument("--out", default="artifacts/nightly_archive/passive_4day_latest_status.json")
    parser.add_argument("--day", default=None)
    parser.add_argument("--require-stable-window", action="store_true")
    parser.add_argument("--allow-day-fail", action="store_true")
    parser.add_argument("--source-dir", action="append", default=[])
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir).resolve()
    archive_root = Path(args.archive_root).resolve()
    tracker_json = Path(args.tracker_json).resolve()
    tracker_md = Path(args.tracker_md).resolve()
    day = args.day or datetime.now().strftime("%Y-%m-%d")

    archive_rc, summary = run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day=day,
        require_green=not args.allow_day_fail,
        allow_day_fail=args.allow_day_fail,
        source_dirs_override=[Path(path).resolve() for path in args.source_dir],
    )

    seven_day_out = artifact_dir / "atp_seven_day_stability_report.latest.json"
    seven_day = build_report(
        tracker_json=tracker_json,
        threshold_policy=Path(args.threshold_policy).resolve(),
        out_path=seven_day_out,
        window_days_override=None,
    )

    status_payload = {
        "schema": "breadboard.passive_window_daily_status.v1",
        "generated_at": time.time(),
        "day": day,
        "archive_rc": archive_rc,
        "atp_ok": bool(summary.get("atp_ok")),
        "evolake_ok": bool(summary.get("evolake_ok")),
        "seven_day": {
            "available": True,
            "classification": seven_day.get("classification"),
            "green_days": seven_day.get("green_days"),
            "ok": bool(seven_day.get("ok")),
            "window_days": seven_day.get("window_days"),
        },
        "copied_artifacts": summary.get("copied_artifacts", {}),
    }

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(status_payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status_payload, indent=2))

    if args.require_stable_window:
        if status_payload["seven_day"]["ok"] and archive_rc == 0:
            return 0
        return 2 if archive_rc in (0, 2) else archive_rc
    return archive_rc


if __name__ == "__main__":
    raise SystemExit(main())
