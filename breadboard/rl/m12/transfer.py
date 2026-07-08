from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from breadboard.rl.m12.command_logs import COMMAND_LOG_ENTRY_TEMPLATES, COMMAND_LOG_MANIFEST_TEMPLATE
from breadboard.rl.m12.final_report import (
    OPTIONAL_COMMAND_LOG_COMMANDS,
    TARGET_ARTIFACT_PATHS,
    TARGET_CLAIM_LEDGER_PATH,
    TARGET_COMMAND_LOG_MANIFEST_PATH,
    TARGET_FINAL_REPORT_PATH,
    TARGET_PROMOTION_AUDIT_PATH,
    TARGET_SCORECARD_PATH,
)


REQUIRED_TRANSFER_ARTIFACTS = [
    "requirements.txt",
    "breadboard/rl",
    "scripts/rl_phase1",
    "tests/__init__.py",
    "tests/test_rl_phase1_scorecard_schema.py",
    "tests/test_rl_phase1_claim_ledger.py",
    "tests/rl",
    "docs/rl_phase1",
    "examples/rl_env_packages",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_summary.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_ledger.jsonl",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/metrics_summary.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/qc_report.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/row_evidence",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.jsonl",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.parquet",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/projection_manifest.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/smoke_consumer_report.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m8_ray_warm_pool_probe/ray_probe_report.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m8_ray_warm_pool_probe/warm_vs_cold_report.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m9_adapter_probes/adapter_probe_summary.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m9_adapter_probes/benchflow.fixture.v1.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m9_adapter_probes/ors.fixture.v1.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m9_adapter_probes/verl.jsonl_probe.v1.json",
    "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m9_adapter_probes/prime_verifiers.fixture.v1.json",
]

TRANSFER_PREP_FILES = [
    "m12_transfer_manifest.json",
    "m12_test_commands.sh",
    "m12_apply_overlay.py",
    "m12_target_bootstrap.sh",
    "m12_rollback_plan.md",
    "m12_readiness_summary.json",
    "m12_load_ladder_report_template.json",
    "m12_soak_report_template.json",
    "m12_command_log_manifest_template.json",
    "m12_transfer_summary.json",
]

M12_LOGGED_COMMAND_IDS = [
    "target_transfer_archive_verify",
    "phase1_validation_suite",
    "target_preflight",
    "target_swe_probe",
    "target_verl_export",
    "target_ray_warm_pool",
    "target_load_ladder",
    "target_soak",
]
M12_FINAL_REPORT_COMMAND = OPTIONAL_COMMAND_LOG_COMMANDS["final_report"]
M12_PROMOTION_AUDIT_COMMAND = OPTIONAL_COMMAND_LOG_COMMANDS["promotion_audit"]
_DEFAULT_COMMAND_LOG_MANIFEST_ARG = "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"
_FINAL_REPORT_OUTPUT_PATH = "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"
_FINAL_REPORT_TARGET_ARGS = (
    ("--output", _FINAL_REPORT_OUTPUT_PATH),
    ("--archive-verify-report", TARGET_ARTIFACT_PATHS["archive_verify_report"]),
    ("--preflight-report", TARGET_ARTIFACT_PATHS["preflight_report"]),
    ("--swe-run-summary", TARGET_ARTIFACT_PATHS["swe_run_summary"]),
    ("--verl-smoke-report", TARGET_ARTIFACT_PATHS["verl_smoke_report"]),
    ("--ray-probe-report", TARGET_ARTIFACT_PATHS["ray_probe_report"]),
    ("--warm-vs-cold-report", TARGET_ARTIFACT_PATHS["warm_vs_cold_report"]),
    ("--load-ladder-report", TARGET_ARTIFACT_PATHS["load_ladder_report"]),
    ("--soak-report", TARGET_ARTIFACT_PATHS["soak_report"]),
)
_FINAL_REPORT_MANIFEST_ARGS = (
    *_FINAL_REPORT_TARGET_ARGS,
    ("--command-log-manifest", TARGET_ARTIFACT_PATHS["command_log_manifest"]),
)
_FINAL_REPORT_SCRIPT_ARGS = (
    *_FINAL_REPORT_TARGET_ARGS,
    ("--command-log-manifest", '"$COMMAND_LOG_MANIFEST"'),
)
_PROMOTION_AUDIT_TARGET_ARGS = (
    ("--output", TARGET_PROMOTION_AUDIT_PATH),
    ("--final-report", TARGET_FINAL_REPORT_PATH),
    ("--scorecard", TARGET_SCORECARD_PATH),
    ("--claim-ledger", TARGET_CLAIM_LEDGER_PATH),
    ("--command-log-manifest", TARGET_COMMAND_LOG_MANIFEST_PATH),
)
_PROMOTION_AUDIT_SCRIPT_ARGS = (
    ("--output", TARGET_PROMOTION_AUDIT_PATH),
    ("--final-report", TARGET_FINAL_REPORT_PATH),
    ("--scorecard", TARGET_SCORECARD_PATH),
    ("--claim-ledger", TARGET_CLAIM_LEDGER_PATH),
    ("--command-log-manifest", '"$COMMAND_LOG_MANIFEST"'),
)
_CLOSEOUT_ARTIFACT_REUSE_GUARD_TEXT = "Existing M12 close-out artifact would make target evidence ambiguous"


def _command_has_flag_value(command: str, flag: str, value: str) -> bool:
    return f"{flag} {value}" in command


def _final_command_has_explicit_target_args(command: str) -> bool:
    return all(_command_has_flag_value(command, flag, value) for flag, value in _FINAL_REPORT_MANIFEST_ARGS)


def _promotion_command_has_explicit_target_args(command: str) -> bool:
    return all(_command_has_flag_value(command, flag, value) for flag, value in _PROMOTION_AUDIT_TARGET_ARGS)


def _build_logged_command_rows() -> list[tuple[str, str]]:
    entries_by_id = {str(entry.get("command_id")): str(entry["command"]) for entry in COMMAND_LOG_ENTRY_TEMPLATES}
    rows: list[tuple[str, str]] = []
    missing_ids: list[str] = []
    for command_id in M12_LOGGED_COMMAND_IDS:
        command = entries_by_id.get(command_id)
        if command is None:
            missing_ids.append(command_id)
            continue
        rows.append((command_id, command))
    if missing_ids:
        raise ValueError(f"M12 logged command template missing ids: {', '.join(missing_ids)}")
    if len(rows) != len(M12_LOGGED_COMMAND_IDS):
        raise ValueError("M12 logged command rows must match M12_LOGGED_COMMAND_IDS")
    return rows


M12_LOGGED_COMMAND_ROWS = _build_logged_command_rows()
M12_LOGGED_COMMANDS = [command for _, command in M12_LOGGED_COMMAND_ROWS]
M12_TEST_COMMAND_ROWS = [
    *M12_LOGGED_COMMAND_ROWS,
    ("final_report", M12_FINAL_REPORT_COMMAND),
    ("promotion_audit", M12_PROMOTION_AUDIT_COMMAND),
]
M12_TEST_COMMANDS = list(M12_LOGGED_COMMANDS) + [M12_FINAL_REPORT_COMMAND, M12_PROMOTION_AUDIT_COMMAND]

EXPECTED_OUTPUTS = [
    "m12_archive_verify/m12_archive_verify_report.json",
    "m12_target_preflight/m12_preflight_report.json",
    "m12_node_swe_probe/run_summary.json",
    "m12_node_swe_probe/run_ledger.jsonl",
    "m12_node_verl_probe/verl_probe_rows.jsonl",
    "m12_node_verl_probe/verl_probe_rows.parquet",
    "m12_node_verl_probe/projection_manifest.json",
    "m12_node_verl_probe/smoke_consumer_report.json",
    "m12_node_ray_probe/ray_probe_report.json",
    "m12_node_ray_probe/warm_vs_cold_report.json",
    "m12_node_load_ladder/load_ladder_report.json",
    "m12_node_soak/soak_report.json",
    "m12_command_logs/command_log_manifest.json",
    "m12_final_report/m12_final_report.json",
    "m12_promotion_audit/m12_promotion_audit.json",
]

LOAD_LADDER_REPORT_TEMPLATE = {
    "report_id": "bb_zyphra_rl_phase1_m12_load_ladder_report_v1",
    "claim_boundary": "target_load_ladder_probe_not_scorecard_update",
    "concurrency_levels": [
        {
            "target_sessions": 5,
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "row_count": None,
            "accepted_count": None,
            "quarantined_count": None,
            "rejected_count": None,
            "p95_total_ms": None,
            "policy_version_integrity": None,
            "queue_backpressure_integrity": None,
            "notes": "",
        },
        {
            "target_sessions": 20,
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "row_count": None,
            "accepted_count": None,
            "quarantined_count": None,
            "rejected_count": None,
            "p95_total_ms": None,
            "policy_version_integrity": None,
            "queue_backpressure_integrity": None,
            "notes": "",
        },
        {
            "target_sessions": 50,
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "row_count": None,
            "accepted_count": None,
            "quarantined_count": None,
            "rejected_count": None,
            "p95_total_ms": None,
            "policy_version_integrity": None,
            "queue_backpressure_integrity": None,
            "notes": "",
        },
        {
            "target_sessions": 100,
            "status": "pending_or_resource_skipped",
            "started_at": None,
            "completed_at": None,
            "row_count": None,
            "accepted_count": None,
            "quarantined_count": None,
            "rejected_count": None,
            "p95_total_ms": None,
            "policy_version_integrity": None,
            "queue_backpressure_integrity": None,
            "notes": "If not run, add a resource_skips entry with target_sessions=100 and a concrete reason.",
        },
    ],
    "resource_skips": [],
    "policy_version_integrity": None,
    "queue_backpressure_integrity": None,
    "operator_notes": "",
}

SOAK_REPORT_TEMPLATE = {
    "report_id": "bb_zyphra_rl_phase1_m12_soak_report_v1",
    "claim_boundary": "target_soak_probe_not_scorecard_update",
    "status": "pending",
    "minimum_duration_seconds": 7200,
    "duration_seconds": None,
    "started_at": None,
    "completed_at": None,
    "runtime_failure_count": None,
    "row_count": None,
    "accepted_count": None,
    "quarantined_count": None,
    "rejected_count": None,
    "max_queue_depth": None,
    "max_worker_restarts": None,
    "operator_notes": "",
}

TRANSFER_REQUIREMENT_COVERAGE = [
    {
        "requirement": "Repo snapshot or commit SHA",
        "covered_by": ["repo.head", "repo.branch", "repo.dirty_status_short"],
    },
    {
        "requirement": "Python environment lock",
        "covered_by": ["requirements.txt"],
    },
    {
        "requirement": "RL Phase 1 source/test/doc overlay",
        "covered_by": [
            "breadboard/rl",
            "scripts/rl_phase1",
            "tests/rl",
            "tests/test_rl_phase1_scorecard_schema.py",
            "tests/test_rl_phase1_claim_ledger.py",
            "docs/rl_phase1",
            "examples/rl_env_packages",
        ],
    },
    {
        "requirement": "ROCm/PyTorch/VeRL/Ray versions",
        "covered_by": ["m12_target_preflight/m12_preflight_report.json"],
    },
    {
        "requirement": "EnvPackage set",
        "covered_by": [
            "examples/rl_env_packages/python_console_toy/env_package.yaml",
            "examples/rl_env_packages/swe_toy_patch/env_package.yaml",
            "examples/rl_env_packages/math_console_toy/env_package.yaml",
        ],
    },
    {
        "requirement": "Run manifests",
        "covered_by": ["test_commands", "docs/rl_phase1/m12_transfer_pack.md"],
    },
    {
        "requirement": "Test command list",
        "covered_by": ["test_commands"],
    },
    {
        "requirement": "Expected outputs",
        "covered_by": ["expected_outputs"],
    },
    {
        "requirement": "Rollback plan",
        "covered_by": ["rollback_plan"],
    },
    {
        "requirement": "Final target report contract",
        "covered_by": [
            "breadboard/rl/m12/final_report.py",
            "scripts/rl_phase1/build_m12_final_report.py",
            "scripts/rl_phase1/summarize_m12_final_report_remediations.py",
            "m12_final_report/m12_final_report.json",
        ],
    },
    {
        "requirement": "Score promotion audit",
        "covered_by": [
            "breadboard/rl/m12/promotion_audit.py",
            "scripts/rl_phase1/audit_m12_score_promotion.py",
            "m12_promotion_audit/m12_promotion_audit.json",
        ],
    },
    {
        "requirement": "Load ladder and soak evidence",
        "covered_by": [
            "breadboard/rl/m12/load_soak.py",
            "scripts/rl_phase1/run_m12_load_ladder.py",
            "scripts/rl_phase1/run_m12_soak.py",
            "m12_node_load_ladder/load_ladder_report.json",
            "m12_node_soak/soak_report.json",
            "breadboard/rl/m12/final_report.py",
        ],
    },
    {
        "requirement": "Raw command log archive",
        "covered_by": [
            "m12_command_logs/command_log_manifest.json",
            "m12_command_log_manifest_template.json",
            "breadboard/rl/m12/command_logs.py",
            "breadboard/rl/m12/final_report.py",
            "scripts/rl_phase1/run_m12_logged_command.py",
        ],
    },
]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _archive_file_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & 0o111 else 0o644


