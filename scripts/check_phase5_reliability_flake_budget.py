#!/usr/bin/env python3
"""
Hard-gate Phase5 reliability flake report against classification budgets.
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


def build_report(
    *,
    flake_report: dict[str, Any],
    max_persistent: int,
    max_active: int,
    max_intermittent: int,
    max_transient: int,
) -> dict[str, Any]:
    scenarios = flake_report.get("scenarios", [])
    if not isinstance(scenarios, list):
        scenarios = []

    counts = {
        "stable-pass": 0,
        "transient-single-recovery": 0,
        "intermittent-flake": 0,
        "persistent-fail": 0,
        "active-regression": 0,
        "other": 0,
    }
    flagged_rows: list[dict[str, Any]] = []
    for row in scenarios:
        if not isinstance(row, dict):
            continue
        cls = str(row.get("classification") or "")
        if cls in counts:
            counts[cls] += 1
        else:
            counts["other"] += 1
        if cls in {"intermittent-flake", "persistent-fail", "active-regression"}:
            flagged_rows.append(
                {
                    "scenario": str(row.get("scenario") or ""),
                    "classification": cls,
                    "fail_count": int(row.get("fail_count") or 0),
                    "sample_count": int(row.get("sample_count") or 0),
                    "latest_pass": bool(row.get("latest_pass")),
                }
            )

    failures: list[dict[str, Any]] = []
    if counts["persistent-fail"] > max_persistent:
        failures.append(
            {
                "check": "persistent_budget",
                "actual": counts["persistent-fail"],
                "allowed": max_persistent,
                "message": "Persistent flake regressions exceed budget.",
            }
        )
    if counts["active-regression"] > max_active:
        failures.append(
            {
                "check": "active_budget",
                "actual": counts["active-regression"],
                "allowed": max_active,
                "message": "Active reliability regressions exceed budget.",
            }
        )
    if counts["intermittent-flake"] > max_intermittent:
        failures.append(
            {
                "check": "intermittent_budget",
                "actual": counts["intermittent-flake"],
                "allowed": max_intermittent,
                "message": "Intermittent flakes exceed budget.",
            }
        )
    if counts["transient-single-recovery"] > max_transient:
        failures.append(
            {
                "check": "transient_budget",
                "actual": counts["transient-single-recovery"],
                "allowed": max_transient,
                "message": "Transient recoveries exceed budget.",
            }
        )
    if counts["other"] > 0:
        failures.append(
            {
                "check": "unknown_classification",
                "actual": counts["other"],
                "allowed": 0,
                "message": "Unknown reliability classification values detected.",
            }
        )

    return {
        "schema_version": "phase5_reliability_flake_budget_gate_v1",
        "overall_ok": len(failures) == 0,
        "scenario_count": len(scenarios),
        "counts": counts,
        "budgets": {
            "max_persistent": int(max_persistent),
            "max_active": int(max_active),
            "max_intermittent": int(max_intermittent),
            "max_transient": int(max_transient),
        },
        "failures": failures,
        "flagged": flagged_rows,
    }


def _render_markdown(report: dict[str, Any], flake_path: Path) -> str:
    counts = report.get("counts", {})
    lines = [
        "# Phase5 Reliability Flake Budget Gate",
        "",
        f"- reliability_flake_report: `{flake_path}`",
        f"- scenario_count: `{int(report.get('scenario_count') or 0)}`",
        f"- overall_ok: `{bool(report.get('overall_ok'))}`",
        "",
        "## Counts",
        "",
        f"- stable-pass: `{int((counts or {}).get('stable-pass') or 0)}`",
        f"- transient-single-recovery: `{int((counts or {}).get('transient-single-recovery') or 0)}`",
        f"- intermittent-flake: `{int((counts or {}).get('intermittent-flake') or 0)}`",
        f"- persistent-fail: `{int((counts or {}).get('persistent-fail') or 0)}`",
        f"- active-regression: `{int((counts or {}).get('active-regression') or 0)}`",
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
            "## Flagged Scenarios",
            "",
            "| Scenario | Classification | Fail Count | Sample Count | Latest Pass |",
            "|---|---|---:|---:|---:|",
        ]
    )
    flagged = report.get("flagged", [])
    if isinstance(flagged, list):
        for row in flagged:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {row.get('scenario')} | {row.get('classification')} | "
                f"{int(row.get('fail_count') or 0)} | {int(row.get('sample_count') or 0)} | "
                f"{1 if bool(row.get('latest_pass')) else 0} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reliability-flake-json", required=True)
    p.add_argument("--max-persistent", type=int, default=0)
    p.add_argument("--max-active", type=int, default=0)
    p.add_argument("--max-intermittent", type=int, default=0)
    p.add_argument("--max-transient", type=int, default=1)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    flake_path = Path(args.reliability_flake_json).expanduser().resolve()
    flake_report = _load_json(flake_path)
    report = build_report(
        flake_report=flake_report,
        max_persistent=int(args.max_persistent),
        max_active=int(args.max_active),
        max_intermittent=int(args.max_intermittent),
        max_transient=int(args.max_transient),
    )

    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report, flake_path), encoding="utf-8")

    print(f"overall_ok={bool(report.get('overall_ok'))}")
    print(f"intermittent={int((report.get('counts') or {}).get('intermittent-flake') or 0)}")
    print(f"persistent={int((report.get('counts') or {}).get('persistent-fail') or 0)}")
    print(f"active={int((report.get('counts') or {}).get('active-regression') or 0)}")
    print(f"json={out_json}")
    return 0 if bool(report.get("overall_ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

