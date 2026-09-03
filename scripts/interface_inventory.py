#!/usr/bin/env python3
"""Build a deterministic, advisory inventory of the ENGINE/TUI contract surface.

The registries under ``contracts/kernel/registries`` and source declarations remain
authoritative.  This script only reports what a static, repeatable scan can see; it
never validates or gates a merge.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VERSION = 1
SCRIPT_ID = "scripts/interface_inventory.py"

# Vendored/generated/build trees are either another authority or not a source tree
# at all.  They are listed in the output so the boundary is inspectable.
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "generated",
        "target",
        ".next",
    }
)
PY_SUFFIXES = frozenset({".py"})
TS_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts", ".cts"})
TEXT_SUFFIXES = PY_SUFFIXES | TS_SUFFIXES


def _root(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{label} is not a directory: {value}")
    return path


def _relative(path: Path, root: Path, label: str) -> str:
    return f"{label}/{path.relative_to(root).as_posix()}"


def _iter_files(root: Path, suffixes: Iterable[str]) -> list[Path]:
    allowed = frozenset(suffixes)
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix not in allowed:
            continue
        if any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _entry_identifier(entry: Mapping[str, Any]) -> tuple[str, str]:
    for field in ("id", "schema_id", "family"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return field, value
    raise ValueError(f"registry entry has no identifier field: {entry!r}")


def _registry_entry(entry: Mapping[str, Any], registry_id: str, path: str) -> dict[str, Any]:
    identifier_field, identifier = _entry_identifier(entry)
    row: dict[str, Any] = {
        "entry_id": identifier,
        "identifier_field": identifier_field,
        "registry_id": registry_id,
        "registry_path": path,
    }
    for field in (
        "status",
        "lifecycle",
        "family",
        "schema_id",
        "tier",
        "disposition",
        "superseded_by",
        "default_for_generation",
    ):
        if field in entry:
            row[field] = entry[field]
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        for field in (
            "actor",
            "classification",
            "consumer",
            "generation_policy",
            "impl",
            "kernel_family",
            "kernel_truth",
            "payload_schema_version",
            "projection_family",
            "schema_versions",
            "source",
            "visibility",
        ):
            if field in metadata:
                row[field] = metadata[field]
    consumers = entry.get("consumers")
    if isinstance(consumers, list):
        row["consumers"] = sorted(
            [
                {
                    key: value
                    for key, value in consumer.items()
                    if key in {"kind", "path"} and isinstance(value, str)
                }
                for consumer in consumers
                if isinstance(consumer, dict)
            ],
            key=lambda item: (item.get("path", ""), item.get("kind", "")),
        )
    return row


def _registries(engine_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry_root = engine_root / "contracts" / "kernel" / "registries"
    if not registry_root.is_dir():
        raise ValueError(f"missing registry root: {registry_root}")
    registry_rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for path in sorted(registry_root.glob("*.json")):
        document = _load_json(path)
        registry_id = document.get("registry_id")
        if not isinstance(registry_id, str) or not registry_id:
            registry_id = path.name.removesuffix(".v1.json")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError(f"registry entries must be an array: {path}")
        relative_path = _relative(path, engine_root, "engine_root")
        rows = [
            _registry_entry(entry, registry_id, relative_path)
            for entry in raw_entries
            if isinstance(entry, dict)
        ]
        if len(rows) != len(raw_entries):
            raise ValueError(f"registry entries must be objects: {path}")
        rows.sort(key=lambda item: str(item["entry_id"]))
        registry_rows.append(
            {
                "registry_id": registry_id,
                "path": relative_path,
                "entry_count": len(rows),
                "entries": rows,
            }
        )
        entries.extend(rows)
    registry_rows.sort(key=lambda item: str(item["registry_id"]))
    entries.sort(key=lambda item: (str(item["registry_id"]), str(item["entry_id"])))
    return registry_rows, entries


def _schema_id(path: Path, document: Mapping[str, Any]) -> str:
    value = document.get("$id")
    if isinstance(value, str) and value:
        candidate = value.rsplit("/", 1)[-1]
        if candidate.endswith(".schema.json"):
            return candidate.removesuffix(".schema.json")
    return path.name.removesuffix(".schema.json")


def _schema_rows(
    engine_root: Path,
    registry_entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    lifecycle = {
        str(row["schema_id"]): row
        for row in registry_entries
        if row.get("registry_id") == "schema_lifecycle" and isinstance(row.get("schema_id"), str)
    }
    tiers = {
        str(row["schema_id"]): row
        for row in registry_entries
        if row.get("registry_id") == "contract_tiers" and isinstance(row.get("schema_id"), str)
    }
    schema_roots = (
        (engine_root / "contracts" / "kernel" / "schemas", "kernel"),
        (engine_root / "contracts" / "kernel" / "schemas" / "payloads", "kernel_payload"),
        (engine_root / "contracts" / "public" / "schemas", "public"),
    )
    rows: list[dict[str, Any]] = []
    for root, domain in schema_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.schema.json")):
            document = _load_json(path)
            identifier = _schema_id(path, document)
            properties = document.get("properties")
            required = document.get("required")
            row: dict[str, Any] = {
                "id": identifier,
                "domain": domain,
                "path": _relative(path, engine_root, "engine_root"),
                "fields": sorted(
                    key for key in properties if isinstance(key, str)
                )
                if isinstance(properties, dict)
                else [],
                "required": sorted(item for item in required if isinstance(item, str))
                if isinstance(required, list)
                else [],
            }
            lifecycle_row = lifecycle.get(identifier)
            if lifecycle_row is not None:
                for field in ("family", "lifecycle", "default_for_generation", "superseded_by"):
                    if field in lifecycle_row:
                        row[field] = lifecycle_row[field]
            tier_row = tiers.get(identifier)
            if tier_row is not None:
                for field in ("tier", "disposition", "superseded_by"):
                    if field in tier_row and field not in row:
                        row[field] = tier_row[field]
            rows.append(row)
    return sorted(rows, key=lambda item: (str(item["domain"]), str(item["id"])))


def _parse_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _python_declarations(path: Path) -> list[dict[str, str]]:
    tree = _parse_python(path)
    if tree is None:
        return []
    declarations: list[dict[str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                declarations.append(
                    {
                        "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                        "name": node.name,
                    }
                )
    return sorted(declarations, key=lambda item: (item["kind"], item["name"]))


def _module_path_from_reference(reference: str) -> str | None:
    module = reference.split(":", 1)[0].strip()
    if not module:
        return None
    if module.endswith(".py"):
        return module
    if "/" in module:
        return f"{module}.py"
    return f"{module.replace('.', '/')}.py"


def _owner_rows(engine_root: Path, registry_entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    references: dict[str, set[str]] = defaultdict(set)
    for row in registry_entries:
        registry_id = str(row.get("registry_id", ""))
        for consumer in row.get("consumers", []):
            if isinstance(consumer, dict) and isinstance(consumer.get("path"), str):
                references[f"engine_root/{consumer['path']}"].add(f"{registry_id}:consumer")
        consumer = row.get("consumer")
        if isinstance(consumer, str):
            module_path = _module_path_from_reference(consumer)
            if module_path:
                references[f"engine_root/{module_path}"].add(f"{registry_id}:consumer")
        implementation = row.get("impl")
        if isinstance(implementation, str):
            module_path = _module_path_from_reference(implementation)
            if module_path:
                references[f"engine_root/{module_path}"].add(f"{registry_id}:impl")
        if registry_id == "kernel_event_kinds":
            references["engine_root/breadboard_engine/api/cli_bridge/events.py"].add(
                "kernel_event_kinds:runtime-event-owner"
            )

    explicit_paths = (
        "engine_root/breadboard_engine/api/cli_bridge/service.py",
        "engine_root/breadboard_engine/api/cli_bridge/session_runner.py",
        "engine_root/breadboard_engine/api/cli_bridge/registry/records.py",
        "engine_root/breadboard_engine/state/session_state.py",
        "engine_root/breadboard_engine/todo/projection.py",
        "engine_root/breadboard/product/runtime/events.py",
        "engine_root/breadboard/product/coordination/views.py",
        "engine_root/breadboard/rl/export/projection.py",
    )
    for path in explicit_paths:
        references[path].add("campaign-owner-root")

    rows: list[dict[str, Any]] = []
    for display_path in sorted(references):
        if not display_path.startswith("engine_root/"):
            continue
        source_path = engine_root / display_path.removeprefix("engine_root/")
        row: dict[str, Any] = {
            "path": display_path,
            "exists": source_path.is_file(),
            "evidence": sorted(references[display_path]),
            "symbols": _python_declarations(source_path) if source_path.is_file() else [],
        }
        rows.append(row)
    return rows


def _event_code_rows(engine_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in _iter_files(engine_root / "breadboard_engine", PY_SUFFIXES):
        tree = _parse_python(path)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name not in {"EventType", "EventKind"}:
                continue
            for member in node.body:
                targets: list[ast.expr] = []
                value: ast.expr | None = None
                if isinstance(member, ast.Assign):
                    targets = member.targets
                    value = member.value
                elif isinstance(member, ast.AnnAssign):
                    targets = [member.target]
                    value = member.value
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                for target in targets:
                    if isinstance(target, ast.Name):
                        rows.append(
                            {
                                "id": value.value,
                                "owner_path": _relative(path, engine_root, "engine_root"),
                                "owner_symbol": f"{node.name}.{target.id}",
                            }
                        )
    return sorted(rows, key=lambda item: (item["id"], item["owner_path"], item["owner_symbol"]))


def _event_rows(
    registry_entries: Sequence[Mapping[str, Any]], engine_root: Path
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in registry_entries:
        if entry.get("registry_id") != "kernel_event_kinds":
            continue
        row = {"id": entry["entry_id"], "source": "authoritative-registry"}
        for field in (
            "registry_path",
            "status",
            "kernel_family",
            "projection_family",
            "payload_schema_version",
            "kernel_truth",
        ):
            if field in entry:
                row[field] = entry[field]
        rows.append(row)
    rows.extend(
        {"id": row["id"], "source": "engine-runtime-declaration", **row}
        for row in _event_code_rows(engine_root)
    )
    return sorted(
        rows,
        key=lambda item: (
            str(item.get("id", "")),
            str(item.get("source", "")),
            str(item.get("owner_path", "")),
            str(item.get("owner_symbol", "")),
        ),
    )


def _python_exports(path: Path) -> list[dict[str, str]]:
    tree = _parse_python(path)
    if tree is None:
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            return []
        names = [item.value for item in node.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        return [{"name": name, "kind": "python", "path": ""} for name in sorted(set(names))]
    return []


def _ts_export_names(path: Path) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    found: dict[str, str] = {}
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export {") or stripped.startswith("export type {"):
            in_block = True
            stripped = stripped[stripped.find("{") + 1 :]
        if in_block:
            body = stripped.split("}", 1)[0]
            for fragment in body.split(","):
                fragment = fragment.strip()
                if not fragment:
                    continue
                fragment = fragment.split("//", 1)[0].strip()
                parts = re.split(r"\s+as\s+", fragment)
                name = parts[-1].strip()
                if name.startswith("type "):
                    name = name.removeprefix("type ").strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", name):
                    found[name] = "typescript-re-export"
            if "}" in stripped:
                in_block = False
            continue
        match = re.match(
            r"export\s+(?:declare\s+)?(?:async\s+)?(class|function|const|let|var|type|interface|enum)\s+([A-Za-z_$][\w$]*)",
            stripped,
        )
        if match:
            found[match.group(2)] = f"typescript-{match.group(1)}"
    return [{"name": name, "kind": kind, "path": ""} for name, kind in sorted(found.items())]


def _generated_manifests(engine_root: Path) -> list[dict[str, Any]]:
    roots = (
        engine_root / "breadboard_sdk" / "generated",
        engine_root / "sdk" / "ts" / "src" / "generated",
        engine_root / "tui_skeleton" / "src" / "generated",
    )
    rows: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*manifest*.json")):
            document = _load_json(path)
            operations = document.get("operations")
            operation_ids = sorted(
                str(row.get("operation_id"))
                for row in operations
                if isinstance(row, dict) and isinstance(row.get("operation_id"), str)
            ) if isinstance(operations, list) else []
            rows.append(
                {
                    "path": _relative(path, engine_root, "engine_root"),
                    "operation_count": len(operation_ids),
                    "operation_ids": operation_ids,
                }
            )
    return sorted(rows, key=lambda item: str(item["path"]))


def _sdk_exports(engine_root: Path) -> dict[str, Any]:
    python_root = engine_root / "breadboard_sdk" / "__init__.py"
    ts_root = engine_root / "sdk" / "ts" / "src" / "index.ts"
    python_rows = _python_exports(python_root) if python_root.is_file() else []
    ts_rows = _ts_export_names(ts_root) if ts_root.is_file() else []
    for row in python_rows:
        row["path"] = _relative(python_root, engine_root, "engine_root")
    for row in ts_rows:
        row["path"] = _relative(ts_root, engine_root, "engine_root")
    return {
        "python": {
            "root": _relative(python_root, engine_root, "engine_root") if python_root.is_file() else None,
            "exports": python_rows,
            "export_count": len(python_rows),
        },
        "typescript": {
            "root": _relative(ts_root, engine_root, "engine_root") if ts_root.is_file() else None,
            "exports": ts_rows,
            "export_count": len(ts_rows),
        },
        "generated_manifests": _generated_manifests(engine_root),
    }


def _consumer_roots(engine_root: Path, tui_root: Path) -> list[tuple[str, Path, str]]:
    return [
        ("engine-tui-skeleton", engine_root / "tui_skeleton" / "src", "engine_root"),
        ("paired-coding-agent", tui_root / "packages" / "coding-agent" / "src", "tui_root"),
        ("paired-agent", tui_root / "packages" / "agent" / "src", "tui_root"),
        ("paired-tui", tui_root / "packages" / "tui" / "src", "tui_root"),
    ]


AMBIGUOUS_EVENT_IDS = frozenset({"error", "warning"})
SDK_MODULE_RE = re.compile(r"^@breadboard/sdk(?:/|$)")


def _event_pattern(token: str) -> re.Pattern[str]:
    quoted = rf"""['"`]{re.escape(token)}['"`]"""
    if token not in AMBIGUOUS_EVENT_IDS:
        return re.compile(quoted)
    discriminator = (
        r"(?:event|event_data|eventData|message|payload)"
        r"(?:\s*\.\s*(?:type|kind|event_type|eventType|event_kind|eventKind)"
        r"|\s*\[\s*['\"](?:type|kind|event_type|eventType|event_kind|eventKind)['\"]\s*\])"
    )
    return re.compile(
        rf"(?:"
        rf"\b(?:type|kind|event|event_type|eventType|event_kind|eventKind)\s*[:=]\s*{quoted}"
        rf"|"
        rf"\b{discriminator}\s*(?:===|!==|==|!=)\s*{quoted}"
        rf"|"
        rf"\b(?:type|kind|event_type|eventType|event_kind|eventKind)\s*"
        rf"(?:===|!==|==|!=)\s*{quoted}"
        rf")"
    )


_EVENT_OBJECT_NAMES = frozenset({"event", "event_data", "eventData", "message", "payload"})
_EVENT_DISCRIMINATOR_NAMES = frozenset(
    {"type", "kind", "event_type", "eventType", "event_kind", "eventKind"}
)


def _lexical_tokens(text: str) -> list[tuple[str, str]]:
    """Tokenize enough TypeScript syntax to balance switch braces safely."""
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline == -1 else newline + 1
            continue
        if text.startswith("/*", index):
            comment_end = text.find("*/", index + 2)
            index = len(text) if comment_end == -1 else comment_end + 2
            continue
        if character in "'\"`":
            quote = character
            index += 1
            value: list[str] = []
            while index < len(text):
                character = text[index]
                if character == "\\" and index + 1 < len(text):
                    value.append(text[index + 1])
                    index += 2
                    continue
                if character == quote:
                    index += 1
                    break
                value.append(character)
                index += 1
            tokens.append(("string", "".join(value)))
            continue
        if character.isalnum() or character in "_$":
            start = index
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] in "_$"):
                index += 1
            tokens.append(("identifier", text[start:index]))
            continue
        tokens.append(("punctuation", character))
        index += 1
    return tokens


