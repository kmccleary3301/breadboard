"""Bounded framed transport and strict worker wire records.

The channel is four-byte big-endian length followed by UTF-8 canonical JSON.
The JSON envelope is typed at the boundary; domain requests are explicit wire
kinds, never a generic invocation bag.
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from io import BufferedIOBase
from typing import Final, Literal, TypeAlias


MAX_FRAME_BYTES: Final = 262_144
MAX_CHECKPOINT_BYTES: Final = 1_048_576
MAX_CHECKPOINT_CHUNK_BYTES: Final = 160 * 1024
MAX_CHECKPOINT_CHUNKS: Final = (
    MAX_CHECKPOINT_BYTES + MAX_CHECKPOINT_CHUNK_BYTES - 1
) // MAX_CHECKPOINT_CHUNK_BYTES
PROTOCOL_VERSION: Final = 2

WireKind: TypeAlias = Literal[
    "start", "input", "output", "service_result", "checkpoint_request",
    "checkpoint_prepare", "checkpoint_compatibility", "checkpoint",
    "checkpoint_chunk", "result", "cancel", "close", "ready", "failure",
    "provider_request", "tool_request", "context_request", "child_request",
    "dependency_request",
]
WIRE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "start", "input", "output", "service_result", "checkpoint_request",
        "checkpoint_prepare", "checkpoint_compatibility", "checkpoint",
        "checkpoint_chunk", "result", "cancel", "close", "ready", "failure",
        "provider_request", "tool_request", "context_request", "child_request",
        "dependency_request",
    }
)


class TransportError(RuntimeError):
    """Base class for bounded transport failures."""


class FrameEOF(TransportError):
    """The peer closed the channel between frames."""


class FrameLimitError(TransportError):
    """A frame or encoded body exceeded its immutable bound."""


class WireProtocolError(TransportError):
    """A malformed or schema-mismatched wire value."""


def _read_exact(stream: BufferedIOBase, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise FrameEOF("channel ended mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BufferedIOBase, *, max_bytes: int = MAX_FRAME_BYTES) -> bytes | None:
    """Read one bounded frame; return ``None`` only for clean pre-header EOF."""
    if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > MAX_FRAME_BYTES:
        raise ValueError("max_bytes must be within the protocol frame bound")
    prefix = stream.read(4)
    if prefix == b"":
        return None
    if len(prefix) != 4:
        raise FrameEOF("channel ended in frame length")
    size = int.from_bytes(prefix, "big", signed=False)
    if size > max_bytes:
        raise FrameLimitError(f"frame is {size} bytes; maximum is {max_bytes}")
    return _read_exact(stream, size)


def write_frame(stream: BufferedIOBase, payload: bytes, *, max_bytes: int = MAX_FRAME_BYTES) -> None:
    if not isinstance(payload, bytes):
        raise TypeError("frame payload must be bytes")
    if len(payload) > max_bytes or len(payload) > MAX_FRAME_BYTES:
        raise FrameLimitError(f"frame is {len(payload)} bytes; maximum is {max_bytes}")
    stream.write(len(payload).to_bytes(4, "big", signed=False))
    stream.write(payload)
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise WireProtocolError("wire body is not canonical JSON") from exc


def _json_object(payload: bytes) -> Mapping[str, object]:
    try:
        text = payload.decode("utf-8")
        decoded = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, WireProtocolError) as exc:
        raise WireProtocolError("frame is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, Mapping):
        raise WireProtocolError("frame JSON must be an object")
    return decoded


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WireProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def encode_bytes(value: bytes, *, maximum: int = MAX_FRAME_BYTES) -> str:
    if not isinstance(value, bytes):
        raise TypeError("payload must be bytes")
    if len(value) > maximum:
        raise FrameLimitError(f"payload is {len(value)} bytes; maximum is {maximum}")
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: object, *, maximum: int = MAX_FRAME_BYTES) -> bytes:
    if not isinstance(value, str):
        raise WireProtocolError("binary body must be a base64 string")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise WireProtocolError("binary body is not valid base64") from exc
    if len(decoded) > maximum:
        raise FrameLimitError(f"decoded body is {len(decoded)} bytes; maximum is {maximum}")
    return decoded


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise WireProtocolError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise WireProtocolError(f"{label} must be an integer >= {minimum}")
    return value


def _exact(raw: Mapping[str, object], fields: frozenset[str], label: str) -> None:
    actual = set(raw)
    missing = fields - actual
    unknown = actual - fields
    if missing or unknown:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            detail.append("unknown " + ", ".join(sorted(unknown)))
        raise WireProtocolError(f"{label}: {'; '.join(detail)}")


@dataclass(frozen=True, slots=True)
class RequestKey:
    worker_session_id: str
    request_id: str
    generation_id: str
    instance_id: str
    work_id: str
    attempt_id: str
    authority_epoch: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.worker_session_id, "worker_session_id"),
            (self.request_id, "request_id"),
            (self.generation_id, "generation_id"),
            (self.instance_id, "instance_id"),
            (self.work_id, "work_id"),
            (self.attempt_id, "attempt_id"),
        ):
            _text(value, label)
        _integer(self.authority_epoch, "authority_epoch")

    @classmethod
    def from_dict(cls, value: object) -> "RequestKey":
        if not isinstance(value, Mapping):
            raise WireProtocolError("header.key must be an object")
        fields = frozenset({
            "worker_session_id", "request_id", "generation_id", "instance_id",
            "work_id", "attempt_id", "authority_epoch",
        })
        _exact(value, fields, "header.key")
        return cls(
            worker_session_id=_text(value["worker_session_id"], "worker_session_id"),
            request_id=_text(value["request_id"], "request_id"),
            generation_id=_text(value["generation_id"], "generation_id"),
            instance_id=_text(value["instance_id"], "instance_id"),
            work_id=_text(value["work_id"], "work_id"),
            attempt_id=_text(value["attempt_id"], "attempt_id"),
            authority_epoch=_integer(value["authority_epoch"], "authority_epoch"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "authority_epoch": self.authority_epoch,
            "attempt_id": self.attempt_id,
            "generation_id": self.generation_id,
            "instance_id": self.instance_id,
            "request_id": self.request_id,
            "worker_session_id": self.worker_session_id,
            "work_id": self.work_id,
        }


@dataclass(frozen=True, slots=True)
class WireHeader:
    protocol_version: int
    kind: WireKind
    key: RequestKey
    sequence: int

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise WireProtocolError("unsupported worker protocol version")
        if self.kind not in WIRE_KINDS:
            raise WireProtocolError(f"unsupported worker wire kind: {self.kind!r}")
        _integer(self.sequence, "header.sequence")

    @classmethod
    def from_dict(cls, value: object) -> "WireHeader":
        if not isinstance(value, Mapping):
            raise WireProtocolError("header must be an object")
        fields = frozenset({"protocol_version", "kind", "key", "sequence"})
        _exact(value, fields, "header")
        kind = value["kind"]
        if not isinstance(kind, str) or kind not in WIRE_KINDS:
            raise WireProtocolError("header.kind is not supported")
        return cls(
            protocol_version=_integer(value["protocol_version"], "protocol_version"),
            kind=kind,  # type: ignore[arg-type]
            key=RequestKey.from_dict(value["key"]),
            sequence=_integer(value["sequence"], "header.sequence"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key.as_dict(),
            "kind": self.kind,
            "protocol_version": self.protocol_version,
            "sequence": self.sequence,
        }


_CHECKPOINT_FIELDS: Final = frozenset(
    {
        "source_generation_id",
        "source_module_id",
        "source_instance_id",
        "source_work_id",
        "source_attempt_id",
        "schema_id",
        "body",
    }
)


def iter_message_frames(
    message: WireMessage,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
) -> Iterator[bytes]:
    """Encode a logical message without weakening the physical frame bound."""
    try:
        frame = message.encode(max_bytes=max_bytes)
    except FrameLimitError:
        if message.header.kind == "start":
            phase, field, context_name = "resume", "resume", "start"
        elif message.header.kind == "checkpoint_prepare":
            phase, field, context_name = "source", "source", "prepare"
        else:
            raise
        checkpoint = message.body.get(field)
        if not isinstance(checkpoint, Mapping):
            raise
        _exact(checkpoint, _CHECKPOINT_FIELDS, f"{phase} checkpoint")
        checkpoint_body = decode_bytes(
            checkpoint["body"], maximum=MAX_CHECKPOINT_BYTES
        )
        message_body = dict(message.body)
        del message_body[field]
        checkpoint_metadata = dict(checkpoint)
        del checkpoint_metadata["body"]
    else:
        yield frame
        return
    count = max(
        1,
        (len(checkpoint_body) + MAX_CHECKPOINT_CHUNK_BYTES - 1)
        // MAX_CHECKPOINT_CHUNK_BYTES,
    )
    for index in range(count):
        start = index * MAX_CHECKPOINT_CHUNK_BYTES
        chunk = checkpoint_body[start : start + MAX_CHECKPOINT_CHUNK_BYTES]
        yield WireMessage(
            WireHeader(
                message.header.protocol_version,
                "checkpoint_chunk",
                message.header.key,
                message.header.sequence,
            ),
            {
                "phase": phase,
                "chunk_index": index,
                "chunk_count": count,
                "total_bytes": len(checkpoint_body),
                context_name: message_body,
                **checkpoint_metadata,
                "body": encode_bytes(chunk, maximum=MAX_CHECKPOINT_CHUNK_BYTES),
            },
        ).encode(max_bytes=max_bytes)


@dataclass(frozen=True, slots=True)
class WireMessage:
    header: WireHeader
    body: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.body, Mapping):
            raise TypeError("wire body must be a mapping")

    def as_dict(self) -> dict[str, object]:
        return {"body": dict(self.body), "header": self.header.as_dict()}

    def encode(self, *, max_bytes: int = MAX_FRAME_BYTES) -> bytes:
        payload = _canonical_json(self.as_dict())
        if len(payload) > max_bytes or len(payload) > MAX_FRAME_BYTES:
            raise FrameLimitError(f"encoded message is {len(payload)} bytes")
        return payload

    @classmethod
    def decode(cls, payload: bytes) -> "WireMessage":
        raw = _json_object(payload)
        _exact(raw, frozenset({"header", "body"}), "frame")
        body = raw["body"]
        if not isinstance(body, Mapping):
            raise WireProtocolError("frame.body must be an object")
        return cls(WireHeader.from_dict(raw["header"]), dict(body))


def read_message(stream: BufferedIOBase, *, max_bytes: int = MAX_FRAME_BYTES) -> WireMessage | None:
    payload = read_frame(stream, max_bytes=max_bytes)
    return None if payload is None else WireMessage.decode(payload)


def write_message(stream: BufferedIOBase, message: WireMessage, *, max_bytes: int = MAX_FRAME_BYTES) -> None:
    write_frame(stream, message.encode(max_bytes=max_bytes), max_bytes=max_bytes)


__all__ = [
    "FrameEOF", "FrameLimitError", "MAX_CHECKPOINT_BYTES",
    "MAX_CHECKPOINT_CHUNK_BYTES", "MAX_CHECKPOINT_CHUNKS", "MAX_FRAME_BYTES",
    "PROTOCOL_VERSION", "RequestKey", "TransportError", "WIRE_KINDS", "WireHeader",
    "WireKind", "WireMessage", "WireProtocolError", "decode_bytes", "encode_bytes",
    "iter_message_frames", "read_frame", "read_message", "write_frame",
    "write_message",
]
