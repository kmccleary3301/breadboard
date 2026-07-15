#!/usr/bin/env python3
"""Build an honest, deterministic inventory of candidate public bindings."""

from __future__ import annotations

import argparse
import argparse as argparse_module
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .validate_public_contracts import (
        ContractValidationError, SURFACES, canonical_bytes, load_frozen_surface,
        load_json, load_schema, validate_catalog, validate_record_surface,
    )
except ImportError:
    from validate_public_contracts import (  # type: ignore[no-redef]
        ContractValidationError, SURFACES, canonical_bytes, load_frozen_surface,
        load_json, load_schema, validate_catalog, validate_record_surface,
    )

PUBLIC_DIR = ROOT / "contracts" / "public"
DEFAULT_OUTPUT = PUBLIC_DIR / "surface_inventory.v1.json"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _cli_commands(root: Path) -> set[str]:
    commands: set[str] = set()
    if root == ROOT:
        from scripts.breadboard_cli import build_parser

        def walk(parser: argparse_module.ArgumentParser, prefix: tuple[str, ...]) -> None:
            for action in parser._actions:
                if isinstance(action, argparse_module._SubParsersAction):
                    for name, child in action.choices.items():
                        nested = any(isinstance(item, argparse_module._SubParsersAction) for item in child._actions)
                        walk(child, (*prefix, name)) if nested else commands.add(" ".join(("bbh", *prefix, name)))
        walk(build_parser(), ())
        return commands
    path = root / "scripts" / "breadboard_cli.py"
    if path.is_file():
        for namespace, command in re.findall(r'command="([a-z-]+) ([a-z-]+)"', path.read_text(encoding="utf-8")):
            commands.add(f"bbh {namespace} {command}")
    return commands


def _openapi_operations(root: Path) -> set[tuple[str, str, str]]:
    path = root / "docs" / "contracts" / "cli_bridge" / "openapi.json"
    if not path.is_file():
        return set()
    found: set[tuple[str, str, str]] = set()
    for route, path_item in load_json(path).get("paths", {}).items():
        if isinstance(path_item, dict):
            for method, operation in path_item.items():
                if isinstance(operation, dict) and isinstance(operation.get("operationId"), str):
                    found.add((method.upper(), route, operation["operationId"]))
    return found


