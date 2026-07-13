from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath
from typing import Any


FINAL_REPORT_ID = "bb_zyphra_rl_phase1_m12_final_report_v1"
FINAL_REPORT_CLAIM_BOUNDARY = "m12_target_validation_candidate_not_scorecard_update"
ARCHIVE_VERIFY_REPORT_ID = "bb_zyphra_rl_phase1_m12_archive_verify_report_v1"
ARCHIVE_VERIFY_CLAIM_BOUNDARY = "transfer_archive_verification_not_m12_validation"
REQUIRED_LOAD_LEVELS = [5, 20, 50]
OPTIONAL_LOAD_LEVELS = [100]
MIN_SOAK_SECONDS = 2 * 60 * 60
COMMAND_LOG_MANIFEST_ID = "bb_zyphra_rl_phase1_m12_command_log_manifest_v1"
REQUIRED_COMMAND_LOG_IDS = [
    "target_transfer_archive_verify",
    "phase1_validation_suite",
    "target_preflight",
    "target_swe_probe",
    "target_verl_export",
    "target_ray_warm_pool",
    "target_load_ladder",
    "target_soak",
]
REQUIRED_COMMAND_LOG_COMMANDS = {
    "target_transfer_archive_verify": (
        "python scripts/rl_phase1/verify_m12_transfer_archive.py "
        "--manifest ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep/m12_transfer_archive_manifest.json "
        "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json"
    ),
    "target_preflight": (
        "python scripts/rl_phase1/run_m12_preflight.py "
        "--output-dir ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight --require-pass"
    ),
    "phase1_validation_suite": "python -m pytest tests/test_rl_phase1_scorecard_schema.py tests/test_rl_phase1_claim_ledger.py tests/rl -q",
    "target_swe_probe": (
        "python scripts/rl_phase1/run_swe_probe.py --package examples/rl_env_packages/swe_toy_patch/env_package.yaml "
        "--output-dir ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe --run-id m12_node_swe_probe --limit 10"
    ),
    "target_verl_export": (
        "python scripts/rl_phase1/export_verl_probe.py "
        "--m6-summary ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe/run_summary.json "
        "--output-dir ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_verl_probe"
    ),
    "target_ray_warm_pool": (
        "python scripts/rl_phase1/run_ray_warm_pool_probe.py "
        "--package examples/rl_env_packages/python_console_toy/env_package.yaml "
        "--output-dir ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe --limit 20 --num-workers 20 --distributed"
    ),
    "target_load_ladder": (
        "python scripts/rl_phase1/run_m12_load_ladder.py --package examples/rl_env_packages/python_console_toy/env_package.yaml "
        "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json"
    ),
    "target_soak": (
        "python scripts/rl_phase1/run_m12_soak.py --package examples/rl_env_packages/python_console_toy/env_package.yaml "
        "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json"
    ),
}
OPTIONAL_COMMAND_LOG_COMMANDS = {
    "final_report": (
        "python scripts/rl_phase1/build_m12_final_report.py "
        "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json "
        "--archive-verify-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json "
        "--preflight-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight/m12_preflight_report.json "
        "--swe-run-summary ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe/run_summary.json "
        "--verl-smoke-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_verl_probe/smoke_consumer_report.json "
        "--ray-probe-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/ray_probe_report.json "
        "--warm-vs-cold-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/warm_vs_cold_report.json "
        "--load-ladder-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json "
        "--soak-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json "
        "--command-log-manifest ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json "
        "--require-eligible"
    ),
    "promotion_audit": (
        "python scripts/rl_phase1/audit_m12_score_promotion.py "
        "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json "
        "--final-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json "
        "--scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml "
        "--claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md "
        "--command-log-manifest ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json "
        "--require-ready"
    ),
}
TARGET_ARTIFACT_PATHS = {
    "archive_verify_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json",
    "preflight_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight/m12_preflight_report.json",
    "swe_run_summary": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe/run_summary.json",
    "verl_smoke_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_verl_probe/smoke_consumer_report.json",
    "ray_probe_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/ray_probe_report.json",
    "warm_vs_cold_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/warm_vs_cold_report.json",
    "load_ladder_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json",
    "soak_report": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json",
    "command_log_manifest": "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json",
}
TARGET_COMMAND_LOG_MANIFEST_PATH = TARGET_ARTIFACT_PATHS["command_log_manifest"]
TARGET_SCORECARD_PATH = "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"
TARGET_CLAIM_LEDGER_PATH = "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
TARGET_FINAL_REPORT_PATH = "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"
TARGET_PROMOTION_AUDIT_PATH = "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"
M12_FINAL_REPORT_GATE_NAMES = {
    "artifact_paths_match_target_defaults",
    "archive_verify_report_present",
    "archive_verify_report_readable",
    "archive_verify_report_valid",
    "archive_verify_status_passed",
    "preflight_report_readable",
    "preflight_report_valid",
    "swe_run_summary_readable",
    "swe_run_summary_valid",
    "verl_smoke_report_readable",
    "verl_smoke_report_valid",
    "ray_probe_report_readable",
    "ray_probe_report_valid",
    "warm_vs_cold_report_readable",
    "warm_vs_cold_report_valid",
    "preflight_passed",
    "preflight_runtime_fingerprint_present",
    "target_hardware",
    "verl_available",
    "ray_available",
    "inference_engine_available",
    "container_runtime_available",
    "filesystem_cas_smoke_passed",
    "swe_probe_ran_10_rows",
    "swe_probe_has_accepted_rows",
    "swe_probe_no_unknown_status",
    "verl_jsonl_tensorizable",
    "verl_parquet_tensorizable",
    "verl_row_count_matches_swe",
    "ray_probe_ran_10_rows",
    "ray_probe_has_workers",
    "ray_probe_distributed",
    "warm_vs_cold_has_total_ms",
    "load_ladder_report_present",
    "load_ladder_report_valid",
    "load_ladder_required_levels_passed",
    "load_ladder_100_attempted_or_skipped",
    "load_ladder_policy_integrity",
    "load_ladder_no_queue_corruption",
    "load_ladder_distributed",
    "soak_report_present",
    "soak_report_valid",
    "soak_status_passed",
    "soak_duration_at_least_2h",
    "soak_no_runtime_failures",
    "soak_distributed",
    "command_log_manifest_present",
    "command_log_manifest_id_valid",
    "command_log_manifest_valid",
    "command_log_required_ids_canonical",
    "command_log_manifest_complete",
    "command_log_hashes_present",
    "command_log_hashes_verified",
    "command_log_commands_passed",
    "command_log_required_logs_archived_summary",
    "command_log_required_commands_passed_summary",
    "command_log_single_target_run_id",
    "command_log_expected_commands_match",
    "target_artifact_run_id_binding",
}
RUNTIME_FINGERPRINT_ID = "bb_zyphra_rl_phase1_m12_runtime_fingerprint_v1"
RUNTIME_FINGERPRINT_ENV_POLICY = "allowlist_only_redact_path_values_no_secret_keys_no_absolute_python_paths"
RUNTIME_FINGERPRINT_REDACTED_ABSOLUTE_PATH = "<redacted:absolute_or_home_path>"
RUNTIME_FINGERPRINT_REDACTION_REASON_ABSOLUTE_PATH = "absolute_or_home_path"
RUNTIME_FINGERPRINT_ALLOWED_ENV_KEYS = {
    "CUDA_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "ROCR_VISIBLE_DEVICES",
    "HSA_VISIBLE_DEVICES",
    "RAY_ADDRESS",
    "CONDA_DEFAULT_ENV",
}
RUNTIME_FINGERPRINT_FORBIDDEN_ENV_KEY_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
RUNTIME_FINGERPRINT_KEYS = {
    "fingerprint_id",
    "gpu_summary",
    "platform",
    "python_modules",
    "ray_summary",
    "sanitized_environment",
    "sha256",
    "tool_presence",
}
RUNTIME_FINGERPRINT_PLATFORM_KEYS = {
    "machine",
    "python",
    "python_executable_name",
    "python_implementation",
    "release",
    "system",
}
RUNTIME_FINGERPRINT_TOOL_PRESENCE_KEYS = {
    "docker",
    "firecracker",
    "gvisor_runsc",
    "rocm_smi",
    "rocminfo",
}
RUNTIME_FINGERPRINT_PYTHON_MODULE_KEYS = {"ray", "sglang", "torch", "verl", "vllm"}
RUNTIME_FINGERPRINT_GPU_SUMMARY_KEYS = {"rocm_smi_output", "torch_probe"}
RUNTIME_FINGERPRINT_RAY_SUMMARY_KEYS = {"status_output"}
RUNTIME_FINGERPRINT_SANITIZED_ENVIRONMENT_KEYS = {"keys", "policy", "redactions", "values"}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "read_error": "FileNotFoundError: JSON artifact is missing",
        }
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "present": True,
            "path": str(path),
            "read_error": f"{exc.__class__.__name__}: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "present": True,
            "path": str(path),
            "read_error": f"expected JSON object, got {type(payload).__name__}",
        }
    payload.setdefault("present", True)
    payload.setdefault("path", str(path))
    return payload


def _read_optional_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"present": False, "path": None}
    return _read_json_object(path)


def _read_error(payload: dict[str, Any]) -> str | None:
    error = payload.get("read_error")
    return str(error) if error else None


