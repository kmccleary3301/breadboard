from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, Optional

MAX_EXCERPT_LENGTH = 2048
_SENSITIVE_HEADER_KEYS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "api_key",
    "x-openai-api-key",
    "openai-organization",
}


def _ensure_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8", "ignore")
        except Exception:
            return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _truncate_excerpt(text: str, limit: int = MAX_EXCERPT_LENGTH) -> Dict[str, Any]:
    length = len(text)
    if length <= limit:
        return {
            "body_excerpt": text,
            "body_length": length,
            "body_truncated": False,
        }
    return {
        "body_excerpt": text[:limit],
        "body_length": length,
        "body_truncated": True,
    }


def _sanitize_header_value(key_lower: str, value: Any) -> Any:
    if key_lower in _SENSITIVE_HEADER_KEYS:
        return "***REDACTED***"
    if isinstance(value, (list, tuple)):
        return [_sanitize_header_value(key_lower, item) for item in value]
    text = _ensure_text(value)
    if len(text) > 256:
        return text[:256] + "..."
    return text


def _sanitize_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not headers:
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in headers.items():
        key_str = str(key)
        sanitized[key_str] = _sanitize_header_value(key_str.lower(), value)
    return sanitized


class StructuredRequestRecorder:
    """Persist structured, sanitized request metadata for incident review."""

    def __init__(self, logger_v2_manager) -> None:
        self.lm = logger_v2_manager

    def record_request(
        self,
        turn_index: int,
        *,
        provider_id: str,
        runtime_id: str,
        model: str,
        request_headers: Optional[Dict[str, Any]],
        request_body: Any,
        stream: bool,
        tool_count: int,
        endpoint: Optional[str] = None,
        attempt: int = 0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not getattr(self.lm, "run_dir", None):
            return ""
        if not getattr(self.lm, "include_structured_requests", True):
            return ""

        body_text = _ensure_text(request_body)
        body_snapshot = _truncate_excerpt(body_text, MAX_EXCERPT_LENGTH)

        record: Dict[str, Any] = {
            "turn": turn_index,
            "attempt": attempt,
            "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "provider": provider_id,
            "runtime": runtime_id,
            "model": model,
            "endpoint": endpoint,
            "stream": bool(stream),
            "tool_count": int(tool_count),
            "request": {
                "headers": _sanitize_headers(request_headers),
                **body_snapshot,
            },
        }
        if extra:
            record["extra"] = extra

        suffix = f"turn_{turn_index}"
        if attempt:
            suffix += f"_attempt_{attempt}"
        return self.lm.write_json(f"meta/requests/{suffix}.json", record)

