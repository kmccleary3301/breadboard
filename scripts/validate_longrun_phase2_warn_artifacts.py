#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("pilot payload must be a JSON object")
    if payload.get("schema_version") != "longrun_phase1_pilot_v1":
        raise ValueError("unexpected schema_version in pilot payload")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 6:
        raise ValueError("pilot payload must include at least 6 scenarios")
    runs_per_scenario = int(payload.get("runs_per_scenario") or 0)
    if runs_per_scenario < 5:
        raise ValueError("runs_per_scenario must be at least 5")
    for row in scenarios:
        if not isinstance(row, dict):
            raise ValueError("each scenario row must be an object")
        if not isinstance(row.get("scenario_id"), str) or not row["scenario_id"]:
            raise ValueError("scenario_id is required")
        baseline = row.get("baseline")
        longrun = row.get("longrun")
        if not isinstance(baseline, dict) or not isinstance(longrun, dict):
            raise ValueError("baseline/longrun sections are required")
    return {
        "ok": True,
        "path": str(path),
        "scenario_count": len(scenarios),
        "runs_per_scenario": runs_per_scenario,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate longrun phase2 warn-lane pilot artifacts.")
    parser.add_argument("--json", required=True, help="Path to longrun pilot JSON artifact.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_payload(Path(args.json))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
