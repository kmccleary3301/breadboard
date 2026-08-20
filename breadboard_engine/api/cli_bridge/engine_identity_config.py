"""Process identity and fixed P30 E4 session-contract configuration."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import re
import select
import secrets
import stat
import threading
import time
import sys
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
    replay_configuration_digest,
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
P30_SESSION_REPLAY_CONTRACT_DIGEST = (
    "sha256:a107aea87bdc7075d68495d3c0bf2b68e85e38a2b2fef1000bf3f1eaee77f743"
)
ENGINE_LAUNCH_ID_ENV = "BREADBOARD_ENGINE_LAUNCH_ID"
ENGINE_BOOTSTRAP_FD_ENV = "BREADBOARD_LIFECYCLE_BOOTSTRAP_FD"
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
    os_process_start_token: str
    engine_instance_id: str
    engine_boot_id: str
    launch_id: str
    launch_source: str
    started_at: datetime
    started_at_unix: float
    engine_artifact_sha256: str



class LaunchBootstrapVerifier:
    """One-use verifier whose credential arrives only through a protected descriptor."""

    def __init__(self, secret: bytearray, identity: EngineProcessIdentity) -> None:
        self._binding = (
            identity.launch_id,
            identity.engine_boot_id,
            identity.engine_instance_id,
        )
        self._digest: bytearray | None = bytearray(self._bound_digest(secret, self._binding))
        self._consumed = False
        self._challenge: tuple[str, str, float] | None = None

    @staticmethod
    def _bound_digest(secret: bytearray, binding: tuple[str, str, str]) -> bytes:
        digest = hashlib.sha256(b"breadboard-p30-launch-bootstrap-v1\0")
        for value in binding:
            encoded = value.encode("ascii")
            digest.update(len(encoded).to_bytes(2, "big"))
            digest.update(encoded)
        digest.update(len(secret).to_bytes(2, "big"))
        digest.update(secret)
        return digest.digest()

    @classmethod
    def from_inherited_fd(
        cls,
        fd: int,
        identity: EngineProcessIdentity,
        *,
        startup_deadline_seconds: float = 1.0,
    ) -> "LaunchBootstrapVerifier":
        material = bytearray()
        try:
            descriptor = os.fstat(fd)
            if descriptor.st_uid != os.geteuid():
                raise EngineIdentityConfigError("launch bootstrap descriptor owner is invalid")
            if stat.S_ISREG(descriptor.st_mode) and descriptor.st_mode & 0o077:
                raise EngineIdentityConfigError("launch bootstrap descriptor permissions are invalid")
            if startup_deadline_seconds <= 0:
                raise EngineIdentityConfigError("launch bootstrap startup deadline is invalid")
            os.set_blocking(fd, False)
            deadline = time.monotonic() + startup_deadline_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EngineIdentityConfigError(
                        "launch bootstrap descriptor did not reach EOF before the startup deadline"
                    )
                readable, _, _ = select.select([fd], [], [], remaining)
                if not readable:
                    raise EngineIdentityConfigError(
                        "launch bootstrap descriptor did not reach EOF before the startup deadline"
                    )
                try:
                    chunk = os.read(fd, 44)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                material.extend(chunk)
                if len(material) > 43:
                    raise EngineIdentityConfigError("launch bootstrap descriptor payload is invalid")
            if len(material) != 43 or _OPAQUE_ID_PATTERN.fullmatch(
                material.decode("ascii", "ignore")
            ) is None:
                raise EngineIdentityConfigError("launch bootstrap descriptor payload is invalid")
            return cls(material, identity)
        finally:
            for index in range(len(material)):
                material[index] = 0
            try:
                os.close(fd)
            except OSError:
                pass

    @classmethod
    def from_environ(
        cls,
        environ: dict[str, str],
        identity: EngineProcessIdentity,
    ) -> "LaunchBootstrapVerifier | None":
        raw_fd = environ.pop(ENGINE_BOOTSTRAP_FD_ENV, None)
        if raw_fd is None:
            return None
        if not raw_fd.isascii() or not raw_fd.isdecimal():
            raise EngineIdentityConfigError("launch bootstrap descriptor must be a decimal integer")
        fd = int(raw_fd)
        if fd < 3:
            raise EngineIdentityConfigError("launch bootstrap descriptor must not use a standard stream")
        return cls.from_inherited_fd(fd, identity)

    @property
    def consumed(self) -> bool:
        return self._consumed

    @property
    def verifier_wiped(self) -> bool:
        return self._digest is None

    def matches_bootstrap_secret(
        self,
        candidate: bytearray,
        identity: EngineProcessIdentity,
    ) -> bool:
        binding = (
            identity.launch_id,
            identity.engine_boot_id,
            identity.engine_instance_id,
        )
        candidate_digest = bytearray(self._bound_digest(candidate, binding))
        try:
            return (
                binding == self._binding
                and self._digest is not None
                and secrets.compare_digest(candidate_digest, self._digest)
            )
        finally:
            for index in range(len(candidate_digest)):
                candidate_digest[index] = 0

    def issue_challenge(
        self,
        identity: EngineProcessIdentity,
        *,
        now: float,
    ) -> tuple[str, str, float] | None:
        binding = (
            identity.launch_id,
            identity.engine_boot_id,
            identity.engine_instance_id,
        )
        if self._consumed or self._digest is None or binding != self._binding:
            return None
        if self._challenge is not None and self._challenge[2] > now:
            return self._challenge
        challenge_id = secrets.token_urlsafe(32)
        challenge = secrets.token_urlsafe(32)
        expires_at = now + 10
        self._challenge = (challenge_id, challenge, expires_at)
        return self._challenge

    def consume_proof(
        self,
        challenge_id: str,
        supplied_proof: str,
        owner_credential: bytearray,
        identity: EngineProcessIdentity,
        *,
        now: float,
    ) -> bool:
        message = bytearray()
        candidate = bytearray()
        try:
            binding = (
                identity.launch_id,
                identity.engine_boot_id,
                identity.engine_instance_id,
            )
            owner_digest = bytearray(self._bound_digest(owner_credential, binding))
            if self._digest is not None and secrets.compare_digest(owner_digest, self._digest):
                return False
            challenge = self._challenge
            if (
                self._consumed
                or self._digest is None
                or binding != self._binding
                or challenge is None
                or challenge_id != challenge[0]
                or now >= challenge[2]
            ):
                return False
            message.extend(b"breadboard-p30-launch-bootstrap-proof-v1\0")
            for value in (*binding, challenge[0], challenge[1]):
                encoded = value.encode("ascii")
                message.extend(len(encoded).to_bytes(2, "big"))
                message.extend(encoded)
            message.extend(len(owner_credential).to_bytes(2, "big"))
            message.extend(owner_credential)
            candidate.extend(hmac.new(self._digest, message, hashlib.sha256).digest())
            expected = "sha256:" + candidate.hex()
            if not secrets.compare_digest(supplied_proof, expected):
                return False
            self._consumed = True
            self._challenge = None
            for index in range(len(self._digest)):
                self._digest[index] = 0
            self._digest = None
            return True
        finally:
            for value in (message, candidate, owner_digest if "owner_digest" in locals() else bytearray()):
                for index in range(len(value)):
                    value[index] = 0

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


@dataclass(frozen=True)
class _OSProcessStart:
    token: str
    started_at_unix: float


class _DarwinProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_process_start(pid: int) -> _OSProcessStart:
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        info = _DarwinProcBSDInfo()
        size = ctypes.sizeof(info)
        read_size = proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise EngineIdentityConfigError(
            "OS process start identity is unavailable"
        ) from exc
    if (
        read_size != size
        or info.pbi_pid != pid
        or info.pbi_start_tvsec <= 0
        or info.pbi_start_tvusec >= 1_000_000
    ):
        raise EngineIdentityConfigError("OS process start identity is unavailable")
    return _OSProcessStart(
        token=f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}",
        started_at_unix=(
            float(info.pbi_start_tvsec) + float(info.pbi_start_tvusec) / 1_000_000
        ),
    )


def _linux_process_start(pid: int) -> _OSProcessStart:
    try:
        stat_payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        stat_fields = stat_payload[stat_payload.rindex(")") + 2 :].split()
        start_ticks = int(stat_fields[19])
        clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
        boot_time_line = next(
            line
            for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
            if line.startswith("btime ")
        )
        boot_time = int(boot_time_line.split()[1])
    except (IndexError, OSError, StopIteration, ValueError) as exc:
        raise EngineIdentityConfigError(
            "OS process start identity is unavailable"
        ) from exc
    if (
        start_ticks <= 0
        or clock_ticks <= 0
        or re.fullmatch(r"[0-9a-f-]{36}", boot_id) is None
    ):
        raise EngineIdentityConfigError("OS process start identity is unavailable")
    return _OSProcessStart(
        token=f"linux:{boot_id}:{start_ticks}",
        started_at_unix=float(boot_time) + float(start_ticks) / float(clock_ticks),
    )


def _read_os_process_start(pid: int) -> _OSProcessStart:
    if pid <= 0:
        raise EngineIdentityConfigError("OS process start identity is unavailable")
    if sys.platform == "darwin":
        return _darwin_process_start(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_start(pid)
    raise EngineIdentityConfigError("OS process start identity is unavailable")


def os_process_start_token(pid: int) -> str:
    """Return the kernel-derived start token for an extant process."""

    return _read_os_process_start(pid).token


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
    process_start = _read_os_process_start(pid)
    launch_id, launch_source = resolve_launch_identity(os.environ)
    return EngineProcessIdentity(
        pid=pid,
        os_process_start_token=process_start.token,
        engine_instance_id=secrets.token_urlsafe(32),
        engine_boot_id=secrets.token_urlsafe(32),
        launch_id=launch_id,
        launch_source=launch_source,
        started_at=datetime.fromtimestamp(process_start.started_at_unix, tz=timezone.utc),
        started_at_unix=process_start.started_at_unix,
        engine_artifact_sha256=engine_source_artifact_sha256(_ENGINE_SOURCE_ROOT),
    )


def _new_process_identity_and_bootstrap(
    pid: int,
) -> tuple[EngineProcessIdentity, LaunchBootstrapVerifier | None]:
    identity = _new_process_identity(pid)
    return identity, LaunchBootstrapVerifier.from_environ(os.environ, identity)


class _ProcessIdentityProvider:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._identity, self._bootstrap_verifier = _new_process_identity_and_bootstrap(os.getpid())
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self) -> None:
        self._lock = threading.Lock()
        self._identity = None
        self._bootstrap_verifier = None

    def get(self) -> EngineProcessIdentity:
        pid = os.getpid()
        with self._lock:
            if self._identity is None or self._identity.pid != pid:
                self._identity, self._bootstrap_verifier = _new_process_identity_and_bootstrap(pid)
            return self._identity

    def bootstrap_verifier(self) -> LaunchBootstrapVerifier | None:
        self.get()
        with self._lock:
            return self._bootstrap_verifier


# importlib.reload preserves the module dictionary. Keeping the provider when it
# already exists prevents an in-process module/app reload from rotating identity.
if "_PROCESS_IDENTITY_PROVIDER" not in globals():
    _PROCESS_IDENTITY_PROVIDER = _ProcessIdentityProvider()


def get_engine_process_identity() -> EngineProcessIdentity:
    return _PROCESS_IDENTITY_PROVIDER.get()


def get_launch_bootstrap_verifier() -> LaunchBootstrapVerifier | None:
    return _PROCESS_IDENTITY_PROVIDER.bootstrap_verifier()


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


P30_SESSION_BASELINE_HTTP = {'delivery_chaos_config': None, 'missing_routes': [], 'operations': [{'method': 'POST', 'parameters': [], 'path': '/v1/sessions', 'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionCreateRequest'}}}, 'required': True}, 'responses': {'200': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionCreateResponse'}}}, 'description': 'Successful Response'}, '400': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Bad Request'}, '422': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}, 'description': 'Validation Error'}}}, {'method': 'GET', 'parameters': [{'in': 'path', 'name': 'session_id', 'required': True, 'schema': {'title': 'Session Id', 'type': 'string'}}], 'path': '/v1/sessions/{session_id}', 'requestBody': None, 'responses': {'200': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionSummary'}}}, 'description': 'Successful Response'}, '404': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Not Found'}, '422': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}, 'description': 'Validation Error'}}}, {'method': 'POST', 'parameters': [{'in': 'path', 'name': 'session_id', 'required': True, 'schema': {'title': 'Session Id', 'type': 'string'}}], 'path': '/v1/sessions/{session_id}/input', 'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionInputRequest'}}}, 'required': True}, 'responses': {'202': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionInputResponse'}}}, 'description': 'Successful Response'}, '400': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Bad Request'}, '404': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Not Found'}, '409': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Conflict'}, '422': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}, 'description': 'Validation Error'}}}, {'method': 'POST', 'parameters': [{'in': 'path', 'name': 'session_id', 'required': True, 'schema': {'title': 'Session Id', 'type': 'string'}}, {'in': 'path', 'name': 'turn_id', 'required': True, 'schema': {'title': 'Turn Id', 'type': 'string'}}], 'path': '/v1/sessions/{session_id}/turns/{turn_id}/cancel', 'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionTurnCancelRequest'}}}, 'required': True}, 'responses': {'202': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SessionTurnCancelResponse'}}}, 'description': 'Successful Response'}, '400': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Bad Request'}, '404': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Not Found'}, '409': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Conflict'}, '422': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}, 'description': 'Validation Error'}}}, {'method': 'GET', 'parameters': [{'in': 'path', 'name': 'session_id', 'required': True, 'schema': {'title': 'Session Id', 'type': 'string'}}, {'in': 'query', 'name': 'replay', 'required': False, 'schema': {'default': False, 'title': 'Replay', 'type': 'boolean'}}, {'in': 'query', 'name': 'limit', 'required': False, 'schema': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'title': 'Limit'}}, {'in': 'query', 'name': 'from_id', 'required': False, 'schema': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'From Id'}}], 'path': '/v1/sessions/{session_id}/events', 'requestBody': None, 'responses': {'200': {'content': {'application/json': {'schema': {}}}, 'description': 'Successful Response'}, '404': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Not Found'}, '422': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}, 'description': 'Validation Error'}}}, {'method': 'DELETE', 'parameters': [{'in': 'path', 'name': 'session_id', 'required': True, 'schema': {'title': 'Session Id', 'type': 'string'}}], 'path': '/v1/sessions/{session_id}', 'requestBody': None, 'responses': {'204': {'description': 'Successful Response'}, '404': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorResponse'}}}, 'description': 'Not Found'}, '422': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}, 'description': 'Validation Error'}}}], 'schemas': {'ErrorResponse': {'description': 'Backward-compatible OpenAPI name for legacy response declarations.', 'properties': {'detail': {'anyOf': [{'type': 'string'}, {'additionalProperties': True, 'type': 'object'}, {'type': 'null'}], 'title': 'Detail'}, 'error': {'title': 'Error', 'type': 'string'}, 'path': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Path'}}, 'required': ['error'], 'title': 'ErrorResponse', 'type': 'object'}, 'HTTPValidationError': {'properties': {'detail': {'items': {'$ref': '#/components/schemas/ValidationError'}, 'title': 'Detail', 'type': 'array'}}, 'title': 'HTTPValidationError', 'type': 'object'}, 'SessionCreateRequest': {'description': 'Incoming payload for POST /sessions.', 'properties': {'config_path': {'description': 'Path to agent config YAML/JSON.', 'title': 'Config Path', 'type': 'string'}, 'max_steps': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'description': 'Override max steps for the loop.', 'title': 'Max Steps'}, 'metadata': {'anyOf': [{'additionalProperties': True, 'type': 'object'}, {'type': 'null'}], 'description': 'Opaque metadata for UX features.', 'title': 'Metadata'}, 'overrides': {'anyOf': [{'additionalProperties': True, 'type': 'object'}, {'type': 'null'}], 'description': 'Dotted-key override map.', 'title': 'Overrides'}, 'permission_mode': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'description': 'Agent permission preset.', 'title': 'Permission Mode'}, 'stream': {'default': True, 'description': 'Request streaming responses when supported.', 'title': 'Stream', 'type': 'boolean'}, 'task': {'default': '', 'description': 'Optional initial task; omit for an idle session.', 'title': 'Task', 'type': 'string'}, 'workspace': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'description': 'Optional explicit workspace root.', 'title': 'Workspace'}}, 'required': ['config_path'], 'title': 'SessionCreateRequest', 'type': 'object'}, 'SessionCreateResponse': {'properties': {'created_at': {'format': 'date-time', 'title': 'Created At', 'type': 'string'}, 'logging_dir': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Logging Dir'}, 'session_id': {'title': 'Session Id', 'type': 'string'}, 'status': {'$ref': '#/components/schemas/SessionStatus'}}, 'required': ['session_id', 'status', 'created_at'], 'title': 'SessionCreateResponse', 'type': 'object'}, 'SessionInputRequest': {'properties': {'attachments': {'anyOf': [{'items': {'type': 'string'}, 'type': 'array'}, {'type': 'null'}], 'description': 'Attachment IDs returned by /attachments.', 'title': 'Attachments'}, 'client_message_id': {'description': 'Stable idempotency key for this input.', 'title': 'Client Message Id', 'type': 'string'}, 'content': {'description': 'User supplied input text.', 'title': 'Content', 'type': 'string'}}, 'required': ['content', 'client_message_id'], 'title': 'SessionInputRequest', 'type': 'object'}, 'SessionInputResponse': {'properties': {'client_message_id': {'title': 'Client Message Id', 'type': 'string'}, 'disposition': {'enum': ['started', 'queued', 'deduplicated'], 'title': 'Disposition', 'type': 'string'}, 'input_id': {'title': 'Input Id', 'type': 'string'}, 'original_disposition': {'enum': ['started', 'queued'], 'title': 'Original Disposition', 'type': 'string'}, 'status': {'const': 'accepted', 'default': 'accepted', 'title': 'Status', 'type': 'string'}, 'turn_id': {'title': 'Turn Id', 'type': 'string'}}, 'required': ['client_message_id', 'input_id', 'turn_id', 'disposition', 'original_disposition'], 'title': 'SessionInputResponse', 'type': 'object'}, 'SessionStatus': {'description': 'Lifecycle marker for a session.', 'enum': ['starting', 'running', 'completed', 'failed', 'stopped'], 'title': 'SessionStatus', 'type': 'string'}, 'SessionSummary': {'properties': {'active_turn_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Active Turn Id'}, 'completion_summary': {'anyOf': [{'additionalProperties': True, 'type': 'object'}, {'type': 'null'}], 'title': 'Completion Summary'}, 'created_at': {'format': 'date-time', 'title': 'Created At', 'type': 'string'}, 'earliestRetainedEventId': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Earliestretainedeventid'}, 'earliestRetainedSequence': {'anyOf': [{'type': 'integer'}, {'type': 'null'}], 'title': 'Earliestretainedsequence'}, 'headEventId': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Headeventid'}, 'headSequence': {'default': 0, 'title': 'Headsequence', 'type': 'integer'}, 'last_activity_at': {'format': 'date-time', 'title': 'Last Activity At', 'type': 'string'}, 'logging_dir': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Logging Dir'}, 'metadata': {'anyOf': [{'additionalProperties': True, 'type': 'object'}, {'type': 'null'}], 'title': 'Metadata'}, 'mode': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Mode'}, 'model': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'title': 'Model'}, 'queued_turn_count': {'default': 0, 'title': 'Queued Turn Count', 'type': 'integer'}, 'replayRetention': {'additionalProperties': True, 'title': 'Replayretention', 'type': 'object'}, 'retainedHistory': {'default': 'complete', 'enum': ['complete', 'partial'], 'title': 'Retainedhistory', 'type': 'string'}, 'reward_summary': {'anyOf': [{'additionalProperties': True, 'type': 'object'}, {'type': 'null'}], 'title': 'Reward Summary'}, 'sessionReplayContractDigest': {'default': '', 'title': 'Sessionreplaycontractdigest', 'type': 'string'}, 'session_id': {'title': 'Session Id', 'type': 'string'}, 'status': {'$ref': '#/components/schemas/SessionStatus'}, 'terminalEventEnvelopes': {'items': {'additionalProperties': True, 'type': 'object'}, 'title': 'Terminaleventenvelopes', 'type': 'array'}, 'terminalTurns': {'items': {'additionalProperties': True, 'type': 'object'}, 'title': 'Terminalturns', 'type': 'array'}, 'turn_admission': {'$ref': '#/components/schemas/TurnAdmission', 'default': 'idle'}}, 'required': ['session_id', 'status', 'created_at', 'last_activity_at'], 'title': 'SessionSummary', 'type': 'object'}, 'SessionTurnCancelRequest': {'properties': {'cancellation_request_key': {'description': 'Stable idempotency key for this cancellation.', 'title': 'Cancellation Request Key', 'type': 'string'}, 'reason': {'default': 'user_requested', 'enum': ['user_requested', 'timeout', 'superseded'], 'title': 'Reason', 'type': 'string'}}, 'required': ['cancellation_request_key'], 'title': 'SessionTurnCancelRequest', 'type': 'object'}, 'SessionTurnCancelResponse': {'properties': {'cancellation_request_id': {'title': 'Cancellation Request Id', 'type': 'string'}, 'cancellation_request_key': {'title': 'Cancellation Request Key', 'type': 'string'}, 'disposition': {'enum': ['cancellation_requested', 'queued_cancelled', 'deduplicated'], 'title': 'Disposition', 'type': 'string'}, 'input_id': {'title': 'Input Id', 'type': 'string'}, 'original_disposition': {'enum': ['cancellation_requested', 'queued_cancelled'], 'title': 'Original Disposition', 'type': 'string'}, 'status': {'const': 'accepted', 'default': 'accepted', 'title': 'Status', 'type': 'string'}, 'turn_id': {'title': 'Turn Id', 'type': 'string'}}, 'required': ['cancellation_request_id', 'cancellation_request_key', 'input_id', 'turn_id', 'disposition', 'original_disposition'], 'title': 'SessionTurnCancelResponse', 'type': 'object'}, 'TurnAdmission': {'description': 'Whether a session can start a newly admitted turn immediately.', 'enum': ['idle', 'active'], 'title': 'TurnAdmission', 'type': 'string'}, 'ValidationError': {'properties': {'ctx': {'title': 'Context', 'type': 'object'}, 'input': {'title': 'Input'}, 'loc': {'items': {'anyOf': [{'type': 'string'}, {'type': 'integer'}]}, 'title': 'Location', 'type': 'array'}, 'msg': {'title': 'Message', 'type': 'string'}, 'type': {'title': 'Error Type', 'type': 'string'}}, 'required': ['loc', 'msg', 'type'], 'title': 'ValidationError', 'type': 'object'}}}
