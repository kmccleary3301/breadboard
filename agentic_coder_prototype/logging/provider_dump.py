from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "x-api-key",
    "x_client_api_key",
    "x-client-api-key",
    "bearer",
}
MAX_TEXT_BYTES = 32768
MAX_BASE64_BYTES = 65536


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SENSITIVE_KEYS and isinstance(item, str):
                result[key] = "[redacted]"
            else:
                result[key] = _scrub(item)
        return result
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1e6) % 1_000_000:06d}Z"


def _ulid() -> str:
    millis = int(time.time() * 1000)
    rand = random.getrandbits(80)
    return f"{millis:012x}{rand:020x}".upper()


class ProviderDumpLogger:
    """Lightweight file logger for provider requests/responses."""

    def __init__(self) -> None:
        self.log_dir = os.environ.get("KC_PROVIDER_LOG_DIR")
        self.workspace_override = os.environ.get("KC_PROVIDER_WORKSPACE")
        self.session_override = os.environ.get("KC_PROVIDER_SESSION_ID")
        self.enabled = bool(self.log_dir)
        self._lock = threading.Lock()

    def _ensure_dir(self) -> Optional[Path]:
        if (not self.enabled or not self.log_dir) and os.environ.get("KC_PROVIDER_LOG_DIR"):
            self.log_dir = os.environ.get("KC_PROVIDER_LOG_DIR")
            self.enabled = bool(self.log_dir)
        if not self.enabled or not self.log_dir:
            return None
        path = Path(self.log_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_file(self, filename: str, payload: Dict[str, Any]) -> None:
        path = self._ensure_dir()
        if not path:
            return
        target = path / filename
        data = json.dumps(payload, indent=2)
        with self._lock:
            target.write_text(data, encoding="utf-8")

    def _resolve_workspace(self, context: Any) -> Optional[str]:
        if self.workspace_override:
            return self.workspace_override
        session_state = getattr(context, "session_state", None) if context else None
        workspace = getattr(session_state, "workspace", None)
        return workspace

    def _resolve_session(self, context: Any) -> Optional[str]:
        if self.session_override:
            return self.session_override
        session_state = getattr(context, "session_state", None) if context else None
        if session_state and hasattr(session_state, "get_provider_metadata"):
            value = session_state.get_provider_metadata("session_id")
            if value:
                return str(value)
        return None

    def log_request(
        self,
        *,
        provider: str,
        model: Optional[str],
        payload: Dict[str, Any],
        context: Any,
        target_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if not self.enabled:
            return None
        request_id = _ulid()
        entry = {
            "logVersion": 1,
            "phase": "request",
            "timestamp": _now_iso(),
            "provider": provider,
            "model": model,
            "requestId": request_id,
            "workspacePath": self._resolve_workspace(context),
            "sessionId": self._resolve_session(context),
            "targetUrl": target_url,
            "body": {
                "encoding": "json",
                "json": _scrub(payload),
            },
            "metadata": metadata or {},
        }
        self._write_file(f"{request_id}_request.json", entry)
        return request_id

    def log_response(
        self,
        *,
        provider: str,
        model: Optional[str],
        request_id: Optional[str],
        status_code: Optional[int],
        headers: Optional[Dict[str, str]],
        content_type: Optional[str],
        body_text: Optional[str],
        body_base64: Optional[str],
        context: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled or not request_id:
            return
        body_payload: Dict[str, Any] = {"encoding": "unknown"}
        if body_text:
            body_payload["encoding"] = "utf8"
            body_payload["text"] = body_text[:MAX_TEXT_BYTES]
        if body_base64:
            body_payload.setdefault("encoding", "base64")
            body_payload["base64"] = body_base64[:MAX_BASE64_BYTES]

        entry = {
            "logVersion": 1,
            "phase": "response",
            "timestamp": _now_iso(),
            "provider": provider,
            "model": model,
            "requestId": request_id,
            "workspacePath": self._resolve_workspace(context),
            "sessionId": self._resolve_session(context),
            "status": {
                "code": status_code,
                "ok": status_code is not None and 200 <= status_code < 300,
                "statusText": metadata.get("statusText") if metadata else None,
            },
            "headers": _scrub(headers or {}),
            "contentType": content_type,
            "body": body_payload,
            "metadata": metadata or {},
        }
        self._write_file(f"{request_id}_response.json", entry)


provider_dump_logger = ProviderDumpLogger()

