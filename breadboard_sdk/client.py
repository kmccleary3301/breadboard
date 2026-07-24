from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import quote, urlencode, urljoin

import requests

from .types import (
    AttachmentFileIterable,
    AttachmentUploadResponse,
    CTreeSnapshotResponse,
    HealthResponse,
    ModelCatalogResponse,
    SessionCreateResponse,
    SessionEvent,
    SessionFileContent,
    SessionFileInfo,
    SessionSummary,
    SkillCatalogResponse,
)


@dataclass
class ApiError(Exception):
    message: str
    status: int
    body: Any | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.message} (status={self.status})"


class BreadboardClient:
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

    def health(self) -> HealthResponse:
        return self._request("GET", "/health")
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
        return self._request("GET", f"/v1/harnesses/{quote(harness_id, safe='/')}")

    def update_harness(self, harness_id: str, definition: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", f"/v1/harnesses/{quote(harness_id, safe='/')}", body={"definition": definition})

    def validate_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/harnesses/{quote(harness_id, safe='/')}/validate")

    def explain_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/harnesses/{quote(harness_id, safe='/')}/explain")

    def lock_harness(self, harness_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/harnesses/{quote(harness_id, safe='/')}/lock")

    def get_harness_lock(self, lock_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/harness-locks/{quote(lock_id, safe='/')}")

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
        return self.stream_events(session_id, last_event_id=str(last_event_id) if last_event_id is not None else None, query=query)

    def create_session(
        self,
        *,
        config_path: str,
        task: str,
        overrides: Dict[str, Any] | None = None,
        metadata: Dict[str, Any] | None = None,
        workspace: str | None = None,
        max_steps: int | None = None,
        permission_mode: str | None = None,
        stream: bool = True,
    ) -> SessionCreateResponse:
        payload: Dict[str, Any] = {
            "config_path": config_path,
            "task": task,
            "stream": bool(stream),
        }
        if overrides:
            payload["overrides"] = dict(overrides)
        if metadata:
            payload["metadata"] = dict(metadata)
        if workspace:
            payload["workspace"] = workspace
        if max_steps is not None:
            payload["max_steps"] = int(max_steps)
        if permission_mode:
            payload["permission_mode"] = permission_mode
        return self._request("POST", "/v1/sessions", body=payload)

    def list_sessions(self) -> List[SessionSummary]:
        return self._request("GET", "/v1/sessions")

    def get_session(self, session_id: str) -> SessionSummary:
        return self._request("GET", f"/v1/sessions/{session_id}")

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/v1/sessions/{session_id}")

    def read_session_records(self, session_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{session_id}/records")


    def post_input(self, session_id: str, *, content: str, attachments: List[str] | None = None) -> None:
        payload: Dict[str, Any] = {"content": content}
        if attachments:
            payload["attachments"] = list(attachments)
        self._request("POST", f"/v1/sessions/{session_id}/input", body=payload)

    def post_command(self, session_id: str, *, command: str, payload: Dict[str, Any] | None = None) -> None:
        self._request("POST", f"/v1/sessions/{session_id}/command", body={"command": command, "payload": payload or {}})

    def get_models(self, *, config_path: str) -> ModelCatalogResponse:
        return self._request("GET", "/v1/models", query={"config_path": config_path})

    def get_skills(self, session_id: str) -> SkillCatalogResponse:
        return self._request("GET", f"/v1/sessions/{session_id}/skills")

    def get_ctree_snapshot(self, session_id: str) -> CTreeSnapshotResponse:
        return self._request("GET", f"/v1/sessions/{session_id}/ctrees")

    def list_session_files(self, session_id: str, *, path_prefix: str | None = None) -> List[SessionFileInfo]:
        return self._request("GET", f"/v1/sessions/{session_id}/files", query={"path": path_prefix} if path_prefix else None)

    def read_session_file(
        self,
        session_id: str,
        *,
        file_path: str,
        mode: str = "cat",
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
    ) -> SessionFileContent:
        query: Dict[str, Any] = {
            "path": file_path,
            "mode": mode,
            "head_lines": head_lines,
            "tail_lines": tail_lines,
            "max_bytes": max_bytes,
        }
        return self._request("GET", f"/v1/sessions/{session_id}/files/content", query=query)

    def download_artifact(self, session_id: str, *, artifact: str) -> str:
        return str(self._request("GET", f"/v1/sessions/{session_id}/download", query={"artifact": artifact}))

    def upload_attachments(
        self,
        session_id: str,
        *,
        files: AttachmentFileIterable,
        metadata: Dict[str, Any] | None = None,
    ) -> AttachmentUploadResponse:
        url = urljoin(self.base_url, f"/v1/sessions/{session_id}/attachments".lstrip("/"))
        headers: Dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        multipart = []
        for filename, data, mime in files:
            multipart.append(("files", (filename, data, mime or "application/octet-stream")))
        form = {"metadata": json.dumps(metadata or {})}
        resp = requests.post(url, headers=headers, files=multipart, data=form, timeout=self.timeout_s)
        if not resp.ok:
            payload: Any = None
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            raise ApiError("Attachment upload failed", resp.status_code, payload)
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    def stream_events(
        self,
        session_id: str,
        *,
        last_event_id: str | None = None,
        query: Dict[str, Any] | None = None,
    ) -> Generator[SessionEvent, None, None]:
        url = urljoin(self.base_url, f"/v1/sessions/{session_id}/events".lstrip("/"))
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

    # --- Provider auth material (in-memory engine store) --------------------
    def attach_provider_auth(
        self,
        *,
        provider_id: str,
        api_key: str | None = None,
        headers: Dict[str, str] | None = None,
        base_url: str | None = None,
        routing: Dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
        is_subscription_plan: bool = False,
        required_profile: Dict[str, Any] | None = None,
        config_path: str | None = None,
        overrides: Dict[str, Any] | None = None,
        alias: str | None = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "material": {
                "provider_id": provider_id,
                "alias": alias,
                "api_key": api_key,
                "headers": headers or {},
                "base_url": base_url,
                "routing": routing,
                "ttl_seconds": ttl_seconds,
                "is_subscription_plan": bool(is_subscription_plan),
            },
            "required_profile": required_profile,
            "config_path": config_path,
            "overrides": overrides,
        }
        return self._request("POST", "/v1/provider-auth/attach", body=body)

    def detach_provider_auth(self, *, provider_id: str, alias: str | None = None) -> Dict[str, Any]:
        return self._request("POST", "/v1/provider-auth/detach", body={"provider_id": provider_id, "alias": alias})

    def provider_auth_status(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/provider-auth/status")
