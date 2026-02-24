#!/usr/bin/env python3
"""
Hard-gate replay discrepancy ranking against severity/risk budgets.
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


def _normalize_severity(value: Any) -> str:
    sev = str(value or "").strip().lower()
    if sev in {"high", "medium", "low", "info"}:
        return sev
    return "unknown"


def build_report(
    *,
    ranking: dict[str, Any],
    max_high: int,
    max_medium: int,
    max_low: int,
    max_unknown: int,
    max_non_info: int,
    max_top_risk: float,
) -> dict[str, Any]:
    rows = ranking.get("items", [])
    if not isinstance(rows, list):
        rows = []

    sev_counts = {"high": 0, "medium": 0, "low": 0, "info": 0, "unknown": 0}
    actionable_rows: list[dict[str, Any]] = []
    top_risk = 0.0
    for raw in rows:
        row = raw if isinstance(raw, dict) else {}
        sev = _normalize_severity(row.get("severity"))
        sev_counts[sev] = sev_counts.get(sev, 0) + 1
        risk = _safe_float(row.get("risk_score"), 0.0)
        if risk > top_risk:
            top_risk = risk
        if sev in {"high", "medium", "low", "unknown"}:
            actionable_rows.append(
                {
                    "key": str(row.get("key") or ""),
                    "category": str(row.get("category") or ""),
                    "severity": sev,
                    "risk_score": risk,
                    "summary": str(row.get("summary") or ""),
                }
            )

    failures: list[dict[str, Any]] = []
    if sev_counts["high"] > max_high:
        failures.append(
            {
                "check": "high_budget",
                "actual": sev_counts["high"],
                "allowed": max_high,
                "message": "Too many high-severity discrepancy items.",
            }
        )
    if sev_counts["medium"] > max_medium:
        failures.append(
            {
                "check": "medium_budget",
                "actual": sev_counts["medium"],
                "allowed": max_medium,
                "message": "Too many medium-severity discrepancy items.",
            }
        )
    if sev_counts["low"] > max_low:
        failures.append(
            {
                "check": "low_budget",
                "actual": sev_counts["low"],
                "allowed": max_low,
                "message": "Too many low-severity discrepancy items.",
            }
        )
    if sev_counts["unknown"] > max_unknown:
        failures.append(
            {
                "check": "unknown_budget",
                "actual": sev_counts["unknown"],
                "allowed": max_unknown,
                "message": "Unexpected unknown-severity discrepancy items present.",
            }
        )
    non_info = sev_counts["high"] + sev_counts["medium"] + sev_counts["low"] + sev_counts["unknown"]
    if non_info > max_non_info:
        failures.append(
            {
                "check": "non_info_budget",
                "actual": non_info,
                "allowed": max_non_info,
                "message": "Too many non-info discrepancy items in ranking.",
            }
        )
    if top_risk > max_top_risk:
        failures.append(
            {
                "check": "top_risk_budget",
                "actual": round(top_risk, 6),
                "allowed": max_top_risk,
                "message": "Top discrepancy risk score exceeds budget.",
            }
        )

    return {
        "schema_version": "phase5_discrepancy_budget_gate_v1",
        "overall_ok": len(failures) == 0,
        "total_items": len(rows),
        "severity_counts": sev_counts,
        "non_info_count": non_info,
        "top_risk_score": round(top_risk, 6),
        "budgets": {
            "max_high": int(max_high),
            "max_medium": int(max_medium),
            "max_low": int(max_low),
            "max_unknown": int(max_unknown),
            "max_non_info": int(max_non_info),
            "max_top_risk": float(max_top_risk),
        },
        "failures": failures,
        "top_actionable_items": actionable_rows[:20],
    }


def _render_markdown(report: dict[str, Any], ranking_json: Path) -> str:
    sev = report.get("severity_counts", {})
    lines = [
        "# Phase5 Discrepancy Budget Gate",
        "",
        f"- ranking_json: `{ranking_json}`",
        f"- total_items: `{int(report.get('total_items') or 0)}`",
        f"- overall_ok: `{bool(report.get('overall_ok'))}`",
        f"- top_risk_score: `{float(report.get('top_risk_score') or 0.0):.6f}`",
        "",
        "## Severity Counts",
        "",
        f"- high: `{int((sev or {}).get('high') or 0)}`",
        f"- medium: `{int((sev or {}).get('medium') or 0)}`",
        f"- low: `{int((sev or {}).get('low') or 0)}`",
        f"- info: `{int((sev or {}).get('info') or 0)}`",
        f"- unknown: `{int((sev or {}).get('unknown') or 0)}`",
        "",
        "## Failures",
    ]
    failures = report.get("failures", [])
    if not isinstance(failures, list) or not failures:
        lines.append("")
        lines.append("- none")
    else:
        lines.append("")
        for row in failures:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('check')}: actual={row.get('actual')} allowed={row.get('allowed')} "
                f"({row.get('message')})"
            )
    lines.extend(
        [
            "",
            "## Top Actionable Items",
            "",
            "| Key | Category | Severity | Risk | Summary |",
            "|---|---|---|---:|---|",
        ]
    )
    actionables = report.get("top_actionable_items", [])
    if isinstance(actionables, list):
        for row in actionables:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {row.get('key')} | {row.get('category')} | {row.get('severity')} | "
                f"{float(row.get('risk_score') or 0.0):.4f} | {row.get('summary')} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ranking-json", required=True)
    p.add_argument("--max-high", type=int, default=0)
    p.add_argument("--max-medium", type=int, default=0)
    p.add_argument("--max-low", type=int, default=0)
    p.add_argument("--max-unknown", type=int, default=0)
    p.add_argument("--max-non-info", type=int, default=0)
    p.add_argument("--max-top-risk", type=float, default=0.0)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    ranking_path = Path(args.ranking_json).expanduser().resolve()
    ranking = _load_json(ranking_path)
    report = build_report(
        ranking=ranking,
        max_high=int(args.max_high),
        max_medium=int(args.max_medium),
        max_low=int(args.max_low),
        max_unknown=int(args.max_unknown),
        max_non_info=int(args.max_non_info),
        max_top_risk=float(args.max_top_risk),
    )

    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report, ranking_path), encoding="utf-8")

    print(f"overall_ok={bool(report.get('overall_ok'))}")
    print(f"non_info={int(report.get('non_info_count') or 0)}")
    print(f"top_risk={float(report.get('top_risk_score') or 0.0):.6f}")
    print(f"json={out_json}")
    return 0 if bool(report.get("overall_ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

