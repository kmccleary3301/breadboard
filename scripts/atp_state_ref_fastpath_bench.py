#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.lean_repl import CANONICAL_MATHLIB_HEADER_HASH, CheckLimits, CheckMode, CheckRequest
from breadboard.lean_repl.firecracker_service import FirecrackerLeanReplService, FirecrackerReplServiceConfig
from scripts.atp_snapshot_pool_locks import SnapshotPoolLockSet
from scripts.snapshot_dirs import split_snapshot_dirs


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] * (c - k) + values[c] * (k - f)


def _snapshot_dirs_from_env() -> List[Path]:
    raw = (os.environ.get("FIRECRACKER_SNAPSHOT_DIRS") or "").strip()
    if raw:
        return [Path(p.strip()) for p in split_snapshot_dirs(raw)]
    snap = (os.environ.get("FIRECRACKER_SNAPSHOT") or "").strip()
    if snap:
        return [Path(snap).parent]
    raise RuntimeError("Set FIRECRACKER_SNAPSHOT_DIRS or FIRECRACKER_SNAPSHOT")


def _require_snapshot_dir(snapshot_dir: Path) -> None:
    required = ("lean.snap", "lean.mem", "rootfs.ext4")
    missing = [name for name in required if not (snapshot_dir / name).exists()]
    if missing:
        raise RuntimeError(f"Missing snapshot files in {snapshot_dir}: {missing}")
    vsock_path = snapshot_dir / "vsock.sock"
    if vsock_path.exists() and (not vsock_path.is_socket()):
        raise RuntimeError(f"Invalid vsock path in {snapshot_dir}: {vsock_path} (expected socket if present)")


def _worker(
    worker_id: int,
    snapshot_dir: Path,
    *,
    iters: int,
    burn_in_iters: int,
    timeout_s: float,
) -> Dict[str, Any]:
    _require_snapshot_dir(snapshot_dir)
    config = FirecrackerReplServiceConfig(
        snapshot_path=str(snapshot_dir / "lean.snap"),
        snapshot_mem_path=str(snapshot_dir / "lean.mem"),
        snapshot_vsock_path=str(snapshot_dir / "vsock.sock"),
        rootfs_path=str(snapshot_dir / "rootfs.ext4"),
        workspace=f"/tmp/atp_state_ref_fastpath_worker_{worker_id}",
        vsock_port=int(os.environ.get("FIRECRACKER_REPL_VSOCK_PORT") or os.environ.get("FIRECRACKER_VSOCK_PORT", "52")),
        firecracker_bin=os.environ.get("FIRECRACKER_BIN") or "/usr/local/bin/firecracker",
    )
    service = FirecrackerLeanReplService(config)
    symbol = f"bbFastSym{worker_id}"
    request_wall_s: List[float] = []
    request_repl_ms: List[float] = []
    unpickle_s: List[float] = []
    try:
        bootstrap = CheckRequest(
            header_hash=CANONICAL_MATHLIB_HEADER_HASH,
            commands=[f"def {symbol} := {worker_id + 1}"],
            mode=CheckMode.COMMAND,
            limits=CheckLimits(timeout_s=timeout_s),
            want=frozenset({"errors", "messages", "state"}),
        )
        bootstrap_result, _ = service.submit_request_with_metrics(bootstrap)
        if not bootstrap_result.success or not bootstrap_result.new_state_ref:
            raise RuntimeError(f"Bootstrap failed for worker={worker_id}: {bootstrap_result.messages}")
        state_ref = bootstrap_result.new_state_ref

        measured_iters = max(1, int(iters))
        burn_in = max(0, int(burn_in_iters))
        total_iters = measured_iters + burn_in

        for iteration in range(total_iters):
            req = CheckRequest(
                header_hash=CANONICAL_MATHLIB_HEADER_HASH,
                state_ref=state_ref,
                commands=[f"#check {symbol}"],
                mode=CheckMode.COMMAND,
                limits=CheckLimits(timeout_s=timeout_s),
                want=frozenset({"errors", "messages"}),
            )
            start = time.time()
            result, metrics = service.submit_request_with_metrics(req)
            if not result.success:
                raise RuntimeError(f"State-ref request failed for worker={worker_id}: {result.messages}")
            if iteration >= burn_in:
                request_wall_s.append(time.time() - start)
                if result.resource_usage and isinstance(result.resource_usage.unpickle_time_s, (int, float)):
                    unpickle_s.append(float(result.resource_usage.unpickle_time_s))
                for metric in metrics:
                    if isinstance(metric.repl_ms, (int, float)):
                        request_repl_ms.append(float(metric.repl_ms))

        caps = service.runtime_capabilities(start_if_needed=False)
        return {
            "worker_id": worker_id,
            "snapshot_dir": str(snapshot_dir),
            "iters": measured_iters,
            "burn_in_iters": burn_in,
            "total_iters": total_iters,
            "state_ref": state_ref,
            "file_io_b64_active": bool(caps.get("file_io_b64_active")),
            "guest_vsock_capabilities": caps.get("guest_vsock") or {},
            "repl_ms_avg": statistics.mean(request_repl_ms) if request_repl_ms else None,
            "unpickle_s_avg": statistics.mean(unpickle_s) if unpickle_s else None,
            "wall_s_avg": statistics.mean(request_wall_s) if request_wall_s else None,
            "wall_s_p95": _percentile(request_wall_s, 95) if request_wall_s else None,
        }
    finally:
        service.close()