def _token_is(tokens: Sequence[tuple[str, str]], index: int, kind: str, value: str) -> bool:
    return index < len(tokens) and tokens[index] == (kind, value)


def _token_kind(tokens: Sequence[tuple[str, str]], index: int, kind: str) -> bool:
    return index < len(tokens) and tokens[index][0] == kind


def _event_discriminator_end(tokens: Sequence[tuple[str, str]], index: int) -> int | None:
    if not _token_kind(tokens, index, "identifier"):
        return None
    if tokens[index][1] not in _EVENT_OBJECT_NAMES:
        return None
    if _token_is(tokens, index + 1, "punctuation", ".") and _token_kind(
        tokens, index + 2, "identifier"
    ):
        if tokens[index + 2][1] in _EVENT_DISCRIMINATOR_NAMES:
            return index + 3
        return None
    if (
        _token_is(tokens, index + 1, "punctuation", "[")
        and _token_kind(tokens, index + 2, "string")
        and tokens[index + 2][1] in _EVENT_DISCRIMINATOR_NAMES
        and _token_is(tokens, index + 3, "punctuation", "]")
    ):
        return index + 4
    return None
def _event_discriminator_aliases(
    tokens: Sequence[tuple[str, str]],
) -> set[str]:
    aliases: set[str] = set()
    for index in range(len(tokens) - 3):
        if (
            tokens[index] not in {("identifier", "const"), ("identifier", "let")}
            or not _token_kind(tokens, index + 1, "identifier")
            or not _token_is(tokens, index + 2, "punctuation", "=")
        ):
            continue
        source_index = index + 3
        wrapped = (
            _token_is(tokens, source_index, "identifier", "String")
            and _token_is(tokens, source_index + 1, "punctuation", "(")
        )
        if wrapped:
            source_index += 2
        source_end = _event_discriminator_end(tokens, source_index)
        if source_end is None:
            continue
        if wrapped and not _token_is(tokens, source_end, "punctuation", ")"):
            continue
        aliases.add(tokens[index + 1][1])
    return aliases




