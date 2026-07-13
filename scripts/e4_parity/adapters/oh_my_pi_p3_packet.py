from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator, RefResolver

from agentic_coder_prototype.conformance.catalog_binding import (
    CATALOG_PATH as CATALOG_BINDING_PATH,
    catalog_segment_hash,
    reusable_catalog_revision,
)
from scripts.e4_parity import lane_runtime

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
GENERATED_AT_UTC = "2026-07-03T07:30:00Z"
FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
SUPPORT_CLAIM_GENERATED_AT_UTC = "2026-07-04T00:00:00Z"
CATALOG_PATH = ROOT / "docs/conformance/e4_artifact_catalog.json"
SCHEMA_DIR = ROOT / "contracts/kernel/schemas"
SOURCE_FREEZE_PATH = ROOT / "config/e4_lanes/evidence_inputs/oh_my_pi_main_5356713e_freeze_provenance.v1.json"
SOURCE_L1_CAPTURE_PATH = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/raw_capture_manifest.json"
SOURCE_L1_PROBE_PATH = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json"
SOURCE_L1_SETUP_PATH = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_setup_and_capture_report.json"
HELPER_MODULE_PATH = ROOT / "agentic_coder_prototype/compilation/helper_runtime_primitives.py"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key)[^\n]{0,20}[:=][^\n]{8,}"),
]

SCHEMA_BY_RECORD_KEY = {
    "context_resource_pack": "bb.context_resource_pack.v1",
    "capability_registry": "bb.capability_registry.v1",
    "extension_hook_execution": "bb.extension_hook_execution.v1",
    "resource_ref": "bb.resource_ref.v1",
    "resource_access": "bb.resource_access.v1",
    "write_resource_access": "bb.resource_access.v1",
    "truncated_redacted_access": "bb.resource_access.v1",
    "blob_ref": "bb.blob_ref.v1",
    "external_protocol_session": "bb.external_protocol_session.v1",
    "provider_route": "bb.provider_route.v1",
    "effective_operation_policy": "bb.effective_operation_policy.v1",
    "memory_compaction_plan": "bb.memory_compaction_plan.v1",
    "work_item": "bb.work_item.v1",
    "projection_event": "bb.projection_event.v1",
    "side_effect_broker": "bb.side_effect_broker.v1",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    payload = {"row_id": row_id, "row": row}
    return lane_runtime.sha256_text(lane_runtime.canonical_json(payload, separators_style="compact"))


def freeze_row_hash(config_id: str) -> str:
    manifest = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise TypeError("freeze manifest must be an object")
    configs = manifest.get("e4_configs")
    if not isinstance(configs, Mapping) or not isinstance(configs.get(config_id), Mapping):
        raise ValueError(f"freeze manifest has no config row {config_id!r}")
    return _row_hash(config_id, configs[config_id])


def _schema_store() -> dict[str, Any]:
    store = {path.name: _read_json(path) for path in SCHEMA_DIR.glob("*.schema.json")}
    store.update({schema.get("$id", name): schema for name, schema in store.items()})
    return store


def _schema_errors(schema_version: str, record: Mapping[str, Any], store: Mapping[str, Any]) -> list[str]:
    schema = _read_json(SCHEMA_DIR / f"{schema_version}.schema.json")
    resolver = RefResolver(base_uri=SCHEMA_DIR.as_uri() + "/", referrer=schema, store=dict(store))
    validator = Draft202012Validator(schema, resolver=resolver)
    errors = []
    for error in sorted(
        validator.iter_errors(record),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    ):
        path = ".".join(str(part) for part in error.absolute_path)
        errors.append(f"{schema_version}:{path}: {error.message}" if path else f"{schema_version}: {error.message}")
    return errors


def validate_records(records: Mapping[str, Any]) -> dict[str, Any]:
    store = _schema_store()
    checks = []
    errors: list[str] = []
    for record_key, record in records.items():
        schema_version = SCHEMA_BY_RECORD_KEY.get(record_key)
        if schema_version is None:
            row_errors = [f"{record_key}: no schema mapping"]
        elif not isinstance(record, Mapping):
            row_errors = [f"{record_key}: record must be an object"]
        else:
            row_errors = _schema_errors(schema_version, record, store)
        checks.append(
            {
                "record": record_key,
                "schema": schema_version,
                "ok": not row_errors,
                "errors": row_errors,
            }
        )
        errors.extend(row_errors)
    return {
        "schema_version": "bb.e4.helper_runtime_schema_validation.v1",
        "generated_at_utc": GENERATED_AT_UTC,
        "ok": not errors,
        "checks": checks,
        "errors": errors,
    }


def write_agent_config(spec: Mapping[str, Any], paths: Mapping[str, Path]) -> None:
    payload = {
        "schema_version": "bb.e4.helper_runtime_lane_config.v1",
        "config_id": spec["config_id"],
        "lane_id": spec["lane_id"],
        "phase": "P3",
        "p3_item": spec["p3_item"],
        "target_family": spec["target_family"],
        "target_version": spec["target_version"],
        "provider_model": spec["provider_model"],
        "sandbox_mode": spec["sandbox_mode"],
        "compiler": spec["tool_id"],
        "primary_primitive": spec["primitive"],
        "source_capture_ref": lane_runtime.display_path(SOURCE_L1_CAPTURE_PATH, repo_root=ROOT),
    }
    path = paths["agent_config"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, width=120), encoding="utf-8")


