"""Immutable events and the deterministic Session read model."""

from __future__ import annotations
import base64
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.modules import (
    CheckpointEnvelope,
    CheckpointProposal,
    MAX_CHECKPOINT_BYTES,
    ModuleInput,
    OutputEnvelope,
    RequestKey,
)
from .artifacts import ArtifactRef
from breadboard.product.projection import Projected, ProjectionSource


def _sync(stream: Any) -> None:
    stream.flush()
    os.fsync(stream.fileno())


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self.stream = os.fdopen(
            os.open(
                path.with_name(f".{path.name}.lock"),
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            ),
            "a+b",
            buffering=0,
        )

    def __enter__(self) -> "ProcessLock":
        if os.name == "nt":
            import msvcrt

            self.stream.seek(0, os.SEEK_END)
            self.stream.write(b"\0") if not self.stream.tell() else None
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_LOCK, 1)
            self.unlock = lambda: (
                self.stream.seek(0),
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1),
            )
        else:
            import fcntl

            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
            self.unlock = lambda: fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        return self

    def __exit__(self, *_: object) -> None:
        self.unlock()
        self.stream.close()


class Clock(Protocol):
    def now(self) -> str: ...


class IdSource(Protocol):
    def new_id(self) -> str: ...


class EventSink(Protocol):
    def append(self, event: object) -> None: ...


class SystemClock:
    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class UUIDSource:
    def new_id(self) -> str:
        return str(uuid4())


class _SinkState:
    def __init__(self) -> None:
        self.lock, self.poisoned = RLock(), set()


_STATES = tuple(_SinkState() for _ in range(256))