def _switch_case_event_matches(text: str, event_tokens: set[str]) -> set[str]:
    wanted = AMBIGUOUS_EVENT_IDS.intersection(event_tokens)
    if not wanted:
        return set()
    tokens = _lexical_tokens(text)
    aliases = _event_discriminator_aliases(tokens)
    matches: set[str] = set()
    for index, (kind, value) in enumerate(tokens):
        if kind != "identifier" or value != "switch":
            continue
        if not _token_is(tokens, index + 1, "punctuation", "("):
            continue
        discriminator_end = (
            index + 3
            if _token_kind(tokens, index + 2, "identifier")
            and tokens[index + 2][1] in aliases
            else _event_discriminator_end(tokens, index + 2)
        )
        if discriminator_end is None or not _token_is(
            tokens, discriminator_end, "punctuation", ")"
        ):
            continue
        body_start = discriminator_end + 1
        if not _token_is(tokens, body_start, "punctuation", "{"):
            continue
        depth = 1
        cursor = body_start + 1
        while cursor < len(tokens) and depth:
            token_kind, token_value = tokens[cursor]
            if token_kind == "punctuation" and token_value == "{":
                depth += 1
            elif token_kind == "punctuation" and token_value == "}":
                depth -= 1
            elif depth == 1 and token_kind == "identifier" and token_value == "case":
                label_index = cursor + 1
                if (
                    _token_kind(tokens, label_index, "string")
                    and tokens[label_index][1] in wanted
                    and _token_is(tokens, label_index + 1, "punctuation", ":")
                ):
                    matches.add(tokens[label_index][1])
                    if matches == wanted:
                        return matches
            cursor += 1
    return matches


