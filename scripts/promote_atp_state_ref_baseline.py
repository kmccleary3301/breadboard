#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_atp_artifacts import validate_state_ref_baseline_meta, validate_state_ref_fastpath_summary


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote ATP state-ref measured artifact to canonical baseline.")
    parser.add_argument(
        "--candidate",
        required=True,
        help="Measured benchmark JSON artifact to promote.",
    )
    parser.add_argument(
        "--baseline",
        default="artifacts/atp_state_ref_fastpath_baseline.json",
        help="Canonical baseline output path.",
    )
    parser.add_argument(
        "--meta-out",
        default="artifacts/atp_state_ref_fastpath_baseline.meta.json",
        help="Metadata output path for promotion record.",
    )
    args = parser.parse_args()

    candidate_path = Path(args.candidate).resolve()
    if not candidate_path.exists():
        raise FileNotFoundError(candidate_path)
    baseline_path = Path(args.baseline).resolve()
    meta_path = Path(args.meta_out).resolve()

    payload = _load_json(candidate_path)
    validate_state_ref_fastpath_summary(payload)
    if payload.get("meets_recommended_sample_depth") is not True:
        raise ValueError(
            "Refusing promotion: candidate does not meet recommended sample depth "
            "(requires measured run with sufficient ITERS)."
        )

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    if candidate_path.resolve() != baseline_path.resolve():
        shutil.copy2(candidate_path, baseline_path)

    meta = {
        "promoted_at": time.time(),
        "candidate_path": str(candidate_path),
        "baseline_path": str(baseline_path),
        "mode": payload.get("mode"),
        "concurrency": payload.get("concurrency"),
        "workers_count": len(payload.get("workers") or []),
        "iters_per_worker": payload.get("iters_per_worker"),
        "burn_in_iters_per_worker": payload.get("burn_in_iters_per_worker"),
        "min_recommended_iters": payload.get("min_recommended_iters"),
        "meets_recommended_sample_depth": payload.get("meets_recommended_sample_depth"),
        "repl_ms_avg_across_workers": payload.get("repl_ms_avg_across_workers"),
        "unpickle_s_avg_across_workers": payload.get("unpickle_s_avg_across_workers"),
        "request_wall_s_avg_across_workers": payload.get("request_wall_s_avg_across_workers"),
        "request_wall_s_p95_across_workers": payload.get("request_wall_s_p95_across_workers"),
        "file_io_b64_active_all_workers": payload.get("file_io_b64_active_all_workers"),
    }
    validate_state_ref_baseline_meta(meta)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

    print(f"baseline_promoted: {baseline_path}")
    print(f"baseline_meta: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
