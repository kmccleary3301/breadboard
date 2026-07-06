from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from breadboard.rl.m12.command_logs import (
    resolve_manifest_log_path,
    sha256_file,
    validate_command_log_manifest,
)
from breadboard.rl.m12.final_report import (
    FINAL_REPORT_ID,
    OPTIONAL_COMMAND_LOG_COMMANDS,
    REQUIRED_COMMAND_LOG_COMMANDS,
    REQUIRED_COMMAND_LOG_IDS,
    TARGET_CLAIM_LEDGER_PATH,
    TARGET_COMMAND_LOG_MANIFEST_PATH,
    TARGET_FINAL_REPORT_PATH,
    TARGET_PROMOTION_AUDIT_PATH,
    TARGET_SCORECARD_PATH,
    validate_m12_final_report,
)


PROMOTION_AUDIT_ID = "bb_zyphra_rl_phase1_m12_promotion_audit_v1"
PROMOTION_AUDIT_CLAIM_BOUNDARY = "promotion_review_only_not_scorecard_update"
FINAL_REPORT_COMMAND_ID = "final_report"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _missing_read_error(kind: str) -> str:
    return f"FileNotFoundError: {kind} is missing"


def _read_optional_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "read_error": _missing_read_error("JSON artifact"),
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
    return payload


def _read_optional_yaml_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "read_error": _missing_read_error("YAML artifact"),
        }
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        return {
            "present": True,
            "path": str(path),
            "read_error": f"{exc.__class__.__name__}: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            "present": True,
            "path": str(path),
            "read_error": f"expected YAML object, got {type(payload).__name__}",
        }
    payload.setdefault("present", True)
    payload.setdefault("path", str(path))
    return payload


def _read_optional_text(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "present": False,
            "path": str(path),
            "text": "",
            "read_error": _missing_read_error("text artifact"),
        }
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "present": True,
            "path": str(path),
            "text": "",
            "read_error": f"{exc.__class__.__name__}: {exc}",
        }
    return {
        "present": True,
        "path": str(path),
        "text": text,
        "read_error": None,
    }


def _sha_or_missing(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256_file(path)


def _m12_milestone(scorecard: dict[str, Any]) -> dict[str, Any]:
    for milestone in scorecard.get("milestones") or []:
        if isinstance(milestone, dict) and milestone.get("id") == "M12":
            return milestone
    return {}


def _command_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in manifest.get("commands") or [] if isinstance(item, dict)]


def _entry_by_id(manifest: dict[str, Any], command_id: str) -> dict[str, Any] | None:
    return next((item for item in _command_entries(manifest) if item.get("command_id") == command_id), None)


def _required_command_text_mismatches(manifest: dict[str, Any]) -> list[dict[str, str]]:
    entries_by_id = {str(entry.get("command_id") or ""): entry for entry in _command_entries(manifest)}
    mismatches: list[dict[str, str]] = []
    for command_id, expected in REQUIRED_COMMAND_LOG_COMMANDS.items():
        entry = entries_by_id.get(command_id)
        if entry is None:
            continue
        observed = str(entry.get("command") or "")
        if observed != expected:
            mismatches.append({"command_id": command_id, "expected": expected, "observed": observed})
    return mismatches


