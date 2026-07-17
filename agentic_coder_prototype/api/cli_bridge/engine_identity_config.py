"""Process identity and fixed P30 E4 session-contract configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .events import (
    EventType,
    OVERFLOW_RECOVERY_ACTION,
    PROTOCOL_VERSION,
    REPLAY_CONTRACT_SCHEMA_VERSION,
    REPLAY_RETENTION_MAX_AGE_MS,
    REPLAY_RETENTION_MAX_EVENTS,
    SNAPSHOT_RECOVERY_ACTION,
)
from .models import (
    ErrorEnvelope,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInputRequest,
    SessionInputResponse,
    SessionSummary,
    SessionTurnCancelRequest,
    SessionTurnCancelResponse,
)

ENGINE_IDENTITY_SCHEMA_VERSION = "bb.engine_identity.v1"
P30_SESSION_CONTRACT_ID = "p30-e4-session-v1"
P30_SESSION_CONTRACT_SCHEMA_VERSION = "bb.p30.e4_session.v1"
# This value is intentionally fixed. A landed session-schema change makes readiness
# false until the contract change is explicitly reviewed and this digest is updated.
P30_SESSION_SCHEMA_SHA256 = "sha256:5757652c22d6aa2eb7a1cc8be1a40021d3f6a15df18d69ca22dc1916a400dbd4"
ENGINE_LAUNCH_ID_ENV = "BREADBOARD_ENGINE_LAUNCH_ID"
_ENGINE_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

P30_REQUIRED_SESSION_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/v1/sessions"),
        ("GET", "/v1/sessions/{session_id}"),
        ("POST", "/v1/sessions/{session_id}/input"),
        ("POST", "/v1/sessions/{session_id}/turns/{turn_id}/cancel"),
        ("GET", "/v1/sessions/{session_id}/events"),
        ("DELETE", "/v1/sessions/{session_id}"),
    }
)

P30_SESSION_ROUTE_BINDINGS: tuple[tuple[str, str, str, str], ...] = (
    ("POST", "/v1/sessions", "create_session", "create_session"),
    ("GET", "/v1/sessions/{session_id}", "get_session", "ensure_session"),
    ("POST", "/v1/sessions/{session_id}/input", "post_input", "send_input"),
    (
        "POST",
        "/v1/sessions/{session_id}/turns/{turn_id}/cancel",
        "cancel_turn",
        "cancel_turn",
    ),
    ("GET", "/v1/sessions/{session_id}/events", "stream_events", "prepare_event_stream"),
    ("DELETE", "/v1/sessions/{session_id}", "delete_session", "stop_session"),
)

P30_REQUIRED_SESSION_SERVICE_METHODS: tuple[str, ...] = (
    "create_session",
    "ensure_session",
    "send_input",
    "cancel_turn",
    "prepare_event_stream",
    "prepared_event_stream",
    "stop_session",
)


class EngineIdentityConfigError(RuntimeError):
    """Secret-safe process identity configuration failure."""


@dataclass(frozen=True)
class EngineProcessIdentity:
    pid: int
    engine_instance_id: str
    engine_boot_id: str
    launch_id: str
    launch_source: str
    started_at: datetime
    started_at_unix: float
    engine_artifact_sha256: str


def engine_source_artifact_sha256(source_root: Path) -> str:
    """Hash the exact Python source artifact served by this engine process."""

    root = source_root.resolve()
    source_paths = sorted(
        path
        for path in root.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    )
    if not source_paths:
        raise EngineIdentityConfigError("engine source artifact contains no Python source files")

    digest = hashlib.sha256(b"breadboard-engine-python-source-v1\0")
    for path in source_paths:
        relative_path = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def resolve_launch_identity(environ: Mapping[str, str]) -> tuple[str, str]:
    """Resolve supervisor metadata or create an explicit unmanaged fallback."""

    supplied = environ.get(ENGINE_LAUNCH_ID_ENV)
    if supplied is None:
        return secrets.token_urlsafe(32), "external_unmanaged"
    if _OPAQUE_ID_PATTERN.fullmatch(supplied) is None:
        raise EngineIdentityConfigError(
            f"{ENGINE_LAUNCH_ID_ENV} must be a 43-character URL-safe identifier"
        )
    return supplied, "supervisor"


def _new_process_identity(pid: int) -> EngineProcessIdentity:
    started_at_unix = time.time()
    launch_id, launch_source = resolve_launch_identity(os.environ)
    return EngineProcessIdentity(
        pid=pid,
        engine_instance_id=secrets.token_urlsafe(32),
        engine_boot_id=secrets.token_urlsafe(32),
        launch_id=launch_id,
        launch_source=launch_source,
        started_at=datetime.fromtimestamp(started_at_unix, tz=timezone.utc),
        started_at_unix=started_at_unix,
        engine_artifact_sha256=engine_source_artifact_sha256(_ENGINE_SOURCE_ROOT),
    )


class _ProcessIdentityProvider:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._identity: EngineProcessIdentity | None = _new_process_identity(os.getpid())
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self) -> None:
        self._lock = threading.Lock()
        self._identity = None

    def get(self) -> EngineProcessIdentity:
        pid = os.getpid()
        with self._lock:
            if self._identity is None or self._identity.pid != pid:
                self._identity = _new_process_identity(pid)
            return self._identity


# importlib.reload preserves the module dictionary. Keeping the provider when it
# already exists prevents an in-process module/app reload from rotating identity.
if "_PROCESS_IDENTITY_PROVIDER" not in globals():
    _PROCESS_IDENTITY_PROVIDER = _ProcessIdentityProvider()


def get_engine_process_identity() -> EngineProcessIdentity:
    return _PROCESS_IDENTITY_PROVIDER.get()


def _contract_schema(model: type[Any]) -> dict[str, Any]:
    """Return validation-relevant JSON Schema without descriptive metadata."""

    def strip_metadata(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip_metadata(item)
                for key, item in value.items()
                if key not in {"description", "title"}
            }
        if isinstance(value, list):
            return [strip_metadata(item) for item in value]
        return value

    return strip_metadata(model.model_json_schema(mode="validation"))


P30_SESSION_EVENT_STREAM_CONTRACT: dict[str, Any] = {
    "media_type": "text/event-stream",
    "framing": {
        "data": "compact_json_event_envelope",
        "id": "stable_cursor_sequence_only",
    },
    "envelope_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "stable_cursor",
            "type",
            "session_id",
            "turn",
            "timestamp",
            "timestamp_ms",
            "protocol_version",
            "payload",
        ],
        "properties": {
            "stable_cursor": {"type": "boolean"},
            "type": {"enum": [event_type.value for event_type in EventType]},
            "session_id": {"type": "string"},
            "turn": {"type": ["integer", "null"]},
            "timestamp": {"type": "integer"},
            "timestamp_ms": {"type": "integer"},
            "protocol_version": {"const": PROTOCOL_VERSION},
            "payload": {"type": "object"},
            "id": {"type": "string"},
            "seq": {"type": ["integer", "null"]},
            "input_id": {"type": "string"},
            "turn_id": {"type": "string"},
            "classification": {"type": "string"},
            "family": {"type": "string"},
            "actor": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind"],
                "properties": {"kind": {"type": "string"}},
            },
            "visibility": {"type": "string"},
        },
    },
    "payload_schemas": {
        EventType.TURN_COMPLETED.value: {
            "type": "object",
            "additionalProperties": False,
            "maxProperties": 0,
        },
        EventType.TURN_FAILED.value: {
            "type": "object",
            "additionalProperties": False,
            "required": ["error"],
            "properties": {
                "error": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code"],
                    "properties": {"code": {"type": "string", "maxLength": 128}},
                }
            },
        },
        EventType.TURN_CANCELLED.value: {
            "type": "object",
            "additionalProperties": False,
            "required": ["reason"],
            "properties": {
                "reason": {"enum": ["user_requested", "timeout", "superseded"]}
            },
        },
        EventType.STREAM_GAP.value: {
            "type": "object",
            "required": [
                "code",
                "last_safely_delivered_cursor",
                "recovery",
                "replayRetention",
                "headSequence",
                "retainedHistory",
                "sessionReplayContractDigest",
            ],
        },
        EventType.STREAM_OPEN.value: {
            "type": "object",
            "required": [
                "replayRetention",
                "headSequence",
                "retainedHistory",
                "sessionReplayContractDigest",
            ],
        },
        "*": {"type": "object"},
    },
    "terminal_event_types": [
        EventType.TURN_COMPLETED.value,
        EventType.TURN_FAILED.value,
        EventType.TURN_CANCELLED.value,
    ],
    "resume": {
        "exclusive_cursor": True,
        "query_precedes_last_event_id_header": True,
        "last_event_id_header": "Last-Event-ID",
        "gap_event_type": EventType.STREAM_GAP.value,
        "open_event_type": EventType.STREAM_OPEN.value,
        "snapshot_recovery_action": SNAPSHOT_RECOVERY_ACTION,
        "overflow_recovery_action": OVERFLOW_RECOVERY_ACTION,
        "retention_schema_version": REPLAY_CONTRACT_SCHEMA_VERSION,
        "max_events": REPLAY_RETENTION_MAX_EVENTS,
        "max_age_ms": REPLAY_RETENTION_MAX_AGE_MS,
    },
}


def p30_session_contract_schema(
    *,
    http_contract: dict[str, Any],
    handler_bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonical complete schema for only the landed bb-89n.11/.12 contract."""

    return {
        "schema_version": P30_SESSION_CONTRACT_SCHEMA_VERSION,
        "contract_id": P30_SESSION_CONTRACT_ID,
        "protocol_version": PROTOCOL_VERSION,
        "http": http_contract,
        "handler_bindings": handler_bindings,
        "event_stream": P30_SESSION_EVENT_STREAM_CONTRACT,
    }


def p30_session_schema_sha256(contract: dict[str, Any]) -> str:
    encoded = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
