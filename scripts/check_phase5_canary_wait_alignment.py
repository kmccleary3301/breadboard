#!/usr/bin/env python3
"""
Check canary hotspot wait-alignment expectations for Phase5 replay roundtrips.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def build_report(*, hotspot: dict[str, Any], max_top_unexpected_waits: int) -> dict[str, Any]:
    summary = hotspot.get("gap_summary", {})
    if not isinstance(summary, dict):
        summary = {}
    top_unexpected = int(summary.get("top_unexpected_wait_count") or 0)
    top_expected = int(summary.get("top_expected_wait_count") or 0)
    top_wait_dominated = int(summary.get("top_wait_dominated_count") or 0)
    overall_ok = top_unexpected <= max_top_unexpected_waits
    return {
        "schema_version": "phase5_canary_wait_alignment_v1",
        "overall_ok": overall_ok,
        "scenario": str(hotspot.get("scenario") or ""),
        "max_top_unexpected_waits": int(max_top_unexpected_waits),
        "metrics": {
            "top_wait_dominated_count": top_wait_dominated,
            "top_expected_wait_count": top_expected,
            "top_unexpected_wait_count": top_unexpected,
        },
        "action": (
            "No action required; canary wait alignment is within threshold."
            if overall_ok
            else "Investigate canary hotspot overlap actions; unexpected waits exceeded threshold."
        ),
    }


def _render_markdown(report: dict[str, Any], hotspot_path: Path) -> str:
    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    lines = [
        "# Phase5 Canary Wait Alignment",
        "",
        f"- hotspot_json: `{hotspot_path}`",
        f"- scenario: `{report.get('scenario')}`",
        f"- overall_ok: `{bool(report.get('overall_ok'))}`",
        f"- max_top_unexpected_waits: `{int(report.get('max_top_unexpected_waits') or 0)}`",
        "",
        "| top_wait_dominated_count | top_expected_wait_count | top_unexpected_wait_count |",
        "|---:|---:|---:|",
        (
            f"| {int(metrics.get('top_wait_dominated_count') or 0)} "
            f"| {int(metrics.get('top_expected_wait_count') or 0)} "
            f"| {int(metrics.get('top_unexpected_wait_count') or 0)} |"
        ),
        "",
        f"- action: {report.get('action')}",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--canary-hotspot-json", required=True)
    p.add_argument("--max-top-unexpected-waits", type=int, default=0)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    hotspot_path = Path(args.canary_hotspot_json).expanduser().resolve()
    hotspot = _load_json(hotspot_path)
    report = build_report(
        hotspot=hotspot,
        max_top_unexpected_waits=max(0, int(args.max_top_unexpected_waits)),
    )
    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report, hotspot_path), encoding="utf-8")
    print(f"overall_ok={bool(report.get('overall_ok'))}")
    print(f"json={out_json}")
    return 0 if bool(report.get("overall_ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
