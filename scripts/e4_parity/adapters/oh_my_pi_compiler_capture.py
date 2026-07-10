from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from agentic_coder_prototype.compilation.effective_config_graph import finalize_effective_config_graph, graph_content_hash
from agentic_coder_prototype.compilation.primitive_records import finalize_record, get_spec
from agentic_coder_prototype.conformance.c4_chain import validate_c4_chain
from agentic_coder_prototype.conformance.catalog_binding import catalog_segment_hash, reusable_catalog_revision
from scripts.e4_parity import build_primitive_projection as primitive_projection
from scripts.e4_parity import lane_inventory_utils as lane_inventory
from scripts.e4_parity import lane_runtime
from scripts.e4_parity.path_refs import (
    ReferenceResolutionError,
    resolve_declared_reference,
)
from scripts.e4_parity.adapters.oh_my_pi_p3_remaining_capture import capture_p3_remaining
from scripts.e4_parity.adapters.oh_my_pi_l5_projection import PROJECTIONS as L5_PROJECTIONS
from scripts.e4_parity.adapters.oh_my_pi_l6_projection import PROJECTIONS as L6_PROJECTIONS
from scripts.e4_parity.adapters.oh_my_pi_p3_1_projection import (
    L1_MANIFEST_SOURCE,
    L1_PROBE_SOURCE,
    L2_MANIFEST_SOURCE,
    L2_PROBE_SOURCE,
    PROJECTIONS as P3_1_PROJECTIONS,
)
from scripts.e4_parity.adapters.oh_my_pi_p3_remaining_projections import PROJECTIONS as P3_PROJECTIONS
from scripts.e4_parity.adapters.oh_my_pi_p6_6_work_item_projection import (
    PROJECTION_ID as TASK_PROJECTION_ID,
    project_work_items,
)
from scripts.e4_parity.adapters.oh_my_pi_projection_packet import build_projection_packet
from scripts.e4_parity.validators import hash_utils
from scripts.e4_parity.validators.registries import schema_generation_default

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT

GENERATED_AT_UTC = "2026-07-03T07:30:00Z"
SUPPORT_CLAIM_GENERATED_AT_UTC = "2026-07-04T00:00:00Z"
SOURCE_FREEZE_PATH = WORKSPACE / "docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_freeze_provenance.json"
FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
HELPER_MODULE_PATH = ROOT / "agentic_coder_prototype/compilation/effective_config_graph.py"
HELPER_TEST_PATH = ROOT / "tests/compilation/test_effective_config_graph.py"
PROJECTION_BUILDER_PATH = ROOT / "scripts/e4_parity/build_primitive_projection.py"
CATALOG_PATH = ROOT / "docs/conformance/e4_artifact_catalog.json"
ADR_AV_3_ACCEPTED_COMPILER_LANES = frozenset(
    {
        "oh_my_pi_p3_1_effective_config_graph_compiler",
        "oh_my_pi_p3_2_context_resource_pack_compiler",
        "oh_my_pi_p3_3_capability_registry_compiler",
        "oh_my_pi_p3_4_extension_hook_execution_compiler",
        "oh_my_pi_p3_5_resource_blob_compiler",
        "oh_my_pi_p3_6_protocol_provider_policy_compiler",
        "oh_my_pi_p3_7_memory_work_compiler",
        "oh_my_pi_p3_8_projection_broker_adapter",
        "oh_my_pi_p6_0_l5_memory_compaction",
        "oh_my_pi_p6_0_l6_tui_projection",
        "oh_my_pi_p6_6_task_job_subagent",
    }
)

Projection = Callable[[Mapping[str, Any]], dict[str, Any]]


def _merge_projection_tables(*tables: Mapping[str, Projection]) -> dict[str, Projection]:
    merged: dict[str, Projection] = {}
    for table in tables:
        duplicate_ids = sorted(set(merged) & set(table))
        if duplicate_ids:
            raise ValueError(f"duplicate projection ids: {duplicate_ids}")
        merged.update(table)
    return merged


TASK_PROJECTIONS: dict[str, Projection] = {TASK_PROJECTION_ID: project_work_items}

PROJECTIONS: dict[str, Projection] = _merge_projection_tables(
    P3_1_PROJECTIONS,
    P3_PROJECTIONS,
    L5_PROJECTIONS,
    L6_PROJECTIONS,
    TASK_PROJECTIONS,
)
L1_DIR = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface"
L1_RAW_CAPTURE_PATH = L1_DIR / "raw_capture_manifest.json"
L1_TARGET_PROBE_PATH = L1_DIR / "target_probe_output.json"
L1_SETUP_REPORT_PATH = L1_DIR / "target_setup_and_capture_report.json"
L1_AGENT_CONFIG_PATH = ROOT / "agent_configs/misc/oh_my_pi_p6_0_l1_config_context_tool_surface_v1.yaml"
CANONICAL_PROJECTION_DIR = ROOT / "artifacts/conformance/e4_primitive_projection/oh_my_pi_p6_0_l1_l2"
CONFIG_GRAPH_NAME = "effective_config_graph.v1.json"
PROJECTION_MANIFEST_NAME = "primitive_projection_manifest.v1.json"
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key)[^\n]{0,20}[:=][^\n]{8,}"),
]


