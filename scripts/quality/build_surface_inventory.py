#!/usr/bin/env python3
"""Build an honest, deterministic inventory of candidate public bindings."""

from __future__ import annotations

import argparse
import argparse as argparse_module
import ast
import hashlib
import json
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
        SCHEMA_DIR,
        SURFACES,
        canonical_bytes,
        load_json,
        validate_catalog,
        validate_record_surface,
    )
except ImportError:
    from validate_public_contracts import (  # type: ignore[no-redef]
        SCHEMA_DIR,
        SURFACES,
        canonical_bytes,
        load_json,
        validate_catalog,
        validate_record_surface,
    )

PUBLIC_DIR = ROOT / "contracts" / "public"
DEFAULT_OUTPUT = PUBLIC_DIR / "surface_inventory.v1.json"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _cli_commands(root: Path) -> set[str]:
    from scripts.breadboard_cli import build_parser

    commands: set[str] = set()

    def walk(parser: argparse_module.ArgumentParser, prefix: tuple[str, ...]) -> None:
        for action in parser._actions:
            if not isinstance(action, argparse_module._SubParsersAction):
                continue
            for name, child in action.choices.items():
                next_prefix = (*prefix, name)
                nested = any(isinstance(item, argparse_module._SubParsersAction) for item in child._actions)
                if nested:
                    walk(child, next_prefix)
                else:
                    commands.add(" ".join(("bbh", *next_prefix)))

    if root == ROOT:
        walk(build_parser(), ())
        return commands
    source = (root / "scripts" / "breadboard_cli.py").read_text(encoding="utf-8")
    for namespace, command in re.findall(r'command="([a-z-]+) ([a-z-]+)"', source):
        commands.add(f"bbh {namespace} {command}")
    return commands


def _openapi_operations(root: Path) -> set[tuple[str, str, str]]:
    path = root / "docs" / "contracts" / "cli_bridge" / "openapi.json"
    if not path.exists():
        return set()
    document = load_json(path)
    found: set[tuple[str, str, str]] = set()
    for route, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if isinstance(operation, dict) and isinstance(operation.get("operationId"), str):
                found.add((method.upper(), route, operation["operationId"]))
    return found


def _python_methods(root: Path) -> set[str]:
    path = root / "breadboard_sdk" / "client.py"
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BreadBoardClient":
            return {child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


def _typescript_methods(root: Path) -> set[str]:
    path = root / "sdk" / "ts" / "src" / "client.ts"
    if not path.exists():
        return set()
    source = path.read_text(encoding="utf-8")
    if re.search(r"\b(?:class|interface)\s+BreadBoardClient\b", source) is None:
        return set()
    return set(re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*):\s", source, re.MULTILINE))


def _surface_result(detected: bool, present: str, missing: str) -> dict[str, str]:
    return {
        "evidence": present if detected else missing,
        "status": "candidate_binding_detected" if detected else "gap",
    }


def build_inventory(root: Path = ROOT) -> dict[str, Any]:
    public_dir = root / "contracts" / "public"
    catalog_path = public_dir / "operations.v1.json"
    records_path = public_dir / "record_surface.v1.json"
    catalog = load_json(catalog_path)
    records = load_json(records_path)
    validate_catalog(catalog)
    validate_record_surface(records)

    cli_commands = _cli_commands(root)
    openapi = _openapi_operations(root)
    python_methods = _python_methods(root)
    typescript_methods = _typescript_methods(root)
    tui_source = root / "tui_skeleton" / "src"
    docs_root = root / "docs" / "reference" / "public"
    tui_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(tui_source.rglob("*.ts"))
    ) if tui_source.exists() else ""

    operation_rows: list[dict[str, Any]] = []
    counts = {surface: 0 for surface in SURFACES}
    for operation in sorted(catalog["operations"], key=lambda row: row["operation_id"]):
        bindings = operation["bindings"]
        command = bindings["bbh"]["command"]
        api = bindings["openapi"]
        py_method = bindings["python_sdk"]["method"]
        ts_method = bindings["typescript_sdk"]["method"]
        action_id = bindings["tui"]["action_id"]
        slug = bindings["docs"]["slug"]
        detected = {
            "bbh": command in cli_commands,
            "openapi": (api["method"], api["path"], api["operation_id"]) in openapi,
            "python_sdk": py_method in python_methods,
            "typescript_sdk": ts_method in typescript_methods,
            "tui": action_id in tui_text,
            "docs": (docs_root / f"{slug}.md").is_file(),
        }
        evidence = {
            "bbh": (f"registered command {command}", f"no registered command {command}"),
            "openapi": (f"OpenAPI has {api['method']} {api['path']} as {api['operation_id']}", f"OpenAPI lacks exact {api['method']} {api['path']} / {api['operation_id']} binding"),
            "python_sdk": (f"BreadboardClient defines {py_method}", f"BreadboardClient lacks {py_method}"),
            "typescript_sdk": (f"TypeScript client defines {ts_method}", f"TypeScript client lacks {ts_method}"),
            "tui": (f"typed TUI action {action_id} is registered", f"typed TUI action {action_id} is not registered"),
            "docs": (f"generated reference exists for {slug}", f"generated reference is missing for {slug}"),
        }
        surfaces: dict[str, dict[str, str]] = {}
        for surface in SURFACES:
            surfaces[surface] = _surface_result(detected[surface], *evidence[surface])
            counts[surface] += int(detected[surface])
        operation_rows.append({"operation_id": operation["operation_id"], "surfaces": surfaces})

    inventory = {
        "candidate_status": "candidate",
        "catalog_sha256": _sha256(catalog_path),
        "contract_id": "bb.public_surface_inventory.v1",
        "generated_by": "scripts/quality/build_surface_inventory.py",
        "operation_count": len(operation_rows),
        "operations": operation_rows,
        "parity_claimed": False,
        "record_surface_sha256": _sha256(records_path),
        "summary": {
            surface: {"detected": counts[surface], "gaps": len(operation_rows) - counts[surface], "total": len(operation_rows)}
            for surface in SURFACES
        },
        "version": 1,
    }
    schema = load_json(SCHEMA_DIR / "bb.public_surface_inventory.v1.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(inventory), key=lambda error: list(error.absolute_path))
    if errors:
        raise ValueError("invalid generated inventory: " + "; ".join(error.message for error in errors))
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
