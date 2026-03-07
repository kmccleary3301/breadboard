#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.sandbox_firecracker import FirecrackerConfig, FirecrackerVMManager
from scripts.snapshot_dirs import split_snapshot_dirs


def _parse_snapshot_dirs(args: argparse.Namespace) -> List[Path]:
    if args.snapshot_dir:
        return [Path(item).resolve() for item in args.snapshot_dir]
    raw = (args.snapshot_dirs or os.environ.get("FIRECRACKER_SNAPSHOT_DIRS") or "").strip()
    if not raw:
        raise RuntimeError("No snapshot dirs provided (use --snapshot-dir or --snapshot-dirs/FIRECRACKER_SNAPSHOT_DIRS).")
    return [Path(part.strip()).resolve() for part in split_snapshot_dirs(raw)]


def _validate_files(snapshot_dir: Path) -> tuple[bool, str]:
    required = ("lean.snap", "lean.mem", "rootfs.ext4")
    missing = [name for name in required if not (snapshot_dir / name).exists()]
    if missing:
        return False, f"missing required files: {missing}"
    vsock_path = snapshot_dir / "vsock.sock"
    if vsock_path.exists() and (not vsock_path.is_socket()):
        return False, "vsock.sock exists but is not a socket"
    return True, ""


def _restore_probe(snapshot_dir: Path, *, index: int, timeout_s: float) -> tuple[bool, str]:
    fc_bin = os.environ.get("FIRECRACKER_BIN") or shutil.which("firecracker") or FirecrackerConfig().firecracker_bin
    vsock_port = int(os.environ.get("FIRECRACKER_REPL_VSOCK_PORT") or os.environ.get("FIRECRACKER_VSOCK_PORT", "52"))
    cfg = FirecrackerConfig(
        firecracker_bin=fc_bin,
        snapshot_path=str(snapshot_dir / "lean.snap"),
        snapshot_mem_path=str(snapshot_dir / "lean.mem"),
        snapshot_vsock_path=str(snapshot_dir / "vsock.sock"),
        rootfs_path=str(snapshot_dir / "rootfs.ext4"),
        exec_mode="repl",
        allow_placeholder=False,
        vsock_port=vsock_port,
    )
    manager = FirecrackerVMManager(cfg, workspace=f"/tmp/atp_snapshot_probe_{index}")
    manager.create_vm()
    try:
        if not manager.start_vm(use_snapshot=True):
            return False, "snapshot restore failed"
        _ = manager.execute_command("#check Nat", timeout=timeout_s)
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            manager.stop_vm()
        except Exception:
            pass


def evaluate_snapshot_dir(snapshot_dir: Path, *, probe_restore: bool, index: int, timeout_s: float) -> Dict[str, Any]:
    files_ok, files_reason = _validate_files(snapshot_dir)
    if not files_ok:
        return {
            "snapshot_dir": str(snapshot_dir),
            "ok": False,
            "files_ok": False,
            "restore_probe_ok": None,
            "reason": files_reason,
        }

    if not probe_restore:
        return {
            "snapshot_dir": str(snapshot_dir),
            "ok": True,
            "files_ok": True,
            "restore_probe_ok": None,
            "reason": "",
        }

    restore_ok, restore_reason = _restore_probe(snapshot_dir, index=index, timeout_s=timeout_s)
    return {
        "snapshot_dir": str(snapshot_dir),
        "ok": bool(restore_ok),
        "files_ok": True,
        "restore_probe_ok": bool(restore_ok),
        "reason": restore_reason if not restore_ok else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and optionally probe ATP snapshot pools.")
    parser.add_argument("--snapshot-dir", action="append", default=[], help="Snapshot directory (repeatable).")
    parser.add_argument("--snapshot-dirs", default="", help="Comma-separated snapshot directories.")
    parser.add_argument("--probe-restore", action="store_true", help="Start/stop VM probe for each dir.")
    parser.add_argument("--probe-timeout-s", type=float, default=30.0, help="REPL probe timeout for --probe-restore.")
    parser.add_argument("--min-healthy", type=int, default=1, help="Minimum healthy dirs required for success.")
    parser.add_argument("--print-export", action="store_true", help="Print export line for healthy dirs.")
    parser.add_argument("--json", action="store_true", help="Emit JSON payload.")
    args = parser.parse_args()

    snapshot_dirs = _parse_snapshot_dirs(args)
    results = [
        evaluate_snapshot_dir(
            snapshot_dir=path,
            probe_restore=bool(args.probe_restore),
            index=idx,
            timeout_s=max(1.0, float(args.probe_timeout_s)),
        )
        for idx, path in enumerate(snapshot_dirs)
    ]
    healthy_dirs = [item["snapshot_dir"] for item in results if item.get("ok")]
    unhealthy_dirs = [item for item in results if not item.get("ok")]
    payload: Dict[str, Any] = {
        "snapshot_count": len(snapshot_dirs),
        "healthy_count": len(healthy_dirs),
        "unhealthy_count": len(unhealthy_dirs),
        "probe_restore": bool(args.probe_restore),
        "healthy_dirs": healthy_dirs,
        "results": results,
    }
    ok = len(healthy_dirs) >= max(0, int(args.min_healthy))
    payload["ok"] = ok

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"snapshot_pool_ok: {ok}")
        print(json.dumps(payload, indent=2))

    if args.print_export and healthy_dirs:
        joined = ",".join(healthy_dirs)
        print(f"export FIRECRACKER_SNAPSHOT_DIRS='{joined}'")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
