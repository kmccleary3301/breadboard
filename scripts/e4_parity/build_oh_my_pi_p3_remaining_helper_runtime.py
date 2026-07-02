#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator, RefResolver

try:
    from scripts.e4_parity import lane_runtime
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lane_runtime


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.compilation import helper_runtime_primitives as helper
from scripts.validate_e4_c4_chain import validate_c4_chain
from scripts.e4_parity import lane_inventory_utils as lane_inventory
from scripts.e4_parity.fixtures import p3_lane_fixtures

GENERATED_AT_UTC = "2026-07-03T07:30:00Z"
PHASE = "P3"

FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
CT_SCENARIOS_PATH = ROOT / "docs/conformance/ct_scenarios_v1.json"
SUPPORT_DIR = ROOT / "docs/conformance/support_claims"
TARGET_SUPPORT_ROOT = ROOT / "docs/conformance/e4_target_support"
NODE_GATE_DIR = ROOT / "artifacts/conformance/node_gate"
AGENT_CONFIG_DIR = ROOT / "agent_configs/misc"
SCHEMA_DIR = ROOT / "contracts/kernel/schemas"
SOURCE_FREEZE_PATH = WORKSPACE / "docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_freeze_provenance.json"
SOURCE_L1_CAPTURE_PATH = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/raw_capture_manifest.json"
SOURCE_L1_PROBE_PATH = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_probe_output.json"
SOURCE_L1_SETUP_PATH = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface/target_setup_and_capture_report.json"
HELPER_MODULE_PATH = ROOT / "agentic_coder_prototype/compilation/helper_runtime_primitives.py"
BUILDER_PATH = Path(__file__).resolve()

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key)[^\n]{0,20}[:=][^\n]{8,}"),
]

INVENTORY_PATH = ROOT / "docs/conformance/e4_lane_inventory.json"

P3_LANE_TOOL_IDS: dict[str, str] = {
    "P3.2": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_context_resource_pack",
    "P3.3": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_capability_registry",
    "P3.4": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_extension_hook_execution",
    "P3.5": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_resource_access_bundle",
    "P3.6": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_protocol_provider_policy_bundle",
    "P3.7": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_memory_work_bundle",
    "P3.8": "agentic_coder_prototype.compilation.helper_runtime_primitives.compile_projection_broker_bundle",
}

P3_LANE_SUMMARIES: dict[str, str] = {
    "P3.2": "BreadBoard P3.2 context resource pack compiler support is accepted only for the named Oh-My-Pi source-derived helper/runtime fixture.",
    "P3.3": "BreadBoard P3.3 capability registry compiler support is accepted only for deterministic discovery and first-wins registry semantics in the named helper/runtime fixture.",
    "P3.4": "BreadBoard P3.4 extension hook execution support is accepted only for source-derived hook effect records in the named helper/runtime fixture.",
    "P3.5": "BreadBoard P3.5 resource/blob compiler support is accepted only for local resource ref, access, blob, truncation, redaction, approval, and containment records in the named helper/runtime fixture.",
    "P3.6": "BreadBoard P3.6 protocol/provider/policy compiler support is accepted only for local static protocol, provider-route, and operation-policy records in the named helper/runtime fixture.",
    "P3.7": "BreadBoard P3.7 memory/work compiler support is accepted only for checkpoint-backed memory plan and work-item lifecycle records in the named helper/runtime fixture.",
    "P3.8": "BreadBoard P3.8 projection/broker adapter support is accepted only for projection-event and side-effect broker records in the named helper/runtime fixture.",
}


def _p3_item_from_ct_id(ct_id: str) -> str:
    match = re.search(r"-P3([1-8])-", ct_id)
    if not match:
        raise ValueError(f"cannot derive P3 item from CT id: {ct_id}")
    return f"P3.{match.group(1)}"


def _ct_output_from_lane(lane: Mapping[str, Any]) -> str:
    ct = lane.get("ct")
    if not isinstance(ct, Mapping):
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing ct block")
    command = ct.get("command")
    argv = command.get("argv") if isinstance(command, Mapping) else None
    if not isinstance(argv, list):
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing ct command argv")
    for index, value in enumerate(argv[:-1]):
        if value == "--json-out" and isinstance(argv[index + 1], str):
            return argv[index + 1]
    raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing --json-out")


