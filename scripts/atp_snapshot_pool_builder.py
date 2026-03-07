#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = ("lean.snap", "lean.mem", "rootfs.ext4")
DEFAULT_BUILD_SCRIPT = str(REPO_ROOT.parent / "fc_create_lean_snapshot_vsock.sh")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_stale_vsock(vsock_path: Path) -> Dict[str, Any]:
    if not vsock_path.exists():
        return {"path": str(vsock_path), "exists": False, "removed": False, "active": False}
    if not vsock_path.is_socket():
        raise RuntimeError(f"Invalid vsock path (must be socket if present): {vsock_path}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    active = False
    try:
        sock.connect(str(vsock_path))
        active = True
    except Exception:
        active = False
    finally:
        sock.close()
    if active:
        raise RuntimeError(f"vsock appears active; refusing to overwrite worker dir: {vsock_path}")
    vsock_path.unlink(missing_ok=True)
    return {"path": str(vsock_path), "exists": True, "removed": True, "active": False}


def _check_disk_budget(
    *,
    pool_root: Path,
    workers: int,
    estimated_gb_per_worker: float,
    reserve_gb: float,
) -> Dict[str, Any]:
    usage = shutil.disk_usage(pool_root)
    free_gb = float(usage.free) / (1024.0**3)
    required_gb = max(0.0, float(workers) * float(estimated_gb_per_worker) + max(0.0, float(reserve_gb)))
    ok = free_gb >= required_gb
    payload = {
        "free_gb": free_gb,
        "required_gb": required_gb,
        "workers": workers,
        "estimated_gb_per_worker": float(estimated_gb_per_worker),
        "reserve_gb": float(reserve_gb),
        "ok": ok,
    }
    if not ok:
        raise RuntimeError(
            f"Insufficient disk budget for snapshot pool build: free_gb={free_gb:.2f} required_gb={required_gb:.2f}"
        )
    return payload


def _worker_dir(generation_dir: Path, worker_id: int) -> Path:
    return generation_dir / f"w{worker_id:02d}"


def _copy_from_source(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_FILES:
        src = source_dir / filename
        if not src.exists():
            raise RuntimeError(f"Source snapshot missing required file: {src}")
        shutil.copy2(src, target_dir / filename)
    optional = ("serial.log", "host_debug.log", "data.ext4")
    for filename in optional:
        src = source_dir / filename
        if src.exists():
            shutil.copy2(src, target_dir / filename)


def _run_builder_script(build_script: Path, target_dir: Path, *, sudo_mode: str) -> None:
    env = os.environ.copy()
    env["SNAP_DIR"] = str(target_dir)
    cmd: List[str] = [str(build_script)]
    if sudo_mode == "always":
        cmd = ["sudo"] + cmd
    elif sudo_mode == "auto" and os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=True, cwd=str(REPO_ROOT.parent), env=env)


def _snapshot_checksums(snapshot_dir: Path) -> Dict[str, str]:
    checksums: Dict[str, str] = {}
    for filename in REQUIRED_FILES:
        path = snapshot_dir / filename
        if not path.exists():
            raise RuntimeError(f"Snapshot missing required file: {path}")
        checksums[filename] = _sha256(path)
    return checksums


def _write_checksums_file(rows: List[Dict[str, Any]], output_path: Path) -> None:
    lines: List[str] = []
    for row in rows:
        for filename, digest in sorted((row.get("checksums") or {}).items()):
            rel = f"{Path(row['worker_dir']).name}/{filename}"
            lines.append(f"{digest}  {rel}")
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _build_generation(
    *,
    pool_root: Path,
    generation: str,
    workers: int,
    source_dir: Path | None,
    build_script: Path | None,
    sudo_mode: str,
    keep_generations: int,
    estimated_gb_per_worker: float,
    reserve_gb: float,
    clean_stale_vsock: bool,
) -> Dict[str, Any]:
    pool_root.mkdir(parents=True, exist_ok=True)
    budget = _check_disk_budget(
        pool_root=pool_root,
        workers=workers,
        estimated_gb_per_worker=estimated_gb_per_worker,
        reserve_gb=reserve_gb,
    )
    generation_dir = pool_root / generation
    if generation_dir.exists():
        raise RuntimeError(f"Generation already exists: {generation_dir}")
    generation_dir.mkdir(parents=True, exist_ok=False)

    rows: List[Dict[str, Any]] = []
    for worker_id in range(max(1, int(workers))):
        worker_dir = _worker_dir(generation_dir, worker_id)
        worker_dir.mkdir(parents=True, exist_ok=True)
        stale_cleanup = None
        if clean_stale_vsock:
            stale_cleanup = _cleanup_stale_vsock(worker_dir / "vsock.sock")
        if source_dir is not None:
            _copy_from_source(source_dir, worker_dir)
        elif build_script is not None:
            _run_builder_script(build_script, worker_dir, sudo_mode=sudo_mode)
        else:
            raise RuntimeError("Either --source-dir or --build-script is required")
        checksums = _snapshot_checksums(worker_dir)
        rows.append(
            {
                "worker_id": worker_id,
                "worker_dir": str(worker_dir.resolve()),
                "checksums": checksums,
                "stale_vsock_cleanup": stale_cleanup,
            }
        )

    manifest = {
        "schema": "breadboard.atp.snapshot_pool_manifest.v1",
        "generated_at": time.time(),
        "pool_root": str(pool_root.resolve()),
        "generation": generation,
        "generation_dir": str(generation_dir.resolve()),
        "workers": rows,
        "disk_budget": budget,
    }
    manifest_path = generation_dir / "pool_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_checksums_file(rows, generation_dir / "CHECKSUMS.sha256")

    _rotate_generations(pool_root=pool_root, keep_generations=keep_generations, apply=True)

    selected_dirs = [row["worker_dir"] for row in rows]
    manifest["export_snapshot_dirs"] = ",".join(selected_dirs)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _verify_generation(generation_dir: Path) -> Dict[str, Any]:
    manifest_path = generation_dir / "pool_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid manifest payload: {manifest_path}")
    workers = payload.get("workers")
    if not isinstance(workers, list) or not workers:
        raise RuntimeError("Manifest has no workers")

    rows: List[Dict[str, Any]] = []
    ok = True
    for row in workers:
        worker_dir = Path(str(row.get("worker_dir") or "")).resolve()
        expected = row.get("checksums") or {}
        actual: Dict[str, str] = {}
        missing: List[str] = []
        mismatch: List[str] = []
        for filename in REQUIRED_FILES:
            path = worker_dir / filename
            if not path.exists():
                missing.append(filename)
                continue
            digest = _sha256(path)
            actual[filename] = digest
            exp = expected.get(filename)
            if exp and exp != digest:
                mismatch.append(filename)
        row_ok = (len(missing) == 0) and (len(mismatch) == 0)
        if not row_ok:
            ok = False
        rows.append(
            {
                "worker_dir": str(worker_dir),
                "ok": row_ok,
                "missing": missing,
                "mismatch": mismatch,
                "actual": actual,
            }
        )
    return {
        "generation_dir": str(generation_dir.resolve()),
        "manifest": str(manifest_path.resolve()),
        "ok": ok,
        "workers": rows,
    }