def comparator_assertions(
    expected_facts: Mapping[str, Any],
    observed_facts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    missing = sorted(set(expected_facts) - set(observed_facts))
    extra = sorted(set(observed_facts) - set(expected_facts))
    if missing or extra:
        raise ValueError(f"comparator fact mismatch: missing={missing}, extra={extra}")
    return [
        {
            "name": name,
            "status": "passed" if observed_facts[name] == expected else "failed",
            "observed": observed_facts[name],
            "expected": expected,
        }
        for name, expected in expected_facts.items()
    ]


def _catalog_binding(lane: Mapping[str, Any]) -> dict[str, Any]:
    catalog = _read_json(CATALOG_PATH)
    lane_id = lane.get("lane_id")
    if not isinstance(lane_id, str) or not lane_id:
        raise ValueError("lane requires a non-empty lane_id for catalog binding")
    binding_hashes = {
        "segment_hash": catalog_segment_hash(catalog, lane_id),
        "shared_segment_hash": catalog_segment_hash(catalog, "shared"),
    }
    return {
        "catalog_path": CATALOG_BINDING_PATH,
        "catalog_revision": reusable_catalog_revision(catalog, None, binding_hashes),
        "segment_id": lane_id,
        **binding_hashes,
    }


def _ledger_ref(feature_id: str) -> str:
    ledger = _read_json(LEDGER_PATH)
    rows = ledger.get("rows") if isinstance(ledger, Mapping) else None
    matches = [row for row in rows or [] if isinstance(row, Mapping) and row.get("feature_id") == feature_id]
    if len(matches) != 1:
        raise ValueError(f"ledger requires exactly one row for {feature_id!r}")
    path = lane_runtime.display_path(LEDGER_PATH, repo_root=ROOT)
    return f"{path}#{feature_id}#{_row_hash(feature_id, matches[0])}"


def build_support_claim(
    spec: Mapping[str, Any],
    inventory_lane: Mapping[str, Any],
    refs: Mapping[str, str],
) -> dict[str, Any]:
    expected = spec.get("comparator_expected")
    if not isinstance(expected, Mapping):
        raise ValueError("spec.comparator_expected must be an object")
    prefix = str(spec["behavior_prefix"])
    p3_item = str(spec["p3_item"])
    claim_id = str(spec["claim_id"])
    asserted_behaviors = [
        {
            "behavior_id": f"{prefix}_{name}",
            "comparator_assertion_ids": [name],
            "description": name.replace("_", " "),
        }
        for name in expected
    ]
    exclusions = [
        f"No broad Oh-My-Pi, OMP, Pi, provider-parity, UI, final-readiness, or target-customization support claim is made from this {p3_item} helper/runtime lane.",
        "No score is claimed for P5 Pi lanes or P8 final readiness from this P3 helper/runtime evidence.",
        "No provider-authenticated, danger-full-access, network, browser, MCP, or model-inference behavior is claimed.",
    ]
    return {
        "acceptance_rationale": f"{p3_item} helper/runtime support is accepted only for the named source-derived compiler fixture; it is backed by schema validation, machine comparator assertions, and a live C4 chain validator.",
        "accepted": True,
        "capture_ref": refs["capture_ref"],
        "catalog_binding": _catalog_binding(inventory_lane),
        "claim_id": claim_id,
        "claim_semantics": {
            "asserted_behaviors": asserted_behaviors,
            "excluded_behaviors": [
                {
                    "behavior_id": "broad_target_parity",
                    "description": "Broad target-family parity remains outside this exact C4 lane claim.",
                }
            ],
        },
        "comparator_ref": refs["comparator_ref"],
        "evidence_manifest_ref": refs["evidence_manifest_ref"],
        "exclusion_facets": {
            "excluded_behavior_classes": [
                "broad_target_parity",
                "browser",
                "danger_full_access",
                "final_readiness",
                "mcp",
                "model_inference",
                "network",
                "provider_authenticated",
                "ui_parity",
            ],
            "excluded_families": ["all_other_families", "pi"],
        },
        "exclusions": exclusions,
        "freeze_ref": refs["freeze_ref"],
        "generated_at_utc": SUPPORT_CLAIM_GENERATED_AT_UTC,
        "kind": spec["kind"],
        "ledger_row_refs": [_ledger_ref(str(spec["feature_id"]))],
        "metadata": {
            "generated_from_schema_version": "bb.e4.support_claim.v4",
            "legacy_scope": {"p3_item": p3_item, "phase": "P3"},
            "v1_archive_ref": f"docs/conformance/support_claims/v1_archive/{claim_id}.json",
        },
        "parity_results_ref": refs["parity_results_ref"],
        "phase_label": "P3",
        "replay_ref": refs["replay_ref"],
        "reverify_command": inventory_lane["reverify_command"],
        "schema_version": "bb.e4.support_claim.v4",
        "scope": {
            "config_id": spec["config_id"],
            "lane_id": spec["lane_id"],
            "provider_model": spec["provider_model"],
            "run_id": spec["run_id"],
            "sandbox_mode": spec["sandbox_mode"],
            "target_family": spec["target_family"],
            "target_version": spec["target_version"],
        },
        "secret_scan_ref": refs["secret_scan_ref"],
        "source_freeze_ref": refs["source_freeze_ref"],
        "summary": spec["summary"],
        "validation_refs": [refs["validation_ref"]],
    }


__all__ = [
    "GENERATED_AT_UTC",
    "FREEZE_MANIFEST_PATH",
    "HELPER_MODULE_PATH",
    "LEDGER_PATH",
    "SCHEMA_BY_RECORD_KEY",
    "SECRET_PATTERNS",
    "SOURCE_FREEZE_PATH",
    "SOURCE_L1_CAPTURE_PATH",
    "SOURCE_L1_PROBE_PATH",
    "SOURCE_L1_SETUP_PATH",
    "build_support_claim",
    "comparator_assertions",
    "freeze_row_hash",
    "validate_records",
    "write_agent_config",
]
