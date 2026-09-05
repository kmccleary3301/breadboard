#!/usr/bin/env python3
"""Generate public operation schemas from the canonical schema source."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SOURCE_RELATIVE = Path("contracts/public/operation_schemas.v1.json")
SCHEMA_DIR_RELATIVE = Path("contracts/public/schemas")
GENERATOR = "scripts/quality/generate_public_operation_schemas.py"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def source_and_schemas(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    source_path = root / SOURCE_RELATIVE
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source.get("contract_id") != "bb.public_operation_schema_source.v1":
        raise ValueError("operation schema source has an unexpected contract_id")
    if source.get("version") != 1 or source.get("status") != "candidate":
        raise ValueError("operation schema source must be candidate version 1")
    if source.get("operation_id") != "research.compare":
        raise ValueError("operation schema source must describe research.compare")
    schemas = source.get("schemas")
    if not isinstance(schemas, dict) or set(schemas) != {
        "bb.research.compare.input.v1",
        "bb.research.compare.result.v1",
    }:
        raise ValueError("operation schema source must contain exactly research.compare input/result schemas")
    source_bytes = canonical_bytes(source)
    if source_path.read_bytes() != source_bytes:
        raise ValueError(f"{source_path} is not canonical JSON")
    source_hash = f"sha256:{hashlib.sha256(source_bytes).hexdigest()}"
    generated: dict[str, dict[str, Any]] = {}
    for schema_id, schema in schemas.items():
        if not isinstance(schema, dict):
            raise ValueError(f"{schema_id} must be an object")
        expected_id = f"https://breadboard.dev/contracts/public/schemas/{schema_id}.schema.json"
        if schema.get("$id") != expected_id:
            raise ValueError(f"{schema_id} has a non-canonical $id")
        if schema.get("properties", {}).get("schema_version") is not None:
            raise ValueError(f"{schema_id} must not introduce a second schema-version field")
        Draft202012Validator.check_schema(schema)
        generated[schema_id] = {
            **schema,
            "x-generated-by": GENERATOR,
            "x-source-sha256": source_hash,
        }
    return source, generated, source_hash


def build_outputs(root: Path | str | None = None) -> dict[Path, bytes]:
    repo_root = Path(ROOT if root is None else root).resolve()
    _, schemas, _ = source_and_schemas(repo_root)
    return {
        repo_root / SCHEMA_DIR_RELATIVE / f"{schema_id}.schema.json": canonical_bytes(schema)
        for schema_id, schema in schemas.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated schemas without writing")
    args = parser.parse_args(argv)
    outputs = build_outputs()
    stale = [path for path, content in outputs.items() if not path.is_file() or path.read_bytes() != content]
    if args.check:
        if stale:
            for path in stale:
                print(f"stale generated operation schema: {path.relative_to(ROOT)}")
            return 1
        print(f"public operation schema codegen check: OK ({len(outputs)} files current)")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print(f"public operation schema codegen: wrote {len(outputs)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