def _sdk_imported_exports(text: str, export_names: set[str]) -> set[str]:
    imported: set[str] = set()
    import_pattern = re.compile(
        r"\b(?:import|export)\s+(?:type\s+)?\{(?P<body>.*?)\}\s+from\s+"
        r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)",
        re.DOTALL,
    )
    for match in import_pattern.finditer(text):
        if not SDK_MODULE_RE.match(match.group("module")):
            continue
        for fragment in match.group("body").split(","):
            fragment = fragment.split("//", 1)[0].strip()
            if not fragment:
                continue
            imported_name = re.split(r"\s+as\s+", fragment)[0].strip()
            imported_name = imported_name.removeprefix("type ").strip()
            if imported_name in export_names:
                imported.add(imported_name)
    namespace_pattern = re.compile(
        r"\bimport\s+\*\s+as\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from\s+"
        r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)"
    )
    for match in namespace_pattern.finditer(text):
        if SDK_MODULE_RE.match(match.group("module")):
            alias = re.escape(match.group("alias"))
            imported.update(
                name for name in export_names if re.search(rf"\b{alias}\.{re.escape(name)}\b", text)
            )
    return imported


def _tui_consumers(
    engine_root: Path,
    tui_root: Path,
    event_rows: Sequence[Mapping[str, Any]],
    schema_rows: Sequence[Mapping[str, Any]],
    sdk_exports: Mapping[str, Any],
) -> dict[str, Any]:
    event_tokens = {
        str(row["id"]) for row in event_rows if isinstance(row.get("id"), str)
    }
    schema_tokens = {
        str(row["id"]) for row in schema_rows if isinstance(row.get("id"), str)
    }
    sdk_export_names = {
        str(row["name"])
        for row in sdk_exports["typescript"]["exports"]
        if isinstance(row.get("name"), str)
    }
    schema_patterns = {
        token: re.compile(rf"""['"`]{re.escape(token)}['"`]""")
        for token in schema_tokens
    }
    event_patterns = {token: _event_pattern(token) for token in event_tokens}
    roots: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for root_id, root, label in _consumer_roots(engine_root, tui_root):
        root_row: dict[str, Any] = {
            "root_id": root_id,
            "root": _relative(root, engine_root if label == "engine_root" else tui_root, label)
            if root.exists()
            else f"{label}/{root.relative_to(engine_root if label == 'engine_root' else tui_root).as_posix()}",
            "exists": root.is_dir(),
            "files_scanned": 0,
            "consumer_count": 0,
        }
        if root.is_dir():
            for path in _iter_files(root, TS_SUFFIXES):
                root_row["files_scanned"] += 1
                text = path.read_text(encoding="utf-8", errors="replace")
                imported_exports = _sdk_imported_exports(text, sdk_export_names)
                switch_events = _switch_case_event_matches(text, event_tokens)
                matched_events = {
                    token for token, pattern in event_patterns.items() if pattern.search(text)
                } | switch_events
                matched_schemas = {
                    token for token, pattern in schema_patterns.items() if pattern.search(text)
                }
                matched = sorted(matched_events | matched_schemas | imported_exports)
                if not matched:
                    continue
                signals = sorted(
                    {
                        "event-kind"
                        if token in event_tokens
                        else "schema"
                        if token in schema_tokens
                        else "sdk-export"
                        for token in matched
                    }
                )
                files.append(
                    {
                        "root_id": root_id,
                        "path": _relative(path, engine_root if label == "engine_root" else tui_root, label),
                        "signals": signals,
                        "matched_tokens": matched,
                    }
                )
                root_row["consumer_count"] += 1
        roots.append(root_row)
    files.sort(key=lambda item: (str(item["root_id"]), str(item["path"])))
    roots.sort(key=lambda item: str(item["root_id"]))
    return {"roots": roots, "files": files, "consumer_count": len(files)}


