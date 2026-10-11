#!/usr/bin/env python3
"""Stage pinned stock-resolved LSP servers and bundled Bun's isolated cache.

OpenCode server.ts:98-106 runs bun x typescript-language-server; :512-527
resolves pyright-langserver on PATH first. Bundled Bun 1.3.10 bunx_command.zig
tries a local PATH executable before the cache/install branches. Install only
here, never in an episode. Package tarballs are checked against recorded npm
integrity and their installed files are compared byte-for-byte afterwards.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

from breadboard_engine.security.stock_install import stock_install_digest

PINS = {
    "typescript-language-server": ("6.0.2", "sha512-Ph7Ql+xkUJvMamnkh1h+aKP6HxLR7lrWIYkPn9/LZBkJdJZgtIj7OoVhkDYtQ7zFPTPmqZHOVXaUKGyz9M2q1Q=="),
    "typescript": ("6.0.3", "sha512-y2TvuxSZPDyQakkFRPZHKFm+KKVqIisdg9/CZwm9ftvKXLP8NRWj38/ODjNbr43SsoXqNuAisEf1GdCxqWcdBw=="),
    "pyright": ("1.1.414", "sha512-FPZZb51jepDX4eP7TEYDeNFtmE3WgwkkEcJpvH3/QmUSsj0EAy3LXu+xB4T/FWDejtsXlCfFr/rbWFjwuwuXww=="),
}


def materialize_bins(root: Path):
    # Bun writes npm bin symlinks. The restricted install policy rejects
    # symlinks, so retain the original package entrypoints behind wrappers.
    for binary in (root / "node_modules/.bin").iterdir():
        if not binary.is_symlink():
            continue
        target = binary.resolve(strict=True)
        if not target.is_relative_to(root):
            raise RuntimeError("Stock LSP binary escapes its verified install root")
        binary.unlink()
        import shlex
        binary.write_text("#!/bin/sh\nexec node " + shlex.quote(str(target)) + ' "$@"\n')
        binary.chmod(0o755)
    for alias in (root / "install-cache").rglob("*"):
        if not alias.is_symlink():
            continue
        target = alias.resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_dir():
            raise RuntimeError("Stock Bun cache alias escapes its verified package directory")
        alias.unlink()
        shutil.copytree(target, alias)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-lsp-1.2.17")
    parser.add_argument("--binary", type=Path, default=Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-darwin-arm64@1.2.17/package/bin/opencode")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    expected = {}
    for name, (version, integrity) in PINS.items():
        url = f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz"
        data = urllib.request.urlopen(url, timeout=180).read()
        if "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode() != integrity:
            raise RuntimeError(f"Recorded npm integrity mismatch: {name}@{version}")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            expected[name] = {member.name.removeprefix("package/"): archive.extractfile(member).read()
                              for member in archive.getmembers() if member.isfile()}
    (root / "package.json").write_text(json.dumps({"private": True, "dependencies": {name: pin[0] for name, pin in PINS.items()}}))
    subprocess.run([str(args.binary), "install", "--ignore-scripts", "--omit=optional", "--backend=copyfile",
                    "--cache-dir=" + str(root / "install-cache")], cwd=root,
                   env={**os.environ, "BUN_BE_BUN": "1", "HOME": str(root / "home")}, check=True)
    for name, files in expected.items():
        for relative, content in files.items():
            if (root / "node_modules" / name / relative).read_bytes() != content:
                raise RuntimeError(f"Installed stock LSP package differs from verified tarball: {name}/{relative}")
    materialize_bins(root)
    (root / "stock-lsp.json").write_text(json.dumps({"opencode": "1.2.17", "bun": "1.3.10", "packages": PINS}, sort_keys=True))
    print(json.dumps({"path": str(root), "digest": stock_install_digest(root)}, sort_keys=True))


if __name__ == "__main__":
    main()
