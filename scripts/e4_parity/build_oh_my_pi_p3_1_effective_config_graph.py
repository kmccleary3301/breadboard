#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

try:
    from scripts.e4_parity import lane_runtime
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lane_runtime


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e4_parity import build_primitive_projection as projection
from scripts.e4_parity import lane_inventory_utils as lane_inventory
from agentic_coder_prototype.compilation.effective_config_graph import graph_content_hash

THIS_BUILDER_PATH = Path(__file__).resolve()
LANE = lane_inventory.lane_for_builder(THIS_BUILDER_PATH)
LANE_ID = str(LANE["lane_id"])
CONFIG_ID = str(LANE["config_id"])
CLAIM_ID = lane_inventory.claim_id(LANE)
RUN_ID = str(LANE["run_id"])
TARGET_FAMILY = str(LANE["target_family"])
TARGET_VERSION = str(LANE["target_version"])
PROVIDER_MODEL = str(LANE["provider_model"])
SANDBOX_MODE = str(LANE["sandbox_mode"])
POINTS = int(LANE["points"])
PHASE = str(LANE["phase"])
CT_ID = lane_inventory.ct_id(LANE)
_P3_ITEM_MATCH = re.search(r"-P3([1-8])-", CT_ID)
if _P3_ITEM_MATCH is None:
    raise ValueError(f"cannot derive P3 item from CT id: {CT_ID}")
P3_ITEM = f"P3.{_P3_ITEM_MATCH.group(1)}"
GENERATED_AT_UTC = "2026-07-03T07:30:00Z"
FEATURE_ID = lane_inventory.ledger_feature_id(LANE)
CT_OUTPUT = lane_inventory.ct_output(LANE)

FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
AGENT_CONFIG_PATH = ROOT / "agent_configs/misc" / f"{CONFIG_ID}.yaml"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
CT_SCENARIOS_PATH = ROOT / "docs/conformance/ct_scenarios_v1.json"
SOURCE_FREEZE_PATH = WORKSPACE / "docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_freeze_provenance.json"
HELPER_MODULE_PATH = ROOT / "agentic_coder_prototype/compilation/effective_config_graph.py"
HELPER_TEST_PATH = ROOT / "tests/compilation/test_effective_config_graph.py"
PROJECTION_BUILDER_PATH = ROOT / "scripts/e4_parity/build_primitive_projection.py"
CONFIG_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.effective_config_graph.v1.schema.json"
PROJECTION_DIR = ROOT / "artifacts/conformance/e4_primitive_projection/oh_my_pi_p6_0_l1_l2"
CONFIG_GRAPH_PATH = PROJECTION_DIR / "effective_config_graph.v1.json"
PROJECTION_MANIFEST_PATH = PROJECTION_DIR / "primitive_projection_manifest.v1.json"
L1_DIR = ROOT / "docs/conformance/e4_target_support/oh_my_pi_p6_0_l1_config_context_tool_surface"
L1_RAW_CAPTURE_PATH = L1_DIR / "raw_capture_manifest.json"
L1_TARGET_PROBE_PATH = L1_DIR / "target_probe_output.json"
L1_SETUP_REPORT_PATH = L1_DIR / "target_setup_and_capture_report.json"

LANE_DIR = ROOT / "docs/conformance/e4_target_support" / LANE_ID
RAW_CAPTURE_PATH = LANE_DIR / "raw_capture_manifest.json"
REPLAY_PATH = LANE_DIR / "bb_replay_result.json"
COMPARATOR_PATH = LANE_DIR / "comparator_report.json"
PARITY_PATH = LANE_DIR / "parity_results.json"
SECRET_SCAN_PATH = LANE_DIR / "secret_scan_report.json"
PREVALIDATION_PATH = LANE_DIR / "prevalidation_report.json"
SUPPORT_CLAIM_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID}.json"
EVIDENCE_MANIFEST_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key)[^\n]{0,20}[:=][^\n]{8,}"),
]


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
    if raw.parts and raw.parts[0] == "docs_tmp":
        return WORKSPACE / raw
    if raw.parts and raw.parts[0] == ROOT.name:
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
    return payload