def _public_catalogs(engine_root: Path) -> list[dict[str, Any]]:
    public_root = engine_root / "contracts" / "public"
    rows: list[dict[str, Any]] = []
    for path in sorted(public_root.glob("*.json")):
        if path.name in {"frozen_public_surface.v1.json"} or path.name.startswith("operations"):
            document = _load_json(path)
            operations = document.get("operations")
            if isinstance(operations, list):
                operation_ids = sorted(
                    str(row["operation_id"])
                    for row in operations
                    if isinstance(row, dict) and isinstance(row.get("operation_id"), str)
                )
            else:
                canonical = document.get("canonical_operations")
                operation_ids = (
                    sorted(
                        str(operation_id)
                        for group in canonical.values()
                        if isinstance(group, list)
                        for operation_id in group
                        if isinstance(operation_id, str)
                    )
                    if isinstance(canonical, dict)
                    else []
                )
            rows.append(
                {
                    "path": _relative(path, engine_root, "engine_root"),
                    "contract_id": document.get("contract_id"),
                    "version": document.get("version"),
                    "status": document.get("status"),
                    "operation_count": (
                        len(operation_ids)
                        if operation_ids
                        else document.get("operation_count")
                    ),
                    "operation_ids": operation_ids,
                }
            )
    return sorted(rows, key=lambda item: str(item["path"]))


