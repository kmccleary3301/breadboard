from __future__ import annotations

import statistics
from typing import Any, Mapping


def summarize_stage_metrics(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    keys = sorted({key for row in rows for key in row.get("metrics_ms", {})})
    summary: dict[str, dict[str, float]] = {}
    for key in keys:
        values = sorted(float(row["metrics_ms"][key]) for row in rows if key in row.get("metrics_ms", {}))
        if not values:
            continue
        p95_index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
        summary[key] = {
            "p50": float(statistics.median(values)),
            "p95": float(values[p95_index]),
            "count": float(len(values)),
        }
    return summary


def build_warm_vs_cold_report(*, warm_rows: list[Mapping[str, Any]], cold_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    warm = summarize_stage_metrics(warm_rows)
    cold = summarize_stage_metrics(cold_rows)
    return {
        "warm": warm,
        "cold": cold,
        "claim_boundary": "local_ray_warm_pool_probe_not_production_scale",
    }
