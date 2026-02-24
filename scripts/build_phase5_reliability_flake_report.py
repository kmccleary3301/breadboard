#!/usr/bin/env python3
"""
Build a Phase5 replay reliability flake report from historical roundtrip outputs.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _stamp_from_path(path: Path) -> str:
    match = re.search(r"phase5_roundtrip_reliability_(.+)\.json$", path.name)
    if match:
        return match.group(1)
    return path.stem


def _check_pass(record: dict[str, Any], check_name: str) -> bool | None:
    checks = record.get("checks", [])
    if not isinstance(checks, list):
        return None
    for row in checks:
        if not isinstance(row, dict):
            continue
        if str(row.get("name") or "") != check_name:
            continue
        return bool(row.get("pass"))
    return None


def _classify(points: list[dict[str, Any]]) -> dict[str, Any]:
    if not points:
        return {
            "latest_pass": True,
            "fail_count": 0,
            "transitions": 0,
            "max_consecutive_fail": 0,
            "classification": "no-data",
        }
    statuses = [bool(p.get("pass")) for p in points]
    fail_count = sum(1 for s in statuses if not s)
    transitions = sum(1 for i in range(1, len(statuses)) if statuses[i] != statuses[i - 1])
    max_consecutive_fail = 0
    streak = 0
    for status in statuses:
        if status:
            streak = 0
        else:
            streak += 1
            max_consecutive_fail = max(max_consecutive_fail, streak)
    latest_pass = statuses[-1]
    sample_count = len(statuses)
    fail_rate = fail_count / max(1, sample_count)

    if fail_count == 0:
        cls = "stable-pass"
    elif latest_pass and fail_count == 1 and max_consecutive_fail == 1 and sample_count >= 6:
        cls = "transient-single-recovery"
    elif latest_pass and transitions > 0:
        cls = "intermittent-flake"
    elif not latest_pass and max_consecutive_fail >= 2:
        cls = "persistent-fail"
    else:
        cls = "active-regression"

    return {
        "latest_pass": latest_pass,
        "sample_count": sample_count,
        "fail_rate": round(fail_rate, 6),
        "fail_count": fail_count,
        "transitions": transitions,
        "max_consecutive_fail": max_consecutive_fail,
        "classification": cls,
    }


def build_report(
    *,
    files: list[Path],
    check_name: str,
    scenario_filter: str | None,
) -> dict[str, Any]:
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(files):
        report = _load_json(path)
        stamp = _stamp_from_path(path)
        records = report.get("records", [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            scenario = str(record.get("scenario") or "")
            if not scenario:
                continue
            if scenario_filter and scenario != scenario_filter:
                continue
            passed = _check_pass(record, check_name)
            if passed is None:
                continue
            metrics = record.get("metrics", {})
            metric_value = None
            if isinstance(metrics, dict) and check_name in metrics:
                raw = metrics.get(check_name)
                metric_value = float(raw) if isinstance(raw, (int, float)) else None
            by_scenario.setdefault(scenario, []).append(
                {
                    "stamp": stamp,
                    "path": str(path),
                    "pass": bool(passed),
                    "metric_value": metric_value,
                }
            )

    scenario_rows: list[dict[str, Any]] = []
    for scenario, points in sorted(by_scenario.items()):
        points_sorted = sorted(points, key=lambda p: p["stamp"])
        cls = _classify(points_sorted)
        scenario_rows.append(
            {
                "scenario": scenario,
                **cls,
                "points": points_sorted,
            }
        )

    overall_ok = all(row.get("classification") in {"stable-pass", "intermittent-flake"} for row in scenario_rows)
    overall_ok = all(
        row.get("classification") in {"stable-pass", "transient-single-recovery", "intermittent-flake"}
        for row in scenario_rows
    )
    return {
        "schema_version": "phase5_reliability_flake_report_v1",
        "check_name": check_name,
        "scenario_filter": scenario_filter,
        "file_count": len(files),
        "scenario_count": len(scenario_rows),
        "overall_ok": overall_ok,
        "scenarios": scenario_rows,
    }


def _render_markdown(report: dict[str, Any], glob_pattern: str) -> str:
    lines = [
        "# Phase5 Reliability Flake Report",
        "",
        f"- check_name: `{report.get('check_name')}`",
        f"- source_glob: `{glob_pattern}`",
        f"- file_count: `{int(report.get('file_count') or 0)}`",
        f"- scenario_count: `{int(report.get('scenario_count') or 0)}`",
        f"- overall_ok: `{bool(report.get('overall_ok'))}`",
        "",
        "| Scenario | Classification | Latest Pass | Fail Count | Transitions | Max Consecutive Fail |",
        "|---|---|---:|---:|---:|---:|",
    ]
    scenarios = report.get("scenarios", [])
    if isinstance(scenarios, list):
        for row in scenarios:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {row.get('scenario')} | {row.get('classification')} | "
                f"{1 if bool(row.get('latest_pass')) else 0} | "
                f"{int(row.get('fail_count') or 0)} | "
                f"{int(row.get('transitions') or 0)} | "
                f"{int(row.get('max_consecutive_fail') or 0)} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reliability-glob", required=True)
    p.add_argument("--check-name", default="isolated_low_edge_rows_total")
    p.add_argument("--scenario")
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    files = [Path(p).expanduser().resolve() for p in glob.glob(args.reliability_glob)]
    report = build_report(
        files=files,
        check_name=str(args.check_name),
        scenario_filter=str(args.scenario) if args.scenario else None,
    )
    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report, args.reliability_glob), encoding="utf-8")
    print(f"overall_ok={bool(report.get('overall_ok'))}")
    print(f"scenarios={int(report.get('scenario_count') or 0)}")
    print(f"json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
