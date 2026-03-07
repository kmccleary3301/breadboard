#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple


def _load(path: Path) -> Dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def _metric_pair(baseline: Dict, candidate: Dict, key: str) -> Tuple[float, float]:
    return float(baseline[key]), float(candidate[key])


def _config_value(payload: Dict, key: str):
    if key == "workers_count":
        workers = payload.get("workers")
        if isinstance(workers, list):
            return len(workers)
        return None
    return payload.get(key)


def run_drift_check(
    *,
    baseline: Dict,
    candidate: Dict,
    max_regression_pct: float,
    allow_config_mismatch: bool = False,
) -> Dict:
    metric_keys = [
        "repl_ms_avg_across_workers",
        "unpickle_s_avg_across_workers",
        "request_wall_s_avg_across_workers",
        "request_wall_s_p95_across_workers",
    ]
    comparisons = {}
    failures = []
    for key in metric_keys:
        old, new = _metric_pair(baseline, candidate, key)
        delta_pct = _pct_change(new, old)
        regressed = delta_pct > float(max_regression_pct)
        comparisons[key] = {
            "baseline": old,
            "candidate": new,
            "delta_pct": delta_pct,
            "regressed": regressed,
        }
        if regressed:
            failures.append(f"{key} regressed by {delta_pct:.2f}%")

    config_keys = [
        "concurrency",
        "iters_per_worker",
        "burn_in_iters_per_worker",
        "min_recommended_iters",
        "meets_recommended_sample_depth",
        "file_io_b64_active_all_workers",
        "workers_count",
    ]
    config_comparisons = {}
    for key in config_keys:
        base_value = _config_value(baseline, key)
        cand_value = _config_value(candidate, key)
        matches = base_value == cand_value
        config_comparisons[key] = {
            "baseline": base_value,
            "candidate": cand_value,
            "matches": matches,
        }
        if not matches and not allow_config_mismatch:
            failures.append(f"config mismatch: {key} baseline={base_value!r} candidate={cand_value!r}")

    for label, payload in (("baseline", baseline), ("candidate", candidate)):
        if payload.get("meets_recommended_sample_depth") is not True:
            failures.append(f"{label} does not meet recommended sample depth")

    return {
        "comparisons": comparisons,
        "config_comparisons": config_comparisons,
        "ok": len(failures) == 0,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check ATP benchmark drift against baseline thresholds.")
    parser.add_argument("--baseline", required=True, help="Baseline benchmark JSON path.")
    parser.add_argument("--candidate", required=True, help="Candidate benchmark JSON path.")
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=10.0,
        help="Maximum allowed regression percentage (higher is worse) for latency metrics.",
    )
    parser.add_argument(
        "--out",
        default="artifacts/atp_benchmark_drift_report.json",
        help="Output report path.",
    )
    parser.add_argument(
        "--allow-config-mismatch",
        action="store_true",
        help="Allow drift comparison even when run-shape metadata differs.",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline).resolve()
    candidate_path = Path(args.candidate).resolve()
    baseline = _load(baseline_path)
    candidate = _load(candidate_path)

    drift = run_drift_check(
        baseline=baseline,
        candidate=candidate,
        max_regression_pct=float(args.max_regression_pct),
        allow_config_mismatch=bool(args.allow_config_mismatch),
    )

    report = {
        "baseline": str(baseline_path),
        "candidate": str(candidate_path),
        "max_regression_pct": float(args.max_regression_pct),
        "allow_config_mismatch": bool(args.allow_config_mismatch),
        "config_comparisons": drift["config_comparisons"],
        "comparisons": drift["comparisons"],
        "ok": drift["ok"],
        "failures": drift["failures"],
    }
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"atp_drift_report: {out_path}")

    if not drift["ok"]:
        for failure in drift["failures"]:
            print(f"drift_fail: {failure}")
        return 1
    print("drift_ok: all metrics within threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
