#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


def _safe_bool(value: Any) -> bool:
    return bool(value)


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def _as_mapping(payload: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _required_summary_fields(summary: Mapping[str, Any]) -> bool:
    required = {"run_count", "success_rate", "median_episodes", "stop_reasons"}
    return required.issubset(set(summary.keys()))


def validate_contract(
    *,
    pilot_payload: Mapping[str, Any],
    contract_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    expected_schema = str(contract_payload.get("pilot_schema_version") or "")
    schema_ok = str(pilot_payload.get("schema_version") or "") == expected_schema
    checks.append(
        _check(
            "pilot_schema_version_match",
            schema_ok,
            {
                "expected": expected_schema,
                "actual": str(pilot_payload.get("schema_version") or ""),
            },
        )
    )

    scenario_rows = pilot_payload.get("scenarios")
    scenario_rows = scenario_rows if isinstance(scenario_rows, list) else []
    required_scenarios = contract_payload.get("required_scenarios")
    required_scenarios = required_scenarios if isinstance(required_scenarios, list) else []
    required_ids = [
        str(row.get("id") or "")
        for row in required_scenarios
        if isinstance(row, Mapping) and str(row.get("id") or "")
    ]
    checks.append(
        _check(
            "scenario_count_matches_contract",
            len(scenario_rows) >= len(required_ids),
            {"observed": len(scenario_rows), "required_min": len(required_ids)},
        )
    )

    by_id: Dict[str, Mapping[str, Any]] = {}
    for row in scenario_rows:
        if isinstance(row, Mapping) and isinstance(row.get("scenario_id"), str):
            by_id[str(row["scenario_id"])] = row

    missing = [sid for sid in required_ids if sid not in by_id]
    checks.append(_check("required_scenarios_present", len(missing) == 0, {"missing": missing}))

    summary_missing: List[str] = []
    for sid in required_ids:
        row = by_id.get(sid) or {}
        for arm in ("baseline", "longrun"):
            arm_payload = row.get(arm)
            summary = (arm_payload or {}).get("summary") if isinstance(arm_payload, Mapping) else None
            if not isinstance(summary, Mapping) or not _required_summary_fields(summary):
                summary_missing.append(f"{sid}:{arm}")
    checks.append(_check("summary_fields_present", len(summary_missing) == 0, {"missing": summary_missing}))

    allowed_stop_classes = {
        str(value) for value in (contract_payload.get("allowed_stop_classes") or []) if str(value)
    }
    unknown_stop_classes: List[Dict[str, Any]] = []
    for sid in required_ids:
        row = by_id.get(sid) or {}
        for arm in ("baseline", "longrun"):
            arm_payload = row.get(arm)
            summary = (arm_payload or {}).get("summary") if isinstance(arm_payload, Mapping) else {}
            stop_reasons = summary.get("stop_reasons") if isinstance(summary, Mapping) else {}
            keys = stop_reasons.keys() if isinstance(stop_reasons, Mapping) else []
            for stop_reason in keys:
                stop_text = str(stop_reason)
                if stop_text not in allowed_stop_classes:
                    unknown_stop_classes.append(
                        {"scenario_id": sid, "arm": arm, "stop_reason": stop_text}
                    )
    checks.append(
        _check("stop_reasons_classified", len(unknown_stop_classes) == 0, {"unknown": unknown_stop_classes})
    )

    expected_stop_failures: List[Dict[str, Any]] = []
    for requirement in required_scenarios:
        if not isinstance(requirement, Mapping):
            continue
        sid = str(requirement.get("id") or "")
        if not sid or sid not in by_id:
            continue
        expected_by_arm = requirement.get("expected_stop_classes")
        expected_by_arm = expected_by_arm if isinstance(expected_by_arm, Mapping) else {}
        row = by_id[sid]
        for arm in ("baseline", "longrun"):
            expected = {
                str(value)
                for value in (expected_by_arm.get(arm) or [])
                if str(value)
            }
            if not expected:
                continue
            summary = ((row.get(arm) or {}).get("summary") or {}) if isinstance(row.get(arm), Mapping) else {}
            stop_reasons = summary.get("stop_reasons") if isinstance(summary, Mapping) else {}
            observed = {str(k) for k in (stop_reasons.keys() if isinstance(stop_reasons, Mapping) else [])}
            if expected.isdisjoint(observed):
                expected_stop_failures.append(
                    {
                        "scenario_id": sid,
                        "arm": arm,
                        "expected_any_of": sorted(expected),
                        "observed": sorted(observed),
                    }
                )
    checks.append(
        _check("expected_stop_classes_match", len(expected_stop_failures) == 0, {"mismatches": expected_stop_failures})
    )

    required_assertions = {
        str(value) for value in (contract_payload.get("required_assertions") or []) if str(value)
    }
    produced_names = {str(check.get("name") or "") for check in checks}
    missing_assertions = sorted(required_assertions - produced_names)
    checks.append(
        _check(
            "required_assertions_present",
            len(missing_assertions) == 0,
            {"missing": missing_assertions},
        )
    )

    report_ok = all(_safe_bool(check.get("passed")) for check in checks)
    return {
        "schema_version": "longrun_phase1_scenario_contract_report_v1",
        "ok": report_ok,
        "checks": checks,
        "required_scenarios": required_ids,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate longrun phase1 pilot output against scenario contract.")
    parser.add_argument("--pilot-json", required=True, help="Path to longrun phase1 pilot artifact JSON.")
    parser.add_argument("--contract-json", required=True, help="Path to scenario contract JSON.")
    parser.add_argument("--report-json", help="Optional output path for report JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pilot_payload = _as_mapping(json.loads(Path(args.pilot_json).read_text(encoding="utf-8")), label="pilot payload")
    contract_payload = _as_mapping(
        json.loads(Path(args.contract_json).read_text(encoding="utf-8")),
        label="contract payload",
    )
    report = validate_contract(pilot_payload=pilot_payload, contract_payload=contract_payload)
    if args.report_json:
        out_path = Path(args.report_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
