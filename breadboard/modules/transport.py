"""Bounded framed transport and strict worker wire records.

The channel is four-byte big-endian length followed by UTF-8 canonical JSON.
The JSON envelope is typed at the boundary; domain requests are explicit wire
kinds, never a generic invocation bag.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from io import BufferedIOBase
from typing import Final, Literal, TypeAlias


MAX_FRAME_BYTES: Final = 262_144
MAX_CHECKPOINT_BYTES: Final = 1_048_576
# Absolute ceilings; each transfer derives its lower physical capacity from its
# encoded envelope and configured frame budget.
MAX_CHECKPOINT_CHUNK_BYTES: Final = MAX_FRAME_BYTES
MAX_CHECKPOINT_CHUNKS: Final = MAX_CHECKPOINT_BYTES
_CHUNK_FIELDS: Final = frozenset(
    {
        "body",
        "body_sha256",
        "chunk_count",
        "chunk_index",
        "total_bytes",
    }
)
PROTOCOL_VERSION: Final = 2

WireKind: TypeAlias = Literal[
    "start", "input", "output", "service_result", "checkpoint_request",
    "checkpoint_prepare", "checkpoint_compatibility", "checkpoint",
    "checkpoint_chunk", "message_chunk", "result", "cancel", "close", "ready",
    "failure", "provider_request", "tool_request", "context_request",
    "child_request", "dependency_request",
]
WIRE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "start", "input", "output", "service_result", "checkpoint_request",
        "checkpoint_prepare", "checkpoint_compatibility", "checkpoint",
        "checkpoint_chunk", "message_chunk", "result", "cancel", "close",
        "ready", "failure", "provider_request", "tool_request",
        "context_request", "child_request", "dependency_request",
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
_FRAGMENTABLE_BODY_FIELDS: Final[Mapping[str, frozenset[str]]] = {
    "input": frozenset({"schema_id", "sequence", "body", "final"}),
    "output": frozenset({"schema_id", "body"}),
}
_MESSAGE_CHUNK_FIELDS: Final = frozenset(
    {
        "body",
        "body_sha256",
        "chunk_count",
        "chunk_index",
        "context",
        "message_kind",
        "total_bytes",
    }
)


def _chunk_capacity(
    header: WireHeader,
    kind: WireKind,
    metadata: Mapping[str, object],
    *,
    index: int,
    count: int,
    digest: str | None,
    max_bytes: int,
    advance_sequence: bool,
) -> int:
    chunk_header = (
        WireHeader(
            header.protocol_version,
            kind,
            header.key,
            header.sequence + index,
        )
        if advance_sequence
        else WireHeader(header.protocol_version, kind, header.key, header.sequence)
    )
    chunk_body = {
        **metadata,
        "chunk_count": count,
        "chunk_index": index,
        "total_bytes": metadata["total_bytes"],
        "body": "",
    }
    if digest is not None:
        chunk_body["body_sha256"] = digest
    empty = WireMessage(chunk_header, chunk_body)
    overhead = len(_canonical_json(empty.as_dict()))
    return 3 * ((max_bytes - overhead) // 4)


def _chunk_capacities(
    header: WireHeader,
    kind: WireKind,
    metadata: Mapping[str, object],
    payload: bytes,
    *,
    max_bytes: int,
    maximum: int,
    advance_sequence: bool,
    digest: str | None,
) -> tuple[int, ...]:
    total = len(payload)
    count = 1
    while True:
        capacities = tuple(
            min(
                maximum,
                _chunk_capacity(
                    header,
                    kind,
                    metadata,
                    index=index,
                    count=count,
                    digest=digest,
                    max_bytes=max_bytes,
                    advance_sequence=advance_sequence,
                ),
            )
            for index in range(count)
        )
        if total == 0:
            if capacities[0] < 0:
                raise FrameLimitError(
                    "chunk metadata exceeds the configured frame maximum"
                )
            return (0,)
        smallest = min(capacities)
        if smallest <= 0:
            raise FrameLimitError(
                "configured frame maximum leaves no chunk payload capacity"
            )
        if count <= total and sum(capacities) >= total:
            return capacities
        count = max(count + 1, (total + smallest - 1) // smallest)
        if count > total:
            raise FrameLimitError(
                "configured frame maximum cannot carry the chunked payload"
            )


def iter_chunked_messages(
    header: WireHeader,
    kind: Literal["checkpoint", "checkpoint_chunk", "message_chunk"],
    metadata: Mapping[str, object],
    payload: bytes,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
    maximum: int = MAX_FRAME_BYTES,
) -> Iterator[WireMessage]:
    """Split one bounded binary value into canonical physical messages."""
    if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > MAX_FRAME_BYTES:
        raise ValueError("max_bytes must be within the protocol frame bound")
    if (
        type(maximum) is not int
        or maximum <= 0
        or maximum > MAX_CHECKPOINT_BYTES
    ):
        raise ValueError("maximum must be within the logical payload bound")
    advance_sequence = kind == "checkpoint"
    if not isinstance(payload, bytes):
        raise TypeError("chunk payload must be bytes")
    if len(payload) > maximum:
        raise FrameLimitError(
            f"payload is {len(payload)} bytes; maximum is {maximum}"
        )
    reserved = set(metadata) & _CHUNK_FIELDS
    if reserved:
        raise WireProtocolError(
            "chunk metadata uses reserved fields: " + ", ".join(sorted(reserved))
        )
    framed_metadata = {**metadata, "total_bytes": len(payload)}
    digest = None if kind == "checkpoint" else hashlib.sha256(payload).hexdigest()
    capacities = _chunk_capacities(
        header,
        kind,
        framed_metadata,
        payload,
        max_bytes=max_bytes,
        maximum=maximum,
        advance_sequence=advance_sequence,
        digest=digest,
    )
    count = len(capacities)
    offset = 0
    for index, capacity in enumerate(capacities):
        remaining_chunks = count - index - 1
        size = min(capacity, len(payload) - offset - remaining_chunks)
        chunk = payload[offset : offset + size]
        offset += size
        chunk_header = (
            WireHeader(
                header.protocol_version,
                kind,
                header.key,
                header.sequence + index,
            )
            if advance_sequence
            else WireHeader(header.protocol_version, kind, header.key, header.sequence)
        )
        chunk_body = {
            **metadata,
            "chunk_count": count,
            "chunk_index": index,
            "total_bytes": len(payload),
            "body": encode_bytes(chunk, maximum=maximum),
        }
        if digest is not None:
            chunk_body["body_sha256"] = digest
        yield WireMessage(chunk_header, chunk_body)




_FRAGMENTABLE_JSON_KINDS: Final = frozenset({
    "start", "service_result", "dependency_request", "child_request",
    "provider_request", "tool_request", "context_request",
})


class MessageReassembler:
    """Strictly reassemble contiguous fragmented wire messages."""

    def __init__(self, *, maximum: int = MAX_FRAME_BYTES) -> None:
        if type(maximum) is not int or maximum <= 0 or maximum > MAX_FRAME_BYTES:
            raise ValueError("maximum must be within the logical message bound")
        self.maximum = maximum
        self._header: WireHeader | None = None
        self._metadata: Mapping[str, object] | None = None
        self._payload = bytearray()
        self._next_index = 0

    def accept(self, message: WireMessage) -> WireMessage | None:
        if message.header.kind != "message_chunk":
            if self._header is not None:
                raise WireProtocolError(
                    "fragmented message ended before its declared chunk count"
                )
            return message
        body = message.body
        _exact(body, _MESSAGE_CHUNK_FIELDS, "message chunk")
        message_kind = body["message_kind"]
        if (
            not isinstance(message_kind, str)
            or (
                message_kind not in _FRAGMENTABLE_BODY_FIELDS
                and message_kind not in _FRAGMENTABLE_JSON_KINDS
            )
        ):
            raise WireProtocolError("message chunk kind is not fragmentable")
        context = body["context"]
        if not isinstance(context, Mapping):
            raise WireProtocolError("message chunk context must be an object")
        if message_kind in _FRAGMENTABLE_BODY_FIELDS:
            expected_context_fields = _FRAGMENTABLE_BODY_FIELDS[message_kind] - {"body"}
            _exact(context, expected_context_fields, "message chunk context")
        else:
            _exact(context, frozenset(), "service message chunk context")
        # Serialized service envelopes include JSON/base64 overhead. Use the
        # chunk protocol's absolute logical ceiling, not a physical frame size.
        maximum = (
            MAX_CHECKPOINT_BYTES
            if message_kind in _FRAGMENTABLE_JSON_KINDS
            else self.maximum
        )
        index = _integer(body["chunk_index"], "message chunk index")
        count = _integer(body["chunk_count"], "message chunk count", minimum=1)
        total = _integer(body["total_bytes"], "message total bytes")
        digest = body["body_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise WireProtocolError("message chunk digest must be lowercase sha256")
        if (
            total > maximum
            or (total == 0 and count != 1)
            or (total > 0 and count > total)
        ):
            raise WireProtocolError("message chunk count or total exceeds its bound")
        metadata = {
            name: value
            for name, value in body.items()
            if name not in {"body", "chunk_index"}
        }
        if self._header is None:
            if index != 0:
                raise WireProtocolError("fragmented message did not start at chunk zero")
            self._header = message.header
            self._metadata = metadata
        elif (
            message.header != self._header
            or metadata != self._metadata
            or index != self._next_index
        ):
            raise WireProtocolError(
                "message chunks changed identity, metadata, or sequence"
            )
        chunk = decode_bytes(body["body"], maximum=min(maximum, MAX_FRAME_BYTES))
        if total > 0 and not chunk:
            raise WireProtocolError("non-empty fragmented message has an empty chunk")
        self._payload.extend(chunk)
        self._next_index += 1
        if len(self._payload) > total:
            raise WireProtocolError("message chunks exceed their declared length")
        if self._next_index < count:
            if len(self._payload) == total:
                raise WireProtocolError(
                    "message chunks reached their declared length too early"
                )
            return None
        if self._next_index != count or len(self._payload) != total:
            raise WireProtocolError("fragmented message is incomplete")
        payload = bytes(self._payload)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise WireProtocolError("fragmented message digest does not match its body")
        if message_kind in _FRAGMENTABLE_JSON_KINDS:
            rebuilt_body = _json_object(payload)
        else:
            rebuilt_body = dict(context)
            rebuilt_body["body"] = encode_bytes(payload, maximum=self.maximum)
        header = WireHeader(
            message.header.protocol_version,
            message_kind,  # type: ignore[arg-type]
            message.header.key,
            message.header.sequence,
        )
        self._header = None
        self._metadata = None
        self._payload.clear()
        self._next_index = 0
        return WireMessage(header, rebuilt_body)


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
            phase = field = context_name = ""
        checkpoint = message.body.get(field) if field else None
        if isinstance(checkpoint, Mapping):
            _exact(checkpoint, _CHECKPOINT_FIELDS, f"{phase} checkpoint")
            checkpoint_body = decode_bytes(
                checkpoint["body"], maximum=MAX_CHECKPOINT_BYTES
            )
            message_body = dict(message.body)
            del message_body[field]
            checkpoint_metadata = dict(checkpoint)
            del checkpoint_metadata["body"]
            chunks = iter_chunked_messages(
                WireHeader(
                    message.header.protocol_version,
                    "checkpoint_chunk",
                    message.header.key,
                    message.header.sequence,
                ),
                "checkpoint_chunk",
                {
                    "phase": phase,
                    context_name: message_body,
                    **checkpoint_metadata,
                },
                checkpoint_body,
                max_bytes=max_bytes,
                maximum=MAX_CHECKPOINT_BYTES,
            )
        elif message.header.kind in _FRAGMENTABLE_BODY_FIELDS:
            expected = _FRAGMENTABLE_BODY_FIELDS[message.header.kind]
            _exact(message.body, expected, f"{message.header.kind} message")
            payload = decode_bytes(message.body["body"], maximum=MAX_FRAME_BYTES)
            context = dict(message.body)
            del context["body"]
            chunks = iter_chunked_messages(
                WireHeader(
                    message.header.protocol_version,
                    "message_chunk",
                    message.header.key,
                    message.header.sequence,
                ),
                "message_chunk",
                {"message_kind": message.header.kind, "context": context},
                payload,
                max_bytes=max_bytes,
                maximum=MAX_FRAME_BYTES,
            )
        elif message.header.kind in _FRAGMENTABLE_JSON_KINDS:
            payload = _canonical_json(message.body)
            chunks = iter_chunked_messages(
                WireHeader(
                    message.header.protocol_version,
                    "message_chunk",
                    message.header.key,
                    message.header.sequence,
                ),
                "message_chunk",
                {"message_kind": message.header.kind, "context": {}},
                payload,
                max_bytes=max_bytes,
                maximum=MAX_CHECKPOINT_BYTES,
            )
        else:
            raise
        for chunk_message in chunks:
            yield chunk_message.encode(max_bytes=max_bytes)
    else:
        yield frame


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
    if payload is None:
        return None
    message = WireMessage.decode(payload)
    if message.header.kind != "message_chunk":
        return message
    reassembler = MessageReassembler(maximum=max_bytes)
    while True:
        complete = reassembler.accept(message)
        if complete is not None:
            return complete
        payload = read_frame(stream, max_bytes=max_bytes)
        if payload is None:
            raise FrameEOF("channel ended during fragmented message")
        message = WireMessage.decode(payload)


def write_message(stream: BufferedIOBase, message: WireMessage, *, max_bytes: int = MAX_FRAME_BYTES) -> None:
    for payload in iter_message_frames(message, max_bytes=max_bytes):
        write_frame(stream, payload, max_bytes=max_bytes)


__all__ = [
    "FrameEOF", "FrameLimitError", "MAX_CHECKPOINT_BYTES",
    "MAX_CHECKPOINT_CHUNK_BYTES", "MAX_CHECKPOINT_CHUNKS", "MAX_FRAME_BYTES",
    "MessageReassembler", "PROTOCOL_VERSION", "RequestKey", "TransportError",
    "WIRE_KINDS", "WireHeader", "WireKind", "WireMessage", "WireProtocolError",
    "decode_bytes", "encode_bytes", "iter_chunked_messages",
    "iter_message_frames", "read_frame", "read_message", "write_frame",
    "write_message",
]
