#!/usr/bin/env python3
"""Generate product-client binding manifests from the current operation catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "contracts" / "public" / "operations.v2.json"
TARGETS = {
    "python_sdk": ROOT / "breadboard_sdk" / "generated" / "public_surface_manifest.v1.json",
    "typescript_sdk": ROOT / "sdk" / "ts" / "src" / "generated" / "public_surface_manifest.v1.json",
    "tui": ROOT / "tui_skeleton" / "src" / "generated" / "public_surface_manifest.v1.json",
}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def build_manifests() -> dict[Path, bytes]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    manifests: dict[Path, bytes] = {}
    for surface, output in TARGETS.items():
        fields = ("action_id", "kind") if surface == "tui" else ("client", "method")
        operations = []
        for operation in sorted(catalog["operations"], key=lambda row: row["operation_id"]):
            binding = operation["bindings"][surface]
            operations.append({
                "operation_id": operation["operation_id"],
                **{field: binding[field] for field in fields},
            })
        manifests[output] = canonical_bytes({
            "catalog_id": catalog["contract_id"],
            "operations": operations,
            "schema_version": "bb.public_client_binding_manifest.v1",
            "surface": surface,
        })
    return manifests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale: list[Path] = []
    for path, content in build_manifests().items():
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if stale:
        for path in stale:
            print(f"stale product client manifest: {path.relative_to(ROOT)}")
        return 1
    print("product client manifests verified" if args.check else "product client manifests written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
