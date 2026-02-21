#!/usr/bin/env python3
"""
Aggregate multiple phase4 tier3 health snapshots into one trend artifact.
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


def _load(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _collect_metric(samples: list[dict[str, Any]], lane: str, metric: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        lanes = sample.get("lanes")
        if not isinstance(lanes, dict):
            continue
        lane_obj = lanes.get(lane)
        if not isinstance(lane_obj, dict):
            continue
        perf = lane_obj.get("perf")
        if not isinstance(perf, dict):
            continue
        v = _num(perf.get(metric))
        if v is not None:
            values.append(v)
    return values


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate phase4 tier3 health snapshots.")
    p.add_argument(
        "--glob",
        dest="glob_pattern",
        default="docs_tmp/tmux_captures/phase4_tier3_run_health*.json",
        help="glob pattern for source health JSON files",
    )
    p.add_argument("--output-json", required=True, help="aggregate output JSON")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(glob.glob(args.glob_pattern))
    samples: list[dict[str, Any]] = []
    for file in files:
        payload = _load(Path(file))
        if payload is None:
            continue
        generated = str(payload.get("generated_at_utc") or "")
        samples.append(
            {
                "path": str(Path(file).resolve()),
                "generated_at_utc": generated,
                "overall_ok": bool(payload.get("overall_ok")),
                "rc": payload.get("rc", {}),
                "lanes": payload.get("lanes", {}),
            }
        )

    samples.sort(key=lambda row: row.get("generated_at_utc", ""))

    lanes = ("provider_codex", "provider_claude", "resize_storm", "concurrency_20")
    metrics = (
        "interval_p95_ms",
        "line_churn_p95",
        "status_line_churn_rate",
        "telemetry_eventMaxQueueDepth_max",
        "telemetry_workgraphMaxQueueDepth_max",
        "telemetry_statusTransitions_max",
        "telemetry_workgraphLaneTransitions_max",
        "telemetry_workgraphDroppedTransitions_max",
        "telemetry_workgraphLaneChurn_max",
        "telemetry_workgraphDroppedEvents_max",
    )
    trend: dict[str, Any] = {}
    for lane in lanes:
        lane_row: dict[str, Any] = {}
        for metric in metrics:
            vals = _collect_metric(samples, lane, metric)
            lane_row[metric] = {
                "count": len(vals),
                "latest": (None if not vals else vals[-1]),
                "mean": (None if not vals else float(mean(vals))),
                "max": (None if not vals else float(max(vals))),
            }
        trend[lane] = lane_row

    overall_ok_count = sum(1 for s in samples if s.get("overall_ok") is True)
    aggregate = {
        "schema_version": "phase4_tier3_health_aggregate_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "overall_ok_count": overall_ok_count,
        "overall_ok_ratio": (0.0 if not samples else overall_ok_count / len(samples)),
        "samples": samples,
        "trend": trend,
    }

    out = Path(args.output_json).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
    print(f"[phase4-tier3-aggregate] wrote: {out}")
    print(f"[phase4-tier3-aggregate] sample_count={aggregate['sample_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