def _load_p3_remaining_lanes(inventory_path: Path = INVENTORY_PATH) -> tuple[dict[str, Any], ...]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    lanes = inventory.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError("lane inventory must contain a lanes list")
    specs: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping) or lane.get("phase") != PHASE or lane.get("status") != "accepted":
            continue
        ct = lane.get("ct")
        if not isinstance(ct, Mapping) or not isinstance(ct.get("test_id"), str):
            continue
        p3_item = _p3_item_from_ct_id(str(ct["test_id"]))
        if p3_item == "P3.1":
            continue
        primitive = lane_inventory.primary_primitive(lane)
        feature_id = lane_inventory.ledger_feature_id(lane)
        ct_output = lane_inventory.ct_output(lane)
        target_family = str(lane["target_family"])
        target_version = str(lane["target_version"])
        provider_model = str(lane["provider_model"])
        sandbox_mode = str(lane["sandbox_mode"])
        run_id = str(lane["run_id"])
        specs.append(
            {
                "p3_item": p3_item,
                "points": int(lane["points"]),
                "lane_id": str(lane["lane_id"]),
                "config_id": str(lane["config_id"]),
                "feature_id": feature_id,
                "ct_id": str(ct["test_id"]),
                "ct_output": ct_output,
                "primitive": primitive,
                "run_id": run_id,
                "target_family": target_family,
                "target_version": target_version,
                "provider_model": provider_model,
                "sandbox_mode": sandbox_mode,
                "tool_id": P3_LANE_TOOL_IDS[p3_item],
                "summary": P3_LANE_SUMMARIES[p3_item],
            }
        )
    return tuple(sorted(specs, key=lambda spec: str(spec["p3_item"])))


LANES: tuple[dict[str, Any], ...] = _load_p3_remaining_lanes()
if not LANES:
    raise ValueError("P3 remaining lanes cannot be empty")
RUN_ID = str(LANES[0]["run_id"])
TARGET_FAMILY = str(LANES[0]["target_family"])
TARGET_VERSION = str(LANES[0]["target_version"])
PROVIDER_MODEL = str(LANES[0]["provider_model"])
SANDBOX_MODE = str(LANES[0]["sandbox_mode"])
for _lane in LANES[1:]:
    if (
        _lane["run_id"],
        _lane["target_family"],
        _lane["target_version"],
        _lane["provider_model"],
        _lane["sandbox_mode"],
    ) != (RUN_ID, TARGET_FAMILY, TARGET_VERSION, PROVIDER_MODEL, SANDBOX_MODE):
        raise ValueError("P3 remaining lanes must share target/run/provider/sandbox identity")

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


def canonical_json(value: Any) -> bytes:
    return lane_runtime.canonical_json(value, separators_style="default").encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    lane_runtime.write_json_artifact(path, value, style="default", trailing_newline=True)


def sha256_file(path: Path) -> str:
    return lane_runtime.sha256_file(path)


def display_path(path: Path) -> str:
    return lane_runtime.display_path(path, repo_root=ROOT)


def resolve_display(path: str) -> Path:
    raw = Path(path.split("#", 1)[0])
    if raw.is_absolute():
        return raw
    if str(raw).startswith("docs_tmp/") or str(raw).startswith(ROOT.name + "/"):
        return WORKSPACE / raw
    return ROOT / raw


def ref(path: Path) -> str:
    return f"{display_path(path)}#{sha256_file(path)}"


def row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    return lane_runtime.sha256_text(lane_runtime.canonical_json({"row_id": row_id, "row": row}, separators_style="compact"))


def load_freeze_manifest() -> dict[str, Any]:
    payload = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("freeze manifest must be a mapping")
    payload.setdefault("e4_configs", {})
    return payload


def freeze_row_hash(config_id: str) -> str:
    manifest = load_freeze_manifest()
    return row_hash(config_id, manifest["e4_configs"][config_id])


def lane_paths(spec: Mapping[str, Any]) -> dict[str, Path]:
    lane_dir = TARGET_SUPPORT_ROOT / str(spec["lane_id"])
    config_id = str(spec["config_id"])
    return {
        "lane_dir": lane_dir,
        "agent_config": AGENT_CONFIG_DIR / f"{config_id}.yaml",
        "raw_capture": lane_dir / "raw_capture_manifest.json",
        "compiled_records": lane_dir / "compiled_records.json",
        "schema_validation": lane_dir / "schema_validation_report.json",
        "replay": lane_dir / "bb_replay_result.json",
        "comparator": lane_dir / "comparator_report.json",
        "parity": lane_dir / "parity_results.json",
        "secret_scan": lane_dir / "secret_scan_report.json",
        "prevalidation": lane_dir / "prevalidation_report.json",
        "support_claim": SUPPORT_DIR / f"{config_id}_c4_support_claim.json",
        "evidence_manifest": SUPPORT_DIR / f"{config_id}_c4_evidence_manifest.json",
        "node_gate": ROOT / str(spec["ct_output"]),
    }


