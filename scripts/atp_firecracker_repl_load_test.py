#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.sandbox_firecracker import FirecrackerConfig, FirecrackerVMManager
from scripts.atp_snapshot_pool_locks import SnapshotPoolLockSet
from scripts.snapshot_dirs import split_snapshot_dirs


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
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
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


def _resolve_snapshot_paths() -> dict:
    snap = os.environ.get("FIRECRACKER_SNAPSHOT")
    if not snap:
        raise RuntimeError("FIRECRACKER_SNAPSHOT is required")
    snap_path = Path(snap)
    mem = os.environ.get("FIRECRACKER_SNAPSHOT_MEM") or str(snap_path.with_suffix(".mem"))
    vsock = os.environ.get("FIRECRACKER_SNAPSHOT_VSOCK") or str(snap_path.parent / "vsock.sock")
    rootfs = os.environ.get("FIRECRACKER_ROOTFS")
    if not rootfs:
        candidate = snap_path.parent / "rootfs.ext4"
        if candidate.exists():
            rootfs = str(candidate)
    return {
        "snapshot": str(snap_path),
        "mem": mem,
        "vsock": vsock,
        "rootfs": rootfs,
    }


def _parse_snapshot_dirs() -> List[Path]:
    raw = (os.environ.get("FIRECRACKER_SNAPSHOT_DIRS") or "").strip()
    if not raw:
        return []
    return [Path(part) for part in split_snapshot_dirs(raw)]


def _resolve_snapshot_paths_from_dir(snapshot_dir: Path) -> dict:
    return {
        "snapshot": str(snapshot_dir / "lean.snap"),
        "mem": str(snapshot_dir / "lean.mem"),
        "vsock": str(snapshot_dir / "vsock.sock"),
        "rootfs": str(snapshot_dir / "rootfs.ext4"),
    }


def _validate_snapshot_dir(snapshot_dir: Path) -> None:
    required = ("lean.snap", "lean.mem", "rootfs.ext4")
    missing = [name for name in required if not (snapshot_dir / name).exists()]
    if missing:
        raise RuntimeError(f"Snapshot dir missing files: dir={snapshot_dir} missing={missing}")
    vsock_path = snapshot_dir / "vsock.sock"
    if vsock_path.exists() and (not vsock_path.is_socket()):
        raise RuntimeError(f"Snapshot dir has invalid vsock path (must be socket if present): {vsock_path}")


def _run_worker(
    worker_id: int,
    config: FirecrackerConfig,
    iters: int,
    repl_cmd: str,
    warmup_cmd: str,
    timeout: float,
    do_warmup: bool,
) -> Tuple[List[float], float | None]:
    manager = FirecrackerVMManager(config, workspace=f"/tmp/atp_load_worker_{worker_id}")
    manager.create_vm()
    if not manager.start_vm(use_snapshot=True):
        manager.stop_vm()
        raise RuntimeError("Snapshot restore failed")

    warmup_ms = None
    if do_warmup:
        warmup_result = manager.execute_command(warmup_cmd, timeout=timeout)
        warmup_ms = warmup_result.get("repl_ms") if isinstance(warmup_result, dict) else None

    samples: List[float] = []
    for _ in range(max(1, iters)):
        result = manager.execute_command(repl_cmd, timeout=timeout)
        if isinstance(result, dict) and isinstance(result.get("repl_ms"), (int, float)):
            samples.append(float(result["repl_ms"]))
        else:
            start = time.time()
            _ = result
            samples.append((time.time() - start) * 1000.0)

    manager.stop_vm()
    return samples, warmup_ms


