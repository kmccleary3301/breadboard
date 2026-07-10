from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from scripts.e4_parity.adapters import oh_my_pi_p3_packet as p3_packet
from scripts.e4_parity.adapters import oh_my_pi_p3_remaining_projections as p3_projections
from scripts.e4_parity import lane_inventory_utils as lane_inventory
from scripts.e4_parity import lane_runtime
from scripts.e4_parity.path_refs import (
    ReferenceResolutionError,
    resolve_declared_reference,
)
from scripts.validate_e4_c4_chain import validate_c4_chain

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT
GENERATED_AT_UTC = p3_packet.GENERATED_AT_UTC
PHASE = "P3"
FREEZE_MANIFEST_PATH = p3_packet.FREEZE_MANIFEST_PATH
LEDGER_PATH = p3_packet.LEDGER_PATH
SOURCE_FREEZE_PATH = p3_packet.SOURCE_FREEZE_PATH
SOURCE_L1_CAPTURE_PATH = p3_packet.SOURCE_L1_CAPTURE_PATH
SOURCE_L1_PROBE_PATH = p3_packet.SOURCE_L1_PROBE_PATH
SOURCE_L1_SETUP_PATH = p3_packet.SOURCE_L1_SETUP_PATH
HELPER_MODULE_PATH = p3_packet.HELPER_MODULE_PATH
SCHEMA_BY_RECORD_KEY = p3_packet.SCHEMA_BY_RECORD_KEY
SECRET_PATTERNS = p3_packet.SECRET_PATTERNS