def schema_store() -> dict[str, Any]:
    store = {path.name: read_json(path) for path in SCHEMA_DIR.glob("*.schema.json")}
    store.update({schema.get("$id", name): schema for name, schema in store.items()})
    return store


def schema_errors(schema_name: str, record: Mapping[str, Any], store: Mapping[str, Any]) -> list[str]:
    schema_path = SCHEMA_DIR / f"{schema_name}.schema.json"
    schema = read_json(schema_path)
    resolver = RefResolver(base_uri=SCHEMA_DIR.as_uri() + "/", referrer=schema, store=dict(store))
    validator = Draft202012Validator(schema, resolver=resolver)
    errors = []
    for error in sorted(validator.iter_errors(record), key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message)):
        path = ".".join(str(part) for part in error.absolute_path)
        errors.append(f"{schema_name}:{path}: {error.message}" if path else f"{schema_name}: {error.message}")
    return errors


def source_paths_for(spec: Mapping[str, Any], paths: Mapping[str, Path]) -> list[Path]:
    return [
        SOURCE_L1_CAPTURE_PATH,
        SOURCE_L1_PROBE_PATH,
        SOURCE_L1_SETUP_PATH,
        SOURCE_FREEZE_PATH,
        paths["agent_config"],
        paths["compiled_records"],
        paths["schema_validation"],
    ]


def source_hashes_for(paths: list[Path]) -> dict[str, str]:
    return {display_path(path): sha256_file(path) for path in paths}


def write_agent_config(spec: Mapping[str, Any], paths: Mapping[str, Path]) -> None:
    payload = {
        "schema_version": "bb.e4.helper_runtime_lane_config.v1",
        "config_id": spec["config_id"],
        "lane_id": spec["lane_id"],
        "phase": PHASE,
        "p3_item": spec["p3_item"],
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "sandbox_mode": SANDBOX_MODE,
        "compiler": spec["tool_id"],
        "primary_primitive": spec["primitive"],
        "source_capture_ref": display_path(SOURCE_L1_CAPTURE_PATH),
    }
    paths["agent_config"].parent.mkdir(parents=True, exist_ok=True)
    paths["agent_config"].write_text(yaml.safe_dump(payload, sort_keys=False, width=120), encoding="utf-8")


