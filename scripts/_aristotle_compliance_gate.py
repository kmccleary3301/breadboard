#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPE_PATH = REPO_ROOT / "config/compliance/aristotle_permission_scope_v1.json"
APPROVED_STATUSES = {
    "approved_full",
    "approved_internal_only",
    "approved_with_restrictions",
    "operator_override_internal_only",
}
REQUIRED_INTERNAL_PERMISSIONS = (
    "benchmark_evaluation_public_datasets",
    "store_outputs_for_reproducibility",
    "internal_comparative_reporting",
)


class AristotleComplianceError(RuntimeError):
    pass


def _load_scope(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AristotleComplianceError(f"scope payload must be object: {path}")
    return payload


def _permission_approved(payload: dict[str, Any], key: str) -> bool:
    permissions = payload.get("permissions")
    if not isinstance(permissions, dict):
        return False
    value = permissions.get(key)
    if not isinstance(value, dict):
        return False
    return bool(value.get("approved", False))


def evaluate_aristotle_compliance(
    *,
    scope_path: Path = DEFAULT_SCOPE_PATH,
    require_external_reporting: bool = False,
) -> dict[str, Any]:
    if not scope_path.exists():
        raise AristotleComplianceError(f"scope file missing: {scope_path}")
    payload = _load_scope(scope_path)
    status = str(payload.get("status") or "").strip().lower()
    status_ok = status in APPROVED_STATUSES
    fail_closed = bool((payload.get("enforcement") or {}).get("fail_closed_until_explicit_approval", True))
    required_permissions = list(REQUIRED_INTERNAL_PERMISSIONS)
    if require_external_reporting:
        required_permissions.append("external_aggregate_reporting")
    missing_permissions = [key for key in required_permissions if not _permission_approved(payload, key)]
    ok = status_ok and not missing_permissions
    if fail_closed:
        ok = ok and status_ok
    return {
        "ok": ok,
        "status": status,
        "status_ok": status_ok,
        "operator_override": status == "operator_override_internal_only",
        "fail_closed": fail_closed,
        "missing_permissions": missing_permissions,
        "scope_path": str(scope_path),
    }


def assert_aristotle_compliance(
    *,
    scope_path: Path = DEFAULT_SCOPE_PATH,
    require_external_reporting: bool = False,
) -> dict[str, Any]:
    report = evaluate_aristotle_compliance(
        scope_path=scope_path,
        require_external_reporting=require_external_reporting,
    )
    if report["ok"]:
        return report
    raise AristotleComplianceError(
        "Aristotle compliance gate denied run "
        f"(status={report['status']}, missing_permissions={report['missing_permissions']}). "
        f"Update scope file: {report['scope_path']}"
    )