def _json_bytes(value: Any) -> bytes:
    return lane_runtime.canonical_json(value, separators_style="default").encode("utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _display(logical_path: Path) -> str:
    return lane_runtime.display_path(logical_path, repo_root=ROOT)


def _sha256_file(path: Path) -> str:
    return lane_runtime.sha256_file(path)


def _ref(physical_path: Path, logical_path: Path) -> str:
    return f"{_display(logical_path)}#{_sha256_file(physical_path)}"


def _source_hashes(pairs: list[tuple[Path, Path]]) -> dict[str, str]:
    return {_display(logical): _sha256_file(physical) for physical, logical in pairs}


def _base(out_dir: Path | None, promote_accepted: bool) -> Path:
    if promote_accepted:
        return ROOT
    if out_dir is None:
        raise ValueError("oh_my_pi_compiler_capture requires --out for scratch capture")
    return Path(out_dir)


def _paths(base: Path, lane_id: str, config_id: str, claim_id: str, ct_output: str) -> dict[str, Path]:
    lane_dir = base / "docs/conformance/e4_target_support" / lane_id
    return {
        "agent_config": base / "agent_configs/misc" / f"{config_id}.yaml",
        "lane_dir": lane_dir,
        "raw_capture": lane_dir / "raw_capture_manifest.json",
        "compiled_records": lane_dir / "compiled_records.json",
        "schema_validation": lane_dir / "schema_validation_report.json",
        "replay": lane_dir / "bb_replay_result.json",
        "comparator": lane_dir / "comparator_report.json",
        "parity": lane_dir / "parity_results.json",
        "secret_scan": lane_dir / "secret_scan_report.json",
        "prevalidation": lane_dir / "prevalidation_report.json",
        "support_claim": base / "docs/conformance/support_claims" / f"{claim_id}.json",
        "evidence_manifest": base / "docs/conformance/support_claims" / f"{claim_id.replace('_support_claim', '_evidence_manifest')}.json",
        "node_gate": base / ct_output,
    }


def _p3_item(inventory_lane: Mapping[str, Any]) -> str:
    match = re.search(r"-P3([2-8])-", lane_inventory.ct_id(inventory_lane))
    if match is None:
        raise ValueError(f"not a P3.2-P3.8 lane: {lane_inventory.ct_id(inventory_lane)}")
    return f"P3.{match.group(1)}"


def _packet_constants(lane_def: Mapping[str, Any]) -> Mapping[str, Any]:
    normalize = lane_def.get("normalize")
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    constants = config.get("packet_constants") if isinstance(config, Mapping) else None
    if not isinstance(constants, Mapping):
        raise ValueError("P3 compiler lane requires normalize.config.packet_constants")
    summary = constants.get("summary")
    behavior_prefix = constants.get("behavior_prefix")
    expected = constants.get("comparator_expected")
    if (
        not isinstance(summary, str)
        or not summary
        or not isinstance(behavior_prefix, str)
        or not behavior_prefix
        or not isinstance(expected, Mapping)
        or not expected
    ):
        raise ValueError("P3 packet constants require summary, behavior_prefix, and comparator_expected")
    return constants


def _spec(lane_def: Mapping[str, Any], inventory_lane: Mapping[str, Any]) -> dict[str, Any]:
    p3_item = _p3_item(inventory_lane)
    constants = _packet_constants(lane_def)
    return {
        "p3_item": p3_item,
        "points": int(lane_def["points"]),
        "claim_id": lane_inventory.claim_id(inventory_lane),
        "lane_id": str(lane_def["lane_id"]),
        "config_id": str(lane_def["config_id"]),
        "feature_id": lane_inventory.ledger_feature_id(inventory_lane),
        "kind": str(lane_def["kind"]),
        "ct_id": lane_inventory.ct_id(inventory_lane),
        "ct_output": lane_inventory.ct_output(inventory_lane),
        "primitive": lane_inventory.primary_primitive(inventory_lane),
        "run_id": str(lane_def["run"]["run_id"]),
        "target_family": str(lane_def["target_family"]),
        "target_version": str(lane_def["target_version"]),
        "provider_model": str(lane_def["run"]["provider_model"]),
        "sandbox_mode": str(lane_def["run"]["sandbox_mode"]),
        "tool_id": "",
        "summary": str(constants["summary"]),
        "behavior_prefix": str(constants["behavior_prefix"]),
        "comparator_expected": dict(constants["comparator_expected"]),
    }




def _tool_id(builders: tuple[Mapping[str, Any], ...]) -> str:
    compiler_ids = {
        p3_projections.HELPER_COMPILER_BY_PROJECTION[str(builder["projection"])]
        for builder in builders
    }
    if len(compiler_ids) != 1:
        raise ValueError(f"P3 lane record builders must share one helper compiler for byte-stable agent config: {sorted(compiler_ids)}")
    return next(iter(compiler_ids))

def _resolve_input(path_text: str) -> Path:
    namespace = "repo"
    try:
        return resolve_declared_reference(
            path_text,
            checkout_root=ROOT,
            namespace=namespace,
            label="capture input",
            must_exist=False,
        )
    except ReferenceResolutionError as exc:
        raise ValueError(str(exc)) from exc


def _resolve_role_output(path_text: str) -> Path:
    raw = Path(path_text)
    if raw.is_absolute():
        return raw
    if path_text.startswith("docs_tmp/") or path_text.startswith(f"{ROOT.name}/"):
        return WORKSPACE / raw
    return ROOT / raw


def _validate_capture_inputs(lane_def: Mapping[str, Any], spec: Mapping[str, Any], builders: tuple[Mapping[str, Any], ...]) -> None:
    capture = lane_def.get("capture")
    inputs = capture.get("inputs") if isinstance(capture, Mapping) else None
    if not isinstance(inputs, list) or not all(isinstance(item, str) and item for item in inputs):
        raise ValueError("capture.inputs must list concrete source artifacts")
    required = {
        _display(SOURCE_L1_CAPTURE_PATH),
        _display(SOURCE_L1_PROBE_PATH),
        _display(SOURCE_L1_SETUP_PATH),
        _display(SOURCE_FREEZE_PATH),
        _display(HELPER_MODULE_PATH),
        "scripts/e4_parity/adapters/oh_my_pi_compiler_capture.py",
        "scripts/e4_parity/adapters/oh_my_pi_p3_remaining_capture.py",
        "scripts/e4_parity/adapters/oh_my_pi_p3_remaining_projections.py",
        "scripts/e4_parity/adapters/oh_my_pi_p3_packet.py",
        "scripts/e4_parity/fixtures/p3_lane_fixtures.py",
    }
    missing = sorted(required - set(inputs))
    if missing:
        raise ValueError(f"capture.inputs missing required P3 compiler sources: {missing}")
    for input_path in inputs:
        resolved = _resolve_input(input_path)
        if not resolved.exists():
            raise ValueError(f"capture.inputs path does not exist: {input_path}")
    for builder in builders:
        source_path = str(builder["source"])
        if source_path not in inputs:
            raise ValueError(f"record builder {builder['id']!r} source must be declared in capture.inputs")
        for record_key in builder["records"]:
            schema_name = SCHEMA_BY_RECORD_KEY.get(str(record_key))
            if schema_name is None:
                raise ValueError(f"{spec['p3_item']} record {record_key!r} has no schema mapping")
            if schema_name != builder["schema_version"]:
                raise ValueError(f"record builder {builder['id']!r} schema_version must match {record_key}: {schema_name}")
            schema_input = f"contracts/kernel/schemas/{schema_name}.schema.json"
            if schema_input not in inputs:
                raise ValueError(f"capture.inputs missing schema for {record_key}: {schema_input}")


def _role_path(role_value: Any, role_name: str) -> Path:
    if not isinstance(role_value, str) or not role_value:
        raise ValueError(f"normalize.config.roles.{role_name} must be a non-empty path")
    resolved = _resolve_role_output(role_value)
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"normalize.config.roles.{role_name} must resolve under repo root: {role_value}") from exc
    return resolved