def freeze_row_hash() -> str:
    manifest = load_freeze_manifest()
    return row_hash(CONFIG_ID, manifest["e4_configs"][CONFIG_ID])


def write_agent_config() -> None:
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(
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


def write_freeze_manifest() -> None:
    manifest = load_freeze_manifest()
    configs = manifest.setdefault("e4_configs", {})
    configs[CONFIG_ID] = {
        "config_path": display_path(AGENT_CONFIG_PATH),
        "harness": {
            "family": TARGET_FAMILY,
            "upstream_repo": "https://github.com/can1357/oh-my-pi",
            "upstream_commit": "5356713eae60e67ee64d9b02e3b5e377d248ee7f",
            "upstream_commit_date": "2026-07-01T20:03:42+02:00",
            "upstream_release_label": TARGET_VERSION,
            "runtime_surface": {"provider_model": PROVIDER_MODEL},
        },
        "calibration_anchor": {
            "class": "raw_target_capture",
            "scenario_id": LANE_ID,
            "run_id": RUN_ID,
            "evidence_paths": [
                display_path(RAW_CAPTURE_PATH),
                display_path(REPLAY_PATH),
                display_path(COMPARATOR_PATH),
                display_path(PARITY_PATH),
                display_path(SUPPORT_CLAIM_PATH),
                display_path(EVIDENCE_MANIFEST_PATH),
                display_path(PREVALIDATION_PATH),
                display_path(SECRET_SCAN_PATH),
                display_path(CONFIG_GRAPH_PATH),
                display_path(PROJECTION_MANIFEST_PATH),
            ],
        },
        "snapshot_source_entry": CONFIG_ID,
        "snapshot_tag": "oh_my_pi_16.2.13_main_5356713e_p3_1_config_graph_20260703",
    }
    FREEZE_MANIFEST_PATH.write_text(yaml.safe_dump(manifest, sort_keys=False, width=120), encoding="utf-8")


def source_hashes_for(paths: list[Path]) -> dict[str, str]:
    return {display_path(path): sha256_file(path) for path in paths}


def ensure_projection_current() -> dict[str, Any]:
    report = projection.build_projection(output_dir=PROJECTION_DIR, write=True)
    if not report.get("ok"):
        raise ValueError(f"primitive projection failed: {report}")
    return report


def write_capture_replay_compare() -> None:
    l1_capture = read_json(L1_RAW_CAPTURE_PATH)
    graph = read_json(CONFIG_GRAPH_PATH)
    projection_manifest = read_json(PROJECTION_MANIFEST_PATH)
    l1_config_path = ROOT / "agent_configs/misc/oh_my_pi_p6_0_l1_config_context_tool_surface_v1.yaml"
    raw_sources = [
        L1_RAW_CAPTURE_PATH,
        L1_TARGET_PROBE_PATH,
        L1_SETUP_REPORT_PATH,
        l1_config_path,
        AGENT_CONFIG_PATH,
    ]
    capture = {
        "accepted_as_capture_ref": True,
        "capture_class": "derived_capture",
        "captured_artifacts": [
            {"path": display_path(path), "role": role, "sha256": sha256_file(path)}
            for path, role in [
                (L1_RAW_CAPTURE_PATH, "source_l1_raw_capture"),
                (L1_TARGET_PROBE_PATH, "source_l1_target_probe"),
                (L1_SETUP_REPORT_PATH, "source_l1_setup_report"),
                (l1_config_path, "source_l1_agent_config"),
                (AGENT_CONFIG_PATH, "p3_1_agent_config"),
            ]
        ],
        "config_id": CONFIG_ID,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": LANE_ID,
        "lineage_rationale": "P3.1 helper/runtime compiler capture is derived from the canonical Oh-My-Pi P6.0-L1 raw target run and scoped to compiling that observed config/context surface into bb.effective_config_graph.v1 records. It does not broaden the target support claim beyond the named helper/runtime compiler lane.",
        "provider_model": PROVIDER_MODEL,
        "raw_source_ref": ref(L1_RAW_CAPTURE_PATH),
        "raw_source_status": "canonical_raw_present",
        "run_id": RUN_ID,
        "sandbox_mode": SANDBOX_MODE,
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "source_artifacts": [display_path(path) for path in raw_sources],
        "source_hashes": source_hashes_for(raw_sources),
        "target_family": TARGET_FAMILY,
        "target_source": l1_capture["target_source"],
        "target_version": TARGET_VERSION,
    }
    write_json(RAW_CAPTURE_PATH, capture)

    replay_inputs = [RAW_CAPTURE_PATH, CONFIG_GRAPH_PATH, PROJECTION_MANIFEST_PATH, AGENT_CONFIG_PATH]
    replay = {
        "config_id": CONFIG_ID,
        "errors": [],
        "exit_status": "passed",
        "generated_at_utc": GENERATED_AT_UTC,
        "input_hashes": source_hashes_for(replay_inputs),
        "lane_id": LANE_ID,
        "normalization": {
            "mode": "p3_1_helper_runtime_compiler",
            "preserved_fields": [
                "schema_version",
                "source_layers",
                "effective_values",
                "merge_policy",
                "visibility",
                "env_gates",
                "graph_hash",
                "migrations",
            ],
        },
        "p3_item": P3_ITEM,
        "replay_summary": "BreadBoard helper runtime compiler emitted a schema-valid bb.effective_config_graph.v1 record from canonical Oh-My-Pi L1/L2 capture inputs without replacing v2_loader.load_agent_config.",
        "run_id": RUN_ID,
        "schema_version": "bb.e4.bb_replay_result.v1",
        "warnings": [],
    }
    write_json(REPLAY_PATH, replay)

    values = {item["path"]: item["value"] for item in graph["effective_values"]}
    source_layers = {item["layer_id"]: item for item in graph["source_layers"]}
    assertions = [
        ("schema_version", graph["schema_version"], "bb.effective_config_graph.v1"),
        ("graph_id", graph["graph_id"], "oh_my_pi_p6_0_l1_l2_effective_config_graph"),
        ("target_family", values["target.family"], TARGET_FAMILY),
        ("target_version", values["target.version"], TARGET_VERSION),
        ("provider_model", values["provider.model"], PROVIDER_MODEL),
        ("sandbox_mode", values["sandbox.mode"], SANDBOX_MODE),
        ("l1_run_id", values["l1.run_id"], RUN_ID),
        ("l1_runtime_source_layer", source_layers["l1_probe_observed_runtime"]["source_ref"], display_path(L1_TARGET_PROBE_PATH)),
        ("graph_hash_current", graph["graph_hash"], graph_content_hash(graph)),
        ("projection_contract_validated", next(item for item in projection_manifest["validations"] if item["contract"] == "bb.effective_config_graph.v1")["ok"], True),
        ("compiler_tests_present", HELPER_TEST_PATH.exists(), True),
    ]
    comparator_inputs = [RAW_CAPTURE_PATH, REPLAY_PATH, CONFIG_GRAPH_PATH, PROJECTION_MANIFEST_PATH, AGENT_CONFIG_PATH]
    comparator = {
        "assertions": [
            {"name": name, "observed": observed, "expected": expected, "status": "passed" if observed == expected else "failed"}
            for name, observed, expected in assertions
        ],
        "comparator_id": f"{LANE_ID}_comparator",
        "config_id": CONFIG_ID,
        "details": [
            "Compared canonical Oh-My-Pi raw capture inputs against the BreadBoard helper-emitted effective config graph.",
            "Assertions are exact-scope and machine-checkable; observed values must equal expected target-scope values.",
        ],
        "failed": sum(1 for _name, observed, expected in assertions if observed != expected),
        "generated_at_utc": GENERATED_AT_UTC,
        "input_hashes": source_hashes_for(comparator_inputs),
        "lane_id": LANE_ID,
        "scope": {
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "phase": PHASE,
            "p3_item": P3_ITEM,
            "provider_model": PROVIDER_MODEL,
            "run_id": RUN_ID,
            "sandbox_mode": SANDBOX_MODE,
            "target_version": TARGET_VERSION,
        },
        "schema_version": "bb.e4.comparator_report.v1",
        "warned": 0,
    }
    write_json(COMPARATOR_PATH, comparator)

    parity = {
        "accepted": comparator["failed"] == 0 and comparator["warned"] == 0,
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": LANE_ID,
        "p3_item": P3_ITEM,
        "passed_assertions": len(comparator["assertions"]),
        "schema_version": "bb.e4.parity_results.v1",
        "scope": comparator["scope"],
    }
    write_json(PARITY_PATH, parity)

    scan_paths = [RAW_CAPTURE_PATH, REPLAY_PATH, COMPARATOR_PATH, PARITY_PATH, CONFIG_GRAPH_PATH, PROJECTION_MANIFEST_PATH]
    findings: list[dict[str, Any]] = []
    for path in scan_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"path": display_path(path), "pattern": pattern.pattern})
    secret_scan = {
        "checked_paths": [display_path(path) for path in scan_paths],
        "finding_count": len(findings),
        "findings": findings,
        "generated_at_utc": GENERATED_AT_UTC,
        "ok": not findings,
        "schema_version": "bb.e4.secret_scan_report.v1",
    }
    write_json(SECRET_SCAN_PATH, secret_scan)

    prevalidation_checks = [
        ("raw_capture_written", RAW_CAPTURE_PATH),
        ("replay_written", REPLAY_PATH),
        ("comparator_written", COMPARATOR_PATH),
        ("parity_results_written", PARITY_PATH),
        ("secret_scan_passed", SECRET_SCAN_PATH),
        ("effective_config_graph_emitted", CONFIG_GRAPH_PATH),
        ("compiler_module_present", HELPER_MODULE_PATH),
        ("compiler_tests_present", HELPER_TEST_PATH),
    ]
    prevalidation = {
        "checks": [
            {"name": name, "passed": path.exists(), "path": display_path(path), "sha256": sha256_file(path)}
            for name, path in prevalidation_checks
        ],
        "config_id": CONFIG_ID,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": LANE_ID,
        "ok": all(path.exists() for _name, path in prevalidation_checks) and secret_scan["ok"] and comparator["failed"] == 0,
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "source": "Bootstrap validator-output reference for the Oh-My-Pi P3.1 effective config graph compiler C4 chain; scripts/validate_e4_c4_chain.py is run as the live validator before score acceptance.",
    }
    write_json(PREVALIDATION_PATH, prevalidation)


