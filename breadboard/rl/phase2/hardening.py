from __future__ import annotations
from collections.abc import Mapping

import json

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


HARDENING_REPORT_ID = "bb_zyphra_rl_phase2_multi_tenant_hardening_v1"
HARDENING_CLAIM_BOUNDARY = "p2_m11_hardening_policy_not_production_isolation_claim"
REDACTED = "<redacted>"
_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "apikey", "authorization")
_DESTRUCTIVE_PATTERNS = (
    "rm -rf",
    "sudo rm",
    "mkfs",
    "dd if=",
    "dd of=",
    "chmod -r 777",
    "chown -r",
    ":(){ :|:& };:",
)


@dataclass(frozen=True)
class EgressPolicy:
    allowed_prefixes: tuple[str, ...]
    max_artifact_bytes: int
    allow_absolute_paths: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_absolute_paths": self.allow_absolute_paths,
            "allowed_prefixes": list(self.allowed_prefixes),
            "max_artifact_bytes": self.max_artifact_bytes,
        }


@dataclass(frozen=True)
class ArtifactEgressRequest:
    relative_path: str
    bytes: int
    classification: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "classification": self.classification,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True)
class DestructiveActionRequest:
    action_id: str
    command: str
    workspace_relative_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action_id": self.action_id,
            "command": self.command,
            "workspace_relative_path": self.workspace_relative_path,
        }


def _is_safe_relative_path(path: str) -> bool:
    if not path or path.startswith("~"):
        return False
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute():
        return False
    return ".." not in pure.parts


def workspace_isolated(path: str, *, workspace_id: str) -> bool:
    if not _is_safe_relative_path(path):
        return False
    pure = PurePosixPath(path.replace("\\", "/"))
    return bool(pure.parts) and pure.parts[0] == workspace_id


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key in sorted(values):
        raw = values[key]
        lowered_key = str(key).lower()
        if any(part in lowered_key for part in _SECRET_KEY_PARTS):
            redacted[str(key)] = REDACTED
        elif isinstance(raw, str) and (raw.startswith("/") or raw.startswith("~")):
            redacted[str(key)] = REDACTED
        else:
            redacted[str(key)] = raw
    return redacted


def evaluate_artifact_egress(request: ArtifactEgressRequest, policy: EgressPolicy) -> dict[str, Any]:
    reasons: list[str] = []
    normalized = request.relative_path.replace("\\", "/")
    if not policy.allow_absolute_paths and not _is_safe_relative_path(normalized):
        reasons.append("path must be workspace-relative and cannot escape with ..")
    if request.bytes > policy.max_artifact_bytes:
        reasons.append("artifact exceeds max_artifact_bytes")
    if request.classification not in {"public", "tenant_internal"}:
        reasons.append("classification is not egress-approved")
    if not any(normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/") for prefix in policy.allowed_prefixes):
        reasons.append("path prefix is not egress-approved")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "request": request.to_dict(),
    }


def guard_destructive_action(request: DestructiveActionRequest, *, workspace_id: str) -> dict[str, Any]:
    command = " ".join(request.command.lower().split())
    reasons: list[str] = []
    if any(pattern in command for pattern in _DESTRUCTIVE_PATTERNS):
        reasons.append("command matches destructive-action denylist")
    if not workspace_isolated(request.workspace_relative_path, workspace_id=workspace_id):
        reasons.append("workspace path is not isolated to tenant workspace")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "request": request.to_dict(),
    }


def build_hardening_report(
    *,
    run_id: str,
    target_run_id: str,
    tenant_id: str,
    workspace_id: str,
    egress_policy: EgressPolicy,
    egress_requests: list[ArtifactEgressRequest],
    destructive_actions: list[DestructiveActionRequest],
    environment: Mapping[str, Any],
    adversarial_package_results: list[Mapping[str, Any]],
) -> dict[str, Any]:
    egress_results = [evaluate_artifact_egress(request, egress_policy) for request in egress_requests]
    destructive_results = [guard_destructive_action(request, workspace_id=workspace_id) for request in destructive_actions]
    adversarial_results = [dict(sorted(result.items())) for result in adversarial_package_results]
    failed_adversarial = [str(result.get("package_id") or result.get("name") or index) for index, result in enumerate(adversarial_results) if result.get("passed") is not True]
    hardening_passed = (
        all(result["allowed"] for result in egress_results)
        and all(result["allowed"] for result in destructive_results)
        and not failed_adversarial
    )
    return {
        "adversarial_package_results": adversarial_results,
        "artifact_egress_policy": egress_policy.to_dict(),
        "artifact_egress_results": egress_results,
        "claim_boundary": HARDENING_CLAIM_BOUNDARY,
        "milestone_id": "P2-M11",
        "passed": hardening_passed,
        "destructive_action_guards": destructive_results,
        "redacted_environment": redact_mapping(environment),
        "redaction_policy": {
            "redacted_value": REDACTED,
            "secret_key_parts": list(_SECRET_KEY_PARTS),
            "absolute_or_home_paths_redacted": True,
        },
        "report_id": HARDENING_REPORT_ID,
        "run_id": run_id,
        "scorecard_update_allowed": False,
        "tenant_id": tenant_id,
        "target_run_id": target_run_id,
        "workspace_isolation": {
            "egress_paths_isolated": all(_is_safe_relative_path(request.relative_path) for request in egress_requests),
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
        },
        "hardening_passed": hardening_passed,
        "failed_adversarial_packages": failed_adversarial,
    }


def validate_hardening_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != HARDENING_REPORT_ID:
        errors.append("report_id must be multi-tenant hardening v1")
    if report.get("claim_boundary") != HARDENING_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be hardening boundary")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if not str(report.get("target_run_id") or ""):
        errors.append("target_run_id must be non-empty")
    for section in ["workspace_isolation", "artifact_egress_policy", "artifact_egress_results", "redacted_environment", "destructive_action_guards", "adversarial_package_results"]:
        if section not in report:
            errors.append(f"{section} section must be present")
    redacted_environment = report.get("redacted_environment") if isinstance(report.get("redacted_environment"), Mapping) else {}
    for key, value in redacted_environment.items():
        lowered_key = str(key).lower()
        if any(part in lowered_key for part in _SECRET_KEY_PARTS) and value != REDACTED:
            errors.append(f"redacted_environment.{key} must be redacted")
        if isinstance(value, str) and re.match(r"^(~|/)", value):
            errors.append(f"redacted_environment.{key} must not expose absolute or home path")
    return errors

def report_to_json(report: Mapping[str, Any]) -> str:
    return json.dumps(dict(report), sort_keys=True, separators=(",", ":")) + "\n"
