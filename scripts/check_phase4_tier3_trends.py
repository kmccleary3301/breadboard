#!/usr/bin/env python3
"""
Check phase4 tier3 trend aggregate against configurable thresholds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check phase4 tier3 trend thresholds.")
    p.add_argument("--aggregate-json", required=True)
    p.add_argument("--max-interval-p95-ms", type=float, default=1200.0)
    p.add_argument("--max-line-churn-p95", type=float, default=0.20)
    p.add_argument("--max-status-line-churn-rate", type=float, default=0.65)
    p.add_argument("--max-event-max-queue-depth", type=float, default=256.0)
    p.add_argument("--max-workgraph-dropped-transitions", type=float, default=0.0)
    p.add_argument("--max-workgraph-lane-churn", type=float, default=50.0)
    p.add_argument("--max-workgraph-dropped-events", type=float, default=0.0)
    p.add_argument("--mode", choices=("warn", "strict"), default="warn")
    p.add_argument("--output-json", default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    aggregate = json.loads(Path(args.aggregate_json).read_text(encoding="utf-8"))
    trend = aggregate.get("trend", {}) if isinstance(aggregate, dict) else {}
    lanes = ("resize_storm", "concurrency_20")

    failures: list[str] = []
    warnings: list[str] = []

    for lane in lanes:
        lane_row = trend.get(lane, {}) if isinstance(trend, dict) else {}
        interval_latest = _as_float((((lane_row or {}).get("interval_p95_ms") or {}).get("latest")))
        churn_latest = _as_float((((lane_row or {}).get("line_churn_p95") or {}).get("latest")))
        status_churn_latest = _as_float((((lane_row or {}).get("status_line_churn_rate") or {}).get("latest")))
        queue_depth_latest = _as_float((((lane_row or {}).get("telemetry_eventMaxQueueDepth_max") or {}).get("latest")))
        dropped_transitions_latest = _as_float(
            (((lane_row or {}).get("telemetry_workgraphDroppedTransitions_max") or {}).get("latest"))
        )
        lane_churn_latest = _as_float((((lane_row or {}).get("telemetry_workgraphLaneChurn_max") or {}).get("latest")))
        dropped_events_latest = _as_float(
            (((lane_row or {}).get("telemetry_workgraphDroppedEvents_max") or {}).get("latest"))
        )

        if interval_latest is None:
            warnings.append(f"{lane}: missing interval_p95_ms.latest")
        elif interval_latest > args.max_interval_p95_ms:
            failures.append(
                f"{lane}: interval_p95_ms.latest={interval_latest:.3f} > max={args.max_interval_p95_ms:.3f}"
            )

        if churn_latest is None:
            warnings.append(f"{lane}: missing line_churn_p95.latest")
        elif churn_latest > args.max_line_churn_p95:
            failures.append(
                f"{lane}: line_churn_p95.latest={churn_latest:.6f} > max={args.max_line_churn_p95:.6f}"
            )

        if status_churn_latest is None:
            warnings.append(f"{lane}: missing status_line_churn_rate.latest")
        elif status_churn_latest > args.max_status_line_churn_rate:
            failures.append(
                f"{lane}: status_line_churn_rate.latest={status_churn_latest:.6f} > max={args.max_status_line_churn_rate:.6f}"
            )

        if queue_depth_latest is None:
            warnings.append(f"{lane}: missing telemetry_eventMaxQueueDepth_max.latest")
        elif queue_depth_latest > args.max_event_max_queue_depth:
            failures.append(
                f"{lane}: telemetry_eventMaxQueueDepth_max.latest={queue_depth_latest:.3f} > max={args.max_event_max_queue_depth:.3f}"
            )

        if dropped_transitions_latest is None:
            warnings.append(f"{lane}: missing telemetry_workgraphDroppedTransitions_max.latest")
        elif dropped_transitions_latest > args.max_workgraph_dropped_transitions:
            failures.append(
                f"{lane}: telemetry_workgraphDroppedTransitions_max.latest={dropped_transitions_latest:.3f} > max={args.max_workgraph_dropped_transitions:.3f}"
            )

        if lane_churn_latest is None:
            warnings.append(f"{lane}: missing telemetry_workgraphLaneChurn_max.latest")
        elif lane_churn_latest > args.max_workgraph_lane_churn:
            failures.append(
                f"{lane}: telemetry_workgraphLaneChurn_max.latest={lane_churn_latest:.3f} > max={args.max_workgraph_lane_churn:.3f}"
            )

        if dropped_events_latest is None:
            warnings.append(f"{lane}: missing telemetry_workgraphDroppedEvents_max.latest")
        elif dropped_events_latest > args.max_workgraph_dropped_events:
            failures.append(
                f"{lane}: telemetry_workgraphDroppedEvents_max.latest={dropped_events_latest:.3f} > max={args.max_workgraph_dropped_events:.3f}"
            )

    report = {
        "schema_version": "phase4_tier3_trend_check_v2",
        "mode": args.mode,
        "ok": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "thresholds": {
            "max_interval_p95_ms": args.max_interval_p95_ms,
            "max_line_churn_p95": args.max_line_churn_p95,
            "max_status_line_churn_rate": args.max_status_line_churn_rate,
            "max_event_max_queue_depth": args.max_event_max_queue_depth,
            "max_workgraph_dropped_transitions": args.max_workgraph_dropped_transitions,
            "max_workgraph_lane_churn": args.max_workgraph_lane_churn,
            "max_workgraph_dropped_events": args.max_workgraph_dropped_events,
        },
    }

    if args.output_json:
        out = Path(args.output_json).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if failures:
        print("[phase4-tier3-trend-check] fail")
        for row in failures:
            print(f"- {row}")
        if args.mode == "strict":
            return 2
    else:
        print("[phase4-tier3-trend-check] pass")

    for row in warnings:
        print(f"[warn] {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