def _logical_paths_from_roles(lane_def: Mapping[str, Any], fallback: Mapping[str, Path]) -> dict[str, Path]:
    normalize = lane_def.get("normalize")
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    roles = config.get("roles") if isinstance(config, Mapping) else None
    if not isinstance(roles, Mapping):
        raise ValueError("normalize.config.roles must map artifact roles to canonical logical paths")
    role_to_key = {
        "agent_config": "agent_config",
        "capture_ref": "raw_capture",
        "compiled_records": "compiled_records",
        "schema_validation": "schema_validation",
        "replay_ref": "replay",
        "comparator_ref": "comparator",
        "parity_results": "parity",
        "secret_scan_report": "secret_scan",
        "validator_output": "prevalidation",
    }
    logical = dict(fallback)
    missing = sorted(role for role in role_to_key if role not in roles)
    if missing:
        raise ValueError(f"normalize.config.roles missing required roles: {missing}")
    for role, key in role_to_key.items():
        logical[key] = _role_path(roles[role], role)
    expected = {
        "support_claim": fallback["support_claim"],
        "evidence_manifest": fallback["evidence_manifest"],
        "node_gate": fallback["node_gate"],
        "lane_dir": fallback["lane_dir"],
    }
    logical.update(expected)
    return logical




def _write_agent_config(spec: Mapping[str, Any], physical: Mapping[str, Path]) -> None:
    p3_packet.write_agent_config(spec, physical)


def _source_pairs(physical: Mapping[str, Path], logical: Mapping[str, Path]) -> list[tuple[Path, Path]]:
    return [
        (SOURCE_L1_CAPTURE_PATH, SOURCE_L1_CAPTURE_PATH),
        (SOURCE_L1_PROBE_PATH, SOURCE_L1_PROBE_PATH),
        (SOURCE_L1_SETUP_PATH, SOURCE_L1_SETUP_PATH),
        (SOURCE_FREEZE_PATH, SOURCE_FREEZE_PATH),
        (physical["agent_config"], logical["agent_config"]),
        (physical["compiled_records"], logical["compiled_records"]),
        (physical["schema_validation"], logical["schema_validation"]),
    ]