def _required_final_report_row_mismatches(
    *,
    final_report: dict[str, Any],
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    final_report_entries = {
        str(entry.get("command_id") or ""): entry
        for entry in (final_report.get("command_logs") or {}).get("commands") or []
        if isinstance(entry, dict)
    }
    manifest_entries = {
        str(entry.get("command_id") or ""): entry
        for entry in _command_entries(manifest)
    }
    mismatches: list[dict[str, str]] = []
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        final_report_entry = final_report_entries.get(command_id)
        manifest_entry = manifest_entries.get(command_id)
        if final_report_entry is None:
            mismatches.append({"command_id": command_id, "reason": "missing_from_final_report"})
            continue
        if manifest_entry is None:
            mismatches.append({"command_id": command_id, "reason": "missing_from_manifest"})
            continue
        if final_report_entry != manifest_entry:
            mismatches.append({"command_id": command_id, "reason": "row_differs_from_manifest"})
    return mismatches


def _optional_command_hash_verified(manifest_path: Path, entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    raw_log_path = entry.get("log_path")
    expected_sha = str(entry.get("sha256") or "")
    if not raw_log_path or not expected_sha.startswith("sha256:"):
        return False
    resolved = resolve_manifest_log_path(manifest_path, str(raw_log_path))
    return resolved.is_file() and sha256_file(resolved) == expected_sha


def _path_matches_target(path: Path, target_path: str) -> bool:
    normalized = str(path).replace("\\", "/")
    suffixes = {
        target_path,
        target_path.removeprefix("../"),
    }
    return normalized in suffixes or any(normalized.endswith(f"/{suffix}") for suffix in suffixes)


def _bool_checks_missing(sections: dict[str, dict[str, bool]]) -> list[str]:
    missing: list[str] = []
    for section, checks in sections.items():
        for name, passed in checks.items():
            if not passed:
                missing.append(f"{section}.{name}")
    return missing


def _refresh_audit_readiness(audit: dict[str, Any]) -> None:
    checks = audit.get("checks") if isinstance(audit.get("checks"), dict) else {}
    missing_requirements = _bool_checks_missing(checks)
    audit["missing_requirements"] = missing_requirements
    audit["promotion_review_ready"] = not missing_requirements


def _audit_check_summary(audit: dict[str, Any]) -> tuple[list[str], list[str]]:
    checks = audit.get("checks")
    if not isinstance(checks, dict) or not checks:
        return [], []
    errors: list[str] = []
    missing: list[str] = []
    for section, section_checks in checks.items():
        section_name = str(section)
        if not isinstance(section_checks, dict):
            errors.append(f"checks.{section_name} must be an object")
            missing.append(f"{section_name}.__section__")
            continue
        for name, passed in section_checks.items():
            check_name = str(name)
            if passed is not True and passed is not False:
                errors.append(f"checks.{section_name}.{check_name} must be boolean")
            if passed is not True:
                missing.append(f"{section_name}.{check_name}")
    return errors, missing


def build_m12_promotion_audit(
    *,
    final_report_path: Path,
    scorecard_path: Path,
    claim_ledger_path: Path,
    command_log_manifest_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    final_report = _read_optional_json_object(final_report_path)
    scorecard = _read_optional_yaml_object(scorecard_path)
    claim_ledger = _read_optional_text(claim_ledger_path)
    claim_ledger_text = str(claim_ledger.get("text") or "")
    command_log_manifest = _read_optional_json_object(command_log_manifest_path)

    if final_report.get("read_error"):
        final_report_errors = [f"final report unreadable: {final_report.get('read_error')}"]
    else:
        final_report_errors = validate_m12_final_report(final_report)
    if command_log_manifest.get("read_error"):
        command_log_errors = [
            f"command log manifest unreadable: {command_log_manifest.get('read_error')}"
        ]
    else:
        command_log_errors = validate_command_log_manifest(
            command_log_manifest_path,
            required_command_ids=list(REQUIRED_COMMAND_LOG_IDS),
            require_passed=True,
            verify_hashes=True,
        )
    m12 = _m12_milestone(scorecard)
    final_report_command = _entry_by_id(command_log_manifest, FINAL_REPORT_COMMAND_ID)
    final_report_command_hash_verified = _optional_command_hash_verified(command_log_manifest_path, final_report_command)
    final_report_command_text_matches = bool(
        final_report_command
        and final_report_command.get("command") == OPTIONAL_COMMAND_LOG_COMMANDS[FINAL_REPORT_COMMAND_ID]
    )
    final_report_target_run_id = (final_report.get("command_logs") or {}).get("single_target_run_id")
    final_report_command_target_run_matches = bool(
        final_report_target_run_id
        and final_report_command
        and final_report_command.get("target_run_id") == final_report_target_run_id
    )
    final_report_sha = _sha_or_missing(final_report_path)
    scorecard_sha = _sha_or_missing(scorecard_path)
    claim_ledger_sha = _sha_or_missing(claim_ledger_path)
    command_log_manifest_sha = _sha_or_missing(command_log_manifest_path)
    current_verified_points = scorecard.get("current_verified_points")
    milestone_verified_sum = sum(
        int(milestone.get("verified_points", 0))
        for milestone in scorecard.get("milestones") or []
        if isinstance(milestone, dict)
    )
    m12_pre_review_state = m12.get("verified_points") == 0 and m12.get("status") != "completed"
    m12_post_review_state = (
        current_verified_points == 1000
        and m12.get("verified_points") == 80
        and m12.get("status") == "completed"
    )
    required_ids = set(REQUIRED_COMMAND_LOG_IDS)
    manifest_command_ids = {str(entry.get("command_id") or "") for entry in _command_entries(command_log_manifest)}
    required_command_text_mismatches = _required_command_text_mismatches(command_log_manifest)
    final_report_required_command_row_mismatches = _required_final_report_row_mismatches(
        final_report=final_report,
        manifest=command_log_manifest,
    )
    required_command_text_matches = bool(
        command_log_manifest_path.is_file()
        and required_ids <= manifest_command_ids
        and not required_command_text_mismatches
    )
    command_log_section = final_report.get("command_logs") or {}
    archived_ids = set(command_log_section.get("archived_command_ids") or [])
    hash_verified_ids = set(command_log_section.get("hash_verified_command_ids") or [])

    sections = {
        "final_report": {
            "path_exists": final_report_path.is_file(),
            "path_matches_target_default": _path_matches_target(final_report_path, TARGET_FINAL_REPORT_PATH),
            "sha256_recorded": final_report_sha is not None,
            "report_id_valid": final_report.get("report_id") == FINAL_REPORT_ID,
            "schema_valid": not final_report_errors,
            "score_eligible": final_report.get("m12_score_eligible") is True,
            "missing_gates_empty": final_report.get("missing_gates") == [],
            "scorecard_update_disallowed": final_report.get("scorecard_update_allowed") is False,
            "required_command_logs_archived": required_ids <= archived_ids,
            "required_command_logs_hash_verified": required_ids <= hash_verified_ids,
        },
        "command_log_manifest": {
            "path_exists": command_log_manifest_path.is_file(),
            "path_matches_target_default": _path_matches_target(
                command_log_manifest_path,
                TARGET_COMMAND_LOG_MANIFEST_PATH,
            ),
            "sha256_recorded": command_log_manifest_sha is not None,
            "required_manifest_valid": not command_log_errors,
            "required_command_text_matches": required_command_text_matches,
            "final_report_required_rows_match_manifest": not final_report_required_command_row_mismatches,
            "final_report_command_logged": final_report_command is not None,
            "final_report_command_text_matches": final_report_command_text_matches,
            "final_report_command_passed": bool(final_report_command and final_report_command.get("status") == "passed"),
            "final_report_command_hash_verified": final_report_command_hash_verified,
            "final_report_command_target_run_matches": final_report_command_target_run_matches,
        },
        "scorecard": {
            "path_exists": scorecard_path.is_file(),
            "path_matches_target_default": _path_matches_target(scorecard_path, TARGET_SCORECARD_PATH),
            "sha256_recorded": scorecard_sha is not None,
            "readable": not scorecard.get("read_error"),
            "total_points_1000": scorecard.get("total_points") == 1000,
            "current_points_match_milestones": current_verified_points == milestone_verified_sum,
            "m12_milestone_present": bool(m12),
            "m12_state_valid_for_review": m12_pre_review_state or m12_post_review_state,
            "m12_post_review_if_awarded": (m12.get("verified_points") != 80) or m12_post_review_state,
            "planning_prose_does_not_score": (scorecard.get("score_policy") or {}).get("planning_prose_scores") is False,
            "points_require_evidence": (scorecard.get("score_policy") or {}).get("points_require_evidence") is True,
        },
        "claim_ledger": {
            "path_exists": claim_ledger_path.is_file(),
            "path_matches_target_default": _path_matches_target(claim_ledger_path, TARGET_CLAIM_LEDGER_PATH),
            "sha256_recorded": claim_ledger_sha is not None,
            "readable": not claim_ledger.get("read_error"),
            "future_claim_requires_eligible_report": "m12_score_eligible=true" in claim_ledger_text,
            "future_claim_requires_raw_command_logs": "raw command logs" in claim_ledger_text,
            "future_claim_requires_separate_review": "separately reviewed" in claim_ledger_text,
            "m12_claim_boundary_recorded": ("M12 validation remains unawarded" in claim_ledger_text)
            or ("BreadBoard passed 8xMI300X final validation" in claim_ledger_text),
        },
        "promotion_audit_output": {
            "path_recorded": output_path is not None,
            "path_matches_target_default": (
                output_path is not None and _path_matches_target(output_path, TARGET_PROMOTION_AUDIT_PATH)
            ),
        },
    }
    missing_requirements = _bool_checks_missing(sections)
    promotion_review_ready = not missing_requirements
    return {
        "audit_id": PROMOTION_AUDIT_ID,
        "claim_boundary": PROMOTION_AUDIT_CLAIM_BOUNDARY,
        "scorecard_update_allowed": False,
        "promotion_review_ready": promotion_review_ready,
        "missing_requirements": missing_requirements,
        "final_report_validation_errors": final_report_errors,
        "final_report_read_error": final_report.get("read_error"),
        "command_log_manifest_errors": command_log_errors,
        "command_log_manifest_read_error": command_log_manifest.get("read_error"),
        "scorecard_read_error": scorecard.get("read_error"),
        "claim_ledger_read_error": claim_ledger.get("read_error"),
        "required_command_text_mismatches": required_command_text_mismatches,
        "final_report_required_command_row_mismatches": final_report_required_command_row_mismatches,
        "checks": sections,
        "inputs": {
            "final_report": {"path": str(final_report_path), "sha256": final_report_sha},
            "scorecard": {
                "path": str(scorecard_path),
                "sha256": scorecard_sha,
                "read_error": scorecard.get("read_error"),
            },
            "claim_ledger": {
                "path": str(claim_ledger_path),
                "sha256": claim_ledger_sha,
                "read_error": claim_ledger.get("read_error"),
            },
            "command_log_manifest": {"path": str(command_log_manifest_path), "sha256": command_log_manifest_sha},
        },
        "outputs": {
            "promotion_audit": {"path": str(output_path) if output_path is not None else None},
        },
        "scorecard_state": {
            "current_verified_points": current_verified_points,
            "milestone_verified_sum": milestone_verified_sum,
            "m12_status": m12.get("status"),
            "m12_verified_points": m12.get("verified_points"),
            "m12_points": m12.get("points"),
        },
        "operator_next_step": (
            "If promotion_review_ready is true, perform a separate human-reviewed scorecard and claim-ledger update. "
            "This audit never edits the scorecard."
        ),
    }


def validate_m12_promotion_audit(audit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if audit.get("audit_id") != PROMOTION_AUDIT_ID:
        errors.append("audit_id must be bb_zyphra_rl_phase1_m12_promotion_audit_v1")
    if audit.get("claim_boundary") != PROMOTION_AUDIT_CLAIM_BOUNDARY:
        errors.append("claim_boundary must remain promotion_review_only_not_scorecard_update")
    if audit.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if not isinstance(audit.get("missing_requirements"), list):
        errors.append("missing_requirements must be a list")
    if not isinstance(audit.get("checks"), dict) or not audit["checks"]:
        errors.append("checks must be a non-empty object")
    check_errors, expected_missing_requirements = _audit_check_summary(audit)
    errors.extend(check_errors)
    if isinstance(audit.get("missing_requirements"), list):
        observed_missing_requirements = [str(item) for item in audit["missing_requirements"]]
        if sorted(observed_missing_requirements) != sorted(expected_missing_requirements):
            errors.append("missing_requirements must match promotion-audit embedded checks")
    if audit.get("promotion_review_ready") is not (not expected_missing_requirements):
        errors.append("promotion_review_ready must match promotion-audit embedded checks")
    if bool(audit.get("promotion_review_ready")) and audit.get("missing_requirements"):
        errors.append("promotion_review_ready cannot be true while missing_requirements is non-empty")
    if bool(audit.get("promotion_review_ready")):
        for section, checks in (audit.get("checks") or {}).items():
            if not isinstance(checks, dict):
                errors.append(f"checks.{section} must be an object")
                continue
            for name, passed in checks.items():
                if passed is not True:
                    errors.append(f"ready audit requires checks.{section}.{name}=true")
    return errors


def write_m12_promotion_audit(
    *,
    output_path: Path,
    final_report_path: Path,
    scorecard_path: Path,
    claim_ledger_path: Path,
    command_log_manifest_path: Path,
) -> dict[str, Any]:
    audit = build_m12_promotion_audit(
        final_report_path=final_report_path,
        scorecard_path=scorecard_path,
        claim_ledger_path=claim_ledger_path,
        command_log_manifest_path=command_log_manifest_path,
        output_path=output_path,
    )
    _refresh_audit_readiness(audit)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit
