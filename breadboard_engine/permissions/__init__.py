"""Canonical package for permission brokers and rule persistence."""

from .authority import (
    CanonicalPermission,
    PermissionAuthority,
    PermissionResolution,
    RuleDecision,
    normalize_permission_response,
    normalize_permission_responses,
    resolve_permission_decision,
    resolve_permission_responses,
)
from .broker import PermissionBroker, PermissionDeniedError, PermissionRequest, PermissionRequestTimeoutError
from .policy_pack import PolicyPack, sign_policy_payload, verify_policy_payload
from .rules_store import (
    PermissionRule,
    build_permission_overrides,
    load_permission_rules,
    upsert_permission_rule,
)

__all__ = [
    "CanonicalPermission",
    "PermissionAuthority",
    "PermissionBroker",
    "PermissionDeniedError",
    "PermissionRequest",
    "PermissionRequestTimeoutError",
    "PermissionResolution",
    "PolicyPack",
    "PermissionRule",
    "RuleDecision",
    "build_permission_overrides",
    "load_permission_rules",
    "normalize_permission_response",
    "normalize_permission_responses",
    "resolve_permission_decision",
    "resolve_permission_responses",
    "sign_policy_payload",
    "upsert_permission_rule",
    "verify_policy_payload",
]
