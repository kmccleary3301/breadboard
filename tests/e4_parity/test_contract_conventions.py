from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity.lint_contract_conventions import lint_contract_conventions


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = ROOT / "contracts" / "kernel" / "schemas"
ALLOWLIST_PATH = ROOT / "docs" / "contracts" / "contract_conventions_allowlist.json"
LINT_SCRIPT = ROOT / "scripts" / "e4_parity" / "lint_contract_conventions.py"
CANONICAL_PREFIX = "https://breadboard.dev/contracts/kernel/schemas/"

PRIMITIVE_SCHEMA_NAMES = [
    "bb.work_item.v1.schema.json",
    "bb.resource_ref.v1.schema.json",
    "bb.resource_access.v1.schema.json",
    "bb.blob_ref.v1.schema.json",
    "bb.projection_event.v1.schema.json",
    "bb.effective_operation_policy.v1.schema.json",
    "bb.external_protocol_session.v1.schema.json",
    "bb.provider_route.v1.schema.json",
    "bb.memory_compaction_plan.v1.schema.json",
    "bb.context_resource_pack.v1.schema.json",
    "bb.effective_config_graph.v1.schema.json",
    "bb.config_mutation_record.v1.schema.json",
    "bb.extension_hook_execution.v1.schema.json",
    "bb.side_effect_broker.v1.schema.json",
]


def _walk_property_schemas(schema: dict[str, Any], pointer: str = "$") -> list[tuple[str, str, dict[str, Any]]]:
    found: list[tuple[str, str, dict[str, Any]]] = []

    def walk(node: Any, node_pointer: str, inherited_property: str | None = None) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            for property_name, property_schema in properties.items():
                property_pointer = f"{node_pointer}/properties/{property_name}"
                if isinstance(property_schema, dict):
                    found.append((property_pointer, property_name, property_schema))
                    walk(property_schema, property_pointer, property_name)
        items = node.get("items")
        if isinstance(items, dict):
            item_name = inherited_property or "items"
            found.append((f"{node_pointer}/items", item_name, items))
            walk(items, f"{node_pointer}/items", item_name)
        for keyword in ("$defs", "definitions"):
            children = node.get(keyword)
            if isinstance(children, dict):
                for name, child in children.items():
                    walk(child, f"{node_pointer}/{keyword}/{name}")
        for keyword in ("allOf", "anyOf", "oneOf"):
            children = node.get(keyword)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    walk(child, f"{node_pointer}/{keyword}/{index}", inherited_property)
        for keyword in ("if", "then", "else", "not", "contains", "additionalProperties"):
            child = node.get(keyword)
            if isinstance(child, dict):
                walk(child, f"{node_pointer}/{keyword}", inherited_property)

    walk(schema, pointer)
    return found


def _needs_inline_example(property_name: str) -> bool:
    tokens = property_name.split("_")
    return (
        "id" in tokens
        or "ids" in tokens
        or "ref" in tokens
        or "refs" in tokens
        or "hash" in tokens
        or "sha256" in tokens
        or "timestamp" in tokens
        or property_name.endswith("_at")
        or property_name.endswith("_utc")
        or property_name.endswith("_uri")
        or property_name.endswith("_path")
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _schema(name: str, **overrides: Any) -> dict[str, Any]:
    contract_name = name.removesuffix(".schema.json")
    payload: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{CANONICAL_PREFIX}{name}",
        "title": contract_name,
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "item_id", "generated_at_utc", "state_hash"],
        "properties": {
            "schema_version": {"const": contract_name},
            "item_id": {"type": "string", "minLength": 1},
            "generated_at_utc": {"$ref": "bb.kernel.common.v1.schema.json#/$defs/timestamp_utc"},
            "state_hash": {"$ref": "bb.kernel.common.v1.schema.json#/$defs/digest_sha256"},
        },
    }
    payload.update(overrides)
    return payload


def _write_allowlist(repo_root: Path, entries: list[dict[str, Any]] | None = None) -> Path:
    path = repo_root / "docs" / "contracts" / "contract_conventions_allowlist.json"
    _write_json(
        path,
        {
            "schema_version": "bb.contract_conventions_allowlist.v1",
            "generated_at_utc": "2026-07-03T00:00:00Z",
            "rules": [
                "canonical_absolute_id",
                "strict_root_additional_properties",
                "schema_version_const",
            ],
            "entries": entries or [],
        },
    )
    return path


