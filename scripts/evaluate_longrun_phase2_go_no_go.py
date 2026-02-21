#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping


REQUIRED_BOUNDEDNESS_SCENARIOS = {
    "persistent_no_progress",
    "retry_then_rollback",
    "signature_repeat_stop",
}
STABLE_SUCCESS_SCENARIOS = {
    "single_success",
    "flaky_then_success",
}


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def evaluate(pilot_payload: Mapping[str, Any], parity_payload: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    schema_ok = str(pilot_payload.get("schema_version")) == "longrun_phase1_pilot_v1"
    checks.append(_check("schema_version", schema_ok, {"schema_version": pilot_payload.get("schema_version")}))

    scenarios = pilot_payload.get("scenarios")
    scenarios = scenarios if isinstance(scenarios, list) else []
    checks.append(_check("scenario_count>=6", len(scenarios) >= 6, {"scenario_count": len(scenarios)}))

    runs_per_scenario = int(pilot_payload.get("runs_per_scenario") or 0)
    checks.append(_check("runs_per_scenario>=5", runs_per_scenario >= 5, {"runs_per_scenario": runs_per_scenario}))

    by_id = {}
    for row in scenarios:
        if isinstance(row, Mapping) and isinstance(row.get("scenario_id"), str):
            by_id[row["scenario_id"]] = row

    no_success_regression = True
    regression_rows: List[Dict[str, Any]] = []
    for sid, row in by_id.items():
        baseline = (row.get("baseline") or {}) if isinstance(row, Mapping) else {}
        longrun = (row.get("longrun") or {}) if isinstance(row, Mapping) else {}
        bsum = (baseline.get("summary") or {}) if isinstance(baseline, Mapping) else {}
        lsum = (longrun.get("summary") or {}) if isinstance(longrun, Mapping) else {}
        bs = float(bsum.get("success_rate") or 0.0)
        ls = float(lsum.get("success_rate") or 0.0)
        if ls + 1e-9 < bs:
            no_success_regression = False
            regression_rows.append({"scenario_id": sid, "baseline_success": bs, "longrun_success": ls})
    checks.append(_check("no_success_regression", no_success_regression, {"regressions": regression_rows}))

    boundedness_ok = True
    boundedness_rows: List[Dict[str, Any]] = []
    for sid in sorted(REQUIRED_BOUNDEDNESS_SCENARIOS):
        row = by_id.get(sid)
        if not isinstance(row, Mapping):
            boundedness_ok = False
            boundedness_rows.append({"scenario_id": sid, "error": "missing"})
            continue
        bsum = ((row.get("baseline") or {}).get("summary") or {}) if isinstance(row.get("baseline"), Mapping) else {}
        lsum = ((row.get("longrun") or {}).get("summary") or {}) if isinstance(row.get("longrun"), Mapping) else {}
        be = float(bsum.get("median_episodes") or 0.0)
        le = float(lsum.get("median_episodes") or 0.0)
        improved = le < be
        if not improved:
            boundedness_ok = False
        boundedness_rows.append({"scenario_id": sid, "baseline_median_episodes": be, "longrun_median_episodes": le, "improved": improved})
    checks.append(_check("boundedness_improvement_required_set", boundedness_ok, {"rows": boundedness_rows}))

    stable_success_ok = True
    stable_success_rows: List[Dict[str, Any]] = []
    for sid in sorted(STABLE_SUCCESS_SCENARIOS):
        row = by_id.get(sid)
        if not isinstance(row, Mapping):
            stable_success_ok = False
            stable_success_rows.append({"scenario_id": sid, "error": "missing"})
            continue
        bsum = ((row.get("baseline") or {}).get("summary") or {}) if isinstance(row.get("baseline"), Mapping) else {}
        lsum = ((row.get("longrun") or {}).get("summary") or {}) if isinstance(row.get("longrun"), Mapping) else {}
        bs = float(bsum.get("success_rate") or 0.0)
        ls = float(lsum.get("success_rate") or 0.0)
        be = float(bsum.get("median_episodes") or 0.0)
        le = float(lsum.get("median_episodes") or 0.0)
        stable = ls >= bs and (le - be) <= 1.0
        if not stable:
            stable_success_ok = False
        stable_success_rows.append(
            {
                "scenario_id": sid,
                "baseline_success": bs,
                "longrun_success": ls,
                "baseline_median_episodes": be,
                "longrun_median_episodes": le,
                "stable": stable,
            }
        )
    checks.append(_check("stable_success_path", stable_success_ok, {"rows": stable_success_rows}))

    parity_ok = True
    parity_details: Dict[str, Any] = {}
    if isinstance(parity_payload, Mapping):
        parity_ok = bool(parity_payload.get("ok", False))
        parity_details = {
            "checked": int(parity_payload.get("checked") or 0),
            "failure_count": len(parity_payload.get("failures") or []),
        }
    checks.append(_check("parity_audit_ok", parity_ok, parity_details))

    overall_ok = all(bool(c.get("passed")) for c in checks)
    return {
        "schema_version": "longrun_phase2_go_no_go_v1",
        "ok": overall_ok,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate longrun phase2 go/no-go criteria.")
    parser.add_argument("--pilot-json", required=True, help="Path to pilot JSON artifact.")
    parser.add_argument("--parity-json", required=False, help="Optional path to parity audit JSON artifact.")
    parser.add_argument("--report-json", required=False, help="Optional output path for report JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pilot_payload = json.loads(Path(args.pilot_json).read_text(encoding="utf-8"))
    parity_payload = None
    if args.parity_json:
        parity_payload = json.loads(Path(args.parity_json).read_text(encoding="utf-8"))
    report = evaluate(pilot_payload, parity_payload)
    if args.report_json:
        out = Path(args.report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