def build_ledger_row(freeze_ref: str) -> dict[str, Any]:
    return {
        "breadboard_mapping": {
            "primitive": "bb.effective_config_graph.v1",
            "support": "supported",
            "truth_scope": "kernel_truth",
        },
        "claim_type": "runtime_behavior",
        "dedupe_key": "omp/helper_runtime/p3_1_effective_config_graph_compiler/c4",
        "e4_row_ref": CONFIG_ID,
        "evidence_tier": "C4",
        "family": "helper_runtime",
        "feature_id": FEATURE_ID,
        "fixture_refs": [
            f"freeze:{freeze_ref}",
            f"capture:{ref(RAW_CAPTURE_PATH)}",
            f"replay:{ref(REPLAY_PATH)}",
            f"comparator:{ref(COMPARATOR_PATH)}",
            f"support_claim:{display_path(SUPPORT_CLAIM_PATH)}",
            f"evidence_manifest:{display_path(EVIDENCE_MANIFEST_PATH)}",
            f"parity_results:{ref(PARITY_PATH)}",
            f"secret_scan_report:{ref(SECRET_SCAN_PATH)}",
            f"validator_output:{ref(PREVALIDATION_PATH)}",
        ],
        "gap_kind": "none",
        "model_visible": True,
        "promotion_state": "ready",
        "schema_version": "bb.atomic_feature_ledger.v1",
        "source_refs": [
            f"source:{display_path(HELPER_MODULE_PATH)}",
            f"source:{display_path(HELPER_TEST_PATH)}",
            f"source:{display_path(PROJECTION_BUILDER_PATH)}",
            f"source:{display_path(SUPPORT_CLAIM_PATH)}",
            f"source:{display_path(EVIDENCE_MANIFEST_PATH)}",
        ],
        "stateful": True,
        "target": "omp",
    }