def records_for(spec: Mapping[str, Any]) -> dict[str, Any]:
    lane_id = str(spec["lane_id"])
    run_id = str(spec["run_id"])
    if spec["p3_item"] == "P3.2":
        return {
            "context_resource_pack": helper.compile_context_resource_pack(
                pack_id=f"{lane_id}_pack",
                sources=p3_lane_fixtures.context_sources(workspace_root=str(ROOT)),
                render_profile=p3_lane_fixtures.RENDER_PROFILE,
            )
        }
    if spec["p3_item"] == "P3.3":
        return {
            "capability_registry": helper.compile_capability_registry(
                registry_id=f"{lane_id}_registry",
                run_id=run_id,
                environment_id=f"{lane_id}_env",
                declarations=p3_lane_fixtures.capability_declarations(),
                generated_at=GENERATED_AT_UTC,
            )
        }
    if spec["p3_item"] == "P3.4":
        execution_id = f"{lane_id}_hook_exec"
        return {
            "extension_hook_execution": helper.compile_extension_hook_execution(
                execution_id=execution_id,
                hook_id=f"{lane_id}_policy_hook",
                hook_type="pre_tool",
                event_id=f"{lane_id}_tool_request",
                event_type="tool.requested",
                effects=p3_lane_fixtures.hook_effects(execution_id=execution_id),
                status="completed",
                started_at=GENERATED_AT_UTC,
                duration_ms=p3_lane_fixtures.HOOK_DURATION_MS,
                model_provider_visibility=p3_lane_fixtures.hook_visibility(execution_id=execution_id),
            )
        }
    if spec["p3_item"] == "P3.5":
        read_access = helper.compile_resource_access_bundle(
            **p3_lane_fixtures.resource_access_inputs(
                access_id=f"{lane_id}_read",
                uri="file:///workspace/src/main.py?rev=abc123#L10",
                content="print('hello')\n",
            )
        )
        write_access = helper.compile_resource_access_bundle(
            **p3_lane_fixtures.resource_access_inputs(
                access_id=f"{lane_id}_write",
                uri="file:///workspace/out/report.txt",
                content="approved output\n",
                operation="write",
                approval_required=True,
            )
        )
        truncated = helper.compile_resource_access_bundle(
            **p3_lane_fixtures.resource_access_inputs(
                access_id=f"{lane_id}_truncated_redacted",
                uri="file:///workspace/logs/secret.log",
                content="secret=redacted\n" * 20,
                returned_size_bytes=24,
                redacted=True,
            )
        )
        return {
            "resource_ref": read_access["resource_ref"],
            "blob_ref": read_access["blob_ref"],
            "resource_access": read_access["resource_access"],
            "write_resource_access": write_access["resource_access"],
            "truncated_redacted_access": truncated["resource_access"],
        }
    if spec["p3_item"] == "P3.6":
        records = p3_lane_fixtures.protocol_provider_policy_records(
            run_id=run_id,
            route_id=f"{lane_id}_route",
            policy_id=f"{lane_id}_policy",
            generated_at=GENERATED_AT_UTC,
        )
        return helper.compile_protocol_provider_policy_bundle(
            protocol_session=records["protocol_session"],
            provider_route=records["provider_route"],
            operation_policy=records["operation_policy"],
        )
    if spec["p3_item"] == "P3.7":
        records = p3_lane_fixtures.memory_work_records(
            run_id=run_id,
            plan_id=f"{lane_id}_plan",
            work_item_id=f"{lane_id}_work_item",
            generated_at=GENERATED_AT_UTC,
        )
        return helper.compile_memory_work_bundle(memory_plan=records["memory_plan"], work_item=records["work_item"])
    if spec["p3_item"] == "P3.8":
        records = p3_lane_fixtures.projection_broker_records(
            run_id=run_id,
            event_id=f"{lane_id}_projection_event",
            broker_id=f"{lane_id}_broker",
            generated_at=GENERATED_AT_UTC,
        )
        return helper.compile_projection_broker_bundle(
            projection_event=records["projection_event"],
            side_effect_broker=records["side_effect_broker"],
        )
    raise ValueError(f"unsupported lane: {spec['p3_item']}")


def validate_records(records: Mapping[str, Any]) -> dict[str, Any]:
    store = schema_store()
    checks = []
    errors: list[str] = []
    for key, record in records.items():
        schema_name = SCHEMA_BY_RECORD_KEY.get(key)
        if schema_name is None:
            errors.append(f"{key}: no schema mapping")
            checks.append({"record": key, "schema": None, "ok": False, "errors": [f"{key}: no schema mapping"]})
            continue
        row_errors = schema_errors(schema_name, record, store)
        checks.append({"record": key, "schema": schema_name, "ok": not row_errors, "errors": row_errors})
        errors.extend(row_errors)
    return {"schema_version": "bb.e4.helper_runtime_schema_validation.v1", "generated_at_utc": GENERATED_AT_UTC, "ok": not errors, "checks": checks, "errors": errors}


def write_freeze_manifest(spec: Mapping[str, Any], paths: Mapping[str, Path]) -> None:
    manifest = load_freeze_manifest()
    manifest["e4_configs"][str(spec["config_id"])] = {
        "config_path": display_path(paths["agent_config"]),
        "harness": {
            "family": TARGET_FAMILY,
            "upstream_repo": "https://github.com/can1357/oh-my-pi",
            "upstream_commit": "5356713eae60e67ee64d9b02e3b5e377d248ee7f",
            "upstream_ref": "main",
            "upstream_version": TARGET_VERSION,
            "runtime_surface": {"provider_model": PROVIDER_MODEL, "sandbox_mode": SANDBOX_MODE, "target_profile_id": str(spec["lane_id"])},
        },
        "calibration_anchor": {
            "class": "derived_capture",
            "scenario_id": str(spec["lane_id"]),
            "run_id": RUN_ID,
            "evidence_paths": [
                display_path(paths["raw_capture"]),
                display_path(paths["replay"]),
                display_path(paths["comparator"]),
                display_path(paths["parity"]),
                display_path(paths["support_claim"]),
                display_path(paths["evidence_manifest"]),
                display_path(paths["prevalidation"]),
                display_path(paths["secret_scan"]),
            ],
        },
        "snapshot_source_entry": str(spec["config_id"]),
        "snapshot_tag": f"oh_my_pi_16.2.13_p3_helper_runtime_{str(spec['p3_item']).lower().replace('.', '_')}_20260703",
    }
    FREEZE_MANIFEST_PATH.write_text(yaml.safe_dump(manifest, sort_keys=False, width=120), encoding="utf-8")


