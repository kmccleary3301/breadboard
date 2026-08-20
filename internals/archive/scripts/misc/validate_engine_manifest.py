#!/usr/bin/env python3
"""Validate engine manifest structure and referenced assets."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate BreadBoard engine manifest.")
    parser.add_argument("manifest", help="Path to manifest.json")
    parser.add_argument(
        "--asset-root",
        help="Optional directory to verify that manifest assets exist locally.",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Manifest {path} is not a JSON object.")
    return data


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(msg)


def _asset_filename(url: str) -> str:
    parsed = urlparse(url)
    candidate = parsed.path or url
    return Path(candidate).name


def _find_asset(asset_root: Path, filename: str) -> Optional[Path]:
    for candidate in asset_root.rglob(filename):
        if candidate.is_file():
            return candidate
    return None


def _validate_assets(assets: Iterable[Dict[str, Any]], asset_root: Optional[Path]) -> None:
    for asset in assets:
        _assert(isinstance(asset, dict), "Manifest asset entries must be objects.")
        for field in ("platform", "arch", "url", "sha256", "size_bytes", "entry"):
            _assert(field in asset, f"Manifest asset missing required field: {field}")
        _assert(
            re.fullmatch(r"[0-9a-fA-F]{64}", str(asset.get("sha256") or "")) is not None,
            f"Manifest asset sha256 is invalid: {asset.get('sha256')}",
        )
        _assert(
            isinstance(asset.get("size_bytes"), int) and asset["size_bytes"] >= 0,
            f"Manifest asset size_bytes invalid: {asset.get('size_bytes')}",
        )
        if asset_root:
            filename = _asset_filename(str(asset.get("url") or ""))
            _assert(filename, "Manifest asset url missing filename.")
            found = _find_asset(asset_root, filename)
            _assert(found is not None, f"Manifest asset {filename} not found under {asset_root}")


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    data = _load_manifest(manifest_path)

    version = str(data.get("version") or "").strip()
    _assert(version, "Manifest missing version.")

    assets = data.get("assets")
    _assert(isinstance(assets, list) and assets, "Manifest assets must be a non-empty list.")

    asset_root = Path(args.asset_root).expanduser().resolve() if args.asset_root else None
    _validate_assets(assets, asset_root)

    print(f"[validate-engine-manifest] ok version={version} assets={len(assets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