def _archive_verify_validation_errors(report: dict[str, Any]) -> list[str]:
    if report.get("present") is not True:
        return []
    if report.get("read_error"):
        return [f"archive verify report unreadable: {report.get('read_error')}"]
    errors: list[str] = []
    if report.get("report_id") != ARCHIVE_VERIFY_REPORT_ID:
        errors.append(f"report_id must be {ARCHIVE_VERIFY_REPORT_ID}")
    if report.get("claim_boundary") != ARCHIVE_VERIFY_CLAIM_BOUNDARY:
        errors.append(f"claim_boundary must remain {ARCHIVE_VERIFY_CLAIM_BOUNDARY}")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if report.get("status") not in {"passed", "failed"}:
        errors.append("status must be passed or failed")
    if report.get("archive_manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1":
        errors.append("archive_manifest_id must be bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1")
    if report.get("archive_claim_boundary") != "transfer_archive_only_not_m12_validation":
        errors.append("archive_claim_boundary must remain transfer_archive_only_not_m12_validation")
    if not str(report.get("archive_sha256") or "").startswith("sha256:"):
        errors.append("archive_sha256 must start with sha256:")
    if _int_or_zero(report.get("included_entry_count")) <= 0:
        errors.append("included_entry_count must be positive")
    for field in [
        "all_required_artifacts_present",
        "all_transfer_requirements_covered",
        "archive_contains_source_overlay",
        "archive_deterministic",
        "source_paths_portable",
    ]:
        if report.get(field) is not True:
            errors.append(f"{field} must be true")
    if report.get("manifest_read_error"):
        errors.append("manifest_read_error must be empty")
    report_errors = report.get("errors")
    if not isinstance(report_errors, list):
        errors.append("errors must be a list")
    elif report.get("status") == "passed" and report_errors != []:
        errors.append("passed report must have no errors")
    return errors


def _artifact_paths_match_target_defaults(artifact_paths: dict[str, Any]) -> bool:
    return {
        key: str(value)
        for key, value in artifact_paths.items()
        if value is not None
    } == TARGET_ARTIFACT_PATHS


def _gate_remediation(gate: str) -> dict[str, Any]:
    archive_verify_gates = {
        "archive_verify_report_present",
        "archive_verify_report_readable",
        "archive_verify_report_valid",
        "archive_verify_status_passed",
    }
    preflight_gates = {
        "preflight_report_readable",
        "preflight_report_valid",
        "preflight_passed",
        "preflight_runtime_fingerprint_present",
        "target_hardware",
        "verl_available",
        "ray_available",
        "inference_engine_available",
        "container_runtime_available",
        "filesystem_cas_smoke_passed",
    }
    swe_gates = {
        "swe_run_summary_readable",
        "swe_run_summary_valid",
        "swe_probe_ran_10_rows",
        "swe_probe_has_accepted_rows",
        "swe_probe_no_unknown_status",
    }
    verl_gates = {
        "verl_smoke_report_readable",
        "verl_smoke_report_valid",
        "verl_jsonl_tensorizable",
        "verl_parquet_tensorizable",
        "verl_row_count_matches_swe",
    }
    ray_gates = {
        "ray_probe_report_readable",
        "ray_probe_report_valid",
        "ray_probe_ran_10_rows",
        "ray_probe_has_workers",
        "ray_probe_distributed",
    }
    warm_gates = {
        "warm_vs_cold_report_readable",
        "warm_vs_cold_report_valid",
        "warm_vs_cold_has_total_ms",
    }
    load_gates = {
        "load_ladder_report_present",
        "load_ladder_report_valid",
        "load_ladder_required_levels_passed",
        "load_ladder_100_attempted_or_skipped",
        "load_ladder_policy_integrity",
        "load_ladder_no_queue_corruption",
        "load_ladder_distributed",
    }
    soak_gates = {
        "soak_report_present",
        "soak_report_valid",
        "soak_status_passed",
        "soak_duration_at_least_2h",
        "soak_no_runtime_failures",
        "soak_distributed",
    }

    if gate == "artifact_paths_match_target_defaults":
        return {
            "gate": gate,
            "blocking_stage": "final_report",
            "target_action_id": "final_report",
            "required_artifact_path": None,
            "operator_action": (
                "Build the final report from the target-node default M12 artifact paths generated in "
                "m12_test_commands.sh; local M6/M7/M8 preparation paths are intentionally non-promotable."
            ),
        }
    if gate in archive_verify_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_transfer_archive_verify",
            "target_action_id": "target_transfer_archive_verify",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["archive_verify_report"],
            "operator_action": (
                "Run target_transfer_archive_verify through m12_test_commands.sh and preserve "
                "m12_archive_verify/m12_archive_verify_report.json. The report must be readable, passed, "
                "non-scoring, and consistent with the transfer archive manifest before target validation can proceed."
            ),
        }
    if gate in preflight_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_preflight",
            "target_action_id": "target_preflight",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["preflight_report"],
            "operator_action": (
                "Run target_preflight through run_m12_logged_command.py on the 8xMI300X target and resolve "
                "ROCm/GPU, Ray, VeRL, container, filesystem/CAS, or runtime-fingerprint blockers before continuing."
            ),
        }
    if gate in swe_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_swe_probe",
            "target_action_id": "target_swe_probe",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["swe_run_summary"],
            "operator_action": (
                "Run the target SWE probe from m12_test_commands.sh and preserve the run summary with at least "
                "10 rows, known row statuses, and at least one accepted hardened row."
            ),
        }
    if gate in verl_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_verl_export",
            "target_action_id": "target_verl_export",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["verl_smoke_report"],
            "operator_action": (
                "Run the target VeRL export probe from the target SWE summary and fix JSONL/Parquet tensorization "
                "or row-count drift before promotion review."
            ),
        }
    if gate in ray_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_ray_warm_pool",
            "target_action_id": "target_ray_warm_pool",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["ray_probe_report"],
            "operator_action": (
                "Run the distributed Ray warm-worker probe from m12_test_commands.sh; local_mode output is a "
                "rehearsal artifact and cannot satisfy M12."
            ),
        }
    if gate in warm_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_ray_warm_pool",
            "target_action_id": "target_ray_warm_pool",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["warm_vs_cold_report"],
            "operator_action": (
                "Re-run or repair the Ray warm-worker comparison until the warm-vs-cold report is readable, valid, "
                "and includes total_ms summaries."
            ),
        }
    if gate in load_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_load_ladder",
            "target_action_id": "target_load_ladder",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["load_ladder_report"],
            "operator_action": (
                "Run the concrete target load ladder in distributed mode. Levels 5, 20, and 50 must pass; level 100 "
                "must either pass or be resource-skipped with a reason, with policy and queue integrity preserved."
            ),
        }
    if gate in soak_gates:
        return {
            "gate": gate,
            "blocking_stage": "target_soak",
            "target_action_id": "target_soak",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["soak_report"],
            "operator_action": (
                "Run the concrete target soak in distributed mode for at least 7200 seconds and preserve a passed "
                "report with zero runtime failures."
            ),
        }
    if gate.startswith("command_log_"):
        return {
            "gate": gate,
            "blocking_stage": "command_log_manifest",
            "target_action_id": "m12_test_commands.sh",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["command_log_manifest"],
            "operator_action": (
                "Run every target command through run_m12_logged_command.py from m12_test_commands.sh, preserve raw "
                "logs and hashes, keep canonical required_command_ids, and use one target_run_id across required rows."
            ),
        }
    if gate == "target_artifact_run_id_binding":
        return {
            "gate": gate,
            "blocking_stage": "target_artifact_identity",
            "target_action_id": "m12_test_commands.sh",
            "required_artifact_path": TARGET_ARTIFACT_PATHS["command_log_manifest"],
            "operator_action": (
                "Run all target-producing commands through m12_test_commands.sh so run_m12_logged_command.py exports "
                "M12_TARGET_RUN_ID to child processes, then rebuild stale preflight/SWE/export/Ray/load/soak artifacts "
                "until every component artifact records the same target_run_id as the required command logs."
            ),
        }
    return {
        "gate": gate,
        "blocking_stage": "unknown",
        "target_action_id": None,
        "required_artifact_path": None,
        "operator_action": "Unknown M12 gate; update final-report gate remediation mapping before relying on this report.",
    }


def _missing_gate_remediations(missing_gates: list[str]) -> list[dict[str, Any]]:
    return [_gate_remediation(gate) for gate in missing_gates]


def summarize_m12_final_report_remediations(report: dict[str, Any]) -> dict[str, Any]:
    remediations = [
        remediation
        for remediation in report.get("missing_gate_remediations") or []
        if isinstance(remediation, dict)
    ]
    by_target_action: dict[str, dict[str, Any]] = {}
    for remediation in remediations:
        action = str(remediation.get("target_action_id") or remediation.get("blocking_stage") or "unknown")
        item = by_target_action.setdefault(
            action,
            {
                "target_action_id": action,
                "blocking_stages": [],
                "gates": [],
                "required_artifact_paths": [],
                "operator_actions": [],
            },
        )
        stage = str(remediation.get("blocking_stage") or "")
        gate = str(remediation.get("gate") or "")
        artifact_path = remediation.get("required_artifact_path")
        operator_action = str(remediation.get("operator_action") or "")
        if stage and stage not in item["blocking_stages"]:
            item["blocking_stages"].append(stage)
        if gate and gate not in item["gates"]:
            item["gates"].append(gate)
        if artifact_path and artifact_path not in item["required_artifact_paths"]:
            item["required_artifact_paths"].append(artifact_path)
        if operator_action and operator_action not in item["operator_actions"]:
            item["operator_actions"].append(operator_action)
    missing_gates = [str(gate) for gate in report.get("missing_gates") or []]
    remediation_gates = [str(remediation.get("gate") or "") for remediation in remediations]
    return {
        "summary_id": "bb_zyphra_rl_phase1_m12_final_report_remediation_summary_v1",
        "claim_boundary": "final_report_remediation_summary_not_scorecard_update",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "m12_score_eligible": report.get("m12_score_eligible") is True,
        "missing_gate_count": len(missing_gates),
        "remediation_count": len(remediations),
        "remediation_gates_match_missing_gates": remediation_gates == missing_gates,
        "next_target_actions": list(by_target_action.values()),
        "operator_next_step": (
            "If m12_score_eligible is false, run or repair the listed target actions and rebuild the final report. "
            "This summary never updates the scorecard."
        ),
    }


