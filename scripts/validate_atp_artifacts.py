#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _require(payload: Dict[str, Any], keys: Iterable[str], *, context: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"{context}: missing keys {missing}")


def validate_state_ref_fastpath_summary(payload: Dict[str, Any]) -> None:
    _require(
        payload,
        [
            "mode",
            "concurrency",
            "iters_per_worker",
            "burn_in_iters_per_worker",
            "min_recommended_iters",
            "meets_recommended_sample_depth",
            "workers",
            "file_io_b64_active_all_workers",
            "request_wall_s_avg_across_workers",
            "request_wall_s_p95_across_workers",
        ],
        context="state_ref_fastpath",
    )
    if payload.get("mode") != "state_ref_fastpath_bench":
        raise ValueError("state_ref_fastpath: mode must be 'state_ref_fastpath_bench'")
    if not isinstance(payload.get("iters_per_worker"), int) or int(payload["iters_per_worker"]) <= 0:
        raise ValueError("state_ref_fastpath: iters_per_worker must be an int > 0")
    if not isinstance(payload.get("burn_in_iters_per_worker"), int) or int(payload["burn_in_iters_per_worker"]) < 0:
        raise ValueError("state_ref_fastpath: burn_in_iters_per_worker must be an int >= 0")
    if not isinstance(payload.get("min_recommended_iters"), int) or int(payload["min_recommended_iters"]) <= 0:
        raise ValueError("state_ref_fastpath: min_recommended_iters must be an int > 0")
    if not isinstance(payload.get("meets_recommended_sample_depth"), bool):
        raise ValueError("state_ref_fastpath: meets_recommended_sample_depth must be bool")
    expected_depth_ok = int(payload["iters_per_worker"]) >= int(payload["min_recommended_iters"])
    if bool(payload["meets_recommended_sample_depth"]) != bool(expected_depth_ok):
        raise ValueError(
            "state_ref_fastpath: meets_recommended_sample_depth does not match "
            "iters_per_worker/min_recommended_iters relationship"
        )
    if not isinstance(payload.get("file_io_b64_active_all_workers"), bool):
        raise ValueError("state_ref_fastpath: file_io_b64_active_all_workers must be bool")
    if not isinstance(payload.get("workers"), list):
        raise ValueError("state_ref_fastpath: workers must be a list")
    for idx, worker in enumerate(payload["workers"]):
        if not isinstance(worker, dict):
            raise ValueError(f"state_ref_fastpath: workers[{idx}] must be an object")
        _require(
            worker,
            [
                "worker_id",
                "snapshot_dir",
                "iters",
                "burn_in_iters",
                "total_iters",
                "file_io_b64_active",
                "guest_vsock_capabilities",
            ],
            context=f"state_ref_fastpath.workers[{idx}]",
        )
        if not isinstance(worker.get("iters"), int) or int(worker["iters"]) <= 0:
            raise ValueError(f"state_ref_fastpath.workers[{idx}]: iters must be an int > 0")
        if not isinstance(worker.get("burn_in_iters"), int) or int(worker["burn_in_iters"]) < 0:
            raise ValueError(f"state_ref_fastpath.workers[{idx}]: burn_in_iters must be an int >= 0")
        if not isinstance(worker.get("total_iters"), int) or int(worker["total_iters"]) <= 0:
            raise ValueError(f"state_ref_fastpath.workers[{idx}]: total_iters must be an int > 0")
        if int(worker["total_iters"]) != int(worker["iters"]) + int(worker["burn_in_iters"]):
            raise ValueError(
                f"state_ref_fastpath.workers[{idx}]: total_iters must equal iters + burn_in_iters"
            )
        if not isinstance(worker.get("file_io_b64_active"), bool):
            raise ValueError(f"state_ref_fastpath.workers[{idx}]: file_io_b64_active must be bool")
        if not isinstance(worker.get("guest_vsock_capabilities"), dict):
            raise ValueError(
                f"state_ref_fastpath.workers[{idx}]: guest_vsock_capabilities must be an object"
            )


def validate_state_ref_baseline_meta(payload: Dict[str, Any]) -> None:
    _require(
        payload,
        [
            "promoted_at",
            "candidate_path",
            "baseline_path",
            "mode",
            "concurrency",
            "workers_count",
            "iters_per_worker",
            "burn_in_iters_per_worker",
            "min_recommended_iters",
            "meets_recommended_sample_depth",
            "repl_ms_avg_across_workers",
            "unpickle_s_avg_across_workers",
            "request_wall_s_avg_across_workers",
            "request_wall_s_p95_across_workers",
            "file_io_b64_active_all_workers",
        ],
        context="state_ref_fastpath_baseline_meta",
    )
    if payload.get("mode") != "state_ref_fastpath_bench":
        raise ValueError("state_ref_fastpath_baseline_meta: mode must be 'state_ref_fastpath_bench'")
    if not isinstance(payload.get("promoted_at"), (int, float)):
        raise ValueError("state_ref_fastpath_baseline_meta: promoted_at must be numeric epoch")
    for key in ("candidate_path", "baseline_path"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"state_ref_fastpath_baseline_meta: {key} must be a non-empty string")
    for key in ("concurrency", "workers_count", "iters_per_worker", "min_recommended_iters"):
        value = payload.get(key)
        if not isinstance(value, int) or int(value) <= 0:
            raise ValueError(f"state_ref_fastpath_baseline_meta: {key} must be an int > 0")
    burn_in = payload.get("burn_in_iters_per_worker")
    if not isinstance(burn_in, int) or int(burn_in) < 0:
        raise ValueError("state_ref_fastpath_baseline_meta: burn_in_iters_per_worker must be an int >= 0")
    if not isinstance(payload.get("meets_recommended_sample_depth"), bool):
        raise ValueError("state_ref_fastpath_baseline_meta: meets_recommended_sample_depth must be bool")
    if payload.get("meets_recommended_sample_depth") is not True:
        raise ValueError("state_ref_fastpath_baseline_meta: promotion requires meets_recommended_sample_depth=true")
    if not isinstance(payload.get("file_io_b64_active_all_workers"), bool):
        raise ValueError("state_ref_fastpath_baseline_meta: file_io_b64_active_all_workers must be bool")


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("root payload must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ATP artifact JSON schemas.")
    parser.add_argument("paths", nargs="+", help="Artifact JSON paths to validate.")
    args = parser.parse_args()

    for raw_path in args.paths:
        path = Path(raw_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        payload = _load_json(path)
        mode = payload.get("mode")
        if ("candidate_path" in payload) and ("baseline_path" in payload):
            validate_state_ref_baseline_meta(payload)
        elif mode == "state_ref_fastpath_bench":
            validate_state_ref_fastpath_summary(payload)
        else:
            raise ValueError(f"{path}: unsupported artifact mode {mode!r}")
        print(f"artifact_ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
