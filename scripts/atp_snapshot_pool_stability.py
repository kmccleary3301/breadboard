#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List


def _load(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _collect(glob_pattern: str, limit: int) -> List[Path]:
    files = [path.resolve() for path in Path(".").glob(glob_pattern) if path.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    limited = files[: max(1, int(limit))]
    return list(reversed(limited))


def _safe_mean(values: List[float]) -> float | None:
    return statistics.mean(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize repeated ATP concurrency stability from load artifacts.")
    parser.add_argument("--glob", default="artifacts/atp_firecracker_repl_load*.json")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--require-concurrency", type=int, default=4)
    parser.add_argument("--min-runs", type=int, default=3)
    parser.add_argument("--max-p95-ms", type=float, default=200.0)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--out", default="artifacts/atp_snapshot_pool_stability_summary.json")
    args = parser.parse_args()

    files = _collect(args.glob, args.limit)
    if not files:
        raise FileNotFoundError(f"No artifacts matched: {args.glob}")

    rows = []
    for path in files:
        payload = _load(path)
        row = {
            "path": str(path),
            "concurrency": int(payload.get("concurrency", 0) or 0),
            "p95_ms": float(payload.get("p95_ms", 0.0) or 0.0),
            "budget_pass": bool(payload.get("budget_pass")),
            "iters": int(payload.get("iters", 0) or 0),
            "duration_s": float(payload.get("duration_s", 0.0) or 0.0),
        }
        rows.append(row)

    filtered = [row for row in rows if row["concurrency"] == int(args.require_concurrency)]
    p95_values = [float(row["p95_ms"]) for row in filtered]
    budget_pass_count = sum(1 for row in filtered if row["budget_pass"])
    failures = [row for row in filtered if (not row["budget_pass"]) or (row["p95_ms"] > float(args.max_p95_ms))]
    run_count = len(filtered)
    fail_count = len(failures)

    ok = (
        run_count >= max(1, int(args.min_runs))
        and fail_count <= max(0, int(args.max_failures))
        and (max(p95_values) if p95_values else float("inf")) <= float(args.max_p95_ms)
    )
    if ok:
        classification = "stable_pass"
    elif run_count < max(1, int(args.min_runs)):
        classification = "insufficient_runs"
    elif fail_count and (budget_pass_count > 0):
        classification = "unstable_mixed"
    else:
        classification = "persistent_fail"

    summary = {
        "schema": "breadboard.atp.snapshot_pool_stability.v1",
        "generated_at": time.time(),
        "settings": {
            "glob": args.glob,
            "limit": int(args.limit),
            "require_concurrency": int(args.require_concurrency),
            "min_runs": int(args.min_runs),
            "max_p95_ms": float(args.max_p95_ms),
            "max_failures": int(args.max_failures),
        },
        "run_count": run_count,
        "ok": ok,
        "classification": classification,
        "p95_ms_avg": _safe_mean(p95_values),
        "p95_ms_max": max(p95_values) if p95_values else None,
        "budget_pass_count": budget_pass_count,
        "fail_count": fail_count,
        "failures": failures,
        "runs": filtered,
    }

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"atp_snapshot_pool_stability_summary: {out_path}")
    print(f"stability_classification: {classification}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
