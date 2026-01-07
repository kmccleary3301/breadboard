#!/usr/bin/env python3
"""Merge per-platform engine bundle manifests into a single manifest.json.

Intended for CI release workflows where each OS builds its own bundle archive.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge BreadBoard engine bundle manifests.")
    parser.add_argument("--out", required=True, help="Output manifest path.")
    parser.add_argument("inputs", nargs="+", help="Input manifest fragment paths.")
    return parser.parse_args()


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Manifest {path} is not an object.")
    return data


def _key(asset: Dict[str, Any]) -> Tuple[str, str]:
    return (str(asset.get("platform") or ""), str(asset.get("arch") or ""))


def main() -> int:
    args = parse_args()
    out_path = Path(args.out).expanduser().resolve()
    inputs = [Path(p).expanduser().resolve() for p in args.inputs]
    manifests = [_load_json(p) for p in inputs]

    version = str(manifests[0].get("version") or "").strip()
    if not version:
        raise SystemExit("Missing version in first manifest fragment.")

    merged: Dict[str, Any] = {
        "version": version,
        "created_at": manifests[0].get("created_at"),
    }
    for key in ("min_cli_version", "protocol_version"):
        value = manifests[0].get(key)
        if value is not None:
            merged[key] = value

    assets_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for manifest, path in zip(manifests, inputs):
        frag_version = str(manifest.get("version") or "").strip()
        if frag_version != version:
            raise SystemExit(f"Manifest {path} version {frag_version} does not match expected {version}")

        for field in ("min_cli_version", "protocol_version"):
            expected = merged.get(field)
            got = manifest.get(field)
            if expected is not None and got is not None and str(expected) != str(got):
                raise SystemExit(f"Manifest {path} {field} {got} does not match expected {expected}")
            if expected is None and got is not None:
                merged[field] = got

        assets = manifest.get("assets")
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            key = _key(asset)
            if key == ("", ""):
                continue
            assets_by_key[key] = dict(asset)

    merged_assets: List[Dict[str, Any]] = list(assets_by_key.values())
    merged_assets.sort(key=lambda a: (str(a.get("platform") or ""), str(a.get("arch") or "")))
    merged["assets"] = merged_assets

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[merge-engine-manifest] wrote {out_path} assets={len(merged_assets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

