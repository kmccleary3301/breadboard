#!/usr/bin/env python3
"""Lint the schema lifecycle registry against kernel schema and family census truth."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_FILENAME_SUFFIX = ".schema.json"
SCHEMA_ID_RE = re.compile(r"^bb\.[a-z0-9_.]+\.v[0-9]+$")
VERSION_RE = re.compile(r"\.v(\d+)$")
SHARED_DEFINITION_SCHEMAS = {"bb.kernel.common.v1", "bb.e4.common.v1"}
ADR_ID_RE = re.compile(r"ADR-AV-[1-9][0-9]*")
SOURCE_SUFFIXES = frozenset({".cjs", ".js", ".jsx", ".mjs", ".py", ".sh", ".ts", ".tsx"})
EXCLUDED_SOURCE_ROOTS = frozenset(
    {
        ".git",
        ".pytest_cache",
        "agent_configs",
        "artifacts",
        "config",
        "contracts",
        "docs",
        "docs_tmp",
        "implementations",
        "logging",
        "misc",
        "renderer_assets",
        "tests",
    }
)
EXCLUDED_SOURCE_DIR_NAMES = frozenset(
    {"__pycache__", "build", "dist", "fixtures", "generated", "node_modules", "test", "tests", "tmp"}
)
NON_PRODUCER_SYMBOL_TERMS = (
    "archive",
    "fixture",
    "load",
    "parse",
    "read",
    "schema_map",
    "schema_path",
    "surface_schema_version",
    "supported",
    "validate",
    "validator",
)
PRODUCER_SYMBOL_TERMS = (
    "build",
    "capture",
    "create",
    "default",
    "emit",
    "finalize",
    "generate",
    "produce",
    "promote",
    "scaffold",
    "update",
    "write",
)
SCHEMA_VERSION_KEY_RE = re.compile(
    r"(?:[\"']schema_version[\"']|\bschemaVersion)\s*:\s*[\"'](?P<schema_id>bb\.[a-z0-9_.]+\.v[0-9]+)[\"']"
)

# Literal §4.1 contract: keys are "<file>:<symbol>" and values are accepted
# Phase19 ADR ids. Both accepted exceptions require an existing canonical v2
# claim and an accepted, explicitly bounded lane before reproduction.
LEGACY_PRODUCER_ALLOWLIST: dict[str, str] = {
    "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py:_support_claim_schema_version": "ADR-AV-3",
    "scripts/e4_parity/lane_acceptance_artifacts.py:build_lane": "ADR-AV-2",
}
LEGACY_PRODUCER_GUARDS: dict[str, str] = {
    "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py:_support_claim_schema_version": "_support_claim_schema_version",
    "scripts/e4_parity/lane_acceptance_artifacts.py:build_lane": "_reproduce_existing_accepted_v2",
}
LEGACY_PRODUCER_SCHEMAS: dict[str, str] = {
    "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py:_support_claim_schema_version": "bb.e4.support_claim.v2",
    "scripts/e4_parity/lane_acceptance_artifacts.py:build_lane": "bb.e4.support_claim.v2",
}
ACCEPTED_LEGACY_PRODUCER_ADRS = frozenset(LEGACY_PRODUCER_ALLOWLIST.values())

@dataclass(frozen=True)
class LifecycleDiagnostic:
    path: str
    rule: str
    message: str
    pointer: str = "$"

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "rule": self.rule,
            "pointer": self.pointer,
            "message": self.message,
        }


def _ordered_unique_diagnostics(
    diagnostics: Sequence[LifecycleDiagnostic],
) -> list[LifecycleDiagnostic]:
    unique: list[LifecycleDiagnostic] = []
    seen: set[tuple[str, str, str, str]] = set()
    for diagnostic in diagnostics:
        key = (
            diagnostic.path,
            diagnostic.rule,
            diagnostic.message,
            diagnostic.pointer,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return unique


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _display(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _schema_id_from_path(path: Path) -> str:
    name = path.name
    if not name.endswith(SCHEMA_FILENAME_SUFFIX):
        raise ValueError(f"schema path does not end with {SCHEMA_FILENAME_SUFFIX!r}: {path}")
    return name[: -len(SCHEMA_FILENAME_SUFFIX)]


def _schema_ids(schemas_dir: Path) -> list[str]:
    return sorted(_schema_id_from_path(path) for path in schemas_dir.glob("*.schema.json") if path.is_file())


def _family_owners(kernel_families_path: Path) -> tuple[dict[str, list[str]], list[LifecycleDiagnostic]]:
    rel_path = kernel_families_path.as_posix()
    payload = _load_json(kernel_families_path)
    if not isinstance(payload, Mapping):
        return {}, [LifecycleDiagnostic(rel_path, "kernel_families_shape", "kernel_families root must be an object")]
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {}, [LifecycleDiagnostic(rel_path, "kernel_families_shape", "kernel_families entries must be an array")]
    owners: dict[str, list[str]] = {}
    diagnostics: list[LifecycleDiagnostic] = []
    family_indices: dict[str, list[int]] = {}
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            diagnostics.append(LifecycleDiagnostic(rel_path, "kernel_families_shape", f"entries[{entry_index}] must be an object", f"$/entries/{entry_index}"))
            continue
        family_id = entry.get("id")
        versions = entry.get("metadata", {}).get("schema_versions") if isinstance(entry.get("metadata"), Mapping) else None
        if versions is None:
            continue
        if not isinstance(family_id, str) or not family_id:
            diagnostics.append(LifecycleDiagnostic(rel_path, "kernel_families_shape", f"entries[{entry_index}].id must be non-empty", f"$/entries/{entry_index}/id"))
            continue
        family_indices.setdefault(family_id, []).append(entry_index)
        if not isinstance(versions, list) or not all(isinstance(version, str) for version in versions):
            diagnostics.append(LifecycleDiagnostic(rel_path, "kernel_families_shape", f"entries[{entry_index}].metadata.schema_versions must be a string array", f"$/entries/{entry_index}/metadata/schema_versions"))
            continue
        for version in versions:
            owners.setdefault(version, []).append(family_id)
    for family_id, indices in sorted(family_indices.items()):
        if len(indices) != 1:
            diagnostics.append(
                LifecycleDiagnostic(
                    rel_path,
                    "kernel_families_unique_id",
                    f"kernel_families id {family_id!r} appears {len(indices)} times",
                    "$/entries",
                )
            )
    return owners, diagnostics


def _version_number(schema_id: str) -> int:
    match = VERSION_RE.search(schema_id)
    return int(match.group(1)) if match else -1

@lru_cache(maxsize=None)
def _python_symbol_map(path: Path) -> dict[int, str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.as_posix())
    except (SyntaxError, UnicodeDecodeError):
        return {}
    line_count = len(source.splitlines())
    symbol_by_line = {line_number: "<module>" for line_number in range(1, line_count + 1)}
    assignments: list[tuple[int, int, str]] = []
    scopes: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scopes.append((start, end, node.name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if names:
                assignments.append((start, end, names[0]))
    for start, end, name in sorted(assignments):
        for line_number in range(start, end + 1):
            symbol_by_line[line_number] = name
    for start, end, name in sorted(scopes):
        for line_number in range(start, end + 1):
            symbol_by_line[line_number] = name
    return symbol_by_line


def _python_symbol_at_line(path: Path, line_number: int) -> str:
    return _python_symbol_map(path).get(line_number, "<module>")


def _is_producer_symbol(symbol: str) -> bool:
    lowered = symbol.lower()
    if any(term in lowered for term in NON_PRODUCER_SYMBOL_TERMS):
        return False
    return any(term in lowered for term in PRODUCER_SYMBOL_TERMS)


def _iter_source_paths(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for current_root, dirnames, filenames in os.walk(repo_root, topdown=True):
        current_path = Path(current_root)
        if current_path == repo_root:
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXCLUDED_SOURCE_ROOTS
                and name not in {"agent_ws", "agent_ws_opencode"}
                and not name.startswith(".venv")
            )
        else:
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXCLUDED_SOURCE_DIR_NAMES and not name.startswith(".venv")
            )
        for filename in sorted(filenames):
            path = current_path / filename
            if path.suffix in SOURCE_SUFFIXES:
                paths.append(path)
    return paths


def _bound_schema_ids(tree: ast.AST, schema_ids: set[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str) or value.value not in schema_ids:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = value.value
    return bindings


def _python_producer_references(path: Path, schema_ids: set[str]) -> set[tuple[str, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.as_posix())
    except (SyntaxError, UnicodeDecodeError):
        return set()
    bindings = _bound_schema_ids(tree, schema_ids)
    references: set[tuple[str, int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or key.value not in {"schema_version", "schemaVersion", "support_claim_schema_version"}:
                    continue
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    schema_id = value.value
                elif isinstance(value, ast.Name):
                    schema_id = bindings.get(value.id)
                else:
                    schema_id = None
                if schema_id in schema_ids:
                    line = getattr(value, "lineno", getattr(node, "lineno", 1))
                    references.add((schema_id, line, _python_symbol_at_line(path, line)))
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value in schema_ids:
            line = getattr(node.value, "lineno", getattr(node, "lineno", 1))
            symbol = _python_symbol_at_line(path, line)
            lowered_symbol = symbol.lower()
            is_schema_selector = "schema_version" in lowered_symbol and not any(
                term in lowered_symbol for term in NON_PRODUCER_SYMBOL_TERMS
            )
            if _is_producer_symbol(symbol) or is_schema_selector:
                references.add((node.value.value, line, symbol))
        elif isinstance(node, ast.Call):
            call_name = ""
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            symbol = _python_symbol_at_line(path, getattr(node, "lineno", 1))
            lowered_call_name = call_name.lower()
            call_is_reader = any(term in lowered_call_name for term in NON_PRODUCER_SYMBOL_TERMS)
            call_is_producer = not call_is_reader and (_is_producer_symbol(call_name) or _is_producer_symbol(symbol))
            if call_name == "add_argument":
                option_names = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                call_is_producer = call_is_producer or any("schema" in option and "version" in option for option in option_names)
            if not call_is_producer:
                continue
            values = [*node.args, *(keyword.value for keyword in node.keywords)]
            for value in values:
                if isinstance(value, ast.Constant) and value.value in schema_ids:
                    line = getattr(value, "lineno", getattr(node, "lineno", 1))
                    references.add((value.value, line, symbol))
                elif isinstance(value, ast.Name) and bindings.get(value.id) in schema_ids:
                    line = getattr(value, "lineno", getattr(node, "lineno", 1))
                    references.add((bindings[value.id], line, symbol))
    return references


def _text_producer_references(path: Path, schema_ids: set[str]) -> set[tuple[str, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return set()
    references: set[tuple[str, int, str]] = set()
    for match in SCHEMA_VERSION_KEY_RE.finditer(source):
        schema_id = match.group("schema_id")
        if schema_id not in schema_ids:
            continue
        line = source.count("\n", 0, match.start()) + 1
        references.add((schema_id, line, "<module>"))
    return references


@lru_cache(maxsize=None)
def _allowlist_guard_satisfied(path: Path, allowlist_key: str) -> bool:
    guard_name = LEGACY_PRODUCER_GUARDS.get(allowlist_key)
    if guard_name is None:
        return True
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    except (SyntaxError, UnicodeDecodeError):
        return False
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    guard = functions.get(guard_name)
    producer_symbol = allowlist_key.rsplit(":", 1)[-1]
    producer = functions.get(producer_symbol)
    if guard is None or producer is None:
        return False
    guard_literals = {
        node.value
        for node in ast.walk(guard)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    guard_calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(guard)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    producer_calls = {
        node.func.id
        for node in ast.walk(producer)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    calls_guard = guard_name == producer_symbol or guard_name in producer_calls
    if guard_name == producer_symbol:
        guard_names = {node.id for node in ast.walk(guard) if isinstance(node, ast.Name)}
        calls_guard = (
            calls_guard
            and "schema_generation_default" in guard_calls
            and "ADR_AV_3_ACCEPTED_COMPILER_LANES" in guard_names
        )
    return (
        "accepted" in guard_literals
        and "bb.e4.support_claim.v2" in guard_literals
        and "is_file" in guard_calls
        and calls_guard
    )


def _lint_non_active_producers(
    *,
    repo_root: Path,
    non_active_schema_ids: set[str],
    generation_default_by_schema: Mapping[str, str],
) -> list[LifecycleDiagnostic]:
    diagnostics: list[LifecycleDiagnostic] = []
    for path in _iter_source_paths(repo_root):
        rel_path = _display(path, repo_root)
        if rel_path == "scripts/e4_parity/lint_schema_lifecycle.py":
            continue
        if path.suffix == ".py":
            references = _python_producer_references(path, non_active_schema_ids)
        else:
            references = _text_producer_references(path, non_active_schema_ids)
        for schema_id, line_index, symbol in sorted(references, key=lambda item: (item[1], item[0], item[2])):
            allowlist_key = f"{rel_path}:{symbol}"
            adr_id = LEGACY_PRODUCER_ALLOWLIST.get(allowlist_key)
            allowed_schema_id = LEGACY_PRODUCER_SCHEMAS.get(allowlist_key)
            if (
                isinstance(adr_id, str)
                and ADR_ID_RE.fullmatch(adr_id)
                and adr_id in ACCEPTED_LEGACY_PRODUCER_ADRS
                and schema_id == allowed_schema_id
                and _allowlist_guard_satisfied(path, allowlist_key)
            ):
                continue
            generation_default = generation_default_by_schema.get(schema_id, "the family generation default")
            diagnostics.append(
                LifecycleDiagnostic(
                    rel_path,
                    "producer_conformance",
                    f"{schema_id} is non-active at {symbol!r}; active producers must emit {generation_default}",
                    f"line:{line_index}",
                )
            )
    return diagnostics


def lint_schema_lifecycle(
    *,
    repo_root: Path,
    registry_path: Path | None = None,
    schemas_dir: Path | None = None,
    kernel_families_path: Path | None = None,
) -> list[LifecycleDiagnostic]:
    repo_root = repo_root.resolve()
    registry_path = registry_path or repo_root / "contracts/kernel/registries/schema_lifecycle.v1.json"
    schemas_dir = schemas_dir or repo_root / "contracts/kernel/schemas"
    kernel_families_path = kernel_families_path or repo_root / "contracts/kernel/registries/kernel_families.v1.json"

    registry_rel = _display(registry_path, repo_root)
    kernel_rel = _display(kernel_families_path, repo_root)
    diagnostics: list[LifecycleDiagnostic] = []

    schema_ids = _schema_ids(schemas_dir)
    schema_id_set = set(schema_ids)
    owners, owner_diagnostics = _family_owners(kernel_families_path)
    diagnostics.extend(
        LifecycleDiagnostic(_display(kernel_families_path, repo_root), diag.rule, diag.message, diag.pointer)
        for diag in owner_diagnostics
    )

    payload = _load_json(registry_path)
    if not isinstance(payload, Mapping):
        return [LifecycleDiagnostic(registry_rel, "registry_shape", "schema_lifecycle root must be an object")]
    if payload.get("schema_version") != "bb.schema_lifecycle.v1":
        diagnostics.append(LifecycleDiagnostic(registry_rel, "registry_shape", "schema_version must be bb.schema_lifecycle.v1", "$/schema_version"))
    if payload.get("registry_id") != "schema_lifecycle":
        diagnostics.append(LifecycleDiagnostic(registry_rel, "registry_shape", "registry_id must be schema_lifecycle", "$/registry_id"))
    entries = payload.get("entries")
    if not isinstance(entries, list):
        diagnostics.append(LifecycleDiagnostic(registry_rel, "registry_shape", "entries must be an array", "$/entries"))
        return diagnostics

    entries_by_schema: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    families: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, entry in enumerate(entries):
        pointer = f"$/entries/{index}"
        if not isinstance(entry, Mapping):
            diagnostics.append(LifecycleDiagnostic(registry_rel, "entry_shape", f"entries[{index}] must be an object", pointer))
            continue
        schema_id = entry.get("schema_id")
        family = entry.get("family")
        lifecycle = entry.get("lifecycle")
        default = entry.get("default_for_generation")
        superseded_by = entry.get("superseded_by")
        if not isinstance(schema_id, str) or not SCHEMA_ID_RE.fullmatch(schema_id):
            diagnostics.append(LifecycleDiagnostic(registry_rel, "entry_shape", f"entries[{index}].schema_id is invalid", f"{pointer}/schema_id"))
            continue
        if schema_id not in schema_id_set:
            diagnostics.append(LifecycleDiagnostic(registry_rel, "totality", f"lifecycle lists {schema_id} but no matching schema file exists", f"{pointer}/schema_id"))
        if not isinstance(family, str) or not family:
            diagnostics.append(LifecycleDiagnostic(registry_rel, "entry_shape", f"entries[{index}].family must be non-empty", f"{pointer}/family"))
        if lifecycle not in {"active_production", "validate_only", "frozen_accepted_evidence", "deprecated_no_consumers"}:
            diagnostics.append(LifecycleDiagnostic(registry_rel, "entry_shape", f"entries[{index}].lifecycle is invalid", f"{pointer}/lifecycle"))
        if not isinstance(default, bool):
            diagnostics.append(LifecycleDiagnostic(registry_rel, "entry_shape", f"entries[{index}].default_for_generation must be boolean", f"{pointer}/default_for_generation"))
        if superseded_by is not None and not (isinstance(superseded_by, str) and SCHEMA_ID_RE.fullmatch(superseded_by)):
            diagnostics.append(LifecycleDiagnostic(registry_rel, "entry_shape", f"entries[{index}].superseded_by is invalid", f"{pointer}/superseded_by"))
        entries_by_schema.setdefault(schema_id, []).append((index, entry))
        if isinstance(family, str) and family:
            families.setdefault(family, []).append((index, entry))
        if schema_id not in SHARED_DEFINITION_SCHEMAS:
            owner = owners.get(schema_id, [])
            if len(owner) != 1:
                diagnostics.append(LifecycleDiagnostic(kernel_rel, "family_census", f"{schema_id} must have exactly one kernel_families owner, found {owner}", "$"))
            elif family != owner[0]:
                diagnostics.append(LifecycleDiagnostic(registry_rel, "family_census", f"{schema_id} lifecycle family {family!r} does not match kernel_families owner {owner[0]!r}", f"{pointer}/family"))

    missing = sorted(schema_id_set - set(entries_by_schema))
    if missing:
        diagnostics.append(LifecycleDiagnostic(registry_rel, "totality", f"schema_lifecycle missing schema(s): {', '.join(missing)}", "$/entries"))
    for schema_id, found in sorted(entries_by_schema.items()):
        if len(found) != 1:
            diagnostics.append(LifecycleDiagnostic(registry_rel, "totality", f"{schema_id} appears {len(found)} times in schema_lifecycle", "$/entries"))

    generation_default_by_schema: dict[str, str] = {}
    for family, family_entries in sorted(families.items()):
        defaults = [(index, entry) for index, entry in family_entries if entry.get("default_for_generation") is True]
        if len(defaults) != 1:
            diagnostics.append(LifecycleDiagnostic(registry_rel, "default_per_family", f"family {family} has {len(defaults)} defaults; expected exactly one", "$/entries"))
            continue
        default_index, default_entry = defaults[0]
        default_schema_id = default_entry.get("schema_id")
        if default_entry.get("lifecycle") != "active_production":
            diagnostics.append(LifecycleDiagnostic(registry_rel, "default_per_family", f"family {family} default must be active_production", f"$/entries/{default_index}/lifecycle"))
        if not isinstance(default_schema_id, str):
            continue
        family_by_schema = {
            str(entry.get("schema_id")): (index, entry)
            for index, entry in family_entries
            if isinstance(entry.get("schema_id"), str)
        }
        for schema_id in family_by_schema:
            generation_default_by_schema[schema_id] = default_schema_id
        if len(family_by_schema) < 2:
            continue
        if default_entry.get("superseded_by") is not None:
            diagnostics.append(
                LifecycleDiagnostic(
                    registry_rel,
                    "supersession",
                    f"family {family} generation default {default_schema_id} must terminate the supersession chain",
                    f"$/entries/{default_index}/superseded_by",
                )
            )
        for schema_id, (index, entry) in sorted(family_by_schema.items()):
            if schema_id == default_schema_id:
                continue
            pointer = f"$/entries/{index}/superseded_by"
            superseded_by = entry.get("superseded_by")
            if not isinstance(superseded_by, str) or superseded_by not in family_by_schema:
                diagnostics.append(
                    LifecycleDiagnostic(
                        registry_rel,
                        "supersession",
                        f"{schema_id} must supersede to an existing schema in family {family}",
                        pointer,
                    )
                )
                continue
            if _version_number(superseded_by) <= _version_number(schema_id):
                diagnostics.append(
                    LifecycleDiagnostic(
                        registry_rel,
                        "supersession",
                        f"{schema_id} supersession edge to {superseded_by} is not forward-only",
                        pointer,
                    )
                )
            visited: set[str] = set()
            current = schema_id
            while current != default_schema_id:
                if current in visited:
                    diagnostics.append(
                        LifecycleDiagnostic(
                            registry_rel,
                            "supersession",
                            f"family {family} supersession chain from {schema_id} contains a cycle at {current}",
                            pointer,
                        )
                    )
                    break
                visited.add(current)
                current_entry = family_by_schema.get(current)
                next_schema_id = current_entry[1].get("superseded_by") if current_entry else None
                if not isinstance(next_schema_id, str) or next_schema_id not in family_by_schema:
                    diagnostics.append(
                        LifecycleDiagnostic(
                            registry_rel,
                            "supersession",
                            f"family {family} supersession chain from {schema_id} does not terminate at generation default {default_schema_id}",
                            pointer,
                        )
                    )
                    break
                current = next_schema_id

    non_active_schema_ids = {
        str(entry.get("schema_id"))
        for family_entries in families.values()
        for _, entry in family_entries
        if entry.get("lifecycle") in {"validate_only", "frozen_accepted_evidence"}
        and isinstance(entry.get("schema_id"), str)
    }
    diagnostics.extend(
        _lint_non_active_producers(
            repo_root=repo_root,
            non_active_schema_ids=non_active_schema_ids,
            generation_default_by_schema=generation_default_by_schema,
        )
    )

    return _ordered_unique_diagnostics(diagnostics)


def _format_text(diagnostics: Sequence[LifecycleDiagnostic]) -> str:
    if not diagnostics:
        return "schema lifecycle lint passed"
    return "\n".join(f"{diag.path}:{diag.pointer}: {diag.rule}: {diag.message}" for diag in diagnostics)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--registry", default=None, help="schema_lifecycle registry path")
    parser.add_argument("--schemas-dir", default=None, help="Schema directory")
    parser.add_argument("--kernel-families", default=None, help="kernel_families registry path")
    parser.add_argument("--json", action="store_true", help="Emit JSON diagnostics")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    diagnostics = lint_schema_lifecycle(
        repo_root=Path(args.repo_root),
        registry_path=Path(args.registry) if args.registry else None,
        schemas_dir=Path(args.schemas_dir) if args.schemas_dir else None,
        kernel_families_path=Path(args.kernel_families) if args.kernel_families else None,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not diagnostics,
                    "diagnostic_count": len(diagnostics),
                    "diagnostics": [diagnostic.as_dict() for diagnostic in diagnostics],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_format_text(diagnostics))
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
