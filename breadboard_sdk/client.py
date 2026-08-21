from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Generator, List
from urllib.parse import quote, urlencode, urljoin

import requests

from .types import SessionEvent


@dataclass
class ApiError(Exception):
    message: str
    status: int
    body: Any | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.message} (status={self.status})"


def _resource_path(value: str) -> str:
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("resource identifiers cannot contain empty or dot segments")
    return "/".join(quote(part, safe="") for part in parts)

class BreadBoardClient:
    """Python SDK for the BreadBoard CLI bridge API (HTTP + SSE)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9099",
        *,
        auth_token: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.auth_token = auth_token
        self.timeout_s = timeout_s

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Dict[str, Any] | None = None,
        body: Any | None = None,
        headers: Dict[str, str] | None = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        if query:
            url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"
        request_headers = self._headers()
        request_headers.update(headers or {})
        resp = requests.request(
            method=method,
            url=url,
            headers=request_headers,
            data=json.dumps(body) if body is not None else None,
            timeout=self.timeout_s,
        )
        if not resp.ok:
            payload: Any = None
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            raise ApiError(f"Request failed: {method} {path}", resp.status_code, payload)
        if method.upper() == "DELETE":
            return None
        if not resp.content:
            return None
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        return resp.text

    @staticmethod
    def _idempotency(value: str | None) -> Dict[str, str] | None:
        return {"Idempotency-Key": value} if value else None

    def describe_system(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/system")

    def health_system(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/health")

    def schemas_system(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/schemas")

    def create_harness(self, directory: str = ".") -> Dict[str, Any]:
        return self._request("POST", "/v1/harnesses", body={"directory": directory})

    def list_harness(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/harnesses")

    def get_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/harnesses/{_resource_path(harness_id)}")

    def update_harness(self, harness_id: str, definition: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", f"/v1/harnesses/{_resource_path(harness_id)}", body={"definition": definition})

    def validate_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/harnesses/{_resource_path(harness_id)}/validate")

    def explain_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/harnesses/{_resource_path(harness_id)}/explain")

    def lock_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/harnesses/{_resource_path(harness_id)}/lock")

    def get_harness_lock(self, lock_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/harness-locks/{_resource_path(lock_id)}")

    def list_integration(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/integrations")

    def get_integration(self, integration_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/integrations/{quote(integration_id, safe='')}")

    def probe_integration(self, integration_id: str, *, idempotency_key: str | None = None) -> Dict[str, Any]:
        return self._request("POST", f"/v1/integrations/{quote(integration_id, safe='')}/probe", headers=self._idempotency(idempotency_key))

    def list_artifact(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/artifacts")

    def get_artifact(self, artifact_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/artifacts/{quote(artifact_id, safe='')}")

    def verify_artifact(self, artifact_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/artifacts/{quote(artifact_id, safe='')}/verify")

    def start_session(self, payload: Dict[str, Any], *, idempotency_key: str | None = None) -> Dict[str, Any]:
        return self._request("POST", "/v1/sessions", body=payload, headers=self._idempotency(idempotency_key))

    def list_session(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/sessions")
    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{quote(session_id, safe='')}")

    def send_input_session(self, session_id: str, content: str, *, idempotency_key: str | None = None) -> Dict[str, Any]:
        return self._request("POST", f"/v1/sessions/{quote(session_id, safe='')}/input", body={"content": content}, headers=self._idempotency(idempotency_key))

    def approve_session(self, session_id: str, request_id: str, decision: str, *, idempotency_key: str | None = None) -> Dict[str, Any]:
        body = {"request_id": request_id, "decision": decision}
        return self._request("POST", f"/v1/sessions/{quote(session_id, safe='')}/approve", body=body, headers=self._idempotency(idempotency_key))

    def resume_session(self, session_id: str, *, idempotency_key: str | None = None) -> Dict[str, Any]:
        return self._request("POST", f"/v1/sessions/{quote(session_id, safe='')}/resume", headers=self._idempotency(idempotency_key))

    def cancel_session(self, session_id: str, reason: str = "operator request", *, idempotency_key: str | None = None) -> Dict[str, Any]:
        return self._request("POST", f"/v1/sessions/{quote(session_id, safe='')}/cancel", body={"reason": reason}, headers=self._idempotency(idempotency_key))

    def artifacts_session(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{quote(session_id, safe='')}/artifacts")
    def events_session(
        self,
        session_id: str,
        *,
        resume_token: int | None = None,
        last_event_id: int | None = None,
        limit: int = 256,
    ) -> Generator[SessionEvent, None, None]:
        query = {"resume_token": resume_token, "limit": limit}
        return self._stream_events(session_id, last_event_id=str(last_event_id) if last_event_id is not None else None, query=query)

    def _stream_events(
        self,
        session_id: str,
        *,
        last_event_id: str | None = None,
        query: Dict[str, Any] | None = None,
    ) -> Generator[SessionEvent, None, None]:
        url = urljoin(self.base_url, f"/v1/sessions/{quote(session_id, safe='')}/events".lstrip("/"))
        if query:
            url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"
        headers: Dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        resp = requests.get(url, headers=headers, stream=True, timeout=self.timeout_s)
        if not resp.ok:
            raise ApiError("Event stream failed", resp.status_code, resp.text)

        data_lines: List[str] = []
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip("\r")
            if not line:
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    try:
                        yield json.loads(payload)
                    except Exception:
                        yield {
                            "id": "raw",
                            "type": "error",
                            "session_id": session_id,
                            "turn": None,
                            "timestamp": 0,
                            "payload": {"raw": payload},
                        }
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
