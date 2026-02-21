#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping


EXPECTED_SCHEMA = "longrun_phase2_live_pilot_v1"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def validate_payload(
    path: Path,
    *,
    allow_budget_exceeded: bool = False,
    require_nonzero_usage: bool = True,
) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("live pilot payload must be a JSON object")
    if str(payload.get("schema_version")) != EXPECTED_SCHEMA:
        raise ValueError("unexpected schema_version in live pilot payload")

    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("payload must include at least one pair row")

    totals = payload.get("totals")
    caps = payload.get("caps")
    if not isinstance(totals, Mapping) or not isinstance(caps, Mapping):
        raise ValueError("payload must include totals and caps sections")

    total_tokens = max(0, _safe_int(totals.get("total_tokens"), 0))
    total_estimated_cost = max(0.0, _safe_float(totals.get("total_estimated_cost_usd"), 0.0))
    max_tokens = max(0, _safe_int(caps.get("max_total_tokens"), 0))
    max_cost = max(0.0, _safe_float(caps.get("max_total_estimated_cost_usd"), 0.0))
    budget_exceeded = bool(totals.get("budget_exceeded"))

    if max_tokens > 0 and total_tokens > max_tokens and not allow_budget_exceeded:
        raise ValueError("total_tokens exceeded cap")
    if max_cost > 0 and total_estimated_cost > max_cost and not allow_budget_exceeded:
        raise ValueError("total_estimated_cost_usd exceeded cap")
    if budget_exceeded and not allow_budget_exceeded:
        raise ValueError("budget_exceeded=true (pass --allow-budget-exceeded to permit)")
    if require_nonzero_usage and not bool(payload.get("dry_run", False)) and total_tokens <= 0:
        raise ValueError("live payload has zero token usage; run is not a valid live sample")

    for row in pairs:
        if not isinstance(row, Mapping):
            raise ValueError("pair rows must be objects")
        if not isinstance(row.get("task_id"), str) or not row.get("task_id"):
            raise ValueError("pair row missing task_id")
        baseline = row.get("baseline")
        longrun = row.get("longrun")
        if not isinstance(baseline, Mapping) or not isinstance(longrun, Mapping):
            if allow_budget_exceeded and (isinstance(baseline, Mapping) or isinstance(longrun, Mapping)):
                # In explicit cap-stop mode, the final row may be partially executed.
                continue
            raise ValueError("pair row must include baseline and longrun objects")
        for arm_name, arm in (("baseline", baseline), ("longrun", longrun)):
            usage = arm.get("usage")
            if not isinstance(usage, Mapping):
                raise ValueError(f"{arm_name}.usage must be an object")
            if _safe_int(usage.get("total_tokens"), -1) < 0:
                raise ValueError(f"{arm_name}.usage.total_tokens must be non-negative")
            if _safe_float(arm.get("estimated_cost_usd"), -1.0) < 0:
                raise ValueError(f"{arm_name}.estimated_cost_usd must be non-negative")
            telemetry = arm.get("telemetry")
            if not isinstance(telemetry, Mapping):
                raise ValueError(f"{arm_name}.telemetry must be an object")
            if not isinstance(telemetry.get("arm_status"), str) or not str(telemetry.get("arm_status")).strip():
                raise ValueError(f"{arm_name}.telemetry.arm_status must be a non-empty string")
            if not isinstance(telemetry.get("failure_class"), str):
                raise ValueError(f"{arm_name}.telemetry.failure_class must be a string")
            if _safe_int(telemetry.get("guard_trigger_count"), -1) < 0:
                raise ValueError(f"{arm_name}.telemetry.guard_trigger_count must be non-negative")
            by_name = telemetry.get("guard_trigger_by_name")
            if by_name is not None and not isinstance(by_name, Mapping):
                raise ValueError(f"{arm_name}.telemetry.guard_trigger_by_name must be an object when provided")
            timeout_hit = telemetry.get("timeout_hit")
            if timeout_hit is not None and not isinstance(timeout_hit, bool):
                raise ValueError(f"{arm_name}.telemetry.timeout_hit must be a boolean when provided")

    return {
        "ok": True,
        "schema_version": EXPECTED_SCHEMA,
        "pair_rows": len(pairs),
        "total_tokens": total_tokens,
        "total_estimated_cost_usd": total_estimated_cost,
        "budget_exceeded": budget_exceeded,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate longrun phase2 live pilot artifacts.")
    parser.add_argument("--json", required=True, help="Path to live pilot JSON artifact.")
    parser.add_argument(
        "--allow-budget-exceeded",
        action="store_true",
        help="Allow payloads where totals.budget_exceeded=true.",
    )
    parser.add_argument(
        "--allow-zero-usage",
        action="store_true",
        help="Allow live payloads with zero aggregate token usage.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_payload(
        Path(args.json),
        allow_budget_exceeded=bool(args.allow_budget_exceeded),
        require_nonzero_usage=(not bool(args.allow_zero_usage)),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