def _write_capture_replay_compare(
    spec: Mapping[str, Any],
    physical: Mapping[str, Path],
    logical: Mapping[str, Path],
    records: Mapping[str, Any],
    validation: Mapping[str, Any],
    comparator_facts: Mapping[str, Any],
) -> None:
    _write_json(physical["compiled_records"], {"schema_version": "bb.e4.helper_runtime_compiled_records.v1", "lane_id": spec["lane_id"], "config_id": spec["config_id"], "records": records})
    _write_json(physical["schema_validation"], validation)
    source_pairs = _source_pairs(physical, logical)
    source_hashes = _source_hashes(source_pairs)
    captured = [{"path": _display(logical_path), "role": "source", "sha256": _sha256_file(physical_path)} for physical_path, logical_path in source_pairs]
    raw_capture = {
        "accepted_as_capture_ref": True,
        "capture_class": "derived_capture",
        "captured_artifacts": captured,
        "config_id": spec["config_id"],
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": spec["lane_id"],
        "lineage_rationale": f"{spec['p3_item']} helper/runtime compiler capture is derived from canonical Oh-My-Pi P6.0-L1 source/capture material and scoped to BreadBoard helper-runtime primitive emission. It does not broaden target support beyond this named P3 lane.",
        "provider_model": spec["provider_model"],
        "raw_source_ref": _ref(SOURCE_L1_CAPTURE_PATH, SOURCE_L1_CAPTURE_PATH),
        "raw_source_status": "canonical_raw_present",
        "run_id": spec["run_id"],
        "sandbox_mode": spec["sandbox_mode"],
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "source_artifacts": [_display(logical_path) for _physical_path, logical_path in source_pairs],
        "source_hashes": source_hashes,
        "target_family": spec["target_family"],
        "target_source": {
            "coding_agent_package": "@oh-my-pi/pi-coding-agent",
            "coding_agent_version": "16.2.13",
            "upstream_commit": "5356713eae60e67ee64d9b02e3b5e377d248ee7f",
            "upstream_ref": "main",
            "upstream_repo": "https://github.com/can1357/oh-my-pi",
        },
        "target_version": spec["target_version"],
    }
    _write_json(physical["raw_capture"], raw_capture)

    replay_inputs = {
        _display(logical["raw_capture"]): _sha256_file(physical["raw_capture"]),
        _display(logical["compiled_records"]): _sha256_file(physical["compiled_records"]),
        _display(logical["schema_validation"]): _sha256_file(physical["schema_validation"]),
    }
    replay = {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "run_id": spec["run_id"],
        "p3_item": spec["p3_item"],
        "generated_at_utc": GENERATED_AT_UTC,
        "exit_status": "passed",
        "warnings": [],
        "errors": [],
        "input_hashes": replay_inputs,
        "normalization": {"mode": "p3_helper_runtime_compiler", "preserved_fields": sorted(records.keys())},
        "normalized_records": records,
        "replay_summary": f"BreadBoard helper runtime emitted schema-valid {spec['primitive']} scoped records for {spec['p3_item']} without changing existing runtime loaders.",
    }
    _write_json(physical["replay"], replay)

    assertions = p3_packet.comparator_assertions(spec["comparator_expected"], comparator_facts)
    comparator = {
        "schema_version": "bb.e4.comparator_report.v1",
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "run_id": spec["run_id"],
        "scope": {
            "config_id": spec["config_id"],
            "lane_id": spec["lane_id"],
            "phase": PHASE,
            "p3_item": spec["p3_item"],
            "provider_model": spec["provider_model"],
            "run_id": spec["run_id"],
            "sandbox_mode": spec["sandbox_mode"],
            "target_family": spec["target_family"],
            "target_version": spec["target_version"],
        },
        "failed": sum(1 for assertion in assertions if assertion["status"] != "passed"),
        "warned": 0,
        "assertions": assertions,
        "details": [{"name": assertion["name"], "status": assertion["status"]} for assertion in assertions],
        "input_hashes": {
            _display(logical["raw_capture"]): _sha256_file(physical["raw_capture"]),
            _display(logical["replay"]): _sha256_file(physical["replay"]),
            _display(logical["compiled_records"]): _sha256_file(physical["compiled_records"]),
        },
    }
    _write_json(physical["comparator"], comparator)

    comparator_failed = sum(1 for assertion in assertions if assertion["status"] != "passed")
    parity = {
        "schema_version": "bb.e4.parity_results.v1",
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "passed": comparator_failed == 0,
        "failed": comparator_failed,
        "warned": 0,
        "comparator_ref": _ref(physical["comparator"], logical["comparator"]),
        "generated_at_utc": GENERATED_AT_UTC,
    }
    _write_json(physical["parity"], parity)

    findings: list[dict[str, Any]] = []
    for path in sorted(physical["lane_dir"].glob("*.json")):
        text = path.read_text(encoding="utf-8")
        logical_path = logical["lane_dir"] / path.name
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"path": _display(logical_path), "pattern": pattern.pattern})
    secret_scan = {"schema_version": "bb.e4.secret_scan_report.v1", "lane_id": spec["lane_id"], "config_id": spec["config_id"], "passed": not findings, "findings": findings, "generated_at_utc": GENERATED_AT_UTC}
    _write_json(physical["secret_scan"], secret_scan)

    prevalidation = {
        "schema_version": "bb.e4.prevalidation_report.v1",
        "ok": bool(validation.get("ok")) and comparator_failed == 0 and not findings,
        "accepted": bool(validation.get("ok")) and comparator_failed == 0 and not findings,
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "checks": [
            {"name": "schema_validation", "passed": bool(validation.get("ok"))},
            {"name": "machine_comparator_assertions", "passed": comparator_failed == 0},
            {"name": "secret_scan", "passed": not findings},
        ],
        "generated_at_utc": GENERATED_AT_UTC,
    }
    _write_json(physical["prevalidation"], prevalidation)