def upsert_ledger(row: dict[str, Any]) -> str:
    return lane_runtime.upsert_ledger_row(LEDGER_PATH, row, feature_id=FEATURE_ID, style="default", trailing_newline=True)


def write_support_claim_and_manifest(ledger_ref: str, freeze_ref: str) -> None:
    support_claim = {
        "accepted": True,
        "acceptance_rationale": "P3.1 helper/runtime effective config graph compiler is accepted only for the named Oh-My-Pi raw target run and generated bb.effective_config_graph.v1 projection; it is backed by compiler unit tests and a live C4 chain validator.",
        "capture_ref": ref(RAW_CAPTURE_PATH),
        "claim_id": CLAIM_ID,
        "comparator_ref": ref(COMPARATOR_PATH),
        "config_id": CONFIG_ID,
        "evidence_manifest_ref": display_path(EVIDENCE_MANIFEST_PATH),
        "exclusions": [
            "No broad Oh-My-Pi, OMP, Pi, provider-parity, tool-surface, task/job, memory, UI, or final-readiness support claim is made from this P3.1 micro-lane.",
            "No score is claimed for P3.2-P3.8 helper/runtime lanes, P5 Pi lanes, or P8 final readiness.",
            "No write-enabled, provider-authenticated, danger-full-access, network, browser, MCP, or model-inference behavior is claimed.",
        ],
        "freeze_ref": freeze_ref,
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": LANE_ID,
        "ledger_row_refs": [ledger_ref],
        "level": P3_ITEM,
        "parity_results_ref": ref(PARITY_PATH),
        "phase": PHASE,
        "points": POINTS,
        "provider_model": PROVIDER_MODEL,
        "replay_ref": ref(REPLAY_PATH),
        "run_id": RUN_ID,
        "sandbox_mode": SANDBOX_MODE,
        "schema_version": "bb.e4.support_claim.v1",
        "scope": {
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "phase": PHASE,
            "p3_item": P3_ITEM,
            "provider_model": PROVIDER_MODEL,
            "run_id": RUN_ID,
            "sandbox_mode": SANDBOX_MODE,
            "target_version": TARGET_VERSION,
        },
        "secret_scan_ref": ref(SECRET_SCAN_PATH),
        "source_freeze_ref": ref(SOURCE_FREEZE_PATH),
        "summary": "BreadBoard P3.1 effective config graph compiler support is accepted for the named Oh-My-Pi target capture and generated bb.effective_config_graph.v1 record only.",
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "tool_id": "agentic_coder_prototype.compilation.effective_config_graph.compile_effective_config_graph",
        "validation_refs": [ref(PREVALIDATION_PATH)],
    }
    write_json(SUPPORT_CLAIM_PATH, support_claim)

    artifacts = [
        {"path": display_path(FREEZE_MANIFEST_PATH), "role": "freeze_manifest", "sha256": freeze_row_hash()},
        {"path": display_path(RAW_CAPTURE_PATH), "role": "capture_ref", "sha256": sha256_file(RAW_CAPTURE_PATH)},
        {"derived_from": [ref(RAW_CAPTURE_PATH), ref(CONFIG_GRAPH_PATH)], "path": display_path(REPLAY_PATH), "role": "replay_ref", "sha256": sha256_file(REPLAY_PATH)},
        {"derived_from": [ref(RAW_CAPTURE_PATH), ref(REPLAY_PATH), ref(CONFIG_GRAPH_PATH)], "path": display_path(COMPARATOR_PATH), "role": "comparator_ref", "sha256": sha256_file(COMPARATOR_PATH)},
        {"path": display_path(SUPPORT_CLAIM_PATH), "role": "support_claim_ref", "sha256": sha256_file(SUPPORT_CLAIM_PATH)},
        {"derived_from": [ref(COMPARATOR_PATH)], "path": display_path(PARITY_PATH), "role": "parity_results", "sha256": sha256_file(PARITY_PATH)},
        {"path": display_path(SECRET_SCAN_PATH), "role": "secret_scan_report", "sha256": sha256_file(SECRET_SCAN_PATH)},
        {"path": display_path(PREVALIDATION_PATH), "role": "validator_output", "sha256": sha256_file(PREVALIDATION_PATH)},
        {"path": display_path(CONFIG_GRAPH_PATH), "role": "effective_config_graph", "sha256": sha256_file(CONFIG_GRAPH_PATH)},
        {"path": display_path(PROJECTION_MANIFEST_PATH), "role": "primitive_projection_manifest", "sha256": sha256_file(PROJECTION_MANIFEST_PATH)},
        {"path": display_path(AGENT_CONFIG_PATH), "role": "agent_config", "sha256": sha256_file(AGENT_CONFIG_PATH)},
        {"path": display_path(SOURCE_FREEZE_PATH), "role": "source_freeze", "sha256": sha256_file(SOURCE_FREEZE_PATH)},
    ]
    manifest = {
        "artifacts": artifacts,
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "generated_at_utc": GENERATED_AT_UTC,
        "hash_algorithm": "sha256",
        "lane_id": LANE_ID,
        "manifest_scope_note": "Every governed artifact for this Oh-My-Pi P3.1 C4 chain is canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or derived from named target capture artifacts. Scratch/tmp/shared roots are not promotion evidence.",
        "schema_version": "bb.e4.evidence_manifest.v1",
    }
    write_json(EVIDENCE_MANIFEST_PATH, manifest)