def main() -> int:
    snapshot_dirs = _snapshot_dirs_from_env()
    concurrency = max(1, _env_int("CONCURRENCY", len(snapshot_dirs)))
    lock_enabled = (os.environ.get("ATP_SNAPSHOT_POOL_LOCK", "1") or "").lower() in {"1", "true", "yes"}
    lock_name = os.environ.get("ATP_SNAPSHOT_POOL_LOCK_NAME", ".bb_snapshot_pool.lock")
    if len(snapshot_dirs) < concurrency:
        raise RuntimeError(
            f"Need at least {concurrency} snapshot dirs, got {len(snapshot_dirs)}. "
            "Set FIRECRACKER_SNAPSHOT_DIRS with one dir per worker."
        )
    iters = max(1, _env_int("ITERS", 8))
    burn_in_iters = max(0, _env_int("BURN_IN_ITERS", 0))
    timeout_s = max(1.0, _env_float("REPL_TIMEOUT", 120.0))
    min_recommended_iters = max(1, _env_int("MIN_RECOMMENDED_ITERS", 10))

    start = time.time()
    lock_set = None
    try:
        if lock_enabled:
            lock_set = SnapshotPoolLockSet(snapshot_dirs[:concurrency], lock_name=lock_name)
            lock_set.acquire()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _worker,
                    idx,
                    snapshot_dirs[idx],
                    iters=iters,
                    burn_in_iters=burn_in_iters,
                    timeout_s=timeout_s,
                )
                for idx in range(concurrency)
            ]
            rows = [future.result() for future in futures]
    finally:
        if lock_set is not None:
            lock_set.release()

    elapsed = time.time() - start
    repl_ms_vals = [float(x["repl_ms_avg"]) for x in rows if isinstance(x.get("repl_ms_avg"), (int, float))]
    unpickle_vals = [float(x["unpickle_s_avg"]) for x in rows if isinstance(x.get("unpickle_s_avg"), (int, float))]
    wall_vals = [float(x["wall_s_avg"]) for x in rows if isinstance(x.get("wall_s_avg"), (int, float))]
    fileio_flags = [bool(x.get("file_io_b64_active")) for x in rows]
    summary = {
        "mode": "state_ref_fastpath_bench",
        "concurrency": concurrency,
        "iters_per_worker": iters,
        "burn_in_iters_per_worker": burn_in_iters,
        "total_iters_per_worker": iters + burn_in_iters,
        "min_recommended_iters": min_recommended_iters,
        "meets_recommended_sample_depth": bool(iters >= min_recommended_iters),
        "duration_s_total": elapsed,
        "workers": rows,
        "file_io_b64_active_all_workers": all(fileio_flags) if fileio_flags else False,
        "file_io_b64_active_any_worker": any(fileio_flags) if fileio_flags else False,
        "repl_ms_avg_across_workers": statistics.mean(repl_ms_vals) if repl_ms_vals else None,
        "unpickle_s_avg_across_workers": statistics.mean(unpickle_vals) if unpickle_vals else None,
        "request_wall_s_avg_across_workers": statistics.mean(wall_vals) if wall_vals else None,
        "request_wall_s_p95_across_workers": _percentile(wall_vals, 95) if wall_vals else None,
    }
    print(json.dumps(summary, indent=2))
    out_path = os.environ.get("ATP_STATE_REF_BENCH_SUMMARY_PATH") or str(
        REPO_ROOT / "artifacts" / "atp_state_ref_fastpath_bench.json"
    )
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"state_ref_bench_summary_path: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"state_ref_bench_error: {exc}")
        raise