def validate_m12_final_report_remediation_summary(summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if summary.get("summary_id") != "bb_zyphra_rl_phase1_m12_final_report_remediation_summary_v1":
        errors.append("summary_id must be bb_zyphra_rl_phase1_m12_final_report_remediation_summary_v1")
    if summary.get("claim_boundary") != "final_report_remediation_summary_not_scorecard_update":
        errors.append("claim_boundary must remain final_report_remediation_summary_not_scorecard_update")
    if summary.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if summary.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if not isinstance(summary.get("m12_score_eligible"), bool):
        errors.append("m12_score_eligible must be boolean")
    if not isinstance(summary.get("missing_gate_count"), int) or summary.get("missing_gate_count", -1) < 0:
        errors.append("missing_gate_count must be a non-negative integer")
    if not isinstance(summary.get("remediation_count"), int) or summary.get("remediation_count", -1) < 0:
        errors.append("remediation_count must be a non-negative integer")
    if not isinstance(summary.get("remediation_gates_match_missing_gates"), bool):
        errors.append("remediation_gates_match_missing_gates must be boolean")
    actions = summary.get("next_target_actions")
    if not isinstance(actions, list):
        errors.append("next_target_actions must be a list")
        actions = []
    gate_rows: list[str] = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            errors.append(f"next_target_actions row {index} must be an object")
            continue
        if not str(action.get("target_action_id") or "").strip():
            errors.append(f"next_target_actions row {index} requires target_action_id")
        for field in ["blocking_stages", "gates", "required_artifact_paths", "operator_actions"]:
            if not isinstance(action.get(field), list):
                errors.append(f"next_target_actions row {index} {field} must be a list")
        gates = action.get("gates") if isinstance(action.get("gates"), list) else []
        for gate in gates:
            gate_name = str(gate)
            if gate_name not in M12_FINAL_REPORT_GATE_NAMES:
                errors.append(f"next_target_actions row {index} has unknown gate: {gate_name}")
            gate_rows.append(gate_name)
        artifact_paths = (
            action.get("required_artifact_paths")
            if isinstance(action.get("required_artifact_paths"), list)
            else []
        )
        for artifact_path in artifact_paths:
            if str(artifact_path) not in TARGET_ARTIFACT_PATHS.values():
                errors.append(f"next_target_actions row {index} has non-target artifact path: {artifact_path}")
        operator_actions = action.get("operator_actions") if isinstance(action.get("operator_actions"), list) else []
        for operator_action in operator_actions:
            if not str(operator_action or "").strip():
                errors.append(f"next_target_actions row {index} has empty operator_action")
    if len(gate_rows) != len(set(gate_rows)):
        errors.append("next_target_actions gates must be unique across target actions")
    if isinstance(summary.get("remediation_count"), int) and summary["remediation_count"] != len(gate_rows):
        errors.append("remediation_count must match next_target_actions gate count")
    if summary.get("remediation_gates_match_missing_gates") is True:
        if isinstance(summary.get("missing_gate_count"), int) and summary["missing_gate_count"] != len(gate_rows):
            errors.append("missing_gate_count must match next_target_actions gate count")
        if summary.get("remediation_count") != summary.get("missing_gate_count"):
            errors.append("remediation_count must match missing_gate_count when gates match")
    if summary.get("m12_score_eligible") is True:
        if summary.get("missing_gate_count") != 0:
            errors.append("eligible remediation summary requires missing_gate_count=0")
        if summary.get("remediation_count") != 0:
            errors.append("eligible remediation summary requires remediation_count=0")
        if gate_rows:
            errors.append("eligible remediation summary requires no next_target_actions gates")
        if summary.get("remediation_gates_match_missing_gates") is not True:
            errors.append("eligible remediation summary requires remediation_gates_match_missing_gates=true")
    if not str(summary.get("operator_next_step") or "").strip():
        errors.append("operator_next_step must be non-empty")
    return errors


def _preflight_validation_errors(preflight: dict[str, Any]) -> list[str]:
    if _read_error(preflight):
        return [f"preflight report unreadable: {_read_error(preflight)}"]
    from breadboard.rl.m12.preflight import validate_m12_preflight_report

    return validate_m12_preflight_report(preflight)


def _load_ladder_validation_errors(load_ladder: dict[str, Any]) -> list[str]:
    if load_ladder.get("present") is not True:
        return []
    if _read_error(load_ladder):
        return [f"load ladder report unreadable: {_read_error(load_ladder)}"]
    from breadboard.rl.m12.load_soak import validate_m12_load_ladder_report

    return validate_m12_load_ladder_report(load_ladder)


def _soak_validation_errors(soak: dict[str, Any]) -> list[str]:
    if soak.get("present") is not True:
        return []
    if _read_error(soak):
        return [f"soak report unreadable: {_read_error(soak)}"]
    from breadboard.rl.m12.load_soak import validate_m12_soak_report

    return validate_m12_soak_report(soak)


def _swe_summary_validation_errors(summary: dict[str, Any]) -> list[str]:
    if _read_error(summary):
        return [f"SWE run summary unreadable: {_read_error(summary)}"]
    errors: list[str] = []
    for key in ["run_id", "package_id", "package_hash", "source_claim"]:
        if not str(summary.get(key) or "").strip():
            errors.append(f"SWE run summary requires non-empty {key}")
    rows = summary.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("SWE run summary requires non-empty rows list")
        return errors
    task_ids: set[str] = set()
    metric_keys = {"export_ms", "reset_ms", "step_ms", "total_ms", "verify_ms"}
    allowed_statuses = {"accepted", "rejected", "quarantined"}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"SWE row {index} must be an object")
            continue
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            errors.append(f"SWE row {index} requires task_id")
        elif task_id in task_ids:
            errors.append(f"SWE row task_id must be unique: {task_id}")
        task_ids.add(task_id)
        status = str(row.get("row_status") or "")
        if status not in allowed_statuses:
            errors.append(f"SWE row {task_id or index} has invalid row_status")
        if not isinstance(row.get("exportable_debug"), bool):
            errors.append(f"SWE row {task_id or index} requires boolean exportable_debug")
        if not isinstance(row.get("trainable"), bool):
            errors.append(f"SWE row {task_id or index} requires boolean trainable")
        if not str(row.get("projection_id") or "").strip():
            errors.append(f"SWE row {task_id or index} requires projection_id")
        if not isinstance(row.get("blocked_reasons"), list):
            errors.append(f"SWE row {task_id or index} requires blocked_reasons list")
        if not isinstance(row.get("findings"), list):
            errors.append(f"SWE row {task_id or index} requires findings list")
        try:
            float(row.get("reward"))
        except (TypeError, ValueError):
            errors.append(f"SWE row {task_id or index} requires numeric reward")
        metrics = row.get("metrics_ms")
        if not isinstance(metrics, dict) or set(metrics) != metric_keys:
            errors.append(f"SWE row {task_id or index} requires exact metrics_ms keys")
        else:
            for metric_key, metric_value in metrics.items():
                try:
                    if float(metric_value) < 0:
                        errors.append(f"SWE row {task_id or index} metric {metric_key} must be non-negative")
                except (TypeError, ValueError):
                    errors.append(f"SWE row {task_id or index} metric {metric_key} must be numeric")
        if status == "accepted":
            if row.get("hardening_status") != "passed":
                errors.append(f"accepted SWE row {task_id or index} requires hardening_status=passed")
            if row.get("replay_status") != "passed":
                errors.append(f"accepted SWE row {task_id or index} requires replay_status=passed")
            if row.get("exportable_debug") is not True:
                errors.append(f"accepted SWE row {task_id or index} requires exportable_debug=true")
        if status == "quarantined" and row.get("hardening_status") != "quarantined":
            errors.append(f"quarantined SWE row {task_id or index} requires hardening_status=quarantined")
    metrics_summary = summary.get("metrics_summary")
    if not isinstance(metrics_summary, dict):
        errors.append("SWE run summary requires metrics_summary object")
    else:
        for metric_key in metric_keys:
            metric_summary = metrics_summary.get(metric_key)
            if not isinstance(metric_summary, dict):
                errors.append(f"SWE metrics_summary requires {metric_key}")
                continue
            for stat_key in ["p50", "p95"]:
                try:
                    if float(metric_summary.get(stat_key)) < 0:
                        errors.append(f"SWE metrics_summary {metric_key}.{stat_key} must be non-negative")
                except (TypeError, ValueError):
                    errors.append(f"SWE metrics_summary {metric_key}.{stat_key} must be numeric")
    qc_report = summary.get("qc_report")
    if not isinstance(qc_report, dict):
        errors.append("SWE run summary requires qc_report object")
    elif "Controlled SWE toy slice only" not in str(qc_report.get("operator_notes") or ""):
        errors.append("SWE qc_report must preserve controlled-slice claim boundary note")
    return errors


def _verl_smoke_validation_errors(smoke: dict[str, Any]) -> list[str]:
    if _read_error(smoke):
        return [f"VeRL smoke report unreadable: {_read_error(smoke)}"]
    errors: list[str] = []
    row_count = _int_or(smoke.get("row_count"), -1)
    trainable_count = _int_or(smoke.get("trainable_candidate_count"), -1)
    if row_count < 1:
        errors.append("VeRL smoke report requires positive row_count")
    if trainable_count < 0 or trainable_count > max(row_count, 0):
        errors.append("VeRL smoke report trainable_candidate_count must be between 0 and row_count")
    if not isinstance(smoke.get("tensorizable"), bool):
        errors.append("VeRL smoke report requires boolean tensorizable")
    if "not DataProto or trainer execution" not in str(smoke.get("compatibility_target") or ""):
        errors.append("VeRL smoke report must preserve non-trainer compatibility boundary")
    if not str(smoke.get("projection_manifest_id") or "").strip():
        errors.append("VeRL smoke report requires projection_manifest_id")
    errors_by_format = smoke.get("errors")
    if not isinstance(errors_by_format, dict):
        errors.append("VeRL smoke report requires errors object")
    formats = smoke.get("formats")
    if not isinstance(formats, dict):
        errors.append("VeRL smoke report requires formats object")
        return errors
    for format_name in ["jsonl", "parquet"]:
        report = formats.get(format_name)
        if not isinstance(report, dict):
            errors.append(f"VeRL smoke report missing {format_name} format report")
            continue
        if _int_or(report.get("row_count"), -1) != row_count:
            errors.append(f"VeRL {format_name} row_count must match top-level row_count")
        if _int_or(report.get("trainable_candidate_count"), -1) != trainable_count:
            errors.append(f"VeRL {format_name} trainable_candidate_count must match top-level count")
        if not isinstance(report.get("tensorizable"), bool):
            errors.append(f"VeRL {format_name} requires boolean tensorizable")
        report_errors = report.get("errors")
        if not isinstance(report_errors, list):
            errors.append(f"VeRL {format_name} errors must be a list")
        elif report.get("tensorizable") is True and report_errors:
            errors.append(f"VeRL {format_name} tensorizable=true requires empty errors")
    return errors


def _ray_probe_validation_errors(ray_probe: dict[str, Any]) -> list[str]:
    if _read_error(ray_probe):
        return [f"Ray probe report unreadable: {_read_error(ray_probe)}"]
    errors: list[str] = []
    if ray_probe.get("claim_boundary") != "local_ray_worker_probe_not_production_scale":
        errors.append("Ray probe claim_boundary must remain local_ray_worker_probe_not_production_scale")
    rows = ray_probe.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("Ray probe requires non-empty rows list")
        rows = []
    row_count = _int_or(ray_probe.get("row_count"), -1)
    if row_count != len(rows):
        errors.append("Ray probe row_count must equal rows length")
    worker_count = _int_or(ray_probe.get("worker_count"), -1)
    if worker_count < 1:
        errors.append("Ray probe worker_count must be positive")
    if not isinstance(ray_probe.get("ray_local_mode"), bool):
        errors.append("Ray probe requires boolean ray_local_mode")
    task_ids: set[str] = set()
    worker_ids: set[str] = set()
    metric_keys = {"reset_ms", "step_ms", "total_ms", "verify_ms"}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"Ray probe row {index} must be an object")
            continue
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            errors.append(f"Ray probe row {index} requires task_id")
        elif task_id in task_ids:
            errors.append(f"Ray probe task_id must be unique: {task_id}")
        task_ids.add(task_id)
        worker_id = str(row.get("worker_id") or "").strip()
        if not worker_id:
            errors.append(f"Ray probe row {task_id or index} requires worker_id")
        worker_ids.add(worker_id)
        if _int_or(row.get("event_count"), -1) < 1:
            errors.append(f"Ray probe row {task_id or index} requires positive event_count")
        if _int_or(row.get("run_count"), -1) < 1:
            errors.append(f"Ray probe row {task_id or index} requires positive run_count")
        try:
            float(row.get("reward"))
        except (TypeError, ValueError):
            errors.append(f"Ray probe row {task_id or index} requires numeric reward")
        metrics = row.get("metrics_ms")
        if not isinstance(metrics, dict) or set(metrics) != metric_keys:
            errors.append(f"Ray probe row {task_id or index} requires exact metrics_ms keys")
        else:
            for metric_key, metric_value in metrics.items():
                try:
                    if float(metric_value) < 0:
                        errors.append(f"Ray probe row {task_id or index} metric {metric_key} must be non-negative")
                except (TypeError, ValueError):
                    errors.append(f"Ray probe row {task_id or index} metric {metric_key} must be numeric")
    if len(worker_ids) > worker_count:
        errors.append("Ray probe observed worker IDs cannot exceed worker_count")
    return errors


def _warm_vs_cold_validation_errors(report: dict[str, Any]) -> list[str]:
    if _read_error(report):
        return [f"warm-vs-cold report unreadable: {_read_error(report)}"]
    errors: list[str] = []
    if report.get("claim_boundary") != "local_ray_warm_pool_probe_not_production_scale":
        errors.append("warm-vs-cold claim_boundary must remain local_ray_warm_pool_probe_not_production_scale")
    warm = report.get("warm")
    cold = report.get("cold")
    if not isinstance(warm, dict) or not isinstance(cold, dict):
        errors.append("warm-vs-cold report requires warm and cold objects")
        return errors
    if set(warm.keys()) != set(cold.keys()):
        errors.append("warm-vs-cold warm and cold metric keys must match")
    if "total_ms" not in warm or "total_ms" not in cold:
        errors.append("warm-vs-cold requires total_ms metric")
    for group_name, group in [("warm", warm), ("cold", cold)]:
        for metric_name, summary in group.items():
            if not isinstance(summary, dict):
                errors.append(f"warm-vs-cold {group_name}.{metric_name} must be an object")
                continue
            for stat_key in ["count", "p50", "p95"]:
                try:
                    value = float(summary.get(stat_key))
                except (TypeError, ValueError):
                    errors.append(f"warm-vs-cold {group_name}.{metric_name}.{stat_key} must be numeric")
                    continue
                if value < 0:
                    errors.append(f"warm-vs-cold {group_name}.{metric_name}.{stat_key} must be non-negative")
            try:
                if float(summary.get("p95")) < float(summary.get("p50")):
                    errors.append(f"warm-vs-cold {group_name}.{metric_name}.p95 must be >= p50")
            except (TypeError, ValueError):
                pass
    return errors


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"accepted": 0, "rejected": 0, "quarantined": 0, "other": 0}
    for row in rows:
        status = str(row.get("row_status") or "")
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    return counts


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_zero(value: Any) -> int:
    return _int_or(value, 0)