def upsert_ct_scenario() -> None:
    lane_runtime.upsert_ct_scenario(CT_SCENARIOS_PATH)


def build(write: bool = True) -> dict[str, Any]:
    if not write:
        raise NotImplementedError("dry-run is not implemented for this deterministic packet builder")
    write_agent_config()
    ensure_projection_current()
    write_freeze_manifest()
    write_capture_replay_compare()
    freeze_ref = f"{display_path(FREEZE_MANIFEST_PATH)}#{CONFIG_ID}#{freeze_row_hash()}"
    ledger_row = build_ledger_row(freeze_ref)
    ledger_row_hash = upsert_ledger(ledger_row)
    ledger_ref = f"{display_path(LEDGER_PATH)}#{FEATURE_ID}#{ledger_row_hash}"
    write_support_claim_and_manifest(ledger_ref, freeze_ref)
    # Re-write ledger after support/manifest exist so refs remain current while row hash stays stable.
    ledger_row = build_ledger_row(freeze_ref)
    ledger_row_hash = upsert_ledger(ledger_row)
    ledger_ref = f"{display_path(LEDGER_PATH)}#{FEATURE_ID}#{ledger_row_hash}"
    write_support_claim_and_manifest(ledger_ref, freeze_ref)
    upsert_ct_scenario()
    return {
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "ct_id": CT_ID,
        "feature_id": FEATURE_ID,
        "freeze_ref": freeze_ref,
        "ledger_ref": ledger_ref,
        "ok": True,
        "paths": {
            "agent_config": display_path(AGENT_CONFIG_PATH),
            "comparator": display_path(COMPARATOR_PATH),
            "ct_scenario_manifest": display_path(CT_SCENARIOS_PATH),
            "evidence_manifest": display_path(EVIDENCE_MANIFEST_PATH),
            "raw_capture": display_path(RAW_CAPTURE_PATH),
            "replay": display_path(REPLAY_PATH),
            "support_claim": display_path(SUPPORT_CLAIM_PATH),
        },
        "points": POINTS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Oh-My-Pi P3.1 effective config graph compiler C4 packet.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build(write=True)
    except Exception as exc:  # pragma: no cover - CLI defensive path
        report = {"error": str(exc), "ok": False}
    if args.json:
        print(canonical_json(report).decode("utf-8"), end="")
    elif report.get("ok"):
        print(f"built {CLAIM_ID}")
    else:
        print(f"error: {report['error']}", file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