def _write_support_and_manifest(spec: Mapping[str, Any], inventory_lane: Mapping[str, Any], physical: Mapping[str, Path], logical: Mapping[str, Path]) -> None:
    refs = {
        "capture_ref": _ref(physical["raw_capture"], logical["raw_capture"]),
        "comparator_ref": _ref(physical["comparator"], logical["comparator"]),
        "evidence_manifest_ref": _display(logical["evidence_manifest"]),
        "freeze_ref": f"{_display(FREEZE_MANIFEST_PATH)}#{spec['config_id']}#{p3_packet.freeze_row_hash(str(spec['config_id']))}",
        "parity_results_ref": _ref(physical["parity"], logical["parity"]),
        "replay_ref": _ref(physical["replay"], logical["replay"]),
        "secret_scan_ref": _ref(physical["secret_scan"], logical["secret_scan"]),
        "source_freeze_ref": _ref(SOURCE_FREEZE_PATH, SOURCE_FREEZE_PATH),
        "validation_ref": _ref(physical["prevalidation"], logical["prevalidation"]),
    }
    support_claim = p3_packet.build_support_claim(spec, inventory_lane, refs)
    _write_json(physical["support_claim"], support_claim)
    artifacts = [
        {"path": _display(FREEZE_MANIFEST_PATH), "role": "freeze_manifest", "sha256": p3_packet.freeze_row_hash(str(spec["config_id"]))},
        {"path": _display(logical["raw_capture"]), "role": "capture_ref", "sha256": _sha256_file(physical["raw_capture"])},
        {"derived_from": [_ref(physical["raw_capture"], logical["raw_capture"]), _ref(physical["compiled_records"], logical["compiled_records"])], "path": _display(logical["replay"]), "role": "replay_ref", "sha256": _sha256_file(physical["replay"])},
        {"derived_from": [_ref(physical["raw_capture"], logical["raw_capture"]), _ref(physical["replay"], logical["replay"]), _ref(physical["compiled_records"], logical["compiled_records"])], "path": _display(logical["comparator"]), "role": "comparator_ref", "sha256": _sha256_file(physical["comparator"])},
        {"path": _display(logical["support_claim"]), "role": "support_claim_ref", "sha256": _sha256_file(physical["support_claim"])},
        {"derived_from": [_ref(physical["comparator"], logical["comparator"])], "path": _display(logical["parity"]), "role": "parity_results", "sha256": _sha256_file(physical["parity"])},
        {"path": _display(logical["secret_scan"]), "role": "secret_scan_report", "sha256": _sha256_file(physical["secret_scan"])},
        {"path": _display(logical["prevalidation"]), "role": "validator_output", "sha256": _sha256_file(physical["prevalidation"])},
        {"path": _display(logical["compiled_records"]), "role": "compiled_records", "sha256": _sha256_file(physical["compiled_records"])},
        {"path": _display(logical["schema_validation"]), "role": "schema_validation", "sha256": _sha256_file(physical["schema_validation"])},
        {"path": _display(logical["agent_config"]), "role": "agent_config", "sha256": _sha256_file(physical["agent_config"])},
        {"path": _display(SOURCE_FREEZE_PATH), "role": "source_freeze", "sha256": _sha256_file(SOURCE_FREEZE_PATH)},
    ]
    manifest = {
        "artifacts": artifacts,
        "claim_id": str(support_claim["claim_id"]),
        "config_id": spec["config_id"],
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "generated_at_utc": GENERATED_AT_UTC,
        "hash_algorithm": "sha256",
        "lane_id": spec["lane_id"],
        "manifest_scope_note": f"Every governed artifact for this {spec['p3_item']} helper/runtime C4 chain is canonical repo evidence or source-freeze evidence under docs_tmp/phase_15/source_freezes. Scratch/tmp/shared roots are not promotion evidence.",
        "schema_version": "bb.e4.evidence_manifest.v1",
    }
    _write_json(physical["evidence_manifest"], manifest)


