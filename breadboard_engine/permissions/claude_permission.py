"""Native Claude SDK permission adapter over the canonical permission broker."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

from .broker import PermissionBroker, PermissionDeniedError, PermissionRequest


def request_stock_tool_permission(broker: PermissionBroker, session_state: Any, request: Dict[str, Any]) -> Dict[str, Any]:
    """Relay a genuine Claude SDK permission prompt without auto-granting it."""
    if session_state.get_provider_metadata("permission_queue") is None:
        raise PermissionDeniedError("No permission response channel is active")
    tool_name = request["tool_name"]
    tool_input = request["input"]
    normalized = broker._build_request(SimpleNamespace(name=tool_name, arguments=tool_input))
    permission = normalized or PermissionRequest("tool", tool_name, {})
    permission.metadata.update(stock_kind="claude_permission", stock_request=request)
    if broker._policy_denies(permission) or broker._resolve_action(permission) == "deny":
        return {"behavior": "deny", "message": "Permission denied by configured policy"}
    native_response: Dict[str, Any] = {}
    decision = broker._request_permissions(
        session_state, [permission], native_response=native_response, concurrent=True
    )[0][1]
    if decision not in {"once", "always"}:
        return {"behavior": "deny", "message": "User denied permission"}
    updated_input = native_response.get("updated_input", tool_input)
    response = {"behavior": "allow", "updatedInput": updated_input}
    if decision == "always" and request.get("permission_suggestions"):
        # cli.js:12505 UV6 applies VN and bh to these native updates;
        # cli.js:331 persists their stock-selected destination via hm.
        response["updatedPermissions"] = request["permission_suggestions"]
    return response
