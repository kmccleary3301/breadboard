from __future__ import annotations

from typing import Any, Dict


_SENSITIVE_KEYS = {
    "authorization",
    "api-key",
    "api_key",
    "x-api-key",
    "proxy-authorization",
}


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                redacted[key_text] = "***REDACTED***"
            else:
                redacted[key_text] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_payload(item) for item in value)
    return value


class APIRequestRecorder:
    def __init__(self, logger_v2) -> None:
        self.logger_v2 = logger_v2

    def save_request(self, turn_index: int, payload: Dict[str, Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        sanitized = _redact_payload(payload)
        canonical_path = self.logger_v2.write_json(f"raw/requests/turn_{turn_index}.request.json", sanitized)
        self.logger_v2.write_json(f"raw/requests/turn_{turn_index}.json", sanitized)
        return canonical_path

    def save_response(self, turn_index: int, payload: Dict[str, Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        sanitized = _redact_payload(payload)
        canonical_path = self.logger_v2.write_json(f"raw/responses/turn_{turn_index}.response.json", sanitized)
        self.logger_v2.write_json(f"raw/responses/turn_{turn_index}.json", sanitized)
        return canonical_path
