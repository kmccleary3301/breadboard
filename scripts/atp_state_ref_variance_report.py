#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_atp_artifacts import validate_state_ref_fastpath_summary


def _load(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    validate_state_ref_fastpath_summary(payload)
    return payload


def _collect(glob_pattern: str, limit: int) -> List[Path]:
    files = [path.resolve() for path in Path(".").glob(glob_pattern) if path.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    capped = files[: max(1, int(limit))]
    return list(reversed(capped))


def _pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def _stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "stdev": None, "min": None, "max": None, "cv_pct": None, "trend_per_run": None}
    count = len(values)
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if count > 1 else 0.0
    cv_pct = (stdev / mean * 100.0) if mean else 0.0
    trend = ((values[-1] - values[0]) / (count - 1)) if count > 1 else 0.0
    return {
        "count": count,
        "mean": mean,
        "stdev": stdev,
        "min": min(values),
        "max": max(values),
        "cv_pct": cv_pct,
        "trend_per_run": trend,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ATP state-ref variance report across recent measured artifacts.")
    parser.add_argument(
        "--glob",
        default="artifacts/atp_state_ref_fastpath_*_measured*.json",
        help="Glob for measured benchmark artifacts.",
    )
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of files to include.")
    parser.add_argument("--baseline", default="", help="Optional baseline JSON for latest-vs-baseline deltas.")
    parser.add_argument("--out", default="artifacts/atp_state_ref_variance_report.json", help="Output JSON path.")
    args = parser.parse_args()

    files = _collect(args.glob, max(1, int(args.limit)))
    if not files:
        raise FileNotFoundError(f"No artifacts matched glob: {args.glob}")

    rows = [_load(path) for path in files]
    metric_keys = [
        "repl_ms_avg_across_workers",
        "unpickle_s_avg_across_workers",
        "request_wall_s_avg_across_workers",
        "request_wall_s_p95_across_workers",
    ]
    metrics: Dict[str, Dict[str, Any]] = {}
    for key in metric_keys:
        vals = [float(item[key]) for item in rows]
        metrics[key] = _stats(vals)

    shape_keys = [
        "concurrency",
        "iters_per_worker",
        "burn_in_iters_per_worker",
        "min_recommended_iters",
        "file_io_b64_active_all_workers",
    ]
    shape_rows = []
    for row in rows:
        shape = {key: row.get(key) for key in shape_keys}
        shape["workers_count"] = len(row.get("workers") or [])
        shape_rows.append(shape)
    shape_reference = shape_rows[0]
    shape_consistent = all(item == shape_reference for item in shape_rows)

    latest = rows[-1]
    baseline_path = Path(args.baseline).resolve() if args.baseline else None
    baseline_deltas: Dict[str, float] | None = None
    if baseline_path:
        baseline_payload = _load(baseline_path)
        baseline_deltas = {
            key: _pct_change(float(latest[key]), float(baseline_payload[key]))
            for key in metric_keys
        }

    report = {
        "mode": "state_ref_fastpath_variance_report",
        "generated_at": time.time(),
        "glob": args.glob,
        "sample_count": len(rows),
        "files": [str(path) for path in files],
        "shape": {
            "consistent": shape_consistent,
            "reference": shape_reference,
        },
        "metrics": metrics,
        "baseline_path": str(baseline_path) if baseline_path else None,
        "latest_vs_baseline_delta_pct": baseline_deltas,
    }

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"atp_state_ref_variance_report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
