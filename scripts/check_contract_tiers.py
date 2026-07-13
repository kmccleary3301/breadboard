#!/usr/bin/env python3
"""Validate the generated Phase 20 contract-tier census."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import AbstractSet, Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKS_PATH = ROOT / "contracts/kernel/packs.v1.json"
DEFAULT_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.contract_tiers.v1.schema.json"
DEFAULT_REGISTRY_PATH = ROOT / "contracts/kernel/registries/contract_tiers.v1.json"
PHASE20_SCHEMA_IDS = frozenset(
    {
        "bb.e4.lane_manifest.v1",
        "bb.e4.lane_lock.v1",
        "bb.contract_tiers.v1",
    }
)


def _tracked_files() -> frozenset[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"cannot enumerate git-tracked files: {exc}") from exc
    return frozenset(
        (ROOT / path).resolve()
        for path in result.stdout.split("\0")
        if path
    )


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _schema_id_from_filename(filename: str) -> str:
    suffix = ".schema.json"
    if not filename.endswith(suffix):
        raise ValueError(f"pack schema entry must end with {suffix}: {filename!r}")
    return filename[: -len(suffix)]


def generated_schema_ids(packs_path: Path = DEFAULT_PACKS_PATH) -> set[str]:
    """Generate the census from pack membership, then add the three Phase 20 schemas."""

    packs = _load_object(packs_path)
    entries = packs.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{packs_path}: entries must be a list")
    schema_ids: set[str] = set()
    for pack_index, pack in enumerate(entries):
        if not isinstance(pack, dict):
            raise ValueError(f"{packs_path}: entries[{pack_index}] must be an object")
        metadata = pack.get("metadata")
        schemas = metadata.get("schemas") if isinstance(metadata, dict) else None
        if not isinstance(schemas, list) or not all(isinstance(item, str) for item in schemas):
            raise ValueError(f"{packs_path}: entries[{pack_index}].metadata.schemas must be strings")
        for filename in schemas:
            schema_id = _schema_id_from_filename(filename)
            if schema_id in schema_ids:
                raise ValueError(f"{packs_path}: duplicate packed schema id {schema_id}")
            schema_ids.add(schema_id)
    return schema_ids | set(PHASE20_SCHEMA_IDS)


def _schema_error(error: Any) -> str:
    pointer = "/" + "/".join(str(part) for part in error.absolute_path) if error.absolute_path else "<root>"
    return f"{pointer}: {error.message}"


def validate_contract_tiers(
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    packs_path: Path = DEFAULT_PACKS_PATH,
    tracked_files: AbstractSet[Path] | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        tracked = frozenset(
            path.resolve()
            for path in (tracked_files if tracked_files is not None else _tracked_files())
        )
    except (OSError, ValueError) as exc:
        return [str(exc)]
    for label, path in (
        ("registry", registry_path),
        ("schema", schema_path),
        ("packs", packs_path),
    ):
        if path.resolve() not in tracked:
            errors.append(f"{label} input is not tracked: {path}")
    try:
        schema = _load_object(schema_path)
        registry = _load_object(registry_path)
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [str(exc)]

    errors.extend(
        _schema_error(error)
        for error in sorted(
            Draft202012Validator(schema).iter_errors(registry),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    )

    entries = registry.get("entries")
    if not isinstance(entries, list):
        return errors
    schema_ids = [entry.get("schema_id") for entry in entries if isinstance(entry, dict)]
    duplicates = sorted(schema_id for schema_id, count in Counter(schema_ids).items() if isinstance(schema_id, str) and count > 1)
    errors.extend(f"duplicate registry entry for {schema_id}" for schema_id in duplicates)

    try:
        expected = generated_schema_ids(packs_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))
        return errors
    actual = {schema_id for schema_id in schema_ids if isinstance(schema_id, str)}
    for schema_id in sorted(expected - actual):
        errors.append(f"generated census schema missing from registry: {schema_id}")
    for schema_id in sorted(actual - expected):
        errors.append(f"registry schema absent from generated census: {schema_id}")

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        schema_id = str(entry.get("schema_id", "<unknown>"))
        consumers = entry.get("consumers")
        if entry.get("disposition") == "keep" and not consumers:
            errors.append(f"{schema_id}: disposition keep requires at least one consumer")
        if isinstance(consumers, list):
            for consumer in consumers:
                if not isinstance(consumer, dict) or not isinstance(consumer.get("path"), str):
                    continue
                consumer_path = Path(consumer["path"])
                if consumer_path.is_absolute():
                    errors.append(f"{schema_id}: consumer path must be repo-relative: {consumer_path}")
                elif (ROOT / consumer_path).resolve() not in tracked:
                    errors.append(f"{schema_id}: consumer path is not tracked: {consumer_path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--packs", type=Path, default=DEFAULT_PACKS_PATH)
    args = parser.parse_args(argv)
    errors = validate_contract_tiers(
        registry_path=args.registry,
        schema_path=args.schema,
        packs_path=args.packs,
    )
    if errors:
        for error in errors:
            print(f"contract-tiers: {error}", file=sys.stderr)
        return 1
    count = len(generated_schema_ids(args.packs))
    print(f"contract-tiers: PASS ({count} generated entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