def _source_signals(engine_root: Path, tui_root: Path) -> list[dict[str, Any]]:
    signals = ("compat", "deprecated", "supersed", "frozen_legacy", "legacy")
    rows: list[dict[str, Any]] = []
    for root, label in ((engine_root, "engine_root"), (tui_root, "tui_root")):
        for path in _iter_files(root, TEXT_SUFFIXES):
            relative = _relative(path, root, label)
            path_text = relative.lower()
            # Compatibility evidence is intentionally path-scoped.  Searching
            # every comment for the word "legacy" produces a noisy second
            # inventory rather than a useful deletion surface.
            if not any(signal in path_text for signal in signals):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            matched = sorted(
                signal
                for signal in signals
                if signal in path_text or signal in text.lower()
            )
            if matched:
                rows.append({"path": relative, "signals": matched})
    return sorted(rows, key=lambda item: str(item["path"]))

def _reconfiguration(engine_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in (engine_root / "breadboard_engine", engine_root / "breadboard"):
        for path in _iter_files(root, PY_SUFFIXES):
            text = path.read_text(encoding="utf-8", errors="replace")
            line_numbers = [
                index
                for index, line in enumerate(text.splitlines(), start=1)
                if "durable_reconfigure" in line
            ]
            if line_numbers:
                rows.append(
                    {
                        "path": _relative(path, engine_root, "engine_root"),
                        "line_numbers": line_numbers,
                        "occurrence_count": len(line_numbers),
                    }
                )
    return sorted(rows, key=lambda item: str(item["path"]))


def _compatibility(
    engine_root: Path,
    tui_root: Path,
    registry_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    lifecycle = [
        {
            key: row[key]
            for key in ("entry_id", "schema_id", "family", "lifecycle", "default_for_generation", "superseded_by")
            if key in row
        }
        for row in registry_entries
        if row.get("registry_id") == "schema_lifecycle"
    ]
    tiers = [
        {
            key: row[key]
            for key in ("entry_id", "schema_id", "tier", "disposition", "superseded_by", "consumers")
            if key in row
        }
        for row in registry_entries
        if row.get("registry_id") == "contract_tiers"
    ]
    tier_by_schema = {
        str(row["schema_id"]): row
        for row in tiers
        if isinstance(row.get("schema_id"), str)
    }
    for row in lifecycle:
        tier_row = tier_by_schema.get(str(row.get("schema_id")))
        if tier_row is not None:
            row["tier_disposition"] = tier_row.get("disposition")
            row["tier_consumers"] = tier_row.get("consumers", [])
    candidates_by_schema: dict[str, dict[str, Any]] = {}
    for row in tiers:
        schema_id = row.get("schema_id")
        if not isinstance(schema_id, str) or row.get("disposition") == "keep":
            continue
        candidates_by_schema[schema_id] = {
            "entry_id": schema_id,
            "schema_id": schema_id,
            "authorities": ["contract_tiers"],
            "contract_tier": dict(row),
        }
    for row in lifecycle:
        schema_id = row.get("schema_id")
        if not isinstance(schema_id, str):
            continue
        tier_row = tier_by_schema.get(schema_id)
        if tier_row is not None and tier_row.get("disposition") == "keep":
            continue
        if not (
            row.get("superseded_by")
            or row.get("lifecycle") in {"frozen_accepted_evidence", "validate_only"}
        ):
            continue
        candidate = candidates_by_schema.setdefault(
            schema_id,
            {
                "entry_id": schema_id,
                "schema_id": schema_id,
                "authorities": [],
            },
        )
        candidate["authorities"].append("schema_lifecycle")
        candidate["schema_lifecycle"] = dict(row)
    deletion_candidates = sorted(
        candidates_by_schema.values(),
        key=lambda item: str(item["schema_id"]),
    )
    for row in (lifecycle, tiers, deletion_candidates):
        row.sort(key=lambda item: str(item.get("schema_id") or item.get("entry_id") or ""))
    return {
        "contract_tiers": {
            "registry_path": "engine_root/contracts/kernel/registries/contract_tiers.v1.json",
            "entries": tiers,
            "entry_count": len(tiers),
        },
        "schema_lifecycle": {
            "registry_path": "engine_root/contracts/kernel/registries/schema_lifecycle.v1.json",
            "entries": lifecycle,
            "entry_count": len(lifecycle),
        },
        "public_catalogs": _public_catalogs(engine_root),
        "source_signals": _source_signals(engine_root, tui_root),
        "reconfiguration": _reconfiguration(engine_root),
        "deletion_candidates": deletion_candidates,
    }


def inventory(engine_root: str | Path, tui_root: str | Path) -> dict[str, Any]:
    """Return a deterministic advisory inventory for an ENGINE/TUI pair."""
    engine = _root(engine_root, "engine_root")
    tui = _root(tui_root, "tui_root")
    registries, registry_entries = _registries(engine)
    schemas = _schema_rows(engine, registry_entries)
    events = _event_rows(registry_entries, engine)
    owners = _owner_rows(engine, registry_entries)
    sdk_exports = _sdk_exports(engine)
    tui_consumers = _tui_consumers(engine, tui, events, schemas, sdk_exports)
    compatibility_surfaces = _compatibility(engine, tui, registry_entries)
    projections = [
        {
            "kind": "schema",
            "id": row["id"],
            "path": row["path"],
            "domain": row["domain"],
        }
        for row in schemas
        if "projection" in str(row["id"]).lower()
    ]
    projections.extend(
        {
            "kind": "owner",
            "path": row["path"],
            "symbols": row["symbols"],
        }
        for row in owners
        if "projection" in str(row["path"]).lower() or "/views.py" in str(row["path"])
    )
    projections.sort(key=lambda item: (str(item["kind"]), str(item.get("id") or item["path"])))
    return {
        "advisory": True,
        "contract_id": "bb.interface_inventory.v1",
        "event_kinds": events,
        "generated_by": SCRIPT_ID,
        "method": {
            "owner_sources": "registry consumer references plus static top-level declarations",
            "schema_sources": "kernel and public schema JSON files with schema_lifecycle and contract_tiers references",
            "tui_consumer_sources": "exact token references in named TUI source roots",
            "excluded_path_parts": sorted(EXCLUDED_PARTS),
        },
        "owners": owners,
        "projections": projections,
        "registries": registries,
        "authoritative_registry_entries": registry_entries,
        "schemas": schemas,
        "sdk_exports": sdk_exports,
        "tui_consumers": tui_consumers,
        "compatibility_surfaces": compatibility_surfaces,
        "version": VERSION,
    }


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--tui-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="write JSON here; stdout when omitted")
    parser.add_argument("--check", action="store_true", help="fail when --output is not the deterministic fixed point")
    args = parser.parse_args(argv)
    try:
        content = canonical_bytes(inventory(args.engine_root, args.tui_root))
    except ValueError as exc:
        parser.error(str(exc))
    if args.check:
        if args.output is None:
            parser.error("--check requires --output")
        if not args.output.is_file() or args.output.read_bytes() != content:
            print(f"interface inventory stale: {args.output}")
            return 1
        print(f"interface inventory fixed point: {args.output}")
        return 0
    if args.output is None:
        sys.stdout.buffer.write(content)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(content)
        print(f"wrote interface inventory: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
