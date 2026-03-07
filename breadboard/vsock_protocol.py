from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union


REPL_VSOCK_PROTOCOL_VERSION = 1
REPL_UNSUPPORTED_ERROR = "repl_unsupported"

ReplCommand = Union[str, Dict[str, Any]]

def build_repl_request(command: ReplCommand, timeout: float) -> Dict[str, Any]:
    return {
        "mode": "repl",
        "command": command,
        "timeout": timeout,
        "protocol": REPL_VSOCK_PROTOCOL_VERSION,
    }


def build_envelope_repl_request(command: ReplCommand, timeout: float) -> Dict[str, Any]:
    return {
        "type": "repl",
        "version": REPL_VSOCK_PROTOCOL_VERSION,
        "payload": {
            "command": command,
            "timeout": timeout,
        },
    }


def build_hello_request(capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "type": "hello",
        "version": REPL_VSOCK_PROTOCOL_VERSION,
    }
    if capabilities:
        payload["capabilities"] = capabilities
    return payload


def build_legacy_repl_request(command: str) -> Dict[str, Any]:
    return {"cmd": command}


def classify_repl_response(payload: Any) -> Tuple[str, Optional[Dict[str, Any]]]:
    if not isinstance(payload, dict):
        return "invalid", None

    if "response" in payload:
        response = payload.get("response")
        if response is None:
            return "envelope", None
        if isinstance(response, dict):
            return "envelope", response
        return "envelope", {"value": response}

    if any(key in payload for key in ("env", "proofState", "messages", "sorries")):
        return "legacy", payload

    if payload.get("type") == "repl_response" and "error" in payload:
        error = payload.get("error") or {}
        error_code = error.get("code") or error.get("error_code") or error.get("error")
        if error_code == REPL_UNSUPPORTED_ERROR:
            return "unsupported", None

    error_code = payload.get("error_code") or payload.get("error")
    stderr = str(payload.get("stderr", "") or "").lower()
    if error_code == REPL_UNSUPPORTED_ERROR or "repl mode not supported" in stderr:
        return "unsupported", None

    if {"exit", "stdout", "stderr"}.issubset(payload.keys()):
        return "command_agent", None

    return "unknown", None