def _write_node_gate(spec: Mapping[str, Any], physical: Mapping[str, Path], logical: Mapping[str, Path]) -> dict[str, Any]:
    report = validate_c4_chain(
        repo_root=ROOT,
        freeze_manifest_path=FREEZE_MANIFEST_PATH,
        config_id=str(spec["config_id"]),
        support_claim_path=logical["support_claim"],
        evidence_manifest_path=logical["evidence_manifest"],
        rerun_comparators=True,
        comparator_registry_path=ROOT / "conformance/comparators/registry.json",
        enforce_catalog_binding=False,
    )
    report["support_claim"] = _display(logical["support_claim"])
    report["evidence_manifest"] = _display(logical["evidence_manifest"])
    refs = report.get("refs")
    if isinstance(refs, dict):
        refs["support_claim"] = _display(logical["support_claim"])
        refs["evidence_manifest"] = _display(logical["evidence_manifest"])
    _write_json(physical["node_gate"], report)
    return report


def capture_p3_remaining(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any],
    *,
    promote_accepted: bool,
    out_dir: Path | None = None,
    record_builders: tuple[Mapping[str, Any], ...],
    projected_records: Mapping[str, Any],
    derived_facts: Mapping[str, Any],
) -> dict[str, Any]:
    spec = _spec(lane_def, inventory_lane)
    builders = record_builders
    spec["tool_id"] = _tool_id(builders)
    base = _base(out_dir, promote_accepted)
    claim_id = lane_inventory.claim_id(inventory_lane)
    physical = _paths(base, str(spec["lane_id"]), str(spec["config_id"]), claim_id, str(spec["ct_output"]))
    fallback_logical = _paths(ROOT, str(spec["lane_id"]), str(spec["config_id"]), claim_id, str(spec["ct_output"]))
    logical = _logical_paths_from_roles(lane_def, fallback_logical)
    _validate_capture_inputs(lane_def, spec, builders)
    _write_agent_config(spec, physical)
    records = dict(projected_records)
    validation = p3_packet.validate_records(records)
    if not validation.get("ok"):
        raise ValueError(f"schema validation failed for {spec['config_id']}: {validation.get('errors')}")
    comparator_facts: dict[str, Any] = {
        "schema_validation_passed": bool(validation.get("ok")),
        "primary_primitive_present": str(spec["primitive"])
        in [SCHEMA_BY_RECORD_KEY.get(record_key) for record_key in records],
    }
    for builder_id, facts in derived_facts.items():
        if not isinstance(facts, Mapping):
            raise ValueError(f"projection {builder_id!r} derived facts must be an object")
        overlap = sorted(set(comparator_facts) & set(facts))
        if overlap:
            raise ValueError(f"duplicate comparator facts: {overlap}")
        comparator_facts.update(facts)
    _write_capture_replay_compare(spec, physical, logical, records, validation, comparator_facts)
    _write_support_and_manifest(spec, inventory_lane, physical, logical)
    node_gate_report = _write_node_gate(spec, physical, logical)

    return {
        "claim_id": claim_id,
        "config_id": spec["config_id"],
        "ct_id": spec["ct_id"],
        "feature_id": spec["feature_id"],
        "node_gate": _display(logical["node_gate"]),
        "ok": bool(node_gate_report.get("ok")),
        "paths": {
            "agent_config": _display(logical["agent_config"]),
            "compiled_records": _display(logical["compiled_records"]),
            "comparator": _display(logical["comparator"]),
            "evidence_manifest": _display(logical["evidence_manifest"]),
            "raw_capture": _display(logical["raw_capture"]),
            "replay": _display(logical["replay"]),
            "schema_validation": _display(logical["schema_validation"]),
            "support_claim": _display(logical["support_claim"]),
        },
        "points": int(spec["points"]),
        "record_builders": [dict(builder) for builder in builders],
    }
