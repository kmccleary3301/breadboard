#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.lean_repl import FirecrackerReplClient


def _env_required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"Missing required env: {name}")
    return value


def main() -> int:
    snap = _env_required("FIRECRACKER_SNAPSHOT")
    mem = _env_required("FIRECRACKER_SNAPSHOT_MEM")
    vsock = _env_required("FIRECRACKER_SNAPSHOT_VSOCK")
    rootfs = _env_required("FIRECRACKER_ROOTFS")

    pickle_path = os.environ.get("ATP_PROBE_PICKLE_PATH", "/tmp/bb_probe_mathlib_env.olean")

    with FirecrackerReplClient(
        snapshot_path=snap,
        snapshot_mem_path=mem,
        snapshot_vsock_path=vsock,
        rootfs_path=rootfs,
    ) as client:
        # Ask the REPL for a known Mathlib symbol so we get an env id.
        probe = client.send("#check CategoryTheory.Category", timeout=120.0)
        env_id = probe.response.env
        if env_id is None:
            raise SystemExit("Probe returned no env id")

        # Pickle the current env to a guest path.
        client.send_payload({"pickleTo": pickle_path, "env": int(env_id)}, timeout=600.0)

        # Measure size from inside guest.
        stat = client.exec_shell(f"python3 -c \"import os; print(os.path.getsize('{pickle_path}'))\"")
        if int(stat.get("exit", 1)) != 0:
            raise SystemExit(f"stat failed: {stat}")
        size = int((stat.get('stdout') or '').strip() or "0")
        print(f"env_id={env_id} pickle_path={pickle_path} size_bytes={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

