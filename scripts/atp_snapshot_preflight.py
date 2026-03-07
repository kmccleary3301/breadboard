#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.snapshot_dirs import split_snapshot_dirs


REQUIRED_FILES = ("lean.snap", "lean.mem", "rootfs.ext4", "vsock.sock")
DEFAULT_LOCK_NAME = ".bb_snapshot_pool.lock"


def _snapshot_dirs_from_env() -> List[Path]:
    raw = (os.environ.get("FIRECRACKER_SNAPSHOT_DIRS") or "").strip()
    if raw:
        return [Path(part.strip()) for part in split_snapshot_dirs(raw)]
    snap = (os.environ.get("FIRECRACKER_SNAPSHOT") or "").strip()
    if snap:
        return [Path(snap).resolve().parent]
    return []


def _socket_active(path: Path) -> bool:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        sock.connect(str(path))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def _validate_dir(path: Path, *, strict_hygiene: bool = False, lock_name: str = DEFAULT_LOCK_NAME) -> Dict:
    checks = []
    ok = True
    hygiene = {
        "strict_hygiene": bool(strict_hygiene),
        "lock_file": str(path / lock_name),
        "lock_present": False,
        "vsock_active": False,
        "warnings": [],
    }
    for name in REQUIRED_FILES:
        file_path = path / name
        exists = file_path.exists()
        readable = os.access(file_path, os.R_OK) if exists else False
        size = file_path.stat().st_size if exists and file_path.is_file() else None
        is_socket = file_path.is_socket() if exists else False
        parent_exists = file_path.parent.exists()
        parent_writable = os.access(file_path.parent, os.W_OK) if parent_exists else False

        entry = {
            "name": name,
            "path": str(file_path),
            "exists": exists,
            "readable": readable,
            "size_bytes": size,
        }
        if name == "vsock.sock":
            entry["is_socket"] = is_socket
            entry["parent_exists"] = parent_exists
            entry["parent_writable"] = parent_writable

        if name != "vsock.sock":
            if not exists or not readable:
                ok = False
            if size is None or size <= 0:
                ok = False
        else:
            # vsock path may be absent at rest; if present it must be a socket.
            if exists and (not readable or not is_socket):
                ok = False
            if exists and is_socket:
                active = _socket_active(file_path)
                hygiene["vsock_active"] = bool(active)
                if active:
                    hygiene["warnings"].append("vsock socket is active")
                    if strict_hygiene:
                        ok = False
            if (not exists) and (not parent_exists or not parent_writable):
                ok = False
        checks.append(entry)

    lock_path = path / lock_name
    lock_present = lock_path.exists()
    hygiene["lock_present"] = bool(lock_present)
    if lock_present:
        hygiene["warnings"].append("snapshot lock file present")
        if strict_hygiene:
            ok = False
    return {"snapshot_dir": str(path), "ok": ok, "checks": checks, "hygiene": hygiene}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ATP snapshot directories before running benchmarks/tests.")
    parser.add_argument(
        "--snapshot-dir",
        action="append",
        default=[],
        help="Explicit snapshot directory (repeatable). If omitted, env vars are used.",
    )
    parser.add_argument("--strict-hygiene", action="store_true", help="Fail on active vsock or lock file presence.")
    parser.add_argument("--lock-name", default=DEFAULT_LOCK_NAME, help="Lock file name for snapshot hygiene checks.")
    parser.add_argument("--json", action="store_true", help="Print JSON output only.")
    args = parser.parse_args()

    dirs = [Path(raw).resolve() for raw in args.snapshot_dir] if args.snapshot_dir else _snapshot_dirs_from_env()
    if not dirs:
        raise RuntimeError("No snapshot directories provided (use --snapshot-dir or FIRECRACKER_SNAPSHOT_DIRS/FIRECRACKER_SNAPSHOT).")

    strict_hygiene = bool(args.strict_hygiene) or (os.environ.get("ATP_SNAPSHOT_HYGIENE_STRICT", "").strip() in {"1", "true", "True"})
    results = [_validate_dir(path, strict_hygiene=strict_hygiene, lock_name=str(args.lock_name)) for path in dirs]
    overall_ok = all(item["ok"] for item in results)
    payload = {"ok": overall_ok, "snapshot_count": len(results), "results": results}

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"snapshot_preflight_ok: {overall_ok}")
        print(json.dumps(payload, indent=2))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
