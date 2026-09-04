from __future__ import annotations

import json
import re
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List
from urllib.parse import quote, urlencode, urljoin, urlsplit

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


def _require_secure_bearer_transport(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme == "https":
        return
    hostname = parsed.hostname
    if parsed.scheme == "http" and hostname is not None:
        if hostname.casefold() == "localhost":
            return
        try:
            if ipaddress.ip_address(hostname).is_loopback:
                return
        except ValueError:
            pass
    raise ValueError(
        "Bearer authentication requires HTTPS except for loopback HTTP origins"
    )


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid session event {field}")
    return value



_RFC3339_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def _required_rfc3339_timestamp(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if _RFC3339_TIMESTAMP_RE.fullmatch(text) is None:
        raise ValueError(f"invalid session event {field}")
    leap_second = text[17:19] == "60"
    parseable = text[:17] + "59" + text[19:] if leap_second else text
    normalized = parseable[:-1] + "+00:00" if parseable.endswith(("Z", "z")) else parseable
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid session event {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid session event {field}")
    if leap_second:
        try:
            utc = parsed.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"invalid session event {field}") from exc
        if (utc.month, utc.day, utc.hour, utc.minute) not in {
            (6, 30, 23, 59),
            (12, 31, 23, 59),
        }:
            raise ValueError(f"invalid session event {field}")
    return text


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
_LIFECYCLE_PAYLOAD_SCHEMA = "bb.payload.product_session.lifecycle.v1"
_EVENT_PAYLOAD_SCHEMA = {
    "session.started": _LIFECYCLE_PAYLOAD_SCHEMA,
    "input.accepted": _LIFECYCLE_PAYLOAD_SCHEMA,
    "approval.requested": _LIFECYCLE_PAYLOAD_SCHEMA,
    "approval.resolved": _LIFECYCLE_PAYLOAD_SCHEMA,
    "session.reconfigured": _LIFECYCLE_PAYLOAD_SCHEMA,
    "session.paused": _LIFECYCLE_PAYLOAD_SCHEMA,
    "session.resumed": _LIFECYCLE_PAYLOAD_SCHEMA,
    "session.completed": _LIFECYCLE_PAYLOAD_SCHEMA,
    "session.failed": _LIFECYCLE_PAYLOAD_SCHEMA,
    "session.canceled": _LIFECYCLE_PAYLOAD_SCHEMA,
    "assistant_message": "bb.payload.message.assistant.v1",
    "tool_call": "bb.payload.tool.called.v1",
    "tool_result": "bb.payload.tool.completed.v1",
}
_LIFECYCLE_PAYLOAD_FIELDS = {
    "session.started": frozenset({"effective_lock_hash", "task_hash"}),
    "input.accepted": frozenset({"content_hash", "attachments"}),
    "approval.requested": frozenset({"request_id", "operation"}),
    "approval.resolved": frozenset({"request_id", "decision"}),
    "session.reconfigured": frozenset({"effective_lock_hash", "reason"}),
    "session.paused": frozenset({"reason"}),
    "session.resumed": frozenset(),
    "session.completed": frozenset({"outcome", "summary"}),
    "session.failed": frozenset({"outcome", "error", "detail"}),
    "session.canceled": frozenset({"outcome", "reason"}),
}
_KERNEL_PAYLOAD_FIELDS = {
    "assistant_message": frozenset({"seq", "metadata", "message", "text", "source"}),
    "tool_call": frozenset(
        {"seq", "metadata", "call", "call_id", "tool", "tool_name", "state"}
    ),
    "tool_result": frozenset(
        {
            "seq",
            "metadata",
            "message",
            "tool",
            "success",
            "status",
            "error",
            "call_id",
            "todo",
        }
    ),
}


def _sha256(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value[7:]
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _validate_kernel_payload(kind: str, payload: dict[str, Any]) -> None:
    if not payload.keys() <= _KERNEL_PAYLOAD_FIELDS[kind]:
        raise ValueError("invalid session event payload fields")
    if "seq" in payload and (type(payload["seq"]) is not int or payload["seq"] < 0):
        raise ValueError("invalid session event payload seq")
    if "metadata" in payload and not isinstance(payload["metadata"], dict):
        raise ValueError("invalid session event payload metadata")
    if kind == "assistant_message":
        text_fields = ("text", "source")
        object_fields: tuple[str, ...] = ()
        boolean_fields: tuple[str, ...] = ()
    elif kind == "tool_call":
        text_fields = ("call_id", "tool", "tool_name", "state")
        object_fields = ("call",)
        boolean_fields = ()
    else:
        text_fields = ("tool", "status", "call_id")
        object_fields = ()
        boolean_fields = ("success",)
    for field in text_fields:
        if field in payload and not isinstance(payload[field], str):
            raise ValueError(f"invalid session event payload {field}")
    for field in object_fields:
        if field in payload and not isinstance(payload[field], dict):
            raise ValueError(f"invalid session event payload {field}")
    for field in boolean_fields:
        if field in payload and type(payload[field]) is not bool:
            raise ValueError(f"invalid session event payload {field}")


def _validate_lifecycle_payload(kind: str, payload: dict[str, Any]) -> None:
    if payload.keys() != _LIFECYCLE_PAYLOAD_FIELDS[kind]:
        raise ValueError("invalid session event lifecycle payload fields")
    if kind == "session.started":
        valid = _sha256(payload["effective_lock_hash"]) and _sha256(
            payload["task_hash"]
        )
    elif kind == "input.accepted":
        attachments = payload["attachments"]
        valid = _sha256(payload["content_hash"]) and isinstance(attachments, list)
        if valid:
            for attachment in attachments:
                if (
                    not isinstance(attachment, dict)
                    or attachment.keys() != {"digest", "size_bytes", "media_type"}
                    or not _sha256(attachment["digest"])
                    or type(attachment["size_bytes"]) is not int
                    or attachment["size_bytes"] < 0
                    or not isinstance(attachment["media_type"], str)
                    or not attachment["media_type"]
                ):
                    valid = False
                    break
    elif kind == "approval.requested":
        valid = all(
            isinstance(payload[field], str) and bool(payload[field])
            for field in ("request_id", "operation")
        )
    elif kind == "approval.resolved":
        valid = (
            isinstance(payload["request_id"], str)
            and bool(payload["request_id"])
            and payload["decision"] in {"allow", "deny", "once", "always", "reject"}
        )
    elif kind == "session.reconfigured":
        valid = _sha256(payload["effective_lock_hash"]) and isinstance(
            payload["reason"], str
        )
    elif kind in {"session.paused", "session.canceled"}:
        valid = isinstance(payload["reason"], str)
        if kind == "session.canceled":
            valid = valid and payload["outcome"] == "canceled"
    elif kind == "session.resumed":
        valid = True
    elif kind == "session.completed":
        valid = payload["outcome"] == "completed" and isinstance(
            payload["summary"], str
        )
    else:
        valid = payload["outcome"] == "failed" and all(
            isinstance(payload[field], str) and bool(payload[field])
            for field in ("error", "detail")
        )
    if not valid:
        raise ValueError("invalid session event lifecycle payload")


def _validate_event_payload(
    kind: str, schema_version: str, payload: dict[str, Any]
) -> None:
    expected_schema = _EVENT_PAYLOAD_SCHEMA.get(kind)
    if expected_schema is None:
        raise ValueError("invalid session event kind")
    if schema_version != expected_schema:
        raise ValueError("invalid session event payload_schema_version")
    if kind in _LIFECYCLE_PAYLOAD_FIELDS:
        _validate_lifecycle_payload(kind, payload)
    else:
        _validate_kernel_payload(kind, payload)


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
    if value.get("schema_version") != "bb.public_session_event.v1":
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
    if visibility.get("redaction_state") not in {"none", "redacted"}:
        raise ValueError("invalid session event visibility.redaction_state")
    event_payload = value.get("payload")
    if not isinstance(event_payload, dict):
        raise ValueError("invalid session event payload")
    kind = _required_text(value.get("kind"), "kind")
    payload_schema_version = _required_text(
        value.get("payload_schema_version"), "payload_schema_version"
    )
    _validate_event_payload(kind, payload_schema_version, event_payload)
    return {
        "schema_version": "bb.public_session_event.v1",
        "event_id": _required_text(value.get("event_id"), "event_id"),
        "seq": sequence,
        "timestamp": _required_rfc3339_timestamp(value.get("timestamp"), "timestamp"),
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
        "kind": kind,
        "payload": event_payload,
        "payload_schema_version": payload_schema_version,
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

    def _bearer_headers(self) -> Dict[str, str]:
        if not self.auth_token:
            return {}
        _require_secure_bearer_transport(self.base_url)
        return {"Authorization": f"Bearer {self.auth_token}"}

    def _headers(self) -> Dict[str, str]:
        headers = self._bearer_headers()
        headers["Content-Type"] = "application/json"
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
        follow: bool = True,
    ) -> Generator[SessionEvent, None, int | None]:
        query: Dict[str, Any] = {"resume_token": resume_token, "limit": limit}
        if not follow:
            query["follow"] = "false"
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
    ) -> Generator[SessionEvent, None, int | None]:
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
        headers = self._bearer_headers()
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        resp = requests.request(
            method=binding.http_method,
            url=url,
            headers=headers,
            stream=True,
            timeout=self.timeout_s,
        )
        try:
            if not resp.ok:
                try:
                    payload: Any = resp.json()
                except Exception:
                    payload = resp.text
                raise ApiError("Event stream failed", resp.status_code, payload)

            data_lines: List[str] = []
            pending_cursor: int | None = None
            last_cursor: int | None = None
            for raw in resp.iter_lines(decode_unicode=True):
                if raw is None:
                    continue
                line = raw.strip("\r")
                if not line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        event = _session_event(payload, session_id, sse_id)
                        terminal = event["kind"] in {
                            "session.completed",
                            "session.failed",
                            "session.canceled",
                        }
                        if pending_cursor is not None:
                            last_cursor = pending_cursor
                        if terminal:
                            resp.close()
                        yield event
                        if terminal:
                            return last_cursor
                    elif pending_cursor is not None:
                        last_cursor = pending_cursor
                    sse_id = None
                    pending_cursor = None
                    continue
                if line.startswith("id:"):
                    sse_id = line[len("id:") :].lstrip()
                    if not sse_id.isdigit() or int(sse_id) < 1:
                        raise ValueError("invalid session event sequence")
                    pending_cursor = int(sse_id)
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].lstrip())
            if data_lines or pending_cursor is not None:
                raise ValueError("incomplete SSE frame")
            return last_cursor
        finally:
            resp.close()