def _binding_manifest(path: Path, fields: tuple[str, ...]) -> set[tuple[str, ...]]:
    if not path.is_file():
        return set()
    rows = load_json(path).get("operations")
    if not isinstance(rows, list):
        raise ContractValidationError(f"{path}: operations must be an array")
    found: set[tuple[str, ...]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or any(not isinstance(row.get(field), str) for field in fields):
            raise ContractValidationError(f"{path}: operations.{index} lacks string fields {fields}")
        identity = tuple(row[field] for field in fields)
        if identity in found:
            raise ContractValidationError(f"{path}: duplicate generated binding {identity}")
        found.add(identity)
    return found


def _surface_result(detected: bool, present: str, missing: str) -> dict[str, str]:
    return {"evidence": present if detected else missing, "status": "candidate_binding_detected" if detected else "gap"}


def build_inventory(root: Path = ROOT) -> dict[str, Any]:
    public_dir = root / "contracts" / "public"
    schema_dir = public_dir / "schemas"
    kernel_schema_dir = root / "contracts" / "kernel" / "schemas"
    catalog_path, records_path = public_dir / "operations.v1.json", public_dir / "record_surface.v1.json"
    catalog, records, frozen = load_json(catalog_path), load_json(records_path), load_frozen_surface(public_dir)
    validate_catalog(catalog, schema_dir, frozen)
    validate_record_surface(records, schema_dir, kernel_schema_dir, frozen)

    cli_commands, openapi = _cli_commands(root), _openapi_operations(root)
    python_bindings = _binding_manifest(root / "breadboard_sdk" / "generated" / "public_surface_manifest.v1.json", ("operation_id", "client", "method"))
    typescript_bindings = _binding_manifest(root / "sdk" / "ts" / "src" / "generated" / "public_surface_manifest.v1.json", ("operation_id", "client", "method"))
    tui_bindings = _binding_manifest(root / "tui_skeleton" / "src" / "generated" / "public_surface_manifest.v1.json", ("operation_id", "action_id", "kind"))
    docs_root = (root / "docs" / "reference" / "public").resolve()
    operation_rows: list[dict[str, Any]] = []
    counts = {surface: 0 for surface in SURFACES}
    for operation in sorted(catalog["operations"], key=lambda row: row["operation_id"]):
        operation_id, bindings = operation["operation_id"], operation["bindings"]
        command, api = bindings["bbh"]["command"], bindings["openapi"]
        py, ts, tui, slug = bindings["python_sdk"], bindings["typescript_sdk"], bindings["tui"], bindings["docs"]["slug"]
        doc_path = (docs_root / f"{slug}.md").resolve()
        detected = {
            "bbh": command in cli_commands,
            "openapi": (api["method"], api["path"], api["operation_id"]) in openapi,
            "python_sdk": (operation_id, py["client"], py["method"]) in python_bindings,
            "typescript_sdk": (operation_id, ts["client"], ts["method"]) in typescript_bindings,
            "tui": (operation_id, tui["action_id"], tui["kind"]) in tui_bindings,
            "docs": doc_path.is_relative_to(docs_root) and doc_path.is_file(),
        }
        evidence = {
            "bbh": (f"registered command {command}", f"no registered command {command}"),
            "openapi": (f"OpenAPI has {api['method']} {api['path']} as {operation_id}", f"OpenAPI lacks exact {api['method']} {api['path']} / {operation_id} binding"),
            "python_sdk": (f"generated Python manifest binds {operation_id} to {py['client']}.{py['method']}", f"generated Python manifest lacks {operation_id} / {py['client']}.{py['method']}"),
            "typescript_sdk": (f"generated TypeScript manifest binds {operation_id} to {ts['client']}.{ts['method']}", f"generated TypeScript manifest lacks {operation_id} / {ts['client']}.{ts['method']}"),
            "tui": (f"generated TUI manifest binds {operation_id} to {tui['action_id']} ({tui['kind']})", f"generated TUI manifest lacks {operation_id} / {tui['action_id']} ({tui['kind']})"),
            "docs": (f"generated reference exists for {slug}", f"generated reference is missing for {slug}"),
        }
        surfaces: dict[str, dict[str, str]] = {}
        for surface in SURFACES:
            surfaces[surface] = _surface_result(detected[surface], *evidence[surface])
            counts[surface] += int(detected[surface])
        operation_rows.append({"operation_id": operation_id, "surfaces": surfaces})

    inventory = {
        "candidate_status": "candidate", "catalog_sha256": _sha256(catalog_path),
        "contract_id": "bb.public_surface_inventory.v1", "generated_by": "scripts/quality/build_surface_inventory.py",
        "operation_count": len(operation_rows), "operations": operation_rows, "parity_claimed": False,
        "record_surface_sha256": _sha256(records_path),
        "summary": {surface: {"detected": counts[surface], "gaps": len(operation_rows) - counts[surface], "total": len(operation_rows)} for surface in SURFACES},
        "version": 1,
    }
    schema = load_schema(schema_dir / "bb.public_surface_inventory.v1.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(inventory), key=lambda error: list(error.absolute_path))
    if errors:
        raise ContractValidationError("invalid generated inventory: " + "; ".join(error.message for error in errors))
    return inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the checked-in inventory is stale")
    args = parser.parse_args(argv)
    content = canonical_bytes(build_inventory())
    if args.check:
        if not args.output.exists() or args.output.read_bytes() != content:
            print(f"surface inventory stale: {args.output}")
            return 1
        print(f"surface inventory fixed point: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(f"wrote candidate surface inventory: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