def main() -> int:
    vsock_port = _env_int("FIRECRACKER_REPL_VSOCK_PORT", _env_int("FIRECRACKER_VSOCK_PORT", 52))
    iters = _env_int("ITERS", 20)
    concurrency = max(1, _env_int("CONCURRENCY", 1))
    timeout = _env_float("REPL_TIMEOUT", 30.0)
    repl_cmd = os.environ.get("REPL_CMD", "#check CategoryTheory.Category")
    warmup_cmd = os.environ.get("WARMUP_CMD", "#check Nat")
    do_warmup = os.environ.get("WARMUP", "1").lower() in {"1", "true", "yes"}
    budget_ms = _env_float("BUDGET_P95_MS", 200.0)

    fc_bin = os.environ.get("FIRECRACKER_BIN") or shutil.which("firecracker") or FirecrackerConfig().firecracker_bin
    snapshot_dirs = _parse_snapshot_dirs()
    lock_enabled = os.environ.get("ATP_SNAPSHOT_POOL_LOCK", "1").lower() in {"1", "true", "yes"}
    lock_name = os.environ.get("ATP_SNAPSHOT_POOL_LOCK_NAME", ".bb_snapshot_pool.lock")
    if snapshot_dirs:
        for d in snapshot_dirs:
            _validate_snapshot_dir(d)
        paths = _resolve_snapshot_paths_from_dir(snapshot_dirs[0])
    else:
        paths = _resolve_snapshot_paths()
    base_config = FirecrackerConfig(
        firecracker_bin=fc_bin,
        snapshot_path=paths["snapshot"],
        snapshot_mem_path=paths["mem"],
        snapshot_vsock_path=paths["vsock"],
        rootfs_path=paths.get("rootfs") or FirecrackerConfig().rootfs_path,
        exec_mode="repl",
        allow_placeholder=False,
        vsock_port=vsock_port,
        repl_warmup_cmd=None,
    )

    configs: List[FirecrackerConfig]
    if concurrency > 1:
        if not snapshot_dirs:
            # In practice Firecracker snapshot restores are path-sensitive (including the host-side vsock UDS path),
            # so a single snapshot directory cannot be used to restore multiple VMs concurrently unless you maintain
            # separate snapshot directories (one per worker) created with distinct `SNAP_DIR`s.
            raise RuntimeError(
                "CONCURRENCY>1 with a single snapshot dir is not supported. "
                "Set FIRECRACKER_SNAPSHOT_DIRS=/snapA,/snapB,... (one dir per worker), "
                "or run with CONCURRENCY=1."
            )
        if len(snapshot_dirs) < concurrency:
            raise RuntimeError(
                f"FIRECRACKER_SNAPSHOT_DIRS has {len(snapshot_dirs)} dirs but CONCURRENCY={concurrency}; "
                "provide at least one snapshot dir per worker."
            )
        configs = []
        for idx in range(concurrency):
            p = _resolve_snapshot_paths_from_dir(snapshot_dirs[idx])
            configs.append(
                FirecrackerConfig(
                    firecracker_bin=fc_bin,
                    snapshot_path=p["snapshot"],
                    snapshot_mem_path=p["mem"],
                    snapshot_vsock_path=p["vsock"],
                    rootfs_path=p.get("rootfs") or FirecrackerConfig().rootfs_path,
                    exec_mode="repl",
                    allow_placeholder=False,
                    vsock_port=vsock_port,
                    repl_warmup_cmd=None,
                )
            )
    else:
        configs = [base_config]

    iters_per_worker = max(1, (iters + concurrency - 1) // concurrency)
    start_ts = time.time()
    samples: List[float] = []
    warmups: List[float] = []

    lock_set = None
    try:
        if snapshot_dirs and lock_enabled:
            lock_set = SnapshotPoolLockSet(snapshot_dirs[:concurrency], lock_name=lock_name)
            lock_set.acquire()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = []
            for idx in range(concurrency):
                futures.append(
                    executor.submit(
                        _run_worker,
                        idx,
                        configs[idx],
                        iters_per_worker,
                        repl_cmd,
                        warmup_cmd,
                        timeout,
                        do_warmup,
                    )
                )
            for future in futures:
                worker_samples, warmup_ms = future.result()
                samples.extend(worker_samples)
                if isinstance(warmup_ms, (int, float)):
                    warmups.append(float(warmup_ms))
    finally:
        if lock_set is not None:
            lock_set.release()

    total_s = max(time.time() - start_ts, 0.001)
    summary = {
        "concurrency": concurrency,
        "iters": len(samples),
        "iters_per_worker": iters_per_worker,
        "warmup_ms": statistics.mean(warmups) if warmups else None,
        "min_ms": min(samples) if samples else None,
        "max_ms": max(samples) if samples else None,
        "avg_ms": statistics.mean(samples) if samples else None,
        "p50_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
        "throughput_rps": len(samples) / total_s if total_s else 0.0,
        "duration_s": total_s,
        "budget_p95_ms": budget_ms,
        "budget_pass": _percentile(samples, 95) <= budget_ms if samples else False,
    }

    print(json.dumps(summary, indent=2))
    summary_path = os.environ.get("ATP_REPL_LOAD_SUMMARY_PATH") or str(
        REPO_ROOT / "artifacts" / "atp_firecracker_repl_load.json"
    )
    try:
        path = Path(summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"load_summary_path: {path}")
    except Exception as exc:
        print(f"load_summary_write_error: {exc}")
    if summary["budget_pass"] is False:
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"load_error: {exc}")
        sys.exit(1)
