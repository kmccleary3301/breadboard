#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_window_days(policy: dict[str, Any], default: int = 7) -> int:
    if not policy:
        return default

    candidate_paths: list[list[str]] = [
        ["seven_day_window_days"],
        ["window_days"],
        ["seven_day_stability", "window_days"],
        ["atp_seven_day", "window_days"],
        ["atp", "seven_day_window_days"],
    ]
    for path in candidate_paths:
        node: Any = policy
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, int) and 1 <= node <= 365:
            return node
    return default


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def _entry_green(entry: dict[str, Any]) -> bool:
    return bool(entry.get("atp_ok")) and bool(entry.get("evolake_ok"))


def _contiguous_green_days(days: list[dict[str, Any]]) -> int:
    parsed: list[tuple[date, bool]] = []
    for row in days:
        day_value = row.get("day")
        if not isinstance(day_value, str):
            continue
        day_obj = _parse_day(day_value)
        if day_obj is None:
            continue
        parsed.append((day_obj, _entry_green(row)))
    if not parsed:
        return 0

    parsed.sort(key=lambda item: item[0])
    by_day: dict[date, bool] = {}
    for day_obj, is_green in parsed:
        by_day[day_obj] = is_green

    current_day = max(by_day)
    green_days = 0
    while True:
        if by_day.get(current_day) is not True:
            break
        green_days += 1
        current_day = current_day - timedelta(days=1)
    return green_days


def build_report(
    tracker_json: Path,
    threshold_policy: Path,
    out_path: Path,
    window_days_override: int | None,
) -> dict[str, Any]:
    tracker = _load_json(tracker_json)
    policy = _load_json(threshold_policy)

    days = tracker.get("days")
    if not isinstance(days, list):
        days = []

    window_days = window_days_override or _read_window_days(policy, default=7)
    green_days = _contiguous_green_days(days)
    ok = green_days >= window_days
    classification = "stable_window" if ok else "failed_window"

    latest_day = None
    for row in reversed(days):
        day_value = row.get("day")
        if isinstance(day_value, str):
            latest_day = day_value
            break

    report = {
        "schema": "breadboard.atp_seven_day_stability_report.v1",
        "generated_at": time.time(),
        "window_days": window_days,
        "green_days": green_days,
        "ok": ok,
        "classification": classification,
        "latest_day": latest_day,
        "tracker_json": str(tracker_json),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--threshold-policy", default="config/atp_threshold_policy.json")
    parser.add_argument("--tracker-json", default=None)
    parser.add_argument("--window-days", type=int, default=None)
    parser.add_argument(
        "--out",
        default=None,
        help="Defaults to <artifact-dir>/atp_seven_day_stability_report.latest.json",
    )
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir).resolve()
    tracker_json = Path(args.tracker_json).resolve() if args.tracker_json else artifact_dir / "nightly_archive" / "passive_4day_tracker.json"
    out_path = Path(args.out).resolve() if args.out else artifact_dir / "atp_seven_day_stability_report.latest.json"
    threshold_policy = Path(args.threshold_policy).resolve()

    report = build_report(
        tracker_json=tracker_json,
        threshold_policy=threshold_policy,
        out_path=out_path,
        window_days_override=args.window_days,
    )

    print(json.dumps(report, indent=2))
    print(f"atp_seven_day_classification: {report['classification']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