class JsonlEventSink:
    def __init__(self, path: str | Path, *, max_bytes: int | None = None) -> None:
        self.path = Path(path).resolve()
        self._max_bytes = max_bytes
        path, state = self.path, _STATES[hash(self.path) % len(_STATES)]
        with state.lock:
            self._mkdir_parent(path.parent)
        with state.lock, ProcessLock(path):
            self._recover(path, state)

    @classmethod
    def _for_existing_path(
        cls,
        path: str | Path,
        *,
        max_bytes: int | None = None,
    ) -> "JsonlEventSink":
        sink = cls.__new__(cls)
        sink.path = Path(path).resolve()
        sink._max_bytes = max_bytes
        return sink

    def _mkdir_parent(self, path: Path) -> None:
        if path.exists():
            return
        self._mkdir_parent(path.parent)
        try:
            path.mkdir()
        except FileExistsError:
            return
        try:
            self._sync_parent(path)
        except BaseException:
            path.rmdir()
            raise

    def _transaction_paths(self, path: Path) -> tuple[Path, Path]:
        wal = path.with_name(f".{path.name}.txn")
        return wal, wal.with_name(f"{wal.name}.tmp")

    def _recover(self, path: Path, state: _SinkState) -> None:
        wal, temporary = self._transaction_paths(path)
        temporary.unlink(missing_ok=True)
        try:
            if not wal.exists():
                state.poisoned.discard(path)
                return
            offset = int(wal.read_text(encoding="ascii"))
            if path.exists():
                with path.open("r+b", buffering=0) as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    if offset < 0 or offset > size:
                        raise ValueError("invalid event transaction offset")
                    stream.truncate(offset)
                    _sync(stream)
            elif offset:
                raise ValueError("event transaction references a missing log")
            wal.unlink()
            self._sync_parent(path)
            state.poisoned.discard(path)
        except BaseException:
            state.poisoned.add(path)
            raise RuntimeError("event sink recovery failed")

    def _begin(self, path: Path, offset: int) -> None:
        wal, temporary = self._transaction_paths(path)
        data = str(offset).encode("ascii")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if os.write(descriptor, data) != len(data):
                raise OSError("short event transaction write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, wal)
        self._sync_parent(path)

    def _finish(self, path: Path) -> None:
        wal, temporary = self._transaction_paths(path)
        temporary.unlink(missing_ok=True)
        wal.unlink(missing_ok=True)
        self._sync_parent(path)

    def _append_body(self, event: object, path: Path, state: _SinkState) -> None:
        payload = (
            json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()  # type: ignore[attr-defined]
        if path in state.poisoned:
            raise RuntimeError("event sink is poisoned after an unconfirmed rollback")
        self._recover(path, state)
        try:
            stream = path.open("x+b", buffering=0)
            created = True
        except FileExistsError:
            stream = path.open("a+b", buffering=0)
            created = False
        try:
            stream.seek(0, os.SEEK_END)
            offset = stream.tell()
            if self._max_bytes is not None and offset + len(payload) > self._max_bytes:
                raise RuntimeError("event journal exceeds byte limit")
            try:
                self._begin(path, offset)
                if stream.write(payload) != len(payload):
                    raise OSError("short event sink write")
                _sync(stream)
                self._finish(path)
            except BaseException:
                try:
                    stream.seek(offset)
                    stream.truncate()
                    _sync(stream)
                    self._finish(path)
                    if created:
                        stream.close()
                        path.unlink()
                        self._sync_parent(path)
                except BaseException:
                    state.poisoned.add(path)
                raise
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def append(self, event: object) -> None:
        path = Path(self.path).resolve()
        state = _STATES[hash(path) % len(_STATES)]  # type: ignore[attr-defined]
        with state.lock, ProcessLock(path):
            self._append_body(event, path, state)

    def _append_with_process_lock(self, event: object) -> None:
        path = Path(self.path).resolve()
        state = _STATES[hash(path) % len(_STATES)]  # type: ignore[attr-defined]
        with state.lock:
            self._append_body(event, path, state)

    def _sync_parent(self, path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class NullEventSink:
    def append(self, event: object) -> None:
        return None


_OBSERVATION_EVENT_KINDS = frozenset({"assistant_message", "tool_call", "tool_result"})
_ADOPTION_EVENT_KIND = "session.adoption_committed"
_EVENT_KINDS = (
    frozenset(
        {
            "session.started",
            "input.accepted",
            "module_output",
            "annotation",
            "context.compacted",
            "approval.requested",
            "approval.resolved",
            "session.reconfigured",
            _ADOPTION_EVENT_KIND,
            "session.paused",
            "session.resumed",
            "session.completed",
            "session.failed",
            "session.canceled",
        }
    )
    | _OBSERVATION_EVENT_KINDS
)
_ALLOWED = MappingProxyType(
    {
        "input.accepted": ("running",),
        "module_output": ("running",),
        "assistant_message": ("running",),
        "tool_call": ("running",),
        "tool_result": ("running",),
        "annotation": (
            "running",
            "awaiting_approval",
            "paused",
            "completed",
            "failed",
            "canceled",
        ),
        "context.compacted": ("running",),
        "approval.requested": ("running",),
        "approval.resolved": ("awaiting_approval",),
        "session.paused": ("running",),
        "session.reconfigured": ("running", "awaiting_approval", "paused"),
        _ADOPTION_EVENT_KIND: ("running", "awaiting_approval", "paused"),
        "session.resumed": ("paused",),
        "session.completed": ("running",),
        "session.failed": ("running", "awaiting_approval", "paused"),
        "session.canceled": ("running", "awaiting_approval", "paused"),
    }
)
_STATUSES, _DECISIONS = (
    frozenset(
        {"running", "awaiting_approval", "paused", "completed", "failed", "canceled"}
    ),
    frozenset({"allow", "deny", "once", "always", "reject"}),
)
_TERMINAL = {
    "session.completed": ("completed", ("summary",)),
    "session.failed": ("failed", ("error", "detail")),
    "session.canceled": ("canceled", ("reason",)),
}


def _string(value: Any, name: str, populated: bool = True) -> str:
    if type(value) is not str or populated and not value:
        raise ValueError(f"{name} must be a{' non-empty' if populated else ''} string")
    return value


def _sha256(value: Any, name: str) -> str:
    if (
        len(value := _string(value, name)) != 71
        or not value.startswith("sha256:")
        or any(c not in "0123456789abcdef" for c in value[7:])
    ):
        raise ValueError(f"{name} must be an exact lowercase sha256 hash")
    return value


def _validate_annotation_payload(payload: Mapping[str, Any]) -> None:
    required = {
        "annotation_id",
        "message_id",
        "trajectory_id",
        "label",
        "author",
        "generation",
    }
    if set(payload) != required:
        raise ValueError(
            "annotation payload must contain only stable annotation fields"
        )
    for name in required:
        _string(payload.get(name), name)


_RAW_FACT_ID = re.compile(r"ctn_[0-9]{6,}")


def validate_raw_fact_ids(values: Any, name: str = "raw_fact_ids") -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    facts = tuple(values)
    if any(
        type(value) is not str or _RAW_FACT_ID.fullmatch(value) is None
        for value in facts
    ):
        raise ValueError(f"{name} must contain canonical C-Tree identities")
    if len(set(facts)) != len(facts):
        raise ValueError(f"{name} must not contain duplicates")
    return facts


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _validate_effective_context(context: bytes) -> None:
    try:
        messages = json.loads(
            context.decode("utf-8"),
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("effective_context must be UTF-8 JSON") from error
    if not isinstance(messages, list) or any(
        not isinstance(message, dict) for message in messages
    ):
        raise ValueError("effective_context must be a JSON array of message objects")


def _decode_compaction_context(payload: Mapping[str, Any]) -> bytes:
    if payload.get("context_encoding") != "base64":
        raise ValueError("compaction context_encoding must be base64")
    encoded = _string(payload.get("effective_context"), "effective_context", False)
    try:
        context = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("effective_context must be canonical base64") from error
    if base64.b64encode(context).decode("ascii") != encoded:
        raise ValueError("effective_context must be canonical base64")
    if _hash_bytes(context) != payload.get("context_sha256"):
        raise ValueError("compaction context_sha256 does not match effective_context")
    _validate_effective_context(context)
    return context


def _validate_compaction_payload(payload: Mapping[str, Any]) -> None:
    required = {
        "compaction_index",
        "source_sequence_start",
        "source_sequence_end",
        "context_encoding",
        "effective_context",
        "context_sha256",
        "raw_fact_ids",
        "shadowed_raw_fact_ids",
    }
    if set(payload) != required:
        raise ValueError(
            "context.compacted payload must contain only stable compaction fields"
        )
    for name in ("compaction_index", "source_sequence_start", "source_sequence_end"):
        value = payload.get(name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if payload["source_sequence_start"] > payload["source_sequence_end"]:
        raise ValueError("compaction source range must be ordered")
    raw_fact_ids = validate_raw_fact_ids(payload.get("raw_fact_ids"))
    shadowed_raw_fact_ids = validate_raw_fact_ids(
        payload.get("shadowed_raw_fact_ids"),
        "shadowed_raw_fact_ids",
    )
    if not set(shadowed_raw_fact_ids).issubset(raw_fact_ids):
        raise ValueError("shadowed_raw_fact_ids must cite retained raw facts")
    _sha256(payload.get("context_sha256"), "context_sha256")
    _decode_compaction_context(payload)


def _decode_event_body(value: Any, name: str) -> bytes:
    if type(value) is not str:
        raise ValueError(f"{name} must be a base64 string")
    try:
        body = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError(f"{name} must be canonical base64") from error
    if base64.b64encode(body).decode("ascii") != value:
        raise ValueError(f"{name} must be canonical base64")
    return body


def _module_input_from_event(value: Any) -> ModuleInput:
    if not isinstance(value, Mapping) or set(value) != {"schema_id", "body", "final"}:
        raise ValueError("module_input must contain exactly schema_id, body, and final")
    schema_id = _string(value.get("schema_id"), "module input schema_id")
    final = value.get("final")
    if type(final) is not bool:
        raise ValueError("module input final must be boolean")
    return ModuleInput(
        schema_id, _decode_event_body(value.get("body"), "module input body"), final
    )


def _output_from_event(value: Any) -> OutputEnvelope:
    if not isinstance(value, Mapping) or set(value) != {"schema_id", "body", "final"}:
        raise ValueError(
            "module_output must contain exactly schema_id, body, and final"
        )
    final = value.get("final")
    if type(final) is not bool:
        raise ValueError("module output final must be boolean")
    return OutputEnvelope(
        _string(value.get("schema_id"), "output schema_id"),
        _decode_event_body(value.get("body"), "module output body"),
    )


def _typed_input_hash(value: ModuleInput) -> str:
    encoded = json.dumps(
        value.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return _hash_bytes(encoded)


def _request_key_from_event(payload: Mapping[str, Any]) -> RequestKey:
    try:
        return RequestKey.from_dict(
            {
                field: payload[field]
                for field in (
                    "worker_session_id",
                    "request_id",
                    "generation_id",
                    "instance_id",
                    "work_id",
                    "attempt_id",
                    "authority_epoch",
                )
            }
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise ValueError("module_output key attribution is invalid") from error


def _validate_attachments(payload: Mapping[str, Any]) -> None:
    attachments = payload.get("attachments")
    if not isinstance(attachments, (list, tuple)):
        raise ValueError("attachments must be an array")
    for ref in attachments:
        if not isinstance(ref, Mapping) or set(ref) != {
            "digest",
            "size_bytes",
            "media_type",
        }:
            raise ValueError("attachments must contain artifact references")
        _sha256(ref.get("digest"), "digest")
        _string(ref.get("media_type"), "media_type")
        if type(ref.get("size_bytes")) is not int or ref["size_bytes"] < 0:
            raise ValueError("size_bytes must be a nonnegative integer")


def _validate_module_input_sequence(value: Any, *, expected: int | None = None) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("module_input_sequence must be a nonnegative integer")
    if expected is not None and value != expected:
        raise ValueError("module_input_sequence must be contiguous")


def _validate_output_sequence(value: Any) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("output_sequence must be a nonnegative integer")


def _validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    if "lineage" in payload:
        if kind != "session.started" and kind not in _TERMINAL:
            raise ValueError("lineage belongs only to Session start and settlement")
        SessionLineage.from_dict(payload["lineage"])
    core = set(payload) - {"lineage"}
    if kind == "session.started":
        _sha256(payload.get("effective_lock_hash"), "effective_lock_hash")
        if "module_input" in payload:
            expected = {
                "effective_lock_hash",
                "task_hash",
                "module_input",
                "module_input_sequence",
            }
            if core != expected:
                raise ValueError("typed session.started payload has invalid fields")
            module_input = _module_input_from_event(payload["module_input"])
            _validate_module_input_sequence(
                payload["module_input_sequence"], expected=0
            )
            if payload["task_hash"] != _typed_input_hash(module_input):
                raise ValueError(
                    "typed session.started task_hash does not match module_input"
                )
        else:
            if core != {"effective_lock_hash", "task_hash"}:
                raise ValueError("session.started payload has invalid fields")
            _sha256(payload.get("task_hash"), "task_hash")
    elif kind == "assistant_message":
        metadata = payload.get("metadata")
        if (
            not isinstance(metadata, Mapping)
            or set(metadata) != {"has_content"}
            or type(metadata.get("has_content")) is not bool
        ):
            raise ValueError(
                "assistant_message payload must contain boolean metadata.has_content"
            )
        identity = set(payload) - {"metadata"}
        if identity not in (set(), {"message_id", "trajectory_id"}):
            raise ValueError(
                "assistant_message identity must contain message_id and trajectory_id"
            )
        if identity:
            _string(payload.get("message_id"), "message_id")
            _string(payload.get("trajectory_id"), "trajectory_id")
    elif kind == "tool_call":
        if set(payload) != {"tool"}:
            raise ValueError("tool_call payload must contain only tool")
        _string(payload.get("tool"), "tool")
    elif kind == "tool_result":
        if set(payload) != {"tool", "error"} or type(payload.get("error")) is not bool:
            raise ValueError(
                "tool_result payload must contain tool and one boolean error field"
            )
        _string(payload.get("tool"), "tool")
    elif kind == "annotation":
        _validate_annotation_payload(payload)
    elif kind == "context.compacted":
        _validate_compaction_payload(payload)
    elif kind == "input.accepted":
        _validate_attachments(payload)
        if "module_input" in payload:
            if core != {"module_input", "module_input_sequence", "attachments"}:
                raise ValueError("typed input.accepted payload has invalid fields")
            _module_input_from_event(payload["module_input"])
            _validate_module_input_sequence(payload["module_input_sequence"])
        else:
            if core != {"content_hash", "attachments"}:
                raise ValueError("input.accepted payload has invalid fields")
            _sha256(payload.get("content_hash"), "content_hash")
    elif kind == "module_output":
        required = {
            "module_output",
            "output_sequence",
            "module_id",
            "worker_session_id",
            "request_id",
            "generation_id",
            "instance_id",
            "work_id",
            "attempt_id",
            "authority_epoch",
        }
        if core != required:
            raise ValueError("module_output payload has invalid fields")
        module_output = payload["module_output"]
        if not isinstance(module_output, Mapping) or set(module_output) != {
            "schema_id",
            "body",
            "final",
        }:
            raise ValueError(
                "module_output must contain exactly schema_id, body, and final"
            )
        _output_from_event(module_output)
        _validate_output_sequence(payload["output_sequence"])
        _string(payload.get("module_id"), "module_id")
        _request_key_from_event(payload)
    elif kind == "approval.requested":
        _string(payload.get("request_id"), "request_id")
        _string(payload.get("operation"), "operation")
    elif kind == "approval.resolved":
        _string(payload.get("request_id"), "request_id")
        decision = _string(payload.get("decision"), "decision")
        if decision not in _DECISIONS:
            raise ValueError("invalid approval decision")
    elif kind == "session.reconfigured":
        _sha256(payload.get("effective_lock_hash"), "effective_lock_hash")
        _string(payload.get("reason"), "reason", False)
    elif kind == _ADOPTION_EVENT_KIND:
        required = {
            "adoption_id",
            "checkpoint_id",
            "source_generation_id",
            "source_module_id",
            "source_instance_id",
            "source_work_id",
            "source_attempt_id",
            "source_schema_id",
            "source_body_sha256",
            "source_frontier",
            "target_generation_id",
        }
        optional = {
            "effective_lock_hash",
            "effective_lock_source",
            "target_lock",
            "request_id",
            "reason",
            "source_admission_id",
            "admission",
            "migration",
        }
        if not required.issubset(core) or set(core) - required - optional:
            raise ValueError("session.adoption_committed payload has invalid fields")
        for name in (
            "adoption_id",
            "checkpoint_id",
            "source_generation_id",
            "source_module_id",
            "source_instance_id",
            "source_work_id",
            "source_attempt_id",
            "source_schema_id",
            "target_generation_id",
        ):
            _string(payload.get(name), name)
        _sha256(payload.get("source_body_sha256"), "source_body_sha256")
        if "effective_lock_hash" in payload:
            _sha256(payload.get("effective_lock_hash"), "effective_lock_hash")
            if payload["effective_lock_hash"] != payload["target_generation_id"]:
                raise ValueError(
                    "adoption target generation does not match effective lock"
                )
        _sha256(payload["target_generation_id"], "target_generation_id")
        SessionCheckpointFrontier.from_dict(payload["source_frontier"])
        if "effective_lock_source" in payload:
            _string(payload.get("effective_lock_source"), "effective_lock_source")
        if "source_admission_id" in payload:
            _string(payload.get("source_admission_id"), "source_admission_id")
        if "target_lock" in payload:
            if not isinstance(payload["target_lock"], Mapping):
                raise ValueError("adoption target_lock must be a JSON object")
            json.dumps(_plain(payload["target_lock"]), allow_nan=False)
        for name in ("request_id", "reason"):
            if name in payload:
                _string(payload.get(name), name, name == "request_id")
        if "admission" in payload:
            if not isinstance(payload["admission"], Mapping):
                raise ValueError("adoption admission must be a JSON object")
            json.dumps(_plain(payload["admission"]), allow_nan=False)
        if "migration" in payload:
            migration = payload["migration"]
            if not isinstance(migration, (list, tuple)) or not all(
                isinstance(item, Mapping) for item in migration
            ):
                raise ValueError("adoption migration must be an array of JSON objects")
            json.dumps(_plain(migration), allow_nan=False)
    elif kind == "session.paused":
        _string(payload.get("reason"), "reason", False)
    elif kind == "session.resumed" and payload:
        raise ValueError("session.resumed payload must be empty")
    elif kind in _TERMINAL:
        outcome, fields = _TERMINAL[kind]
        if _string(payload.get("outcome"), "outcome") != outcome:
            raise ValueError(f"{kind} outcome does not match its kind")
        for field in fields:
            _string(payload.get(field), field, kind == "session.failed")


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("mapping keys must be strings")
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    json.dumps(value, allow_nan=False)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SessionCheckpointFrontier:
    """The exact owner-visible source position for a Session checkpoint."""

    event_sequence: int
    generation_id: str
    typed_input_sequence: int
    output_sequence: int
    compaction_index: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_sequence, "event_sequence"),
            (self.typed_input_sequence, "typed_input_sequence"),
            (self.output_sequence, "output_sequence"),
            (self.compaction_index, "compaction_index"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        _sha256(self.generation_id, "generation_id")

    @classmethod
    def from_dict(cls, value: object) -> "SessionCheckpointFrontier":
        required = {
            "event_sequence",
            "generation_id",
            "typed_input_sequence",
            "output_sequence",
            "compaction_index",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("checkpoint frontier must contain exactly its five fields")
        return cls(
            event_sequence=value["event_sequence"],
            generation_id=value["generation_id"],
            typed_input_sequence=value["typed_input_sequence"],
            output_sequence=value["output_sequence"],
            compaction_index=value["compaction_index"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_sequence": self.event_sequence,
            "generation_id": self.generation_id,
            "typed_input_sequence": self.typed_input_sequence,
            "output_sequence": self.output_sequence,
            "compaction_index": self.compaction_index,
        }


@dataclass(frozen=True, slots=True)
class SessionGenerationCheckpoint:
    """Immutable, JSON-roundtrippable bytes owned by one Session generation."""

    checkpoint_id: str
    session_id: str
    source_generation_id: str
    source_module_id: str
    source_instance_id: str
    source_work_id: str
    source_attempt_id: str
    schema_id: str
    body_sha256: str
    body: bytes
    frontier: SessionCheckpointFrontier

    def __post_init__(self) -> None:
        for value, name in (
            (self.checkpoint_id, "checkpoint_id"),
            (self.session_id, "session_id"),
            (self.source_generation_id, "source_generation_id"),
            (self.source_module_id, "source_module_id"),
            (self.source_instance_id, "source_instance_id"),
            (self.source_work_id, "source_work_id"),
            (self.source_attempt_id, "source_attempt_id"),
            (self.schema_id, "schema_id"),
        ):
            _string(value, name)
        _sha256(self.body_sha256, "body_sha256")
        if type(self.body) is not bytes:
            raise TypeError("checkpoint body must be bytes")
        if len(self.body) > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint body exceeds maximum size")
        if _hash_bytes(self.body) != self.body_sha256:
            raise ValueError("checkpoint body digest does not match body")
        if not isinstance(self.frontier, SessionCheckpointFrontier):
            raise TypeError("checkpoint frontier must be a SessionCheckpointFrontier")
        if self.source_generation_id != self.frontier.generation_id:
            raise ValueError("checkpoint owner generation differs from its frontier")

    @property
    def body_digest(self) -> str:
        return self.body_sha256

    @property
    def source_frontier(self) -> SessionCheckpointFrontier:
        return self.frontier

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "source_generation_id": self.source_generation_id,
            "source_module_id": self.source_module_id,
            "source_instance_id": self.source_instance_id,
            "source_work_id": self.source_work_id,
            "source_attempt_id": self.source_attempt_id,
            "schema_id": self.schema_id,
            "body_sha256": self.body_sha256,
            "body": base64.b64encode(self.body).decode("ascii"),
            "frontier": self.frontier.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "SessionGenerationCheckpoint":
        required = {
            "checkpoint_id",
            "session_id",
            "source_generation_id",
            "source_module_id",
            "source_instance_id",
            "source_work_id",
            "source_attempt_id",
            "schema_id",
            "body_sha256",
            "body",
            "frontier",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("session generation checkpoint has invalid fields")
        return cls(
            checkpoint_id=value["checkpoint_id"],
            session_id=value["session_id"],
            source_generation_id=value["source_generation_id"],
            source_module_id=value["source_module_id"],
            source_instance_id=value["source_instance_id"],
            source_work_id=value["source_work_id"],
            source_attempt_id=value["source_attempt_id"],
            schema_id=value["schema_id"],
            body_sha256=value["body_sha256"],
            body=_decode_event_body(value["body"], "checkpoint body"),
            frontier=SessionCheckpointFrontier.from_dict(value["frontier"]),
        )

    @classmethod
    def from_proposal(
        cls,
        checkpoint_id: str,
        session_id: str,
        proposal: CheckpointProposal,
        frontier: SessionCheckpointFrontier,
    ) -> "SessionGenerationCheckpoint":
        if not isinstance(proposal, CheckpointProposal):
            raise TypeError("checkpoint proposal must be a CheckpointProposal")
        envelope = proposal.payload
        return cls(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            source_generation_id=envelope.source_generation_id,
            source_module_id=envelope.source_module_id,
            source_instance_id=envelope.source_instance_id,
            source_work_id=envelope.source_work_id,
            source_attempt_id=envelope.source_attempt_id,
            schema_id=envelope.schema_id,
            body_sha256=_hash_bytes(envelope.body),
            body=envelope.body,
            frontier=frontier,
        )


@dataclass(frozen=True, slots=True)
class CompactionSnapshot:
    """Persistence-owner bytes and cumulative raw-fact identities at one boundary."""

    effective_context: bytes
    raw_fact_ids: tuple[str, ...]
    expected_context_sha256: str | None = None
    expected_context_sequence: int | None = None
    expected_source_sequence_end: int | None = None
    expected_raw_fact_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.effective_context) is not bytes:
            raise TypeError("effective_context must be bytes")
        _validate_effective_context(self.effective_context)
        facts = validate_raw_fact_ids(self.raw_fact_ids)
        object.__setattr__(self, "raw_fact_ids", facts)
        for value, name in ((self.expected_context_sha256, "expected_context_sha256"),):
            if value is not None:
                _sha256(value, name)
        for value, name in (
            (self.expected_context_sequence, "expected_context_sequence"),
            (self.expected_source_sequence_end, "expected_source_sequence_end"),
        ):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer")
        if self.expected_raw_fact_ids is not None:
            object.__setattr__(
                self,
                "expected_raw_fact_ids",
                validate_raw_fact_ids(
                    self.expected_raw_fact_ids, "expected_raw_fact_ids"
                ),
            )


@dataclass(frozen=True, slots=True)
class SessionLineage:
    """Immutable parent and Work Item identities carried by a child Session."""

    parent_session_id: str
    root_session_id: str
    parent_work_item_id: str
    child_work_item_id: str

    def __post_init__(self) -> None:
        for name in (
            "parent_session_id",
            "root_session_id",
            "parent_work_item_id",
            "child_work_item_id",
        ):
            _string(getattr(self, name), name)
        if self.parent_work_item_id == self.child_work_item_id:
            raise ValueError("child and parent Work Item identities must differ")

    @classmethod
    def from_dict(cls, value: object) -> "SessionLineage":
        if not isinstance(value, Mapping) or set(value) != {
            "parent_session_id",
            "root_session_id",
            "parent_work_item_id",
            "child_work_item_id",
        }:
            raise ValueError("lineage must contain exactly its four stable identities")
        return cls(
            value["parent_session_id"],
            value["root_session_id"],
            value["parent_work_item_id"],
            value["child_work_item_id"],
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "parent_session_id": self.parent_session_id,
            "root_session_id": self.root_session_id,
            "parent_work_item_id": self.parent_work_item_id,
            "child_work_item_id": self.child_work_item_id,
        }


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """Stable, immutable label metadata for one canonical message target."""

    annotation_id: str
    message_id: str
    trajectory_id: str
    label: str
    author: str
    generation: str

    def __post_init__(self) -> None:
        for name in (
            "annotation_id",
            "message_id",
            "trajectory_id",
            "label",
            "author",
            "generation",
        ):
            _string(getattr(self, name), name)

    def as_dict(self) -> dict[str, str]:
        return {
            "annotation_id": self.annotation_id,
            "message_id": self.message_id,
            "trajectory_id": self.trajectory_id,
            "label": self.label,
            "author": self.author,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class KernelEvent:
    session_id: str
    sequence: int
    kind: str
    occurred_at: str
    payload: Mapping[str, Any]
    schema_version: str = "bb.session_event.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "bb.session_event.v1":
            raise ValueError("unsupported session event schema_version")
        if any(
            type(value) is not str or not value
            for value in (self.session_id, self.kind, self.occurred_at)
        ):
            raise ValueError("session event identity fields must be non-empty strings")
        if self.kind not in _EVENT_KINDS:
            raise ValueError("unsupported session event kind")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("session event sequence must be a positive integer")
        if not isinstance(self.payload, Mapping):
            raise TypeError("session event payload must be a mapping")
        payload = _frozen(self.payload)
        _validate_payload(self.kind, payload)
        object.__setattr__(self, "payload", payload)

    @classmethod
    def create(
        cls,
        session_id: str,
        sequence: int,
        kind: str,
        occurred_at: str,
        payload: Mapping[str, Any],
    ) -> "KernelEvent":
        return cls(session_id, sequence, kind, occurred_at, payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "occurred_at": self.occurred_at,
            "payload": _plain(self.payload),
        }


@dataclass(frozen=True, slots=True)
class CompactionEvent:
    """Decoded durable compaction boundary returned by Session.compact."""

    session_id: str
    sequence: int
    compaction_index: int
    source_sequence_start: int
    source_sequence_end: int
    effective_context: bytes
    raw_fact_ids: tuple[str, ...]
    shadowed_raw_fact_ids: tuple[str, ...]


def _compaction_event(event: KernelEvent) -> CompactionEvent:
    if event.kind != "context.compacted":
        raise ValueError("compaction event requires context.compacted")
    return CompactionEvent(
        session_id=event.session_id,
        sequence=event.sequence,
        compaction_index=event.payload["compaction_index"],
        source_sequence_start=event.payload["source_sequence_start"],
        source_sequence_end=event.payload["source_sequence_end"],
        effective_context=_decode_compaction_context(event.payload),
        raw_fact_ids=tuple(event.payload["raw_fact_ids"]),
        shadowed_raw_fact_ids=tuple(event.payload["shadowed_raw_fact_ids"]),
    )


def _trajectory_segment_id(session_id: str, index: int, generation_id: str) -> str:
    return f"{session_id}:segment:{index}:{generation_id.removeprefix('sha256:')}"


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str
    status: str
    effective_lock_hash: str
    task_hash: str
    event_count: int
    trajectory_segment_id: str
    pending_approval: str | None = None
    terminal_outcome: Mapping[str, Any] | None = None
    lineage: SessionLineage | None = None

    def __post_init__(self) -> None:
        _string(self.session_id, "session_id")
        _sha256(self.effective_lock_hash, "effective_lock_hash")
        _sha256(self.task_hash, "task_hash")
        _string(self.trajectory_segment_id, "trajectory_segment_id")
        if self.lineage is not None and not isinstance(self.lineage, SessionLineage):
            raise TypeError("lineage must be a SessionLineage")
        if type(self.status) is not str or self.status not in _STATUSES:
            raise ValueError("invalid session status")
        if type(self.event_count) is not int or self.event_count < 1:
            raise ValueError("event_count must be a positive integer")
        if self.status != "running" and self.event_count == 1:
            raise ValueError("non-running sessions require at least two events")
        if self.status == "awaiting_approval":
            _string(self.pending_approval, "pending_approval")
        elif self.pending_approval is not None:
            raise ValueError("pending_approval requires awaiting_approval status")
        if self.status in {"completed", "failed", "canceled"}:
            if not isinstance(self.terminal_outcome, Mapping):
                raise ValueError("terminal_outcome must match terminal status")
            terminal = _frozen(self.terminal_outcome)
            _validate_payload(f"session.{self.status}", terminal)
            object.__setattr__(self, "terminal_outcome", terminal)
        elif self.terminal_outcome is not None:
            raise ValueError("terminal_outcome requires terminal status")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.session.v1",
            "session_id": self.session_id,
            "status": self.status,
            "effective_lock_hash": self.effective_lock_hash,
            "generation_id": self.effective_lock_hash,
            "trajectory_segment_id": self.trajectory_segment_id,
            "lineage": None if self.lineage is None else self.lineage.as_dict(),
            "task_hash": self.task_hash,
            "event_count": self.event_count,
            "pending_approval": self.pending_approval,
            "terminal_outcome": _plain(self.terminal_outcome),
        }


def rebuild(events: Iterable[KernelEvent]) -> SessionView:
    rows = tuple(events)
    if not rows or rows[0].kind != "session.started":
        raise ValueError("event stream must begin with session.started")
    start, status, pending, outcome = rows[0], "running", None, None
    lock_hash = start.payload["effective_lock_hash"]
    generation_index = 0
    lineage_payload = start.payload.get("lineage")
    lineage = (
        None if lineage_payload is None else SessionLineage.from_dict(lineage_payload)
    )
    if lineage is not None and start.session_id in (
        lineage.parent_session_id,
        lineage.root_session_id,
    ):
        raise ValueError("child Session cannot be its own parent or root")
    typed_input_next = 0
    typed_input_closed = False
    if "module_input" in start.payload:
        initial_input = _module_input_from_event(start.payload["module_input"])
        _validate_module_input_sequence(
            start.payload["module_input_sequence"], expected=0
        )
        typed_input_next = 1
        typed_input_closed = initial_input.final
    output_frontiers: dict[tuple[str, ...], tuple[int, bool]] = {}
    message_targets: dict[str, str] = {}
    annotation_ids: set[str] = set()
    adoption_ids: set[str] = set()
    compaction_count = 0
    last_compaction_sequence: int | None = None
    retained_raw_fact_order: tuple[str, ...] = ()
    last_compaction_context_hash: str | None = None
    for expected, event in enumerate(rows, 1):
        if event.session_id != start.session_id or event.sequence != expected:
            raise ValueError("event stream is not contiguous for one session")
        if expected == 1:
            continue
        if status not in _ALLOWED.get(event.kind, ()):
            raise ValueError(f"invalid {event.kind} transition from {status}")
        if event.kind == _ADOPTION_EVENT_KIND:
            adoption_id = event.payload["adoption_id"]
            if adoption_id in adoption_ids:
                raise ValueError("duplicate adoption_id in event stream")
            frontier = SessionCheckpointFrontier.from_dict(
                event.payload["source_frontier"]
            )
            if (
                frontier.event_sequence != expected - 1
                or frontier.generation_id != lock_hash
                or event.payload["source_generation_id"] != lock_hash
            ):
                raise ValueError(
                    "adoption source frontier does not match durable order"
                )
            adoption_ids.add(adoption_id)
        if event.kind in _TERMINAL and event.payload.get("lineage") != lineage_payload:
            raise ValueError("Session settlement lineage differs from its start")
        if event.kind == "input.accepted" and "module_input" in event.payload:
            if typed_input_closed:
                raise ValueError("typed input follows a final module input")
            _validate_module_input_sequence(
                event.payload["module_input_sequence"],
                expected=typed_input_next,
            )
            typed_input_next += 1
            typed_input_closed = _module_input_from_event(
                event.payload["module_input"]
            ).final
        elif event.kind == "module_output":
            identity = (
                event.payload["module_id"],
                event.payload["worker_session_id"],
                event.payload["request_id"],
                event.payload["generation_id"],
                event.payload["instance_id"],
                event.payload["work_id"],
                event.payload["attempt_id"],
                str(event.payload["authority_epoch"]),
            )
            prior = output_frontiers.get(identity)
            if prior is not None and prior[1]:
                raise ValueError("module output follows a final output")
            expected_output_sequence = 0 if prior is None else prior[0]
            _validate_output_sequence(event.payload["output_sequence"])
            if event.payload["output_sequence"] != expected_output_sequence:
                raise ValueError("module output sequences must be contiguous per owner")
            output_frontiers[identity] = (
                expected_output_sequence + 1,
                event.payload["module_output"]["final"],
            )
        if event.kind == "assistant_message" and "message_id" in event.payload:
            message_id = event.payload["message_id"]
            trajectory_id = event.payload["trajectory_id"]
            if message_id in message_targets:
                raise ValueError("duplicate canonical message identity")
            message_targets[message_id] = trajectory_id
        elif event.kind == "annotation":
            annotation_id = event.payload["annotation_id"]
            if annotation_id in annotation_ids:
                raise ValueError("duplicate annotation_id in event stream")
            annotation_ids.add(annotation_id)
            if (
                message_targets.get(event.payload["message_id"])
                != event.payload["trajectory_id"]
            ):
                raise ValueError("annotation target is not registered for this session")
        elif event.kind == "context.compacted":
            expected_start = last_compaction_sequence or 1
            if event.payload["compaction_index"] != compaction_count + 1:
                raise ValueError("compaction indexes must be contiguous")
            if (
                event.payload["source_sequence_start"] != expected_start
                or event.payload["source_sequence_end"] != event.sequence - 1
            ):
                raise ValueError(
                    "compaction source range does not match durable event order"
                )
            current_raw_fact_order = tuple(event.payload["raw_fact_ids"])
            if (
                current_raw_fact_order[: len(retained_raw_fact_order)]
                != retained_raw_fact_order
            ):
                raise ValueError(
                    "compaction cannot reorder or discard retained raw facts"
                )
            expected_shadowed = (
                retained_raw_fact_order
                if last_compaction_context_hash is not None
                and event.payload["context_sha256"] != last_compaction_context_hash
                else ()
            )
            if tuple(event.payload["shadowed_raw_fact_ids"]) != expected_shadowed:
                raise ValueError(
                    "compaction shadow chain does not cite the replaced surface"
                )
            compaction_count += 1
            last_compaction_sequence = event.sequence
            retained_raw_fact_order = current_raw_fact_order
            last_compaction_context_hash = event.payload["context_sha256"]
        if event.kind == "approval.requested":
            pending, status = event.payload["request_id"], "awaiting_approval"
        elif event.kind == "approval.resolved":
            if pending != event.payload["request_id"]:
                raise ValueError("approval does not match the pending request")
            pending, status = None, "running"
        elif event.kind == "session.reconfigured":
            lock_hash = event.payload["effective_lock_hash"]
            generation_index += 1
        elif event.kind == _ADOPTION_EVENT_KIND:
            lock_hash = event.payload["target_generation_id"]
            generation_index += 1
        elif event.kind == "session.paused":
            status = "paused"
        elif event.kind == "session.resumed":
            status = "running"
        elif event.kind.startswith("session."):
            pending, status, outcome = (
                None,
                event.kind.removeprefix("session."),
                event.payload,
            )
    return SessionView(
        start.session_id,
        status,
        lock_hash,
        start.payload["task_hash"],
        len(rows),
        _trajectory_segment_id(start.session_id, generation_index, lock_hash),
        pending,
        outcome,
        lineage,
    )


SESSION_PROJECTOR_VERSION = "bb.session.projector.v2"


class SessionProjectionError(ValueError):
    """A Session projection request cannot be satisfied."""


class SessionProjectionAsOfError(SessionProjectionError):
    """A requested Session source sequence is outside the stream."""

    def __init__(self, as_of: int, available: int) -> None:
        super().__init__(
            f"Session as_of {as_of!r} is outside source range 1..{available}"
        )
        self.as_of, self.available = as_of, available


class SessionProjectionVersionError(SessionProjectionError):
    """A caller requested a projector version this owner does not provide."""

    def __init__(self, expected: str) -> None:
        super().__init__(f"unsupported Session projector version {expected!r}")
        self.expected = expected


def _session_projection_limit(rows: tuple[KernelEvent, ...], as_of: int | None) -> int:
    if not rows:
        raise ValueError("event stream must begin with session.started")
    limit = len(rows) if as_of is None else as_of
    if type(limit) is not int or limit < 1 or limit > len(rows):
        raise SessionProjectionAsOfError(limit, len(rows))
    return limit


def _check_session_projection_version(expected: str | None) -> None:
    if expected is not None and expected != SESSION_PROJECTOR_VERSION:
        raise SessionProjectionVersionError(expected)


def project_session_replay(
    events: Iterable[KernelEvent],
    *,
    as_of: int | None = None,
    expected_projector_version: str | None = None,
) -> Projected[SessionView]:
    _check_session_projection_version(expected_projector_version)
    rows = tuple(events)
    limit = _session_projection_limit(rows, as_of)
    value = rebuild(rows[:limit])
    return Projected(
        value,
        SESSION_PROJECTOR_VERSION,
        ProjectionSource(f"session:{value.session_id}", 1, limit),
        limit,
    )


def project_session(
    events: Iterable[KernelEvent],
    *,
    as_of: int | None = None,
    expected_projector_version: str | None = None,
) -> Projected[SessionView]:
    return project_session_replay(
        events, as_of=as_of, expected_projector_version=expected_projector_version
    )


def project_session_snapshot(
    view: SessionView,
    *,
    as_of: int | None = None,
    expected_projector_version: str | None = None,
) -> Projected[SessionView]:
    _check_session_projection_version(expected_projector_version)
    if not isinstance(view, SessionView):
        raise TypeError("Session snapshot projection requires a SessionView")
    if as_of is not None and (type(as_of) is not int or as_of != view.event_count):
        raise SessionProjectionAsOfError(as_of, view.event_count)
    return Projected(
        view,
        SESSION_PROJECTOR_VERSION,
        ProjectionSource(f"session:{view.session_id}", 1, view.event_count),
        view.event_count,
    )


class ContextCASConflict(RuntimeError):
    """A context operation observed a stale Session context frontier."""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


_SESSION_ACTIONS = MappingProxyType(
    {
        "accept input": ("running",),
        "emit module output": ("running",),
        "observe assistant": ("running",),
        "observe tool call": ("running",),
        "observe tool result": ("running",),
        "annotate": (
            "running",
            "awaiting_approval",
            "paused",
            "completed",
            "failed",
            "canceled",
        ),
        "compact": ("running",),
        "request approval": ("running",),
        "resolve approval": ("awaiting_approval",),
        "reconfigure": ("running", "awaiting_approval", "paused"),
        "pause": ("running",),
        "resume": ("paused",),
        "cancel": ("running", "awaiting_approval", "paused"),
        "complete": ("running",),
        "fail": ("running", "awaiting_approval", "paused"),
    }
)


def _generation_id(lock: EffectiveHarnessLock) -> str:
    return lock.generation_id


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class ReplayError(ValueError):
    """A durable event stream cannot be rebuilt into a valid Session."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


_SESSION_ACTIONS = MappingProxyType(
    {
        "accept input": ("running",),
        "emit module output": ("running",),
        "observe assistant": ("running",),
        "observe tool call": ("running",),
        "observe tool result": ("running",),
        "annotate": (
            "running",
            "awaiting_approval",
            "paused",
            "completed",
            "failed",
            "canceled",
        ),
        "compact": ("running",),
        "request approval": ("running",),
        "resolve approval": ("awaiting_approval",),
        "reconfigure": ("running", "awaiting_approval", "paused"),
        "adopt checkpoint": ("running", "awaiting_approval", "paused"),
        "pause": ("running",),
        "resume": ("paused",),
        "cancel": ("running", "awaiting_approval", "paused"),
        "complete": ("running",),
        "fail": ("running", "awaiting_approval", "paused"),
    }
)


def _check(condition: bool, error: type[Exception], message: str) -> None:
    if not condition:
        raise error(message)


class GenerationAdoptionError(RuntimeError):
    """Typed refusal for an invalid or non-quiescent generation adoption."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _assistant_payload(
    content: str,
    message_id: str | None,
    trajectory_id: str | None,
) -> dict[str, Any]:
    _check(type(content) is str, TypeError, "assistant content must be a string")
    if (message_id is None) != (trajectory_id is None):
        raise ValueError(
            "assistant message identity requires message_id and trajectory_id"
        )
    payload: dict[str, Any] = {"metadata": {"has_content": bool(content)}}
    if message_id is not None and trajectory_id is not None:
        _string(message_id, "message_id")
        _string(trajectory_id, "trajectory_id")
        payload.update(message_id=message_id, trajectory_id=trajectory_id)
    return payload


class Session:
    """Lifecycle owner; adapters may add only validated, minimal runtime observations."""

    def __init__(
        self,
        events: Iterable[KernelEvent],
        *,
        clock: Clock | None = None,
        sink: EventSink | None = None,
        task: str | None = None,
        checkpoints: Iterable[SessionGenerationCheckpoint] = (),
    ) -> None:
        if task is not None and (not isinstance(task, str) or not task.strip()):
            raise ValueError("task must be non-empty when retained")
        self._task = task
        self._transition_lock = RLock()
        self._appending = False
        self._events = list(events)
        self._clock = clock if clock is not None else SystemClock()
        self._sink = sink if sink is not None else NullEventSink()
        retained_checkpoints = tuple(checkpoints)
        if any(
            not isinstance(item, SessionGenerationCheckpoint)
            for item in retained_checkpoints
        ):
            raise TypeError(
                "retained checkpoints must be SessionGenerationCheckpoint records"
            )
        session_ids = {item.session_id for item in retained_checkpoints}
        if len(session_ids) > 1:
            raise ValueError("retained checkpoints belong to multiple Sessions")
        self._checkpoints: dict[str, SessionGenerationCheckpoint] = {
            item.checkpoint_id: item for item in retained_checkpoints
        }
        if len(self._checkpoints) != len(retained_checkpoints):
            raise ValueError("retained checkpoint ids must be unique")
        self._committed_adoption_ids = {
            event.payload["adoption_id"]
            for event in self._events
            if event.kind == _ADOPTION_EVENT_KIND
        }
        self._terminal_annotation_commit: (
            Callable[[AnnotationRecord], tuple[KernelEvent, ...]] | None
        ) = None
        self._view = rebuild(self._events)
        if session_ids and next(iter(session_ids)) != self._view.session_id:
            raise ValueError("retained checkpoint belongs to another Session")
        if any(
            checkpoint.session_id != self._view.session_id
            or checkpoint.source_generation_id not in self.generation_sequence
            for checkpoint in self._checkpoints.values()
        ):
            raise ValueError(
                "retained checkpoint is not owned by this Session generation"
            )
        compaction = next(
            (row for row in reversed(self._events) if row.kind == "context.compacted"),
            None,
        )
        self._effective_context = (
            None
            if compaction is None
            else _decode_compaction_context(compaction.payload)
        )
        self._raw_fact_ids = (
            () if compaction is None else tuple(compaction.payload["raw_fact_ids"])
        )

    @classmethod
    def start(
        cls,
        lock: EffectiveHarnessLock,
        task: str | None = None,
        *,
        module_input: ModuleInput | None = None,
        module_input_sequence: int | None = None,
        session_id: str | None = None,
        clock: Clock | None = None,
        ids: IdSource | None = None,
        sink: EventSink | None = None,
        lineage: SessionLineage | None = None,
    ) -> "Session":
        if not isinstance(lock, EffectiveHarnessLock):
            raise TypeError("Session.start requires an EffectiveHarnessLock")
        if (task is None) == (module_input is None):
            raise ValueError("supply exactly one task or module_input")
        if module_input is None:
            if not isinstance(task, str) or not task.strip():
                raise ValueError("task must be non-empty")
        else:
            if not isinstance(module_input, ModuleInput):
                raise TypeError("module_input must be a ModuleInput")
            _validate_module_input_sequence(module_input_sequence, expected=0)
        active_clock, active_ids = (
            clock if clock is not None else SystemClock(),
            ids if ids is not None else UUIDSource(),
        )
        generation_id = _generation_id(lock)
        active_session_id = (
            session_id if session_id is not None else active_ids.new_id()
        )
        payload: dict[str, Any] = {"effective_lock_hash": generation_id}
        if module_input is None:
            payload["task_hash"] = _hash(task)
        else:
            payload.update(
                {
                    "task_hash": _typed_input_hash(module_input),
                    "module_input": module_input.to_dict(),
                    "module_input_sequence": module_input_sequence,
                }
            )
        if lineage is not None:
            if not isinstance(lineage, SessionLineage):
                raise TypeError("lineage must be a SessionLineage")
            if active_session_id in (
                lineage.parent_session_id,
                lineage.root_session_id,
            ):
                raise ValueError("child Session cannot be its own parent or root")
            payload["lineage"] = lineage.as_dict()
        event = KernelEvent.create(
            active_session_id,
            1,
            "session.started",
            active_clock.now(),
            payload,
        )
        active_sink = sink if sink is not None else NullEventSink()
        active_sink.append(event)
        return cls((event,), clock=active_clock, sink=active_sink, task=task)

    @classmethod
    def restore(
        cls,
        events: Iterable[KernelEvent],
        *,
        clock: Clock | None = None,
        sink: EventSink | None = None,
        task: str | None = None,
        checkpoints: Iterable[SessionGenerationCheckpoint] = (),
    ) -> "Session":
        try:
            return cls(
                events,
                clock=clock,
                sink=sink,
                task=task,
                checkpoints=checkpoints,
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ReplayError("invalid_event_stream", str(error)) from error

    @property
    def events(self) -> tuple[KernelEvent, ...]:
        with self._transition_lock:
            return tuple(self._events)

    @property
    def task(self) -> str | None:
        return self._task

    @property
    def read_model(self) -> SessionView:
        with self._transition_lock:
            return self._view

    def checkpoint_frontier(self) -> SessionCheckpointFrontier:
        """Return the exact source frontier currently owned by this Session."""
        with self._transition_lock:
            typed_input_sequence, _ = self._typed_input_frontier()
            output_sequence = max(
                (
                    int(event.payload["output_sequence"]) + 1
                    for event in self._events
                    if event.kind == "module_output"
                ),
                default=0,
            )
            compaction_index = max(
                (
                    int(event.payload["compaction_index"])
                    for event in self._events
                    if event.kind == "context.compacted"
                ),
                default=0,
            )
            return SessionCheckpointFrontier(
                event_sequence=len(self._events),
                generation_id=self._view.effective_lock_hash,
                typed_input_sequence=typed_input_sequence,
                output_sequence=output_sequence,
                compaction_index=compaction_index,
            )

    def validate_checkpoint_frontier(
        self, frontier: SessionCheckpointFrontier
    ) -> SessionCheckpointFrontier:
        """Validate that a stamped source frontier is still the current one."""
        if not isinstance(frontier, SessionCheckpointFrontier):
            raise TypeError("checkpoint frontier must be a SessionCheckpointFrontier")
        with self._transition_lock:
            current = self.checkpoint_frontier()
            if frontier.generation_id != current.generation_id:
                raise GenerationAdoptionError(
                    "source_generation_mismatch",
                    "checkpoint source generation is no longer current",
                )
            if (
                frontier.event_sequence != current.event_sequence
                or frontier.typed_input_sequence != current.typed_input_sequence
                or frontier.output_sequence != current.output_sequence
                or frontier.compaction_index != current.compaction_index
            ):
                code = (
                    "source_advanced"
                    if any(
                        (
                            frontier.event_sequence != current.event_sequence,
                            frontier.typed_input_sequence
                            != current.typed_input_sequence,
                            frontier.output_sequence != current.output_sequence,
                            frontier.compaction_index != current.compaction_index,
                        )
                    )
                    else "source_frontier_mismatch"
                )
                raise GenerationAdoptionError(
                    code,
                    "checkpoint source frontier no longer matches the Session",
                )
            return frontier

    @property
    def checkpoints(self) -> tuple[SessionGenerationCheckpoint, ...]:
        with self._transition_lock:
            return tuple(self._checkpoints.values())

    def runtime_state(self) -> dict[str, Any]:
        """Return bounded, explicit state needed to recover retained checkpoints."""
        with self._transition_lock:
            return {
                "checkpoints": [item.as_dict() for item in self._checkpoints.values()]
            }

    def stamp_checkpoint(
        self,
        proposal: CheckpointProposal | CheckpointEnvelope,
        *,
        checkpoint_id: str | None = None,
        source_frontier: SessionCheckpointFrontier | None = None,
    ) -> SessionGenerationCheckpoint:
        """Owner-stamp a validated module checkpoint at the current frontier."""
        if isinstance(proposal, CheckpointEnvelope):
            proposal = CheckpointProposal(
                proposal,
                self.checkpoint_frontier().typed_input_sequence,
            )
        if not isinstance(proposal, CheckpointProposal):
            raise TypeError("stamp_checkpoint requires a CheckpointProposal")
        envelope = proposal.payload
        if not isinstance(envelope, CheckpointEnvelope):
            raise TypeError("checkpoint proposal payload must be a CheckpointEnvelope")
        with self._transition_lock:
            if self._view.status == "awaiting_approval":
                raise GenerationAdoptionError(
                    "pending_approval",
                    "checkpoint cannot be stamped while approval is pending",
                )
            if self._view.status != "running":
                raise GenerationAdoptionError(
                    "boundary_unavailable",
                    "checkpoint requires a running Session boundary",
                )
            frontier = (
                self.checkpoint_frontier()
                if source_frontier is None
                else source_frontier
            )
            self.validate_checkpoint_frontier(frontier)
            if proposal.declared_at_sequence > frontier.typed_input_sequence:
                raise GenerationAdoptionError(
                    "source_advanced",
                    "checkpoint proposal is ahead of the Session input frontier",
                )
            if envelope.source_generation_id != frontier.generation_id:
                raise GenerationAdoptionError(
                    "source_generation_mismatch",
                    "checkpoint proposal generation is not pinned by this Session",
                )
            digest = _hash_bytes(envelope.body)
            selected_id = checkpoint_id or (
                f"{self._view.session_id}:checkpoint:"
                f"{frontier.event_sequence}:{digest.removeprefix('sha256:')}"
            )
            record = SessionGenerationCheckpoint(
                checkpoint_id=selected_id,
                session_id=self._view.session_id,
                source_generation_id=envelope.source_generation_id,
                source_module_id=envelope.source_module_id,
                source_instance_id=envelope.source_instance_id,
                source_work_id=envelope.source_work_id,
                source_attempt_id=envelope.source_attempt_id,
                schema_id=envelope.schema_id,
                body_sha256=digest,
                body=envelope.body,
                frontier=frontier,
            )
            prior = self._checkpoints.get(record.checkpoint_id)
            if prior is not None and prior != record:
                raise GenerationAdoptionError(
                    "checkpoint_conflict",
                    "checkpoint_id is already bound to different bytes or attribution",
                )
            self._checkpoints[record.checkpoint_id] = record
            return record

    def commit_checkpoint_adoption(
        self,
        lock: EffectiveHarnessLock,
        adoption_record: Mapping[str, Any],
    ) -> SessionView:
        """Append one authoritative generation-adoption commit event."""
        if not isinstance(lock, EffectiveHarnessLock):
            raise TypeError("checkpoint adoption requires an EffectiveHarnessLock")
        if not isinstance(adoption_record, Mapping):
            raise TypeError("checkpoint adoption record must be a mapping")
        adoption_id = adoption_record.get("adoption_id")
        checkpoint_id = adoption_record.get("checkpoint_id")
        _string(adoption_id, "adoption_id")
        _string(checkpoint_id, "checkpoint_id")
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            candidate = adoption_record.get("checkpoint")
            if candidate is not None:
                checkpoint = SessionGenerationCheckpoint.from_dict(candidate)
                if checkpoint.checkpoint_id != checkpoint_id:
                    raise GenerationAdoptionError(
                        "checkpoint_conflict",
                        "adoption checkpoint_id does not match retained checkpoint",
                    )
            else:
                raise GenerationAdoptionError(
                    "checkpoint_missing",
                    "adoption references no retained checkpoint",
                )
        target_generation_id = _generation_id(lock)
        supplied_target = adoption_record.get(
            "target_generation_id", adoption_record.get("new_generation_id")
        )
        if supplied_target is not None and supplied_target != target_generation_id:
            raise GenerationAdoptionError(
                "target_generation_mismatch",
                "adoption target does not match the supplied EffectiveHarnessLock",
            )
        with self._transition_lock:
            if adoption_id in self._committed_adoption_ids:
                return self._view
            if checkpoint.session_id != self._view.session_id:
                raise GenerationAdoptionError(
                    "checkpoint_conflict",
                    "adoption checkpoint belongs to another Session",
                )
            self.validate_checkpoint_frontier(checkpoint.frontier)
            if checkpoint.source_generation_id != self._view.effective_lock_hash:
                raise GenerationAdoptionError(
                    "source_generation_mismatch",
                    "adoption checkpoint is not from the active generation",
                )
            if target_generation_id == self._view.effective_lock_hash:
                raise GenerationAdoptionError(
                    "target_generation_mismatch",
                    "adoption target is already the active generation",
                )
            for name in (
                "source_generation_id",
                "source_module_id",
                "source_instance_id",
                "source_work_id",
                "source_attempt_id",
                "source_schema_id",
            ):
                supplied = adoption_record.get(name)
                if supplied is not None and supplied != getattr(checkpoint, name):
                    raise GenerationAdoptionError(
                        "checkpoint_conflict",
                        f"adoption {name} differs from retained checkpoint",
                    )
            supplied_frontier = adoption_record.get(
                "source_frontier", adoption_record.get("frontier")
            )
            if supplied_frontier is not None:
                if (
                    SessionCheckpointFrontier.from_dict(supplied_frontier)
                    != checkpoint.frontier
                ):
                    raise GenerationAdoptionError(
                        "checkpoint_conflict",
                        "adoption source frontier differs from retained checkpoint",
                    )
            supplied_digest = adoption_record.get(
                "source_body_sha256", adoption_record.get("body_sha256")
            )
            if (
                supplied_digest is not None
                and supplied_digest != checkpoint.body_sha256
            ):
                raise GenerationAdoptionError(
                    "checkpoint_conflict",
                    "adoption source digest differs from retained checkpoint",
                )
            reason = adoption_record.get("reason", "")
            request_id = adoption_record.get("request_id", "")
            if type(reason) is not str or type(request_id) is not str:
                raise TypeError("adoption reason and request_id must be strings")
            payload: dict[str, Any] = {
                "adoption_id": adoption_id,
                "checkpoint_id": checkpoint.checkpoint_id,
                "source_generation_id": checkpoint.source_generation_id,
                "source_module_id": checkpoint.source_module_id,
                "source_instance_id": checkpoint.source_instance_id,
                "source_work_id": checkpoint.source_work_id,
                "source_attempt_id": checkpoint.source_attempt_id,
                "source_schema_id": checkpoint.schema_id,
                "source_body_sha256": checkpoint.body_sha256,
                "source_frontier": checkpoint.frontier.as_dict(),
                "target_generation_id": target_generation_id,
                "effective_lock_hash": target_generation_id,
                "target_lock": lock.as_dict(),
                "reason": reason,
                "admission": _plain(adoption_record.get("admission", {})),
                "migration": _plain(adoption_record.get("migration", [])),
            }
            if request_id:
                payload["request_id"] = request_id
            effective_lock_source = adoption_record.get("effective_lock_source")
            if effective_lock_source is not None:
                if type(effective_lock_source) is not str:
                    raise TypeError("effective_lock_source must be a string")
                payload["effective_lock_source"] = effective_lock_source
            source_admission_id = adoption_record.get("source_admission_id")
            if source_admission_id is not None:
                if type(source_admission_id) is not str or not source_admission_id:
                    raise TypeError("source_admission_id must be a non-empty string")
                payload["source_admission_id"] = source_admission_id
            event, view = self._append_event(
                "adopt checkpoint",
                _ADOPTION_EVENT_KIND,
                lambda: payload,
            )
            self._committed_adoption_ids.add(event.payload["adoption_id"])
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            return view

    def has_committed_adoption(self, adoption_id: str) -> bool:
        """Pure recovery query for the authoritative adoption commit."""
        _string(adoption_id, "adoption_id")
        with self._transition_lock:
            return adoption_id in self._committed_adoption_ids

    @property
    def effective_context(self) -> bytes | None:
        with self._transition_lock:
            return self._effective_context

    @property
    def raw_fact_ids(self) -> tuple[str, ...]:
        with self._transition_lock:
            return self._raw_fact_ids

    @property
    def pinned_generation_id(self) -> str:
        """The immutable Lock identity pinned by this Session."""
        with self._transition_lock:
            return self._view.effective_lock_hash

    @property
    def generation_sequence(self) -> tuple[str, ...]:
        """Ordered Lock identities that have governed this Session."""
        with self._transition_lock:
            return tuple(
                (
                    event.payload["target_generation_id"]
                    if event.kind == _ADOPTION_EVENT_KIND
                    else event.payload["effective_lock_hash"]
                )
                for event in self._events
                if event.kind
                in {"session.started", "session.reconfigured", _ADOPTION_EVENT_KIND}
            )

    @property
    def trajectory_segments(self) -> tuple[Mapping[str, Any], ...]:
        with self._transition_lock:
            session_id = self._view.session_id
            boundaries = tuple(
                event
                for event in self._events
                if event.kind
                in {"session.started", "session.reconfigured", _ADOPTION_EVENT_KIND}
            )
            return tuple(
                MappingProxyType(
                    {
                        "segment_id": _trajectory_segment_id(
                            session_id,
                            index,
                            (
                                boundary.payload["target_generation_id"]
                                if boundary.kind == _ADOPTION_EVENT_KIND
                                else boundary.payload["effective_lock_hash"]
                            ),
                        ),
                        "segment_index": index,
                        "generation_id": (
                            boundary.payload["target_generation_id"]
                            if boundary.kind == _ADOPTION_EVENT_KIND
                            else boundary.payload["effective_lock_hash"]
                        ),
                        "start_sequence": boundary.sequence,
                    }
                )
                for index, boundary in enumerate(boundaries)
            )

    @property
    def adoption_history(self) -> tuple[Mapping[str, Any], ...]:
        with self._transition_lock:
            session_id = self._view.session_id
            prior = None
            history = []
            for event in self._events:
                if event.kind not in {
                    "session.started",
                    "session.reconfigured",
                    _ADOPTION_EVENT_KIND,
                }:
                    continue
                generation = (
                    event.payload["target_generation_id"]
                    if event.kind == _ADOPTION_EVENT_KIND
                    else event.payload["effective_lock_hash"]
                )
                if event.kind == "session.reconfigured":
                    history.append(
                        MappingProxyType(
                            {
                                "old_generation_id": prior,
                                "new_generation_id": generation,
                                "reason": event.payload["reason"],
                                "effective_sequence": event.sequence,
                                "trajectory_segment_id": _trajectory_segment_id(
                                    session_id, len(history) + 1, generation
                                ),
                            }
                        )
                    )
                elif event.kind == _ADOPTION_EVENT_KIND:
                    history.append(
                        MappingProxyType(
                            {
                                "adoption_id": event.payload["adoption_id"],
                                "checkpoint_id": event.payload["checkpoint_id"],
                                "old_generation_id": prior,
                                "new_generation_id": generation,
                                "reason": event.payload.get("reason", ""),
                                "effective_sequence": event.sequence,
                                "source_frontier": event.payload["source_frontier"],
                                "source_body_sha256": event.payload[
                                    "source_body_sha256"
                                ],
                                "trajectory_segment_id": _trajectory_segment_id(
                                    session_id, len(history) + 1, generation
                                ),
                            }
                        )
                    )
                prior = generation
            return tuple(history)

    def lifecycle_projection(self) -> dict[str, Any]:
        """Return durable generation and checkpoint facts for public diagnosis."""
        with self._transition_lock:
            return {
                "schema_version": "bb.session_lifecycle.v1",
                "generation_sequence": list(self.generation_sequence),
                "trajectory_segments": [
                    _plain(segment) for segment in self.trajectory_segments
                ],
                "checkpoints": [
                    checkpoint.as_dict() for checkpoint in self._checkpoints.values()
                ],
                "adoptions": [_plain(adoption) for adoption in self.adoption_history],
                "pending": {
                    "approval_request_id": self._view.pending_approval,
                    "effects": {
                        "status": "runtime_owner_required",
                        "references": [],
                    },
                },
            }

    def projected_read_model(
        self, *, as_of: int | None = None, expected_projector_version: str | None = None
    ) -> Projected[SessionView]:
        return project_session_live(
            self, as_of=as_of, expected_projector_version=expected_projector_version
        )

    def context_snapshot(self):
        """Return the current context and its exact durable Session frontier."""
        from breadboard.modules.author import (
            ContextSnapshot,
            ContextSourceProvenance,
            EffectiveContextDocument,
        )

        with self._transition_lock:
            compaction = next(
                (
                    row
                    for row in reversed(self._events)
                    if row.kind == "context.compacted"
                ),
                None,
            )
            effective = self._effective_context or b"[]"
            context_sha256 = _hash_bytes(effective)
            compaction_index = (
                0 if compaction is None else int(compaction.payload["compaction_index"])
            )
            source_start = (
                1
                if compaction is None
                else int(compaction.payload["source_sequence_start"])
            )
            source_end = (
                len(self._events)
                if compaction is None
                else int(compaction.payload["source_sequence_end"])
            )
            raw_fact_ids = tuple(self._raw_fact_ids)
            shadowed = (
                ()
                if compaction is None
                else tuple(compaction.payload["shadowed_raw_fact_ids"])
            )
            return ContextSnapshot(
                session_id=self._view.session_id,
                context_id=(
                    f"{self._view.session_id}:context:{compaction_index}:"
                    f"{context_sha256.removeprefix('sha256:')}"
                ),
                session_event_sequence=len(self._events),
                effective_context=EffectiveContextDocument(
                    encoding="utf-8-json",
                    body=effective,
                    context_sha256=context_sha256,
                ),
                raw_fact_ids=raw_fact_ids,
                shadowed_raw_fact_ids=shadowed,
                source=ContextSourceProvenance(
                    session_id=self._view.session_id,
                    trajectory_segment_id=self._view.trajectory_segment_id,
                    source_sequence_start=source_start,
                    source_sequence_end=source_end,
                ),
                compaction_index=compaction_index,
                turn_index=None,
            )

    def propose_turn_policy(self, decision):
        """Atomically apply a typed turn decision or return a CAS refusal."""
        from breadboard.modules.author import (
            CompactionProposal,
            TurnPolicyDecision,
            TurnPolicyReceipt,
        )

        if not isinstance(decision, TurnPolicyDecision):
            raise TypeError("turn policy requires a TurnPolicyDecision")
        with self._transition_lock:
            current = self.context_snapshot()
            applied_hash = current.effective_context.context_sha256
            proposal_id = (
                f"{current.session_id}:turn:{current.session_event_sequence}:"
                f"{decision.kind}"
            )
            if decision.expected_context_sha256 != applied_hash:
                return TurnPolicyReceipt(
                    proposal_id,
                    False,
                    current.session_event_sequence,
                    applied_hash,
                    "context_hash_mismatch",
                )
            if decision.expected_context_sequence != current.session_event_sequence:
                return TurnPolicyReceipt(
                    proposal_id,
                    False,
                    current.session_event_sequence,
                    applied_hash,
                    "context_sequence_mismatch",
                )
            if decision.kind == "compact":
                proposal = decision.compaction
                if not isinstance(proposal, CompactionProposal):
                    return TurnPolicyReceipt(
                        proposal_id,
                        False,
                        current.session_event_sequence,
                        applied_hash,
                        "compaction_missing",
                    )
                prior_compaction = next(
                    (
                        row
                        for row in reversed(self._events)
                        if row.kind == "context.compacted"
                    ),
                    None,
                )
                expected_source_start = (
                    1 if prior_compaction is None else int(prior_compaction.sequence)
                )
                if (
                    proposal.expected_context_sha256 != applied_hash
                    or proposal.effective_context.context_sha256
                    != _hash_bytes(proposal.effective_context.body)
                    or proposal.source_sequence_end != current.session_event_sequence
                    or proposal.source_sequence_start != expected_source_start
                    or proposal.compaction_index != current.compaction_index + 1
                    or tuple(proposal.raw_fact_ids[: len(current.raw_fact_ids)])
                    != current.raw_fact_ids
                ):
                    return TurnPolicyReceipt(
                        proposal_id,
                        False,
                        current.session_event_sequence,
                        applied_hash,
                        "context_frontier_mismatch",
                    )
                try:
                    snapshot = CompactionSnapshot(
                        effective_context=proposal.effective_context.body,
                        raw_fact_ids=proposal.raw_fact_ids,
                        expected_context_sha256=decision.expected_context_sha256,
                        expected_context_sequence=decision.expected_context_sequence,
                        expected_source_sequence_end=proposal.source_sequence_end,
                        expected_raw_fact_ids=current.raw_fact_ids,
                    )
                except (TypeError, ValueError):
                    return TurnPolicyReceipt(
                        proposal_id,
                        False,
                        current.session_event_sequence,
                        applied_hash,
                        "compaction_invalid",
                    )
                try:
                    self.compact(snapshot)
                except ContextCASConflict as error:
                    return TurnPolicyReceipt(
                        proposal_id,
                        False,
                        current.session_event_sequence,
                        applied_hash,
                        error.code,
                    )
            elif decision.kind in {"pause", "complete"}:
                try:
                    if decision.kind == "pause":
                        self.pause(decision.reason)
                    else:
                        self.complete(decision.reason)
                except (RuntimeError, ValueError):
                    return TurnPolicyReceipt(
                        proposal_id,
                        False,
                        current.session_event_sequence,
                        applied_hash,
                        "turn_policy_refused",
                    )
            elif decision.kind != "continue":
                return TurnPolicyReceipt(
                    proposal_id,
                    False,
                    current.session_event_sequence,
                    applied_hash,
                    "turn_policy_kind_unknown",
                )
            updated = self.context_snapshot()
            return TurnPolicyReceipt(
                proposal_id,
                True,
                updated.session_event_sequence,
                updated.effective_context.context_sha256,
                None,
            )

    def compact(self, snapshot: CompactionSnapshot) -> CompactionEvent:
        if not isinstance(snapshot, CompactionSnapshot):
            raise TypeError("compact requires a CompactionSnapshot")
        with self._transition_lock:
            current_hash = _hash_bytes(self._effective_context or b"[]")
            current_sequence = len(self._events)
            if (
                snapshot.expected_context_sha256 is not None
                and snapshot.expected_context_sha256 != current_hash
            ):
                raise ContextCASConflict(
                    "context_hash_mismatch",
                    "effective context changed since the proposal was observed",
                )
            if (
                snapshot.expected_context_sequence is not None
                and snapshot.expected_context_sequence != current_sequence
            ):
                raise ContextCASConflict(
                    "context_sequence_mismatch",
                    "Session event frontier changed since the proposal was observed",
                )
            if (
                snapshot.expected_source_sequence_end is not None
                and snapshot.expected_source_sequence_end != current_sequence
            ):
                raise ContextCASConflict(
                    "context_frontier_mismatch",
                    "compaction source frontier no longer matches Session",
                )
            if (
                snapshot.expected_raw_fact_ids is not None
                and snapshot.expected_raw_fact_ids != self._raw_fact_ids
            ):
                raise ContextCASConflict(
                    "raw_fact_frontier_mismatch",
                    "retained raw-fact frontier changed since the proposal was observed",
                )
            event, _ = self._append_event(
                "compact",
                "context.compacted",
                lambda: self._compaction_payload(snapshot),
            )
            self._effective_context = snapshot.effective_context
            self._raw_fact_ids = snapshot.raw_fact_ids
            return _compaction_event(event)

    def _compaction_payload(self, snapshot: CompactionSnapshot) -> dict[str, Any]:
        previous = next(
            (row for row in reversed(self._events) if row.kind == "context.compacted"),
            None,
        )
        if snapshot.raw_fact_ids[: len(self.raw_fact_ids)] != self.raw_fact_ids:
            raise ValueError("compaction cannot reorder or discard retained raw facts")
        context_sha256 = _hash_bytes(snapshot.effective_context)
        shadowed_raw_fact_ids = (
            list(previous.payload["raw_fact_ids"])
            if previous is not None
            and previous.payload["context_sha256"] != context_sha256
            else []
        )
        return {
            "compaction_index": 1
            if previous is None
            else previous.payload["compaction_index"] + 1,
            "source_sequence_start": 1 if previous is None else previous.sequence,
            "source_sequence_end": len(self._events),
            "context_encoding": "base64",
            "effective_context": base64.b64encode(snapshot.effective_context).decode(
                "ascii"
            ),
            "context_sha256": context_sha256,
            "raw_fact_ids": list(snapshot.raw_fact_ids),
            "shadowed_raw_fact_ids": shadowed_raw_fact_ids,
        }

    def _typed_input_frontier(self) -> tuple[int, bool]:
        typed = [
            event.payload["module_input"]
            for event in self._events
            if event.kind == "session.started"
            and "module_input" in event.payload
            or event.kind == "input.accepted"
            and "module_input" in event.payload
        ]
        return len(typed), bool(typed and typed[-1]["final"])

    def input(
        self,
        content: str | None = None,
        attachments: Iterable[ArtifactRef] = (),
        *,
        module_input: ModuleInput | None = None,
        module_input_sequence: int | None = None,
    ) -> SessionView:
        def payload() -> dict[str, Any]:
            if (content is None) == (module_input is None):
                raise ValueError("supply exactly one content or module_input")
            if module_input is None:
                _check(
                    isinstance(content, str) and bool(content.strip()),
                    ValueError,
                    "input must be non-empty",
                )
                return {
                    "content_hash": _hash(content),
                    "attachments": [ref.as_dict() for ref in attachments],
                }
            if not isinstance(module_input, ModuleInput):
                raise TypeError("module_input must be a ModuleInput")
            next_sequence, closed = self._typed_input_frontier()
            _validate_module_input_sequence(
                module_input_sequence,
                expected=next_sequence,
            )
            if closed:
                raise ValueError("typed input follows a final module input")
            return {
                "module_input": module_input.to_dict(),
                "module_input_sequence": module_input_sequence,
                "attachments": [ref.as_dict() for ref in attachments],
            }

        return self._append("accept input", "input.accepted", payload)

    def input_digest(
        self, content_hash: str, attachments: Iterable[ArtifactRef] = ()
    ) -> SessionView:
        """Append an accepted input when only its retained content hash is available."""
        return self._append(
            "accept input",
            "input.accepted",
            lambda: (
                _sha256(content_hash, "content_hash"),
                {
                    "content_hash": content_hash,
                    "attachments": [ref.as_dict() for ref in attachments],
                },
            )[1],
        )

    def _module_output_frontier(
        self,
        module_id: str,
        key: RequestKey,
    ) -> tuple[int, bool]:
        identity = (
            module_id,
            key.worker_session_id,
            key.request_id,
            key.generation_id,
            key.instance_id,
            key.work_id,
            key.attempt_id,
            str(key.authority_epoch),
        )
        prior = [
            event.payload
            for event in self._events
            if event.kind == "module_output"
            and (
                event.payload["module_id"],
                event.payload["worker_session_id"],
                event.payload["request_id"],
                event.payload["generation_id"],
                event.payload["instance_id"],
                event.payload["work_id"],
                event.payload["attempt_id"],
                str(event.payload["authority_epoch"]),
            )
            == identity
        ]
        return len(prior), bool(prior and prior[-1]["module_output"]["final"])

    def module_output(
        self,
        output: OutputEnvelope,
        *,
        key: RequestKey,
        module_id: str,
        output_sequence: int,
        final: bool,
    ) -> SessionView:
        def payload() -> dict[str, Any]:
            if not isinstance(output, OutputEnvelope):
                raise TypeError("module_output requires an OutputEnvelope")
            if not isinstance(key, RequestKey):
                raise TypeError("module_output key requires a RequestKey")
            _string(module_id, "module_id")
            _validate_output_sequence(output_sequence)
            if type(final) is not bool:
                raise TypeError("module output final must be boolean")
            expected_sequence, closed = self._module_output_frontier(module_id, key)
            if closed:
                raise ValueError("module output follows a final output")
            if output_sequence != expected_sequence:
                raise ValueError("module output sequences must be contiguous per owner")
            return {
                "module_output": {
                    "schema_id": output.schema_id,
                    "body": base64.b64encode(output.body).decode("ascii"),
                    "final": final,
                },
                "output_sequence": output_sequence,
                "module_id": module_id,
                **key.as_dict(),
            }

        return self._append("emit module output", "module_output", payload)

    def assistant_message(
        self,
        content: str,
        *,
        message_id: str | None = None,
        trajectory_id: str | None = None,
    ) -> SessionView:
        return self._append(
            "observe assistant",
            "assistant_message",
            lambda: self._assistant_event_payload(content, message_id, trajectory_id),
        )

    def _assistant_event_payload(
        self, content: str, message_id: str | None, trajectory_id: str | None
    ) -> dict[str, Any]:
        payload = _assistant_payload(content, message_id, trajectory_id)
        if message_id is not None and any(
            event.kind == "assistant_message"
            and event.payload.get("message_id") == message_id
            for event in self._events
        ):
            raise ValueError("duplicate canonical message identity")
        return payload

    def tool_called(self, tool: str) -> SessionView:
        return self._append(
            "observe tool call",
            "tool_call",
            lambda: (
                _check(
                    type(tool) is str and bool(tool),
                    ValueError,
                    "tool name must be a non-empty string",
                ),
                {"tool": tool},
            )[1],
        )

    def annotate(self, record: AnnotationRecord) -> SessionView:
        with self._transition_lock:
            if (
                self._view.status not in {"completed", "failed", "canceled"}
                or self._terminal_annotation_commit is None
            ):
                return self._append(
                    "annotate", "annotation", lambda: self._annotation_payload(record)
                )
            self._require("annotate")
            self._annotation_payload(record)
            self._appending = True
            try:
                events = tuple(self._terminal_annotation_commit(record))
                if (
                    len(events) <= len(self._events)
                    or events[: len(self._events)] != tuple(self._events)
                    or events[-1].kind != "annotation"
                    or events[-1].payload != record.as_dict()
                ):
                    raise RuntimeError(
                        "durable annotation commit returned inconsistent events"
                    )
                view = rebuild(events)
                self._events, self._view = list(events), view
                return view
            finally:
                self._appending = False

    def _bind_terminal_annotation_commit(
        self, commit: Callable[[AnnotationRecord], tuple[KernelEvent, ...]]
    ) -> None:
        if not callable(commit):
            raise TypeError("terminal annotation commit must be callable")
        with self._transition_lock:
            self._terminal_annotation_commit = commit

    def tool_completed(self, tool: str, failed: bool) -> SessionView:
        return self._append(
            "observe tool result",
            "tool_result",
            lambda: (
                _check(
                    type(tool) is str and bool(tool),
                    ValueError,
                    "tool name must be a non-empty string",
                ),
                _check(
                    type(failed) is bool,
                    TypeError,
                    "tool completion error flag must be boolean",
                ),
                {"tool": tool, "error": failed},
            )[2],
        )

    def request_approval(self, request_id: str, operation: str) -> SessionView:
        return self._append(
            "request approval",
            "approval.requested",
            lambda: (
                _check(
                    bool(request_id and operation),
                    ValueError,
                    "approval request fields must be populated",
                ),
                {"request_id": request_id, "operation": operation},
            )[1],
        )

    def resolve_approval(self, request_id: str, decision: str) -> SessionView:
        return self._append(
            "resolve approval",
            "approval.resolved",
            lambda: (
                _check(
                    bool(request_id and decision in _DECISIONS),
                    ValueError,
                    "invalid approval decision",
                ),
                {"request_id": request_id, "decision": decision},
            )[1],
        )

    def adopt_generation(self, lock: EffectiveHarnessLock, reason: str) -> SessionView:
        if not isinstance(lock, EffectiveHarnessLock):
            raise GenerationAdoptionError(
                "incompatible", "generation must be an EffectiveHarnessLock"
            )
        if not isinstance(reason, str):
            raise GenerationAdoptionError(
                "incompatible", "adoption reason must be a string"
            )
        try:
            generation_id = _generation_id(lock)
        except (TypeError, ValueError) as error:
            raise GenerationAdoptionError(
                "incompatible", "generation Lock has no canonical identity"
            ) from error
        return self._append(
            "reconfigure",
            "session.reconfigured",
            lambda: {"effective_lock_hash": generation_id, "reason": reason},
        )

    def reconfigure(self, lock: EffectiveHarnessLock, reason: str) -> SessionView:
        return self.adopt_generation(lock, reason)

    def pause(self, reason: str) -> SessionView:
        return self._append("pause", "session.paused", lambda: {"reason": reason})

    def resume(self) -> SessionView:
        return self._append("resume", "session.resumed", lambda: {})

    def cancel(self, reason: str = "operator request") -> SessionView:
        return self._append(
            "cancel",
            "session.canceled",
            lambda: {"outcome": "canceled", "reason": reason},
        )

    def complete(self, summary: str = "completed") -> SessionView:
        return self._append(
            "complete",
            "session.completed",
            lambda: {"outcome": "completed", "summary": summary},
        )

    def fail(self, error_code: str, detail: str) -> SessionView:
        return self._append(
            "fail",
            "session.failed",
            lambda: (
                _check(
                    bool(error_code and detail),
                    ValueError,
                    "terminal error fields must be populated",
                ),
                {"outcome": "failed", "error": error_code, "detail": detail},
            )[1],
        )

    def _annotation_payload(self, record: AnnotationRecord) -> dict[str, str]:
        if not isinstance(record, AnnotationRecord):
            raise TypeError("annotation requires an AnnotationRecord")
        message_targets = {
            event.payload["message_id"]: event.payload["trajectory_id"]
            for event in self._events
            if event.kind == "assistant_message" and "message_id" in event.payload
        }
        annotation_ids = {
            event.payload["annotation_id"]
            for event in self._events
            if event.kind == "annotation"
        }
        if record.annotation_id in annotation_ids:
            raise ValueError("duplicate annotation_id")
        if message_targets.get(record.message_id) != record.trajectory_id:
            raise ValueError("annotation target is not registered for this session")
        return record.as_dict()

    def _require(self, action: str) -> None:
        if self._appending:
            raise RuntimeError("cannot mutate session while an append is in progress")
        if self._view.status not in _SESSION_ACTIONS[action]:
            raise RuntimeError(f"cannot {action} while session is {self._view.status}")

    def _append_event(
        self, action: str, kind: str, payload: Callable[[], dict[str, Any]]
    ) -> tuple[KernelEvent, SessionView]:
        with self._transition_lock:
            self._require(action)
            self._appending = True
            try:
                body = payload()
                if kind in _TERMINAL and self._view.lineage is not None:
                    body["lineage"] = self._view.lineage.as_dict()
                event = KernelEvent.create(
                    self._view.session_id,
                    len(self._events) + 1,
                    kind,
                    self._clock.now(),
                    body,
                )
                next_events = [*self._events, event]
                next_view = rebuild(next_events)
                self._sink.append(event)
                self._events, self._view = next_events, next_view
                return event, next_view
            finally:
                self._appending = False

    def _append(
        self, action: str, kind: str, payload: Callable[[], dict[str, Any]]
    ) -> SessionView:
        return self._append_event(action, kind, payload)[1]


def replay_differential(session: Session) -> dict[str, Any]:
    """Compare live compaction reconstruction with a fresh durable replay."""
    if not isinstance(session, Session):
        raise TypeError("replay_differential requires a Session")
    restored = Session.restore(session.events)
    difference: dict[str, Any] = {}
    if restored.effective_context != session.effective_context:
        difference["effective_context"] = {
            "live": None
            if session.effective_context is None
            else _hash_bytes(session.effective_context),
            "replay": None
            if restored.effective_context is None
            else _hash_bytes(restored.effective_context),
        }
    live_facts = tuple(session.raw_fact_ids)
    replay_facts = tuple(restored.raw_fact_ids)
    if live_facts != replay_facts:
        difference["raw_fact_ids"] = {
            "live": list(live_facts),
            "replay": list(replay_facts),
        }
    return difference


def project_session_live(
    session: Session,
    *,
    as_of: int | None = None,
    expected_projector_version: str | None = None,
) -> Projected[SessionView]:
    if not isinstance(session, Session):
        raise TypeError("live Session projection requires a Session")
    return project_session_replay(
        session.events,
        as_of=as_of,
        expected_projector_version=expected_projector_version,
    )