def _command_log_entries(command_logs: dict[str, Any]) -> list[dict[str, Any]]:
    entries = command_logs.get("commands")
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)]


def _command_log_ids(entries: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("command_id") or "") for item in entries if item.get("command_id")}


def _command_log_entry_has_hash(entry: dict[str, Any]) -> bool:
    return str(entry.get("sha256") or "").startswith("sha256:")


def _command_log_entry_archived(entry: dict[str, Any]) -> bool:
    return bool(entry.get("log_path")) and _command_log_entry_has_hash(entry)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _resolve_command_log_path(manifest_path: Path | None, raw_log_path: Any) -> Path | None:
    if not raw_log_path:
        return None
    path = Path(str(raw_log_path))
    if path.is_absolute():
        return path
    if manifest_path is not None:
        manifest_relative = manifest_path.parent / path
        if manifest_relative.exists():
            return manifest_relative
    return path


def _command_log_entry_hash_verified(entry: dict[str, Any], manifest_path: Path | None) -> bool:
    if not _command_log_entry_archived(entry):
        return False
    path = _resolve_command_log_path(manifest_path, entry.get("log_path"))
    if path is None or not path.is_file():
        return False
    return _sha256_file(path) == entry.get("sha256")


def _required_command_target_run_ids(entries: list[dict[str, Any]], required_command_ids: set[str]) -> set[str]:
    return {
        str(entry.get("target_run_id") or "")
        for entry in entries
        if str(entry.get("command_id") or "") in required_command_ids
    }


