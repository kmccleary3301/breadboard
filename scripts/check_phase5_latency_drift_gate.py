#!/usr/bin/env python3
"""
Check Phase5 latency drift gate from a latency trend report.

This script consumes the output of `build_phase5_latency_trend_report.py` and
enforces headroom + drift budgets.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


@dataclass
class GateResult:
    ok: bool
    report: dict[str, Any]


def check_latency_drift_gate(
    *,
    trend_payload: dict[str, Any],
    max_threshold_ratio: float,
    max_mean_delta_first_frame: float,
    max_mean_delta_replay_first_frame: float,
    max_mean_delta_duration: float,
    max_mean_delta_stall: float,
    max_mean_delta_max_gap: float,
    allow_bootstrap_no_baseline: bool,
) -> GateResult:
    summary = trend_payload.get("summary", {})
    thresholds = trend_payload.get("thresholds", {})
    delta = trend_payload.get("delta_summary", {})
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(thresholds, dict):
        thresholds = {}
    if not isinstance(delta, dict):
        delta = {}

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, expected: Any, observed: Any) -> None:
        checks.append(
            {
                "name": name,
                "pass": bool(passed),
                "expected": expected,
                "observed": observed,
            }
        )

    trend_overall_ok = bool(trend_payload.get("overall_ok"))
    missing_count = int(summary.get("missing_count") or 0)
    baseline_present = bool(trend_payload.get("baseline_present"))
    add("trend_overall_ok", trend_overall_ok, True, trend_overall_ok)
    add("missing_count", missing_count == 0, 0, missing_count)

    ratio_metrics = [
        ("first_frame_seconds", "max_first_frame_seconds"),
        ("replay_first_frame_seconds", "max_replay_first_frame_seconds"),
        ("duration_seconds", "max_duration_seconds"),
        ("max_stall_seconds", "max_stall_seconds"),
        ("max_frame_gap_seconds", "max_frame_gap_seconds"),
        ("max_non_wait_frame_gap_seconds", "max_non_wait_frame_gap_seconds"),
    ]
    ratio_by_metric: dict[str, float] = {}
    for metric, threshold_key in ratio_metrics:
        row = summary.get(metric, {})
        observed = _safe_float((row if isinstance(row, dict) else {}).get("max"), 0.0)
        threshold = _safe_float(thresholds.get(threshold_key), 0.0)
        ratio = observed / threshold if threshold > 0 else 0.0
        ratio_by_metric[metric] = ratio
    worst_metric = max(ratio_by_metric.items(), key=lambda kv: kv[1]) if ratio_by_metric else ("", 0.0)
    add("max_threshold_ratio", worst_metric[1] <= max_threshold_ratio, f"<={max_threshold_ratio}", round(worst_metric[1], 6))
    add("baseline_present_or_bootstrap_allowed", baseline_present or allow_bootstrap_no_baseline, True, baseline_present)

    drift_checks_pass = True
    drift_observed: dict[str, float] = {}
    if baseline_present:
        def _delta_mean(name: str) -> float:
            row = delta.get(name, {})
            if not isinstance(row, dict):
                return 0.0
            return _safe_float(row.get("mean_delta"), 0.0)

        drift_budgets = {
            "first_frame_seconds": max_mean_delta_first_frame,
            "replay_first_frame_seconds": max_mean_delta_replay_first_frame,
            "duration_seconds": max_mean_delta_duration,
            "max_stall_seconds": max_mean_delta_stall,
            "max_frame_gap_seconds": max_mean_delta_max_gap,
        }
        for metric, budget in drift_budgets.items():
            observed = _delta_mean(metric)
            drift_observed[metric] = observed
            passed = observed <= budget
            drift_checks_pass = drift_checks_pass and passed
            add(f"mean_delta:{metric}", passed, f"<={budget}", round(observed, 6))
    else:
        add("delta_checks_skipped_bootstrap", True, "no baseline required", "skipped")

    overall_ok = all(bool(row["pass"]) for row in checks) and drift_checks_pass
    near = trend_payload.get("near_threshold", {})
    near_top = near.get("top", []) if isinstance(near, dict) else []
    worst_metric_context: dict[str, Any] = {}
    if isinstance(near_top, list):
        for row in near_top:
            if not isinstance(row, dict):
                continue
            if str(row.get("metric") or "") != str(worst_metric[0]):
                continue
            worst_metric_context = {
                "scenario": str(row.get("scenario") or ""),
                "ratio": _safe_float(row.get("ratio"), 0.0),
                "wait_overlap": bool(row.get("wait_overlap")),
                "overlap_actions": row.get("overlap_actions") if isinstance(row.get("overlap_actions"), list) else [],
            }
            break

    report = {
        "schema_version": "phase5_latency_drift_gate_v1",
        "overall_ok": overall_ok,
        "config": {
            "max_threshold_ratio": max_threshold_ratio,
            "max_mean_delta_first_frame": max_mean_delta_first_frame,
            "max_mean_delta_replay_first_frame": max_mean_delta_replay_first_frame,
            "max_mean_delta_duration": max_mean_delta_duration,
            "max_mean_delta_stall": max_mean_delta_stall,
            "max_mean_delta_max_gap": max_mean_delta_max_gap,
            "allow_bootstrap_no_baseline": allow_bootstrap_no_baseline,
        },
        "trend_overall_ok": trend_overall_ok,
        "baseline_present": baseline_present,
        "allow_bootstrap_no_baseline": allow_bootstrap_no_baseline,
        "worst_threshold_ratio_metric": worst_metric[0],
        "worst_threshold_ratio": worst_metric[1],
        "worst_metric_context": worst_metric_context,
        "ratio_by_metric": ratio_by_metric,
        "drift_mean_delta_observed": drift_observed,
        "checks": checks,
    }
    return GateResult(ok=overall_ok, report=report)


def _render_markdown(report: dict[str, Any]) -> str:
    config = report.get("config", {})
    max_ratio = _safe_float((config if isinstance(config, dict) else {}).get("max_threshold_ratio"), 0.0)
    lines = [
        "# Phase5 Latency Drift Gate",
        "",
        f"- overall_ok: `{report.get('overall_ok')}`",
        f"- baseline_present: `{report.get('baseline_present')}`",
        f"- max_threshold_ratio: `{max_ratio:.3f}`",
        f"- worst_threshold_ratio: `{float(report.get('worst_threshold_ratio') or 0.0):.3f}` ({report.get('worst_threshold_ratio_metric')})",
        "",
        "| Check | Pass | Expected | Observed |",
        "|---|---:|---|---|",
    ]
    for row in report.get("checks", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {row.get('name')} | {'PASS' if row.get('pass') else 'FAIL'} | {row.get('expected')} | {row.get('observed')} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check Phase5 latency drift gate.")
    p.add_argument("--latency-trend-json", required=True)
    p.add_argument("--max-threshold-ratio", type=float, default=0.95)
    p.add_argument("--max-mean-delta-first-frame", type=float, default=0.20)
    p.add_argument("--max-mean-delta-replay-first-frame", type=float, default=0.20)
    p.add_argument("--max-mean-delta-duration", type=float, default=0.60)
    p.add_argument("--max-mean-delta-stall", type=float, default=0.20)
    p.add_argument("--max-mean-delta-max-gap", type=float, default=0.20)
    p.add_argument("--allow-bootstrap-no-baseline", action="store_true")
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    trend_payload = _load_json(Path(args.latency_trend_json).expanduser().resolve())
    result = check_latency_drift_gate(
        trend_payload=trend_payload,
        max_threshold_ratio=float(args.max_threshold_ratio),
        max_mean_delta_first_frame=float(args.max_mean_delta_first_frame),
        max_mean_delta_replay_first_frame=float(args.max_mean_delta_replay_first_frame),
        max_mean_delta_duration=float(args.max_mean_delta_duration),
        max_mean_delta_stall=float(args.max_mean_delta_stall),
        max_mean_delta_max_gap=float(args.max_mean_delta_max_gap),
        allow_bootstrap_no_baseline=bool(args.allow_bootstrap_no_baseline),
    )
    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(result.report), encoding="utf-8")
    if result.ok:
        print("phase5 latency drift gate: pass")
        print(f"json={out_json}")
        return 0
    print("phase5 latency drift gate: fail")
    print(f"json={out_json}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