def _write_schema_pack(repo_root: Path, schema_names: list[str]) -> Path:
    path = repo_root / "contracts" / "kernel" / "packs.v1.json"
    existing: set[str] = set()
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload.get("entries", []):
            metadata = entry.get("metadata", {})
            existing.update(str(name) for name in metadata.get("schemas", []))
    existing.update(schema_names)
    _write_json(
        path,
        {
            "schema_version": "bb.registry.v1",
            "registry_id": "schema_packs",
            "description": "Test fixture pack manifest.",
            "entries": [
                {
                    "id": "kernel",
                    "status": "active",
                    "description": "Test fixture kernel pack.",
                    "metadata": {"schemas": sorted(existing)},
                }
            ],
        },
    )
    return path


def _write_kernel_families(repo_root: Path, entries: list[dict[str, Any]]) -> Path:
    path = repo_root / "contracts" / "kernel" / "registries" / "kernel_families.v1.json"
    _write_json(
        path,
        {
            "schema_version": "bb.registry.v1",
            "registry_id": "kernel_families",
            "entries": entries,
        },
    )
    return path


def _family_entry(family_id: str, schema_versions: list[str]) -> dict[str, Any]:
    return {
        "id": family_id,
        "status": "active",
        "description": f"{family_id} test family.",
        "metadata": {"schema_versions": schema_versions},
    }


def _write_schema(repo_root: Path, name: str, payload: dict[str, Any], *, assign_to_pack: bool = True) -> Path:
    if assign_to_pack:
        _write_schema_pack(repo_root, [name])
    path = repo_root / "contracts" / "kernel" / "schemas" / name
    _write_json(path, payload)
    return path


def _rules(repo_root: Path) -> list[str]:
    diagnostics = lint_contract_conventions(repo_root=repo_root)
    return [diagnostic.rule for diagnostic in diagnostics]


def test_checked_in_kernel_schema_pack_satisfies_contract_conventions() -> None:
    assert SCHEMAS_DIR.is_dir(), "expected checked-in kernel schema pack"
    assert ALLOWLIST_PATH.is_file(), "expected contract convention allowlist"

    diagnostics = lint_contract_conventions(repo_root=ROOT)

    assert diagnostics == []


def test_primitive_schemas_describe_every_property_schema() -> None:
    missing: list[str] = []
    for schema_name in PRIMITIVE_SCHEMA_NAMES:
        schema = json.loads((SCHEMAS_DIR / schema_name).read_text(encoding="utf-8"))
        for pointer, property_name, property_schema in _walk_property_schemas(schema):
            if "description" not in property_schema:
                missing.append(f"{schema_name}:{pointer}:{property_name}")

    assert missing == []


def test_primitive_identifier_ref_hash_and_timestamp_fields_have_examples() -> None:
    missing: list[str] = []
    for schema_name in PRIMITIVE_SCHEMA_NAMES:
        schema = json.loads((SCHEMAS_DIR / schema_name).read_text(encoding="utf-8"))
        for pointer, property_name, property_schema in _walk_property_schemas(schema):
            if _needs_inline_example(property_name) and "examples" not in property_schema and "example" not in property_schema:
                missing.append(f"{schema_name}:{pointer}:{property_name}")

    assert missing == []


def test_checked_in_non_allowlisted_schemas_use_canonical_absolute_ids() -> None:
    allowlist = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    allowlisted_paths = {entry["path"] for entry in allowlist["entries"]}
    failures: list[str] = []
    for schema_path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        rel_path = schema_path.relative_to(ROOT).as_posix()
        if rel_path in allowlisted_paths:
            continue
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        expected_id = f"{CANONICAL_PREFIX}{schema_path.name}"
        if schema.get("$id") != expected_id:
            failures.append(f"{rel_path}: {schema.get('$id')!r} != {expected_id!r}")

    assert failures == []