def _required_command_text_mismatches(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    entries_by_id = {str(entry.get("command_id") or ""): entry for entry in entries}
    mismatches: list[dict[str, str]] = []
    for command_id, expected in REQUIRED_COMMAND_LOG_COMMANDS.items():
        entry = entries_by_id.get(command_id)
        if entry is None:
            continue
        observed = str(entry.get("command") or "")
        if observed != expected:
            mismatches.append({"command_id": command_id, "expected": expected, "observed": observed})
    return mismatches


def _target_artifact_run_ids(
    *,
    preflight: dict[str, Any],
    swe_summary: dict[str, Any],
    verl_smoke: dict[str, Any],
    ray_probe: dict[str, Any],
    warm_vs_cold: dict[str, Any],
    load_ladder: dict[str, Any],
    soak: dict[str, Any],
) -> dict[str, str | None]:
    return {
        "preflight_report": preflight.get("target_run_id"),
        "swe_run_summary": swe_summary.get("target_run_id"),
        "verl_smoke_report": verl_smoke.get("target_run_id"),
        "ray_probe_report": ray_probe.get("target_run_id"),
        "warm_vs_cold_report": warm_vs_cold.get("target_run_id"),
        "load_ladder_report": load_ladder.get("target_run_id"),
        "soak_report": soak.get("target_run_id"),
    }


def _target_artifact_run_ids_from_final_report(report: dict[str, Any]) -> dict[str, str | None]:
    return _target_artifact_run_ids(
        preflight=report.get("preflight", {}) if isinstance(report.get("preflight"), dict) else {},
        swe_summary=report.get("swe_probe", {}) if isinstance(report.get("swe_probe"), dict) else {},
        verl_smoke=report.get("verl_export", {}) if isinstance(report.get("verl_export"), dict) else {},
        ray_probe=report.get("ray_probe", {}) if isinstance(report.get("ray_probe"), dict) else {},
        warm_vs_cold=report.get("warm_vs_cold", {}) if isinstance(report.get("warm_vs_cold"), dict) else {},
        load_ladder=report.get("load_ladder", {}) if isinstance(report.get("load_ladder"), dict) else {},
        soak=report.get("soak", {}) if isinstance(report.get("soak"), dict) else {},
    )


def _target_run_identity_validation_errors(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    identity = report.get("target_run_identity")
    if not isinstance(identity, dict):
        return ["target_run_identity must be an object"]
    expected_artifact_run_ids = _target_artifact_run_ids_from_final_report(report)
    if identity.get("artifact_target_run_ids") != expected_artifact_run_ids:
        errors.append("target_run_identity artifact_target_run_ids must match embedded artifact sections")
    missing = sorted(name for name, target_run_id in expected_artifact_run_ids.items() if not target_run_id)
    if identity.get("missing_artifact_target_run_ids") != missing:
        errors.append("target_run_identity missing_artifact_target_run_ids is stale")
    command_target_run_id = identity.get("single_command_log_target_run_id")
    mismatched = {
        name: {"expected": command_target_run_id, "observed": target_run_id}
        for name, target_run_id in expected_artifact_run_ids.items()
        if target_run_id and command_target_run_id and target_run_id != command_target_run_id
    }
    if identity.get("mismatched_artifact_target_run_ids") != mismatched:
        errors.append("target_run_identity mismatched_artifact_target_run_ids is stale")
    expected_match = bool(command_target_run_id) and not missing and not mismatched
    if identity.get("run_ids_match_command_logs") is not expected_match:
        errors.append("target_run_identity run_ids_match_command_logs is stale")
    return errors


def _object_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _missing_gate_names_from_final_report(report: dict[str, Any]) -> list[str]:
    """Recompute final-report gates from the embedded final-report sections."""

    artifact_paths = _object_or_empty(report.get("artifact_paths"))
    archive_verify = _object_or_empty(report.get("archive_verify"))
    preflight = _object_or_empty(report.get("preflight"))
    swe_probe = _object_or_empty(report.get("swe_probe"))
    verl_export = _object_or_empty(report.get("verl_export"))
    ray_probe = _object_or_empty(report.get("ray_probe"))
    warm_vs_cold = _object_or_empty(report.get("warm_vs_cold"))
    load_ladder = _object_or_empty(report.get("load_ladder"))
    soak = _object_or_empty(report.get("soak"))
    command_logs = _object_or_empty(report.get("command_logs"))

    swe_row_count = _int_or_zero(swe_probe.get("row_count"))
    swe_row_counts = _object_or_empty(swe_probe.get("row_status_counts"))
    load_level_items = [
        item for item in load_ladder.get("concurrency_levels", []) if isinstance(item, dict)
    ]
    load_levels = {
        _int_or(item.get("target_sessions"), -1): str(item.get("status") or "")
        for item in load_level_items
    }
    resource_skipped_levels = {
        _int_or(item.get("target_sessions"), -1)
        for item in load_ladder.get("resource_skips", [])
        if isinstance(item, dict)
    }

    command_entries = _command_log_entries(command_logs)
    required_command_ids = set(REQUIRED_COMMAND_LOG_IDS)
    archived_command_ids = _command_log_ids(
        [entry for entry in command_entries if _command_log_entry_archived(entry)]
    )
    hash_verified_command_ids = {
        str(command_id)
        for command_id in command_logs.get("hash_verified_command_ids", [])
        if str(command_id)
    }
    passed_command_ids = _command_log_ids(
        [entry for entry in command_entries if str(entry.get("status") or "") == "passed"]
    )
    missing_command_log_ids = sorted(required_command_ids - archived_command_ids)
    required_target_run_ids = _required_command_target_run_ids(command_entries, required_command_ids)
    required_target_run_ids_without_empty = {item for item in required_target_run_ids if item}
    single_target_run_id = (
        next(iter(required_target_run_ids_without_empty))
        if len(required_target_run_ids_without_empty) == 1 and "" not in required_target_run_ids
        else None
    )
    required_command_text_mismatches = _required_command_text_mismatches(command_entries)
    target_artifact_run_ids = _target_artifact_run_ids_from_final_report(report)
    missing_target_artifact_run_ids = sorted(
        name for name, target_run_id in target_artifact_run_ids.items() if not target_run_id
    )
    mismatched_target_artifact_run_ids = {
        name: {"expected": single_target_run_id, "observed": target_run_id}
        for name, target_run_id in target_artifact_run_ids.items()
        if target_run_id and single_target_run_id and target_run_id != single_target_run_id
    }

    gates = {
        "artifact_paths_match_target_defaults": _artifact_paths_match_target_defaults(artifact_paths),
        "archive_verify_report_present": archive_verify.get("present") is True,
        "archive_verify_report_readable": archive_verify.get("present") is True and _read_error(archive_verify) is None,
        "archive_verify_report_valid": archive_verify.get("present") is True
        and not _archive_verify_validation_errors(archive_verify),
        "archive_verify_status_passed": archive_verify.get("status") == "passed",
        "preflight_report_readable": _read_error(preflight) is None,
        "preflight_report_valid": preflight.get("validation_errors") == [],
        "swe_run_summary_readable": _read_error(swe_probe) is None,
        "swe_run_summary_valid": swe_probe.get("validation_errors") == [],
        "verl_smoke_report_readable": _read_error(verl_export) is None,
        "verl_smoke_report_valid": verl_export.get("validation_errors") == [],
        "ray_probe_report_readable": _read_error(ray_probe) is None,
        "ray_probe_report_valid": ray_probe.get("validation_errors") == [],
        "warm_vs_cold_report_readable": _read_error(warm_vs_cold) is None,
        "warm_vs_cold_report_valid": warm_vs_cold.get("validation_errors") == [],
        "preflight_passed": preflight.get("status") == "preflight_passed",
        "preflight_runtime_fingerprint_present": _runtime_fingerprint_valid(preflight),
        "target_hardware": (
            preflight.get("gpu", {}).get("required_accelerator_count") == 8
            and preflight.get("gpu", {}).get("required_accelerator_family") == "MI300X"
            and preflight.get("gpu", {}).get("mi300x_product_evidence") is True
            and _int_or_zero(preflight.get("gpu", {}).get("torch_probe", {}).get("device_count")) >= 8
        ),
        "verl_available": preflight.get("python_modules", {}).get("verl", {}).get("available") is True,
        "ray_available": preflight.get("python_modules", {}).get("ray", {}).get("available") is True,
        "inference_engine_available": (
            preflight.get("inference_engine_feasibility", {}).get("decision") == "available"
            and (
                preflight.get("inference_engine_feasibility", {}).get("vllm_available") is True
                or preflight.get("inference_engine_feasibility", {}).get("sglang_available") is True
            )
        ),
        "container_runtime_available": any(
            value is True for value in (preflight.get("container_runtimes") or {}).values()
        ),
        "filesystem_cas_smoke_passed": preflight.get("filesystem_cas_smoke", {}).get("status") == "passed",
        "swe_probe_ran_10_rows": swe_row_count >= 10,
        "swe_probe_has_accepted_rows": _int_or_zero(swe_row_counts.get("accepted")) > 0,
        "swe_probe_no_unknown_status": _int_or(swe_row_counts.get("other"), 1) == 0,
        "verl_jsonl_tensorizable": verl_export.get("formats", {}).get("jsonl", {}).get("tensorizable") is True,
        "verl_parquet_tensorizable": verl_export.get("formats", {}).get("parquet", {}).get("tensorizable") is True,
        "verl_row_count_matches_swe": _int_or(verl_export.get("row_count"), -1) == swe_row_count,
        "ray_probe_ran_10_rows": _int_or_zero(ray_probe.get("row_count")) >= 10,
        "ray_probe_has_workers": _int_or_zero(ray_probe.get("worker_count")) >= 2,
        "ray_probe_distributed": ray_probe.get("ray_local_mode") is False,
        "warm_vs_cold_has_total_ms": (
            "total_ms" in _object_or_empty(warm_vs_cold.get("warm"))
            and "total_ms" in _object_or_empty(warm_vs_cold.get("cold"))
        ),
        "load_ladder_report_present": load_ladder.get("present") is True,
        "load_ladder_report_valid": load_ladder.get("present") is not True or load_ladder.get("validation_errors") == [],
        "load_ladder_required_levels_passed": all(
            load_levels.get(level) == "passed" for level in REQUIRED_LOAD_LEVELS
        ),
        "load_ladder_100_attempted_or_skipped": all(
            load_levels.get(level) == "passed" or level in resource_skipped_levels for level in OPTIONAL_LOAD_LEVELS
        ),
        "load_ladder_policy_integrity": load_ladder.get("policy_version_integrity") is True,
        "load_ladder_no_queue_corruption": load_ladder.get("queue_backpressure_integrity") is True,
        "load_ladder_distributed": all(
            item.get("status") == "resource_skipped" or item.get("ray_local_mode") is False
            for item in load_level_items
        )
        and bool(load_level_items),
        "soak_report_present": soak.get("present") is True,
        "soak_report_valid": soak.get("present") is not True or soak.get("validation_errors") == [],
        "soak_status_passed": soak.get("status") == "passed",
        "soak_duration_at_least_2h": _int_or_zero(soak.get("duration_seconds")) >= MIN_SOAK_SECONDS,
        "soak_no_runtime_failures": _int_or(soak.get("runtime_failure_count"), 1) == 0,
        "soak_distributed": soak.get("ray_local_mode") is False,
        "command_log_manifest_present": command_logs.get("present") is True,
        "command_log_manifest_id_valid": command_logs.get("manifest_id") == COMMAND_LOG_MANIFEST_ID,
        "command_log_manifest_valid": (
            command_logs.get("present") is True
            and command_logs.get("manifest_validation_errors") == []
        ),
        "command_log_required_ids_canonical": (
            command_logs.get("manifest_required_command_ids") == list(REQUIRED_COMMAND_LOG_IDS)
        ),
        "command_log_manifest_complete": not missing_command_log_ids,
        "command_log_hashes_present": all(
            _command_log_entry_has_hash(entry)
            for entry in command_entries
            if str(entry.get("command_id") or "") in required_command_ids
        )
        and not missing_command_log_ids,
        "command_log_hashes_verified": all(command_id in hash_verified_command_ids for command_id in required_command_ids),
        "command_log_commands_passed": all(command_id in passed_command_ids for command_id in required_command_ids),
        "command_log_required_logs_archived_summary": command_logs.get("all_required_logs_archived") is True,
        "command_log_required_commands_passed_summary": command_logs.get("all_required_commands_passed") is True,
        "command_log_single_target_run_id": not missing_command_log_ids and single_target_run_id is not None,
        "command_log_expected_commands_match": not missing_command_log_ids and not required_command_text_mismatches,
        "target_artifact_run_id_binding": (
            bool(single_target_run_id)
            and not missing_target_artifact_run_ids
            and not mismatched_target_artifact_run_ids
        ),
    }
    return [name for name, passed in gates.items() if not passed]


def _artifact_path_policy_validation_errors(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    policy = report.get("artifact_path_policy")
    if not isinstance(policy, dict) or not policy:
        return errors
    artifact_paths = _object_or_empty(report.get("artifact_paths"))
    if policy.get("policy_id") != "m12_target_artifact_paths_v1":
        errors.append("artifact_path_policy.policy_id must be m12_target_artifact_paths_v1")
    if policy.get("required_target_paths") != TARGET_ARTIFACT_PATHS:
        errors.append("artifact_path_policy.required_target_paths must match canonical M12 target artifact paths")
    expected_paths_match = _artifact_paths_match_target_defaults(artifact_paths)
    if policy.get("paths_match_target_defaults") is not expected_paths_match:
        errors.append("artifact_path_policy.paths_match_target_defaults is stale")
    if not str(policy.get("reason") or "").strip():
        errors.append("artifact_path_policy.reason must be non-empty")
    return errors


def _artifact_section_paths_from_final_report(report: dict[str, Any]) -> dict[str, str | None]:
    section_by_artifact = {
        "archive_verify_report": "archive_verify",
        "preflight_report": "preflight",
        "swe_run_summary": "swe_probe",
        "verl_smoke_report": "verl_export",
        "ray_probe_report": "ray_probe",
        "warm_vs_cold_report": "warm_vs_cold",
        "load_ladder_report": "load_ladder",
        "soak_report": "soak",
        "command_log_manifest": "command_logs",
    }
    paths: dict[str, str | None] = {}
    for artifact_key, section_key in section_by_artifact.items():
        section = report.get(section_key)
        if not isinstance(section, dict):
            paths[artifact_key] = None
            continue
        path = section.get("path")
        paths[artifact_key] = str(path) if path is not None else None
    return paths


def _artifact_section_path_validation_errors(report: dict[str, Any]) -> list[str]:
    artifact_paths = report.get("artifact_paths")
    if not isinstance(artifact_paths, dict):
        return []
    section_paths = _artifact_section_paths_from_final_report(report)
    errors: list[str] = []
    for artifact_key in TARGET_ARTIFACT_PATHS:
        artifact_path = artifact_paths.get(artifact_key)
        expected = str(artifact_path) if artifact_path is not None else None
        if section_paths.get(artifact_key) != expected:
            errors.append(f"artifact_paths.{artifact_key} must match embedded section path")
    return errors


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _command_log_summary_validation_errors(report: dict[str, Any]) -> list[str]:
    command_logs = report.get("command_logs")
    if not isinstance(command_logs, dict) or not command_logs:
        return []

    command_entries = _command_log_entries(command_logs)
    required_command_ids = set(REQUIRED_COMMAND_LOG_IDS)
    archived_command_ids = sorted(
        _command_log_ids([entry for entry in command_entries if _command_log_entry_archived(entry)])
    )
    passed_command_ids = _command_log_ids(
        [entry for entry in command_entries if str(entry.get("status") or "") == "passed"]
    )
    expected_missing_command_ids = sorted(required_command_ids - set(archived_command_ids))
    required_target_run_ids = _required_command_target_run_ids(command_entries, required_command_ids)
    expected_target_run_ids = sorted(item for item in required_target_run_ids if item)
    expected_single_target_run_id = (
        expected_target_run_ids[0]
        if len(expected_target_run_ids) == 1 and "" not in required_target_run_ids
        else None
    )
    expected_command_text_mismatches = _required_command_text_mismatches(command_entries)
    observed_hash_verified_ids = set(_string_list(command_logs.get("hash_verified_command_ids")))
    all_command_ids = _command_log_ids(command_entries)

    errors: list[str] = []
    if command_logs.get("required_command_ids") != list(REQUIRED_COMMAND_LOG_IDS):
        errors.append("command_logs.required_command_ids must match canonical M12 required command IDs")
    if sorted(_string_list(command_logs.get("archived_command_ids"))) != archived_command_ids:
        errors.append("command_logs.archived_command_ids is stale")
    if sorted(_string_list(command_logs.get("missing_command_log_ids"))) != expected_missing_command_ids:
        errors.append("command_logs.missing_command_log_ids is stale")
    if sorted(_string_list(command_logs.get("target_run_ids"))) != expected_target_run_ids:
        errors.append("command_logs.target_run_ids is stale")
    if command_logs.get("single_target_run_id") != expected_single_target_run_id:
        errors.append("command_logs.single_target_run_id is stale")
    if command_logs.get("command_text_mismatches") != expected_command_text_mismatches:
        errors.append("command_logs.command_text_mismatches is stale")
    if command_logs.get("command_count") != len(command_entries):
        errors.append("command_logs.command_count is stale")
    if observed_hash_verified_ids - all_command_ids:
        errors.append("command_logs.hash_verified_command_ids contains unknown command IDs")
    if observed_hash_verified_ids - set(archived_command_ids):
        errors.append("command_logs.hash_verified_command_ids contains unarchived command IDs")

    readable_manifest = command_logs.get("present") is True and not command_logs.get("read_error")
    if readable_manifest:
        expected_all_required_logs_archived = not expected_missing_command_ids
        expected_all_required_commands_passed = not expected_missing_command_ids and all(
            command_id in passed_command_ids for command_id in required_command_ids
        )
        if command_logs.get("all_required_logs_archived") is not expected_all_required_logs_archived:
            errors.append("command_logs.all_required_logs_archived is stale")
        if command_logs.get("all_required_commands_passed") is not expected_all_required_commands_passed:
            errors.append("command_logs.all_required_commands_passed is stale")
    else:
        if command_logs.get("all_required_logs_archived") is True:
            errors.append("command_logs.all_required_logs_archived cannot be true without a readable manifest")
        if command_logs.get("all_required_commands_passed") is True:
            errors.append("command_logs.all_required_commands_passed cannot be true without a readable manifest")
    return errors


def _command_log_required_ids_canonical(command_logs: dict[str, Any]) -> bool:
    return command_logs.get("required_command_ids") == list(REQUIRED_COMMAND_LOG_IDS)


def _stable_runtime_fingerprint_sha256(fingerprint: dict[str, Any]) -> str | None:
    payload = dict(fingerprint)
    observed = payload.pop("sha256", None)
    if not isinstance(observed, str) or not observed.startswith("sha256:"):
        return None
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _dict_has_exact_keys(raw: Any, expected_keys: set[str]) -> bool:
    return isinstance(raw, dict) and {str(key) for key in raw.keys()} == expected_keys


def _env_value_is_path_like(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("~"):
        return True
    return Path(stripped).is_absolute() or PureWindowsPath(stripped).is_absolute()


def _runtime_fingerprint_valid(preflight: dict[str, Any]) -> bool:
    fingerprint = preflight.get("runtime_fingerprint")
    if not _dict_has_exact_keys(fingerprint, RUNTIME_FINGERPRINT_KEYS):
        return False
    if fingerprint.get("fingerprint_id") != RUNTIME_FINGERPRINT_ID:
        return False
    if _stable_runtime_fingerprint_sha256(fingerprint) != fingerprint.get("sha256"):
        return False
    if not _dict_has_exact_keys(fingerprint.get("platform"), RUNTIME_FINGERPRINT_PLATFORM_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("tool_presence"), RUNTIME_FINGERPRINT_TOOL_PRESENCE_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("python_modules"), RUNTIME_FINGERPRINT_PYTHON_MODULE_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("gpu_summary"), RUNTIME_FINGERPRINT_GPU_SUMMARY_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("ray_summary"), RUNTIME_FINGERPRINT_RAY_SUMMARY_KEYS):
        return False
    sanitized = fingerprint.get("sanitized_environment")
    if not _dict_has_exact_keys(sanitized, RUNTIME_FINGERPRINT_SANITIZED_ENVIRONMENT_KEYS):
        return False
    if sanitized.get("policy") != RUNTIME_FINGERPRINT_ENV_POLICY:
        return False
    raw_keys = sanitized.get("keys")
    values = sanitized.get("values")
    redactions = sanitized.get("redactions")
    if not isinstance(raw_keys, list) or not isinstance(values, dict) or not isinstance(redactions, dict):
        return False
    keys = {str(key) for key in raw_keys}
    value_keys = {str(key) for key in values}
    redaction_keys = {str(key) for key in redactions}
    if keys != RUNTIME_FINGERPRINT_ALLOWED_ENV_KEYS:
        return False
    if not value_keys.issubset(RUNTIME_FINGERPRINT_ALLOWED_ENV_KEYS):
        return False
    if not redaction_keys.issubset(value_keys):
        return False
    if any(part in key.upper() for key in keys | value_keys for part in RUNTIME_FINGERPRINT_FORBIDDEN_ENV_KEY_PARTS):
        return False
    for key, raw_value in values.items():
        value = str(raw_value)
        if key in redactions:
            if value != RUNTIME_FINGERPRINT_REDACTED_ABSOLUTE_PATH:
                return False
            if redactions.get(key) != RUNTIME_FINGERPRINT_REDACTION_REASON_ABSOLUTE_PATH:
                return False
            continue
        if value == RUNTIME_FINGERPRINT_REDACTED_ABSOLUTE_PATH:
            return False
        if _env_value_is_path_like(value):
            return False
    python_executable_name = str((fingerprint.get("platform") or {}).get("python_executable_name") or "")
    if "/" in python_executable_name or "\\" in python_executable_name:
        return False
    gpu = preflight.get("gpu") if isinstance(preflight.get("gpu"), dict) else {}
    modules = preflight.get("python_modules") if isinstance(preflight.get("python_modules"), dict) else {}
    ray_cluster = preflight.get("ray_cluster") if isinstance(preflight.get("ray_cluster"), dict) else {}
    containers = preflight.get("container_runtimes") if isinstance(preflight.get("container_runtimes"), dict) else {}
    expected_tool_presence = {
        "rocm_smi": gpu.get("rocm_smi_available"),
        "rocminfo": gpu.get("rocminfo_available"),
        "docker": containers.get("docker"),
        "gvisor_runsc": containers.get("gvisor_runsc"),
        "firecracker": containers.get("firecracker"),
    }
    expected_gpu_summary = {
        "rocm_smi_output": gpu.get("rocm_smi_output"),
        "torch_probe": gpu.get("torch_probe"),
    }
    if fingerprint.get("tool_presence") != expected_tool_presence:
        return False
    if fingerprint.get("python_modules") != modules:
        return False
    if fingerprint.get("gpu_summary") != expected_gpu_summary:
        return False
    if fingerprint.get("ray_summary") != {"status_output": ray_cluster.get("status_output")}:
        return False
    return True


def build_m12_final_report(
    *,
    archive_verify_report_path: Path | None = None,
    preflight_report_path: Path,
    swe_run_summary_path: Path,
    verl_smoke_report_path: Path,
    ray_probe_report_path: Path,
    warm_vs_cold_report_path: Path,
    load_ladder_report_path: Path | None = None,
    soak_report_path: Path | None = None,
    command_log_manifest_path: Path | None = None,
) -> dict[str, Any]:
    archive_verify = _read_optional_json(archive_verify_report_path)
    preflight = _read_json_object(preflight_report_path)
    swe_summary = _read_json_object(swe_run_summary_path)
    verl_smoke = _read_json_object(verl_smoke_report_path)
    ray_probe = _read_json_object(ray_probe_report_path)
    warm_vs_cold = _read_json_object(warm_vs_cold_report_path)
    load_ladder = _read_optional_json(load_ladder_report_path)
    soak = _read_optional_json(soak_report_path)
    command_logs = _read_optional_json(command_log_manifest_path)
    command_log_manifest_validation_errors: list[str] = []
    if command_log_manifest_path is not None and command_logs.get("present") is True:
        if command_logs.get("read_error"):
            command_log_manifest_validation_errors = [
                f"command log manifest unreadable: {command_logs.get('read_error')}"
            ]
        else:
            from breadboard.rl.m12.command_logs import validate_command_log_manifest

            command_log_manifest_validation_errors = validate_command_log_manifest(
                command_log_manifest_path,
                required_command_ids=list(REQUIRED_COMMAND_LOG_IDS),
                require_passed=True,
                verify_hashes=True,
            )

    rows = list(swe_summary.get("rows") or [])
    row_counts = _status_counts(rows)
    command_entries = _command_log_entries(command_logs)
    archive_verify_validation_errors = _archive_verify_validation_errors(archive_verify)
    preflight_validation_errors = _preflight_validation_errors(preflight)
    swe_summary_validation_errors = _swe_summary_validation_errors(swe_summary)
    verl_smoke_validation_errors = _verl_smoke_validation_errors(verl_smoke)
    ray_probe_validation_errors = _ray_probe_validation_errors(ray_probe)
    warm_vs_cold_validation_errors = _warm_vs_cold_validation_errors(warm_vs_cold)
    load_ladder_validation_errors = _load_ladder_validation_errors(load_ladder)
    soak_validation_errors = _soak_validation_errors(soak)
    archived_command_ids = _command_log_ids(
        [entry for entry in command_entries if _command_log_entry_archived(entry)]
    )
    hash_verified_command_ids = _command_log_ids(
        [entry for entry in command_entries if _command_log_entry_hash_verified(entry, command_log_manifest_path)]
    )
    passed_command_ids = _command_log_ids(
        [entry for entry in command_entries if str(entry.get("status") or "") == "passed"]
    )
    required_command_ids = set(REQUIRED_COMMAND_LOG_IDS)
    required_target_run_ids = _required_command_target_run_ids(command_entries, required_command_ids)
    required_target_run_ids_without_empty = {item for item in required_target_run_ids if item}
    single_target_run_id = (
        next(iter(required_target_run_ids_without_empty))
        if len(required_target_run_ids_without_empty) == 1 and "" not in required_target_run_ids
        else None
    )
    required_command_text_mismatches = _required_command_text_mismatches(command_entries)
    missing_command_log_ids = sorted(required_command_ids - archived_command_ids)
    target_artifact_run_ids = _target_artifact_run_ids(
        preflight=preflight,
        swe_summary=swe_summary,
        verl_smoke=verl_smoke,
        ray_probe=ray_probe,
        warm_vs_cold=warm_vs_cold,
        load_ladder=load_ladder,
        soak=soak,
    )
    missing_target_artifact_run_ids = sorted(
        name for name, target_run_id in target_artifact_run_ids.items() if not target_run_id
    )
    mismatched_target_artifact_run_ids = {
        name: {"expected": single_target_run_id, "observed": target_run_id}
        for name, target_run_id in target_artifact_run_ids.items()
        if target_run_id and single_target_run_id and target_run_id != single_target_run_id
    }
    target_artifact_run_id_binding = (
        bool(single_target_run_id)
        and not missing_target_artifact_run_ids
        and not mismatched_target_artifact_run_ids
    )
    load_levels = {
        _int_or(item.get("target_sessions"), -1): str(item.get("status") or "")
        for item in load_ladder.get("concurrency_levels", [])
        if isinstance(item, dict)
    }
    load_level_items = [
        item for item in load_ladder.get("concurrency_levels", []) if isinstance(item, dict)
    ]
    resource_skipped_levels = {
        _int_or(item.get("target_sessions"), -1)
        for item in load_ladder.get("resource_skips", [])
        if isinstance(item, dict)
    }
    expected_artifacts = {
        "archive_verify_report": str(archive_verify_report_path) if archive_verify_report_path is not None else None,
        "preflight_report": str(preflight_report_path),
        "swe_run_summary": str(swe_run_summary_path),
        "verl_smoke_report": str(verl_smoke_report_path),
        "ray_probe_report": str(ray_probe_report_path),
        "warm_vs_cold_report": str(warm_vs_cold_report_path),
        "load_ladder_report": str(load_ladder_report_path) if load_ladder_report_path is not None else None,
        "soak_report": str(soak_report_path) if soak_report_path is not None else None,
        "command_log_manifest": str(command_log_manifest_path) if command_log_manifest_path is not None else None,
    }
    gates = {
        "artifact_paths_match_target_defaults": _artifact_paths_match_target_defaults(expected_artifacts),
        "archive_verify_report_present": archive_verify.get("present") is True,
        "archive_verify_report_readable": archive_verify.get("present") is True and _read_error(archive_verify) is None,
        "archive_verify_report_valid": archive_verify.get("present") is True
        and not archive_verify_validation_errors,
        "archive_verify_status_passed": archive_verify.get("status") == "passed",
        "preflight_report_readable": _read_error(preflight) is None,
        "preflight_report_valid": not preflight_validation_errors,
        "swe_run_summary_readable": _read_error(swe_summary) is None,
        "swe_run_summary_valid": not swe_summary_validation_errors,
        "verl_smoke_report_readable": _read_error(verl_smoke) is None,
        "verl_smoke_report_valid": not verl_smoke_validation_errors,
        "ray_probe_report_readable": _read_error(ray_probe) is None,
        "ray_probe_report_valid": not ray_probe_validation_errors,
        "warm_vs_cold_report_readable": _read_error(warm_vs_cold) is None,
        "warm_vs_cold_report_valid": not warm_vs_cold_validation_errors,
        "preflight_passed": preflight.get("status") == "preflight_passed",
        "preflight_runtime_fingerprint_present": _runtime_fingerprint_valid(preflight),
        "target_hardware": (
            preflight.get("gpu", {}).get("required_accelerator_count") == 8
            and preflight.get("gpu", {}).get("required_accelerator_family") == "MI300X"
            and preflight.get("gpu", {}).get("mi300x_product_evidence") is True
            and _int_or_zero(preflight.get("gpu", {}).get("torch_probe", {}).get("device_count")) >= 8
        ),
        "verl_available": preflight.get("python_modules", {}).get("verl", {}).get("available") is True,
        "ray_available": preflight.get("python_modules", {}).get("ray", {}).get("available") is True,
        "inference_engine_available": (
            preflight.get("inference_engine_feasibility", {}).get("decision") == "available"
            and (
                preflight.get("inference_engine_feasibility", {}).get("vllm_available") is True
                or preflight.get("inference_engine_feasibility", {}).get("sglang_available") is True
            )
        ),
        "container_runtime_available": any(
            value is True for value in (preflight.get("container_runtimes") or {}).values()
        ),
        "filesystem_cas_smoke_passed": preflight.get("filesystem_cas_smoke", {}).get("status") == "passed",
        "swe_probe_ran_10_rows": len(rows) >= 10,
        "swe_probe_has_accepted_rows": row_counts["accepted"] > 0,
        "swe_probe_no_unknown_status": row_counts["other"] == 0,
        "verl_jsonl_tensorizable": verl_smoke.get("formats", {}).get("jsonl", {}).get("tensorizable") is True,
        "verl_parquet_tensorizable": verl_smoke.get("formats", {}).get("parquet", {}).get("tensorizable") is True,
        "verl_row_count_matches_swe": _int_or(verl_smoke.get("row_count"), -1) == len(rows),
        "ray_probe_ran_10_rows": _int_or_zero(ray_probe.get("row_count")) >= 10,
        "ray_probe_has_workers": _int_or_zero(ray_probe.get("worker_count")) >= 2,
        "ray_probe_distributed": ray_probe.get("ray_local_mode") is False,
        "warm_vs_cold_has_total_ms": (
            "total_ms" in warm_vs_cold.get("warm", {}) and "total_ms" in warm_vs_cold.get("cold", {})
        ),
        "load_ladder_report_present": load_ladder.get("present") is True,
        "load_ladder_report_valid": load_ladder.get("present") is not True or not load_ladder_validation_errors,
        "load_ladder_required_levels_passed": all(
            load_levels.get(level) == "passed" for level in REQUIRED_LOAD_LEVELS
        ),
        "load_ladder_100_attempted_or_skipped": all(
            load_levels.get(level) == "passed" or level in resource_skipped_levels for level in OPTIONAL_LOAD_LEVELS
        ),
        "load_ladder_policy_integrity": load_ladder.get("policy_version_integrity") is True,
        "load_ladder_no_queue_corruption": load_ladder.get("queue_backpressure_integrity") is True,
        "load_ladder_distributed": all(
            item.get("status") == "resource_skipped" or item.get("ray_local_mode") is False
            for item in load_level_items
        )
        and bool(load_level_items),
        "soak_report_present": soak.get("present") is True,
        "soak_report_valid": soak.get("present") is not True or not soak_validation_errors,
        "soak_status_passed": soak.get("status") == "passed",
        "soak_duration_at_least_2h": _int_or_zero(soak.get("duration_seconds")) >= MIN_SOAK_SECONDS,
        "soak_no_runtime_failures": _int_or(soak.get("runtime_failure_count"), 1) == 0,
        "soak_distributed": soak.get("ray_local_mode") is False,
        "command_log_manifest_present": command_logs.get("present") is True,
        "command_log_manifest_id_valid": command_logs.get("manifest_id") == COMMAND_LOG_MANIFEST_ID,
        "command_log_manifest_valid": (
            command_logs.get("present") is True and not command_log_manifest_validation_errors
        ),
        "command_log_required_ids_canonical": _command_log_required_ids_canonical(command_logs),
        "command_log_manifest_complete": not missing_command_log_ids,
        "command_log_hashes_present": all(
            _command_log_entry_has_hash(entry)
            for entry in command_entries
            if str(entry.get("command_id") or "") in required_command_ids
        )
        and not missing_command_log_ids,
        "command_log_hashes_verified": all(command_id in hash_verified_command_ids for command_id in required_command_ids),
        "command_log_commands_passed": all(command_id in passed_command_ids for command_id in required_command_ids),
        "command_log_required_logs_archived_summary": command_logs.get("all_required_logs_archived") is True,
        "command_log_required_commands_passed_summary": command_logs.get("all_required_commands_passed") is True,
        "command_log_single_target_run_id": (
            not missing_command_log_ids
            and single_target_run_id is not None
        ),
        "command_log_expected_commands_match": not missing_command_log_ids and not required_command_text_mismatches,
        "target_artifact_run_id_binding": target_artifact_run_id_binding,
    }
    missing_gates = [name for name, passed in gates.items() if not passed]
    return {
        "report_id": FINAL_REPORT_ID,
        "claim_boundary": FINAL_REPORT_CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "m12_score_eligible": not missing_gates,
        "missing_gates": missing_gates,
        "missing_gate_remediations": _missing_gate_remediations(missing_gates),
        "artifact_paths": expected_artifacts,
        "artifact_path_policy": {
            "policy_id": "m12_target_artifact_paths_v1",
            "required_target_paths": dict(TARGET_ARTIFACT_PATHS),
            "paths_match_target_defaults": _artifact_paths_match_target_defaults(expected_artifacts),
            "reason": "M12 score eligibility requires target-node default artifact paths, not local M6/M7/M8 preparation paths.",
        },
        "archive_verify": {
            "present": archive_verify.get("present") is True,
            "path": archive_verify.get("path"),
            "read_error": archive_verify.get("read_error"),
            "report_id": archive_verify.get("report_id"),
            "claim_boundary": archive_verify.get("claim_boundary"),
            "scorecard_update_allowed": archive_verify.get("scorecard_update_allowed"),
            "m12_points_awarded": archive_verify.get("m12_points_awarded"),
            "status": archive_verify.get("status"),
            "archive_manifest_id": archive_verify.get("archive_manifest_id"),
            "archive_claim_boundary": archive_verify.get("archive_claim_boundary"),
            "archive_sha256": archive_verify.get("archive_sha256"),
            "included_entry_count": archive_verify.get("included_entry_count"),
            "all_required_artifacts_present": archive_verify.get("all_required_artifacts_present"),
            "all_transfer_requirements_covered": archive_verify.get("all_transfer_requirements_covered"),
            "archive_contains_source_overlay": archive_verify.get("archive_contains_source_overlay"),
            "archive_deterministic": archive_verify.get("archive_deterministic"),
            "source_paths_portable": archive_verify.get("source_paths_portable"),
            "errors": list(archive_verify.get("errors") or []),
            "validation_errors": archive_verify_validation_errors,
        },
        "preflight": {
            "present": preflight.get("present") is True,
            "path": preflight.get("path"),
            "read_error": preflight.get("read_error"),
            "status": preflight.get("status"),
            "target_run_id": preflight.get("target_run_id"),
            "blockers": list(preflight.get("blockers") or []),
            "gpu": preflight.get("gpu", {}),
            "python_modules": preflight.get("python_modules", {}),
            "ray_cluster": preflight.get("ray_cluster", {}),
            "inference_engine_feasibility": preflight.get("inference_engine_feasibility", {}),
            "filesystem_cas_smoke": preflight.get("filesystem_cas_smoke", {}),
            "container_runtimes": preflight.get("container_runtimes", {}),
            "runtime_fingerprint": preflight.get("runtime_fingerprint", {}),
            "validation_errors": preflight_validation_errors,
        },
        "swe_probe": {
            "present": swe_summary.get("present") is True,
            "path": swe_summary.get("path"),
            "read_error": swe_summary.get("read_error"),
            "run_id": swe_summary.get("run_id"),
            "target_run_id": swe_summary.get("target_run_id"),
            "row_count": len(rows),
            "row_status_counts": row_counts,
            "package_id": swe_summary.get("package_id"),
            "package_hash": swe_summary.get("package_hash"),
            "validation_errors": swe_summary_validation_errors,
        },
        "verl_export": {
            "present": verl_smoke.get("present") is True,
            "path": verl_smoke.get("path"),
            "read_error": verl_smoke.get("read_error"),
            "row_count": verl_smoke.get("row_count"),
            "target_run_id": verl_smoke.get("target_run_id"),
            "trainable_candidate_count": verl_smoke.get("trainable_candidate_count"),
            "tensorizable": verl_smoke.get("tensorizable"),
            "formats": verl_smoke.get("formats", {}),
            "validation_errors": verl_smoke_validation_errors,
        },
        "ray_probe": {
            "present": ray_probe.get("present") is True,
            "path": ray_probe.get("path"),
            "read_error": ray_probe.get("read_error"),
            "row_count": ray_probe.get("row_count"),
            "target_run_id": ray_probe.get("target_run_id"),
            "worker_count": ray_probe.get("worker_count"),
            "ray_local_mode": ray_probe.get("ray_local_mode"),
            "validation_errors": ray_probe_validation_errors,
        },
        "warm_vs_cold": {
            "present": warm_vs_cold.get("present") is True,
            "path": warm_vs_cold.get("path"),
            "read_error": warm_vs_cold.get("read_error"),
            "target_run_id": warm_vs_cold.get("target_run_id"),
            "warm": warm_vs_cold.get("warm", {}),
            "cold": warm_vs_cold.get("cold", {}),
            "validation_errors": warm_vs_cold_validation_errors,
        },
        "load_ladder": {
            "present": load_ladder.get("present") is True,
            "path": load_ladder.get("path"),
            "read_error": load_ladder.get("read_error"),
            "target_run_id": load_ladder.get("target_run_id"),
            "required_levels": list(REQUIRED_LOAD_LEVELS),
            "optional_levels": list(OPTIONAL_LOAD_LEVELS),
            "concurrency_levels": list(load_ladder.get("concurrency_levels") or []),
            "resource_skips": list(load_ladder.get("resource_skips") or []),
            "policy_version_integrity": load_ladder.get("policy_version_integrity"),
            "queue_backpressure_integrity": load_ladder.get("queue_backpressure_integrity"),
            "validation_errors": load_ladder_validation_errors,
        },
        "soak": {
            "present": soak.get("present") is True,
            "path": soak.get("path"),
            "read_error": soak.get("read_error"),
            "minimum_duration_seconds": MIN_SOAK_SECONDS,
            "duration_seconds": soak.get("duration_seconds"),
            "target_run_id": soak.get("target_run_id"),
            "status": soak.get("status"),
            "runtime_failure_count": soak.get("runtime_failure_count"),
            "ray_local_mode": soak.get("ray_local_mode"),
            "validation_errors": soak_validation_errors,
        },
        "command_logs": {
            "present": command_logs.get("present") is True,
            "path": command_logs.get("path"),
            "read_error": command_logs.get("read_error"),
            "manifest_id": command_logs.get("manifest_id"),
            "manifest_validation_errors": command_log_manifest_validation_errors,
            "required_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
            "manifest_required_command_ids": command_logs.get("required_command_ids"),
            "archived_command_ids": sorted(archived_command_ids),
            "hash_verified_command_ids": sorted(hash_verified_command_ids),
            "missing_command_log_ids": missing_command_log_ids,
            "target_run_ids": sorted(required_target_run_ids_without_empty),
            "single_target_run_id": (
                single_target_run_id
            ),
            "command_text_mismatches": required_command_text_mismatches,
            "all_required_logs_archived": command_logs.get("all_required_logs_archived"),
            "all_required_commands_passed": command_logs.get("all_required_commands_passed"),
            "command_count": len(command_entries),
            "commands": command_entries,
        },
        "target_run_identity": {
            "single_command_log_target_run_id": single_target_run_id,
            "artifact_target_run_ids": target_artifact_run_ids,
            "missing_artifact_target_run_ids": missing_target_artifact_run_ids,
            "mismatched_artifact_target_run_ids": mismatched_target_artifact_run_ids,
            "run_ids_match_command_logs": target_artifact_run_id_binding,
        },
        "operator_next_step": (
            "If m12_score_eligible is true, archive this report with raw command logs and update the scorecard in a "
            "separate reviewed change. This builder never updates the scorecard."
        ),
    }


def validate_m12_final_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != FINAL_REPORT_ID:
        errors.append("report_id must be bb_zyphra_rl_phase1_m12_final_report_v1")
    if report.get("claim_boundary") != FINAL_REPORT_CLAIM_BOUNDARY:
        errors.append("claim_boundary must remain m12_target_validation_candidate_not_scorecard_update")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false; score updates require separate review")
    if not isinstance(report.get("missing_gates"), list):
        errors.append("missing_gates must be a list")
    else:
        unknown_gates = sorted({str(gate) for gate in report["missing_gates"]} - M12_FINAL_REPORT_GATE_NAMES)
        for gate in unknown_gates:
            errors.append(f"unknown missing gate: {gate}")
    remediations = report.get("missing_gate_remediations")
    if not isinstance(remediations, list):
        errors.append("missing_gate_remediations must be a list")
    elif isinstance(report.get("missing_gates"), list):
        remediation_gates: list[str] = []
        for index, remediation in enumerate(remediations, start=1):
            if not isinstance(remediation, dict):
                errors.append(f"missing_gate_remediations row {index} must be an object")
                continue
            gate = str(remediation.get("gate") or "")
            remediation_gates.append(gate)
            if gate not in M12_FINAL_REPORT_GATE_NAMES:
                errors.append(f"missing_gate_remediations row {index} has unknown gate: {gate}")
            if not str(remediation.get("blocking_stage") or "").strip():
                errors.append(f"missing_gate_remediations row {index} requires blocking_stage")
            if not str(remediation.get("operator_action") or "").strip():
                errors.append(f"missing_gate_remediations row {index} requires operator_action")
            if remediation.get("target_action_id") is not None and not str(
                remediation.get("target_action_id") or ""
            ).strip():
                errors.append(f"missing_gate_remediations row {index} target_action_id must be non-empty when set")
            required_artifact_path = remediation.get("required_artifact_path")
            if required_artifact_path is not None and str(required_artifact_path) not in TARGET_ARTIFACT_PATHS.values():
                errors.append(
                    f"missing_gate_remediations row {index} required_artifact_path must be an M12 target artifact path"
                )
        if remediation_gates != [str(gate) for gate in report["missing_gates"]]:
            errors.append("missing_gate_remediations gates must match missing_gates in order")
    if not isinstance(report.get("artifact_paths"), dict) or not report["artifact_paths"]:
        errors.append("artifact_paths must be a non-empty object")
    if not isinstance(report.get("artifact_path_policy"), dict) or not report["artifact_path_policy"]:
        errors.append("artifact_path_policy must be a non-empty object")
    errors.extend(_artifact_path_policy_validation_errors(report))
    errors.extend(_artifact_section_path_validation_errors(report))
    errors.extend(_command_log_summary_validation_errors(report))
    expected_missing_gates = _missing_gate_names_from_final_report(report)
    if isinstance(report.get("missing_gates"), list):
        observed_missing_gates = [str(gate) for gate in report["missing_gates"]]
        if observed_missing_gates != expected_missing_gates:
            errors.append("missing_gates must match final-report embedded evidence gates")
    if report.get("m12_score_eligible") is not (not expected_missing_gates):
        errors.append("m12_score_eligible must match final-report embedded evidence gates")
    errors.extend(_target_run_identity_validation_errors(report))
    if bool(report.get("m12_score_eligible")) and report.get("missing_gates"):
        errors.append("m12_score_eligible cannot be true while missing_gates is non-empty")
    if bool(report.get("m12_score_eligible")):
        preflight = report.get("preflight", {})
        archive_verify = report.get("archive_verify", {})
        swe_probe = report.get("swe_probe", {})
        verl_export = report.get("verl_export", {})
        ray_probe = report.get("ray_probe", {})
        load_ladder = report.get("load_ladder", {})
        soak = report.get("soak", {})
        command_logs = report.get("command_logs", {})
        target_run_identity = report.get("target_run_identity", {})
        if not _artifact_paths_match_target_defaults(report.get("artifact_paths") or {}):
            errors.append("eligible report requires target-node default artifact paths")
        artifact_path_policy = report.get("artifact_path_policy") or {}
        if artifact_path_policy.get("paths_match_target_defaults") is not True:
            errors.append("eligible report requires artifact_path_policy.paths_match_target_defaults=true")
        for section_name, section in [
            ("archive_verify", archive_verify),
            ("preflight", preflight),
            ("swe_probe", swe_probe),
            ("verl_export", verl_export),
            ("ray_probe", ray_probe),
            ("warm_vs_cold", report.get("warm_vs_cold", {})),
            ("load_ladder", load_ladder),
            ("soak", soak),
            ("command_logs", command_logs),
        ]:
            if section.get("read_error"):
                errors.append(f"eligible report requires readable {section_name}: {section.get('read_error')}")
        gpu = preflight.get("gpu", {})
        modules = preflight.get("python_modules", {})
        if archive_verify.get("present") is not True:
            errors.append("eligible report requires archive verify report")
        if archive_verify.get("validation_errors") != []:
            errors.append("eligible report requires archive verify validation errors to be empty")
        if archive_verify.get("status") != "passed":
            errors.append("eligible report requires archive verify status=passed")
        if preflight.get("status") != "preflight_passed":
            errors.append("eligible report requires preflight.status=preflight_passed")
        if preflight.get("validation_errors") != []:
            errors.append("eligible report requires preflight validation errors to be empty")
        if preflight.get("blockers"):
            errors.append("eligible report requires no preflight blockers")
        if not _runtime_fingerprint_valid(preflight):
            errors.append("eligible report requires preflight runtime fingerprint")
        if gpu.get("required_accelerator_count") != 8:
            errors.append("eligible report requires required_accelerator_count=8")
        if gpu.get("required_accelerator_family") != "MI300X":
            errors.append("eligible report requires required_accelerator_family=MI300X")
        if gpu.get("mi300x_product_evidence") is not True:
            errors.append("eligible report requires MI300X product evidence")
        if _int_or_zero(gpu.get("torch_probe", {}).get("device_count")) < 8:
            errors.append("eligible report requires torch device_count >= 8")
        if not (gpu.get("rocminfo_available") is True or gpu.get("rocm_smi_available") is True):
            errors.append("eligible report requires ROCm tooling availability")
        if modules.get("verl", {}).get("available") is not True:
            errors.append("eligible report requires VeRL import availability")
        if modules.get("ray", {}).get("available") is not True:
            errors.append("eligible report requires Ray import availability")
        inference = preflight.get("inference_engine_feasibility", {})
        if not (
            inference.get("decision") == "available"
            and (inference.get("vllm_available") is True or inference.get("sglang_available") is True)
        ):
            errors.append("eligible report requires vLLM or SGLang inference-engine availability")
        if not any(value is True for value in (preflight.get("container_runtimes") or {}).values()):
            errors.append("eligible report requires at least one container runtime")
        if preflight.get("filesystem_cas_smoke", {}).get("status") != "passed":
            errors.append("eligible report requires filesystem/CAS smoke passed")
        if _int_or_zero(swe_probe.get("row_count")) < 10:
            errors.append("eligible report requires at least 10 SWE rows")
        if swe_probe.get("validation_errors") != []:
            errors.append("eligible report requires SWE run summary validation errors to be empty")
        if _int_or(swe_probe.get("row_status_counts", {}).get("other"), 1) != 0:
            errors.append("eligible report requires no unknown SWE row statuses")
        if _int_or_zero(swe_probe.get("row_status_counts", {}).get("accepted")) < 1:
            errors.append("eligible report requires at least one accepted SWE row")
        if _int_or(verl_export.get("row_count"), -1) != _int_or(swe_probe.get("row_count"), -2):
            errors.append("eligible report requires VeRL row count to match SWE row count")
        if verl_export.get("validation_errors") != []:
            errors.append("eligible report requires VeRL smoke validation errors to be empty")
        if verl_export.get("formats", {}).get("jsonl", {}).get("tensorizable") is not True:
            errors.append("eligible report requires tensorizable JSONL export")
        if verl_export.get("formats", {}).get("parquet", {}).get("tensorizable") is not True:
            errors.append("eligible report requires tensorizable Parquet export")
        if _int_or_zero(ray_probe.get("row_count")) < 10:
            errors.append("eligible report requires at least 10 Ray rows")
        if ray_probe.get("validation_errors") != []:
            errors.append("eligible report requires Ray probe validation errors to be empty")
        if _int_or_zero(ray_probe.get("worker_count")) < 2:
            errors.append("eligible report requires at least two Ray workers")
        if ray_probe.get("ray_local_mode") is not False:
            errors.append("eligible report requires Ray distributed mode, not local_mode")
        if report.get("warm_vs_cold", {}).get("validation_errors") != []:
            errors.append("eligible report requires warm-vs-cold validation errors to be empty")
        if load_ladder.get("present") is not True:
            errors.append("eligible report requires load ladder report")
        if load_ladder.get("validation_errors") != []:
            errors.append("eligible report requires load ladder validation errors to be empty")
        observed_levels = {
            _int_or(item.get("target_sessions"), -1): str(item.get("status") or "")
            for item in load_ladder.get("concurrency_levels", [])
            if isinstance(item, dict)
        }
        skipped_levels = {
            _int_or(item.get("target_sessions"), -1)
            for item in load_ladder.get("resource_skips", [])
            if isinstance(item, dict)
        }
        for level in REQUIRED_LOAD_LEVELS:
            if observed_levels.get(level) != "passed":
                errors.append(f"eligible report requires load level {level} passed")
        for level in OPTIONAL_LOAD_LEVELS:
            if observed_levels.get(level) != "passed" and level not in skipped_levels:
                errors.append(f"eligible report requires load level {level} passed or resource-skipped")
        if load_ladder.get("policy_version_integrity") is not True:
            errors.append("eligible report requires load policy_version_integrity=true")
        if load_ladder.get("queue_backpressure_integrity") is not True:
            errors.append("eligible report requires load queue_backpressure_integrity=true")
        for item in load_ladder.get("concurrency_levels", []):
            if isinstance(item, dict) and item.get("status") != "resource_skipped" and item.get("ray_local_mode") is not False:
                errors.append("eligible report requires distributed load ladder, not local_mode")
        if soak.get("present") is not True:
            errors.append("eligible report requires soak report")
        if soak.get("validation_errors") != []:
            errors.append("eligible report requires soak validation errors to be empty")
        if soak.get("status") != "passed":
            errors.append("eligible report requires soak status=passed")
        if _int_or_zero(soak.get("duration_seconds")) < MIN_SOAK_SECONDS:
            errors.append("eligible report requires soak duration >= 7200 seconds")
        if _int_or(soak.get("runtime_failure_count"), 1) != 0:
            errors.append("eligible report requires zero soak runtime failures")
        if soak.get("ray_local_mode") is not False:
            errors.append("eligible report requires distributed soak, not local_mode")
        if command_logs.get("present") is not True:
            errors.append("eligible report requires command log manifest")
        if command_logs.get("manifest_id") != COMMAND_LOG_MANIFEST_ID:
            errors.append("eligible report requires valid command log manifest_id")
        if command_logs.get("manifest_validation_errors") != []:
            errors.append("eligible report requires command log manifest validation errors to be empty")
        if command_logs.get("manifest_required_command_ids") != list(REQUIRED_COMMAND_LOG_IDS):
            errors.append("eligible report requires canonical command log required_command_ids")
        archived_command_ids = set(command_logs.get("archived_command_ids") or [])
        hash_verified_command_ids = set(command_logs.get("hash_verified_command_ids") or [])
        for command_id in REQUIRED_COMMAND_LOG_IDS:
            if command_id not in archived_command_ids:
                errors.append(f"eligible report requires archived command log for {command_id}")
            if command_id not in hash_verified_command_ids:
                errors.append(f"eligible report requires hash-verified command log for {command_id}")
        if command_logs.get("all_required_logs_archived") is not True:
            errors.append("eligible report requires all_required_logs_archived=true")
        if command_logs.get("all_required_commands_passed") is not True:
            errors.append("eligible report requires all_required_commands_passed=true")
        if not command_logs.get("single_target_run_id"):
            errors.append("eligible report requires all required command logs to share one target_run_id")
        if command_logs.get("command_text_mismatches") != []:
            errors.append("eligible report requires required command text to match M12 target command manifest")
        if not isinstance(target_run_identity, dict) or not target_run_identity:
            errors.append("eligible report requires target_run_identity")
        else:
            command_target_run_id = command_logs.get("single_target_run_id")
            if target_run_identity.get("single_command_log_target_run_id") != command_target_run_id:
                errors.append("eligible report requires target_run_identity to match command log target_run_id")
            if target_run_identity.get("run_ids_match_command_logs") is not True:
                errors.append("eligible report requires target artifacts to share command-log target_run_id")
            artifact_target_run_ids = target_run_identity.get("artifact_target_run_ids")
            if not isinstance(artifact_target_run_ids, dict):
                errors.append("eligible report requires artifact target_run_id map")
            else:
                actual_artifact_target_run_ids = _target_artifact_run_ids_from_final_report(report)
                for artifact_key in [
                    "preflight_report",
                    "swe_run_summary",
                    "verl_smoke_report",
                    "ray_probe_report",
                    "warm_vs_cold_report",
                    "load_ladder_report",
                    "soak_report",
                ]:
                    if str(actual_artifact_target_run_ids.get(artifact_key) or "") != str(command_target_run_id or ""):
                        errors.append(f"eligible report requires {artifact_key} target_run_id to match command logs")
        for entry in command_logs.get("commands", []):
            if not isinstance(entry, dict):
                continue
            command_id = str(entry.get("command_id") or "")
            if command_id in REQUIRED_COMMAND_LOG_IDS and not _command_log_entry_archived(entry):
                errors.append(f"eligible report requires log_path and sha256 for {command_id}")
    return errors


def write_m12_final_report(
    *,
    output_path: Path,
    archive_verify_report_path: Path | None = None,
    preflight_report_path: Path,
    swe_run_summary_path: Path,
    verl_smoke_report_path: Path,
    ray_probe_report_path: Path,
    warm_vs_cold_report_path: Path,
    load_ladder_report_path: Path | None = None,
    soak_report_path: Path | None = None,
    command_log_manifest_path: Path | None = None,
) -> dict[str, Any]:
    report = build_m12_final_report(
        archive_verify_report_path=archive_verify_report_path,
        preflight_report_path=preflight_report_path,
        swe_run_summary_path=swe_run_summary_path,
        verl_smoke_report_path=verl_smoke_report_path,
        ray_probe_report_path=ray_probe_report_path,
        warm_vs_cold_report_path=warm_vs_cold_report_path,
        load_ladder_report_path=load_ladder_report_path,
        soak_report_path=soak_report_path,
        command_log_manifest_path=command_log_manifest_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
