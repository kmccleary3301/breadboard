#!/usr/bin/env python3
"""
Build a compact post-cert monitoring bundle from phase5 roundtrip outputs.
"""

from __future__ import annotations

import argparse
import json
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


def build_report(
    *,
    stamp: str,
    footer_gate: dict[str, Any],
    discrepancy_budget_gate: dict[str, Any],
    reliability_budget_gate: dict[str, Any],
    latency_drift_gate: dict[str, Any],
    wait_alignment_gate: dict[str, Any],
    discrepancy_ranking: dict[str, Any],
    latency_trend: dict[str, Any],
    sources: dict[str, str],
) -> dict[str, Any]:
    items = discrepancy_ranking.get("items", [])
    if not isinstance(items, list):
        items = []

    non_info_count = sum(
        1 for row in items if str((row or {}).get("severity") or "").strip().lower() != "info"
    )
    top_risk = max((_safe_float((row or {}).get("risk_score"), 0.0) for row in items), default=0.0)

    overall_ok = all(
        [
            bool(footer_gate.get("overall_ok")),
            bool(discrepancy_budget_gate.get("overall_ok")),
            bool(reliability_budget_gate.get("overall_ok")),
            bool(latency_drift_gate.get("overall_ok")),
            bool(wait_alignment_gate.get("overall_ok")),
            non_info_count == 0,
            round(top_risk, 6) == 0.0,
        ]
    )

    return {
        "schema_version": "phase5_postcert_monitoring_v1",
        "stamp": stamp,
        "overall_ok": overall_ok,
        "checks": {
            "footer_contrast_gate": {
                "overall_ok": bool(footer_gate.get("overall_ok")),
                "failure_count": int(
                    footer_gate.get("failure_count") or len(footer_gate.get("failures", []))
                ),
            },
            "discrepancy_budget_gate": {
                "overall_ok": bool(discrepancy_budget_gate.get("overall_ok")),
                "non_info_count": int(discrepancy_budget_gate.get("non_info_count") or 0),
                "top_risk_score": round(_safe_float(discrepancy_budget_gate.get("top_risk_score"), 0.0), 6),
            },
            "reliability_flake_budget_gate": {
                "overall_ok": bool(reliability_budget_gate.get("overall_ok")),
                "counts": reliability_budget_gate.get("counts", {}),
            },
            "latency_drift_gate": {
                "overall_ok": bool(latency_drift_gate.get("overall_ok")),
                "worst_threshold_ratio": _safe_float(
                    latency_drift_gate.get("worst_threshold_ratio"), 0.0
                ),
                "worst_metric_context": latency_drift_gate.get("worst_metric_context", {}),
            },
            "canary_wait_alignment_gate": {"overall_ok": bool(wait_alignment_gate.get("overall_ok"))},
            "ranking": {
                "item_count": int(discrepancy_ranking.get("count") or len(items)),
                "non_info_count": non_info_count,
                "top_risk_score": round(top_risk, 6),
            },
            "latency_trend": {
                "overall_ok": bool(latency_trend.get("overall_ok")),
                "summary_max_frame_gap_seconds": _safe_float(
                    ((latency_trend.get("summary") or {}).get("max_frame_gap_seconds") or {}).get("max"),
                    0.0,
                ),
                "summary_max_non_wait_frame_gap_seconds": _safe_float(
                    ((latency_trend.get("summary") or {}).get("max_non_wait_frame_gap_seconds") or {}).get(
                        "max"
                    ),
                    0.0,
                ),
                "near_threshold_top": ((latency_trend.get("near_threshold") or {}).get("top") or [])[:3],
            },
        },
        "sources": dict(sources),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    foot = checks.get("footer_contrast_gate", {})
    disc = checks.get("discrepancy_budget_gate", {})
    rel = checks.get("reliability_flake_budget_gate", {})
    lat = checks.get("latency_drift_gate", {})
    wait = checks.get("canary_wait_alignment_gate", {})
    rank = checks.get("ranking", {})
    counts = rel.get("counts", {}) if isinstance(rel, dict) else {}
    lines = [
        "# Phase5 Post-Cert Monitoring",
        "",
        f"- stamp: `{report.get('stamp')}`",
        f"- overall_ok: `{bool(report.get('overall_ok'))}`",
        "",
        "## Gate Summary",
        "",
        f"- footer contrast gate: `overall_ok={bool(foot.get('overall_ok'))}`, "
        f"`failure_count={int(foot.get('failure_count') or 0)}`",
        f"- discrepancy budget gate: `overall_ok={bool(disc.get('overall_ok'))}`, "
        f"`non_info_count={int(disc.get('non_info_count') or 0)}`, "
        f"`top_risk_score={_safe_float(disc.get('top_risk_score'), 0.0):.6f}`",
        f"- reliability flake budget gate: `overall_ok={bool(rel.get('overall_ok'))}`, "
        f"`intermittent={int(counts.get('intermittent-flake') or 0)}`, "
        f"`persistent={int(counts.get('persistent-fail') or 0)}`, "
        f"`active={int(counts.get('active-regression') or 0)}`",
        f"- canary wait alignment gate: `overall_ok={bool(wait.get('overall_ok'))}`",
        f"- latency drift gate: `overall_ok={bool(lat.get('overall_ok'))}`, "
        f"`worst_threshold_ratio={_safe_float(lat.get('worst_threshold_ratio'), 0.0):.6f}`",
        f"- ranking: `items={int(rank.get('item_count') or 0)}`, "
        f"`non_info={int(rank.get('non_info_count') or 0)}`, "
        f"`top_risk={_safe_float(rank.get('top_risk_score'), 0.0):.6f}`",
        "",
        "## Sources",
        "",
    ]
    src = report.get("sources", {})
    if isinstance(src, dict):
        for k, v in src.items():
            lines.append(f"- `{k}`: `{v}`")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stamp", required=True)
    p.add_argument("--footer-gate-json", required=True)
    p.add_argument("--discrepancy-budget-json", required=True)
    p.add_argument("--reliability-budget-json", required=True)
    p.add_argument("--latency-drift-json", required=True)
    p.add_argument("--wait-alignment-json", required=True)
    p.add_argument("--ranking-json", required=True)
    p.add_argument("--latency-trend-json", required=True)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    footer_path = Path(args.footer_gate_json).expanduser().resolve()
    disc_path = Path(args.discrepancy_budget_json).expanduser().resolve()
    rel_path = Path(args.reliability_budget_json).expanduser().resolve()
    lat_path = Path(args.latency_drift_json).expanduser().resolve()
    wait_path = Path(args.wait_alignment_json).expanduser().resolve()
    rank_path = Path(args.ranking_json).expanduser().resolve()
    trend_path = Path(args.latency_trend_json).expanduser().resolve()

    report = build_report(
        stamp=str(args.stamp),
        footer_gate=_load_json(footer_path),
        discrepancy_budget_gate=_load_json(disc_path),
        reliability_budget_gate=_load_json(rel_path),
        latency_drift_gate=_load_json(lat_path),
        wait_alignment_gate=_load_json(wait_path),
        discrepancy_ranking=_load_json(rank_path),
        latency_trend=_load_json(trend_path),
        sources={
            "footer_contrast_gate": str(footer_path),
            "discrepancy_budget_gate": str(disc_path),
            "reliability_flake_budget_gate": str(rel_path),
            "latency_drift_gate": str(lat_path),
            "canary_wait_alignment_gate": str(wait_path),
            "discrepancy_ranking": str(rank_path),
            "latency_trend": str(trend_path),
        },
    )

    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report), encoding="utf-8")

    print(f"overall_ok={bool(report.get('overall_ok'))}")
    print(f"stamp={report.get('stamp')}")
    print(f"json={out_json}")
    return 0 if bool(report.get("overall_ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

