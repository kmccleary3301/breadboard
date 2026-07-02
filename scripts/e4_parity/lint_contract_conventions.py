#!/usr/bin/env python3
"""Lint kernel contract schemas against BB-ER contract conventions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CANONICAL_SCHEMA_ID_PREFIX = "https://breadboard.dev/contracts/kernel/schemas/"
ALLOWLIST_SCHEMA_VERSION = "bb.contract_conventions_allowlist.v1"
SCHEMA_FILENAME_RE = re.compile(r"^bb(?:\.[a-z0-9_]+)+\.v\d+\.schema\.json$")
CONTRACT_NAME_RE = re.compile(r"^(bb(?:\.[a-z0-9_]+)+\.v\d+)\.schema\.json$")
BARE_SHA256_RE = re.compile(r"\^[a-f0-9]\{64\}\$|\^\[a-f0-9\]\{64\}\$")
SHA256_PREFIX_PATTERN_RE = re.compile(r"sha256:\[a-f0-9\]\{64\}|sha256:\[a-f0-9\]\{64\}|sha256:\\[a-f0-9\\]\{64\\}")
TIMESTAMP_FIELD_RE = re.compile(r"(^|_)(timestamp_utc|generated_at_utc|created_at_utc|updated_at_utc|discovered_at_utc|entered_at_utc|started_at_utc|finished_at_utc|completed_at_utc|captured_at_utc)$")
AT_UTC_FIELD_RE = re.compile(r"_at_utc$")
DIGEST_FIELD_RE = re.compile(r"(^|_)(sha256|hash|checksum)(_sha256)?$")
HASH_REF_FIELD_RE = re.compile(r"(^|_)(hash_ref|row_hash_ref)$")
VISIBILITY_FIELD_RE = re.compile(r"(^|_)(visibility|visible|redaction|redacted|host_only|model_visible|provider_visible|host_visible)(_|$)")
SNAKE_PROPERTY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CANONICAL_VISIBILITY_ENUMS = {"model_visible", "provider_visible", "host_only", "secret", "redacted"}
CANONICAL_REDACTION_ENUMS = {"none", "redacted", "summarized", "elided"}
ROOT_RULES = {
    "canonical_absolute_id",
    "strict_root_additional_properties",
    "schema_version_const",
    "pack_totality",
    "schema_census",
}


@dataclass(frozen=True)
class ConventionDiagnostic:
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


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_allowlist(path: Path) -> dict[str, frozenset[str]]:
    payload = _load_json(path)
    if not isinstance(payload, Mapping):
        raise ValueError(f"allowlist root must be an object: {path}")
    if payload.get("schema_version") != ALLOWLIST_SCHEMA_VERSION:
        raise ValueError(
            f"allowlist schema_version must be {ALLOWLIST_SCHEMA_VERSION!r}: {path}"
        )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"allowlist.entries must be an array: {path}")
    allowlist: dict[str, frozenset[str]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"allowlist entry {index} must be an object: {path}")
        rel_path = entry.get("path")
        exemptions = entry.get("exemptions")
        if not isinstance(rel_path, str) or not rel_path:
            raise ValueError(f"allowlist entry {index} has invalid path: {path}")
        if not isinstance(exemptions, list) or not all(
            isinstance(item, str) for item in exemptions
        ):
            raise ValueError(f"allowlist entry {rel_path} has invalid exemptions: {path}")
        unknown = set(exemptions) - ROOT_RULES
        if unknown:
            raise ValueError(
                f"allowlist entry {rel_path} has unknown exemptions: {sorted(unknown)}"
            )
        allowlist[rel_path] = frozenset(exemptions)
    return allowlist


def _is_exempt(rel_path: str, rule: str, allowlist: Mapping[str, frozenset[str]]) -> bool:
    return rule in allowlist.get(rel_path, frozenset())


def _is_legacy_allowlisted(rel_path: str, allowlist: Mapping[str, frozenset[str]]) -> bool:
    return rel_path in allowlist


def _contract_name(filename: str) -> str | None:
    match = CONTRACT_NAME_RE.match(filename)
    return match.group(1) if match else None


def _walk_schema_nodes(node: Any, pointer: str = "$") -> Iterable[tuple[str, Any, str | None]]:
    """Yield every schema object with its pointer and owning property name."""
    stack: list[tuple[str, Any, str | None]] = [(pointer, node, None)]
    while stack:
        current_pointer, current, property_name = stack.pop()
        if isinstance(current, Mapping):
            yield current_pointer, current, property_name
            properties = current.get("properties")
            if isinstance(properties, Mapping):
                for name, child in reversed(list(properties.items())):
                    stack.append((f"{current_pointer}/properties/{name}", child, str(name)))
            defs = current.get("$defs")
            if isinstance(defs, Mapping):
                for name, child in reversed(list(defs.items())):
                    stack.append((f"{current_pointer}/$defs/{name}", child, str(name)))
            for keyword in ("items", "additionalProperties", "contains", "propertyNames"):
                child = current.get(keyword)
                if isinstance(child, Mapping):
                    stack.append((f"{current_pointer}/{keyword}", child, property_name))
            for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
                children = current.get(keyword)
                if isinstance(children, list):
                    for index, child in reversed(list(enumerate(children))):
                        if isinstance(child, Mapping):
                            stack.append((f"{current_pointer}/{keyword}/{index}", child, property_name))


def _has_type(schema: Mapping[str, Any], expected: str) -> bool:
    value = schema.get("type")
    if value == expected:
        return True
    return isinstance(value, list) and expected in value


def _ref_mentions(schema: Mapping[str, Any], *tokens: str) -> bool:
    value = schema.get("$ref")
    return isinstance(value, str) and any(token in value for token in tokens)


def _pattern_mentions_prefixed_sha(schema: Mapping[str, Any]) -> bool:
    pattern = schema.get("pattern")
    return isinstance(pattern, str) and ("sha256:" in pattern or SHA256_PREFIX_PATTERN_RE.search(pattern))



def _schema_mentions_ref(schema: Mapping[str, Any], *tokens: str) -> bool:
    stack: list[Any] = [schema]
    while stack:
        current = stack.pop()
        if not isinstance(current, Mapping):
            continue
        if _ref_mentions(current, *tokens):
            return True
        for keyword in ("items", "additionalProperties", "contains", "propertyNames"):
            child = current.get(keyword)
            if isinstance(child, Mapping):
                stack.append(child)
        for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
            children = current.get(keyword)
            if isinstance(children, list):
                stack.extend(child for child in children if isinstance(child, Mapping))
    return False


def _schema_major_version(filename: str) -> int | None:
    contract_name = _contract_name(filename)
    if contract_name is None:
        return None
    version = contract_name.rsplit(".v", 1)[-1]
    return int(version) if version.isdigit() else None


def _uses_new_kernel_dialect(schema_path: Path, rel_path: str, allowlist: Mapping[str, frozenset[str]]) -> bool:
    if _is_legacy_allowlisted(rel_path, allowlist):
        return False
    major_version = _schema_major_version(schema_path.name)
    if major_version is None:
        return False
    return major_version >= 2

def _schema_accepts_null(schema: Mapping[str, Any]) -> bool:
    typ = schema.get("type")
    if isinstance(typ, list) and "null" in typ:
        return True
    for keyword in ("anyOf", "oneOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, Mapping) and variant.get("type") == "null":
                    return True
    return False


def _record_root(schema: Mapping[str, Any]) -> bool:
    return _has_type(schema, "object") and isinstance(schema.get("properties"), Mapping)


def _rule_name_id(schema_path: Path, rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    filename = schema_path.name
    if not SCHEMA_FILENAME_RE.match(filename):
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "schema_filename",
                "schema filename must be bb.<name>.vN.schema.json with lowercase snake/dot segments",
            )
        )
    expected_id = f"{CANONICAL_SCHEMA_ID_PREFIX}{filename}"
    if schema.get("$id") != expected_id:
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "canonical_absolute_id",
                f"$id must equal {expected_id!r}",
                "$/$id",
            )
        )
    return diagnostics


def _rule_root_strictness(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    if not _record_root(schema):
        return []
    if schema.get("additionalProperties") is not False:
        return [
            ConventionDiagnostic(
                rel_path,
                "strict_root_additional_properties",
                "record schema root must set additionalProperties: false",
                "$/additionalProperties",
            )
        ]
    return []


def _rule_closed_object_property_names(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    for pointer, node, _property_name in _walk_schema_nodes(schema):
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            continue
        if node.get("additionalProperties") is not False:
            continue
        for property_name in properties:
            if SNAKE_PROPERTY_RE.match(str(property_name)):
                continue
            diagnostics.append(
                ConventionDiagnostic(
                    rel_path,
                    "snake_case_property_names",
                    f"closed-object property {property_name!r} must match ^[a-z][a-z0-9_]*$",
                    f"{pointer}/properties/{property_name}",
                )
            )
    return diagnostics


def _rule_at_utc_timestamp_refs(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    for pointer, node, property_name in _walk_schema_nodes(schema):
        if not property_name or not AT_UTC_FIELD_RE.search(property_name):
            continue
        if _schema_mentions_ref(node, "/$defs/timestamp_utc"):
            continue
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "timestamp_utc_ref",
                f"timestamp field {property_name!r} must $ref common timestamp_utc",
                pointer,
            )
        )
    return diagnostics


def _rule_schema_version(schema_path: Path, rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    if not _record_root(schema):
        return []
    expected = _contract_name(schema_path.name)
    properties = schema.get("properties")
    required = schema.get("required")
    version_schema = properties.get("schema_version") if isinstance(properties, Mapping) else None
    diagnostics: list[ConventionDiagnostic] = []
    if not isinstance(version_schema, Mapping):
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "schema_version_const",
                "record schema must expose a schema_version property",
                "$/properties/schema_version",
            )
        )
    elif expected is not None and version_schema.get("const") != expected:
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "schema_version_const",
                f"schema_version.const must equal {expected!r}",
                "$/properties/schema_version/const",
            )
        )
    if isinstance(required, list) and "schema_version" not in required:
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "schema_version_const",
                "record schema must require schema_version",
                "$/required",
            )
        )
    return diagnostics


def _rule_timestamp_fields(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    for pointer, node, property_name in _walk_schema_nodes(schema):
        if not property_name or not TIMESTAMP_FIELD_RE.search(property_name):
            continue
        if _ref_mentions(node, "/$defs/timestamp_utc"):
            continue
        if node.get("format") == "date-time" and _has_type(node, "string"):
            continue
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "timestamp_convention",
                f"timestamp-like field {property_name!r} must use common timestamp_utc or string format date-time",
                pointer,
            )
        )
    return diagnostics


def _rule_digest_fields(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    for pointer, node, property_name in _walk_schema_nodes(schema):
        if not isinstance(node, Mapping):
            continue
        pattern = node.get("pattern")
        if isinstance(pattern, str) and BARE_SHA256_RE.search(pattern):
            diagnostics.append(
                ConventionDiagnostic(
                    rel_path,
                    "digest_convention",
                    "digest pattern must include the sha256: prefix",
                    f"{pointer}/pattern",
                )
            )
        if not property_name:
            continue
        if not (DIGEST_FIELD_RE.search(property_name) or HASH_REF_FIELD_RE.search(property_name)):
            continue
        if _ref_mentions(
            node,
            "/$defs/digest_sha256",
            "/$defs/nullable_digest_sha256",
            "/$defs/hash_ref",
            "/$defs/row_hash_ref",
        ):
            continue
        if _pattern_mentions_prefixed_sha(node):
            continue
        if property_name in {"id", "ref", "refs"}:
            continue
        diagnostics.append(
            ConventionDiagnostic(
                rel_path,
                "digest_convention",
                f"digest/ref-like field {property_name!r} must use common sha256/hash ref definitions or a sha256:-prefixed pattern",
                pointer,
            )
        )
    return diagnostics


def _rule_nullable_fields(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    for pointer, node, _property_name in _walk_schema_nodes(schema):
        if not _schema_accepts_null(node):
            continue
        typ = node.get("type")
        if isinstance(typ, list) and (typ[-1] != "null" or len(typ) != len(set(typ))):
            diagnostics.append(
                ConventionDiagnostic(
                    rel_path,
                    "nullable_convention",
                    "nullable type arrays must place null last and avoid duplicates",
                    f"{pointer}/type",
                )
            )
    return diagnostics


def _rule_visibility_fields(rel_path: str, schema: Mapping[str, Any]) -> list[ConventionDiagnostic]:
    diagnostics: list[ConventionDiagnostic] = []
    for pointer, node, property_name in _walk_schema_nodes(schema):
        if not property_name or not VISIBILITY_FIELD_RE.search(property_name):
            continue
        if _ref_mentions(node, "/$defs/visibility", "/$defs/visibility_paths"):
            continue
        enum = node.get("enum")
        if isinstance(enum, list):
            values = {item for item in enum if isinstance(item, str)}
            if values <= CANONICAL_VISIBILITY_ENUMS or values <= CANONICAL_REDACTION_ENUMS:
                continue
            diagnostics.append(
                ConventionDiagnostic(
                    rel_path,
                    "visibility_convention",
                    f"visibility-like enum {property_name!r} must use canonical common visibility vocabulary",
                    f"{pointer}/enum",
                )
            )
    return diagnostics





def _schema_contract_versions(schema_paths: Iterable[Path]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for schema_path in schema_paths:
        contract_name = _contract_name(schema_path.name)
        if contract_name is not None:
            versions[contract_name] = schema_path.name
    return versions


def _rule_pack_totality(repo_root: Path, schemas_dir: Path, schema_paths: Sequence[Path]) -> list[ConventionDiagnostic]:
    pack_path = repo_root / "contracts" / "kernel" / "packs.v1.json"
    rel_path = _display_path(pack_path, repo_root)
    schema_names = {path.name for path in schema_paths if path.name.endswith(".schema.json")}
    if not pack_path.exists():
        return [ConventionDiagnostic(rel_path, "pack_totality", "contracts/kernel/packs.v1.json is required and must assign every top-level schema exactly once")]
    payload = _load_json(pack_path)
    if not isinstance(payload, Mapping):
        return [ConventionDiagnostic(rel_path, "pack_totality", "schema pack manifest root must be an object")]
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return [ConventionDiagnostic(rel_path, "pack_totality", "schema pack manifest entries must be an array")]
    owners: dict[str, list[str]] = {}
    listed: list[str] = []
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            return [ConventionDiagnostic(rel_path, "pack_totality", f"entries[{entry_index}] must be an object", f"$/entries/{entry_index}")]
        pack_id = entry.get("id")
        metadata = entry.get("metadata")
        schemas = metadata.get("schemas") if isinstance(metadata, Mapping) else None
        if not isinstance(pack_id, str) or not pack_id:
            return [ConventionDiagnostic(rel_path, "pack_totality", f"entries[{entry_index}].id must be a non-empty string", f"$/entries/{entry_index}/id")]
        if not isinstance(schemas, list) or not all(isinstance(item, str) for item in schemas):
            return [ConventionDiagnostic(rel_path, "pack_totality", f"entries[{entry_index}].metadata.schemas must be a string array", f"$/entries/{entry_index}/metadata/schemas")]
        for schema_name in schemas:
            owners.setdefault(schema_name, []).append(pack_id)
            listed.append(schema_name)
    missing = sorted(schema_names - set(listed))
    if missing:
        return [ConventionDiagnostic(rel_path, "pack_totality", f"schema pack manifest missing top-level schema(s): {', '.join(missing)}")]
    stale = sorted(set(listed) - schema_names)
    if stale:
        return [ConventionDiagnostic(rel_path, "pack_totality", f"schema pack manifest lists missing schema file(s): {', '.join(stale)}")]
    duplicates = sorted(name for name, pack_ids in owners.items() if len(pack_ids) != 1)
    if duplicates:
        duplicate = duplicates[0]
        return [ConventionDiagnostic(rel_path, "pack_totality", f"{duplicate} appears in multiple schema pack entries: {', '.join(owners[duplicate])}")]
    return []


def _rule_schema_census(repo_root: Path, schema_paths: Sequence[Path]) -> list[ConventionDiagnostic]:
    registry_path = repo_root / "contracts" / "kernel" / "registries" / "kernel_families.v1.json"
    rel_path = _display_path(registry_path, repo_root)
    contract_to_file = _schema_contract_versions(schema_paths)
    if not registry_path.exists():
        return []
    payload = _load_json(registry_path)
    if not isinstance(payload, Mapping):
        return [ConventionDiagnostic(rel_path, "schema_census", "kernel_families registry root must be an object")]
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return [ConventionDiagnostic(rel_path, "schema_census", "kernel_families entries must be an array")]
    owners: dict[str, list[str]] = {}
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            return [ConventionDiagnostic(rel_path, "schema_census", f"entries[{entry_index}] must be an object", f"$/entries/{entry_index}")]
        family_id = entry.get("id")
        metadata = entry.get("metadata")
        versions = metadata.get("schema_versions") if isinstance(metadata, Mapping) else None
        if versions is None:
            continue
        if not isinstance(versions, list) or not all(isinstance(item, str) for item in versions):
            return [ConventionDiagnostic(rel_path, "schema_census", f"entries[{entry_index}].metadata.schema_versions must be a string array", f"$/entries/{entry_index}/metadata/schema_versions")]
        owner = str(family_id) if isinstance(family_id, str) and family_id else f"entries[{entry_index}]"
        for schema_version in versions:
            owners.setdefault(schema_version, []).append(owner)
    for schema_version in sorted(contract_to_file):
        pack_name = contract_to_file[schema_version]
        # Shared definition schemas are pack-owned but intentionally not finalized record families.
        if schema_version in {"bb.kernel.common.v1", "bb.e4.common.v1"}:
            continue
        if schema_version not in owners:
            return [ConventionDiagnostic(rel_path, "schema_census", f"missing family registry entry for {schema_version} ({pack_name})")]
    for schema_version, family_ids in sorted(owners.items()):
        if schema_version not in contract_to_file:
            return [ConventionDiagnostic(rel_path, "schema_census", f"kernel_families lists {schema_version} but no matching top-level schema exists")]
        if len(family_ids) != 1:
            return [ConventionDiagnostic(rel_path, "schema_census", f"{schema_version} appears in multiple kernel_families entries: {', '.join(family_ids)}")]
    return []

def lint_schema(
    schema_path: Path,
    *,
    repo_root: Path,
    allowlist: Mapping[str, frozenset[str]],
) -> list[ConventionDiagnostic]:
    rel_path = _display_path(schema_path, repo_root)
    payload = _load_json(schema_path)
    if not isinstance(payload, Mapping):
        return [ConventionDiagnostic(rel_path, "schema_json", "schema root must be a JSON object")]

    diagnostics: list[ConventionDiagnostic] = []
    legacy_allowlisted = _is_legacy_allowlisted(rel_path, allowlist)
    for diagnostic in _rule_name_id(schema_path, rel_path, payload):
        if not _is_exempt(rel_path, diagnostic.rule, allowlist):
            diagnostics.append(diagnostic)
    for diagnostic in _rule_root_strictness(rel_path, payload):
        if not _is_exempt(rel_path, diagnostic.rule, allowlist):
            diagnostics.append(diagnostic)
    if not legacy_allowlisted:
        diagnostics.extend(_rule_schema_version(schema_path, rel_path, payload))

    if legacy_allowlisted:
        return diagnostics

    diagnostics.extend(_rule_timestamp_fields(rel_path, payload))
    diagnostics.extend(_rule_digest_fields(rel_path, payload))
    diagnostics.extend(_rule_nullable_fields(rel_path, payload))
    diagnostics.extend(_rule_visibility_fields(rel_path, payload))
    if _uses_new_kernel_dialect(schema_path, rel_path, allowlist):
        diagnostics.extend(_rule_closed_object_property_names(rel_path, payload))
        diagnostics.extend(_rule_at_utc_timestamp_refs(rel_path, payload))
    return diagnostics


def lint_contract_conventions(
    *,
    repo_root: Path,
    schemas_dir: Path | None = None,
    allowlist_path: Path | None = None,
) -> list[ConventionDiagnostic]:
    repo_root = repo_root.resolve()
    schemas_dir = schemas_dir or repo_root / "contracts/kernel/schemas"
    allowlist_path = allowlist_path or repo_root / "docs/contracts/contract_conventions_allowlist.json"
    allowlist = load_allowlist(allowlist_path)
    diagnostics: list[ConventionDiagnostic] = []
    for schema_path in sorted(schemas_dir.glob("*.json")):
        diagnostics.extend(lint_schema(schema_path, repo_root=repo_root, allowlist=allowlist))
    schema_file_paths = sorted(path for path in schemas_dir.glob("*.json") if path.is_file())
    diagnostics.extend(_rule_pack_totality(repo_root, schemas_dir, schema_file_paths))
    diagnostics.extend(_rule_schema_census(repo_root, schema_file_paths))
    schema_paths = {
        _display_path(path, repo_root)
        for path in schema_file_paths
    }
    for allowlisted_path in sorted(allowlist):
        if allowlisted_path not in schema_paths:
            diagnostics.append(
                ConventionDiagnostic(
                    allowlisted_path,
                    "allowlist_path_exists",
                    "allowlist entry does not match a schema file",
                )
            )
    return diagnostics


def _format_text(diagnostics: Sequence[ConventionDiagnostic]) -> str:
    if not diagnostics:
        return "contract conventions lint passed"
    return "\n".join(
        f"{diag.path}:{diag.pointer}: {diag.rule}: {diag.message}" for diag in diagnostics
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument(
        "--schemas-dir",
        default=None,
        help="Schema directory, default: <repo-root>/contracts/kernel/schemas",
    )
    parser.add_argument(
        "--allowlist",
        default=None,
        help="Convention allowlist, default: <repo-root>/docs/contracts/contract_conventions_allowlist.json",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON diagnostics")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root)
    diagnostics = lint_contract_conventions(
        repo_root=repo_root,
        schemas_dir=Path(args.schemas_dir) if args.schemas_dir else None,
        allowlist_path=Path(args.allowlist) if args.allowlist else None,
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
        stream = sys.stdout if not diagnostics else sys.stderr
        print(_format_text(diagnostics), file=stream)
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
