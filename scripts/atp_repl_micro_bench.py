#!/usr/bin/env python3
from __future__ import annotations

import argparse
import statistics
import time
from typing import List

from breadboard.lean_repl import FirecrackerReplClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quick Firecracker REPL micro-benchmark.")
    parser.add_argument("--iters", type=int, default=10, help="Number of REPL requests.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout (s).")
    parser.add_argument("--command", type=str, default="#check Nat", help="Lean command to execute.")
    return parser.parse_args()


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[int(k)]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


def main() -> None:
    args = _parse_args()
    client = FirecrackerReplClient(
        snapshot_path=_env_required("FIRECRACKER_SNAPSHOT"),
        snapshot_mem_path=_env_required("FIRECRACKER_SNAPSHOT_MEM"),
        snapshot_vsock_path=_env_required("FIRECRACKER_SNAPSHOT_VSOCK"),
        rootfs_path=_env_optional("FIRECRACKER_ROOTFS"),
        firecracker_bin=_env_optional("FIRECRACKER_BIN"),
        vsock_port=int(_env_optional("FIRECRACKER_REPL_VSOCK_PORT") or _env_optional("FIRECRACKER_VSOCK_PORT") or "52"),
    )

    samples: List[float] = []
    with client:
        for _ in range(max(1, int(args.iters))):
            start = time.time()
            result = client.send(args.command, timeout=args.timeout)
            elapsed_ms = result.metrics.repl_ms if result.metrics.repl_ms is not None else (time.time() - start) * 1000.0
            samples.append(elapsed_ms)

    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    print(f"[micro-bench] iters={len(samples)} p50_ms={p50:.2f} p95_ms={p95:.2f} avg_ms={statistics.mean(samples):.2f}")


def _env_required(name: str) -> str:
    import os

    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"Missing required env: {name}")
    return value


def _env_optional(name: str) -> str | None:
    import os

    value = (os.environ.get(name) or "").strip()
    return value or None


if __name__ == "__main__":
    main()
