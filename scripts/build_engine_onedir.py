#!/usr/bin/env python3
"""
Build a standalone onedir executable for the BreadBoard CLI engine via PyInstaller.

This is the first stage of the Phase 12 / Phase 1.1 packaging pipeline.
It produces a directory containing an executable plus all shared libraries
needed to run the engine without an external Python installation.

Example:
  python scripts/build_engine_onedir.py --out-dir local_engine_bundles/build --name breadboard-engine

Notes:
  - This script does *not* create the final archive/manifest. Use
    scripts/build_engine_bundle.py (or a higher-level pipeline) to package
    the resulting onedir folder into a `.tar.gz` / `.zip` + manifest entry.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENTRY = ROOT / "agentic_coder_prototype" / "api" / "cli_bridge" / "server.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an onedir BreadBoard engine executable via PyInstaller.")
    parser.add_argument("--name", default="breadboard-engine", help="Executable name (default: breadboard-engine)")
    parser.add_argument(
        "--entry",
        default=str(DEFAULT_ENTRY),
        help="Entry script path (default: agentic_coder_prototype/api/cli_bridge/server.py)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "local_engine_bundles" / "build"),
        help="Output directory (default: local_engine_bundles/build)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete prior build output for the same name before building.",
    )
    parser.add_argument(
        "--pyinstaller-arg",
        action="append",
        default=[],
        help="Additional args forwarded to PyInstaller (repeatable).",
    )
    return parser.parse_args()


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        raise SystemExit(
            "PyInstaller is not installed in the active Python environment.\n"
            "Install it with: python -m pip install pyinstaller"
        )


def main() -> int:
    args = parse_args()
    ensure_pyinstaller()

    entry = Path(args.entry).expanduser().resolve()
    if not entry.exists():
        print(f"[engine-onedir] entry not found: {entry}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir).expanduser().resolve()
    build_dir = out_dir / "work"
    dist_dir = out_dir / "dist"
    spec_dir = out_dir / "spec"

    if args.clean:
        shutil.rmtree(build_dir, ignore_errors=True)
        shutil.rmtree(dist_dir / args.name, ignore_errors=True)
        shutil.rmtree(spec_dir, ignore_errors=True)

    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    # Ensure repo root is on sys.path for analysis/runtime import resolution.
    pathex = str(ROOT)

    extra_binaries: list[str] = []
    try:
        import ray  # type: ignore

        ray_root = Path(getattr(ray, "__file__", "")).resolve().parent
        thirdparty_psutil = ray_root / "thirdparty_files" / "psutil"
        if thirdparty_psutil.exists():
            for binary in thirdparty_psutil.glob("_psutil*.so*"):
                extra_binaries.append(str(binary))
    except Exception:
        extra_binaries = []

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        args.name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        pathex,
        str(entry),
        "--collect-submodules",
        "agentic_coder_prototype",
        "--collect-all",
        "ray",
    ]

    # Ray injects thirdparty_files onto sys.path at runtime; include its psutil extension modules.
    # Without these, the packaged engine fails early during `import ray`.
    for binary in extra_binaries:
        cmd.extend(
            [
                "--add-binary",
                f"{binary}{os.pathsep}ray/thirdparty_files/psutil",
            ]
        )

    # Keep logs quieter unless explicitly requested.
    if os.environ.get("BREADBOARD_PYINSTALLER_LOG_LEVEL"):
        cmd.extend(["--log-level", os.environ["BREADBOARD_PYINSTALLER_LOG_LEVEL"]])

    for extra in args.pyinstaller_arg:
        if extra:
            cmd.append(extra)

    print("[engine-onedir] running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    output = dist_dir / args.name
    if not output.exists():
        print(f"[engine-onedir] expected output missing: {output}", file=sys.stderr)
        return 3

    print(f"[engine-onedir] built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
