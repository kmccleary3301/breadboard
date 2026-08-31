from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Generator, List
from urllib.parse import quote, urlencode, urljoin

import requests

from .generated.public_bindings import PUBLIC_BINDINGS_BY_OPERATION_ID
from .types import (
    PublicResult,
    PublicSessionDecision,
    PublicSessionStartRequest,
    SessionEvent,
)


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


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid session event {field}")
    return value


def _nullable_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


_SESSION_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "seq",
        "timestamp",
        "work_item_id",
        "parent_work_item_id",
        "attempt_id",
        "session_id",
        "span_id",
        "visibility",
        "kind",
        "payload",
        "payload_schema_version",
    }
)

_SESSION_EVENT_VISIBILITY_FIELDS = frozenset(
    {"model_visible", "provider_visible", "host_visible", "redaction_state"}
)


def _session_event(
    payload: str, expected_session_id: str, sse_id: str | None
) -> SessionEvent:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("invalid session event JSON") from error
    if not isinstance(value, dict):
        raise ValueError("invalid session event envelope")
    if value.keys() != _SESSION_EVENT_FIELDS:
        raise ValueError("invalid session event fields")
    if value.get("schema_version") != "bb.kernel_event.v2":
        raise ValueError("invalid session event schema_version")
    sequence = value.get("seq")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("invalid session event seq")
    if sse_id is None:
        raise ValueError("session event is missing an SSE id")
    if sse_id != str(sequence):
        raise ValueError("session event SSE id does not match seq")
    session_id = _required_text(value.get("session_id"), "session_id")
    if session_id != expected_session_id:
        raise ValueError("session event belongs to another session")
    visibility = value.get("visibility")
    if not isinstance(visibility, dict):
        raise ValueError("invalid session event visibility")
    if visibility.keys() != _SESSION_EVENT_VISIBILITY_FIELDS:
        raise ValueError("invalid session event visibility fields")
    for field in ("model_visible", "provider_visible", "host_visible"):
        if type(visibility.get(field)) is not bool:
            raise ValueError(f"invalid session event visibility.{field}")
    event_payload = value.get("payload")
    if not isinstance(event_payload, dict):
        raise ValueError("invalid session event payload")
    return {
        "schema_version": "bb.kernel_event.v2",
        "event_id": _required_text(value.get("event_id"), "event_id"),
        "seq": sequence,
        "timestamp": _required_text(value.get("timestamp"), "timestamp"),
        "work_item_id": _nullable_text(value.get("work_item_id"), "work_item_id"),
        "parent_work_item_id": _nullable_text(
            value.get("parent_work_item_id"), "parent_work_item_id"
        ),
        "attempt_id": _nullable_text(value.get("attempt_id"), "attempt_id"),
        "session_id": session_id,
        "span_id": _nullable_text(value.get("span_id"), "span_id"),
        "visibility": {
            "model_visible": visibility["model_visible"],
            "provider_visible": visibility["provider_visible"],
            "host_visible": visibility["host_visible"],
            "redaction_state": _required_text(
                visibility.get("redaction_state"), "visibility.redaction_state"
            ),
        },
        "kind": _required_text(value.get("kind"), "kind"),
        "payload": event_payload,
        "payload_schema_version": _required_text(
            value.get("payload_schema_version"), "payload_schema_version"
        ),
    }


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
            url = (
                f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"
            )
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
            raise ApiError(
                f"Request failed: {method} {path}", resp.status_code, payload
            )
        if method.upper() == "DELETE":
            return None
        if not resp.content:
            return None
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        return resp.text

    def _request_operation(
        self,
        operation_id: str,
        *,
        path_params: Dict[str, str] | None = None,
        query: Dict[str, Any] | None = None,
        body: Any | None = None,
        headers: Dict[str, str] | None = None,
    ) -> PublicResult:
        binding = PUBLIC_BINDINGS_BY_OPERATION_ID.get(operation_id)
        if binding is None:
            raise ValueError(f"unknown public operation ID: {operation_id}")
        try:
            path = binding.path.format(**(path_params or {}))
        except KeyError as error:
            raise ValueError(
                f"missing path parameter {error.args[0]!r} for {operation_id}"
            ) from error
        return self._request(
            binding.http_method,
            path,
            query=query,
            body=body,
            headers=headers,
        )

    @staticmethod
    def _idempotency(value: str | None) -> Dict[str, str] | None:
        return {"Idempotency-Key": value} if value else None

    def describe_system(self) -> PublicResult:
        return self._request_operation("system.describe")

    def health_system(self) -> PublicResult:
        return self._request_operation("system.health")

    def schemas_system(self) -> PublicResult:
        return self._request_operation("system.schemas")

    def create_harness(self, directory: str = ".") -> PublicResult:
        return self._request_operation("harness.create", body={"directory": directory})

    def list_harness(self) -> PublicResult:
        return self._request_operation("harness.list")

    def get_harness(self, harness_id: str) -> PublicResult:
        return self._request_operation(
            "harness.get",
            path_params={"harness_id": _resource_path(harness_id)},
        )

    def update_harness(
        self, harness_id: str, definition: Dict[str, Any]
    ) -> PublicResult:
        return self._request_operation(
            "harness.update",
            path_params={"harness_id": _resource_path(harness_id)},
            body={"definition": definition},
        )

    def validate_harness(self, harness_id: str) -> PublicResult:
        return self._request_operation(
            "harness.validate",
            path_params={"harness_id": _resource_path(harness_id)},
        )

    def explain_harness(self, harness_id: str) -> PublicResult:
        return self._request_operation(
            "harness.explain",
            path_params={"harness_id": _resource_path(harness_id)},
        )

    def lock_harness(self, harness_id: str) -> PublicResult:
        return self._request_operation(
            "harness.lock",
            path_params={"harness_id": _resource_path(harness_id)},
        )

    def get_harness_lock(self, lock_id: str) -> PublicResult:
        return self._request_operation(
            "harness_lock.get",
            path_params={"lock_id": _resource_path(lock_id)},
        )

    def list_integration(self) -> PublicResult:
        return self._request_operation("integration.list")

    def get_integration(self, integration_id: str) -> PublicResult:
        return self._request_operation(
            "integration.get",
            path_params={"integration_id": quote(integration_id, safe="")},
        )

    def probe_integration(
        self, integration_id: str, *, idempotency_key: str | None = None
    ) -> PublicResult:
        return self._request_operation(
            "integration.probe",
            path_params={"integration_id": quote(integration_id, safe="")},
            headers=self._idempotency(idempotency_key),
        )

    def list_artifact(self) -> PublicResult:
        return self._request_operation("artifact.list")

    def get_artifact(self, artifact_id: str) -> PublicResult:
        return self._request_operation(
            "artifact.get",
            path_params={"artifact_id": quote(artifact_id, safe="")},
        )

    def verify_artifact(self, artifact_id: str) -> PublicResult:
        return self._request_operation(
            "artifact.verify",
            path_params={"artifact_id": quote(artifact_id, safe="")},
        )

    def start_session(
        self, payload: PublicSessionStartRequest, *, idempotency_key: str | None = None
    ) -> PublicResult:
        return self._request_operation(
            "session.start",
            body=payload,
            headers=self._idempotency(idempotency_key),
        )

    def list_session(self) -> PublicResult:
        return self._request_operation("session.list")

    def get_session(self, session_id: str) -> PublicResult:
        return self._request_operation(
            "session.get",
            path_params={"session_id": quote(session_id, safe="")},
        )

    def send_input_session(
        self, session_id: str, content: str, *, idempotency_key: str | None = None
    ) -> PublicResult:
        return self._request_operation(
            "session.send_input",
            path_params={"session_id": quote(session_id, safe="")},
            body={"content": content},
            headers=self._idempotency(idempotency_key),
        )

    def approve_session(
        self,
        session_id: str,
        request_id: str,
        decision: PublicSessionDecision,
        *,
        idempotency_key: str | None = None,
    ) -> PublicResult:
        body = {"request_id": request_id, "decision": decision}
        return self._request_operation(
            "session.approve",
            path_params={"session_id": quote(session_id, safe="")},
            body=body,
            headers=self._idempotency(idempotency_key),
        )

    def resume_session(
        self, session_id: str, *, idempotency_key: str | None = None
    ) -> PublicResult:
        return self._request_operation(
            "session.resume",
            path_params={"session_id": quote(session_id, safe="")},
            headers=self._idempotency(idempotency_key),
        )

    def cancel_session(
        self,
        session_id: str,
        reason: str = "operator request",
        *,
        idempotency_key: str | None = None,
    ) -> PublicResult:
        return self._request_operation(
            "session.cancel",
            path_params={"session_id": quote(session_id, safe="")},
            body={"reason": reason},
            headers=self._idempotency(idempotency_key),
        )

    def artifacts_session(self, session_id: str) -> PublicResult:
        return self._request_operation(
            "session.artifacts",
            path_params={"session_id": quote(session_id, safe="")},
        )

    def events_session(
        self,
        session_id: str,
        *,
        resume_token: int | None = None,
        last_event_id: int | None = None,
        limit: int = 256,
    ) -> Generator[SessionEvent, None, None]:
        query = {"resume_token": resume_token, "limit": limit}
        return self._stream_events(
            "session.events",
            {"session_id": quote(session_id, safe="")},
            session_id=session_id,
            last_event_id=str(last_event_id) if last_event_id is not None else None,
            query=query,
        )

    def _stream_events(
        self,
        operation_id: str,
        path_params: Dict[str, str],
        *,
        session_id: str,
        last_event_id: str | None = None,
        query: Dict[str, Any] | None = None,
    ) -> Generator[SessionEvent, None, None]:
        binding = PUBLIC_BINDINGS_BY_OPERATION_ID.get(operation_id)
        if binding is None:
            raise ValueError(f"unknown public operation ID: {operation_id}")
        try:
            path = binding.path.format(**path_params)
        except KeyError as error:
            raise ValueError(
                f"missing path parameter {error.args[0]!r} for {operation_id}"
            ) from error
        url = urljoin(self.base_url, path.lstrip("/"))
        if query:
            url = (
                f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"
            )
        headers: Dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        resp = requests.request(
            method=binding.http_method,
            url=url,
            headers=headers,
            stream=True,
            timeout=self.timeout_s,
        )
        if not resp.ok:
            raise ApiError("Event stream failed", resp.status_code, resp.text)

        data_lines: List[str] = []
        sse_id: str | None = None
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip("\r")
            if not line:
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines = []
                    yield _session_event(payload, session_id, sse_id)
                    sse_id = None
                continue
            if line.startswith("id:"):
                sse_id = line[len("id:") :].lstrip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