def _rotate_generations(*, pool_root: Path, keep_generations: int, apply: bool) -> Dict[str, Any]:
    keep = max(0, int(keep_generations))
    generations = [path for path in pool_root.glob("gen-*") if path.is_dir()]
    generations.sort(key=lambda item: (item.name, item.stat().st_mtime), reverse=True)
    stale = generations[keep:]
    if apply:
        for path in stale:
            shutil.rmtree(path, ignore_errors=True)
    return {
        "pool_root": str(pool_root.resolve()),
        "keep_generations": keep,
        "found": [str(path.resolve()) for path in generations],
        "stale": [str(path.resolve()) for path in stale],
        "applied": bool(apply),
    }


def _default_generation() -> str:
    return f"gen-{time.strftime('%Y%m%d-%H%M%S')}"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/verify/rotate ATP snapshot pools.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a new snapshot pool generation.")
    build.add_argument("--pool-root", default=str(REPO_ROOT.parent / "lean_snapshot"), help="Pool root directory.")
    build.add_argument("--generation", default=_default_generation(), help="Generation name (default gen-<timestamp>).")
    build.add_argument("--workers", type=int, default=4, help="Worker snapshot count.")
    build.add_argument("--source-dir", default="", help="Copy mode source snapshot directory.")
    build.add_argument("--build-script", default=DEFAULT_BUILD_SCRIPT, help="Snapshot build script path.")
    build.add_argument(
        "--sudo-mode",
        choices=("auto", "always", "never"),
        default="auto",
        help="Whether to run build script via sudo.",
    )
    build.add_argument("--estimated-gb-per-worker", type=float, default=30.0)
    build.add_argument("--reserve-gb", type=float, default=20.0)
    build.add_argument("--keep-generations", type=int, default=3)
    build.add_argument("--no-clean-stale-vsock", action="store_true")
    build.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="Verify checksums for a generation manifest.")
    verify.add_argument("--generation-dir", required=True)
    verify.add_argument("--json", action="store_true")

    rotate = sub.add_parser("rotate", help="Rotate old generations under pool root.")
    rotate.add_argument("--pool-root", default=str(REPO_ROOT.parent / "lean_snapshot"))
    rotate.add_argument("--keep-generations", type=int, default=3)
    rotate.add_argument("--apply", action="store_true")
    rotate.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "build":
        source_dir = Path(args.source_dir).resolve() if str(args.source_dir).strip() else None
        build_script = Path(args.build_script).resolve() if str(args.build_script).strip() else None
        manifest = _build_generation(
            pool_root=Path(args.pool_root).resolve(),
            generation=str(args.generation),
            workers=max(1, int(args.workers)),
            source_dir=source_dir,
            build_script=build_script,
            sudo_mode=str(args.sudo_mode),
            keep_generations=max(0, int(args.keep_generations)),
            estimated_gb_per_worker=float(args.estimated_gb_per_worker),
            reserve_gb=float(args.reserve_gb),
            clean_stale_vsock=not bool(args.no_clean_stale_vsock),
        )
        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            print(f"snapshot_pool_generation: {manifest['generation_dir']}")
            print(f"snapshot_pool_workers: {len(manifest['workers'])}")
            print(f"export FIRECRACKER_SNAPSHOT_DIRS='{manifest['export_snapshot_dirs']}'")
        return 0

    if args.command == "verify":
        result = _verify_generation(Path(args.generation_dir).resolve())
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"snapshot_pool_verify_ok: {result['ok']}")
            print(json.dumps(result, indent=2))
        return 0 if bool(result["ok"]) else 1

    if args.command == "rotate":
        result = _rotate_generations(
            pool_root=Path(args.pool_root).resolve(),
            keep_generations=max(0, int(args.keep_generations)),
            apply=bool(args.apply),
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"snapshot_pool_rotate_applied: {result['applied']}")
            print(json.dumps(result, indent=2))
        return 0

    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