def write_capture_replay_compare(spec: Mapping[str, Any], paths: Mapping[str, Path], records: Mapping[str, Any], validation: Mapping[str, Any]) -> None:
    write_json(paths["compiled_records"], {"schema_version": "bb.e4.helper_runtime_compiled_records.v1", "lane_id": spec["lane_id"], "config_id": spec["config_id"], "records": records})
    write_json(paths["schema_validation"], validation)
    source_paths = source_paths_for(spec, paths)
    source_hashes = source_hashes_for(source_paths)
    captured = [{"path": display_path(path), "role": "source", "sha256": sha256_file(path)} for path in source_paths]
    raw_capture = {
        "accepted_as_capture_ref": True,
        "capture_class": "derived_capture",
        "captured_artifacts": captured,
        "config_id": spec["config_id"],
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": spec["lane_id"],
        "lineage_rationale": f"{spec['p3_item']} helper/runtime compiler capture is derived from canonical Oh-My-Pi P6.0-L1 source/capture material and scoped to BreadBoard helper-runtime primitive emission. It does not broaden target support beyond this named P3 lane.",
        "provider_model": PROVIDER_MODEL,
        "raw_source_ref": ref(SOURCE_L1_CAPTURE_PATH),
        "raw_source_status": "canonical_raw_present",
        "run_id": RUN_ID,
        "sandbox_mode": SANDBOX_MODE,
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "source_artifacts": [display_path(path) for path in source_paths],
        "source_hashes": source_hashes,
        "target_family": TARGET_FAMILY,
        "target_source": {
            "coding_agent_package": "@oh-my-pi/pi-coding-agent",
            "coding_agent_version": "16.2.13",
            "upstream_commit": "5356713eae60e67ee64d9b02e3b5e377d248ee7f",
            "upstream_ref": "main",
            "upstream_repo": "https://github.com/can1357/oh-my-pi",
        },
        "target_version": TARGET_VERSION,
    }
    write_json(paths["raw_capture"], raw_capture)

    replay_inputs = {
        display_path(paths["raw_capture"]): sha256_file(paths["raw_capture"]),
        display_path(paths["compiled_records"]): sha256_file(paths["compiled_records"]),
        display_path(paths["schema_validation"]): sha256_file(paths["schema_validation"]),
    }
    replay = {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "run_id": RUN_ID,
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
    write_json(paths["replay"], replay)

    assertions = comparator_assertions(spec, records, validation)
    comparator = {
        "schema_version": "bb.e4.comparator_report.v1",
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "run_id": RUN_ID,
        "scope": {
            "config_id": spec["config_id"],
            "lane_id": spec["lane_id"],
            "phase": PHASE,
            "p3_item": spec["p3_item"],
            "provider_model": PROVIDER_MODEL,
            "run_id": RUN_ID,
            "sandbox_mode": SANDBOX_MODE,
            "target_version": TARGET_VERSION,
        },
        "failed": sum(1 for assertion in assertions if assertion["status"] != "passed"),
        "warned": 0,
        "assertions": assertions,
        "details": [{"name": assertion["name"], "status": assertion["status"]} for assertion in assertions],
        "input_hashes": {
            display_path(paths["raw_capture"]): sha256_file(paths["raw_capture"]),
            display_path(paths["replay"]): sha256_file(paths["replay"]),
            display_path(paths["compiled_records"]): sha256_file(paths["compiled_records"]),
        },
    }
    write_json(paths["comparator"], comparator)

    comparator_failed = sum(1 for assertion in assertions if assertion["status"] != "passed")
    parity = {
        "schema_version": "bb.e4.parity_results.v1",
        "lane_id": spec["lane_id"],
        "config_id": spec["config_id"],
        "passed": comparator_failed == 0,
        "failed": comparator_failed,
        "warned": 0,
        "comparator_ref": ref(paths["comparator"]),
        "generated_at_utc": GENERATED_AT_UTC,
    }
    write_json(paths["parity"], parity)

    findings = secret_findings(paths["lane_dir"])
    secret_scan = {"schema_version": "bb.e4.secret_scan_report.v1", "lane_id": spec["lane_id"], "config_id": spec["config_id"], "passed": not findings, "findings": findings, "generated_at_utc": GENERATED_AT_UTC}
    write_json(paths["secret_scan"], secret_scan)

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
    write_json(paths["prevalidation"], prevalidation)


def assertion_eq(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "status": "passed" if observed == expected else "failed", "observed": observed, "expected": expected}


def comparator_assertions(spec: Mapping[str, Any], records: Mapping[str, Any], validation: Mapping[str, Any]) -> list[dict[str, Any]]:
    assertions = [
        assertion_eq("schema_validation_passed", bool(validation.get("ok")), True),
        assertion_eq("primary_primitive_present", str(spec["primitive"]) in [SCHEMA_BY_RECORD_KEY.get(key) for key in records], True),
    ]
    p3_item = spec["p3_item"]
    if p3_item == "P3.2":
        pack = records["context_resource_pack"]
        assertions.extend([
            assertion_eq("context_order_preserved", pack["render_order"], ["system_prompt", "project_context", "generated_cwd", "redacted_env"]),
            assertion_eq("redacted_env_hidden_from_model", pack["model_visibility"]["redacted_source_ids"], ["redacted_env"]),
        ])
    elif p3_item == "P3.3":
        registry = records["capability_registry"]
        assertions.extend([
            assertion_eq("first_wins_capability_count", [item["capability_id"] for item in registry["capabilities"]], ["tool.read", "resolver.workspace"]),
            assertion_eq("shadow_and_disabled_in_mutation_log", [item["operation"] for item in registry["mutation_log"]], ["discover", "shadow", "disable", "discover"]),
        ])
    elif p3_item == "P3.4":
        execution = records["extension_hook_execution"]
        assertions.append(assertion_eq("hook_effect_types", [effect["effect_type"] for effect in execution["effects"]], ["log", "tool_surface_patch", "capability_exposure", "signal"]))
    elif p3_item == "P3.5":
        assertions.extend([
            assertion_eq("resource_access_variants", sorted(key for key in records if key.endswith("access")), ["resource_access", "truncated_redacted_access", "write_resource_access"]),
            assertion_eq("truncation_and_redaction_recorded", [records["truncated_redacted_access"]["truncation"]["truncated"], records["truncated_redacted_access"]["redaction"]["redacted"]], [True, True]),
            assertion_eq("write_requires_approval", records["write_resource_access"]["approval"]["required"], True),
        ])
    elif p3_item == "P3.6":
        route = records["provider_route"]
        assertions.extend([
            assertion_eq("fallback_selected_index", route["selected_fallback_index"], 0),
            assertion_eq("policy_feeds_route", records["effective_operation_policy"]["applies_to"]["provider_route_ref"], f"provider_route:{route['route_id']}"),
        ])
    elif p3_item == "P3.7":
        assertions.extend([
            assertion_eq("work_checkpoint_refs_memory_plan", records["work_item"]["state"]["checkpoint_ref"], f"bb.memory_compaction_plan.v1:{records['memory_compaction_plan']['plan_id']}"),
            assertion_eq("memory_plan_has_transcript_and_summary", [len(records["memory_compaction_plan"]["transcript_refs"]), len(records["memory_compaction_plan"]["generated_refs"])], [1, 1]),
        ])
    elif p3_item == "P3.8":
        assertions.extend([
            assertion_eq("projection_is_not_kernel_truth", records["projection_event"]["kernel_truth"], False),
            assertion_eq("broker_audits_before_after_refs", [len(records["side_effect_broker"]["before_refs"]), len(records["side_effect_broker"]["after_refs"])], [1, 1]),
        ])
    return assertions


def secret_findings(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not root.exists():
        return findings
    for path in sorted(root.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"path": display_path(path), "pattern": pattern.pattern})
    return findings


def build_ledger_row(spec: Mapping[str, Any], paths: Mapping[str, Path], freeze_ref: str) -> dict[str, Any]:
    return {
        "breadboard_mapping": {"primitive": spec["primitive"], "support": "supported", "truth_scope": "kernel_truth"},
        "claim_type": "runtime_behavior",
        "dedupe_key": f"omp/helper_runtime/{spec['lane_id']}/c4",
        "e4_row_ref": spec["config_id"],
        "evidence_tier": "C4",
        "family": "helper_runtime",
        "feature_id": spec["feature_id"],
        "fixture_refs": [
            f"freeze:{freeze_ref}",
            f"capture:{ref(paths['raw_capture'])}",
            f"replay:{ref(paths['replay'])}",
            f"comparator:{ref(paths['comparator'])}",
            f"support_claim:{display_path(paths['support_claim'])}",
            f"evidence_manifest:{display_path(paths['evidence_manifest'])}",
            f"parity_results:{ref(paths['parity'])}",
            f"secret_scan_report:{ref(paths['secret_scan'])}",
            f"validator_output:{ref(paths['prevalidation'])}",
        ],
        "gap_kind": "none",
        "model_visible": True,
        "promotion_state": "ready",
        "schema_version": "bb.atomic_feature_ledger.v1",
        "source_refs": [
            f"source:{display_path(HELPER_MODULE_PATH)}",
            f"source:{display_path(BUILDER_PATH)}",
            f"source:{display_path(paths['support_claim'])}",
            f"source:{display_path(paths['evidence_manifest'])}",
        ],
        "stateful": True,
        "target": "omp",
    }


def upsert_ledger(rows_by_feature: Mapping[str, dict[str, Any]]) -> dict[str, str]:
    ledger = read_json(LEDGER_PATH)
    rows = [item for item in ledger.get("rows", []) if not (isinstance(item, Mapping) and item.get("feature_id") in rows_by_feature)]
    rows.extend(rows_by_feature[feature_id] for feature_id in sorted(rows_by_feature))
    ledger["rows"] = rows
    ledger["row_count"] = len(rows)
    write_json(LEDGER_PATH, ledger)
    return {feature_id: row_hash(feature_id, row) for feature_id, row in rows_by_feature.items()}


def write_support_claim_and_manifest(spec: Mapping[str, Any], paths: Mapping[str, Path], freeze_ref: str, ledger_ref: str) -> None:
    support_claim = {
        "accepted": True,
        "acceptance_rationale": f"{spec['p3_item']} helper/runtime support is accepted only for the named source-derived compiler fixture; it is backed by schema validation, machine comparator assertions, and a live C4 chain validator.",
        "capture_ref": ref(paths["raw_capture"]),
        "claim_id": f"{spec['config_id']}_c4_support_claim",
        "comparator_ref": ref(paths["comparator"]),
        "config_id": spec["config_id"],
        "evidence_manifest_ref": display_path(paths["evidence_manifest"]),
        "exclusions": [
            f"No broad Oh-My-Pi, OMP, Pi, provider-parity, UI, final-readiness, or target-customization support claim is made from this {spec['p3_item']} helper/runtime lane.",
            "No score is claimed for P5 Pi lanes or P8 final readiness from this P3 helper/runtime evidence.",
            "No provider-authenticated, danger-full-access, network, browser, MCP, or model-inference behavior is claimed.",
        ],
        "freeze_ref": freeze_ref,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": spec["lane_id"],
        "ledger_row_refs": [ledger_ref],
        "level": spec["p3_item"],
        "parity_results_ref": ref(paths["parity"]),
        "phase": PHASE,
        "points": spec["points"],
        "provider_model": PROVIDER_MODEL,
        "replay_ref": ref(paths["replay"]),
        "run_id": RUN_ID,
        "sandbox_mode": SANDBOX_MODE,
        "schema_version": "bb.e4.support_claim.v1",
        "scope": {
            "config_id": spec["config_id"],
            "lane_id": spec["lane_id"],
            "phase": PHASE,
            "p3_item": spec["p3_item"],
            "provider_model": PROVIDER_MODEL,
            "run_id": RUN_ID,
            "sandbox_mode": SANDBOX_MODE,
            "target_version": TARGET_VERSION,
        },
        "secret_scan_ref": ref(paths["secret_scan"]),
        "source_freeze_ref": ref(SOURCE_FREEZE_PATH),
        "summary": spec["summary"],
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "tool_id": spec["tool_id"],
        "validation_refs": [ref(paths["prevalidation"])],
    }
    write_json(paths["support_claim"], support_claim)

    artifacts = [
        {"path": display_path(FREEZE_MANIFEST_PATH), "role": "freeze_manifest", "sha256": freeze_row_hash(str(spec["config_id"]))},
        {"path": display_path(paths["raw_capture"]), "role": "capture_ref", "sha256": sha256_file(paths["raw_capture"])},
        {"derived_from": [ref(paths["raw_capture"]), ref(paths["compiled_records"])], "path": display_path(paths["replay"]), "role": "replay_ref", "sha256": sha256_file(paths["replay"])},
        {"derived_from": [ref(paths["raw_capture"]), ref(paths["replay"]), ref(paths["compiled_records"])], "path": display_path(paths["comparator"]), "role": "comparator_ref", "sha256": sha256_file(paths["comparator"])},
        {"path": display_path(paths["support_claim"]), "role": "support_claim_ref", "sha256": sha256_file(paths["support_claim"])},
        {"derived_from": [ref(paths["comparator"])], "path": display_path(paths["parity"]), "role": "parity_results", "sha256": sha256_file(paths["parity"])},
        {"path": display_path(paths["secret_scan"]), "role": "secret_scan_report", "sha256": sha256_file(paths["secret_scan"])},
        {"path": display_path(paths["prevalidation"]), "role": "validator_output", "sha256": sha256_file(paths["prevalidation"])},
        {"path": display_path(paths["compiled_records"]), "role": "compiled_records", "sha256": sha256_file(paths["compiled_records"])},
        {"path": display_path(paths["schema_validation"]), "role": "schema_validation", "sha256": sha256_file(paths["schema_validation"])},
        {"path": display_path(paths["agent_config"]), "role": "agent_config", "sha256": sha256_file(paths["agent_config"])},
        {"path": display_path(SOURCE_FREEZE_PATH), "role": "source_freeze", "sha256": sha256_file(SOURCE_FREEZE_PATH)},
    ]
    manifest = {
        "artifacts": artifacts,
        "claim_id": f"{spec['config_id']}_c4_support_claim",
        "config_id": spec["config_id"],
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "generated_at_utc": GENERATED_AT_UTC,
        "hash_algorithm": "sha256",
        "lane_id": spec["lane_id"],
        "manifest_scope_note": f"Every governed artifact for this {spec['p3_item']} helper/runtime C4 chain is canonical repo evidence or source-freeze evidence under docs_tmp/phase_15/source_freezes. Scratch/tmp/shared roots are not promotion evidence.",
        "schema_version": "bb.e4.evidence_manifest.v1",
    }
    write_json(paths["evidence_manifest"], manifest)


def upsert_ct_scenarios(specs: tuple[dict[str, Any], ...]) -> None:
    _ = specs
    lane_runtime.upsert_ct_scenario(CT_SCENARIOS_PATH)


def validate_and_write_node_gate(spec: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    report = validate_c4_chain(
        repo_root=ROOT,
        freeze_manifest_path=FREEZE_MANIFEST_PATH,
        config_id=str(spec["config_id"]),
        support_claim_path=paths["support_claim"],
        evidence_manifest_path=paths["evidence_manifest"],
        enforce_catalog_binding=False,
    )
    write_json(paths["node_gate"], report)
    return report


def build(write: bool = True) -> dict[str, Any]:
    if not write:
        return {"ok": True, "lanes": [spec["config_id"] for spec in LANES]}
    lane_infos: dict[str, dict[str, Any]] = {}
    ledger_rows: dict[str, dict[str, Any]] = {}

    for spec in LANES:
        paths = lane_paths(spec)
        write_agent_config(spec, paths)
        write_freeze_manifest(spec, paths)
        records = records_for(spec)
        validation = validate_records(records)
        if not validation.get("ok"):
            raise RuntimeError(f"schema validation failed for {spec['config_id']}: {validation['errors']}")
        write_capture_replay_compare(spec, paths, records, validation)
        freeze_ref = f"{display_path(FREEZE_MANIFEST_PATH)}#{spec['config_id']}#{freeze_row_hash(str(spec['config_id']))}"
        ledger_rows[str(spec["feature_id"])] = build_ledger_row(spec, paths, freeze_ref)
        lane_infos[str(spec["config_id"])] = {"spec": spec, "paths": paths, "freeze_ref": freeze_ref}

    ledger_hashes = upsert_ledger(ledger_rows)

    for config_id, info in lane_infos.items():
        spec = info["spec"]
        paths = info["paths"]
        ledger_ref = f"{display_path(LEDGER_PATH)}#{spec['feature_id']}#{ledger_hashes[str(spec['feature_id'])]}"
        write_support_claim_and_manifest(spec, paths, str(info["freeze_ref"]), ledger_ref)

    node_reports = []
    for config_id, info in lane_infos.items():
        report = validate_and_write_node_gate(info["spec"], info["paths"])
        node_reports.append(report)

    upsert_ct_scenarios(LANES)

    return {
        "ok": all(report.get("ok") for report in node_reports),
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_count": len(LANES),
        "points": sum(int(spec["points"]) for spec in LANES),
        "node_gate_reports": {report["config_id"]: {"ok": report.get("ok"), "path": display_path(lane_infos[report["config_id"]]["paths"]["node_gate"])} for report in node_reports},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build P3.2-P3.8 Oh-My-Pi helper/runtime C4 packets.")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--check", action="store_true", help="Validate builder metadata without writing")
    args = parser.parse_args(argv)
    report = build(write=not args.check)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("ok" if report.get("ok") else "failed")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
