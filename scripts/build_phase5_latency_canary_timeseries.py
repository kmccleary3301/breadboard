#!/usr/bin/env python3
"""
Build a latency canary timeseries from Phase5 roundtrip trend reports.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _extract_stamp(path: Path) -> str:
    stem = path.stem
    prefix = "phase5_roundtrip_latency_trend_"
    if stem.startswith(prefix):
        return stem[len(prefix) :]
    return stem


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _stamp_sort_key(stamp: str) -> tuple[str, int, str]:
    m = re.search(r"(.*?)-roundtrip(\d+)$", stamp)
    if not m:
        return (stamp, -1, stamp)
    prefix = m.group(1)
    idx = int(m.group(2))
    return (prefix, idx, stamp)


def build_timeseries(
    *,
    trend_paths: list[Path],
    scenario: str,
    metric: str,
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for path in sorted(trend_paths):
        payload = _load_json(path)
        thresholds = payload.get("thresholds", {})
        if not isinstance(thresholds, dict):
            thresholds = {}
        limit_key = metric if metric.startswith("max_") else f"max_{metric}"
        threshold = _safe_float(thresholds.get(limit_key), 0.0)
        if threshold <= 0:
            continue
        records = payload.get("records", [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("scenario") or "") != scenario:
                continue
            metrics = record.get("metrics", {})
            if not isinstance(metrics, dict):
                continue
            observed = metrics.get(metric)
            if not isinstance(observed, (int, float)):
                continue
            ratio = float(observed) / threshold
            points.append(
                {
                    "stamp": _extract_stamp(path),
                    "trend_json": str(path),
                    "observed": float(observed),
                    "threshold": float(threshold),
                    "ratio": ratio,
                }
            )
            break

    points.sort(key=lambda row: _stamp_sort_key(str(row.get("stamp") or "")))
    ratios = [_safe_float(row.get("ratio")) for row in points]
    summary = {
        "count": len(points),
        "latest_ratio": ratios[-1] if ratios else 0.0,
        "max_ratio": max(ratios) if ratios else 0.0,
        "min_ratio": min(ratios) if ratios else 0.0,
        "mean_ratio": (sum(ratios) / len(ratios)) if ratios else 0.0,
    }
    return {
        "schema_version": "phase5_latency_canary_timeseries_v1",
        "scenario": scenario,
        "metric": metric,
        "summary": summary,
        "points": points,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Phase5 Latency Canary Timeseries",
        "",
        f"- scenario: `{payload.get('scenario')}`",
        f"- metric: `{payload.get('metric')}`",
        f"- points: `{int(summary.get('count') or 0)}`",
        f"- latest_ratio: `{float(summary.get('latest_ratio') or 0.0):.3f}`",
        f"- max_ratio: `{float(summary.get('max_ratio') or 0.0):.3f}`",
        "",
        "| Stamp | Observed | Threshold | Ratio |",
        "|---|---:|---:|---:|",
    ]
    for row in payload.get("points", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {row.get('stamp')} | {float(row.get('observed') or 0.0):.3f} | {float(row.get('threshold') or 0.0):.3f} | {float(row.get('ratio') or 0.0):.3f} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build latency canary timeseries from trend reports.")
    p.add_argument("--trend-glob", default="docs_tmp/cli_phase_5/phase5_roundtrip_latency_trend_*.json")
    p.add_argument("--scenario", required=True)
    p.add_argument("--metric", default="max_frame_gap_seconds")
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    paths = [Path(p).expanduser().resolve() for p in glob.glob(args.trend_glob)]
    payload = build_timeseries(
        trend_paths=paths,
        scenario=str(args.scenario),
        metric=str(args.metric),
    )
    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(payload), encoding="utf-8")
    print(f"points={payload['summary']['count']}")
    print(f"latest_ratio={payload['summary']['latest_ratio']:.3f}")
    print(f"json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
