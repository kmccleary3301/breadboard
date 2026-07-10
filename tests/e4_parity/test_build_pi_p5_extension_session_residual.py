from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from scripts.e4_parity.adapters import pi_p5_l2_capture as builder
from scripts.e4_parity.validators.registries import schema_generation_default


EXPECTED_ASSERTIONS = {
    "package_version",
    "extension_loaded_once",
    "extension_load_errors",
    "extension_registered_tool",
    "extension_registered_command",
    "extension_registered_flag",
    "extension_event_coverage",
    "provider_payload_patched",
    "context_hook_appended_message",
    "tool_write_blocked",
    "session_switch_resume_allowed",
    "session_fork_policy",
    "session_compaction_hook_result",
    "session_tree_hook_summary",
    "session_file_has_compaction",
    "branched_session_parent_ref",
    "forked_session_parent",
    "continue_recent_returns_valid_session_file",
    "reopened_session_id_matches_branch",
    "work_item_schema_replay",
    "memory_plan_schema_replay",
    "patch_schema_replay",
    "no_provider_secrets",
}

REQUIRED_ROLES = {
    "freeze_manifest",
    "capture_ref",
    "replay_ref",
    "comparator_ref",
    "support_claim_ref",
    "parity_results",
    "secret_scan_report",
    "validator_output",
    "target_setup_report",
    "target_probe_output",
    "target_probe_script",
    "agent_config",
    "source_freeze",
    "source_archive",
}

REQUIRED_RECORDS = {
    "extension_hook_execution_provider": "bb.extension_hook_execution.v1",
    "extension_hook_execution_session": "bb.extension_hook_execution.v1",
    "work_item_session_resume_fork": "bb.work_item.v1",
    "memory_compaction_plan": "bb.memory_compaction_plan.v1",
    "transcript_continuation_patch": "bb.transcript_continuation_patch.v1",
}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_display_path(path_ref: str) -> Path:
    path = Path(path_ref.split("#", 1)[0])
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "docs_tmp":
        return builder.WORKSPACE / path
    return builder.ROOT / path


def assert_ref_hash_current(ref: str) -> None:
    path_text, digest = ref.rsplit("#", 1)
    assert digest.startswith("sha256:")
    assert sha256_file(resolve_display_path(path_text)) == digest


def assert_schema_valid(record: Mapping[str, Any]) -> None:
    schema_version = record["schema_version"]
    schema = load_json(builder.SCHEMA_DIR / f"{schema_version}.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(record), key=lambda item: list(item.absolute_path))
    assert [error.message for error in errors] == []


def test_residual_claim_constants_are_exact_scope() -> None:
    assert builder.LANE_ID == "pi_p5_l2_extension_session_residual"
    assert builder.CONFIG_ID == "pi_p5_l2_extension_session_residual_v1"
    assert builder.CLAIM_ID == "pi_p5_l2_extension_session_residual_v1_c4_support_claim"
    assert builder.POINTS == 40
    assert builder.P5_ITEMS == ["P5.2-residual", "P5.3-residual", "P5.5", "P5.6"]
    assert builder.PROVIDER_MODEL == "no-provider"
    assert builder.SANDBOX_MODE == "read-only-no-secret"


def test_replay_records_are_schema_valid_and_cover_extension_session_residuals() -> None:
    replay = load_json(builder.REPLAY_PATH)
    records = replay["normalized_records"]
    assert {name: record["schema_version"] for name, record in records.items()} == REQUIRED_RECORDS
    for record in records.values():
        assert_schema_valid(record)

    session_hook = records["extension_hook_execution_session"]
    assert session_hook["status"] == "completed"
    assert {effect["effect_ref"] for effect in session_hook["effects"]} >= {
        "pi.session_before_switch:cancel=false",
        "pi.session_before_fork:skipConversationRestore=true",
        "bb.memory_compaction_plan.v1:pi_p5_l2_extension_session_residual_memory_compaction_plan",
        "pi.session_before_tree:summary",
    }
    assert records["work_item_session_resume_fork"]["resume_policy"]["mode"] == "checkpoint"
    assert records["memory_compaction_plan"]["trigger"]["source_ref"] == "pi.extension_event:session_before_compact"
    assert records["transcript_continuation_patch"]["lossiness_flags"] == ["compaction_summary_replaces_elided_branch_tail"]


def test_comparator_prevalidation_secret_scan_and_node_gate_are_passing() -> None:
    comparator = load_json(builder.COMPARATOR_PATH)
    assert comparator["failed"] == 0
    assert {item["name"] for item in comparator["assertions"]} == EXPECTED_ASSERTIONS
    assert all(item["status"] == "passed" for item in comparator["assertions"])

    prevalidation = load_json(builder.PREVALIDATION_PATH)
    assert prevalidation["ok"] is True
    assert all(check["passed"] is True for check in prevalidation["checks"])

    secret_scan = load_json(builder.SECRET_SCAN_PATH)
    assert secret_scan["passed"] is True
    assert secret_scan["findings"] == []

    node_gate = load_json(builder.NODE_GATE_PATH)
    assert node_gate["ok"] is True
    assert node_gate["errors"] == []


def test_support_claim_and_evidence_manifest_use_current_hashes() -> None:
    support = load_json(builder.SUPPORT_CLAIM_PATH)
    assert support["accepted"] is True
    assert support["schema_version"] == schema_generation_default("support_claim")
    assert support["scope"] == {
        "config_id": builder.CONFIG_ID,
        "lane_id": builder.LANE_ID,
        "provider_model": builder.PROVIDER_MODEL,
        "run_id": builder.RUN_ID,
        "sandbox_mode": builder.SANDBOX_MODE,
        "target_version": builder.TARGET_VERSION,
        "target_family": builder.TARGET_FAMILY,
    }
    assert support["metadata"]["legacy_scope"] == {
        "phase": builder.PHASE,
        "p5_items": builder.P5_ITEMS,
    }
    for ref in [support["capture_ref"], support["replay_ref"], support["comparator_ref"], support["parity_results_ref"], support["secret_scan_ref"], support["source_freeze_ref"], *support["validation_refs"]]:
        assert_ref_hash_current(ref)

    manifest = load_json(builder.EVIDENCE_MANIFEST_PATH)
    assert manifest["claim_id"] == builder.CLAIM_ID
    roles = {artifact["role"] for artifact in manifest["artifacts"]}
    assert REQUIRED_ROLES <= roles
    for artifact in manifest["artifacts"]:
        role = artifact["role"]
        if role == "freeze_manifest":
            assert artifact["sha256"] == builder.freeze_row_hash()
        else:
            assert sha256_file(resolve_display_path(artifact["path"])) == artifact["sha256"]

    ledger_ref = support["ledger_row_refs"][0]
    ledger_path, feature_id, digest = ledger_ref.split("#")
    ledger = load_json(resolve_display_path(ledger_path))
    row = next(item for item in ledger["rows"] if item["feature_id"] == feature_id)
    assert digest == builder.row_hash(feature_id, row)
