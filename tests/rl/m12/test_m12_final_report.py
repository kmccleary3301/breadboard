from __future__ import annotations

import json
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path

from breadboard.rl.m12 import (
    build_m12_final_report,
    record_command_log_result,
    summarize_m12_final_report_remediations,
    validate_m12_final_report,
    validate_m12_final_report_remediation_summary,
    write_m12_final_report,
)
from breadboard.rl.m12.final_report import REQUIRED_COMMAND_LOG_COMMANDS, REQUIRED_COMMAND_LOG_IDS, TARGET_ARTIFACT_PATHS
from breadboard.rl.m12.transfer import (
    COMMAND_LOG_MANIFEST_TEMPLATE,
    LOAD_LADDER_REPORT_TEMPLATE,
    SOAK_REPORT_TEMPLATE,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE_RUNS = REPO_ROOT.parent / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs"


def _fingerprint_sha256(fingerprint: dict) -> str:
    payload = dict(fingerprint)
    payload.pop("sha256", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _write_wrapper_style_log(
    log_path: Path,
    *,
    command_id: str,
    command: str,
    exit_code: int = 0,
    started_at: str = "2026-06-17T00:00:00Z",
    completed_at: str = "2026-06-17T00:00:01Z",
    target_run_id: str = "m12-target-run-test",
) -> None:
    log_path.write_text(
        "\n".join(
            [
                f"# command_id: {command_id}",
                f"# target_run_id: {target_run_id}",
                f"# command: {command}",
                f"# argv_json: {json.dumps(shlex.split(command), ensure_ascii=True)}",
                f"# started_at: {started_at}",
                f"{command_id} ok",
                f"# completed_at: {completed_at}",
                f"# exit_code: {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_complete_command_log_manifest(tmp_path: Path, *, target_run_id: str = "m12-target-run-test") -> Path:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        _write_wrapper_style_log(
            log_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            target_run_id=target_run_id,
        )
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id=target_run_id,
        )
    return manifest_path


def _local_report() -> dict:
    return build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
    )


def test_local_m12_final_report_is_not_score_eligible() -> None:
    report = _local_report()

    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_final_report_v1"
    assert report["claim_boundary"] == "m12_target_validation_candidate_not_scorecard_update"
    assert report["scorecard_update_allowed"] is False
    assert report["m12_score_eligible"] is False
    assert "artifact_paths_match_target_defaults" in report["missing_gates"]
    assert report["artifact_path_policy"]["paths_match_target_defaults"] is False
    assert "archive_verify_report_present" in report["missing_gates"]
    assert "archive_verify_report_readable" in report["missing_gates"]
    assert "archive_verify_report_valid" in report["missing_gates"]
    assert "archive_verify_status_passed" in report["missing_gates"]
    assert "preflight_passed" not in report["missing_gates"]
    assert "preflight_report_valid" not in report["missing_gates"]
    assert "swe_run_summary_valid" not in report["missing_gates"]
    assert "verl_smoke_report_valid" not in report["missing_gates"]
    assert "ray_probe_report_valid" not in report["missing_gates"]
    assert "warm_vs_cold_report_valid" not in report["missing_gates"]
    assert "preflight_runtime_fingerprint_present" not in report["missing_gates"]
    assert "target_hardware" not in report["missing_gates"]
    assert "verl_available" not in report["missing_gates"]
    assert "inference_engine_available" not in report["missing_gates"]
    assert "container_runtime_available" not in report["missing_gates"]
    assert "ray_probe_distributed" in report["missing_gates"]
    assert "load_ladder_report_present" in report["missing_gates"]
    assert "soak_report_present" in report["missing_gates"]
    assert "command_log_manifest_present" in report["missing_gates"]
    assert "target_artifact_run_id_binding" in report["missing_gates"]
    assert [item["gate"] for item in report["missing_gate_remediations"]] == report["missing_gates"]
    remediations_by_gate = {item["gate"]: item for item in report["missing_gate_remediations"]}
    assert (
        remediations_by_gate["archive_verify_report_present"]["target_action_id"]
        == "target_transfer_archive_verify"
    )
    assert (
        remediations_by_gate["archive_verify_report_present"]["required_artifact_path"]
        == TARGET_ARTIFACT_PATHS["archive_verify_report"]
    )
    assert remediations_by_gate["load_ladder_report_present"]["target_action_id"] == "target_load_ladder"
    assert remediations_by_gate["soak_report_present"]["target_action_id"] == "target_soak"
    assert remediations_by_gate["command_log_manifest_present"]["target_action_id"] == "m12_test_commands.sh"
    assert report["load_ladder"]["present"] is False
    assert report["soak"]["present"] is False
    assert report["command_logs"]["present"] is False
    assert report["archive_verify"]["present"] is False
    assert report["archive_verify"]["validation_errors"] == []
    assert report["preflight"]["validation_errors"] == []
    assert report["preflight"]["inference_engine_feasibility"]["decision"] == "available"
    assert report["preflight"]["container_runtimes"]
    assert "status_output" in report["preflight"]["ray_cluster"]
    assert report["swe_probe"]["validation_errors"] == []
    assert report["verl_export"]["validation_errors"] == []
    assert report["ray_probe"]["validation_errors"] == []
    assert report["warm_vs_cold"]["validation_errors"] == []
    assert report["load_ladder"]["validation_errors"] == []
    assert report["soak"]["validation_errors"] == []
    assert report["command_logs"]["missing_command_log_ids"] == sorted(REQUIRED_COMMAND_LOG_IDS)
    assert report["target_run_identity"]["run_ids_match_command_logs"] is False
    assert "swe_run_summary" in report["target_run_identity"]["missing_artifact_target_run_ids"]
    assert report["swe_probe"]["row_count"] == 10
    assert report["swe_probe"]["row_status_counts"] == {
        "accepted": 7,
        "rejected": 1,
        "quarantined": 2,
        "other": 0,
    }
    assert validate_m12_final_report(report) == []


def test_m12_final_report_write_persists_json(tmp_path) -> None:
    output_path = tmp_path / "m12_final_report.json"

    report = write_m12_final_report(
        output_path=output_path,
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
    )

    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["report_id"] == report["report_id"]


def test_pending_target_templates_make_final_report_noneligible_without_crashing(tmp_path) -> None:
    load_path = tmp_path / "load_ladder_report.json"
    soak_path = tmp_path / "soak_report.json"
    command_log_path = tmp_path / "command_log_manifest.json"
    load_path.write_text(json.dumps(LOAD_LADDER_REPORT_TEMPLATE), encoding="utf-8")
    soak_path.write_text(json.dumps(SOAK_REPORT_TEMPLATE), encoding="utf-8")
    command_log_path.write_text(json.dumps(COMMAND_LOG_MANIFEST_TEMPLATE), encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        load_ladder_report_path=load_path,
        soak_report_path=soak_path,
        command_log_manifest_path=command_log_path,
    )

    assert report["m12_score_eligible"] is False
    assert report["load_ladder"]["present"] is True
    assert report["soak"]["present"] is True
    assert report["command_logs"]["present"] is True
    assert "load_ladder_required_levels_passed" in report["missing_gates"]
    assert "load_ladder_report_valid" in report["missing_gates"]
    assert "load_ladder_distributed" in report["missing_gates"]
    assert report["load_ladder"]["validation_errors"]
    assert "soak_status_passed" in report["missing_gates"]
    assert "soak_report_valid" in report["missing_gates"]
    assert "soak_duration_at_least_2h" in report["missing_gates"]
    assert "soak_no_runtime_failures" in report["missing_gates"]
    assert "soak_distributed" in report["missing_gates"]
    assert report["soak"]["validation_errors"]
    assert "command_log_manifest_complete" in report["missing_gates"]
    assert "command_log_hashes_present" in report["missing_gates"]
    assert "command_log_hashes_verified" in report["missing_gates"]
    assert "command_log_commands_passed" in report["missing_gates"]
    assert validate_m12_final_report(report) == []


def test_malformed_optional_target_artifacts_make_final_report_noneligible_without_crashing(tmp_path) -> None:
    load_path = tmp_path / "load_ladder_report.json"
    soak_path = tmp_path / "soak_report.json"
    command_log_path = tmp_path / "command_log_manifest.json"
    load_path.write_text("{not valid load ladder json", encoding="utf-8")
    soak_path.write_text("{not valid soak json", encoding="utf-8")
    command_log_path.write_text("{not valid command log json", encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        load_ladder_report_path=load_path,
        soak_report_path=soak_path,
        command_log_manifest_path=command_log_path,
    )

    assert report["m12_score_eligible"] is False
    assert report["load_ladder"]["present"] is True
    assert "JSONDecodeError" in report["load_ladder"]["read_error"]
    assert "load_ladder_report_valid" in report["missing_gates"]
    assert report["load_ladder"]["validation_errors"] == [
        f"load ladder report unreadable: {report['load_ladder']['read_error']}"
    ]
    assert report["soak"]["present"] is True
    assert "JSONDecodeError" in report["soak"]["read_error"]
    assert "soak_report_valid" in report["missing_gates"]
    assert report["soak"]["validation_errors"] == [f"soak report unreadable: {report['soak']['read_error']}"]
    assert report["command_logs"]["present"] is True
    assert "JSONDecodeError" in report["command_logs"]["read_error"]
    assert "command_log_manifest_valid" in report["missing_gates"]
    assert report["command_logs"]["manifest_validation_errors"] == [
        f"command log manifest unreadable: {report['command_logs']['read_error']}"
    ]
    assert validate_m12_final_report(report) == []


def test_malformed_required_preflight_artifact_make_final_report_noneligible_without_crashing(
    tmp_path,
) -> None:
    preflight_path = tmp_path / "m12_preflight_report.json"
    preflight_path.write_text("{not valid preflight json", encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=preflight_path,
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
    )

    assert report["m12_score_eligible"] is False
    assert report["preflight"]["present"] is True
    assert "JSONDecodeError" in report["preflight"]["read_error"]
    assert "preflight_report_readable" in report["missing_gates"]
    assert "preflight_report_valid" in report["missing_gates"]
    assert "preflight_passed" in report["missing_gates"]
    assert report["preflight"]["validation_errors"] == [
        f"preflight report unreadable: {report['preflight']['read_error']}"
    ]
    assert validate_m12_final_report(report) == []


def test_malformed_required_swe_verl_ray_and_warm_artifacts_make_final_report_noneligible(
    tmp_path,
) -> None:
    swe_path = tmp_path / "run_summary.json"
    verl_path = tmp_path / "smoke_consumer_report.json"
    ray_path = tmp_path / "ray_probe_report.json"
    warm_path = tmp_path / "warm_vs_cold_report.json"
    swe_path.write_text("{not valid swe json", encoding="utf-8")
    verl_path.write_text("{not valid verl json", encoding="utf-8")
    ray_path.write_text("{not valid ray json", encoding="utf-8")
    warm_path.write_text("{not valid warm json", encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=swe_path,
        verl_smoke_report_path=verl_path,
        ray_probe_report_path=ray_path,
        warm_vs_cold_report_path=warm_path,
    )

    assert report["m12_score_eligible"] is False
    assert "swe_run_summary_readable" in report["missing_gates"]
    assert "swe_run_summary_valid" in report["missing_gates"]
    assert "SWE run summary unreadable" in report["swe_probe"]["validation_errors"][0]
    assert "verl_smoke_report_readable" in report["missing_gates"]
    assert "verl_smoke_report_valid" in report["missing_gates"]
    assert "VeRL smoke report unreadable" in report["verl_export"]["validation_errors"][0]
    assert "ray_probe_report_readable" in report["missing_gates"]
    assert "ray_probe_report_valid" in report["missing_gates"]
    assert "Ray probe report unreadable" in report["ray_probe"]["validation_errors"][0]
    assert "warm_vs_cold_report_readable" in report["missing_gates"]
    assert "warm_vs_cold_report_valid" in report["missing_gates"]
    assert "warm-vs-cold report unreadable" in report["warm_vs_cold"]["validation_errors"][0]
    assert validate_m12_final_report(report) == []


def test_forced_m12_eligibility_is_rejected_when_gates_are_missing() -> None:
    report = _local_report()
    report["m12_score_eligible"] = True

    errors = validate_m12_final_report(report)

    assert "m12_score_eligible must match final-report embedded evidence gates" in errors
    assert "m12_score_eligible cannot be true while missing_gates is non-empty" in errors
    assert "eligible report requires preflight.status=preflight_passed" not in errors
    assert "eligible report requires preflight runtime fingerprint" not in errors
    assert "eligible report requires MI300X product evidence" not in errors
    assert "eligible report requires torch device_count >= 8" not in errors
    assert "eligible report requires VeRL import availability" not in errors
    assert "eligible report requires Ray distributed mode, not local_mode" in errors
    assert "eligible report requires load ladder report" in errors
    assert "eligible report requires soak report" in errors
    assert "eligible report requires distributed soak, not local_mode" in errors
    assert "eligible report requires command log manifest" in errors


def test_m12_final_report_rejects_unknown_or_unmapped_missing_gate() -> None:
    report = _local_report()
    report["missing_gates"].append("synthetic_unknown_gate")
    report["missing_gate_remediations"].append(
        {
            "gate": "synthetic_unknown_gate",
            "blocking_stage": "unknown",
            "target_action_id": None,
            "required_artifact_path": None,
            "operator_action": "synthetic action",
        }
    )

    errors = validate_m12_final_report(report)

    assert "unknown missing gate: synthetic_unknown_gate" in errors
    assert "missing_gate_remediations row 31 has unknown gate: synthetic_unknown_gate" in errors


def test_m12_final_report_requires_remediation_rows_to_match_missing_gates() -> None:
    report = _local_report()
    report["missing_gate_remediations"] = report["missing_gate_remediations"][:-1]

    errors = validate_m12_final_report(report)

    assert "missing_gate_remediations gates must match missing_gates in order" in errors


def test_m12_final_report_rejects_stale_missing_gates_against_embedded_evidence() -> None:
    report = _local_report()
    removed_gate = "ray_probe_distributed"
    report["missing_gates"] = [gate for gate in report["missing_gates"] if gate != removed_gate]
    report["missing_gate_remediations"] = [
        item for item in report["missing_gate_remediations"] if item["gate"] != removed_gate
    ]

    errors = validate_m12_final_report(report)

    assert "missing_gates must match final-report embedded evidence gates" in errors


def test_m12_final_report_rejects_stale_artifact_path_policy_summary() -> None:
    report = _local_report()
    report["artifact_path_policy"]["paths_match_target_defaults"] = True

    errors = validate_m12_final_report(report)

    assert "artifact_path_policy.paths_match_target_defaults is stale" in errors


def test_m12_final_report_requires_canonical_artifact_path_policy_metadata() -> None:
    report = _local_report()
    report["artifact_path_policy"]["policy_id"] = "stale-policy"
    report["artifact_path_policy"]["required_target_paths"]["swe_run_summary"] = "wrong/path.json"
    report["artifact_path_policy"]["reason"] = ""

    errors = validate_m12_final_report(report)

    assert "artifact_path_policy.policy_id must be m12_target_artifact_paths_v1" in errors
    assert "artifact_path_policy.required_target_paths must match canonical M12 target artifact paths" in errors
    assert "artifact_path_policy.reason must be non-empty" in errors


def test_m12_final_report_rejects_artifact_path_section_path_drift() -> None:
    report = _local_report()
    report["swe_probe"]["path"] = "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/stale_swe/run_summary.json"

    errors = validate_m12_final_report(report)

    assert "artifact_paths.swe_run_summary must match embedded section path" in errors


def test_m12_final_report_rejects_stale_empty_command_log_summary() -> None:
    report = _local_report()
    report["command_logs"]["archived_command_ids"] = ["target_preflight"]
    report["command_logs"]["missing_command_log_ids"] = []
    report["command_logs"]["target_run_ids"] = ["stale-target-run"]
    report["command_logs"]["single_target_run_id"] = "stale-target-run"
    report["command_logs"]["command_text_mismatches"] = [
        {
            "command_id": "target_preflight",
            "expected": REQUIRED_COMMAND_LOG_COMMANDS["target_preflight"],
            "observed": "python stale.py",
        }
    ]
    report["command_logs"]["command_count"] = 1
    report["command_logs"]["hash_verified_command_ids"] = ["target_preflight"]
    report["command_logs"]["all_required_logs_archived"] = True
    report["command_logs"]["all_required_commands_passed"] = True

    errors = validate_m12_final_report(report)

    assert "command_logs.archived_command_ids is stale" in errors
    assert "command_logs.missing_command_log_ids is stale" in errors
    assert "command_logs.target_run_ids is stale" in errors
    assert "command_logs.single_target_run_id is stale" in errors
    assert "command_logs.command_text_mismatches is stale" in errors
    assert "command_logs.command_count is stale" in errors
    assert "command_logs.hash_verified_command_ids contains unknown command IDs" in errors
    assert "command_logs.hash_verified_command_ids contains unarchived command IDs" in errors
    assert "command_logs.all_required_logs_archived cannot be true without a readable manifest" in errors
    assert "command_logs.all_required_commands_passed cannot be true without a readable manifest" in errors


def test_m12_final_report_rejects_stale_populated_command_log_summary(tmp_path) -> None:
    manifest_path = _write_complete_command_log_manifest(tmp_path)
    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )
    assert validate_m12_final_report(report) == []

    report["command_logs"]["archived_command_ids"] = report["command_logs"]["archived_command_ids"][:-1]
    report["command_logs"]["missing_command_log_ids"] = ["target_preflight"]
    report["command_logs"]["target_run_ids"] = []
    report["command_logs"]["single_target_run_id"] = None
    report["command_logs"]["command_count"] = 0
    report["command_logs"]["all_required_logs_archived"] = False
    report["command_logs"]["all_required_commands_passed"] = False

    errors = validate_m12_final_report(report)

    assert "command_logs.archived_command_ids is stale" in errors
    assert "command_logs.missing_command_log_ids is stale" in errors
    assert "command_logs.target_run_ids is stale" in errors
    assert "command_logs.single_target_run_id is stale" in errors
    assert "command_logs.command_count is stale" in errors
    assert "command_logs.all_required_logs_archived is stale" in errors
    assert "command_logs.all_required_commands_passed is stale" in errors


def test_m12_final_report_rejects_stale_target_run_identity_summary() -> None:
    report = _local_report()
    report["target_run_identity"]["artifact_target_run_ids"]["swe_run_summary"] = "stale-run"
    report["target_run_identity"]["missing_artifact_target_run_ids"] = []
    report["target_run_identity"]["run_ids_match_command_logs"] = True

    errors = validate_m12_final_report(report)

    assert "target_run_identity artifact_target_run_ids must match embedded artifact sections" in errors
    assert "target_run_identity missing_artifact_target_run_ids is stale" in errors
    assert "target_run_identity run_ids_match_command_logs is stale" in errors


def test_m12_final_report_rejects_empty_or_non_target_remediation_fields() -> None:
    report = _local_report()
    report["missing_gate_remediations"][0]["operator_action"] = ""
    report["missing_gate_remediations"][1]["required_artifact_path"] = "/tmp/not-a-target-artifact.json"
    report["missing_gate_remediations"][2]["target_action_id"] = ""

    errors = validate_m12_final_report(report)

    assert "missing_gate_remediations row 1 requires operator_action" in errors
    assert "missing_gate_remediations row 2 required_artifact_path must be an M12 target artifact path" in errors
    assert "missing_gate_remediations row 3 target_action_id must be non-empty when set" in errors


def test_m12_final_report_remediation_summary_groups_by_target_action() -> None:
    report = _local_report()

    summary = summarize_m12_final_report_remediations(report)

    assert summary["summary_id"] == "bb_zyphra_rl_phase1_m12_final_report_remediation_summary_v1"
    assert summary["claim_boundary"] == "final_report_remediation_summary_not_scorecard_update"
    assert summary["scorecard_update_allowed"] is False
    assert summary["m12_points_awarded"] is False
    assert summary["m12_score_eligible"] is False
    assert summary["missing_gate_count"] == len(report["missing_gates"])
    assert summary["remediation_count"] == len(report["missing_gate_remediations"])
    assert summary["remediation_gates_match_missing_gates"] is True
    actions = {item["target_action_id"]: item for item in summary["next_target_actions"]}
    assert {"final_report", "target_transfer_archive_verify", "target_ray_warm_pool", "target_load_ladder", "target_soak", "m12_test_commands.sh"} <= set(actions)
    assert "archive_verify_report_present" in actions["target_transfer_archive_verify"]["gates"]
    assert TARGET_ARTIFACT_PATHS["archive_verify_report"] in actions["target_transfer_archive_verify"]["required_artifact_paths"]
    assert "command_log_manifest_present" in actions["m12_test_commands.sh"]["gates"]
    assert TARGET_ARTIFACT_PATHS["command_log_manifest"] in actions["m12_test_commands.sh"]["required_artifact_paths"]
    assert validate_m12_final_report_remediation_summary(summary) == []


def test_m12_final_report_remediation_summary_rejects_stale_counts() -> None:
    summary = summarize_m12_final_report_remediations(_local_report())
    summary["missing_gate_count"] += 1
    summary["remediation_count"] -= 1

    errors = validate_m12_final_report_remediation_summary(summary)

    assert "remediation_count must match next_target_actions gate count" in errors
    assert "missing_gate_count must match next_target_actions gate count" in errors
    assert "remediation_count must match missing_gate_count when gates match" in errors


def test_m12_final_report_remediation_summary_rejects_unsafe_action_fields() -> None:
    summary = summarize_m12_final_report_remediations(_local_report())
    summary["next_target_actions"][0]["target_action_id"] = ""
    summary["next_target_actions"][0]["gates"].append("synthetic_unknown_gate")
    summary["next_target_actions"][0]["required_artifact_paths"].append("/tmp/not-target.json")
    summary["next_target_actions"][0]["operator_actions"].append("")

    errors = validate_m12_final_report_remediation_summary(summary)

    assert "next_target_actions row 1 requires target_action_id" in errors
    assert "next_target_actions row 1 has unknown gate: synthetic_unknown_gate" in errors
    assert "next_target_actions row 1 has non-target artifact path: /tmp/not-target.json" in errors
    assert "next_target_actions row 1 has empty operator_action" in errors


def test_m12_final_report_remediation_summary_cli_writes_json(tmp_path) -> None:
    report_path = tmp_path / "m12_final_report.json"
    output_path = tmp_path / "m12_remediation_summary.json"
    report_path.write_text(json.dumps(_local_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/summarize_m12_final_report_remediations.py",
            "--final-report",
            str(report_path),
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "missing_gates=30" in result.stdout
    assert "remediations=30" in result.stdout
    assert "target_transfer_archive_verify" in result.stdout
    summary = json.loads(output_path.read_text(encoding="utf-8"))
    assert summary["remediation_gates_match_missing_gates"] is True
    assert summary["next_target_actions"]


def test_m12_final_report_require_eligible_cli_exits_nonzero_for_local_probe(tmp_path) -> None:
    output_path = tmp_path / "m12_final_report.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/build_m12_final_report.py",
            "--output",
            str(output_path),
            "--preflight-report",
            str(PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json"),
            "--swe-run-summary",
            str(PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json"),
            "--verl-smoke-report",
            str(PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json"),
            "--ray-probe-report",
            str(PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json"),
            "--warm-vs-cold-report",
            str(PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json"),
            "--require-eligible",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode in {1, 4}
    if result.returncode == 4:
        assert output_path.exists()
        assert "score_eligible=False" in result.stdout
    else:
        assert "invalid_m12_final_report" in result.stderr


def test_synthetic_complete_m12_report_validates() -> None:
    target_run_id = "m12-target-run-test"
    report = _local_report()
    report["m12_score_eligible"] = True
    report["missing_gates"] = []
    report["missing_gate_remediations"] = []
    report["artifact_paths"] = dict(TARGET_ARTIFACT_PATHS)
    report["artifact_path_policy"]["paths_match_target_defaults"] = True
    report["archive_verify"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["archive_verify_report"],
        "read_error": None,
        "report_id": "bb_zyphra_rl_phase1_m12_archive_verify_report_v1",
        "claim_boundary": "transfer_archive_verification_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "status": "passed",
        "archive_manifest_id": "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1",
        "archive_claim_boundary": "transfer_archive_only_not_m12_validation",
        "archive_sha256": "sha256:" + ("b" * 64),
        "included_entry_count": 213,
        "all_required_artifacts_present": True,
        "all_transfer_requirements_covered": True,
        "archive_contains_source_overlay": True,
        "archive_deterministic": True,
        "source_paths_portable": True,
        "errors": [],
        "validation_errors": [],
    }
    report["preflight"]["path"] = TARGET_ARTIFACT_PATHS["preflight_report"]
    report["swe_probe"]["path"] = TARGET_ARTIFACT_PATHS["swe_run_summary"]
    report["verl_export"]["path"] = TARGET_ARTIFACT_PATHS["verl_smoke_report"]
    report["ray_probe"]["path"] = TARGET_ARTIFACT_PATHS["ray_probe_report"]
    report["warm_vs_cold"]["path"] = TARGET_ARTIFACT_PATHS["warm_vs_cold_report"]
    report["preflight"]["status"] = "preflight_passed"
    report["preflight"]["target_run_id"] = target_run_id
    report["preflight"]["blockers"] = []
    report["preflight"]["gpu"]["rocminfo_available"] = True
    report["preflight"]["gpu"]["mi300x_product_evidence"] = True
    report["preflight"]["gpu"]["torch_probe"]["device_count"] = 8
    report["preflight"]["gpu"]["torch_probe"]["device_names"] = ["AMD Instinct MI300X"] * 8
    report["preflight"]["python_modules"]["verl"]["available"] = True
    report["preflight"]["python_modules"]["ray"]["available"] = True
    report["preflight"]["filesystem_cas_smoke"]["status"] = "passed"
    fingerprint = report["preflight"]["runtime_fingerprint"]
    fingerprint["tool_presence"] = {
        "rocm_smi": report["preflight"]["gpu"]["rocm_smi_available"],
        "rocminfo": report["preflight"]["gpu"]["rocminfo_available"],
        "docker": report["preflight"]["container_runtimes"]["docker"],
        "gvisor_runsc": report["preflight"]["container_runtimes"]["gvisor_runsc"],
        "firecracker": report["preflight"]["container_runtimes"]["firecracker"],
    }
    fingerprint["python_modules"] = report["preflight"]["python_modules"]
    fingerprint["gpu_summary"] = {
        "rocm_smi_output": report["preflight"]["gpu"]["rocm_smi_output"],
        "torch_probe": report["preflight"]["gpu"]["torch_probe"],
    }
    fingerprint["ray_summary"] = {"status_output": report["preflight"]["ray_cluster"]["status_output"]}
    fingerprint["sha256"] = _fingerprint_sha256(fingerprint)
    report["ray_probe"]["ray_local_mode"] = False
    report["swe_probe"]["target_run_id"] = target_run_id
    report["verl_export"]["target_run_id"] = target_run_id
    report["ray_probe"]["target_run_id"] = target_run_id
    report["warm_vs_cold"]["target_run_id"] = target_run_id
    report["load_ladder"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["load_ladder_report"],
        "target_run_id": target_run_id,
        "validation_errors": [],
        "required_levels": [5, 20, 50],
        "optional_levels": [100],
        "concurrency_levels": [
            {"target_sessions": 5, "status": "passed", "ray_local_mode": False},
            {"target_sessions": 20, "status": "passed", "ray_local_mode": False},
            {"target_sessions": 50, "status": "passed", "ray_local_mode": False},
        ],
        "resource_skips": [{"target_sessions": 100, "reason": "insufficient target maintenance window"}],
        "policy_version_integrity": True,
        "queue_backpressure_integrity": True,
    }
    report["soak"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["soak_report"],
        "target_run_id": target_run_id,
        "validation_errors": [],
        "minimum_duration_seconds": 7200,
        "duration_seconds": 7200,
        "status": "passed",
        "runtime_failure_count": 0,
        "ray_local_mode": False,
    }
    command_entries = [
        {
            "command_id": command_id,
            "status": "passed",
            "exit_code": 0,
            "log_path": f"m12_command_logs/{command_id}.log",
            "sha256": "sha256:" + ("a" * 64),
            "started_at": "2026-06-17T00:00:00Z",
                "completed_at": "2026-06-17T00:00:01Z",
                "target_run_id": target_run_id,
                "command": REQUIRED_COMMAND_LOG_COMMANDS[command_id],
        }
        for command_id in REQUIRED_COMMAND_LOG_IDS
    ]
    report["command_logs"] = {
        "present": True,
        "path": TARGET_ARTIFACT_PATHS["command_log_manifest"],
        "manifest_id": "bb_zyphra_rl_phase1_m12_command_log_manifest_v1",
        "manifest_validation_errors": [],
        "required_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "manifest_required_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "archived_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "hash_verified_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
        "missing_command_log_ids": [],
        "target_run_ids": [target_run_id],
        "single_target_run_id": target_run_id,
        "command_text_mismatches": [],
        "all_required_logs_archived": True,
        "all_required_commands_passed": True,
        "command_count": len(command_entries),
        "commands": command_entries,
    }
    report["target_run_identity"] = {
        "single_command_log_target_run_id": target_run_id,
        "artifact_target_run_ids": {
            "preflight_report": target_run_id,
            "swe_run_summary": target_run_id,
            "verl_smoke_report": target_run_id,
            "ray_probe_report": target_run_id,
            "warm_vs_cold_report": target_run_id,
            "load_ladder_report": target_run_id,
            "soak_report": target_run_id,
        },
        "missing_artifact_target_run_ids": [],
        "mismatched_artifact_target_run_ids": {},
        "run_ids_match_command_logs": True,
    }

    assert validate_m12_final_report(report) == []

    stale_eligibility_report = json.loads(json.dumps(report))
    stale_eligibility_report["m12_score_eligible"] = False
    assert "m12_score_eligible must match final-report embedded evidence gates" in validate_m12_final_report(
        stale_eligibility_report
    )

    local_artifact_path_report = json.loads(json.dumps(report))
    local_artifact_path_report["artifact_paths"]["swe_run_summary"] = (
        "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_summary.json"
    )
    local_artifact_path_report["artifact_path_policy"]["paths_match_target_defaults"] = False
    artifact_path_errors = validate_m12_final_report(local_artifact_path_report)
    assert "eligible report requires target-node default artifact paths" in artifact_path_errors
    assert "eligible report requires artifact_path_policy.paths_match_target_defaults=true" in artifact_path_errors

    missing_fingerprint_report = json.loads(json.dumps(report))
    missing_fingerprint_report["preflight"].pop("runtime_fingerprint")
    assert "eligible report requires preflight runtime fingerprint" in validate_m12_final_report(
        missing_fingerprint_report
    )

    tampered_fingerprint_report = json.loads(json.dumps(report))
    tampered_fingerprint_report["preflight"]["runtime_fingerprint"]["platform"]["machine"] = "tampered-machine"
    assert "eligible report requires preflight runtime fingerprint" in validate_m12_final_report(
        tampered_fingerprint_report
    )

    unsafe_env_fingerprint_report = json.loads(json.dumps(report))
    unsafe_env_fingerprint_report["preflight"]["runtime_fingerprint"]["sanitized_environment"]["values"][
        "OPENAI_API_KEY"
    ] = "should-not-be-here"
    assert "eligible report requires preflight runtime fingerprint" in validate_m12_final_report(
        unsafe_env_fingerprint_report
    )

    unredacted_path_report = json.loads(json.dumps(report))
    unredacted_fp = unredacted_path_report["preflight"]["runtime_fingerprint"]
    unredacted_fp["sanitized_environment"]["values"]["CONDA_DEFAULT_ENV"] = "/tmp/private-conda-env"
    unredacted_fp["sanitized_environment"]["redactions"].pop("CONDA_DEFAULT_ENV", None)
    unredacted_fp["sha256"] = _fingerprint_sha256(unredacted_fp)
    assert "eligible report requires preflight runtime fingerprint" in validate_m12_final_report(
        unredacted_path_report
    )

    extra_top_level_report = json.loads(json.dumps(report))
    extra_top_level_fp = extra_top_level_report["preflight"]["runtime_fingerprint"]
    extra_top_level_fp["unexpected_section"] = {"leak": "not allowed"}
    extra_top_level_fp["sha256"] = _fingerprint_sha256(extra_top_level_fp)
    assert "eligible report requires preflight runtime fingerprint" in validate_m12_final_report(
        extra_top_level_report
    )

    extra_nested_report = json.loads(json.dumps(report))
    extra_nested_fp = extra_nested_report["preflight"]["runtime_fingerprint"]
    extra_nested_fp["sanitized_environment"]["unexpected_field"] = "not allowed"
    extra_nested_fp["sha256"] = _fingerprint_sha256(extra_nested_fp)
    assert "eligible report requires preflight runtime fingerprint" in validate_m12_final_report(
        extra_nested_report
    )

    preflight_validation_error_report = json.loads(json.dumps(report))
    preflight_validation_error_report["preflight"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires preflight validation errors to be empty" in validate_m12_final_report(
        preflight_validation_error_report
    )

    preflight_top_level_drift_report = json.loads(json.dumps(report))
    preflight_top_level_drift_report["preflight"]["container_runtimes"]["docker"] = False
    preflight_top_level_drift_report["preflight"]["container_runtimes"]["gvisor_runsc"] = False
    preflight_top_level_drift_report["preflight"]["container_runtimes"]["firecracker"] = False
    preflight_top_level_drift_errors = validate_m12_final_report(preflight_top_level_drift_report)
    assert "eligible report requires preflight runtime fingerprint" in preflight_top_level_drift_errors
    assert "eligible report requires at least one container runtime" in preflight_top_level_drift_errors

    stale_target_artifact_report = json.loads(json.dumps(report))
    stale_target_artifact_report["target_run_identity"]["artifact_target_run_ids"]["swe_run_summary"] = "stale-run"
    stale_target_artifact_report["target_run_identity"]["mismatched_artifact_target_run_ids"] = {
        "swe_run_summary": {"expected": target_run_id, "observed": "stale-run"}
    }
    stale_target_artifact_report["target_run_identity"]["run_ids_match_command_logs"] = False
    target_identity_errors = validate_m12_final_report(stale_target_artifact_report)
    assert "target_run_identity artifact_target_run_ids must match embedded artifact sections" in target_identity_errors
    assert "target_run_identity mismatched_artifact_target_run_ids is stale" in target_identity_errors
    assert "eligible report requires target artifacts to share command-log target_run_id" in target_identity_errors

    stale_section_target_run_report = json.loads(json.dumps(report))
    stale_section_target_run_report["swe_probe"]["target_run_id"] = "stale-run"
    stale_section_errors = validate_m12_final_report(stale_section_target_run_report)
    assert "target_run_identity artifact_target_run_ids must match embedded artifact sections" in stale_section_errors
    assert "target_run_identity mismatched_artifact_target_run_ids is stale" in stale_section_errors
    assert "eligible report requires swe_run_summary target_run_id to match command logs" in stale_section_errors

    no_inference_report = json.loads(json.dumps(report))
    no_inference_report["preflight"]["inference_engine_feasibility"] = {
        "decision": "blocked_no_vllm_or_sglang",
        "vllm_available": False,
        "sglang_available": False,
    }
    assert "eligible report requires vLLM or SGLang inference-engine availability" in validate_m12_final_report(
        no_inference_report
    )

    no_container_report = json.loads(json.dumps(report))
    no_container_report["preflight"]["container_runtimes"] = {
        "docker": False,
        "gvisor_runsc": False,
        "firecracker": False,
    }
    assert "eligible report requires at least one container runtime" in validate_m12_final_report(
        no_container_report
    )

    load_ladder_validation_error_report = json.loads(json.dumps(report))
    load_ladder_validation_error_report["load_ladder"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires load ladder validation errors to be empty" in validate_m12_final_report(
        load_ladder_validation_error_report
    )

    soak_validation_error_report = json.loads(json.dumps(report))
    soak_validation_error_report["soak"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires soak validation errors to be empty" in validate_m12_final_report(
        soak_validation_error_report
    )

    swe_validation_error_report = json.loads(json.dumps(report))
    swe_validation_error_report["swe_probe"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires SWE run summary validation errors to be empty" in validate_m12_final_report(
        swe_validation_error_report
    )

    verl_validation_error_report = json.loads(json.dumps(report))
    verl_validation_error_report["verl_export"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires VeRL smoke validation errors to be empty" in validate_m12_final_report(
        verl_validation_error_report
    )

    ray_validation_error_report = json.loads(json.dumps(report))
    ray_validation_error_report["ray_probe"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires Ray probe validation errors to be empty" in validate_m12_final_report(
        ray_validation_error_report
    )

    warm_validation_error_report = json.loads(json.dumps(report))
    warm_validation_error_report["warm_vs_cold"]["validation_errors"] = ["synthetic component error"]
    assert "eligible report requires warm-vs-cold validation errors to be empty" in validate_m12_final_report(
        warm_validation_error_report
    )


def test_m12_final_report_verifies_command_log_hashes(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        _write_wrapper_style_log(
            log_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
        )
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id="m12-target-run-test",
        )

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_manifest_present" not in report["missing_gates"]
    assert "command_log_manifest_complete" not in report["missing_gates"]
    assert "command_log_hashes_present" not in report["missing_gates"]
    assert "command_log_hashes_verified" not in report["missing_gates"]
    assert "command_log_commands_passed" not in report["missing_gates"]
    assert "command_log_manifest_valid" not in report["missing_gates"]
    assert "command_log_single_target_run_id" not in report["missing_gates"]
    assert "command_log_required_ids_canonical" not in report["missing_gates"]
    assert report["command_logs"]["manifest_validation_errors"] == []
    assert report["command_logs"]["manifest_required_command_ids"] == list(REQUIRED_COMMAND_LOG_IDS)
    assert report["command_logs"]["hash_verified_command_ids"] == sorted(REQUIRED_COMMAND_LOG_IDS)
    assert report["command_logs"]["single_target_run_id"] == "m12-target-run-test"

    first_log = log_dir / f"{REQUIRED_COMMAND_LOG_IDS[0]}.log"
    first_log.write_text("tampered\n", encoding="utf-8")
    tampered_report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_hashes_verified" in tampered_report["missing_gates"]
    assert "command_log_manifest_valid" in tampered_report["missing_gates"]
    assert tampered_report["command_logs"]["manifest_validation_errors"] == [
        f"attempt log sha256 mismatch: {REQUIRED_COMMAND_LOG_IDS[0]} attempt 1",
        f"log sha256 mismatch: {REQUIRED_COMMAND_LOG_IDS[0]}",
    ]


def test_m12_final_report_rejects_narrowed_command_log_required_ids(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        _write_wrapper_style_log(
            log_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
        )
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id="m12-target-run-test",
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["required_command_ids"] = ["target_preflight"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_required_ids_canonical" in report["missing_gates"]
    assert "command_log_manifest_valid" in report["missing_gates"]
    assert report["command_logs"]["manifest_required_command_ids"] == ["target_preflight"]
    assert report["command_logs"]["manifest_validation_errors"] == [
        "required_command_ids must equal canonical M12 required command IDs"
    ]


def test_m12_final_report_rejects_stale_command_log_summary_flags(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        _write_wrapper_style_log(
            log_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
        )
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id="m12-target-run-test",
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["all_required_logs_archived"] = False
    manifest["all_required_commands_passed"] = False
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_manifest_complete" not in report["missing_gates"]
    assert "command_log_manifest_valid" in report["missing_gates"]
    assert "command_log_hashes_verified" not in report["missing_gates"]
    assert "command_log_commands_passed" not in report["missing_gates"]
    assert "command_log_required_logs_archived_summary" in report["missing_gates"]
    assert "command_log_required_commands_passed_summary" in report["missing_gates"]
    assert report["command_logs"]["manifest_validation_errors"] == [
        "all_required_logs_archived must match required command log rows",
        "all_required_commands_passed must match required command statuses",
    ]


def test_m12_final_report_rejects_missing_attempt_history(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        _write_wrapper_style_log(
            log_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
        )
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id="m12-target-run-test",
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target_entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    target_entry.pop("attempts")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_manifest_valid" in report["missing_gates"]
    assert report["command_logs"]["manifest_validation_errors"] == [
        "completed command entry must preserve attempts: target_preflight"
    ]


def test_m12_final_report_rejects_mixed_target_run_ids(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for index, command_id in enumerate(REQUIRED_COMMAND_LOG_IDS):
        log_path = log_dir / f"{command_id}.log"
        target_run_id = "m12-target-run-a" if index == 0 else "m12-target-run-b"
        _write_wrapper_style_log(
            log_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            target_run_id=target_run_id,
        )
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=REQUIRED_COMMAND_LOG_COMMANDS[command_id],
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id=target_run_id,
        )

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_single_target_run_id" in report["missing_gates"]
    assert report["command_logs"]["single_target_run_id"] is None
    assert report["command_logs"]["target_run_ids"] == ["m12-target-run-a", "m12-target-run-b"]


def test_m12_final_report_rejects_required_command_text_mismatch(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        command = (
            "python -c 'print(\"wrong command\")'"
            if command_id == "target_preflight"
            else REQUIRED_COMMAND_LOG_COMMANDS[command_id]
        )
        _write_wrapper_style_log(log_path, command_id=command_id, command=command)
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command=command,
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-17T00:00:00Z",
            completed_at="2026-06-17T00:00:01Z",
            target_run_id="m12-target-run-test",
        )

    report = build_m12_final_report(
        preflight_report_path=PHASE_RUNS / "m12_target_preflight" / "m12_preflight_report.json",
        swe_run_summary_path=PHASE_RUNS / "m6_controlled_swe_toy" / "run_summary.json",
        verl_smoke_report_path=PHASE_RUNS / "m7_verl_probe" / "smoke_consumer_report.json",
        ray_probe_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "ray_probe_report.json",
        warm_vs_cold_report_path=PHASE_RUNS / "m8_ray_warm_pool_probe" / "warm_vs_cold_report.json",
        command_log_manifest_path=manifest_path,
    )

    assert "command_log_expected_commands_match" in report["missing_gates"]
    assert report["command_logs"]["command_text_mismatches"] == [
        {
            "command_id": "target_preflight",
            "expected": REQUIRED_COMMAND_LOG_COMMANDS["target_preflight"],
            "observed": "python -c 'print(\"wrong command\")'",
        }
    ]
