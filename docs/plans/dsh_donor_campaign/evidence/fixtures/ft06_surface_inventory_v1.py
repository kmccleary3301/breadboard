#!/usr/bin/env python3
"""Reproduce the FT-06 source-surface inventory from pinned ENGINE/TUI roots."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "breadboard.ft06_surface_inventory.v1"
ENGINE_HEAD = "b3cacc7356244253305f8a6f84308a993485bfe2"
TUI_HEAD = "73d6e6f55a238fc9ff0486bbcc9ecffe85705715"
PROJECTION_OWNERS = (
    ("breadboard_engine/todo/projection.py", "project_store_snapshot_to_tui_envelope"),
    ("breadboard_engine/state/session_state.py", "SessionState"),
    ("breadboard_engine/orchestration/event_log.py", "EventLog"),
)
SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})
HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "route", "websocket"})
TUI_EXCLUDED_PARTS = frozenset({"build", "dist", "generated", "node_modules", "vendor"})
SDK_IMPORT = re.compile(r"['\"]@breadboard/sdk(?:['\"/])")
DURABLE_TOKEN = re.compile(r"\bdurable_reconfigure\b")


def _run(root: Path, *args: str) -> str:
    return subprocess.check_output(args, cwd=root, text=True).strip()


def _tracked(root: Path) -> list[str]:
    raw = subprocess.check_output(("git", "ls-files", "-z"), cwd=root)
    return sorted(item for item in raw.decode("utf-8").split("\0") if item)


def _head(root: Path) -> str:
    return _run(root, "git", "rev-parse", "HEAD")


def _status(root: Path) -> str:
    return _run(root, "git", "status", "--short")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _under(files: Iterable[str], prefix: str) -> list[str]:
    return [path for path in files if path.startswith(prefix)]


def _source_files(files: Iterable[str], prefix: str, *, excluded: frozenset[str] = frozenset()) -> list[str]:
    return [
        path
        for path in files
        if path.startswith(prefix)
        and Path(path).suffix in SOURCE_SUFFIXES
        and not excluded.intersection(Path(path).parts)
    ]


def _http_routes(engine: Path, files: list[str]) -> dict[str, Any]:
    denominator_files = [path for path in files if path.startswith("breadboard_engine/") and path.endswith(".py")]
    scan_files = [
        path
        for path in files
        if path.endswith(".py")
        and path.startswith(("breadboard_engine/api/", "breadboard/product/"))
    ]
    declarations: list[str] = []
    parse_errors: list[str] = []
    for relative in scan_files:
        try:
            tree = ast.parse(_text(engine, relative), filename=relative)
        except SyntaxError:
            parse_errors.append(relative)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                attribute = call.func if call and isinstance(call.func, ast.Attribute) else None
                if attribute and attribute.attr in HTTP_METHODS:
                    declarations.append(f"{relative}:{decorator.lineno}:{attribute.attr}")
    return {
        "count": len(declarations),
        "declarations": sorted(declarations),
        "denominator": len(denominator_files),
        "denominator_files": denominator_files,
        "parse_errors": parse_errors,
        "rule": "AST function decorators whose final attribute is one of HTTP_METHODS; scan only breadboard_engine/api/**/*.py and breadboard/product/**/*.py; denominator is all tracked breadboard_engine/**/*.py",
    }


def _literal_parser_declarations(engine: Path, relative: str) -> dict[str, Any]:
    tree = ast.parse(_text(engine, relative), filename=relative)
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_parser":
            continue
        value = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
        rows.append({"line": node.lineno, "literal": value})
    return {
        "literal_count": sum(row["literal"] is not None for row in rows),
        "dynamic_count": sum(row["literal"] is None for row in rows),
        "declarations": rows,
        "rule": "AST Call with final attribute add_parser; literal_count requires first positional argument to be a string literal",
    }


def _class_string_values(engine: Path, relative: str, class_name: str) -> list[dict[str, Any]]:
    tree = ast.parse(_text(engine, relative), filename=relative)
    rows: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                value_node = child.value
                if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                    rows.append({"line": child.lineno, "value": value_node.value})
    return rows



def _mapping_string_keys(
    engine: Path, relative: str, assignment_name: str
) -> list[str]:
    tree = ast.parse(_text(engine, relative), filename=relative)
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == assignment_name
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, dict) or not all(
                isinstance(item, str) for item in value
            ):
                raise ValueError(f"{relative}:{assignment_name} is not a string-key mapping")
            return sorted(value)
    raise ValueError(f"{relative}:{assignment_name} not found")


def _parser_node_count(parser: Any) -> int:
    import argparse as argparse_module

    children = [
        child
        for action in parser._actions
        if isinstance(action, argparse_module._SubParsersAction)
        for child in action.choices.values()
    ]
    return 1 + sum(_parser_node_count(child) for child in children)


def _enabled_if_count(engine: Path, flag: str) -> int:
    tree = ast.parse(_text(engine, "breadboard/product/cli/main.py"))
    return sum(
        isinstance(node, ast.If)
        and any(
            isinstance(value, ast.Constant) and value.value == flag
            for value in ast.walk(node.test)
        )
        for node in ast.walk(tree)
    )


def _cli_inventory(engine: Path) -> dict[str, Any]:
    import os
    import sys

    flags = (
        "BREADBOARD_LEGACY_ROUTES",
        "BREADBOARD_ENABLE_LOCAL_MIGRATIONS",
        "BREADBOARD_ENABLE_E4_API",
    )
    saved = {flag: os.environ.get(flag) for flag in flags}
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(engine))
        for flag in flags:
            os.environ.pop(flag, None)
        from breadboard.product.cli.main import build_parser

        default_nodes = _parser_node_count(build_parser())
        os.environ["BREADBOARD_ENABLE_E4_API"] = "1"
        e4_nodes = _parser_node_count(build_parser())
    finally:
        sys.path[:] = old_path
        for flag, value in saved.items():
            if value is None:
                os.environ.pop(flag, None)
            else:
                os.environ[flag] = value
    pyproject = _text(engine, "pyproject.toml")
    entrypoint_count = len(
        re.findall(r'(?m)^breadboard\s*=\s*"breadboard\.product\.cli:main"\s*$', pyproject)
    )
    if entrypoint_count != 1:
        raise ValueError("expected one breadboard product CLI entry point")
    return {
        "default_parser_nodes": default_nodes,
        "entrypoint_count": entrypoint_count,
        "default_surface_count": default_nodes + entrypoint_count,
        "legacy_conditional_declaration_count": _enabled_if_count(
            engine, "BREADBOARD_LEGACY_ROUTES"
        ),
        "local_migration_conditional_declaration_count": _enabled_if_count(
            engine, "BREADBOARD_ENABLE_LOCAL_MIGRATIONS"
        ),
        "e4_parser_declaration_count": e4_nodes - default_nodes,
        "main_ast": _literal_parser_declarations(
            engine, "breadboard/product/cli/main.py"
        ),
        "e4_ast": _literal_parser_declarations(
            engine, "breadboard/product/cli/e4.py"
        ),
        "rule": "recursively count the argparse root and every registered subparser with all feature flags absent; add the single exact pyproject breadboard entry point; legacy/local counts are AST If declaration sites for their exact flags; E4 count is the recursive parser-node delta with only BREADBOARD_ENABLE_E4_API=1",
    }

def _python_all(engine: Path) -> list[str]:
    tree = ast.parse(_text(engine, "breadboard_sdk/__init__.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError("breadboard_sdk.__all__ is not a string list")
            return value
    raise ValueError("breadboard_sdk.__all__ not found")


def _tui_inventory(engine: Path, tui: Path, engine_files: list[str], tui_files: list[str]) -> dict[str, Any]:
    roots = (
        (
            "engine:tui_skeleton/src/",
            engine,
            engine_files,
            "tui_skeleton/src/",
            frozenset({".ts", ".tsx"}),
        ),
        (
            "tui:packages/coding-agent/src/",
            tui,
            tui_files,
            "packages/coding-agent/src/",
            frozenset({".ts", ".tsx", ".js", ".jsx"}),
        ),
    )
    result: dict[str, Any] = {}
    for label, root, files, prefix, suffixes in roots:
        source = [
            path
            for path in files
            if path.startswith(prefix)
            and Path(path).suffix in suffixes
            and not TUI_EXCLUDED_PARTS.intersection(Path(path).parts)
        ]
        import_files = [path for path in source if SDK_IMPORT.search(_text(root, path))]
        result[label] = {
            "source_count": len(source),
            "source_files": source,
            "sdk_import_file_count": len(import_files),
            "sdk_import_files": import_files,
        }
    result["rule"] = "tracked .ts/.tsx files under ENGINE tui_skeleton/src; tracked .ts/.tsx/.js/.jsx files under TUI packages/coding-agent/src; exclude any path component in TUI_EXCLUDED_PARTS; count each file once when an exact @breadboard/sdk module literal matches"
    return result


def _durable_reconfigure(engine: Path, tui: Path, engine_files: list[str], tui_files: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    product_roots = (
        "breadboard_engine/",
        "breadboard/",
        "breadboard_sdk/",
        "sdk/",
        "tui_skeleton/",
    )
    for label, root, files in (("engine", engine, engine_files), ("tui", tui, tui_files)):
        for relative in files:
            if Path(relative).suffix not in SOURCE_SUFFIXES:
                continue
            if label == "engine" and not relative.startswith(product_roots):
                continue
            if label == "tui" and not relative.startswith("packages/"):
                continue
            for line_number, line in enumerate(_text(root, relative).splitlines(), 1):
                if not DURABLE_TOKEN.search(line):
                    continue
                stripped = line.strip()
                if re.search(r"\bdef\s+durable_reconfigure\b|^durable_reconfigure\s*:", stripped):
                    kind = "definition"
                elif re.search(r"\bdurable_reconfigure\s*\(", stripped):
                    kind = "invocation"
                elif re.search(r"\bdurable_reconfigure\s+is\s+None\b", stripped):
                    kind = "guard"
                else:
                    kind = "wiring"
                rows.append(
                    {
                        "repository": label,
                        "path": relative,
                        "line": line_number,
                        "kind": kind,
                    }
                )
    return {
        "definition_count": sum(row["kind"] == "definition" for row in rows),
        "invocation_count": sum(row["kind"] == "invocation" for row in rows),
        "guard_count": sum(row["kind"] == "guard" for row in rows),
        "wiring_count": sum(row["kind"] == "wiring" for row in rows),
        "rows": rows,
        "rule": "one row per source line containing the exact word token in tracked product roots; classify def/typed parameter as definition, call as invocation, 'is None' as guard, and remaining references as wiring",
    }


def inventory(engine: Path, tui: Path) -> dict[str, Any]:
    engine = engine.resolve()
    tui = tui.resolve()
    if _head(engine) != ENGINE_HEAD or _head(tui) != TUI_HEAD:
        raise ValueError("pinned head mismatch")
    if _status(engine) or _status(tui):
        raise ValueError("pinned root is not clean")
    engine_files = _tracked(engine)
    tui_files = _tracked(tui)
    operations = json.loads(
        _text(engine, "contracts/public/operations.v2.json")
    )["operations"]
    event_registry = json.loads(
        _text(engine, "contracts/kernel/registries/kernel_event_kinds.v1.json")
    )["entries"]
    tiers = json.loads(
        _text(engine, "contracts/kernel/registries/contract_tiers.v1.json")
    )
    lifecycle = json.loads(
        _text(engine, "contracts/kernel/registries/schema_lifecycle.v1.json")
    )
    kernel_index = _text(
        engine, "sdk/ts-kernel-contracts/src/generated/index.ts"
    )
    event_type_values = _class_string_values(
        engine, "breadboard_engine/api/cli_bridge/events.py", "EventType"
    )
    canonical_runtime = _mapping_string_keys(
        engine,
        "breadboard_engine/state/session_state.py",
        "CANONICAL_KERNEL_EVENT_TYPES",
    )
    projection_only = _mapping_string_keys(
        engine,
        "breadboard_engine/state/session_state.py",
        "PROJECTION_ONLY_RUNTIME_EVENT_TYPES",
    )
    audit_only = _mapping_string_keys(
        engine,
        "breadboard_engine/state/session_state.py",
        "AUDIT_ONLY_RUNTIME_EVENT_TYPES",
    )
    projection_rows = []
    for relative, symbol in PROJECTION_OWNERS:
        text = _text(engine, relative)
        matches = [
            index
            for index, line in enumerate(text.splitlines(), 1)
            if re.search(rf"\b{re.escape(symbol)}\b", line)
        ]
        if not matches:
            raise ValueError(f"projection owner missing: {relative}:{symbol}")
        projection_rows.append(
            {"path": relative, "symbol": symbol, "matching_lines": matches}
        )
    compat_path = "breadboard_sdk/compat.py"
    compat_text = _text(engine, compat_path)
    if not re.search(r"\bclass\s+CompatibilityBreadboardClient\b", compat_text):
        raise ValueError("CompatibilityBreadboardClient missing")
    pyproject = _text(engine, "pyproject.toml")
    python_all = _python_all(engine)
    tui_inventory = _tui_inventory(engine, tui, engine_files, tui_files)
    result = {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "engine_head": _head(engine),
            "engine_tracked_tree": _run(engine, "git", "rev-parse", "HEAD^{tree}"),
            "tui_head": _head(tui),
            "tui_tracked_tree": _run(tui, "git", "rev-parse", "HEAD^{tree}"),
        },
        "methods": {
            "http_methods": sorted(HTTP_METHODS),
            "sdk_import_pattern": SDK_IMPORT.pattern,
            "source_suffixes": sorted(SOURCE_SUFFIXES),
            "tui_excluded_parts": sorted(TUI_EXCLUDED_PARTS),
            "deduplication": "tracked paths once; HTTP decorators once per AST node; durable token once per source line; registry and manifest rows once per array row",
        },
        "inventories": {
            "http_routes": _http_routes(engine, engine_files),
            "public_operations": {
                "count": len(operations),
                "operation_ids": sorted(
                    str(item["bindings"]["openapi"]["operation_id"])
                    for item in operations
                ),
                "rule": "every row in contracts/public/operations.v2.json operations",
            },
            "cli": _cli_inventory(engine),
            "event_kinds": {
                "event_type_value_count": len(event_type_values),
                "event_type_values": event_type_values,
                "canonical_runtime_count": len(canonical_runtime),
                "canonical_runtime_ids": canonical_runtime,
                "projection_only_runtime_count": len(projection_only),
                "projection_only_runtime_ids": projection_only,
                "audit_only_runtime_count": len(audit_only),
                "audit_only_runtime_ids": audit_only,
                "kernel_registry_count": len(event_registry),
                "kernel_registry_ids": sorted(str(item["id"]) for item in event_registry),
                "rule": "EventType string assignments in cli_bridge/events.py; exact keys of the three named session_state mappings; every kernel_event_kinds registry row",
            },
            "projections": {
                "owners": projection_rows,
                "rule": "each exact symbol must occur in its pinned tracked owner path",
            },
            "sdk_exports": {
                "python_all_count": len(python_all),
                "python_all": python_all,
                "typescript_public_operation_count": len(operations),
                "typescript_extra_invoke_count": len(
                    re.findall(
                        r"(?m)^\s*invokePublicAction\s*\(",
                        _text(engine, "sdk/ts/src/public-client.ts"),
                    )
                ),
                "kernel_generated_type_reexport_count": len(
                    re.findall(r"^export type ", kernel_index, re.MULTILINE)
                ),
                "rule": "literal breadboard_sdk.__all__; operation manifest rows; public-client method declarations named invoke; exact generated-index lines beginning 'export type '",
            },
            "tui_consumers": tui_inventory,
            "compatibility": {
                "compatibility_client": compat_path
                + ":CompatibilityBreadboardClient",
                "contract_tier_entry_count": len(tiers["entries"]),
                "schema_lifecycle_entry_count": len(lifecycle["entries"]),
                "nondefault_lifecycle_schema_ids": sorted(
                    str(row["schema_id"])
                    for row in lifecycle["entries"]
                    if not row.get("default_for_generation", False)
                ),
                "rule": "exact compatibility subclass plus every row in the two authoritative registries; nondefault IDs are lifecycle rows with default_for_generation false",
            },
            "repository_surfaces": {
                "config_files": len(_under(engine_files, "config/")),
                "script_files": len(_under(engine_files, "scripts/")),
                "conformance_files": len(_under(engine_files, "conformance/")),
                "test_files": len(_under(engine_files, "tests/")),
                "generated_python_sdk_files": len(
                    _under(engine_files, "breadboard_sdk/generated/")
                ),
                "generated_ts_public_files": len(
                    _under(engine_files, "sdk/ts/src/generated/")
                ),
                "generated_kernel_ts_files": len(
                    _under(engine_files, "sdk/ts-kernel-contracts/src/generated/")
                ),
                "rule": "git ls-files paths with exact prefix; each tracked path once",
            },
            "profiles_and_lanes": {
                "agent_config_files": len(_under(engine_files, "agent_configs/")),
                "agent_config_yaml": sum(
                    path.startswith("agent_configs/")
                    and Path(path).suffix in {".yaml", ".yml"}
                    for path in engine_files
                ),
                "implementation_profiles": len(
                    _under(engine_files, "implementations/profiles/")
                ),
                "e4_lane_files": len(_under(engine_files, "config/e4_lanes/")),
                "e4_lane_yaml": sum(
                    path.startswith("config/e4_lanes/")
                    and Path(path).suffix in {".yaml", ".yml"}
                    for path in engine_files
                ),
                "e4_lane_json": sum(
                    path.startswith("config/e4_lanes/")
                    and Path(path).suffix == ".json"
                    for path in engine_files
                ),
                "rule": "git ls-files exact prefixes; YAML=.yaml/.yml; JSON=.json",
            },
            "omp_adapters": {
                "engine_present": bool(_under(engine_files, ".omp/")),
                "engine_paths": _under(engine_files, ".omp/"),
                "tui_present": bool(_under(tui_files, ".omp/")),
                "tui_count": len(_under(tui_files, ".omp/")),
                "tui_paths": _under(tui_files, ".omp/"),
                "rule": "all git ls-files paths under exact repository-root .omp/ prefix",
            },
            "binaries": {
                "tracked_bb_or_bbh_paths": [
                    path
                    for path in engine_files
                    if Path(path).name in {"bb", "bbh"}
                ],
                "breadboard_entrypoint_count": len(
                    re.findall(
                        r'(?m)^breadboard\s*=\s*"breadboard\.product\.cli:main"\s*$',
                        pyproject,
                    )
                ),
                "bbh_candidate_binding_count": len(operations),
                "rule": "tracked basenames exactly bb/bbh; exact pyproject entrypoint; operation manifest rows",
            },
            "durable_reconfigure": _durable_reconfigure(
                engine, tui, engine_files, tui_files
            ),
        },
        "schema_tiers": {
            "entry_count": len(tiers["entries"]),
            "by_tier_and_disposition": {
                f"{tier}:{disposition}": sum(
                    row.get("tier") == tier
                    and row.get("disposition") == disposition
                    for row in tiers["entries"]
                )
                for tier in sorted(
                    {str(row.get("tier")) for row in tiers["entries"]}
                )
                for disposition in sorted(
                    {str(row.get("disposition")) for row in tiers["entries"]}
                )
            },
            "kernel_schema_files": len(
                [
                    path
                    for path in engine_files
                    if path.startswith("contracts/kernel/schemas/")
                    and path.endswith(".json")
                ]
            ),
            "public_schema_files": len(
                [
                    path
                    for path in engine_files
                    if path.startswith("contracts/public/schemas/")
                    and path.endswith(".json")
                ]
            ),
            "rule": "every contract_tiers entry grouped by exact pair; tracked .json paths under exact schema roots",
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--tui-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--check",
        type=Path,
        help="fail unless generated canonical bytes equal this committed baseline",
    )
    args = parser.parse_args()
    payload = (
        json.dumps(
            inventory(args.engine_root, args.tui_root),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if args.check is not None and args.check.read_text(encoding="utf-8") != payload:
        print(f"FT-06 baseline differs: {args.check}")
        return 1
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
