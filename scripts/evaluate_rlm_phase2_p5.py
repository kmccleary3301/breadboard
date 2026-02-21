#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


SCHEMA_VERSION = "rlm_phase2_p5_evaluation_v1"


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    idx = max(0.0, min(float(len(ordered) - 1), q * float(len(ordered) - 1)))
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(ordered[lo])
    frac = idx - float(lo)
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def _wilson_interval(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = float(k) / float(n)
    z2 = z * z
    denom = 1.0 + z2 / float(n)
    center = (p + z2 / (2.0 * float(n))) / denom
    spread = (z / denom) * math.sqrt((p * (1.0 - p) / float(n)) + (z2 / (4.0 * float(n * n))))
    return (max(0.0, center - spread), min(1.0, center + spread))


def evaluate(payload: Mapping[str, Any], *, allow_partial_arms: bool = False) -> Dict[str, Any]:
    runs = payload.get("runs")
    runs = runs if isinstance(runs, list) else []
    by_arm: Dict[str, List[Mapping[str, Any]]] = {}
    for row in runs:
        if isinstance(row, Mapping):
            by_arm.setdefault(str(row.get("arm") or ""), []).append(row)

    arm_reports: List[Dict[str, Any]] = []
    for arm, rows in sorted(by_arm.items(), key=lambda kv: kv[0]):
        n = len(rows)
        success_count = len([r for r in rows if bool(r.get("task_success"))])
        review_count = len([r for r in rows if bool(r.get("review_pass"))])
        artifact_ok_count = len([r for r in rows if bool(r.get("artifact_validation_ok"))])
        nondet_count = len([r for r in rows if bool(r.get("nondeterminism_detected"))])
        success_ci = _wilson_interval(success_count, n)
        review_ci = _wilson_interval(review_count, n)
        artifact_ci = _wilson_interval(artifact_ok_count, n)
        walls = [float(r.get("wallclock_seconds") or 0.0) for r in rows]
        subcalls = [float(r.get("subcall_count") or 0.0) for r in rows]
        blocked = [float(r.get("blocked_count") or 0.0) for r in rows]
        timeouts = [float(r.get("timeout_count") or 0.0) for r in rows]
        arm_reports.append(
            {
                "arm": arm,
                "runs": n,
                "success_rate": float(success_count / n) if n else 0.0,
                "success_rate_ci95": {"low": success_ci[0], "high": success_ci[1]},
                "review_pass_rate": float(review_count / n) if n else 0.0,
                "review_pass_ci95": {"low": review_ci[0], "high": review_ci[1]},
                "artifact_ok_rate": float(artifact_ok_count / n) if n else 0.0,
                "artifact_ok_ci95": {"low": artifact_ci[0], "high": artifact_ci[1]},
                "nondeterminism_rate": float(nondet_count / n) if n else 0.0,
                "median_wallclock_seconds": _median(walls),
                "p95_wallclock_seconds": _quantile(walls, 0.95),
                "median_subcall_count": _median(subcalls),
                "p95_subcall_count": _quantile(subcalls, 0.95),
                "median_blocked_count": _median(blocked),
                "median_timeout_count": _median(timeouts),
            }
        )

    checks: List[Dict[str, Any]] = []
    reports = {str(row.get("arm") or ""): row for row in arm_reports}
    required_arms = ("A1_RLM_SYNC", "A2_RLM_BATCH", "A3_HYBRID_LONGRUN")
    for required in required_arms:
        row = reports.get(required) or {}
        if allow_partial_arms and required not in reports:
            checks.append(
                {
                    "name": f"{required}_missing_but_allowed_partial_mode",
                    "passed": True,
                    "details": {"skipped": True},
                }
            )
            continue
        checks.append(
            {
                "name": f"{required}_artifact_ok_rate==1",
                "passed": float(row.get("artifact_ok_rate") or 0.0) >= 1.0,
                "details": {"artifact_ok_rate": float(row.get("artifact_ok_rate") or 0.0)},
            }
        )
        checks.append(
            {
                "name": f"{required}_nondeterminism_rate==0",
                "passed": float(row.get("nondeterminism_rate") or 0.0) <= 0.0,
                "details": {"nondeterminism_rate": float(row.get("nondeterminism_rate") or 0.0)},
            }
        )
    ok = all(bool(c.get("passed")) for c in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": payload.get("schema_version"),
        "ok": ok,
        "arm_reports": arm_reports,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RLM Phase-2 P5 tier results.")
    parser.add_argument("--results-json", required=True, help="Input results JSON from run_rlm_phase2_tier0.py")
    parser.add_argument("--report-json", required=False, help="Output report JSON path.")
    parser.add_argument("--allow-partial-arms", action="store_true", help="Do not fail checks for missing required arms.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.results_json).read_text(encoding="utf-8"))
    report = evaluate(payload, allow_partial_arms=bool(args.allow_partial_arms))
    if args.report_json:
        out = Path(args.report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if bool(report.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