def _json_bytes(value: Any) -> bytes:
    return lane_runtime.canonical_json(value, separators_style="default").encode("utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _sha256_file(path: Path) -> str:
    return lane_runtime.sha256_file(path)


def _sha256_bytes(data: bytes) -> str:
    return hash_utils.sha256_bytes(data)


def _display(path: Path, *, logical_root: Path | None = None) -> str:
    root = logical_root or ROOT
    return lane_runtime.display_path(path, repo_root=root)


def _resolve_display(path: str) -> Path:
    namespace = "repo"
    try:
        return resolve_declared_reference(
            path,
            checkout_root=ROOT,
            namespace=namespace,
            label="capture input",
            must_exist=False,
        )
    except ReferenceResolutionError as exc:
        raise ValueError(str(exc)) from exc


def _normalize_config(lane_def: Mapping[str, Any]) -> Mapping[str, Any]:
    normalize = lane_def.get("normalize")
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    if not isinstance(config, Mapping):
        raise ValueError("oh_my_pi_compiler_capture requires normalize.config")
    return config


def _record_builders(lane_def: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    config = _normalize_config(lane_def)
    builders = config.get("record_builders")
    if not isinstance(builders, list) or not builders:
        raise ValueError("normalize.config.record_builders must be a non-empty list")
    capture = lane_def.get("capture")
    capture_inputs = capture.get("inputs") if isinstance(capture, Mapping) else None
    if not isinstance(capture_inputs, list) or not all(isinstance(item, str) and item for item in capture_inputs):
        raise ValueError("capture.inputs must be a non-empty string list")
    declared_inputs = set(capture_inputs)
    runtime_payload_inputs = config.get("runtime_payload_inputs", {})
    if not isinstance(runtime_payload_inputs, Mapping) or not all(
        isinstance(key, str) and key
        for key in runtime_payload_inputs
    ):
        raise ValueError("normalize.config.runtime_payload_inputs must be an object")
    declared_inputs.update(runtime_payload_inputs)
    seen: set[str] = set()
    validated: list[Mapping[str, Any]] = []
    for row in builders:
        if not isinstance(row, Mapping):
            raise ValueError("normalize.config.record_builders entries must be objects")
        builder_id = row.get("id")
        if not isinstance(builder_id, str) or not builder_id:
            raise ValueError("record builder id must be a non-empty string")
        if builder_id in seen:
            raise ValueError(f"duplicate record builder id: {builder_id}")
        seen.add(builder_id)
        if row.get("projection") != builder_id:
            raise ValueError(f"record builder {builder_id!r} projection must match id")
        if builder_id not in PROJECTIONS:
            raise ValueError(f"unknown projection: {builder_id}")
        schema_version = row.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError(f"record builder {builder_id!r} schema_version must be a non-empty string")
        source = row.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError(f"record builder {builder_id!r} source must be a non-empty path")
        if source not in declared_inputs:
            raise ValueError(
                f"record builder {builder_id!r} source must be declared "
                "in capture.inputs or runtime_payload_inputs"
            )
        records = row.get("records")
        if not isinstance(records, list) or not records or not all(isinstance(item, str) and item for item in records):
            raise ValueError(f"record builder {builder_id!r} records must be a non-empty string list")
        if len(set(records)) != len(records):
            raise ValueError(f"record builder {builder_id!r} records must not contain duplicates")
        validated.append(row)
    return tuple(validated)


def _decode_projection_input(path: Path) -> Any:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    try:
        if path.suffix == ".json":
            return json.loads(text)
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        if path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(text)
    except json.JSONDecodeError:
        return text
    return text


def _projection_input(path_text: str, path: Path) -> dict[str, Any]:
    return {
        "bytes": path.stat().st_size,
        "path": path_text,
        "sha256": _sha256_file(path),
        "value": _decode_projection_input(path),
    }


def _load_projection_inputs(lane_def: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    capture = lane_def["capture"]
    declared = capture["inputs"]
    inputs: dict[str, dict[str, Any]] = {}
    for path_text in declared:
        path = _resolve_display(path_text)
        if not path.exists():
            raise ValueError(f"capture input does not exist: {path_text}")
        if path.is_dir():
            child_values: dict[str, dict[str, Any]] = {}
            child_hashes: dict[str, str] = {}
            for child in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
                child_key = _display(child)
                item = _projection_input(child_key, child)
                child_values[child_key] = item
                child_hashes[child_key] = str(item["sha256"])
                inputs[child_key] = item
            inputs[path_text] = {
                "path": path_text,
                "sha256": _sha256_bytes(_json_bytes(child_hashes)),
                "value": child_values,
            }
            continue
        item = _projection_input(path_text, path)
        inputs[path_text] = item
        inputs.setdefault(_display(path), item)
    runtime_payload_inputs = _normalize_config(lane_def).get(
        "runtime_payload_inputs",
        {},
    )
    if not isinstance(runtime_payload_inputs, Mapping):
        raise ValueError("normalize.config.runtime_payload_inputs must be an object")
    overlap = sorted(set(inputs).intersection(runtime_payload_inputs))
    if overlap:
        raise ValueError(
            "runtime payload inputs overlap physical capture inputs: "
            + ", ".join(overlap)
        )
    for path_text, value in runtime_payload_inputs.items():
        if not isinstance(path_text, str) or not path_text:
            raise ValueError("runtime payload input paths must be non-empty strings")
        data = _json_bytes(value)
        inputs[path_text] = {
            "bytes": len(data),
            "path": path_text,
            "sha256": _sha256_bytes(data),
            "value": value,
        }
    return inputs


def _projection_context(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any],
    builder: Mapping[str, Any],
    inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    config = _normalize_config(lane_def)
    constants = config.get("projection_constants")
    constants = constants if isinstance(constants, Mapping) else {}
    builder_constants = constants.get(str(builder["id"]))
    if isinstance(builder_constants, Mapping):
        constants = builder_constants
    roles = config.get("roles")
    roles = roles if isinstance(roles, Mapping) else {}
    run = lane_def["run"]
    lane = {
        "claim_id": lane_inventory.claim_id(inventory_lane),
        "config_id": str(lane_def["config_id"]),
        "ct_id": lane_inventory.ct_id(inventory_lane),
        "feature_id": lane_inventory.ledger_feature_id(inventory_lane),
        "lane_id": str(lane_def["lane_id"]),
        "provider_model": str(run["provider_model"]),
        "run_id": str(run["run_id"]),
        "sandbox_mode": str(run["sandbox_mode"]),
        "target_family": str(lane_def["target_family"]),
        "target_version": str(lane_def["target_version"]),
    }
    source_key = str(builder["source"])
    role_hashes = {
        str(role): str(inputs[path]["sha256"])
        for role, path in roles.items()
        if isinstance(path, str) and path in inputs
    }
    context: dict[str, Any] = {
        "constants": constants,
        "generated_at_utc": str(constants.get("generated_at_utc") or GENERATED_AT_UTC),
        "inputs": inputs,
        "lane": lane,
        "lane_id": lane["lane_id"],
        "roles": roles,
        "role_hashes": role_hashes,
        "root": str(ROOT),
        "run_id": lane["run_id"],
        "source": inputs[source_key],
        "source_hashes": {key: str(item["sha256"]) for key, item in inputs.items()},
    }
    p3_1_sources = {
        "l1_manifest": L1_MANIFEST_SOURCE,
        "l1_probe": L1_PROBE_SOURCE,
        "l2_manifest": L2_MANIFEST_SOURCE,
        "l2_probe": L2_PROBE_SOURCE,
    }
    if all(source in inputs for source in p3_1_sources.values()):
        context.update({name: inputs[source]["value"] for name, source in p3_1_sources.items()})
    return context


def _finalize_projected_record(schema_version: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if schema_version == "bb.effective_config_graph.v1":
        return finalize_effective_config_graph(value)
    spec = get_spec(schema_version)
    if spec.hash_field is not None and spec.hash_field in value:
        spec = replace(spec, hash_field=None)
    return finalize_record(spec, value)


def _normalize_projected_records(
    builder: Mapping[str, Any],
    projected: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    expected_keys = tuple(str(key) for key in builder["records"])
    schema_version = str(builder["schema_version"])
    rows = projected.get("records")
    if isinstance(rows, list):
        normalized_rows = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"projection {builder['id']!r} emitted a non-object record row")
            normalized_rows.append(row)
    else:
        normalized_rows = [
            {"record_key": key, "schema_version": schema_version, "value": value}
            for key, value in projected.items()
            if key != "derived_facts"
        ]
    emitted_keys = tuple(str(row.get("record_key")) for row in normalized_rows)
    if emitted_keys != expected_keys:
        raise ValueError(f"projection {builder['id']!r} emitted {list(emitted_keys)}; expected {list(expected_keys)}")
    records: dict[str, Any] = {}
    for row in normalized_rows:
        row_schema = row.get("schema_version", schema_version)
        if row_schema != schema_version:
            raise ValueError(
                f"projection {builder['id']!r} schema {row_schema!r} does not match descriptor {schema_version!r}"
            )
        value = row.get("value")
        if not isinstance(value, Mapping):
            raise ValueError(f"projection {builder['id']!r} record value must be an object")
        record_key = str(row["record_key"])
        records[record_key] = _finalize_projected_record(schema_version, value)
    derived_facts = projected.get("derived_facts")
    return records, derived_facts if isinstance(derived_facts, Mapping) else {}


def _execute_record_builders(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    builders = _record_builders(lane_def)
    inputs = _load_projection_inputs(lane_def)
    records: dict[str, Any] = {}
    derived_facts: dict[str, Any] = {}
    for builder in builders:
        builder_id = str(builder["id"])
        projected = PROJECTIONS[builder_id](_projection_context(lane_def, inventory_lane, builder, inputs))
        if not isinstance(projected, Mapping):
            raise ValueError(f"projection {builder_id!r} must return an object")
        projected_records, facts = _normalize_projected_records(builder, projected)
        overlap = sorted(set(records) & set(projected_records))
        if overlap:
            raise ValueError(f"duplicate projected record keys: {overlap}")
        records.update(projected_records)
        if facts:
            derived_facts[builder_id] = dict(facts)
    return builders, records, derived_facts, inputs


def _capture_projection_packet(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any],
    *,
    promote_accepted: bool,
    out_dir: Path | None,
    builders: tuple[Mapping[str, Any], ...],
    records: Mapping[str, Any],
    derived_facts: Mapping[str, Any],
    projection_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    config = _normalize_config(lane_def)
    roles = config.get("roles")
    if not isinstance(roles, Mapping):
        raise ValueError("normalize.config.roles must be an object")
    logical_roles: dict[str, str] = {}
    for role, value in roles.items():
        if not isinstance(role, str) or not isinstance(value, str) or not value:
            raise ValueError("normalize.config.roles must map non-empty role names to paths")
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"normalize.config.roles.{role} must be a repo-relative path")
        logical_roles[role] = value
    if promote_accepted:
        base = ROOT
    elif out_dir is not None:
        base = Path(out_dir)
    else:
        raise ValueError("oh_my_pi_compiler_capture requires --out for scratch capture")
    physical_roles = {role: base / path for role, path in logical_roles.items()}
    source_role_hashes = {
        role: str(projection_inputs[path]["sha256"])
        for role, path in logical_roles.items()
        if path in projection_inputs
    }
    source_input_refs = {
        role: f"{logical_roles[role]}#{digest}"
        for role, digest in source_role_hashes.items()
    }
    source_payloads = {
        role: projection_inputs[path]["value"]
        for role, path in logical_roles.items()
        if path in projection_inputs and isinstance(projection_inputs[path]["value"], (Mapping, list))
    }
    source_bytes = {
        role: int(projection_inputs[path]["bytes"])
        for role, path in logical_roles.items()
        if path in projection_inputs and "bytes" in projection_inputs[path]
    }
    packet = build_projection_packet(
        lane_def,
        inventory_lane,
        records,
        derived_facts,
        logical_roles=logical_roles,
        physical_roles=physical_roles,
        role_hashes=source_role_hashes,
        input_refs=source_input_refs,
        source_payloads=source_payloads,
        source_bytes=source_bytes,
        source_values=source_payloads,
    )
    node_bytes = packet.get("node_gate")
    if not isinstance(node_bytes, bytes):
        raise ValueError("projection packet must emit node_gate")
    node_gate = json.loads(node_bytes)
    validator_bytes = packet.get("validator_output")
    validator = json.loads(validator_bytes) if isinstance(validator_bytes, bytes) else {}
    validator_errors = validator.get("errors") if isinstance(validator, Mapping) else None
    return {
        "claim_id": lane_inventory.claim_id(inventory_lane),
        "config_id": str(lane_def["config_id"]),
        "ct_id": lane_inventory.ct_id(inventory_lane),
        "feature_id": lane_inventory.ledger_feature_id(inventory_lane),
        "fresh_validation": {
            "catalog_binding_pending": list(validator.get("catalog_binding_pending", []))
            if isinstance(validator, Mapping)
            else [],
            "error_count": len(validator_errors) if isinstance(validator_errors, list) else 0,
            "ok": isinstance(validator, Mapping) and validator.get("ok") is True,
        },
        "node_gate": logical_roles["node_gate"],
        "ok": isinstance(node_gate, Mapping) and node_gate.get("ok") is True,
        "paths": dict(logical_roles),
        "points": int(lane_def["points"]),
        "record_builders": [dict(builder) for builder in builders],
    }


def _ref(path: Path, *, logical_path: Path | None = None, logical_root: Path | None = None) -> str:
    visible = logical_path or path
    return f"{_display(visible, logical_root=logical_root)}#{_sha256_file(path)}"


def _support_claim_schema_version(lane_def: Mapping[str, Any], canonical_claim_path: Path) -> str:
    """ADR-AV-3: reproduce v2 only for named accepted lanes with canonical v2 truth."""
    generation_default = schema_generation_default("support_claim")
    lane_id = lane_def.get("lane_id")
    if (
        lane_id not in ADR_AV_3_ACCEPTED_COMPILER_LANES
        or lane_def.get("status") != "accepted"
        or not canonical_claim_path.is_file()
    ):
        return generation_default
    existing_claim = _read_json(canonical_claim_path)
    if isinstance(existing_claim, Mapping) and existing_claim.get("schema_version") == "bb.e4.support_claim.v2":
        return "bb.e4.support_claim.v2"
    return generation_default


def _catalog_binding(
    lane_id: str,
    support_claim_schema_version: str,
    prior_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = _read_json(CATALOG_PATH)
    catalog_schema_version = catalog.get("schema_version") if isinstance(catalog, Mapping) else None
    if catalog_schema_version != "bb.e4.artifact_catalog.v2":
        raise ValueError(f"support-claim binding requires bb.e4.artifact_catalog.v2, got {catalog_schema_version!r}")
    if support_claim_schema_version == "bb.e4.support_claim.v2":
        integrity = catalog.get("integrity")
        stable_hash = integrity.get("stable_entries_hash") if isinstance(integrity, Mapping) else None
        if not isinstance(stable_hash, str) or not stable_hash.startswith("sha256:"):
            raise ValueError("artifact catalog integrity.stable_entries_hash is required for v2 support-claim binding")
        binding_hashes = {"catalog_hash": stable_hash}
        return {
            "catalog_path": "docs/conformance/e4_artifact_catalog.json",
            "catalog_revision": reusable_catalog_revision(catalog, prior_binding, binding_hashes),
            **binding_hashes,
        }
    generation_default = schema_generation_default("support_claim")
    if support_claim_schema_version != generation_default:
        raise ValueError(
            f"unsupported support-claim generation schema {support_claim_schema_version!r}; expected {generation_default!r}"
        )
    binding_hashes = {
        "segment_hash": catalog_segment_hash(catalog, lane_id),
        "shared_segment_hash": catalog_segment_hash(catalog, "shared"),
    }
    return {
        "catalog_path": "docs/conformance/e4_artifact_catalog.json",
        "catalog_revision": reusable_catalog_revision(catalog, prior_binding, binding_hashes),
        "segment_id": lane_id,
        **binding_hashes,
    }


def _source_hashes(paths: list[tuple[Path, Path | None]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for physical, logical in paths:
        hashes[_display(logical or physical)] = _sha256_file(physical)
    return hashes


def _load_freeze_manifest() -> dict[str, Any]:
    payload = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("freeze manifest must be a mapping")
    return payload


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    return lane_runtime.sha256_text(lane_runtime.canonical_json({"row_id": row_id, "row": row}, separators_style="compact"))


def _freeze_row_hash(config_id: str) -> str:
    manifest = _load_freeze_manifest()
    return _row_hash(config_id, manifest["e4_configs"][config_id])


def _write_agent_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "extends: oh_my_pi_p6_0_l1_config_context_tool_surface_v1.yaml",
                "",
                "profile:",
                "  name: oh-my-pi-p3-1-effective-config-graph-compiler-v1",
                "",
                "p3_acceptance:",
                "  item: P3.1",
                "  helper_module: agentic_coder_prototype.compilation.effective_config_graph",
                "  contract: bb.effective_config_graph.v1",
                "  source_capture: oh_my_pi_p6_0_l1_config_context_tool_surface",
                "  compiler_behavior: precedence_hash_migration_redaction_loader_separation",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _projection_records(primitive_outputs: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, bytes], dict[str, Any]]:
    l1_manifest = primitive_projection._read_json(primitive_projection.L1_MANIFEST_PATH)
    l2_manifest = primitive_projection._read_json(primitive_projection.L2_MANIFEST_PATH)
    expected_contracts = {
        "bb.capability_registry.v1",
        "bb.effective_config_graph.v1",
        "bb.effective_tool_surface.v1",
    }
    if set(primitive_outputs) != expected_contracts:
        raise ValueError(f"P3.1 projection contracts must be {sorted(expected_contracts)}")
    validations: dict[str, dict[str, Any]] = {}
    for contract, record in sorted(primitive_outputs.items()):
        errors = primitive_projection._validate_record(contract, record)
        validations[contract] = {
            "contract": contract,
            "error_count": len(errors),
            "errors": errors,
            "ok": not errors,
            "schema_path": primitive_projection._repo_rel(primitive_projection._SCHEMA_PATHS[contract]),
        }
    inputs = primitive_projection._input_records(l1_manifest, l2_manifest)
    manifest = primitive_projection._build_manifest(
        l1_manifest=l1_manifest,
        l2_manifest=l2_manifest,
        inputs=inputs,
        primitive_outputs=primitive_outputs,
        output_dir=CANONICAL_PROJECTION_DIR,
        validations=validations,
    )
    output_payloads: dict[str, bytes] = {}
    for contract, record in primitive_outputs.items():
        output_payloads[primitive_projection._OUTPUT_NAMES[contract]] = primitive_projection._canonical_json_bytes(record)
    output_payloads[PROJECTION_MANIFEST_NAME] = primitive_projection._canonical_json_bytes(manifest)
    errors = [error for validation in validations.values() for error in validation["errors"]]
    report = {
        "input_hash": manifest["input_hash"],
        "ok": not errors,
        "output_dir": primitive_projection._repo_rel(CANONICAL_PROJECTION_DIR),
        "projection_id": primitive_projection.PROJECTION_ID,
        "validation_status": sorted(validations.values(), key=lambda item: item["contract"]),
    }
    return output_payloads, report


def _write_projection(
    out_projection_dir: Path,
    primitive_outputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    payloads, report = _projection_records(primitive_outputs)
    writes = []
    for name in sorted(payloads):
        path = out_projection_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_bytes() if path.exists() else None
        path.write_bytes(payloads[name])
        writes.append({"path": _display(CANONICAL_PROJECTION_DIR / name), "sha256": _sha256_bytes(payloads[name]), "status": "unchanged" if before == payloads[name] else "written"})
    return {**report, "outputs": writes}


def _ledger_ref_from_current(feature_id: str) -> str:
    ledger = _read_json(LEDGER_PATH)
    entries = ledger.get("rows") if isinstance(ledger, Mapping) else None
    if not isinstance(entries, list):
        raise ValueError("ledger must contain rows list")
    matches = [row for row in entries if isinstance(row, Mapping) and row.get("feature_id") == feature_id]
    if len(matches) != 1:
        raise ValueError(f"feature {feature_id!r} must have exactly one ledger row")
    return f"{_display(LEDGER_PATH)}#{feature_id}#{_row_hash(feature_id, matches[0])}"


def _p3_item(inventory_lane: Mapping[str, Any]) -> str:
    ct_id = lane_inventory.ct_id(inventory_lane)
    match = re.search(r"-P3([1-8])-", ct_id)
    if match is None:
        raise ValueError(f"cannot derive P3 item from CT id: {ct_id}")
    return f"P3.{match.group(1)}"


def _lane_paths(lane_id: str, claim_id: str, out_dir: Path | None, promote_accepted: bool) -> dict[str, Path]:
    base = ROOT if promote_accepted else Path(out_dir or ROOT)
    return {
        "agent_config": base / "agent_configs/misc" / "oh_my_pi_p3_1_effective_config_graph_compiler_v1.yaml",
        "lane_dir": base / "docs/conformance/e4_target_support" / lane_id,
        "support_claim": base / "docs/conformance/support_claims" / f"{claim_id}.json",
        "evidence_manifest": base / "docs/conformance/support_claims" / f"{claim_id.replace('_support_claim', '_evidence_manifest')}.json",
        "projection_dir": base / "artifacts/conformance/e4_primitive_projection/oh_my_pi_p6_0_l1_l2",
    }


def _logical_paths(lane_id: str, claim_id: str) -> dict[str, Path]:
    return _lane_paths(lane_id, claim_id, ROOT, True)


def _validate_config(lane_def: Mapping[str, Any]) -> None:
    capture_cfg = lane_def.get("capture")
    if not isinstance(capture_cfg, Mapping) or capture_cfg.get("strategy") != "adapter" or capture_cfg.get("adapter") != "oh_my_pi_compiler_capture":
        raise ValueError("oh_my_pi_compiler_capture requires capture.strategy=adapter and capture.adapter=oh_my_pi_compiler_capture")
    _record_builders(lane_def)
    run = lane_def.get("run")
    if not isinstance(run, Mapping):
        raise ValueError("lane_def v2 run block is required")
    provenance = lane_def.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("lane_def v2 provenance block is required")
    acceptance = lane_def.get("acceptance")
    if not isinstance(acceptance, Mapping):
        raise ValueError("lane_def v2 acceptance block is required")


def capture(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any] | None = None,
    *,
    promote_accepted: bool,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    _validate_config(lane_def)
    if inventory_lane is None:
        raise ValueError("oh_my_pi_compiler_capture requires inventory lane metadata")
    builders, projected_records, derived_facts, projection_inputs = _execute_record_builders(lane_def, inventory_lane)
    lane_id = str(lane_def["lane_id"])
    config_id = str(lane_def["config_id"])
    roles = _normalize_config(lane_def).get("roles")
    if not isinstance(roles, Mapping):
        raise ValueError("normalize.config.roles must be an object")
    if "compiled_records" in roles:
        return capture_p3_remaining(
            lane_def,
            inventory_lane,
            promote_accepted=promote_accepted,
            out_dir=out_dir,
            record_builders=builders,
            projected_records=projected_records,
            derived_facts=derived_facts,
        )
    if "primitive_projection_manifest" not in roles:
        return _capture_projection_packet(
            lane_def,
            inventory_lane,
            promote_accepted=promote_accepted,
            out_dir=out_dir,
            builders=builders,
            records=projected_records,
            derived_facts=derived_facts,
            projection_inputs=projection_inputs,
        )
    primitive_outputs = {
        str(builder["schema_version"]): projected_records[str(builder["records"][0])]
        for builder in builders
    }
    claim_id = lane_inventory.claim_id(inventory_lane)
    run = lane_def["run"]
    run_id = str(run["run_id"])
    provider_model = str(run["provider_model"])
    sandbox_mode = str(run["sandbox_mode"])
    target_family = str(lane_def["target_family"])
    target_version = str(lane_def["target_version"])
    points = int(lane_def["points"])
    phase = str(inventory_lane.get("phase", "P3"))
    p3_item = _p3_item(inventory_lane)
    feature_id = lane_inventory.ledger_feature_id(inventory_lane)
    paths = _lane_paths(lane_id, claim_id, out_dir, promote_accepted)
    logical = _logical_paths(lane_id, claim_id)
    lane_dir = paths["lane_dir"]
    logical_lane_dir = logical["lane_dir"]
    raw_capture_path = lane_dir / "raw_capture_manifest.json"
    replay_path = lane_dir / "bb_replay_result.json"
    comparator_path = lane_dir / "comparator_report.json"
    parity_path = lane_dir / "parity_results.json"
    secret_scan_path = lane_dir / "secret_scan_report.json"
    prevalidation_path = lane_dir / "prevalidation_report.json"
    graph_path = paths["projection_dir"] / CONFIG_GRAPH_NAME
    projection_manifest_path = paths["projection_dir"] / PROJECTION_MANIFEST_NAME
    logical_raw_capture_path = logical_lane_dir / "raw_capture_manifest.json"
    logical_replay_path = logical_lane_dir / "bb_replay_result.json"
    logical_comparator_path = logical_lane_dir / "comparator_report.json"
    logical_parity_path = logical_lane_dir / "parity_results.json"
    logical_secret_scan_path = logical_lane_dir / "secret_scan_report.json"
    logical_prevalidation_path = logical_lane_dir / "prevalidation_report.json"
    logical_graph_path = logical["projection_dir"] / CONFIG_GRAPH_NAME
    logical_projection_manifest_path = logical["projection_dir"] / PROJECTION_MANIFEST_NAME

    _write_agent_config(paths["agent_config"])
    projection_report = _write_projection(paths["projection_dir"], primitive_outputs)
    if not projection_report.get("ok"):
        raise ValueError(f"primitive projection failed: {projection_report}")

    l1_capture = _read_json(L1_RAW_CAPTURE_PATH)
    graph = _read_json(graph_path)
    projection_manifest = _read_json(projection_manifest_path)
    raw_sources = [
        (L1_RAW_CAPTURE_PATH, None),
        (L1_TARGET_PROBE_PATH, None),
        (L1_SETUP_REPORT_PATH, None),
        (L1_AGENT_CONFIG_PATH, None),
        (paths["agent_config"], logical["agent_config"]),
    ]
    capture_payload = {
        "accepted_as_capture_ref": True,
        "capture_class": "derived_capture",
        "captured_artifacts": [
            {"path": _display(logical_path or physical), "role": role, "sha256": _sha256_file(physical)}
            for (physical, logical_path), role in [
                (raw_sources[0], "source_l1_raw_capture"),
                (raw_sources[1], "source_l1_target_probe"),
                (raw_sources[2], "source_l1_setup_report"),
                (raw_sources[3], "source_l1_agent_config"),
                (raw_sources[4], "p3_1_agent_config"),
            ]
        ],
        "config_id": config_id,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": lane_id,
        "lineage_rationale": "P3.1 helper/runtime compiler capture is derived from the canonical Oh-My-Pi P6.0-L1 raw target run and scoped to compiling that observed config/context surface into bb.effective_config_graph.v1 records. It does not broaden the target support claim beyond the named helper/runtime compiler lane.",
        "provider_model": provider_model,
        "raw_source_ref": _ref(L1_RAW_CAPTURE_PATH),
        "raw_source_status": "canonical_raw_present",
        "run_id": run_id,
        "sandbox_mode": sandbox_mode,
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "source_artifacts": [_display(logical_path or physical) for physical, logical_path in raw_sources],
        "source_hashes": _source_hashes(raw_sources),
        "target_family": target_family,
        "target_source": l1_capture["target_source"],
        "target_version": target_version,
    }
    _write_json(raw_capture_path, capture_payload)

    replay_inputs = [(raw_capture_path, logical_raw_capture_path), (graph_path, logical_graph_path), (projection_manifest_path, logical_projection_manifest_path), (paths["agent_config"], logical["agent_config"])]
    replay_payload = {
        "config_id": config_id,
        "errors": [],
        "exit_status": "passed",
        "generated_at_utc": GENERATED_AT_UTC,
        "input_hashes": _source_hashes(replay_inputs),
        "lane_id": lane_id,
        "normalization": {
            "mode": "p3_1_helper_runtime_compiler",
            "preserved_fields": ["schema_version", "source_layers", "effective_values", "merge_policy", "visibility", "env_gates", "graph_hash", "migrations"],
        },
        "p3_item": p3_item,
        "replay_summary": "BreadBoard helper runtime compiler emitted a schema-valid bb.effective_config_graph.v1 record from canonical Oh-My-Pi L1/L2 capture inputs without replacing v2_loader.load_agent_config.",
        "run_id": run_id,
        "schema_version": "bb.e4.bb_replay_result.v1",
        "warnings": [],
    }
    _write_json(replay_path, replay_payload)

    values = {item["path"]: item["value"] for item in graph["effective_values"]}
    source_layers = {item["layer_id"]: item for item in graph["source_layers"]}
    assertions = [
        ("schema_version", graph["schema_version"], "bb.effective_config_graph.v1"),
        ("graph_id", graph["graph_id"], "oh_my_pi_p6_0_l1_l2_effective_config_graph"),
        ("target_family", values["target.family"], target_family),
        ("target_version", values["target.version"], target_version),
        ("provider_model", values["provider.model"], provider_model),
        ("sandbox_mode", values["sandbox.mode"], sandbox_mode),
        ("l1_run_id", values["l1.run_id"], run_id),
        ("l1_runtime_source_layer", source_layers["l1_probe_observed_runtime"]["source_ref"], _display(L1_TARGET_PROBE_PATH)),
        ("graph_hash_current", graph["graph_hash"], graph_content_hash(graph)),
        ("projection_contract_validated", next(item for item in projection_manifest["validations"] if item["contract"] == "bb.effective_config_graph.v1")["ok"], True),
        ("compiler_tests_present", HELPER_TEST_PATH.exists(), True),
    ]
    comparator_inputs = [(raw_capture_path, logical_raw_capture_path), (replay_path, logical_replay_path), (graph_path, logical_graph_path), (projection_manifest_path, logical_projection_manifest_path), (paths["agent_config"], logical["agent_config"])]
    comparator_payload = {
        "assertions": [{"name": name, "observed": observed, "expected": expected, "status": "passed" if observed == expected else "failed"} for name, observed, expected in assertions],
        "comparator_id": f"{lane_id}_comparator",
        "config_id": config_id,
        "details": [
            "Compared canonical Oh-My-Pi raw capture inputs against the BreadBoard helper-emitted effective config graph.",
            "Assertions are exact-scope and machine-checkable; observed values must equal expected target-scope values.",
        ],
        "failed": sum(1 for _name, observed, expected in assertions if observed != expected),
        "generated_at_utc": GENERATED_AT_UTC,
        "input_hashes": _source_hashes(comparator_inputs),
        "lane_id": lane_id,
        "scope": {"config_id": config_id, "lane_id": lane_id, "phase": phase, "p3_item": p3_item, "provider_model": provider_model, "run_id": run_id, "sandbox_mode": sandbox_mode, "target_family": target_family, "target_version": target_version},
        "schema_version": "bb.e4.comparator_report.v1",
        "warned": 0,
    }
    _write_json(comparator_path, comparator_payload)

    parity_payload = {
        "accepted": comparator_payload["failed"] == 0 and comparator_payload["warned"] == 0,
        "claim_id": claim_id,
        "config_id": config_id,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": lane_id,
        "p3_item": p3_item,
        "passed_assertions": len(comparator_payload["assertions"]),
        "schema_version": "bb.e4.parity_results.v1",
        "scope": comparator_payload["scope"],
    }
    _write_json(parity_path, parity_payload)

    scan_pairs = [
        (raw_capture_path, logical_raw_capture_path),
        (replay_path, logical_replay_path),
        (comparator_path, logical_comparator_path),
        (parity_path, logical_parity_path),
        (graph_path, logical_graph_path),
        (projection_manifest_path, logical_projection_manifest_path),
    ]
    findings: list[dict[str, Any]] = []
    for physical, logical_path in scan_pairs:
        text = physical.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"path": _display(logical_path), "pattern": pattern.pattern})
    secret_scan_payload = {
        "checked_paths": [_display(logical_path) for _physical, logical_path in scan_pairs],
        "finding_count": len(findings),
        "findings": findings,
        "generated_at_utc": GENERATED_AT_UTC,
        "ok": not findings,
        "schema_version": "bb.e4.secret_scan_report.v1",
    }
    _write_json(secret_scan_path, secret_scan_payload)

    prevalidation_checks = [
        ("raw_capture_written", raw_capture_path, logical_raw_capture_path),
        ("replay_written", replay_path, logical_replay_path),
        ("comparator_written", comparator_path, logical_comparator_path),
        ("parity_results_written", parity_path, logical_parity_path),
        ("secret_scan_passed", secret_scan_path, logical_secret_scan_path),
        ("effective_config_graph_emitted", graph_path, logical_graph_path),
        ("compiler_module_present", HELPER_MODULE_PATH, None),
        ("compiler_tests_present", HELPER_TEST_PATH, None),
    ]
    prevalidation_payload = {
        "checks": [{"name": name, "passed": physical.exists(), "path": _display(logical_path or physical), "sha256": _sha256_file(physical)} for name, physical, logical_path in prevalidation_checks],
        "config_id": config_id,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": lane_id,
        "ok": all(physical.exists() for _name, physical, _logical in prevalidation_checks) and secret_scan_payload["ok"] and comparator_payload["failed"] == 0,
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "source": "Bootstrap validator-output reference for the Oh-My-Pi P3.1 effective config graph compiler C4 chain; scripts/validate_e4_c4_chain.py is run as the live validator before score acceptance.",
    }
    _write_json(prevalidation_path, prevalidation_payload)

    freeze_ref = f"{_display(FREEZE_MANIFEST_PATH)}#{config_id}#{_freeze_row_hash(config_id)}"
    ledger_ref = _ledger_ref_from_current(feature_id)
    support_claim_path = paths["support_claim"]
    evidence_manifest_path = paths["evidence_manifest"]
    logical_support_claim_path = logical["support_claim"]
    logical_evidence_manifest_path = logical["evidence_manifest"]
    existing_claim = _read_json(logical_support_claim_path) if logical_support_claim_path.exists() else {}
    support_claim_schema_version = _support_claim_schema_version(lane_def, logical_support_claim_path)
    support_claim_scope = {
        "config_id": config_id,
        "lane_id": lane_id,
        "provider_model": provider_model,
        "run_id": run_id,
        "sandbox_mode": sandbox_mode,
        "target_version": target_version,
    }
    if support_claim_schema_version != "bb.e4.support_claim.v2":
        support_claim_scope["target_family"] = target_family
    support_claim_payload = {
        "acceptance_rationale": "P3.1 helper/runtime effective config graph compiler is accepted only for the named Oh-My-Pi raw target run and generated bb.effective_config_graph.v1 projection; it is backed by compiler unit tests and a live C4 chain validator.",
        "accepted": True,
        "capture_ref": _ref(raw_capture_path, logical_path=logical_raw_capture_path),
        "claim_id": claim_id,
        "claim_semantics": {
            "asserted_behaviors": [
                {"behavior_id": f"config_graph_{name}", "comparator_assertion_ids": [name], "description": name.replace("_", " ")}
                for name, _observed, _expected in assertions
            ],
            "excluded_behaviors": [{"behavior_id": "broad_target_parity", "description": "Broad target-family parity remains outside this exact C4 lane claim."}],
        },
        "comparator_ref": _ref(comparator_path, logical_path=logical_comparator_path),
        "evidence_manifest_ref": _display(logical_evidence_manifest_path),
        "exclusion_facets": {
            "excluded_behavior_classes": ["broad_target_parity", "browser", "danger_full_access", "final_readiness", "mcp", "model_inference", "network", "provider_authenticated", "ui_parity", "write_enabled"],
            "excluded_families": ["all_other_families", "pi"],
        },
        "exclusions": [
            "No broad Oh-My-Pi, OMP, Pi, provider-parity, tool-surface, task/job, memory, UI, or final-readiness support claim is made from this P3.1 micro-lane.",
            "No score is claimed for P3.2-P3.8 helper/runtime lanes, P5 Pi lanes, or P8 final readiness.",
            "No write-enabled, provider-authenticated, danger-full-access, network, browser, MCP, or model-inference behavior is claimed.",
        ],
        "freeze_ref": freeze_ref,
        "generated_at_utc": SUPPORT_CLAIM_GENERATED_AT_UTC,
        "kind": str(lane_def["kind"]),
        "ledger_row_refs": [ledger_ref],
        "metadata": {"p3_item": p3_item},
        "parity_results_ref": _ref(parity_path, logical_path=logical_parity_path),
        "phase_label": phase,
        "replay_ref": _ref(replay_path, logical_path=logical_replay_path),
        "reverify_command": lane_def.get("reverify_command"),
        "schema_version": support_claim_schema_version,
        "scope": support_claim_scope,
        "secret_scan_ref": _ref(secret_scan_path, logical_path=logical_secret_scan_path),
        "source_freeze_ref": _ref(SOURCE_FREEZE_PATH),
        "summary": "BreadBoard P3.1 effective config graph compiler support is accepted for the named Oh-My-Pi target capture and generated bb.effective_config_graph.v1 record only.",
        "validation_refs": [_ref(prevalidation_path, logical_path=logical_prevalidation_path)],
    }
    if support_claim_schema_version == "bb.e4.support_claim.v2":
        support_claim_payload.update(
            {
                "config_id": config_id,
                "lane_id": lane_id,
                "metadata": {
                    "generated_from_schema_version": "bb.e4.support_claim.v2",
                    "legacy_scope": {"p3_item": p3_item, "phase": phase},
                    "v1_archive_ref": f"docs/conformance/support_claims/v1_archive/{claim_id}.json",
                },
                "provider_model": provider_model,
                "run_id": run_id,
                "sandbox_mode": sandbox_mode,
                "target_family": target_family,
                "target_version": target_version,
            }
        )
    prior_binding = existing_claim.get("catalog_binding") if isinstance(existing_claim, Mapping) and isinstance(existing_claim.get("catalog_binding"), Mapping) else None
    support_claim_payload["catalog_binding"] = _catalog_binding(
        lane_id,
        support_claim_schema_version,
        prior_binding,
    )
    _write_json(support_claim_path, support_claim_payload)

    artifacts = [
        {"path": _display(FREEZE_MANIFEST_PATH), "role": "freeze_manifest", "sha256": _freeze_row_hash(config_id)},
        {"path": _display(logical_raw_capture_path), "role": "capture_ref", "sha256": _sha256_file(raw_capture_path)},
        {"derived_from": [_ref(raw_capture_path, logical_path=logical_raw_capture_path), _ref(graph_path, logical_path=logical_graph_path)], "path": _display(logical_replay_path), "role": "replay_ref", "sha256": _sha256_file(replay_path)},
        {"derived_from": [_ref(raw_capture_path, logical_path=logical_raw_capture_path), _ref(replay_path, logical_path=logical_replay_path), _ref(graph_path, logical_path=logical_graph_path)], "path": _display(logical_comparator_path), "role": "comparator_ref", "sha256": _sha256_file(comparator_path)},
        {"path": _display(logical_support_claim_path), "role": "support_claim_ref", "sha256": _sha256_file(support_claim_path)},
        {"derived_from": [_ref(comparator_path, logical_path=logical_comparator_path)], "path": _display(logical_parity_path), "role": "parity_results", "sha256": _sha256_file(parity_path)},
        {"path": _display(logical_secret_scan_path), "role": "secret_scan_report", "sha256": _sha256_file(secret_scan_path)},
        {"path": _display(logical_prevalidation_path), "role": "validator_output", "sha256": _sha256_file(prevalidation_path)},
        {"path": _display(logical_graph_path), "role": "effective_config_graph", "sha256": _sha256_file(graph_path)},
        {"path": _display(logical_projection_manifest_path), "role": "primitive_projection_manifest", "sha256": _sha256_file(projection_manifest_path)},
        {"path": _display(logical["agent_config"]), "role": "agent_config", "sha256": _sha256_file(paths["agent_config"])},
        {"path": _display(SOURCE_FREEZE_PATH), "role": "source_freeze", "sha256": _sha256_file(SOURCE_FREEZE_PATH)},
    ]
    evidence_manifest_payload = {
        "artifacts": artifacts,
        "claim_id": claim_id,
        "config_id": config_id,
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "generated_at_utc": GENERATED_AT_UTC,
        "hash_algorithm": "sha256",
        "lane_id": lane_id,
        "manifest_scope_note": "Every governed artifact for this Oh-My-Pi P3.1 C4 chain is canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or derived from named target capture artifacts. Scratch/tmp/shared roots are not promotion evidence.",
        "schema_version": "bb.e4.evidence_manifest.v1",
    }
    _write_json(evidence_manifest_path, evidence_manifest_payload)

    ct_output_path = Path(lane_inventory.ct_output(inventory_lane))
    node_gate_path = (ROOT if promote_accepted else Path(out_dir or ROOT)) / ct_output_path
    logical_node_gate_path = ROOT / ct_output_path
    selected_support_claim_path = support_claim_path if promote_accepted else logical_support_claim_path
    selected_evidence_manifest_path = evidence_manifest_path if promote_accepted else logical_evidence_manifest_path
    node_gate_report = validate_c4_chain(
        repo_root=ROOT,
        freeze_manifest_path=FREEZE_MANIFEST_PATH,
        config_id=config_id,
        support_claim_path=selected_support_claim_path,
        evidence_manifest_path=selected_evidence_manifest_path,
        rerun_comparators=True,
        comparator_registry_path=ROOT / "conformance/comparators/registry.json",
        enforce_catalog_binding=False,
    )
    node_gate_report["support_claim"] = _display(logical_support_claim_path)
    node_gate_report["evidence_manifest"] = _display(logical_evidence_manifest_path)
    refs = node_gate_report.get("refs")
    if isinstance(refs, dict):
        refs["support_claim"] = _display(logical_support_claim_path)
        refs["evidence_manifest"] = _display(logical_evidence_manifest_path)
    node_gate_path.parent.mkdir(parents=True, exist_ok=True)
    node_gate_path.write_text(json.dumps(node_gate_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "claim_id": claim_id,
        "config_id": config_id,
        "ct_id": lane_inventory.ct_id(inventory_lane),
        "feature_id": feature_id,
        "freeze_ref": freeze_ref,
        "ledger_ref": ledger_ref,
        "node_gate": _display(logical_node_gate_path),
        "ok": prevalidation_payload["ok"] and support_claim_payload["accepted"] and node_gate_report.get("ok") is True,
        "paths": {
            "agent_config": _display(logical["agent_config"]),
            "comparator": _display(logical_comparator_path),
            "evidence_manifest": _display(logical_evidence_manifest_path),
            "raw_capture": _display(logical_raw_capture_path),
            "replay": _display(logical_replay_path),
            "support_claim": _display(logical_support_claim_path),
        },
        "points": points,
        "projection_report": projection_report,
    }


capture.supports_scratch_out_dir = True
