#!/usr/bin/env python3
"""Stage stock-selected ast-grep 0.40.0; never enable writable-cache execution.

Source omo 5137df72 tools/ast-grep/downloader.ts:19,21-28,85-87 selects
0.40.0; sg-cli-path.ts:34-66 checks cache, @ast-grep/cli, platform package.
The npm ast-grep binary is byte-identical to the pinned GitHub release binary.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request

from breadboard_engine.security.stock_install import stock_install_digest

VERSION = "0.40.0"
CLI_URL = "https://registry.npmjs.org/@ast-grep/cli/-/cli-0.40.0.tgz"
CLI_INTEGRITY = "sha512-L8AkflsfI2ZP70yIdrwqvjR02ScCuRmM/qNGnJWUkOFck+e6gafNVJ4e4jjGQlEul+dNdBpx36+O2Op629t47A=="
PLATFORM_URL = "https://registry.npmjs.org/@ast-grep/cli-darwin-arm64/-/cli-darwin-arm64-0.40.0.tgz"
PLATFORM_INTEGRITY = "sha512-UehY2MMUkdJbsriP7NKc6+uojrqPn7d1Cl0em+WAkee7Eij81VdyIjRsRxtZSLh440ZWQBHI3PALZ9RkOO8pKQ=="
BINARY_SHA256 = "a8de4e534206a71030ae7151f049cb4513548b4e5e9e88a71ed0b73b041e5ace"
RELEASE_URL = "https://github.com/ast-grep/ast-grep/releases/download/0.40.0/app-aarch64-apple-darwin.zip"
RELEASE_SHA256 = "fabfd23fe06ed780ecfa2250adc7615221bfbfa962902b6f3807de67bcef253f"


def stage_package(url: str, integrity: str, destination: Path) -> None:
    data = urllib.request.urlopen(url, timeout=180).read()
    observed = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if observed != integrity:
        raise RuntimeError(f"Stock dependency integrity mismatch: {url}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith("package/"):
                raise RuntimeError(f"Unexpected stock dependency archive member: {member.name}")
            relative = Path(member.name).relative_to("package")
            if ".." in relative.parts:
                raise RuntimeError("Stock dependency archive escapes its root")
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(member.mode & 0o777)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path,
                        default=Path.home() / ".cache/bb-compaction-e4/pkgs/oh-my-opencode@3.10.0-5137df72")
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        parser.error("This recorded stock dependency pin is for darwin-arm64 only")
    root = args.plugin_root.expanduser().resolve()
    if not (root / "dist/index.js").is_file():
        parser.error(f"Pinned plugin must already be staged: {root}")
    modules = root / "node_modules/@ast-grep"
    stage_package(CLI_URL, CLI_INTEGRITY, modules / "cli")
    stage_package(PLATFORM_URL, PLATFORM_INTEGRITY, modules / "cli-darwin-arm64")
    binary = modules / "cli-darwin-arm64/ast-grep"
    with binary.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != BINARY_SHA256:
            raise RuntimeError("Stock ast-grep binary digest mismatch")
    # Execute the pinned npm package's reviewed, unmodified postinstall layout.
    subprocess.run(["node", str(modules / "cli/postinstall.js")], check=True)
    print(f"ast-grep {VERSION}: {BINARY_SHA256}")
    print(f"plugin tree-v1: {stock_install_digest(root)}")


if __name__ == "__main__":
    main()
