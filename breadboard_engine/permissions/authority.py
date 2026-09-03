"""Typed ownership boundary for permission normalization and execution decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .rules_store import PermissionRule


CanonicalPermission = Literal["once", "always", "reject"]
RuleDecision = Literal["allow", "deny"]
_RESPONSE_ALIASES: dict[str, CanonicalPermission] = {
    alias: decision
    for decision, aliases in {
        "once": "once allow approve approved ok okay yes y allow-once allow_once",
        "always": "always allow-always allow_always",
        "reject": "reject deny denied no n deny-once deny_once deny-always deny_always deny-stop deny_stop",
    }.items()
    for alias in aliases.split()
}


def normalize_permission_response(
    value: Any, *, fallback: CanonicalPermission = "reject"
) -> CanonicalPermission:

    """Resolve one response token without exposing broker internals."""
    token = str(value or "").strip().lower()
    if not token:
        return fallback
    return _RESPONSE_ALIASES.get(token, "reject")


def resolve_permission_decision(value: Any) -> PermissionResolution:
    """Resolve a command token and retain its durable-policy semantics."""
    token = str(value or "").strip().lower()
    canonical = normalize_permission_response(token)
    persistent = token in {
        "always",
        "allow-always",
        "allow_always",
        "deny-always",
        "deny_always",
    }
    rule_decision: RuleDecision | None = (
        "deny"
        if token in {"deny-always", "deny_always"}
        else "allow"
        if token in {"always", "allow-always", "allow_always"}
        else None
    )
    return PermissionResolution(
        value=canonical,
        token=token,
        persistent=persistent,
        rule_decision=rule_decision,
        stop=token in {"deny-stop", "deny_stop"},
    )


def _response_tokens(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        return [token for nested in value.values() for token in _response_tokens(nested)]
    return [value]


def resolve_permission_responses(
    response: Any,
    responses: Any,
    requested_ids: Sequence[str] = (),
    missing_response: CanonicalPermission = "reject",
) -> CanonicalPermission:
    """Resolve scalar or per-item responses to one canonical batch decision."""
    explicit = responses.get("items") if isinstance(responses, Mapping) else None
    wrapped = isinstance(explicit, Mapping)
    if (
        not wrapped
        and isinstance(responses, Mapping)
        and requested_ids
        and any(item_id in responses for item_id in requested_ids)
    ):
        explicit = responses
    if isinstance(responses, Mapping) and "default" in responses:
        tokens = _response_tokens(responses["default"])
    elif isinstance(explicit, Mapping):
        fallback = (
            (
                responses.get("fallback")
                or responses.get("default_response")
                or missing_response
            )
            if wrapped
            else missing_response
        )
        values = (
            [explicit.get(item_id, fallback) for item_id in requested_ids]
            if requested_ids
            else list(explicit.values())
        )
        tokens = [token for value in values for token in _response_tokens(value)]
    else:
        tokens = _response_tokens(responses if isinstance(responses, Mapping) else response)
    values = [normalize_permission_response(token) for token in tokens]
    if not values:
        raise ValueError("permission response contains no valid decisions")
    if "reject" in values:
        return "reject"
    if all(value == "always" for value in values):
        return "always"
    return "once"


def normalize_permission_responses(responses: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize nested response payloads at the authority boundary."""
    return {
        key: normalize_permission_responses(value)
        if isinstance(value, Mapping)
        else resolve_permission_responses(value, None)
        for key, value in responses.items()
    }


def permission_item_ids(request: Any) -> tuple[str, ...]:
    """Return non-empty item identifiers from one permission request."""
    if not isinstance(request, Mapping):
        return ()
    items = request.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return ()
    return tuple(
        str(item["item_id"]).strip()
        for item in items
        if isinstance(item, Mapping)
        and isinstance(item.get("item_id"), str)
        and str(item["item_id"]).strip()
    )



@dataclass(frozen=True, slots=True)
class PermissionResolution:
    """A canonical decision and the policy facts needed by command handling."""

    value: CanonicalPermission
    token: str
    persistent: bool = False
    rule_decision: RuleDecision | None = None
    stop: bool = False


class PermissionAuthority(Protocol):
    """Typed permission owner shared by session control and runtime execution."""

    def configure(
        self, config: Mapping[str, Any] | None = None, *, policy_pack: Any | None = None
    ) -> None:
        """Replace the effective permission configuration for one execution owner."""

    def normalize_response(self, value: Any, *, fallback: CanonicalPermission = "reject") -> CanonicalPermission:
        """Resolve a response token or alias to one canonical decision."""
    def normalize_request(self, call: Any) -> PermissionRequest | None:
        """Normalize one tool call at the permission boundary."""

    def request_item_ids(self, request: Any) -> tuple[str, ...]:
        """Return canonical item identifiers from one permission request."""


    def resolve_decision(self, value: Any) -> PermissionResolution:
        """Resolve a command decision, retaining persistence and stop semantics."""

    def resolve_responses(
        self,
        response: Any,
        responses: Any,
        requested_ids: Sequence[str] = (),
        missing_response: CanonicalPermission = "reject",
    ) -> CanonicalPermission:
        """Resolve a scalar or per-item response payload to one batch decision."""

    def normalize_responses(self, responses: Mapping[str, Any]) -> dict[str, Any]:
        """Canonicalize nested response items without caller-side alias parsing."""

    def default_response(self, config: Mapping[str, Any] | None = None) -> CanonicalPermission:
        """Return the configured response used when an interactive request has no reply."""

    def permission_mode_overrides(self, mode: str) -> dict[str, Any]:
        """Return the controlled overrides for an interactive permission mode."""

    def is_interactive_mode(self, mode: str) -> bool:
        """Whether a mode requires an interactive permission queue."""

    def load_rules(self, workspace_dir: Path) -> list[PermissionRule]:
        """Load workspace rules through the authority's persistence boundary."""

    def build_rule_overrides(
        self, config: Mapping[str, Any], rules: Iterable[PermissionRule]
    ) -> dict[str, Any]:
        """Project persisted rules into runtime configuration overrides."""

    def update_rule(
        self,
        workspace_dir: Path | str,
        *,
        category: str,
        pattern: str,
        decision: RuleDecision,
        scope: str = "project",
    ) -> bool:
        """Persist one durable permission rule."""

    def decide(self, call: Any) -> str | None:
        """Return the configured action for one tool call."""

    def allows(self, call: Any) -> bool:
        """Return whether a runtime tool call may execute without another prompt."""

    def ensure_allowed(self, session_state: Any, parsed_calls: Iterable[Any]) -> None:
        """Apply policy and interactive decisions to parsed calls."""

    def disabled_tool_names(self) -> set[str]:
        """Return tools hidden by fully denied permission categories."""
