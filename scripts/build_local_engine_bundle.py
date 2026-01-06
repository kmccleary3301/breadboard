#!/usr/bin/env python3
"""
Local, Linux-first engine bundling pipeline (PyInstaller onedir -> archive -> manifest).

Outputs are written under a gitignored directory by default:
  local_engine_bundles/

This script is intended for *developer validation* and as a building block for
multi-platform CI bundling (macOS/Windows should be built on their native runners).

Example (local build + manifest):
  python scripts/build_local_engine_bundle.py --version 0.1.0-dev

Then, to run the CLI against the bundled engine:
  export BREADBOARD_ENGINE_AUTO_DOWNLOAD=1
  export BREADBOARD_ENGINE_MANIFEST_URL="$(pwd)/local_engine_bundles/dist/manifest.json"
  export BREADBOARD_ENGINE_VERSION="0.1.0-dev"
  breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "breadboard-engine"


def _default_platform() -> str:
    if os.name == "nt":
        return "win32"
    sys_platform = platform.system().lower()
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


def _read_cli_version() -> str | None:
    try:
        raw = (ROOT / "tui_skeleton" / "package.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        version = data.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except Exception:
        return None
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build + package a local BreadBoard engine bundle.")
    parser.add_argument("--version", required=True, help="Engine version to write into the manifest.")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Executable name (default: breadboard-engine)")
    parser.add_argument(
        "--out-root",
        default=str(ROOT / "local_engine_bundles"),
        help="Root output directory (default: local_engine_bundles/)",
    )
    parser.add_argument(
        "--min-cli-version",
        default=None,
        help="Minimum CLI version required for this engine bundle (default: tui_skeleton/package.json version).",
    )
    parser.add_argument(
        "--protocol-version",
        default=None,
        help="CLI bridge protocol version (default: import from agentic_coder_prototype.api.cli_bridge.events).",
    )
    parser.add_argument(
        "--pyinstaller-arg",
        action="append",
        default=[],
        help="Extra args forwarded to scripts/build_engine_onedir.py (repeatable).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove previous build output for this version/platform before building.",
    )
    return parser.parse_args()


def _resolve_protocol_version() -> str:
    try:
        from agentic_coder_prototype.api.cli_bridge.events import PROTOCOL_VERSION

        if isinstance(PROTOCOL_VERSION, str) and PROTOCOL_VERSION.strip():
            return PROTOCOL_VERSION.strip()
    except Exception:
        pass
    return "1.0"


def main() -> int:
    args = parse_args()
    platform_name = _default_platform()
    arch_name = _default_arch()

    out_root = Path(args.out_root).expanduser().resolve()
    build_root = out_root / "build" / args.version / f"{platform_name}-{arch_name}"
    dist_root = out_root / "dist"
    dist_root.mkdir(parents=True, exist_ok=True)

    if args.clean and build_root.exists():
        shutil.rmtree(build_root, ignore_errors=True)

    # 1) Build onedir executable via PyInstaller
    onedir_script = ROOT / "scripts" / "build_engine_onedir.py"
    subprocess.check_call(
        [
            sys.executable,
            str(onedir_script),
            "--name",
            args.name,
            "--out-dir",
            str(build_root),
            *(["--clean"] if args.clean else []),
            *sum([["--pyinstaller-arg", item] for item in args.pyinstaller_arg if item], []),
        ],
        cwd=str(ROOT),
    )

    onedir_dir = build_root / "dist" / args.name
    if not onedir_dir.exists():
        raise SystemExit(f"expected onedir output missing: {onedir_dir}")

    # 2) Package as archive + manifest entry
    min_cli_version = args.min_cli_version or _read_cli_version()
    protocol_version = args.protocol_version or _resolve_protocol_version()

    packer = ROOT / "scripts" / "build_engine_bundle.py"
    manifest_path = dist_root / "manifest.json"
    append = False
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and str(existing.get("version", "")).strip() == args.version:
                append = True
        except Exception:
            append = False
    cmd = [
        sys.executable,
        str(packer),
        "--version",
        args.version,
        "--engine-dir",
        str(onedir_dir),
        "--platform",
        platform_name,
        "--arch",
        arch_name,
        "--out-dir",
        str(dist_root),
        "--manifest-path",
        str(manifest_path),
        *(["--append"] if append else []),
        "--protocol-version",
        protocol_version,
    ]
    if min_cli_version:
        cmd.extend(["--min-cli-version", min_cli_version])

    subprocess.check_call(cmd, cwd=str(ROOT))

    manifest = dist_root / "manifest.json"
    print()
    print("[local-engine-bundle] done.")
    print(f"[local-engine-bundle] manifest: {manifest}")
    print()
    print("Next:")
    print(f'  export BREADBOARD_ENGINE_AUTO_DOWNLOAD=1')
    print(f'  export BREADBOARD_ENGINE_MANIFEST_URL="{manifest}"')
    print(f'  export BREADBOARD_ENGINE_VERSION="{args.version}"')
    print(f'  breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
