#!/usr/bin/env python3
"""
Build a single-platform engine bundle + manifest entry.

Usage:
  python scripts/build_engine_bundle.py --version 1.0.0 --engine-bin /path/to/breadboard-engine
  python scripts/build_engine_bundle.py --version 1.0.0 --engine-bin dist/engine/breadboard-engine --base-url https://cdn.example.com/breadboard/engine
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _default_platform() -> str:
    if os.name == "nt":
        return "win32"
    if sys_platform := platform.system().lower():
        if "darwin" in sys_platform or "mac" in sys_platform:
            return "darwin"
        if "linux" in sys_platform:
            return "linux"
    return sys_platform or "linux"


def _default_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return machine


def _make_archive(bundle_root: Path, out_path: Path) -> None:
    if out_path.suffix == ".zip":
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in bundle_root.rglob("*"):
                if item.is_dir():
                    continue
                zf.write(item, item.relative_to(bundle_root))
        return
    with tarfile.open(out_path, "w:gz") as tf:
        tf.add(bundle_root, arcname=".")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Breadboard engine bundle for the current platform.")
    parser.add_argument("--version", required=True, help="Engine version string (e.g., 1.0.0)")
    parser.add_argument(
        "--engine-bin",
        help="Path to the engine binary to package (single file).",
    )
    parser.add_argument(
        "--engine-dir",
        help="Path to an onedir folder to package (e.g., PyInstaller dist output).",
    )
    parser.add_argument("--entry", help="Entry path within bundle (default: bin/<binary name>)")
    parser.add_argument("--platform", dest="platform_name", help="Override platform (default: auto-detect)")
    parser.add_argument("--arch", dest="arch_name", help="Override arch (default: auto-detect)")
    parser.add_argument("--out-dir", default="dist/engine_bundles", help="Output directory for bundles.")
    parser.add_argument("--base-url", help="Base URL for manifest asset URLs.")
    parser.add_argument("--min-cli-version", help="Minimum CLI version required to use this engine bundle.")
    parser.add_argument("--protocol-version", help="Protocol version required to use this engine bundle.")
    parser.add_argument("--manifest-path", help="Path to write manifest JSON (default: <out-dir>/manifest.json).")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append/merge this platform asset into an existing manifest (replaces prior platform+arch entry).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if bool(args.engine_bin) == bool(args.engine_dir):
        raise SystemExit("Provide exactly one of --engine-bin or --engine-dir.")

    engine_bin = Path(args.engine_bin).expanduser().resolve() if args.engine_bin else None
    engine_dir = Path(args.engine_dir).expanduser().resolve() if args.engine_dir else None

    if engine_bin is not None:
        if not engine_bin.exists():
            raise SystemExit(f"Engine binary not found: {engine_bin}")
        if not engine_bin.is_file():
            raise SystemExit(f"--engine-bin must be a file: {engine_bin}")

    if engine_dir is not None:
        if not engine_dir.exists():
            raise SystemExit(f"Engine directory not found: {engine_dir}")
        if not engine_dir.is_dir():
            raise SystemExit(f"--engine-dir must be a directory: {engine_dir}")

    platform_name = args.platform_name or _default_platform()
    arch_name = args.arch_name or _default_arch()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle_root = out_dir / args.version / f"{platform_name}-{arch_name}"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    if engine_dir is not None:
        shutil.copytree(engine_dir, bundle_root, dirs_exist_ok=True)
        if args.entry:
            entry = args.entry
        else:
            candidates = [
                "breadboard-engine.exe",
                "breadboard-engine",
                "breadboard_engine.exe",
                "breadboard_engine",
            ]
            entry = None
            for candidate in candidates:
                if (bundle_root / candidate).exists():
                    entry = candidate
                    break
            if entry is None:
                raise SystemExit(
                    f"Unable to infer bundle entrypoint. Pass --entry explicitly. Checked: {', '.join(candidates)}"
                )
    else:
        (bundle_root / "bin").mkdir(parents=True, exist_ok=True)
        assert engine_bin is not None
        target_name = engine_bin.name
        target_path = bundle_root / "bin" / target_name
        shutil.copy2(engine_bin, target_path)
        entry = args.entry or f"bin/{target_name}"

    archive_ext = ".zip" if platform_name == "win32" else ".tar.gz"
    archive_name = f"breadboard-engine-{platform_name}-{arch_name}{archive_ext}"
    archive_path = out_dir / args.version / archive_name
    _make_archive(bundle_root, archive_path)

    checksum = _sha256(archive_path)
    size_bytes = archive_path.stat().st_size

    asset_url = f"{args.version}/{archive_name}"
    if args.base_url:
        asset_url = f"{args.base_url.rstrip('/')}/{args.version}/{archive_name}"

    manifest_path = Path(args.manifest_path) if args.manifest_path else out_dir / "manifest.json"
    asset_payload: Dict[str, Any] = {
        "platform": platform_name,
        "arch": arch_name,
        "url": asset_url,
        "sha256": checksum,
        "size_bytes": size_bytes,
        "entry": entry,
    }

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    manifest: Dict[str, Any]
    if args.append and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise SystemExit(f"Manifest at {manifest_path} is not an object.")
        existing_version = manifest.get("version")
        if existing_version and str(existing_version) != args.version:
            raise SystemExit(f"Manifest version {existing_version} does not match requested {args.version}")
        manifest["version"] = args.version
        manifest["created_at"] = created_at
        if args.min_cli_version:
            manifest["min_cli_version"] = args.min_cli_version
        if args.protocol_version:
            manifest["protocol_version"] = args.protocol_version

        assets = manifest.get("assets")
        if not isinstance(assets, list):
            assets = []
        else:
            assets = [item for item in assets if not (isinstance(item, dict) and item.get("platform") == platform_name and item.get("arch") == arch_name)]
        assets.append(asset_payload)
        manifest["assets"] = assets
    else:
        manifest = {
            "version": args.version,
            "created_at": created_at,
            **({"min_cli_version": args.min_cli_version} if args.min_cli_version else {}),
            **({"protocol_version": args.protocol_version} if args.protocol_version else {}),
            "assets": [asset_payload],
        }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[engine-bundle] wrote {archive_path}")
    print(f"[engine-bundle] sha256 {checksum}")
    print(f"[engine-bundle] manifest {manifest_path}")


if __name__ == "__main__":
    main()