def _gzip_mtime(path: Path) -> int | None:
    header = path.read_bytes()[:8]
    if len(header) < 8 or header[:2] != b"\x1f\x8b":
        return None
    return int.from_bytes(header[4:8], "little")


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _git_output(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=repo_root, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unavailable"


def _artifact_record(repo_root: Path, rel_path: str) -> dict[str, Any]:
    path = (repo_root / rel_path).resolve()
    if not path.exists():
        return {"path": rel_path, "exists": False}
    if path.is_file():
        return {
            "path": rel_path,
            "exists": True,
            "kind": "file",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    files = _iter_artifact_files(path)
    return {
        "path": rel_path,
        "exists": True,
        "kind": "directory",
        "file_count": len(files),
        "size_bytes": sum(item.stat().st_size for item in files),
    }


def _resolve_archive_manifest_path(manifest_path: Path, key: str, fallback_name: str) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = manifest.get(key)
    if not raw:
        return manifest_path.parent / fallback_name
    path = Path(str(raw))
    return path if path.is_absolute() else manifest_path.parent / path


def _portable_colocated_file_name_error(*, key: str, value: str, expected_name: str) -> str | None:
    path = Path(value)
    if not value:
        return f"{key} must be set"
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1 or path.name != expected_name:
        return f"{key} must be portable colocated file name: {expected_name}"
    return None


def validate_m12_transfer_archive_manifest(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"archive manifest is not readable JSON: {type(exc).__name__}: {exc}"]

    archive_name = str(manifest.get("archive_name") or "m12_transfer_evidence_pack.tar.gz")
    archive_path = _resolve_archive_manifest_path(manifest_path, "archive_path", archive_name)
    sha_path = _resolve_archive_manifest_path(manifest_path, "archive_sha256_file", archive_name + ".sha256")
    expected_archive_sha = str(manifest.get("archive_sha256") or "")
    entries = manifest.get("included_entries")
    archive_name_error = _portable_colocated_file_name_error(
        key="archive_name",
        value=archive_name,
        expected_name=Path(archive_name).name,
    )
    if archive_name_error or Path(archive_name).name != archive_name:
        errors.append("archive_name must be portable file name")
    archive_path_error = _portable_colocated_file_name_error(
        key="archive_path",
        value=str(manifest.get("archive_path") or ""),
        expected_name=archive_name,
    )
    if archive_path_error:
        errors.append(archive_path_error)
    sha_path_error = _portable_colocated_file_name_error(
        key="archive_sha256_file",
        value=str(manifest.get("archive_sha256_file") or ""),
        expected_name=archive_name + ".sha256",
    )
    if sha_path_error:
        errors.append(sha_path_error)
    if manifest.get("archive_manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1":
        errors.append("archive_manifest_id must be bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1")
    if manifest.get("claim_boundary") != "transfer_archive_only_not_m12_validation":
        errors.append("claim_boundary must remain transfer_archive_only_not_m12_validation")
    if manifest.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if manifest.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if manifest.get("archive_is_repo_replacement") is not False:
        errors.append("archive_is_repo_replacement must be false")
    if manifest.get("archive_contains_source_overlay") is not True:
        errors.append("archive_contains_source_overlay must be true")
    if manifest.get("archive_excludes_pycache") is not True:
        errors.append("archive_excludes_pycache must be true")
    if manifest.get("archive_deterministic") is not True:
        errors.append("archive_deterministic must be true")
    if manifest.get("source_paths_portable") is not True:
        errors.append("source_paths_portable must be true")
    deterministic_metadata = manifest.get("deterministic_archive_metadata")
    if deterministic_metadata != {
        "gzip_mtime": 0,
        "member_gid": 0,
        "member_gname": "",
        "member_mtime": 0,
        "member_order": "sorted_by_archive_path",
        "member_uid": 0,
        "member_uname": "",
    }:
        errors.append("deterministic_archive_metadata must match normalized archive metadata")
    if not expected_archive_sha.startswith("sha256:"):
        errors.append("archive_sha256 must start with sha256:")
    if not isinstance(entries, list) or not entries:
        errors.append("included_entries must be a non-empty list")
        entries = []
    if manifest.get("included_entry_count") != len(entries):
        errors.append("included_entry_count must equal len(included_entries)")
    generated_files = manifest.get("generated_transfer_files")
    if generated_files != TRANSFER_PREP_FILES:
        errors.append("generated_transfer_files must match TRANSFER_PREP_FILES")
    if not archive_path.is_file():
        errors.append(f"archive file missing: {archive_path}")
    else:
        actual_archive_sha = _sha256_file(archive_path)
        if actual_archive_sha != expected_archive_sha:
            errors.append("archive sha256 mismatch")
        if manifest.get("archive_size_bytes") != archive_path.stat().st_size:
            errors.append("archive_size_bytes does not match archive file")
        gzip_mtime = _gzip_mtime(archive_path)
        if gzip_mtime is not None and gzip_mtime != 0:
            errors.append("archive gzip mtime must be zero")
    if not sha_path.is_file():
        errors.append(f"archive sha256 sidecar missing: {sha_path}")
    else:
        sidecar_first = sha_path.read_text(encoding="utf-8").strip().split()[0]
        if sidecar_first != expected_archive_sha:
            errors.append("archive sha256 sidecar does not match archive manifest")

    entry_paths = [str(entry.get("archive_path") or "") for entry in entries if isinstance(entry, dict)]
    expected_paths = set(entry_paths)
    if len(entry_paths) != len(expected_paths):
        errors.append("included_entries archive_path values must be unique")
    if "" in expected_paths:
        errors.append("included_entries must all have archive_path")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        private_keys = [str(key) for key in entry if str(key).startswith("_")]
        if private_keys:
            errors.append(f"included_entry private keys are not allowed: {','.join(sorted(private_keys))}")
        archive_member = str(entry.get("archive_path") or "")
        source_path = str(entry.get("source_path") or "")
        source_member = Path(source_path)
        if not source_path:
            errors.append("included_entries must all have source_path")
        elif source_member.is_absolute() or ".." in source_member.parts:
            errors.append(f"unsafe included source path: {source_path}")
        elif source_path != archive_member:
            errors.append(f"included_entry source_path must equal archive_path: {archive_member}")
    for archive_member in expected_paths:
        member_path = Path(archive_member)
        if member_path.is_absolute() or ".." in member_path.parts:
            errors.append(f"unsafe archive member path: {archive_member}")
        if "__pycache__" in member_path.parts or member_path.suffix == ".pyc":
            errors.append(f"generated Python cache member is not allowed: {archive_member}")
    if archive_path.is_file():
        try:
            archived_transfer_manifest: bytes | None = None
            archived_test_commands: bytes | None = None
            archived_readiness_summary: bytes | None = None
            archived_transfer_summary: bytes | None = None
            with tarfile.open(archive_path, "r:gz") as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
                member_names = [member.name for member in members]
                tar_paths = {member.name for member in members}
                if len(member_names) != len(tar_paths):
                    errors.append("archive file member paths must be unique")
                if tar_paths != expected_paths:
                    errors.append("archive member list does not match included_entries")
                if member_names != sorted(expected_paths):
                    errors.append("archive member order must match sorted included_entries")
                by_name = {member.name: member for member in members}
                for entry in entries:
                    if not isinstance(entry, dict):
                        errors.append("included_entries must contain objects")
                        continue
                    archive_member = str(entry.get("archive_path") or "")
                    member = by_name.get(archive_member)
                    if member is None:
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        errors.append(f"archive member unreadable: {archive_member}")
                        continue
                    payload = extracted.read()
                    if len(payload) != entry.get("size_bytes"):
                        errors.append(f"archive member size mismatch: {archive_member}")
                    if _sha256_bytes(payload) != entry.get("sha256"):
                        errors.append(f"archive member sha256 mismatch: {archive_member}")
                    if member.mtime != 0:
                        errors.append(f"archive member mtime must be zero: {archive_member}")
                    if member.uid != 0 or member.gid != 0:
                        errors.append(f"archive member uid/gid must be zero: {archive_member}")
                    if member.uname or member.gname:
                        errors.append(f"archive member uname/gname must be empty: {archive_member}")
                    if member.mode != entry.get("mode"):
                        errors.append(f"archive member mode mismatch: {archive_member}")
                    if member.mode not in {0o644, 0o755}:
                        errors.append(f"archive member mode must be normalized: {archive_member}")
                    if archive_member.endswith("/m12_transfer_manifest.json"):
                        archived_transfer_manifest = payload
                    if archive_member.endswith("/m12_test_commands.sh"):
                        archived_test_commands = payload
                    if archive_member.endswith("/m12_readiness_summary.json"):
                        archived_readiness_summary = payload
                    if archive_member.endswith("/m12_transfer_summary.json"):
                        archived_transfer_summary = payload
            errors.extend(
                _validate_archived_m12_transfer_manifest(
                    archived_transfer_manifest=archived_transfer_manifest,
                )
            )
            errors.extend(
                _validate_archived_m12_test_commands_pair(
                    archived_transfer_manifest=archived_transfer_manifest,
                    archived_test_commands=archived_test_commands,
                )
            )
            errors.extend(
                _validate_archived_m12_transfer_summaries(
                    archived_transfer_manifest=archived_transfer_manifest,
                    archived_readiness_summary=archived_readiness_summary,
                    archived_transfer_summary=archived_transfer_summary,
                )
            )
        except Exception as exc:
            errors.append(f"archive file is not readable tar.gz: {type(exc).__name__}: {exc}")

    for generated_name in TRANSFER_PREP_FILES:
        if not any(str(path).endswith("/" + generated_name) for path in expected_paths):
            errors.append(f"generated transfer file missing from archive: {generated_name}")
    return errors


def _validate_archived_m12_test_commands_pair(
    *,
    archived_transfer_manifest: bytes | None,
    archived_test_commands: bytes | None,
) -> list[str]:
    errors: list[str] = []
    if archived_transfer_manifest is None:
        errors.append("archived m12_transfer_manifest.json missing")
        return errors
    if archived_test_commands is None:
        errors.append("archived m12_test_commands.sh missing")
        return errors
    try:
        transfer_manifest = json.loads(archived_transfer_manifest.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_manifest.json is not readable JSON: {type(exc).__name__}: {exc}"]
    try:
        test_commands = archived_test_commands.decode("utf-8")
    except Exception as exc:
        return [f"archived m12_test_commands.sh is not readable UTF-8: {type(exc).__name__}: {exc}"]
    return [f"archived m12_test_commands.sh invalid: {error}" for error in validate_m12_test_commands_script(test_commands, transfer_manifest)]


def _validate_archived_m12_transfer_manifest(*, archived_transfer_manifest: bytes | None) -> list[str]:
    if archived_transfer_manifest is None:
        return ["archived m12_transfer_manifest.json missing"]
    try:
        transfer_manifest = json.loads(archived_transfer_manifest.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_manifest.json is not readable JSON: {type(exc).__name__}: {exc}"]
    if not isinstance(transfer_manifest, dict):
        return ["archived m12_transfer_manifest.json must be an object"]

    errors: list[str] = []
    if transfer_manifest.get("manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_manifest_v1":
        errors.append(
            "archived m12_transfer_manifest.json invalid: "
            "manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
        )
    if transfer_manifest.get("claim_boundary") != "transfer_preparation_only_not_m12_validation":
        errors.append(
            "archived m12_transfer_manifest.json invalid: "
            "claim_boundary must remain transfer_preparation_only_not_m12_validation"
        )
    repo = transfer_manifest.get("repo")
    if not isinstance(repo, dict):
        errors.append("archived m12_transfer_manifest.json invalid: repo must be an object")
    elif repo.get("root_path_portable") is not True:
        errors.append("archived m12_transfer_manifest.json invalid: repo.root_path_portable must be true")
    if transfer_manifest.get("all_required_artifacts_present") is not True:
        errors.append("archived m12_transfer_manifest.json invalid: all_required_artifacts_present must be true")
    if transfer_manifest.get("all_transfer_requirements_covered") is not True:
        errors.append("archived m12_transfer_manifest.json invalid: all_transfer_requirements_covered must be true")
    if transfer_manifest.get("expected_outputs") != EXPECTED_OUTPUTS:
        errors.append("archived m12_transfer_manifest.json invalid: expected_outputs must match EXPECTED_OUTPUTS")
    return errors


def _validate_archived_m12_transfer_summaries(
    *,
    archived_transfer_manifest: bytes | None,
    archived_readiness_summary: bytes | None,
    archived_transfer_summary: bytes | None,
) -> list[str]:
    errors: list[str] = []
    if archived_transfer_manifest is None:
        errors.append("archived m12_transfer_manifest.json missing")
        return errors
    if archived_readiness_summary is None:
        errors.append("archived m12_readiness_summary.json missing")
        return errors
    if archived_transfer_summary is None:
        errors.append("archived m12_transfer_summary.json missing")
        return errors
    try:
        transfer_manifest = json.loads(archived_transfer_manifest.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_manifest.json is not readable JSON: {type(exc).__name__}: {exc}"]
    try:
        readiness_summary = json.loads(archived_readiness_summary.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_readiness_summary.json is not readable JSON: {type(exc).__name__}: {exc}"]
    try:
        transfer_summary = json.loads(archived_transfer_summary.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_summary.json is not readable JSON: {type(exc).__name__}: {exc}"]
    if not isinstance(transfer_manifest, dict):
        return ["archived m12_transfer_manifest.json must be an object"]
    if not isinstance(readiness_summary, dict):
        errors.append("archived m12_readiness_summary.json must be an object")
    else:
        errors.extend(
            f"archived m12_readiness_summary.json invalid: {error}"
            for error in validate_m12_readiness_summary(readiness_summary, transfer_manifest)
        )
    if not isinstance(transfer_summary, dict):
        errors.append("archived m12_transfer_summary.json must be an object")
    else:
        errors.extend(
            f"archived m12_transfer_summary.json invalid: {error}"
            for error in validate_m12_transfer_summary(transfer_summary, transfer_manifest)
        )
    return errors


def _iter_artifact_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if _include_artifact_file(path) else []
    if path.is_dir():
        return sorted(item for item in path.rglob("*") if item.is_file() and _include_artifact_file(item))
    return []


def _include_artifact_file(path: Path) -> bool:
    return "__pycache__" not in path.parts and path.suffix != ".pyc"


def _archive_name(
    *,
    workspace_root: Path,
    path: Path,
    fallback_root: Path | None = None,
    fallback_prefix: str = "external",
) -> str:
    resolved = path.resolve()
    try:
        return "workspace/" + resolved.relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        if fallback_root is not None:
            try:
                return fallback_prefix.strip("/") + "/" + resolved.relative_to(fallback_root.resolve()).as_posix()
            except ValueError:
                pass
        return fallback_prefix.strip("/") + "/" + resolved.name


def build_m12_transfer_manifest(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    artifacts = [_artifact_record(repo_root, rel_path) for rel_path in REQUIRED_TRANSFER_ARTIFACTS]
    return {
        "manifest_id": "bb_zyphra_rl_phase1_m12_transfer_manifest_v1",
        "claim_boundary": "transfer_preparation_only_not_m12_validation",
        "repo": {
            "root": repo_root.name,
            "root_path_portable": True,
            "head": _git_output(repo_root, "rev-parse", "HEAD"),
            "branch": _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_status_short": _git_output(repo_root, "status", "--short"),
        },
        "artifacts": artifacts,
        "all_required_artifacts_present": all(item.get("exists") for item in artifacts),
        "transfer_requirement_coverage": list(TRANSFER_REQUIREMENT_COVERAGE),
        "all_transfer_requirements_covered": True,
        "test_commands": list(M12_TEST_COMMANDS),
        "expected_outputs": list(EXPECTED_OUTPUTS),
        "rollback_plan": [
            "Stop Ray workers and runtime processes.",
            "Preserve run directories before cleanup.",
            "Archive preflight, run reports, and raw command logs with sha256 hashes.",
            "If load/soak artifacts are missing or non-eligible, preserve the final report as a blocked target outcome.",
            "Build and validate m12_final_report.json before any scorecard edit.",
            "Do not update scorecard unless M12 target evidence satisfies the gate.",
        ],
    }


def build_m12_readiness_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    commands = [str(command) for command in manifest.get("test_commands") or []]
    preflight_command = next((command for command in commands if "run_m12_preflight.py" in command), "")
    final_command = next((command for command in commands if "build_m12_final_report.py" in command), "")
    promotion_command = next((command for command in commands if "audit_m12_score_promotion.py" in command), "")
    generated_script = build_m12_test_commands_script()
    script_errors = validate_m12_test_commands_script(generated_script, manifest)
    return {
        "summary_id": "bb_zyphra_rl_phase1_m12_readiness_summary_v1",
        "claim_boundary": "transfer_readiness_summary_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "artifact_count": len(manifest.get("artifacts") or []),
        "command_count": len(manifest.get("test_commands") or []),
        "expected_output_count": len(manifest.get("expected_outputs") or []),
        "all_required_artifacts_present": bool(manifest.get("all_required_artifacts_present")),
        "all_transfer_requirements_covered": bool(manifest.get("all_transfer_requirements_covered")),
        "target_script_fail_closed": {
            "archive_verifier_runs_first": bool(commands) and "verify_m12_transfer_archive.py" in commands[0],
            "preflight_requires_pass": "--require-pass" in preflight_command,
            "final_report_requires_eligible": "--require-eligible" in final_command,
            "final_report_uses_explicit_target_artifact_args": _final_command_has_explicit_target_args(final_command),
            "promotion_audit_requires_ready": "--require-ready" in promotion_command,
            "promotion_audit_uses_explicit_score_inputs": (
                "--scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml" in promotion_command
                and "--claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
                in promotion_command
            ),
            "promotion_audit_uses_explicit_target_paths": _promotion_command_has_explicit_target_args(
                promotion_command
            ),
            "generated_script_uses_logged_command_wrapper": True,
            "generated_script_runs_concrete_load_soak": True,
            "bootstrap_rejects_dirty_checkout_by_default": True,
            "bootstrap_runs_overlaid_test_commands": True,
            "bootstrap_cds_to_repo_root_before_handoff": True,
            "generated_script_sets_target_run_id": True,
            "generated_script_rejects_mixed_target_run_logs": (
                "Existing M12 command log manifest belongs to different target run id(s)" in generated_script
            ),
            "generated_script_rejects_stale_closeout_artifacts": (
                _CLOSEOUT_ARTIFACT_REUSE_GUARD_TEXT in generated_script
            ),
            "generated_script_summarizes_final_report_remediations_on_error": (
                "trap m12_on_error ERR" in generated_script
                and "summarize_m12_final_report_remediations.py" in generated_script
            ),
            "generated_script_manifest_consistent": not script_errors,
        },
        "generated_script_validation_errors": script_errors,
        "target_only_required_outputs": [
            "m12_node_load_ladder/load_ladder_report.json",
            "m12_node_soak/soak_report.json",
            "m12_command_logs/command_log_manifest.json",
            "m12_final_report/m12_final_report.json",
            "m12_promotion_audit/m12_promotion_audit.json",
        ],
        "score_promotion_rule": (
            "Do not edit the scorecard unless target m12_final_report.json has m12_score_eligible=true, "
            "raw command logs are archived with final-report-verified sha256 hashes, and the scorecard edit is separately reviewed."
        ),
        "known_local_blockers": [
            "rocm_tools_unavailable",
            "torch_device_count_below_8",
            "verl_unavailable",
        ],
    }


def build_m12_transfer_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    commands = [str(command) for command in manifest.get("test_commands") or []]
    preflight_command = next((command for command in commands if "run_m12_preflight.py" in command), "")
    final_command = next((command for command in commands if "build_m12_final_report.py" in command), "")
    promotion_command = next((command for command in commands if "audit_m12_score_promotion.py" in command), "")
    generated_script = build_m12_test_commands_script()
    script_errors = validate_m12_test_commands_script(generated_script, manifest)
    return {
        "manifest_id": manifest["manifest_id"],
        "artifacts_present": manifest["all_required_artifacts_present"],
        "artifact_count": len(manifest["artifacts"]),
        "command_count": len(manifest["test_commands"]),
        "concrete_load_soak_scripts": True,
        "claim_boundary": manifest["claim_boundary"],
        "expected_output_count": len(manifest["expected_outputs"]),
        "archive_verifier_runs_first": bool(commands) and "verify_m12_transfer_archive.py" in commands[0],
        "preflight_command_require_pass": "--require-pass" in preflight_command,
        "final_command_require_eligible": "--require-eligible" in final_command,
        "final_command_explicit_target_artifact_args": _final_command_has_explicit_target_args(final_command),
        "promotion_audit_require_ready": "--require-ready" in promotion_command,
        "promotion_audit_explicit_score_inputs": (
            "--scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml" in promotion_command
            and "--claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"
            in promotion_command
        ),
        "promotion_audit_explicit_target_paths": _promotion_command_has_explicit_target_args(promotion_command),
        "load_soak_command_log_templates": True,
        "logged_command_wrapper": True,
        "bootstrap_dirty_checkout_guard": True,
        "bootstrap_overlaid_test_commands_handoff": True,
        "bootstrap_repo_root_cwd_handoff": True,
        "target_run_id_command_binding": True,
        "target_run_log_reuse_guard": (
            "Existing M12 command log manifest belongs to different target run id(s)" in generated_script
        ),
        "target_closeout_artifact_reuse_guard": _CLOSEOUT_ARTIFACT_REUSE_GUARD_TEXT in generated_script,
        "final_report_failure_remediation_summary": (
            "trap m12_on_error ERR" in generated_script
            and "summarize_m12_final_report_remediations.py" in generated_script
        ),
        "generated_script_manifest_consistent": not script_errors,
        "generated_script_validation_errors": script_errors,
        "readiness_summary": "m12_readiness_summary.json",
        "all_transfer_requirements_covered": manifest["all_transfer_requirements_covered"],
    }


def validate_m12_readiness_summary(summary: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = build_m12_readiness_summary(manifest)
    if summary.get("summary_id") != "bb_zyphra_rl_phase1_m12_readiness_summary_v1":
        errors.append("summary_id must be bb_zyphra_rl_phase1_m12_readiness_summary_v1")
    if summary.get("claim_boundary") != "transfer_readiness_summary_not_m12_validation":
        errors.append("claim_boundary must remain transfer_readiness_summary_not_m12_validation")
    if summary.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if summary.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    for field in [
        "artifact_count",
        "command_count",
        "expected_output_count",
        "all_required_artifacts_present",
        "all_transfer_requirements_covered",
        "target_only_required_outputs",
        "score_promotion_rule",
        "known_local_blockers",
    ]:
        if summary.get(field) != expected.get(field):
            errors.append(f"{field} must match transfer manifest")
    fail_closed = summary.get("target_script_fail_closed")
    expected_fail_closed = expected["target_script_fail_closed"]
    if not isinstance(fail_closed, dict):
        errors.append("target_script_fail_closed must be an object")
    else:
        if set(fail_closed) != set(expected_fail_closed):
            errors.append("target_script_fail_closed keys must match expected fail-closed checks")
        for field, expected_value in expected_fail_closed.items():
            if fail_closed.get(field) != expected_value:
                errors.append(f"target_script_fail_closed.{field} must match transfer manifest")
    if summary.get("generated_script_validation_errors") != expected.get("generated_script_validation_errors"):
        errors.append("generated_script_validation_errors must match generated target script validation")
    return errors


def validate_m12_transfer_summary(summary: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = build_m12_transfer_summary(manifest)
    if summary.get("manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_manifest_v1":
        errors.append("manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1")
    if summary.get("claim_boundary") != "transfer_preparation_only_not_m12_validation":
        errors.append("claim_boundary must remain transfer_preparation_only_not_m12_validation")
    for field in [
        "artifacts_present",
        "artifact_count",
        "command_count",
        "expected_output_count",
        "archive_verifier_runs_first",
        "preflight_command_require_pass",
        "final_command_require_eligible",
        "final_command_explicit_target_artifact_args",
        "promotion_audit_require_ready",
        "promotion_audit_explicit_score_inputs",
        "promotion_audit_explicit_target_paths",
        "load_soak_command_log_templates",
        "logged_command_wrapper",
        "bootstrap_dirty_checkout_guard",
        "bootstrap_overlaid_test_commands_handoff",
        "bootstrap_repo_root_cwd_handoff",
        "target_run_id_command_binding",
        "target_run_log_reuse_guard",
        "target_closeout_artifact_reuse_guard",
        "final_report_failure_remediation_summary",
        "generated_script_manifest_consistent",
        "generated_script_validation_errors",
        "readiness_summary",
        "all_transfer_requirements_covered",
    ]:
        if summary.get(field) != expected.get(field):
            errors.append(f"{field} must match transfer manifest")
    if summary.get("concrete_load_soak_scripts") is not True:
        errors.append("concrete_load_soak_scripts must be true")
    return errors


def _logged_command_line(command_id: str, command: str) -> str:
    return (
        'python scripts/rl_phase1/run_m12_logged_command.py --manifest "$COMMAND_LOG_MANIFEST" '
        f'--log-dir "$COMMAND_LOG_DIR" --command-id {command_id} --target-run-id "$M12_TARGET_RUN_ID" -- {command}'
    )


def _script_command(command_id: str, command: str) -> str:
    if command_id in {"final_report", "promotion_audit"}:
        return command.replace(_DEFAULT_COMMAND_LOG_MANIFEST_ARG, '"$COMMAND_LOG_MANIFEST"')
    return command


def build_m12_test_commands_script() -> str:
    command_lines = [
        _logged_command_line(command_id, _script_command(command_id, command))
        for command_id, command in M12_TEST_COMMAND_ROWS
    ]
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        'REPO_ROOT="${REPO_ROOT:-$(pwd)}"\n'
        'if [[ ! -d "$REPO_ROOT/breadboard/rl" ]]; then\n'
        '  echo "Set REPO_ROOT to the BreadBoard repository root before running M12 commands." >&2\n'
        "  exit 2\n"
        "fi\n"
        'cd "$REPO_ROOT"\n\n'
        'COMMAND_LOG_DIR="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs"\n'
        'COMMAND_LOG_MANIFEST="$COMMAND_LOG_DIR/command_log_manifest.json"\n'
        'M12_FINAL_REPORT_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"\n'
        'M12_REMEDIATION_SUMMARY_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_remediation_summary.json"\n'
        'M12_PROMOTION_AUDIT_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"\n'
        'M12_TARGET_RUN_ID="${M12_TARGET_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"\n'
        'mkdir -p "$COMMAND_LOG_DIR"\n\n'
        'if [[ -f "$COMMAND_LOG_MANIFEST" ]]; then\n'
        '  python - "$COMMAND_LOG_MANIFEST" "$M12_TARGET_RUN_ID" <<\'PY\'\n'
        'import json\n'
        'import sys\n'
        'from pathlib import Path\n\n'
        'manifest_path = Path(sys.argv[1])\n'
        'target_run_id = sys.argv[2]\n'
        'try:\n'
        '    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))\n'
        'except Exception as exc:\n'
        '    print(f"Existing M12 command log manifest is unreadable: {exc}", file=sys.stderr)\n'
        '    sys.exit(3)\n'
        'seen_ids = set()\n'
        'for value in manifest.get("target_run_ids") or []:\n'
        '    if value:\n'
        '        seen_ids.add(str(value))\n'
        'for row in manifest.get("commands") or []:\n'
        '    if not isinstance(row, dict):\n'
        '        continue\n'
        '    if row.get("target_run_id"):\n'
        '        seen_ids.add(str(row["target_run_id"]))\n'
        '    for attempt in row.get("attempts") or []:\n'
        '        if isinstance(attempt, dict) and attempt.get("target_run_id"):\n'
        '            seen_ids.add(str(attempt["target_run_id"]))\n'
        'foreign_ids = sorted(value for value in seen_ids if value != target_run_id)\n'
        'if foreign_ids:\n'
        '    joined = ", ".join(foreign_ids)\n'
        '    print(\n'
        '        "Existing M12 command log manifest belongs to different target run id(s): "\n'
        '        f"{joined}. Set M12_TARGET_RUN_ID to resume that run or archive/remove "\n'
        '        "runs/m12_command_logs before starting a new target run.",\n'
        '        file=sys.stderr,\n'
        '    )\n'
        '    sys.exit(3)\n'
        'PY\n'
        'fi\n\n'
        'for existing_artifact in "$M12_FINAL_REPORT_PATH" "$M12_REMEDIATION_SUMMARY_PATH" "$M12_PROMOTION_AUDIT_PATH"; do\n'
        '  if [[ -e "$existing_artifact" ]]; then\n'
        '    echo "Existing M12 close-out artifact would make target evidence ambiguous: $existing_artifact. Archive/remove runs/m12_final_report and runs/m12_promotion_audit before starting a new target run." >&2\n'
        '    exit 3\n'
        '  fi\n'
        'done\n\n'
        'm12_on_error() {\n'
        '  local exit_code="$?"\n'
        '  if [[ -f "$M12_FINAL_REPORT_PATH" ]]; then\n'
        '    echo "m12_final_report_remediation_summary_attempt=1" >&2\n'
        '    python scripts/rl_phase1/summarize_m12_final_report_remediations.py \\\n'
        '      --final-report "$M12_FINAL_REPORT_PATH" \\\n'
        '      --output "$M12_REMEDIATION_SUMMARY_PATH" || true\n'
        '  fi\n'
        '  exit "$exit_code"\n'
        '}\n'
        'trap m12_on_error ERR\n\n'
        'echo "m12_target_run_id=$M12_TARGET_RUN_ID"\n\n'
        + "\n".join(command_lines)
        + "\n"
    )


def validate_m12_test_commands_script(script_text: str, manifest: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    command_rows = list(M12_TEST_COMMAND_ROWS)
    command_lines = [
        line.strip()
        for line in script_text.splitlines()
        if "scripts/rl_phase1/run_m12_logged_command.py" in line and "--command-id" in line
    ]
    if len(command_lines) != len(command_rows):
        errors.append("m12_test_commands.sh logged command count must match M12_TEST_COMMAND_ROWS")
    if 'set -euo pipefail' not in script_text:
        errors.append("m12_test_commands.sh must fail closed with set -euo pipefail")
    if 'COMMAND_LOG_MANIFEST="$COMMAND_LOG_DIR/command_log_manifest.json"' not in script_text:
        errors.append("m12_test_commands.sh must define COMMAND_LOG_MANIFEST under COMMAND_LOG_DIR")
    if (
        'M12_FINAL_REPORT_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"'
        not in script_text
    ):
        errors.append("m12_test_commands.sh must define M12_FINAL_REPORT_PATH")
    if (
        'M12_REMEDIATION_SUMMARY_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_remediation_summary.json"'
        not in script_text
    ):
        errors.append("m12_test_commands.sh must define M12_REMEDIATION_SUMMARY_PATH")
    if (
        'M12_PROMOTION_AUDIT_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"'
        not in script_text
    ):
        errors.append("m12_test_commands.sh must define M12_PROMOTION_AUDIT_PATH")
    if 'M12_TARGET_RUN_ID="${M12_TARGET_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"' not in script_text:
        errors.append("m12_test_commands.sh must define M12_TARGET_RUN_ID")
    if "Existing M12 command log manifest belongs to different target run id(s)" not in script_text:
        errors.append("m12_test_commands.sh must reject mixed target-run command logs")
    if _CLOSEOUT_ARTIFACT_REUSE_GUARD_TEXT not in script_text:
        errors.append("m12_test_commands.sh must reject stale close-out artifacts")
    if "trap m12_on_error ERR" not in script_text:
        errors.append("m12_test_commands.sh must install ERR trap for final-report remediation summary")
    if "scripts/rl_phase1/summarize_m12_final_report_remediations.py" not in script_text:
        errors.append("m12_test_commands.sh must summarize final-report remediations on failure")
    final_report_lines = [line for line in command_lines if "--command-id final_report" in line]
    if not final_report_lines:
        errors.append("m12_test_commands.sh must include logged final_report command")
    else:
        final_report_line = final_report_lines[0]
        for flag, value in _FINAL_REPORT_SCRIPT_ARGS:
            if not _command_has_flag_value(final_report_line, flag, value):
                errors.append(f"final_report command must pass explicit {flag} path")
    promotion_lines = [line for line in command_lines if "--command-id promotion_audit" in line]
    if not promotion_lines:
        errors.append("m12_test_commands.sh must include logged promotion_audit command")
    else:
        promotion_line = promotion_lines[0]
        for flag, value in _PROMOTION_AUDIT_SCRIPT_ARGS:
            if not _command_has_flag_value(promotion_line, flag, value):
                errors.append(f"promotion_audit command must pass explicit {flag} path")
        if "--require-ready" not in promotion_line:
            errors.append("promotion_audit command must require ready evidence")
    for index, (command_id, command) in enumerate(command_rows):
        expected_line = _logged_command_line(command_id, _script_command(command_id, command))
        if index >= len(command_lines):
            errors.append(f"missing logged command line: {command_id}")
            continue
        actual_line = command_lines[index]
        if actual_line != expected_line:
            errors.append(f"logged command line mismatch at position {index + 1}: {command_id}")
        if f"--command-id {command_id}" not in actual_line:
            errors.append(f"logged command line missing command_id: {command_id}")
        if '--target-run-id "$M12_TARGET_RUN_ID"' not in actual_line:
            errors.append(f"logged command line missing target run binding: {command_id}")
    if manifest is not None:
        if manifest.get("test_commands") != list(M12_TEST_COMMANDS):
            errors.append("transfer manifest test_commands must match M12_TEST_COMMANDS")
        if len(manifest.get("test_commands") or []) != len(command_rows):
            errors.append("transfer manifest test_commands count must match M12_TEST_COMMAND_ROWS")
    return errors


def _standalone_overlay_script() -> str:
    return '''#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile


EXPECTED_OUTPUTS = [
    "m12_archive_verify/m12_archive_verify_report.json",
    "m12_target_preflight/m12_preflight_report.json",
    "m12_node_swe_probe/run_summary.json",
    "m12_node_swe_probe/run_ledger.jsonl",
    "m12_node_verl_probe/verl_probe_rows.jsonl",
    "m12_node_verl_probe/verl_probe_rows.parquet",
    "m12_node_verl_probe/projection_manifest.json",
    "m12_node_verl_probe/smoke_consumer_report.json",
    "m12_node_ray_probe/ray_probe_report.json",
    "m12_node_ray_probe/warm_vs_cold_report.json",
    "m12_node_load_ladder/load_ladder_report.json",
    "m12_node_soak/soak_report.json",
    "m12_command_logs/command_log_manifest.json",
    "m12_final_report/m12_final_report.json",
    "m12_promotion_audit/m12_promotion_audit.json",
]
FINAL_REPORT_EXPLICIT_TARGET_ARGS = [
    ("--output", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"),
    ("--archive-verify-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json"),
    ("--preflight-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight/m12_preflight_report.json"),
    ("--swe-run-summary", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe/run_summary.json"),
    ("--verl-smoke-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_verl_probe/smoke_consumer_report.json"),
    ("--ray-probe-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/ray_probe_report.json"),
    ("--warm-vs-cold-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/warm_vs_cold_report.json"),
    ("--load-ladder-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json"),
    ("--soak-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json"),
    ("--command-log-manifest", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"),
]
PROMOTION_AUDIT_EXPLICIT_TARGET_ARGS = [
    ("--output", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"),
    ("--final-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"),
    ("--scorecard", "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"),
    ("--claim-ledger", "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"),
    ("--command-log-manifest", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"),
]
PROMOTION_AUDIT_EXPLICIT_SCRIPT_ARGS = [
    ("--output", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"),
    ("--final-report", "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"),
    ("--scorecard", "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml"),
    ("--claim-ledger", "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md"),
    ("--command-log-manifest", '"$COMMAND_LOG_MANIFEST"'),
]


def final_command_has_explicit_target_args(command: str) -> bool:
    return all(f"{flag} {value}" in command for flag, value in FINAL_REPORT_EXPLICIT_TARGET_ARGS)


def promotion_command_has_explicit_target_args(command: str) -> bool:
    return all(f"{flag} {value}" in command for flag, value in PROMOTION_AUDIT_EXPLICIT_TARGET_ARGS)


def promotion_script_line_has_explicit_target_args(command: str) -> bool:
    return all(f"{flag} {value}" in command for flag, value in PROMOTION_AUDIT_EXPLICIT_SCRIPT_ARGS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def gzip_mtime(path: Path) -> int | None:
    header = path.read_bytes()[:8]
    if len(header) < 8 or header[:2] != b"\\x1f\\x8b":
        return None
    return int.from_bytes(header[4:8], "little")


def resolve_path(manifest_path: Path, manifest: dict, key: str, fallback_name: str) -> Path:
    raw = manifest.get(key)
    if not raw:
        return manifest_path.parent / fallback_name
    path = Path(str(raw))
    return path if path.is_absolute() else manifest_path.parent / path


def portable_colocated_file_name_error(key: str, value: str, expected_name: str) -> str | None:
    path = Path(value)
    if not value:
        return f"{key} must be set"
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1 or path.name != expected_name:
        return f"{key} must be portable colocated file name: {expected_name}"
    return None


def destination_for(workspace_root: Path, archive_path: str) -> Path:
    member_path = Path(archive_path)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"unsafe archive member path: {archive_path}")
    if "__pycache__" in member_path.parts or member_path.suffix == ".pyc":
        raise ValueError(f"generated Python cache member is not allowed: {archive_path}")
    if not member_path.parts:
        raise ValueError(f"archive member must be rooted under workspace/ or m12_transfer_prep/: {archive_path}")
    if member_path.parts[0] == "workspace":
        relative = Path(*member_path.parts[1:])
    elif member_path.parts[0] == "m12_transfer_prep":
        relative = Path("docs_tmp", "ZYPHRA", "RL_PHASE_1", "runs", *member_path.parts)
    else:
        raise ValueError(f"archive member must be rooted under workspace/ or m12_transfer_prep/: {archive_path}")
    destination = (workspace_root / relative).resolve()
    workspace_root = workspace_root.resolve()
    try:
        destination.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"archive member escapes workspace root: {archive_path}") from exc
    return destination


def blocking_parent_for(path: Path, workspace_root: Path) -> Path | None:
    parent = path.parent
    workspace_root = workspace_root.resolve()
    while True:
        if parent.exists():
            return None if parent.is_dir() else parent
        if parent == workspace_root or parent.parent == parent:
            return None
        parent = parent.parent


M12_TEST_COMMAND_IDS = [
    "target_transfer_archive_verify",
    "phase1_validation_suite",
    "target_preflight",
    "target_swe_probe",
    "target_verl_export",
    "target_ray_warm_pool",
    "target_load_ladder",
    "target_soak",
    "final_report",
    "promotion_audit",
]
DEFAULT_COMMAND_LOG_MANIFEST_ARG = "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"
GENERATED_TRANSFER_FILES = [
    "m12_transfer_manifest.json",
    "m12_test_commands.sh",
    "m12_apply_overlay.py",
    "m12_target_bootstrap.sh",
    "m12_rollback_plan.md",
    "m12_readiness_summary.json",
    "m12_load_ladder_report_template.json",
    "m12_soak_report_template.json",
    "m12_command_log_manifest_template.json",
    "m12_transfer_summary.json",
]


def script_command(command_id: str, command: str) -> str:
    if command_id in {"final_report", "promotion_audit"}:
        return command.replace(DEFAULT_COMMAND_LOG_MANIFEST_ARG, '"$COMMAND_LOG_MANIFEST"')
    return command


def logged_command_line(command_id: str, command: str) -> str:
    return (
        'python scripts/rl_phase1/run_m12_logged_command.py --manifest "$COMMAND_LOG_MANIFEST" '
        f'--log-dir "$COMMAND_LOG_DIR" --command-id {command_id} --target-run-id "$M12_TARGET_RUN_ID" -- {command}'
    )


def validate_archived_test_commands_pair(
    *,
    transfer_manifest_payload: bytes | None,
    test_commands_payload: bytes | None,
) -> list[str]:
    errors: list[str] = []
    if transfer_manifest_payload is None:
        errors.append("archived m12_transfer_manifest.json missing")
        return errors
    if test_commands_payload is None:
        errors.append("archived m12_test_commands.sh missing")
        return errors
    try:
        transfer_manifest = json.loads(transfer_manifest_payload.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_manifest.json is not readable JSON: {type(exc).__name__}: {exc}"]
    try:
        test_commands = test_commands_payload.decode("utf-8")
    except Exception as exc:
        return [f"archived m12_test_commands.sh is not readable UTF-8: {type(exc).__name__}: {exc}"]

    manifest_commands = transfer_manifest.get("test_commands")
    if not isinstance(manifest_commands, list):
        errors.append("archived transfer manifest test_commands must be a list")
        manifest_commands = []
    if len(manifest_commands) != len(M12_TEST_COMMAND_IDS):
        errors.append("archived transfer manifest test_commands count must match M12_TEST_COMMAND_IDS")
    command_lines = [
        line.strip()
        for line in test_commands.splitlines()
        if "scripts/rl_phase1/run_m12_logged_command.py" in line and "--command-id" in line
    ]
    if len(command_lines) != len(M12_TEST_COMMAND_IDS):
        errors.append("archived m12_test_commands.sh logged command count must match M12_TEST_COMMAND_IDS")
    if "set -euo pipefail" not in test_commands:
        errors.append("archived m12_test_commands.sh must fail closed with set -euo pipefail")
    if 'COMMAND_LOG_MANIFEST="$COMMAND_LOG_DIR/command_log_manifest.json"' not in test_commands:
        errors.append("archived m12_test_commands.sh must define COMMAND_LOG_MANIFEST under COMMAND_LOG_DIR")
    if 'M12_TARGET_RUN_ID="${M12_TARGET_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"' not in test_commands:
        errors.append("archived m12_test_commands.sh must define M12_TARGET_RUN_ID")
    if "Existing M12 command log manifest belongs to different target run id(s)" not in test_commands:
        errors.append("archived m12_test_commands.sh must reject mixed target-run command logs")
    if "Existing M12 close-out artifact would make target evidence ambiguous" not in test_commands:
        errors.append("archived m12_test_commands.sh must reject stale close-out artifacts")
    for index, command_id in enumerate(M12_TEST_COMMAND_IDS):
        if index >= len(command_lines) or index >= len(manifest_commands):
            continue
        expected_line = logged_command_line(command_id, script_command(command_id, str(manifest_commands[index])))
        actual_line = command_lines[index]
        if actual_line != expected_line:
            errors.append(f"archived logged command line mismatch at position {index + 1}: {command_id}")
        if f"--command-id {command_id}" not in actual_line:
            errors.append(f"archived logged command line missing command_id: {command_id}")
        if '--target-run-id "$M12_TARGET_RUN_ID"' not in actual_line:
            errors.append(f"archived logged command line missing target run binding: {command_id}")
    promotion_lines = [line for line in command_lines if "--command-id promotion_audit" in line]
    if not promotion_lines:
        errors.append("archived m12_test_commands.sh must include promotion_audit command")
    elif not promotion_script_line_has_explicit_target_args(promotion_lines[0]):
        errors.append("archived promotion_audit command must use explicit target output/input paths")
    if promotion_lines and "--require-ready" not in promotion_lines[0]:
        errors.append("archived promotion_audit command must require ready evidence")
    return errors


def validate_archived_transfer_manifest(*, transfer_manifest_payload: bytes | None) -> list[str]:
    if transfer_manifest_payload is None:
        return ["archived m12_transfer_manifest.json missing"]
    try:
        transfer_manifest = json.loads(transfer_manifest_payload.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_manifest.json is not readable JSON: {type(exc).__name__}: {exc}"]
    if not isinstance(transfer_manifest, dict):
        return ["archived m12_transfer_manifest.json must be an object"]
    errors: list[str] = []
    if transfer_manifest.get("manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_manifest_v1":
        errors.append(
            "archived m12_transfer_manifest.json invalid: "
            "manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
        )
    if transfer_manifest.get("claim_boundary") != "transfer_preparation_only_not_m12_validation":
        errors.append(
            "archived m12_transfer_manifest.json invalid: "
            "claim_boundary must remain transfer_preparation_only_not_m12_validation"
        )
    repo = transfer_manifest.get("repo")
    if not isinstance(repo, dict):
        errors.append("archived m12_transfer_manifest.json invalid: repo must be an object")
    elif repo.get("root_path_portable") is not True:
        errors.append("archived m12_transfer_manifest.json invalid: repo.root_path_portable must be true")
    if transfer_manifest.get("all_required_artifacts_present") is not True:
        errors.append("archived m12_transfer_manifest.json invalid: all_required_artifacts_present must be true")
    if transfer_manifest.get("all_transfer_requirements_covered") is not True:
        errors.append("archived m12_transfer_manifest.json invalid: all_transfer_requirements_covered must be true")
    if transfer_manifest.get("expected_outputs") != EXPECTED_OUTPUTS:
        errors.append("archived m12_transfer_manifest.json invalid: expected_outputs must match EXPECTED_OUTPUTS")
    return errors


def validate_archived_transfer_summaries(
    *,
    transfer_manifest_payload: bytes | None,
    readiness_summary_payload: bytes | None,
    transfer_summary_payload: bytes | None,
) -> list[str]:
    errors: list[str] = []
    if transfer_manifest_payload is None:
        errors.append("archived m12_transfer_manifest.json missing")
        return errors
    if readiness_summary_payload is None:
        errors.append("archived m12_readiness_summary.json missing")
        return errors
    if transfer_summary_payload is None:
        errors.append("archived m12_transfer_summary.json missing")
        return errors
    try:
        transfer_manifest = json.loads(transfer_manifest_payload.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_manifest.json is not readable JSON: {type(exc).__name__}: {exc}"]
    try:
        readiness = json.loads(readiness_summary_payload.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_readiness_summary.json is not readable JSON: {type(exc).__name__}: {exc}"]
    try:
        transfer_summary = json.loads(transfer_summary_payload.decode("utf-8"))
    except Exception as exc:
        return [f"archived m12_transfer_summary.json is not readable JSON: {type(exc).__name__}: {exc}"]
    if not isinstance(transfer_manifest, dict):
        return ["archived m12_transfer_manifest.json must be an object"]
    if not isinstance(readiness, dict):
        errors.append("archived m12_readiness_summary.json must be an object")
        readiness = {}
    if not isinstance(transfer_summary, dict):
        errors.append("archived m12_transfer_summary.json must be an object")
        transfer_summary = {}
    if readiness.get("summary_id") != "bb_zyphra_rl_phase1_m12_readiness_summary_v1":
        errors.append(
            "archived m12_readiness_summary.json invalid: "
            "summary_id must be bb_zyphra_rl_phase1_m12_readiness_summary_v1"
        )
    if readiness.get("claim_boundary") != "transfer_readiness_summary_not_m12_validation":
        errors.append(
            "archived m12_readiness_summary.json invalid: "
            "claim_boundary must remain transfer_readiness_summary_not_m12_validation"
        )
    if readiness.get("scorecard_update_allowed") is not False:
        errors.append("archived m12_readiness_summary.json invalid: scorecard_update_allowed must be false")
    if readiness.get("m12_points_awarded") is not False:
        errors.append("archived m12_readiness_summary.json invalid: m12_points_awarded must be false")
    if transfer_summary.get("manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_manifest_v1":
        errors.append(
            "archived m12_transfer_summary.json invalid: "
            "manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
        )
    if transfer_summary.get("claim_boundary") != "transfer_preparation_only_not_m12_validation":
        errors.append(
            "archived m12_transfer_summary.json invalid: "
            "claim_boundary must remain transfer_preparation_only_not_m12_validation"
        )
    commands = [str(command) for command in transfer_manifest.get("test_commands") or []]
    preflight_command = next((command for command in commands if "run_m12_preflight.py" in command), "")
    final_command = next((command for command in commands if "build_m12_final_report.py" in command), "")
    promotion_command = next((command for command in commands if "audit_m12_score_promotion.py" in command), "")
    counts = {
        "artifact_count": len(transfer_manifest.get("artifacts") or []),
        "command_count": len(transfer_manifest.get("test_commands") or []),
        "expected_output_count": len(transfer_manifest.get("expected_outputs") or []),
    }
    for field, expected in counts.items():
        if readiness.get(field) != expected:
            errors.append(f"archived m12_readiness_summary.json invalid: {field} must match transfer manifest")
        if transfer_summary.get(field) != expected:
            errors.append(f"archived m12_transfer_summary.json invalid: {field} must match transfer manifest")
    if readiness.get("all_required_artifacts_present") != bool(transfer_manifest.get("all_required_artifacts_present")):
        errors.append("archived m12_readiness_summary.json invalid: all_required_artifacts_present must match transfer manifest")
    if readiness.get("all_transfer_requirements_covered") != bool(transfer_manifest.get("all_transfer_requirements_covered")):
        errors.append("archived m12_readiness_summary.json invalid: all_transfer_requirements_covered must match transfer manifest")
    if transfer_summary.get("artifacts_present") != transfer_manifest.get("all_required_artifacts_present"):
        errors.append("archived m12_transfer_summary.json invalid: artifacts_present must match transfer manifest")
    if transfer_summary.get("all_transfer_requirements_covered") != transfer_manifest.get("all_transfer_requirements_covered"):
        errors.append("archived m12_transfer_summary.json invalid: all_transfer_requirements_covered must match transfer manifest")
    target_only_outputs = [
        "m12_node_load_ladder/load_ladder_report.json",
        "m12_node_soak/soak_report.json",
        "m12_command_logs/command_log_manifest.json",
        "m12_final_report/m12_final_report.json",
        "m12_promotion_audit/m12_promotion_audit.json",
    ]
    if readiness.get("target_only_required_outputs") != target_only_outputs:
        errors.append("archived m12_readiness_summary.json invalid: target_only_required_outputs must match expected target outputs")
    score_promotion_rule = (
        "Do not edit the scorecard unless target m12_final_report.json has m12_score_eligible=true, "
        "raw command logs are archived with final-report-verified sha256 hashes, and the scorecard edit is separately reviewed."
    )
    if readiness.get("score_promotion_rule") != score_promotion_rule:
        errors.append("archived m12_readiness_summary.json invalid: score_promotion_rule must match expected rule")
    if readiness.get("known_local_blockers") != [
        "rocm_tools_unavailable",
        "torch_device_count_below_8",
        "verl_unavailable",
    ]:
        errors.append("archived m12_readiness_summary.json invalid: known_local_blockers must match expected local blockers")
    fail_closed = readiness.get("target_script_fail_closed") if isinstance(readiness.get("target_script_fail_closed"), dict) else {}
    fail_closed_expectations = {
        "archive_verifier_runs_first": bool(commands) and "verify_m12_transfer_archive.py" in commands[0],
        "preflight_requires_pass": "--require-pass" in preflight_command,
        "final_report_requires_eligible": "--require-eligible" in final_command,
        "final_report_uses_explicit_target_artifact_args": final_command_has_explicit_target_args(final_command),
        "promotion_audit_requires_ready": "--require-ready" in promotion_command,
        "promotion_audit_uses_explicit_score_inputs": (
            "--scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml" in promotion_command
            and "--claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md" in promotion_command
        ),
        "promotion_audit_uses_explicit_target_paths": promotion_command_has_explicit_target_args(promotion_command),
        "generated_script_uses_logged_command_wrapper": True,
        "generated_script_runs_concrete_load_soak": True,
        "bootstrap_rejects_dirty_checkout_by_default": True,
        "bootstrap_runs_overlaid_test_commands": True,
        "bootstrap_cds_to_repo_root_before_handoff": True,
        "generated_script_sets_target_run_id": True,
        "generated_script_rejects_mixed_target_run_logs": True,
        "generated_script_rejects_stale_closeout_artifacts": True,
        "generated_script_summarizes_final_report_remediations_on_error": True,
        "generated_script_manifest_consistent": True,
    }
    if set(fail_closed) != set(fail_closed_expectations):
        errors.append(
            "archived m12_readiness_summary.json invalid: "
            "target_script_fail_closed keys must match expected fail-closed checks"
        )
    for field, expected in fail_closed_expectations.items():
        if fail_closed.get(field) != expected:
            errors.append(f"archived m12_readiness_summary.json invalid: target_script_fail_closed.{field} must match transfer manifest")
    transfer_expectations = {
        "archive_verifier_runs_first": bool(commands) and "verify_m12_transfer_archive.py" in commands[0],
        "preflight_command_require_pass": "--require-pass" in preflight_command,
        "final_command_require_eligible": "--require-eligible" in final_command,
        "final_command_explicit_target_artifact_args": final_command_has_explicit_target_args(final_command),
        "promotion_audit_require_ready": "--require-ready" in promotion_command,
        "promotion_audit_explicit_score_inputs": fail_closed_expectations["promotion_audit_uses_explicit_score_inputs"],
        "promotion_audit_explicit_target_paths": fail_closed_expectations[
            "promotion_audit_uses_explicit_target_paths"
        ],
        "load_soak_command_log_templates": True,
        "logged_command_wrapper": True,
        "bootstrap_dirty_checkout_guard": True,
        "bootstrap_overlaid_test_commands_handoff": True,
        "bootstrap_repo_root_cwd_handoff": True,
        "target_run_id_command_binding": True,
        "target_run_log_reuse_guard": fail_closed_expectations[
            "generated_script_rejects_mixed_target_run_logs"
        ],
        "target_closeout_artifact_reuse_guard": fail_closed_expectations[
            "generated_script_rejects_stale_closeout_artifacts"
        ],
        "final_report_failure_remediation_summary": True,
        "generated_script_manifest_consistent": True,
        "readiness_summary": "m12_readiness_summary.json",
        "concrete_load_soak_scripts": True,
    }
    for field, expected in transfer_expectations.items():
        if transfer_summary.get(field) != expected:
            errors.append(f"archived m12_transfer_summary.json invalid: {field} must match transfer manifest")
    if readiness.get("generated_script_validation_errors") != []:
        errors.append("archived m12_readiness_summary.json invalid: generated_script_validation_errors must be empty")
    if transfer_summary.get("generated_script_validation_errors") != []:
        errors.append("archived m12_transfer_summary.json invalid: generated_script_validation_errors must be empty")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the M12 transfer overlay after verifying archive hashes.")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent / "m12_transfer_archive_manifest.json")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd().parent)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "m12_overlay_apply_report.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    workspace_root = args.workspace_root.resolve()
    errors: list[str] = []
    written_count = 0
    entries: list[dict] = []
    payloads_by_destination: dict[str, bytes] = {}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        manifest = {}
        errors.append(f"archive manifest is not readable JSON: {type(exc).__name__}: {exc}")

    archive_name = str(manifest.get("archive_name") or "m12_transfer_evidence_pack.tar.gz")
    archive_name_error = portable_colocated_file_name_error(
        "archive_name",
        archive_name,
        Path(archive_name).name,
    )
    if archive_name_error or Path(archive_name).name != archive_name:
        errors.append("archive_name must be portable file name")
    archive_path_error = portable_colocated_file_name_error(
        "archive_path",
        str(manifest.get("archive_path") or ""),
        archive_name,
    )
    if archive_path_error:
        errors.append(archive_path_error)
    sha_path_error = portable_colocated_file_name_error(
        "archive_sha256_file",
        str(manifest.get("archive_sha256_file") or ""),
        archive_name + ".sha256",
    )
    if sha_path_error:
        errors.append(sha_path_error)

    archive_path = resolve_path(
        manifest_path,
        manifest,
        "archive_path",
        archive_name,
    )
    sha_path = resolve_path(
        manifest_path,
        manifest,
        "archive_sha256_file",
        archive_name + ".sha256",
    )
    expected_sha = str(manifest.get("archive_sha256") or "")
    included = manifest.get("included_entries")
    if manifest.get("archive_manifest_id") != "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1":
        errors.append("archive_manifest_id must be bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1")
    if manifest.get("claim_boundary") != "transfer_archive_only_not_m12_validation":
        errors.append("claim_boundary must remain transfer_archive_only_not_m12_validation")
    if manifest.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if manifest.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if manifest.get("archive_is_repo_replacement") is not False:
        errors.append("archive_is_repo_replacement must be false")
    if manifest.get("archive_contains_source_overlay") is not True:
        errors.append("archive_contains_source_overlay must be true")
    if manifest.get("archive_excludes_pycache") is not True:
        errors.append("archive_excludes_pycache must be true")
    if manifest.get("archive_deterministic") is not True:
        errors.append("archive_deterministic must be true")
    if manifest.get("source_paths_portable") is not True:
        errors.append("source_paths_portable must be true")
    deterministic_metadata = manifest.get("deterministic_archive_metadata")
    if deterministic_metadata != {
        "gzip_mtime": 0,
        "member_gid": 0,
        "member_gname": "",
        "member_mtime": 0,
        "member_order": "sorted_by_archive_path",
        "member_uid": 0,
        "member_uname": "",
    }:
        errors.append("deterministic_archive_metadata must match normalized archive metadata")
    if not expected_sha.startswith("sha256:"):
        errors.append("archive_sha256 must start with sha256:")
    if not isinstance(included, list) or not included:
        errors.append("included_entries must be a non-empty list")
        included = []
    if manifest.get("included_entry_count") != len(included):
        errors.append("included_entry_count must equal len(included_entries)")
    if manifest.get("generated_transfer_files") != GENERATED_TRANSFER_FILES:
        errors.append("generated_transfer_files must match GENERATED_TRANSFER_FILES")
    if not archive_path.is_file():
        errors.append(f"archive file missing: {archive_path}")
    else:
        if sha256_file(archive_path) != expected_sha:
            errors.append("archive sha256 mismatch")
        if manifest.get("archive_size_bytes") != archive_path.stat().st_size:
            errors.append("archive_size_bytes does not match archive file")
        observed_gzip_mtime = gzip_mtime(archive_path)
        if observed_gzip_mtime is not None and observed_gzip_mtime != 0:
            errors.append("archive gzip mtime must be zero")
    if not sha_path.is_file():
        errors.append(f"archive sha256 sidecar missing: {sha_path}")
    else:
        sidecar_parts = sha_path.read_text(encoding="utf-8").strip().split()
        sidecar_first = sidecar_parts[0] if sidecar_parts else ""
        if sidecar_first != expected_sha:
            errors.append("archive sha256 sidecar does not match archive manifest")

    expected_paths = set()
    included_archive_paths = []
    destinations = set()
    for item in included:
        if not isinstance(item, dict):
            errors.append("included_entries must contain objects")
            continue
        archive_member = str(item.get("archive_path") or "")
        private_keys = [str(key) for key in item if str(key).startswith("_")]
        if private_keys:
            errors.append(f"included_entry private keys are not allowed: {','.join(sorted(private_keys))}")
        source_path = str(item.get("source_path") or "")
        source_member = Path(source_path)
        if not source_path:
            errors.append("included_entries must all have source_path")
        elif source_member.is_absolute() or ".." in source_member.parts:
            errors.append(f"unsafe included source path: {source_path}")
        elif source_path != archive_member:
            errors.append(f"included_entry source_path must equal archive_path: {archive_member}")
        included_archive_paths.append(archive_member)
        expected_paths.add(archive_member)
        try:
            destination = destination_for(workspace_root, archive_member)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if str(destination) in destinations:
            errors.append(f"duplicate overlay destination: {destination}")
            continue
        destinations.add(str(destination))
        exists = destination.exists()
        blocking_parent = blocking_parent_for(destination, workspace_root)
        if blocking_parent is not None:
            errors.append(f"destination parent exists and is not directory: {blocking_parent}")
        if exists and destination.is_dir():
            errors.append(f"destination exists and is directory: {destination}")
        if exists and args.apply and not args.allow_overwrite:
            errors.append(f"destination exists and allow_overwrite is false: {destination}")
        entries.append(
            {
                "archive_path": archive_member,
                "destination_path": str(destination),
                "exists": exists,
                "size_bytes": item.get("size_bytes"),
                "mode": item.get("mode"),
                "sha256": item.get("sha256"),
            }
        )
    if len(included_archive_paths) != len(set(included_archive_paths)):
        errors.append("included_entries archive_path values must be unique")
    for generated_name in GENERATED_TRANSFER_FILES:
        if not any(str(path).endswith("/" + generated_name) for path in expected_paths):
            errors.append(f"generated transfer file missing from archive: {generated_name}")

    if archive_path.is_file() and not errors:
        transfer_manifest_payload = None
        test_commands_payload = None
        readiness_summary_payload = None
        transfer_summary_payload = None
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                member_list = [member for member in archive.getmembers() if member.isfile()]
                member_names = [member.name for member in member_list]
                members = {member.name: member for member in member_list}
                if len(member_names) != len(set(member_names)):
                    errors.append("archive file member paths must be unique")
                if set(members) != expected_paths:
                    errors.append("archive member list does not match included_entries")
                if member_names != sorted(expected_paths):
                    errors.append("archive member order must match sorted included_entries")
                else:
                    for entry in entries:
                        member = members.get(str(entry["archive_path"]))
                        if member is None:
                            errors.append(f"archive member missing: {entry['archive_path']}")
                            break
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            errors.append(f"archive member unreadable: {entry['archive_path']}")
                            break
                        payload = extracted.read()
                        if len(payload) != entry["size_bytes"]:
                            errors.append(f"archive member size mismatch: {entry['archive_path']}")
                            break
                        if sha256_bytes(payload) != entry["sha256"]:
                            errors.append(f"archive member sha256 mismatch: {entry['archive_path']}")
                            break
                        if member.mtime != 0:
                            errors.append(f"archive member mtime must be zero: {entry['archive_path']}")
                            break
                        if member.uid != 0 or member.gid != 0:
                            errors.append(f"archive member uid/gid must be zero: {entry['archive_path']}")
                            break
                        if member.uname or member.gname:
                            errors.append(f"archive member uname/gname must be empty: {entry['archive_path']}")
                            break
                        if member.mode != entry["mode"]:
                            errors.append(f"archive member mode mismatch: {entry['archive_path']}")
                            break
                        if member.mode not in {0o644, 0o755}:
                            errors.append(f"archive member mode must be normalized: {entry['archive_path']}")
                            break
                        if str(entry["archive_path"]).endswith("/m12_transfer_manifest.json"):
                            transfer_manifest_payload = payload
                        if str(entry["archive_path"]).endswith("/m12_test_commands.sh"):
                            test_commands_payload = payload
                        if str(entry["archive_path"]).endswith("/m12_readiness_summary.json"):
                            readiness_summary_payload = payload
                        if str(entry["archive_path"]).endswith("/m12_transfer_summary.json"):
                            transfer_summary_payload = payload
                        payloads_by_destination[str(entry["destination_path"])] = payload
        except Exception as exc:
            errors.append(f"archive file is not readable tar.gz: {type(exc).__name__}: {exc}")
        if not errors:
            errors.extend(
                validate_archived_transfer_manifest(
                    transfer_manifest_payload=transfer_manifest_payload,
                )
            )
            errors.extend(
                validate_archived_test_commands_pair(
                    transfer_manifest_payload=transfer_manifest_payload,
                    test_commands_payload=test_commands_payload,
                )
            )
            errors.extend(
                validate_archived_transfer_summaries(
                    transfer_manifest_payload=transfer_manifest_payload,
                    readiness_summary_payload=readiness_summary_payload,
                    transfer_summary_payload=transfer_summary_payload,
                )
            )
        if args.apply and not errors:
            for destination_path, payload in payloads_by_destination.items():
                destination = Path(destination_path)
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(payload)
                except Exception as exc:
                    errors.append(f"overlay write failed: {destination}: {type(exc).__name__}: {exc}")
                    break
                written_count += 1

    report = {
        "report_id": "bb_zyphra_rl_phase1_m12_overlay_apply_report_v1",
        "claim_boundary": "transfer_overlay_application_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "dry_run": not args.apply,
        "allow_overwrite": args.allow_overwrite,
        "workspace_root": str(workspace_root),
        "archive_manifest": str(manifest_path),
        "archive_path": str(archive_path),
        "status": "passed" if not errors else "failed",
        "would_write_count": len(entries),
        "written_count": written_count,
        "existing_destination_count": sum(1 for entry in entries if entry["exists"]),
        "errors": errors,
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    print(
        f"report={report['report_id']} status={report['status']} dry_run={report['dry_run']} "
        f"would_write={report['would_write_count']} written={report['written_count']} "
        f"existing_destinations={report['existing_destination_count']} errors={len(errors)}"
    )
    if errors:
        for error in errors:
            print(f"error={error}")
        raise SystemExit(6)


if __name__ == "__main__":
    main()
'''


def _target_bootstrap_script() -> str:
    return '''#!/usr/bin/env bash
set -euo pipefail

PREP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(pwd)}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$REPO_ROOT/.." && pwd)}"
TRANSFER_MANIFEST="$PREP_DIR/m12_transfer_manifest.json"
ARCHIVE_MANIFEST="$PREP_DIR/m12_transfer_archive_manifest.json"
OVERLAY_DRY_RUN_REPORT="$PREP_DIR/m12_overlay_apply_dry_run_report.json"
OVERLAY_APPLY_REPORT="$PREP_DIR/m12_overlay_apply_report.json"
TARGET_PREP_DIR="$WORKSPACE_ROOT/docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep"
TARGET_TEST_COMMANDS="$TARGET_PREP_DIR/m12_test_commands.sh"

if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "REPO_ROOT must point to the exact BreadBoard git checkout before M12 bootstrap." >&2
  exit 2
fi
if [[ ! -f "$TRANSFER_MANIFEST" ]]; then
  echo "Missing transfer manifest: $TRANSFER_MANIFEST" >&2
  exit 2
fi
if [[ ! -f "$ARCHIVE_MANIFEST" ]]; then
  echo "Missing archive manifest: $ARCHIVE_MANIFEST" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
  else
    echo "python3 or python is required for M12 bootstrap." >&2
    exit 2
  fi
fi

EXPECTED_HEAD="$("$PYTHON_BIN" - "$TRANSFER_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print((manifest.get("repo") or {}).get("head") or "")
PY
)"
ACTUAL_HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [[ -z "$EXPECTED_HEAD" || "$EXPECTED_HEAD" == "unavailable" ]]; then
  echo "Transfer manifest does not record a usable repo HEAD." >&2
  exit 7
fi
if [[ "$ACTUAL_HEAD" != "$EXPECTED_HEAD" ]]; then
  echo "Repo HEAD mismatch. expected=$EXPECTED_HEAD actual=$ACTUAL_HEAD" >&2
  exit 7
fi
REPO_STATUS_SHORT="$(git -C "$REPO_ROOT" status --short)"
REPO_STATUS_LINES="$(printf "%s\n" "$REPO_STATUS_SHORT" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ -n "$REPO_STATUS_SHORT" && "${ALLOW_M12_DIRTY_CHECKOUT:-0}" != "1" ]]; then
  echo "Repo checkout is dirty before M12 overlay. Refusing to continue; set ALLOW_M12_DIRTY_CHECKOUT=1 only for local rehearsal/debugging." >&2
  echo "dirty_status_lines=$REPO_STATUS_LINES" >&2
  exit 7
fi
if [[ -n "$REPO_STATUS_SHORT" ]]; then
  echo "repo_dirty_check=override dirty_status_lines=$REPO_STATUS_LINES"
else
  echo "repo_dirty_check=clean dirty_status_lines=0"
fi

"$PYTHON_BIN" "$PREP_DIR/m12_apply_overlay.py" \
  --manifest "$ARCHIVE_MANIFEST" \
  --workspace-root "$WORKSPACE_ROOT" \
  --output "$OVERLAY_DRY_RUN_REPORT"

if [[ "${BOOTSTRAP_DRY_RUN_ONLY:-0}" == "1" ]]; then
  echo "bootstrap_dry_run_only=true repo_head_verified=true overlay_dry_run_report=$OVERLAY_DRY_RUN_REPORT"
  exit 0
fi

"$PYTHON_BIN" "$PREP_DIR/m12_apply_overlay.py" \
  --manifest "$ARCHIVE_MANIFEST" \
  --workspace-root "$WORKSPACE_ROOT" \
  --output "$OVERLAY_APPLY_REPORT" \
  --apply \
  --allow-overwrite

mkdir -p "$TARGET_PREP_DIR"
for transfer_artifact in \
  m12_transfer_archive_manifest.json \
  m12_transfer_evidence_pack.tar.gz \
  m12_transfer_evidence_pack.tar.gz.sha256; do
  if [[ ! -f "$PREP_DIR/$transfer_artifact" ]]; then
    echo "Missing target transfer artifact before command handoff: $PREP_DIR/$transfer_artifact" >&2
    exit 8
  fi
  cp -f "$PREP_DIR/$transfer_artifact" "$TARGET_PREP_DIR/$transfer_artifact"
done

if [[ -n "${M12_LOCAL_TEST_FIXTURES_ZIP:-}" ]]; then
  if [[ ! -f "$M12_LOCAL_TEST_FIXTURES_ZIP" ]]; then
    echo "M12_LOCAL_TEST_FIXTURES_ZIP does not exist: $M12_LOCAL_TEST_FIXTURES_ZIP" >&2
    exit 8
  fi
  M12_FIXTURE_WORKSPACE="$WORKSPACE_ROOT" M12_FIXTURE_ZIP="$M12_LOCAL_TEST_FIXTURES_ZIP" "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
import zipfile
from pathlib import Path

fixture_zip = Path(os.environ["M12_FIXTURE_ZIP"])
roots = [
    Path(os.environ["M12_FIXTURE_WORKSPACE"]),
    Path("/Users/kylemccleary/projects/breadboard"),
    Path("/shared_folders/querylake_server/ray_testing/ray_SCE"),
]
for root in roots:
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(fixture_zip) as zf:
        zf.extractall(root)
PY
fi

if [[ ! -f "$TARGET_TEST_COMMANDS" ]]; then
  echo "Missing overlaid M12 test command script after overlay apply: $TARGET_TEST_COMMANDS" >&2
  exit 8
fi
if [[ ! -r "$TARGET_TEST_COMMANDS" ]]; then
  echo "Overlaid M12 test command script is not readable after overlay apply: $TARGET_TEST_COMMANDS" >&2
  exit 8
fi

cd "$REPO_ROOT"
REPO_ROOT="$REPO_ROOT" bash "$TARGET_TEST_COMMANDS"
'''


def write_m12_transfer_pack(*, repo_root: Path, output_dir: Path) -> dict[str, Any]:
    manifest = build_m12_transfer_manifest(repo_root)
    test_commands_script = build_m12_test_commands_script()
    script_errors = validate_m12_test_commands_script(test_commands_script, manifest)
    if script_errors:
        raise ValueError("invalid M12 test command script: " + "; ".join(script_errors))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "m12_transfer_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "m12_test_commands.sh").write_text(
        test_commands_script,
        encoding="utf-8",
    )
    overlay_script = output_dir / "m12_apply_overlay.py"
    overlay_script.write_text(_standalone_overlay_script(), encoding="utf-8")
    overlay_script.chmod(0o755)
    bootstrap_script = output_dir / "m12_target_bootstrap.sh"
    bootstrap_script.write_text(_target_bootstrap_script(), encoding="utf-8")
    bootstrap_script.chmod(0o755)
    (output_dir / "m12_rollback_plan.md").write_text(
        "# M12 Rollback Plan\n\n"
        + "\n".join(f"- {item}" for item in manifest["rollback_plan"])
        + "\n",
        encoding="utf-8",
    )
    readiness_summary = build_m12_readiness_summary(manifest)
    readiness_errors = validate_m12_readiness_summary(readiness_summary, manifest)
    if readiness_errors:
        raise ValueError("invalid M12 readiness summary: " + "; ".join(readiness_errors))
    (output_dir / "m12_readiness_summary.json").write_text(
        json.dumps(readiness_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    transfer_summary = build_m12_transfer_summary(manifest)
    transfer_summary_errors = validate_m12_transfer_summary(transfer_summary, manifest)
    if transfer_summary_errors:
        raise ValueError("invalid M12 transfer summary: " + "; ".join(transfer_summary_errors))
    (output_dir / "m12_transfer_summary.json").write_text(
        json.dumps(transfer_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "m12_load_ladder_report_template.json").write_text(
        json.dumps(LOAD_LADDER_REPORT_TEMPLATE, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "m12_soak_report_template.json").write_text(
        json.dumps(SOAK_REPORT_TEMPLATE, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "m12_command_log_manifest_template.json").write_text(
        json.dumps(COMMAND_LOG_MANIFEST_TEMPLATE, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def write_m12_transfer_archive(
    *,
    repo_root: Path,
    output_dir: Path,
    archive_name: str = "m12_transfer_evidence_pack.tar.gz",
) -> dict[str, Any]:
    """Write a companion evidence archive for target-node transfer.

    The archive is intentionally not a repo replacement. Operators still need an
    exact repo checkout; this just packages the M12 evidence/control files with
    hashes so transfer drift is easy to detect.
    """

    manifest = write_m12_transfer_pack(repo_root=repo_root, output_dir=output_dir)
    repo_root = repo_root.resolve()
    workspace_root = repo_root.parent.resolve()
    output_dir = output_dir.resolve()
    archive_path = output_dir / archive_name

    included: dict[str, dict[str, Any]] = {}
    for rel_path in REQUIRED_TRANSFER_ARTIFACTS:
        source = (repo_root / rel_path).resolve()
        for file_path in _iter_artifact_files(source):
            archive_member = _archive_name(workspace_root=workspace_root, path=file_path)
            included[archive_member] = {
                "_local_source_path": str(file_path),
                "source_path": archive_member,
                "archive_path": archive_member,
                "size_bytes": file_path.stat().st_size,
                "mode": _archive_file_mode(file_path),
                "sha256": _sha256_file(file_path),
            }

    for name in TRANSFER_PREP_FILES:
        source = output_dir / name
        if source.exists():
            archive_member = _archive_name(
                workspace_root=workspace_root,
                path=source,
                fallback_root=output_dir,
                fallback_prefix="m12_transfer_prep",
            )
            included[archive_member] = {
                "_local_source_path": str(source),
                "source_path": archive_member,
                "archive_path": archive_member,
                "size_bytes": source.stat().st_size,
                "mode": _archive_file_mode(source),
                "sha256": _sha256_file(source),
            }

    output_dir.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw_archive:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as gzip_archive:
            with tarfile.open(fileobj=gzip_archive, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for archive_member in sorted(included):
                    source_path = Path(str(included[archive_member]["_local_source_path"]))
                    payload = source_path.read_bytes()
                    info = tarfile.TarInfo(archive_member)
                    info.size = len(payload)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = _archive_file_mode(source_path)
                    archive.addfile(info, io.BytesIO(payload))

    archive_sha256 = _sha256_file(archive_path)
    sha_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    sha_path.write_text(f"{archive_sha256}  {archive_path.name}\n", encoding="utf-8")

    archive_manifest = {
        "archive_manifest_id": "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1",
        "claim_boundary": "transfer_archive_only_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "archive_is_repo_replacement": False,
        "archive_contains_source_overlay": True,
        "archive_excludes_pycache": True,
        "archive_deterministic": True,
        "source_paths_portable": True,
        "deterministic_archive_metadata": {
            "gzip_mtime": 0,
            "member_gid": 0,
            "member_gname": "",
            "member_mtime": 0,
            "member_order": "sorted_by_archive_path",
            "member_uid": 0,
            "member_uname": "",
        },
        "source_overlay_paths": [
            "breadboard/rl",
            "scripts/rl_phase1",
            "tests/rl",
            "tests/test_rl_phase1_scorecard_schema.py",
            "tests/test_rl_phase1_claim_ledger.py",
            "docs/rl_phase1",
            "examples/rl_env_packages",
        ],
        "required_operator_repo_step": (
            "Checkout the exact repo SHA from m12_transfer_manifest.json, then overlay the archived RL Phase 1 "
            "source/control files before running target commands."
        ),
        "archive_path": archive_path.name,
        "archive_name": archive_path.name,
        "archive_sha256": archive_sha256,
        "archive_sha256_file": sha_path.name,
        "archive_size_bytes": archive_path.stat().st_size,
        "included_entry_count": len(included),
        "all_required_artifacts_present": bool(manifest["all_required_artifacts_present"]),
        "all_transfer_requirements_covered": bool(manifest["all_transfer_requirements_covered"]),
        "generated_transfer_files": list(TRANSFER_PREP_FILES),
        "included_entries": [
            {entry_key: entry_value for entry_key, entry_value in included[key].items() if not entry_key.startswith("_")}
            for key in sorted(included)
        ],
    }
    (output_dir / "m12_transfer_archive_manifest.json").write_text(
        json.dumps(archive_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return archive_manifest


def _safe_overlay_destination(*, workspace_root: Path, archive_path: str) -> Path:
    member_path = Path(archive_path)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"unsafe archive member path: {archive_path}")
    if "__pycache__" in member_path.parts or member_path.suffix == ".pyc":
        raise ValueError(f"generated Python cache member is not allowed: {archive_path}")
    if not member_path.parts:
        raise ValueError(f"archive member must be rooted under workspace/ or m12_transfer_prep/: {archive_path}")
    if member_path.parts[0] == "workspace":
        relative = Path(*member_path.parts[1:])
    elif member_path.parts[0] == "m12_transfer_prep":
        relative = Path("docs_tmp", "ZYPHRA", "RL_PHASE_1", "runs", *member_path.parts)
    else:
        raise ValueError(f"archive member must be rooted under workspace/ or m12_transfer_prep/: {archive_path}")
    destination = (workspace_root / relative).resolve()
    root = workspace_root.resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"archive member escapes workspace root: {archive_path}") from exc
    return destination


def _blocking_overlay_parent(*, workspace_root: Path, destination: Path) -> Path | None:
    parent = destination.parent
    root = workspace_root.resolve()
    while True:
        if parent.exists():
            return None if parent.is_dir() else parent
        if parent == root or parent.parent == parent:
            return None
        parent = parent.parent


def apply_m12_transfer_overlay(
    *,
    manifest_path: Path,
    workspace_root: Path,
    dry_run: bool = True,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    """Verify and optionally apply the M12 source/evidence overlay.

    This is intentionally not a validation command and not a score promotion path.
    It only makes target-node transfer less error-prone after an operator has
    checked out the exact repository SHA recorded in the transfer manifest.
    """

    workspace_root = workspace_root.resolve()
    manifest_path = manifest_path.resolve()
    validation_errors = validate_m12_transfer_archive_manifest(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "report_id": "bb_zyphra_rl_phase1_m12_overlay_apply_report_v1",
            "claim_boundary": "transfer_overlay_application_not_m12_validation",
            "scorecard_update_allowed": False,
            "m12_points_awarded": False,
            "dry_run": dry_run,
            "allow_overwrite": allow_overwrite,
            "workspace_root": str(workspace_root),
            "archive_manifest": str(manifest_path),
            "archive_path": None,
            "status": "failed",
            "would_write_count": 0,
            "written_count": 0,
            "existing_destination_count": 0,
            "errors": [f"archive manifest is not readable JSON: {type(exc).__name__}: {exc}"],
            "entries": [],
        }

    archive_path = _resolve_archive_manifest_path(
        manifest_path,
        "archive_path",
        str(manifest.get("archive_name") or "m12_transfer_evidence_pack.tar.gz"),
    )
    errors = list(validation_errors)
    entries: list[dict[str, Any]] = []

    included_entries = manifest.get("included_entries")
    if not isinstance(included_entries, list):
        included_entries = []
        errors.append("included_entries must be a list")

    destinations: dict[str, str] = {}
    for entry in included_entries:
        if not isinstance(entry, dict):
            errors.append("included_entries must contain objects")
            continue
        archive_member = str(entry.get("archive_path") or "")
        try:
            destination = _safe_overlay_destination(workspace_root=workspace_root, archive_path=archive_member)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        destination_key = str(destination)
        if destination_key in destinations:
            errors.append(f"duplicate overlay destination: {destination_key}")
            continue
        destinations[destination_key] = archive_member
        exists = destination.exists()
        blocking_parent = _blocking_overlay_parent(workspace_root=workspace_root, destination=destination)
        if blocking_parent is not None:
            errors.append(f"destination parent exists and is not directory: {blocking_parent}")
        if exists and destination.is_dir():
            errors.append(f"destination exists and is directory: {destination}")
        if exists and not allow_overwrite and not dry_run:
            errors.append(f"destination exists and allow_overwrite is false: {destination}")
        entries.append(
            {
                "archive_path": archive_member,
                "destination_path": destination_key,
                "exists": exists,
                "size_bytes": entry.get("size_bytes"),
                "sha256": entry.get("sha256"),
            }
        )

    written_count = 0
    if not errors and not dry_run:
        with tarfile.open(archive_path, "r:gz") as archive:
            by_name = {member.name: member for member in archive.getmembers() if member.isfile()}
            for entry in entries:
                archive_member = str(entry["archive_path"])
                member = by_name.get(archive_member)
                if member is None:
                    errors.append(f"archive member missing during overlay apply: {archive_member}")
                    break
                extracted = archive.extractfile(member)
                if extracted is None:
                    errors.append(f"archive member unreadable during overlay apply: {archive_member}")
                    break
                payload = extracted.read()
                expected_sha = str(entry.get("sha256") or "")
                if _sha256_bytes(payload) != expected_sha:
                    errors.append(f"archive member sha256 mismatch during overlay apply: {archive_member}")
                    break
                destination = Path(str(entry["destination_path"]))
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(payload)
                except Exception as exc:
                    errors.append(f"overlay write failed: {destination}: {type(exc).__name__}: {exc}")
                    break
                written_count += 1

    status = "passed" if not errors else "failed"
    return {
        "report_id": "bb_zyphra_rl_phase1_m12_overlay_apply_report_v1",
        "claim_boundary": "transfer_overlay_application_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "dry_run": dry_run,
        "allow_overwrite": allow_overwrite,
        "workspace_root": str(workspace_root),
        "archive_manifest": str(manifest_path),
        "archive_path": str(archive_path),
        "status": status,
        "would_write_count": len(entries),
        "written_count": written_count,
        "existing_destination_count": sum(1 for entry in entries if entry["exists"]),
        "errors": errors,
        "entries": entries,
    }


def validate_m12_transfer_overlay_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != "bb_zyphra_rl_phase1_m12_overlay_apply_report_v1":
        errors.append("report_id must be bb_zyphra_rl_phase1_m12_overlay_apply_report_v1")
    if report.get("claim_boundary") != "transfer_overlay_application_not_m12_validation":
        errors.append("claim_boundary must remain transfer_overlay_application_not_m12_validation")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    if report.get("status") not in {"passed", "failed"}:
        errors.append("status must be passed or failed")
    report_errors = report.get("errors")
    if not isinstance(report_errors, list):
        errors.append("errors must be a list")
        report_errors = []
    entries = report.get("entries")
    if not isinstance(entries, list):
        errors.append("entries must be a list")
        entries = []
    would_write_count = report.get("would_write_count")
    written_count = report.get("written_count")
    existing_destination_count = report.get("existing_destination_count")
    if not isinstance(would_write_count, int) or isinstance(would_write_count, bool):
        errors.append("would_write_count must be an integer")
        would_write_count = None
    if not isinstance(written_count, int) or isinstance(written_count, bool):
        errors.append("written_count must be an integer")
        written_count = None
    if not isinstance(existing_destination_count, int) or isinstance(existing_destination_count, bool):
        errors.append("existing_destination_count must be an integer")
        existing_destination_count = None
    if would_write_count != len(entries):
        errors.append("would_write_count must equal len(entries)")
    if written_count is not None and would_write_count is not None and (
        written_count < 0 or written_count > would_write_count
    ):
        errors.append("written_count must be between 0 and would_write_count")
    if report.get("status") == "passed" and report_errors != []:
        errors.append("passed report must have no errors")
    if report.get("status") == "failed" and report_errors == []:
        errors.append("failed report must include at least one error")
    if report.get("dry_run") is True and written_count != 0:
        errors.append("dry-run report must have written_count=0")
    if report.get("dry_run") is False and report.get("status") == "passed":
        if written_count != len(entries):
            errors.append("successful apply report must write every entry")
    observed_existing_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("entries must contain objects")
            continue
        archive_path = str(entry.get("archive_path") or "")
        destination_path = str(entry.get("destination_path") or "")
        if not isinstance(entry.get("exists"), bool):
            errors.append(f"entry exists must be boolean: {archive_path}")
        elif entry["exists"]:
            observed_existing_count += 1
        if not (archive_path.startswith("workspace/") or archive_path.startswith("m12_transfer_prep/")):
            errors.append(f"entry archive_path must start with workspace/ or m12_transfer_prep/: {archive_path}")
        if "__pycache__" in archive_path or archive_path.endswith(".pyc"):
            errors.append(f"entry archive_path must exclude Python cache files: {archive_path}")
        if not destination_path:
            errors.append("entry destination_path must be non-empty")
        if not str(entry.get("sha256") or "").startswith("sha256:"):
            errors.append(f"entry sha256 must start with sha256: {archive_path}")
    if existing_destination_count != observed_existing_count:
        errors.append("existing_destination_count must equal entries with exists=true")
    return errors
