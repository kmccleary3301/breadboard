from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PHASE3_COMMAND_LOG_MANIFEST_SCHEMA = "bb.rl.phase3.command_log_manifest.v1"
PHASE3_COMPONENT_REPORT_SCHEMA = "bb.rl.phase3.component_report.v1"
PHASE3_TARGET_RUN_ID_PATTERN = r"^\d{8}T\d{6}Z-slurm-\d+$"
REQUIRED_COMMAND_LOG_FIELDS = (
    "command_id",
    "argv",
    "raw_log_path",
    "raw_log_sha256",
    "slurm_job_id",
    "target_run_id",
    "node",
    "started_at",
    "completed_at",
    "exit_code",
    "status",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def write_phase3_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

def normalize_phase3_metric_sources(metrics: dict[str, dict]) -> dict[str, dict]:
    normalized = {key: dict(value) for key, value in metrics.items()}
    source_defaults = {
        "slurm": "slurm_sacct",
        "gpu": "rocm_smi",
        "verifier": "verifier_client",
        "service": "service_event_log",
        "object_store": "object_store",
        "scheduler": "scheduler_control",
    }
    for key, source in source_defaults.items():
        normalized.setdefault(key, {}).setdefault("source", source)
    return normalized


def _phase3_runs_root(evidence_root: Path) -> Path:
    return (evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs").resolve()


def _resolve_under(root: Path, raw_path: Any) -> Path | None:
    if raw_path is None:
        return None
    text = str(raw_path)
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _command_rows(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = manifest.get("commands", manifest.get("command_logs", []))
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _inline_reports_from_log(path: Path) -> list[Any]:
    reports: list[Any] = []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("PHASE3_INTROSPECTION_REPORT=") or line.startswith("PHASE3_COMPONENT_REPORT_JSON="):
            _, payload = line.split("=", 1)
            try:
                reports.append(json.loads(payload))
            except json.JSONDecodeError:
                reports.append({"passed": False, "blocked_reason": "invalid_inline_report"})
    return reports


def validate_phase3_command_log_manifest(
    manifest: Mapping[str, Any], *, target_run_id: str, repo_root: Path, evidence_root: Path
) -> list[str]:
    del repo_root
    errors: list[str] = []
    if manifest.get("schema_version") != PHASE3_COMMAND_LOG_MANIFEST_SCHEMA:
        errors.append("schema_version must be bb.rl.phase3.command_log_manifest.v1")
    manifest_target = str(manifest.get("target_run_id") or "")
    if manifest_target != target_run_id:
        errors.append("manifest target_run_id must match expected target_run_id")
    if not re.match(PHASE3_TARGET_RUN_ID_PATTERN, target_run_id):
        errors.append("target_run_id must match Phase 3 Slurm target run id pattern")
    rows = _command_rows(manifest)
    if not rows:
        errors.append("commands must contain at least one command row")
        return errors
    seen: set[str] = set()
    runs_root = _phase3_runs_root(evidence_root)
    for index, row in enumerate(rows, start=1):
        prefix = f"commands[{index}]"
        for field_name in REQUIRED_COMMAND_LOG_FIELDS:
            if row.get(field_name) in (None, "", []):
                errors.append(f"{prefix}.{field_name} must be present")
        command_id = str(row.get("command_id") or "")
        if command_id in seen:
            errors.append(f"{prefix}.command_id must be unique")
        seen.add(command_id)
        if row.get("target_run_id") != target_run_id:
            errors.append(f"{prefix}.target_run_id must match manifest target_run_id")
        if row.get("exit_code") != 0:
            errors.append(f"{prefix}.exit_code must be 0")
        if row.get("status") != "passed":
            errors.append(f"{prefix}.status must be passed")
        raw_path = _resolve_under(runs_root, row.get("raw_log_path"))
        if raw_path is None:
            errors.append(f"{prefix}.raw_log_path must stay under evidence RL_PHASE_3/runs")
            continue
        if not raw_path.exists() or not raw_path.is_file():
            errors.append(f"{prefix}.raw_log_path must exist")
            continue
        expected_hash = sha256_file(raw_path)
        if row.get("raw_log_sha256") != expected_hash:
            errors.append(f"{prefix}.raw_log_sha256 must match current raw log hash")
        inline_reports = _inline_reports_from_log(raw_path)
        for report_index, report in enumerate(inline_reports, start=1):
            if not isinstance(report, Mapping):
                errors.append(f"{prefix}.inline_reports[{report_index}] must be an object")
                continue
            if report.get("passed") is not True:
                errors.append(f"{prefix}.inline_reports[{report_index}].passed must be true")
        if inline_reports and "component_passed" in row and row.get("component_passed") is not True:
            errors.append(f"{prefix}.component_passed must be true when inline reports are canonical")
    return errors


def _artifact_mapping(report: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = report.get("artifact_paths")
    if isinstance(artifacts, Mapping):
        return dict(artifacts)
    if isinstance(artifacts, list):
        return {str(index): value for index, value in enumerate(artifacts)}
    return {}

def validate_phase3_artifact_hashes(
    report: Mapping[str, Any], *, required_artifact_keys: Sequence[str], evidence_root: Path
) -> list[str]:
    errors: list[str] = []
    artifact_paths = _artifact_mapping(report)
    input_hashes = report.get("input_hashes")
    input_hashes = input_hashes if isinstance(input_hashes, Mapping) else {}
    root = evidence_root.resolve()
    for key in required_artifact_keys:
        if key not in artifact_paths:
            errors.append(f"artifact_paths.{key} must be present")
            continue
        path = _resolve_under(root, artifact_paths[key])
        if path is None:
            errors.append(f"artifact_paths.{key} must stay under evidence_root")
            continue
        if not path.exists() or not path.is_file():
            errors.append(f"artifact_paths.{key} must exist")
            continue
        expected_hash = input_hashes.get(key)
        if not isinstance(expected_hash, str) or not expected_hash:
            errors.append(f"input_hashes.{key} must be present")
            continue
        if expected_hash != sha256_file(path):
            errors.append(f"input_hashes.{key} must match artifact_paths.{key} content sha256")
    return errors



def validate_phase3_component_report(
    report: Mapping[str, Any], *, expected_schema: str, expected_claim_boundary: str, target_run_id: str,
    required_artifact_keys: Sequence[str], evidence_root: Path
) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != expected_schema:
        errors.append("schema_version must match expected component schema")
    if expected_schema == PHASE3_COMPONENT_REPORT_SCHEMA and not str(report.get("component") or ""):
        errors.append("generic Phase 3 component reports must include component")
    if report.get("claim_boundary") != expected_claim_boundary:
        errors.append("claim_boundary must match exact expected claim boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("passed") is not True:
        errors.append("passed must be true")
    if not str(report.get("report_id") or ""):
        errors.append("report_id must be present")
    if report.get("target_run_id") != target_run_id:
        errors.append("target_run_id must match expected target_run_id")
    if not isinstance(report.get("input_hashes"), Mapping) or not report.get("input_hashes"):
        errors.append("input_hashes must be a non-empty mapping")
    artifact_paths = _artifact_mapping(report)
    if not artifact_paths:
        errors.append("artifact_paths must be present")
    errors.extend(validate_phase3_artifact_hashes(report, required_artifact_keys=required_artifact_keys, evidence_root=evidence_root))
    return errors