def test_new_schema_rejects_relative_id(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    _write_schema(
        tmp_path,
        "bb.test_item.v1.schema.json",
        _schema("bb.test_item.v1.schema.json", **{"$id": "bb.test_item.v1.schema.json"}),
    )

    diagnostics = lint_contract_conventions(repo_root=tmp_path)

    assert [diagnostic.rule for diagnostic in diagnostics] == ["canonical_absolute_id"]
    assert "https://breadboard.dev/contracts/kernel/schemas/bb.test_item.v1.schema.json" in diagnostics[0].message


def test_allowlist_exemption_suppresses_only_the_named_root_rule(tmp_path: Path) -> None:
    schema_rel = "contracts/kernel/schemas/bb.test_item.v1.schema.json"
    _write_allowlist(
        tmp_path,
        [
            {
                "path": schema_rel,
                "exemptions": ["canonical_absolute_id"],
                "source": "test",
                "reason": "unit-test fixture",
            }
        ],
    )
    bad_schema = _schema(
        "bb.test_item.v1.schema.json",
        **{"$id": "bb.test_item.v1.schema.json", "additionalProperties": True},
    )
    _write_schema(tmp_path, "bb.test_item.v1.schema.json", bad_schema)

    assert _rules(tmp_path) == ["strict_root_additional_properties"]


def test_new_record_schema_requires_schema_version_const(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    payload = _schema("bb.test_item.v1.schema.json")
    payload["required"] = ["item_id"]
    payload["properties"].pop("schema_version")
    _write_schema(tmp_path, "bb.test_item.v1.schema.json", payload)

    rules = _rules(tmp_path)

    assert rules == ["schema_version_const", "schema_version_const"]


def test_new_schema_checks_timestamp_digest_nullable_and_visibility_conventions(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    payload = _schema("bb.test_item.v1.schema.json")
    payload["properties"]["generated_at_utc"] = {"type": "string", "minLength": 1}
    payload["properties"]["state_hash"] = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    payload["properties"]["maybe_label"] = {"type": ["null", "string"]}
    payload["properties"]["visibility_mode"] = {"enum": ["model-visible", "host-only"]}
    _write_schema(tmp_path, "bb.test_item.v1.schema.json", payload)

    assert _rules(tmp_path) == [
        "timestamp_convention",
        "digest_convention",
        "digest_convention",
        "nullable_convention",
        "visibility_convention",
    ]


def test_synthetic_new_open_root_schema_fails(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    _write_schema(
        tmp_path,
        "bb.test_item.v2.schema.json",
        _schema("bb.test_item.v2.schema.json", additionalProperties=True),
    )

    assert _rules(tmp_path) == ["strict_root_additional_properties"]


def test_synthetic_new_unassigned_top_level_schema_fails_pack_totality(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    _write_schema_pack(tmp_path, [])
    _write_schema(
        tmp_path,
        "bb.test_item.v2.schema.json",
        _schema("bb.test_item.v2.schema.json"),
        assign_to_pack=False,
    )

    diagnostics = lint_contract_conventions(repo_root=tmp_path)

    assert [diagnostic.rule for diagnostic in diagnostics] == ["pack_totality"]
    assert "bb.test_item.v2.schema.json" in diagnostics[0].message


def test_synthetic_conforming_v2_schema_passes(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    payload = _schema("bb.test_item.v2.schema.json")
    payload["required"].append("nested_record")
    payload["properties"]["nested_record"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["child_id", "created_at_utc"],
        "properties": {
            "child_id": {"type": "string", "minLength": 1},
            "created_at_utc": {"$ref": "bb.kernel.common.v1.schema.json#/$defs/timestamp_utc"},
        },
    }
    _write_schema(tmp_path, "bb.test_item.v2.schema.json", payload)

    assert lint_contract_conventions(repo_root=tmp_path) == []


def test_synthetic_new_v2_schema_enforces_closed_property_names_and_at_utc_refs(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    payload = _schema("bb.test_item.v2.schema.json")
    payload["properties"]["camelCase"] = {"type": "string"}
    payload["properties"]["finished_at_utc"] = {"type": "string", "format": "date-time"}
    _write_schema(tmp_path, "bb.test_item.v2.schema.json", payload)

    assert _rules(tmp_path) == ["snake_case_property_names", "timestamp_utc_ref"]


@pytest.mark.parametrize(
    "entries, expected_message",
    [
        ([], "missing family registry entry for bb.test_item.v2"),
        (
            [
                _family_entry("test_item", ["bb.test_item.v2"]),
                _family_entry("test_item_duplicate", ["bb.test_item.v2"]),
            ],
            "bb.test_item.v2 appears in multiple kernel_families entries",
        ),
        (
            [
                _family_entry("test_item", ["bb.test_item.v2"]),
                _family_entry("ghost", ["bb.ghost.v1"]),
            ],
            "kernel_families lists bb.ghost.v1 but no matching top-level schema exists",
        ),
    ],
)
def test_schema_census_rejects_missing_duplicate_and_stale_family_versions(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    expected_message: str,
) -> None:
    _write_allowlist(tmp_path)
    _write_kernel_families(tmp_path, entries)
    _write_schema(tmp_path, "bb.test_item.v2.schema.json", _schema("bb.test_item.v2.schema.json"))

    diagnostics = lint_contract_conventions(repo_root=tmp_path)

    assert [diagnostic.rule for diagnostic in diagnostics] == ["schema_census"]
    assert expected_message in diagnostics[0].message


def test_cli_json_reports_nonzero_for_bad_schema(tmp_path: Path) -> None:
    _write_allowlist(tmp_path)
    _write_schema(
        tmp_path,
        "bb.test_item.v1.schema.json",
        _schema("bb.test_item.v1.schema.json", additionalProperties=True),
    )

    result = subprocess.run(
        [sys.executable, str(LINT_SCRIPT), "--repo-root", str(tmp_path), "--json"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["rule"] == "strict_root_additional_properties"
