#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.sandbox_firecracker import FirecrackerConfig, FirecrackerVMManager


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


def main() -> int:
    paths = _resolve_snapshot_paths()
    vsock_port = _env_int("FIRECRACKER_REPL_VSOCK_PORT", _env_int("FIRECRACKER_VSOCK_PORT", 52))
    iters = _env_int("ITERS", 10)
    budget_ms = _env_float("BUDGET_P95_MS", 200.0)
    timeout = _env_float("REPL_TIMEOUT", 30.0)
    repl_cmd = os.environ.get("REPL_CMD", "#check CategoryTheory.Category")
    warmup_cmd = os.environ.get("WARMUP_CMD", "#check Nat")
    do_warmup = os.environ.get("WARMUP", "1").lower() in {"1", "true", "yes"}

    fc_bin = os.environ.get("FIRECRACKER_BIN") or shutil.which("firecracker") or FirecrackerConfig().firecracker_bin
    config = FirecrackerConfig(
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
    manager = FirecrackerVMManager(config, workspace="/tmp")
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

    summary = {
        "iters": len(samples),
        "warmup_ms": warmup_ms,
        "min_ms": min(samples) if samples else None,
        "max_ms": max(samples) if samples else None,
        "avg_ms": statistics.mean(samples) if samples else None,
        "p50_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
        "budget_p95_ms": budget_ms,
        "budget_pass": _percentile(samples, 95) <= budget_ms if samples else False,
    }

    print(json.dumps(summary, indent=2))
    summary_path = os.environ.get("ATP_REPL_BENCH_SUMMARY_PATH") or os.environ.get("BENCH_SUMMARY_PATH")
    if not summary_path:
        summary_path = str(REPO_ROOT / "artifacts" / "atp_firecracker_repl_bench.json")
    try:
        path = Path(summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"bench_summary_path: {path}")
    except Exception as exc:
        print(f"bench_summary_write_error: {exc}")
    if summary["budget_pass"] is False:
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"bench_error: {exc}")
        sys.exit(1)
