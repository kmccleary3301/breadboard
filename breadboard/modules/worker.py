"""Worker-only executable for captured author packages.

Run with ``python -m breadboard.modules.worker --stdio``.  The host owns the
process and sends a framed ``start`` message naming a read-only package path
(usually under ``/breadboard-captured``).  This process alone imports captured
author bytes. The SDK dispatches control frames during synchronous service
waits without creating an additional process or thread.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Callable, Final, Literal, TypeAlias

from .author import (
    CheckpointCompatibility,
    CheckpointCompatibilityContext,
    CheckpointCapture,
    CheckpointEnvelope,
    CheckpointProposal,
    CheckpointRefusal,
    CheckpointRequest,
    ChildFailed,
    ChildHandle,
    ChildOutput,
    ChildPlan,
    ChildSucceeded,
    ChildTarget,
    ChildUnknown,
    ContinueResult,
    DependencyAccess,
    DependencyBindings,
    DependencyDeclaration,
    EffectiveContextDocument,
    FailureResult,
    InputEnvelope,
    InstanceIdentity,
    ModuleInput,
    OutputEnvelope,
    OutputResult,
    PolicyFailure,
    ToolApproval,
    ToolApprovalRequest,
    ToolCancelled,
    ToolFailed,
    ToolSucceeded,
    ToolUnknown,
    TurnPolicyDecision,
    TurnPolicyReceipt,
    ContextSnapshot,
    ContextSourceProvenance,
)
from .transport import (
    FrameEOF,
    FrameLimitError,
    MAX_CHECKPOINT_BYTES,
    MAX_FRAME_BYTES,
    MIN_FRAME_BYTES,
    PROTOCOL_VERSION,
    RequestKey,
    WireHeader,
    WireMessage,
    WireProtocolError,
    WireKind,
    decode_bytes,
    encode_bytes,
    iter_chunked_messages,
    read_message,
    write_message,
)


_CAPTURED_ROOT = Path(os.environ.get("BREADBOARD_CAPTURED_ROOT", "/breadboard-captured"))
_MAX_PACKAGE_BYTES: Final = 48 * 1024 * 1024
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class WorkerError(RuntimeError):
    """A typed worker-side refusal, never an owner decision."""

    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise WorkerError("malformed_frame", f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise WorkerError("malformed_frame", f"{label} must be an integer >= {minimum}")
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise WorkerError("malformed_frame", f"{label} must be an object")
    return value


def _exact(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        detail = f"{label} fields differ"
        if missing:
            detail += "; missing " + ", ".join(missing)
        if unknown:
            detail += "; unknown " + ", ".join(unknown)
        raise WorkerError("malformed_frame", detail)


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise WorkerError("malformed_frame", "value is not canonical JSON") from exc


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class _CapturedPackage:
    root: Path
    manifest: Mapping[str, object]
    schema_documents: Mapping[str, Mapping[str, object]]
    module_name: str
    symbol_name: str


class _CapturedFinder(importlib.abc.MetaPathFinder):
    def __init__(self, members: Mapping[str, Path]) -> None:
        self._members = dict(members)
        self._parents = self._namespace_parents(self._members)

    @staticmethod
    def _namespace_parents(members: Mapping[str, Path]) -> set[str]:
        parents: set[str] = set()
        for name in members:
            parts = name.split(".")
            parents.update(".".join(parts[:index]) for index in range(1, len(parts)))
        return parents

    def find_spec(self, fullname: str, path: object = None, target: ModuleType | None = None):
        source = self._members.get(fullname)
        if source is not None:
            return importlib.util.spec_from_file_location(fullname, source)
        if fullname in self._parents:
            return importlib.util.spec_from_loader(fullname, loader=None, is_package=True)
        return None


class _PackageLoader:
    def __init__(self, package_path: Path, expected_digest: str) -> None:
        self._package_path = package_path
        self._expected_digest = expected_digest
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._finder: _CapturedFinder | None = None

    def load(self) -> _CapturedPackage:
        if not _DIGEST_RE.fullmatch(self._expected_digest):
            raise WorkerError("closure_mismatch", "package digest is not sha256")
        try:
            resolved_root = _CAPTURED_ROOT.resolve()
            resolved_path = self._package_path.resolve()
            if resolved_root not in resolved_path.parents:
                raise WorkerError("closure_mismatch", "package path is outside captured root")
            stat = resolved_path.stat()
        except (OSError, RuntimeError) as exc:
            raise WorkerError("closure_mismatch", "captured package cannot be opened") from exc
        if not resolved_path.is_file() or stat.st_size <= 0 or stat.st_size > _MAX_PACKAGE_BYTES:
            raise WorkerError("closure_mismatch", "captured package size or type is invalid")
        try:
            archive_bytes = resolved_path.read_bytes()
        except OSError as exc:
            raise WorkerError("closure_mismatch", "captured package cannot be read") from exc
        if _sha256(archive_bytes) != self._expected_digest:
            raise WorkerError("closure_mismatch", "captured package digest mismatch")
        self._temporary = tempfile.TemporaryDirectory(prefix="bb-author-")
        destination = Path(self._temporary.name)
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(archive_bytes), "r") as archive:
                return self._extract(archive, destination)
        except WorkerError:
            self.close()
            raise
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self.close()
            raise WorkerError("closure_mismatch", "captured package is not a valid archive") from exc

    def _extract(self, archive: zipfile.ZipFile, destination: Path) -> _CapturedPackage:
        infos = archive.infolist()
        paths = [info.filename for info in infos]
        if len(paths) != len(set(paths)):
            raise WorkerError("closure_mismatch", "captured package contains duplicate members")
        if "module.json" not in paths:
            raise WorkerError("closure_mismatch", "captured package has no module.json")
        manifest_bytes = archive.read("module.json")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkerError("closure_mismatch", "module.json is not valid JSON") from exc
        manifest_obj = _mapping(manifest, "module.json")
        if manifest_obj.get("schema_version") != "bb.module_manifest.v1":
            raise WorkerError("worker_protocol_mismatch", "unsupported module manifest")
        try:
            if _json_bytes(manifest_obj) != manifest_bytes:
                raise WorkerError("closure_mismatch", "module.json is not canonical JSON")
        except WorkerError:
            raise
        source_members = _member_records(manifest_obj, "source_members")
        import_members = _member_records(manifest_obj, "import_members")
        schema_members = _mapping(manifest_obj.get("schema_members"), "schema_members")
        declared = {"module.json"} | {record["path"] for record in source_members}
        actual = set(paths)
        if actual != declared:
            raise WorkerError("closure_mismatch", "archive members differ from manifest")
        members: dict[str, Path] = {}
        for record in source_members:
            path = record["path"]
            if not _safe_member(path):
                raise WorkerError("closure_mismatch", f"unsafe captured member: {path}")
            payload = archive.read(path)
            if len(payload) != record["size_bytes"] or _sha256(payload) != record["sha256"]:
                raise WorkerError("closure_mismatch", f"captured member digest mismatch: {path}")
            target = destination / Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            os.chmod(target, 0o444)
        for record in import_members:
            path = record["path"]
            if path not in declared:
                raise WorkerError("closure_mismatch", f"import member is not a source member: {path}")
            payload = archive.read(path)
            if len(payload) != record["size_bytes"] or _sha256(payload) != record["sha256"]:
                raise WorkerError("closure_mismatch", f"import member digest mismatch: {path}")
            module_name = record["module"]
            if module_name in sys.modules:
                raise WorkerError(
                    "closure_mismatch",
                    f"declared import member is already loaded in worker: {module_name}",
                )
            if module_name in members and members[module_name] != destination / Path(path):
                raise WorkerError("closure_mismatch", f"duplicate import binding: {module_name}")
            members[module_name] = destination / Path(path)
        schema_documents: dict[str, Mapping[str, object]] = {}
        for schema_id, path_value in schema_members.items():
            schema_id = _text(schema_id, "schema id")
            path = _text(path_value, "schema member path")
            if path not in declared:
                raise WorkerError("closure_mismatch", f"schema member is not captured: {path}")
            try:
                document = json.loads(archive.read(path).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkerError("closure_mismatch", f"schema {schema_id} is not JSON") from exc
            document_obj = _mapping(document, f"schema {schema_id}")
            if document_obj.get("$id") != schema_id:
                raise WorkerError("closure_mismatch", f"schema id mismatch: {schema_id}")
            schema_documents[schema_id] = dict(document_obj)
        entrypoint = _text(manifest_obj.get("entrypoint"), "entrypoint")
        if entrypoint.count(":") != 1:
            raise WorkerError("closure_mismatch", "entrypoint must be path:symbol")
        entry_path, symbol_name = entrypoint.split(":", 1)
        module_candidates = [record["module"] for record in import_members if record["path"] == entry_path]
        if len(module_candidates) != 1:
            raise WorkerError("closure_mismatch", "entrypoint path has no unique import binding")
        self._finder = _CapturedFinder(members)
        sys.meta_path.insert(0, self._finder)
        return _CapturedPackage(destination, dict(manifest_obj), schema_documents, module_candidates[0], _text(symbol_name, "entrypoint symbol"))

    def close(self) -> None:
        if self._finder is not None:
            try:
                sys.meta_path.remove(self._finder)
            except ValueError:
                pass
            self._finder = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def _safe_member(path: str) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/"):
        return False
    parsed = PurePosixPath(path)
    return ".." not in parsed.parts and str(parsed) == path and "\\" not in path


def _member_records(manifest: Mapping[str, object], field: str) -> tuple[dict[str, object], ...]:
    values = manifest.get(field)
    if not isinstance(values, list):
        raise WorkerError("closure_mismatch", f"manifest {field} must be an array")
    records: list[dict[str, object]] = []
    for value in values:
        record = _mapping(value, f"manifest {field} member")
        required = {"path", "sha256", "size_bytes"} | ({"module"} if field == "import_members" else set())
        _exact(record, required, f"manifest {field} member")
        path = _text(record["path"], f"{field}.path")
        digest = _text(record["sha256"], f"{field}.sha256")
        if not _DIGEST_RE.fullmatch(digest):
            raise WorkerError("closure_mismatch", f"invalid member digest: {path}")
        records.append({**record, "path": path, "sha256": digest, "size_bytes": _integer(record["size_bytes"], f"{field}.size_bytes")})
    return tuple(records)


class _WorkerIO:
    def __init__(self, reader, writer) -> None:
        self.reader = reader
        self.writer = writer
        self.max_message_bytes = MAX_FRAME_BYTES
        self.max_checkpoint_bytes = MAX_CHECKPOINT_BYTES
        self._out_sequence = 0
        self._in_sequence = 0
        self._service_sequence = 0
        self._expected: RequestKey | None = None

    def new_service_id(self) -> str:
        request_id = f"svc-{self._service_sequence}"
        self._service_sequence += 1
        return request_id

    def set_identity(self, key: RequestKey) -> None:
        self._expected = key

    def validate_identity(self, message: WireMessage) -> None:
        expected = self._expected
        if expected is None:
            return
        key = message.header.key
        if (
            key.worker_session_id != expected.worker_session_id
            or key.generation_id != expected.generation_id
            or key.instance_id != expected.instance_id
            or key.work_id != expected.work_id
            or key.attempt_id != expected.attempt_id
            or key.authority_epoch != expected.authority_epoch
        ):
            raise WorkerError("stale_reply", "wire identity does not match admitted worker")

    def next_message(self) -> WireMessage:
        message = read_message(self.reader, max_bytes=self.max_message_bytes)
        if message is None:
            raise FrameEOF("host closed worker channel")
        if message.header.kind == "checkpoint_chunk":
            message = self._checkpoint_transfer(message)
        self.validate_identity(message)
        if message.header.sequence != self._in_sequence:
            raise WorkerError("stale_reply", "wire sequence is not the next owner sequence")
        self._in_sequence += 1
        return message

    def _checkpoint_transfer(self, first: WireMessage) -> WireMessage:
        payload = bytearray()
        metadata: Mapping[str, object] | None = None
        message = first
        next_index = 0
        while True:
            self.validate_identity(message)
            if (
                message.header.kind != "checkpoint_chunk"
                or message.header.key != first.header.key
                or message.header.sequence != self._in_sequence
            ):
                raise WorkerError(
                    "stale_reply",
                    "checkpoint transfer escaped its owner scope or sequence",
                )
            body = message.body
            phase = body.get("phase")
            if phase not in {"resume", "source"}:
                raise WorkerError(
                    "malformed_frame",
                    "checkpoint transfer phase is invalid",
                )
            context_name = "start" if phase == "resume" else "prepare"
            fields = {
                "phase",
                "chunk_index",
                "chunk_count",
                "total_bytes",
                "body_sha256",
                context_name,
                "source_generation_id",
                "source_module_id",
                "source_instance_id",
                "source_work_id",
                "source_attempt_id",
                "schema_id",
                "body",
            }
            _exact(body, fields, "checkpoint transfer")
            context = _mapping(
                body[context_name],
                f"checkpoint transfer {context_name}",
            )
            if phase == "resume":
                _exact(
                    context,
                    {
                        "package_path",
                        "package_digest",
                        "module_id",
                        "instance_id",
                        "generation_id",
                        "instance_label",
                        "input_schemas",
                        "output_schemas",
                        "checkpoint_schemas",
                        "dependencies",
                        "child_targets",
                        "initial_input",
                        "next_input_sequence",
                        "max_message_bytes",
                        "max_checkpoint_bytes",
                    },
                    "checkpoint transfer start",
                )
                frame_maximum = _integer(
                    context["max_message_bytes"],
                    "max_message_bytes",
                    minimum=1,
                )
                checkpoint_maximum = _integer(
                    context["max_checkpoint_bytes"],
                    "max_checkpoint_bytes",
                    minimum=1,
                )
                if (
                    frame_maximum > MAX_FRAME_BYTES
                    or checkpoint_maximum > MAX_CHECKPOINT_BYTES
                ):
                    raise WorkerError(
                        "malformed_frame",
                        "checkpoint transfer exceeds protocol limits",
                    )
            else:
                _exact(
                    context,
                    {
                        "source_dependencies",
                        "target_dependencies",
                        "declared_at_sequence",
                    },
                    "checkpoint transfer prepare",
                )
                frame_maximum = self.max_message_bytes
                checkpoint_maximum = self.max_checkpoint_bytes
            index = _integer(body["chunk_index"], "checkpoint chunk index")
            count = _integer(
                body["chunk_count"],
                "checkpoint chunk count",
                minimum=1,
            )
            total = _integer(body["total_bytes"], "checkpoint total bytes")
            digest = body["body_sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in digest
                )
            ):
                raise WorkerError(
                    "malformed_frame",
                    "checkpoint transfer digest is invalid",
                )
            if (
                index != next_index
                or total > checkpoint_maximum
                or (total == 0 and count != 1)
                or (total > 0 and count > total)
            ):
                raise WorkerError(
                    "malformed_frame",
                    "checkpoint transfer has an invalid bound or sequence",
                )
            current_metadata = {
                name: value
                for name, value in body.items()
                if name not in {"body", "chunk_index"}
            }
            if metadata is not None and metadata != current_metadata:
                raise WorkerError(
                    "malformed_frame",
                    "checkpoint transfer metadata changed between chunks",
                )
            metadata = current_metadata
            chunk = decode_bytes(body["body"], maximum=checkpoint_maximum)
            if total > 0 and not chunk:
                raise WorkerError(
                    "malformed_frame",
                    "non-empty checkpoint transfer has an empty chunk",
                )
            payload.extend(chunk)
            next_index += 1
            if len(payload) > total:
                raise WorkerError(
                    "malformed_frame",
                    "checkpoint chunks exceed their declared length",
                )
            if next_index < count:
                if len(payload) == total:
                    raise WorkerError(
                        "malformed_frame",
                        "checkpoint transfer reached its length before its final chunk",
                    )
                message = read_message(self.reader, max_bytes=frame_maximum)
                if message is None:
                    raise FrameEOF("host closed during checkpoint transfer")
                continue
            if next_index != count or len(payload) != total:
                raise WorkerError(
                    "malformed_frame",
                    "checkpoint transfer is incomplete",
                )
            break
        assert metadata is not None
        checkpoint_body = bytes(payload)
        if hashlib.sha256(checkpoint_body).hexdigest() != metadata["body_sha256"]:
            raise WorkerError(
                "malformed_frame",
                "checkpoint transfer digest does not match its body",
            )
        checkpoint = {
            name: metadata[name]
            for name in (
                "source_generation_id",
                "source_module_id",
                "source_instance_id",
                "source_work_id",
                "source_attempt_id",
                "schema_id",
            )
        }
        checkpoint["body"] = encode_bytes(
            checkpoint_body,
            maximum=checkpoint_maximum,
        )
        logical_body = dict(metadata[context_name])
        logical_body["resume" if phase == "resume" else "source"] = checkpoint
        kind: WireKind = "start" if phase == "resume" else "checkpoint_prepare"
        return WireMessage(
            WireHeader(
                PROTOCOL_VERSION,
                kind,
                first.header.key,
                first.header.sequence,
            ),
            logical_body,
        )

    def emit(self, kind: WireKind, key: RequestKey, body: Mapping[str, object]) -> None:
        header = WireHeader(PROTOCOL_VERSION, kind, key, self._out_sequence)
        write_message(
            self.writer,
            WireMessage(header, dict(body)),
            max_bytes=self.max_message_bytes,
        )
        self._out_sequence += 1

    def emit_chunks(
        self,
        kind: Literal["checkpoint"],
        key: RequestKey,
        metadata: Mapping[str, object],
        payload: bytes,
        *,
        maximum: int,
    ) -> None:
        header = WireHeader(PROTOCOL_VERSION, kind, key, self._out_sequence)
        for message in iter_chunked_messages(
            header,
            kind,
            metadata,
            payload,
            max_bytes=self.max_message_bytes,
            maximum=maximum,
        ):
            write_message(
                self.writer,
                message,
                max_bytes=self.max_message_bytes,
            )
            self._out_sequence += 1

    def wait_service(self, request_id: str) -> Mapping[str, object]:
        while True:
            message = self.next_message()
            if message.header.kind in {"cancel", "close"}:
                raise WorkerError("revoked", "worker service request cancelled")
            if message.header.kind == "checkpoint_request":
                body = message.body
                _exact(body, {"request_id", "reason", "requested_at_sequence"}, "checkpoint_request")
                request = CheckpointRequest(body["request_id"], body["reason"], body["requested_at_sequence"])
                self.emit("result", message.header.key, {
                    "status": "checkpoint_refused",
                    "request_id": request.request_id,
                    "code": "pending_effect",
                    "detail": "an owner operation has not settled",
                    "retryable": True,
                })
                continue
            if message.header.kind != "service_result":
                raise WorkerError("worker_protocol_mismatch", "unexpected message during owner operation")
            if message.header.key.request_id != request_id:
                raise WorkerError("stale_reply", "service result does not match pending request")
            return message.body


class _PortBase:
    def __init__(self, io: _WorkerIO, identity: RequestKey, opening: Callable[[], bool]) -> None:
        self._io = io
        self._identity = identity
        self._opening = opening

    def _request(self, kind: str, body: Mapping[str, object]) -> Mapping[str, object]:
        if self._opening():
            raise WorkerError("authority_denied", "constructor may not issue effects")
        request_id = self._io.new_service_id()
        key = RequestKey(
            worker_session_id=self._identity.worker_session_id,
            request_id=request_id,
            generation_id=self._identity.generation_id,
            instance_id=self._identity.instance_id,
            work_id=self._identity.work_id,
            attempt_id=self._identity.attempt_id,
            authority_epoch=self._identity.authority_epoch,
        )
        self._io.emit(kind, key, {"request_id": request_id, **dict(body)})
        response = self._io.wait_service(request_id)
        if response.get("request_id") != request_id:
            raise WorkerError("stale_reply", "service response request identity mismatch")
        status = response.get("status")
        if status == "cancelled":
            raise WorkerError("revoked", "service request cancelled")
        if status != "ok":
            raise WorkerError(
                _text(response.get("code"), "service result code"),
                _text(response.get("detail"), "service result detail"),
            )
        return response


class _DependencyProxy(_PortBase):
    def __init__(self, io: _WorkerIO, identity: RequestKey, opening: Callable[[], bool], name: str, contract_id: str) -> None:
        super().__init__(io, identity, opening)
        self._name = name
        self._contract_id = contract_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def contract_id(self) -> str:
        return self._contract_id

    def exchange(self, value: ModuleInput) -> OutputEnvelope:
        response = self._request(
            "dependency_request",
            {"dependency": self._name, "contract_id": self._contract_id, "input": value.to_dict()},
        )
        return OutputEnvelope(
            schema_id=_text(response.get("schema_id"), "dependency output schema_id"),
            body=decode_bytes(response.get("body")),
        )


class _ProviderProxy(_PortBase):
    def start(self, request):
        from .provider import ProviderExchangeHandle, ProviderRoute, CanonicalProviderExchangeCodec
        response = self._request("provider_request", {"operation": "start", "call": encode_bytes(CanonicalProviderExchangeCodec.encode_call_request(request))})
        route = ProviderRoute.from_dict(response.get("route"))
        return ProviderExchangeHandle(_text(response.get("exchange_id"), "exchange_id"), _text(response.get("stream_id"), "stream_id"), route)

    def next_event(self, handle):
        from .provider import CanonicalProviderExchangeCodec, ProviderCancelled, ProviderDone, ProviderErrorTerminal, ProviderEvent, ProviderUnknown
        response = self._request("provider_request", {"operation": "next_event", "exchange_id": handle.exchange_id, "stream_id": handle.stream_id})
        kind = _text(response.get("item_kind"), "provider item_kind")
        if kind == "event":
            raw = _mapping(response.get("event"), "provider event")
            return ProviderEvent(**dict(raw))
        if kind == "done":
            raw = _mapping(response.get("terminal"), "provider terminal")
            return ProviderDone(_wire_strict=True, **dict(raw))
        if kind == "error":
            raw = _mapping(response.get("terminal"), "provider terminal")
            return ProviderErrorTerminal(**dict(raw))
        if kind == "cancelled":
            raw = _mapping(response.get("terminal"), "provider terminal")
            return ProviderCancelled(**dict(raw))
        if kind == "unknown":
            return ProviderUnknown(**dict(_mapping(response.get("unknown"), "provider unknown")))
        raise WorkerError("provider_failed", "unknown provider stream item")

    def cancel(self, handle, reason: str):
        from .provider import ProviderCancelled, ProviderUnknown
        response = self._request("provider_request", {"operation": "cancel", "exchange_id": handle.exchange_id, "stream_id": handle.stream_id, "reason": _text(reason, "cancel reason")})
        kind = _text(response.get("item_kind"), "provider cancel item_kind")
        if kind == "cancelled":
            return ProviderCancelled(**dict(_mapping(response.get("terminal"), "provider terminal")))
        if kind == "unknown":
            return ProviderUnknown(**dict(_mapping(response.get("unknown"), "provider unknown")))
        raise WorkerError("provider_failed", "provider cancel returned invalid outcome")


class _ToolProxy(_PortBase):
    def request_approval(self, request: ToolApprovalRequest) -> ToolApproval:
        response = self._request("tool_request", {"operation": "request_approval", "request": _tool_request_dict(request)})
        return ToolApproval(**dict(_mapping(response.get("approval"), "tool approval")))

    def execute(self, request: ToolApprovalRequest, approval: ToolApproval):
        response = self._request("tool_request", {"operation": "execute", "request": _tool_request_dict(request), "approval": _tool_approval_dict(approval)})
        outcome = _mapping(response.get("outcome"), "tool outcome")
        kind = _text(outcome.get("kind"), "tool outcome kind")
        if kind == "succeeded":
            return ToolSucceeded(request_id=outcome["request_id"], output_schema_id=_text(outcome.get("output_schema_id"), "tool output schema"), output=decode_bytes(outcome.get("output")))
        if kind == "failed":
            return ToolFailed(request_id=outcome["request_id"], code=_text(outcome.get("code"), "tool failure code"), detail=_text(outcome.get("detail"), "tool failure detail"))
        if kind == "cancelled":
            return ToolCancelled(request_id=outcome["request_id"], owner=outcome["owner"], reason=_text(outcome.get("reason"), "tool cancel reason"))
        if kind == "unknown":
            return ToolUnknown(request_id=outcome["request_id"], reason=_text(outcome.get("reason"), "tool unknown reason"), evidence_refs=tuple(outcome.get("evidence_refs", ())))
        raise WorkerError("tool_failed", "unknown tool outcome")


class _ContextProxy(_PortBase):
    def snapshot(self) -> ContextSnapshot:
        response = self._request("context_request", {"operation": "snapshot"})
        return _context_snapshot(response.get("snapshot"))

    def propose(self, decision: TurnPolicyDecision) -> TurnPolicyReceipt:
        response = self._request("context_request", {"operation": "propose", "decision": _as_json_dataclass(decision)})
        receipt = _mapping(response.get("receipt"), "context receipt")
        return TurnPolicyReceipt(**dict(receipt))


class _ChildProxy(_PortBase):
    def __init__(self, io: _WorkerIO, identity: RequestKey, opening: Callable[[], bool], targets: tuple[ChildTarget, ...]) -> None:
        super().__init__(io, identity, opening)
        self._targets = targets

    @property
    def targets(self) -> tuple[ChildTarget, ...]:
        return self._targets

    def start(self, plan: ChildPlan) -> ChildHandle:
        target = next((item for item in self._targets if item.label == plan.target.label), None)
        if target != plan.target:
            raise WorkerError("child_denied", "child target is not a pinned edge")
        response = self._request("child_request", {"operation": "start", "target": _as_json_dataclass(plan.target), "initial_input": plan.initial_input.to_dict()})
        return ChildHandle(**dict(_mapping(response.get("handle"), "child handle")))

    def submit_input(self, handle: ChildHandle, chunk: ModuleInput) -> None:
        self._request("child_request", {"operation": "submit_input", "handle": _as_json_dataclass(handle), "input": chunk.to_dict()})

    def next_output(self, handle: ChildHandle):
        response = self._request("child_request", {"operation": "next_output", "handle": _as_json_dataclass(handle)})
        return _child_item(response)

    def join(self, handles: tuple[ChildHandle, ...]):
        response = self._request("child_request", {"operation": "join", "handles": [_as_json_dataclass(handle) for handle in handles]})
        outcomes = response.get("outcomes")
        if not isinstance(outcomes, list):
            raise WorkerError("child_failed", "child join outcomes must be an array")
        return tuple(_child_outcome(item) for item in outcomes)


def _as_json_dataclass(value: object) -> dict[str, object]:
    if hasattr(value, "__dataclass_fields__"):
        result: dict[str, object] = {}
        for name in value.__dataclass_fields__:  # type: ignore[attr-defined]
            item = getattr(value, name)
            if isinstance(item, bytes):
                result[name] = encode_bytes(item, maximum=MAX_CHECKPOINT_BYTES)
            elif isinstance(item, tuple):
                result[name] = [_as_json_dataclass(child) if hasattr(child, "__dataclass_fields__") else child for child in item]
            elif hasattr(item, "__dataclass_fields__"):
                result[name] = _as_json_dataclass(item)
            else:
                result[name] = item
        return result
    raise TypeError("expected dataclass")


def _input_dict(value: InputEnvelope) -> dict[str, object]:
    return {"schema_id": value.schema_id, "sequence": value.sequence, "body": encode_bytes(value.body), "final": value.final}


def _tool_request_dict(value: ToolApprovalRequest) -> dict[str, object]:
    return _as_json_dataclass(value)


def _tool_approval_dict(value: ToolApproval) -> dict[str, object]:
    return _as_json_dataclass(value)


def _context_snapshot(value: object) -> ContextSnapshot:
    raw = _mapping(value, "context snapshot")
    effective = _mapping(raw.get("effective_context"), "effective context")
    source = _mapping(raw.get("source"), "context source")
    document = EffectiveContextDocument(encoding=effective["encoding"], body=decode_bytes(effective["body"]), context_sha256=_text(effective.get("context_sha256"), "context hash"))
    provenance = ContextSourceProvenance(**dict(source))
    return ContextSnapshot(session_id=raw["session_id"], context_id=raw["context_id"], session_event_sequence=raw["session_event_sequence"], effective_context=document, raw_fact_ids=tuple(raw["raw_fact_ids"]), shadowed_raw_fact_ids=tuple(raw["shadowed_raw_fact_ids"]), source=provenance, compaction_index=raw["compaction_index"], turn_index=raw.get("turn_index"))


def _child_item(value: Mapping[str, object]):
    kind = _text(value.get("item_kind"), "child item_kind")
    if kind == "output":
        output = _mapping(value.get("output"), "child output")
        return ChildOutput(child_work_id=output["child_work_id"], child_attempt_id=output["child_attempt_id"], sequence=output["sequence"], output=OutputEnvelope(schema_id=_text(output.get("schema_id"), "child output schema"), body=decode_bytes(output.get("body"))))
    return _child_outcome(value.get("outcome"))


def _child_outcome(value: object):
    raw = _mapping(value, "child outcome")
    kind = _text(raw.get("kind"), "child outcome kind")
    if kind == "succeeded":
        output = _mapping(raw.get("output"), "child output")
        return ChildSucceeded(child_work_id=raw["child_work_id"], child_attempt_id=raw["child_attempt_id"], output=OutputEnvelope(schema_id=_text(output.get("schema_id"), "child output schema"), body=decode_bytes(output.get("body"))))
    if kind == "failed":
        return ChildFailed(child_work_id=raw["child_work_id"], child_attempt_id=raw["child_attempt_id"], code=_text(raw.get("code"), "child failure code"), detail=_text(raw.get("detail"), "child failure detail"))
    if kind == "unknown":
        return ChildUnknown(child_work_id=raw["child_work_id"], child_attempt_id=raw["child_attempt_id"], reason=_text(raw.get("reason"), "child unknown reason"))
    raise WorkerError("child_failed", "unknown child outcome")


class _Worker:
    def __init__(self, reader, writer) -> None:
        self.io = _WorkerIO(reader, writer)
        self.loader: _PackageLoader | None = None
        self.package: _CapturedPackage | None = None
        self.module = None
        self.instance = None
        self.identity: InstanceIdentity | None = None
        self.key: RequestKey | None = None
        self.input_sequence = 0
        self.opening = True
        self.output_emitted = False
        self.latest_state = None
        self.dependencies = None
        self.providers = None
        self.tools = None
        self.context = None
        self.children = None
        self.resume_state = None
        self.input_schemas: frozenset[str] = frozenset()
        self.output_schemas: frozenset[str] = frozenset()
        self.checkpoint_schemas: frozenset[str] = frozenset()

    def run(self) -> int:
        try:
            start = self.io.next_message()
            if start.header.kind != "start":
                raise WorkerError("worker_protocol_mismatch", "first worker message must be start")
            self._start(start)
            while True:
                message = self.io.next_message()
                self.key = message.header.key
                if message.header.kind == "input":
                    self._input(message)
                elif message.header.kind == "checkpoint_request":
                    self._checkpoint(message)
                elif message.header.kind == "checkpoint_prepare":
                    self._prepare_checkpoint(message)
                elif message.header.kind == "cancel":
                    self.io.emit("result", message.header.key, {"status": "cancelled", "reason": _text(message.body.get("reason"), "cancel reason")})
                    return 0
                elif message.header.kind == "close":
                    return 0
                else:
                    raise WorkerError("worker_protocol_mismatch", f"unexpected worker message: {message.header.kind}")
        except FrameEOF:
            return 0
        except BaseException as exc:
            self._failure(exc)
            return 1
        finally:
            if self.loader is not None:
                self.loader.close()

    def _start(self, message: WireMessage) -> None:
        body = message.body
        required = {
            "package_path", "package_digest", "module_id", "instance_id",
            "generation_id", "instance_label", "input_schemas", "output_schemas",
            "checkpoint_schemas", "dependencies", "child_targets", "initial_input",
            "resume", "next_input_sequence", "max_message_bytes",
            "max_checkpoint_bytes",
        }
        _exact(body, required, "start")
        self.key = message.header.key
        self.io.set_identity(self.key)
        self.io.max_message_bytes = _integer(
            body["max_message_bytes"],
            "max_message_bytes",
            minimum=MIN_FRAME_BYTES,
        )
        self.io.max_checkpoint_bytes = _integer(
            body["max_checkpoint_bytes"],
            "max_checkpoint_bytes",
            minimum=1,
        )
        if (
            self.io.max_message_bytes > MAX_FRAME_BYTES
            or self.io.max_checkpoint_bytes > MAX_CHECKPOINT_BYTES
        ):
            raise WorkerError(
                "malformed_frame",
                "start transport limits exceed protocol bounds",
            )
        self.input_sequence = _integer(
            body["next_input_sequence"], "next_input_sequence"
        )
        digest = _text(body["package_digest"], "package_digest")
        self.loader = _PackageLoader(Path(_text(body["package_path"], "package_path")), digest)
        self.package = self.loader.load()
        manifest = self.package.manifest
        module_id = _text(body["module_id"], "module_id")
        if _text(manifest.get("logical_package"), "manifest logical_package") != module_id:
            raise WorkerError("closure_mismatch", "module identity differs from package manifest")
        self.input_schemas = frozenset(
            _text(item, "input schema")
            for item in _string_list(body["input_schemas"], "input_schemas")
        )
        self.output_schemas = frozenset(
            _text(item, "output schema")
            for item in _string_list(body["output_schemas"], "output_schemas")
        )
        self.checkpoint_schemas = frozenset(
            _text(item, "checkpoint schema")
            for item in _string_list(body["checkpoint_schemas"], "checkpoint_schemas")
        )
        declared_input = frozenset(
            _text(item, "manifest input schema")
            for item in _string_list(manifest.get("input_schema_ids"), "manifest input_schema_ids")
        )
        declared_output = frozenset(
            _text(item, "manifest output schema")
            for item in _string_list(manifest.get("output_schema_ids"), "manifest output_schema_ids")
        )
        if not self.input_schemas <= declared_input or not self.output_schemas <= declared_output:
            raise WorkerError("closure_mismatch", "admitted schema set differs from captured manifest")
        initial = None if body["initial_input"] is None else _input_from_wire(body["initial_input"])
        if initial is not None:
            self._validate_input(initial)
        self.identity = InstanceIdentity(
            instance_id=_text(body["instance_id"], "instance_id"),
            module_id=module_id,
            generation_id=_text(body["generation_id"], "generation_id"),
            instance_label=_text(body["instance_label"], "instance_label"),
        )
        self.dependencies, self.providers, self.tools, self.context, self.children = self._ports(
            body["dependencies"], body["child_targets"]
        )
        if self.package.module_name in sys.modules:
            raise WorkerError(
                "closure_mismatch",
                f"declared import member is already loaded in worker: {self.package.module_name}",
            )
        module_file = importlib.import_module(self.package.module_name)
        entry = getattr(module_file, self.package.symbol_name, None)
        if entry is None:
            raise WorkerError("worker_start_failed", "captured entrypoint symbol is missing")
        required_methods = (
            "bind_dependencies", "decode_input", "decode_output",
            "decode_checkpoint", "encode_output", "encode_checkpoint",
            "assess_checkpoint", "open_instance",
        )
        if isinstance(entry, type) or not all(callable(getattr(entry, name, None)) for name in required_methods):
            if not callable(entry):
                raise WorkerError("worker_start_failed", "entrypoint is not a PolicyModule or factory")
            entry = entry()
        if not all(callable(getattr(entry, name, None)) for name in required_methods):
            raise WorkerError("worker_start_failed", "entrypoint does not implement PolicyModule")
        self.module = entry
        self.dependencies = self.module.bind_dependencies(self.dependencies)
        if body["resume"] is not None:
            resume_envelope = _checkpoint_from_wire(body["resume"])
            if len(resume_envelope.body) > self.io.max_checkpoint_bytes:
                raise WorkerError(
                    "serialization_limit",
                    "resume checkpoint exceeds maximum size",
                )
            if resume_envelope.schema_id not in self.checkpoint_schemas:
                raise WorkerError("checkpoint_incompatible", "resume schema is not admitted")
            self.resume_state = self.module.decode_checkpoint(resume_envelope)
        if initial is not None:
            self._open_instance(initial)
        self.io.emit(
            "ready",
            self.key,
            {
                "status": "ready",
                "instance_open": self.instance is not None,
                "module_id": self.identity.module_id,
                "instance_id": self.identity.instance_id,
            },
        )

    def _open_instance(self, initial: InputEnvelope):
        if self.instance is not None:
            return self.module.decode_input(initial)
        if self.module is None or self.identity is None:
            raise WorkerError("worker_protocol_mismatch", "module is not loaded")
        try:
            decoded = self.module.decode_input(initial)
            self.instance = self.module.open_instance(
                identity=self.identity,
                initial_input=decoded,
                dependencies=self.dependencies,
                children=self.children,
                context=self.context,
                providers=self.providers,
                tools=self.tools,
                resume=self.resume_state,
            )
        except WorkerError:
            raise
        except BaseException as exc:
            raise WorkerError(
                "worker_start_failed",
                f"author instance construction failed: {type(exc).__name__}: {exc}",
            ) from exc
        self.opening = False
        return decoded

    def _ports(self, dependencies_value: object, targets_value: object):
        raw_dependencies = dependencies_value
        if not isinstance(raw_dependencies, list):
            raise WorkerError("dependency_mismatch", "dependencies must be an array")
        declarations: list[DependencyDeclaration] = []
        ports: dict[str, DependencyAccess] = {}
        for value in raw_dependencies:
            record = _mapping(value, "dependency binding")
            _exact(record, {"name", "contract_id"}, "dependency binding")
            name = _text(record["name"], "dependency name")
            contract_id = _text(record["contract_id"], "dependency contract_id")
            declarations.append(DependencyDeclaration(name, contract_id))
            ports[name] = _DependencyProxy(self.io, self.key, lambda: self.opening, name, contract_id)
        targets: list[ChildTarget] = []
        if not isinstance(targets_value, list):
            raise WorkerError("child_denied", "child_targets must be an array")
        for value in targets_value:
            record = _mapping(value, "child target")
            label = _text(record.get("label"), "child label")
            target = _text(record.get("target"), "child target")
            contract_id = _text(record.get("contract_id"), "child contract_id")
            targets.append(ChildTarget(
                label, target, contract_id,
                _string_list(record.get("input_schema_ids"), "child input schemas"),
                _string_list(record.get("output_schema_ids"), "child output schemas"),
            ))
        children = _ChildProxy(self.io, self.key, lambda: self.opening, tuple(targets))
        providers = _ProviderProxy(self.io, self.key, lambda: self.opening)
        tools = _ToolProxy(self.io, self.key, lambda: self.opening)
        context = _ContextProxy(self.io, self.key, lambda: self.opening)
        dependencies = DependencyBindings(tuple(declarations), ports)
        return dependencies, providers, tools, context, children

    def _input(self, message: WireMessage) -> None:
        if self.module is None:
            raise WorkerError("worker_protocol_mismatch", "input arrived before module load")
        envelope = _input_from_wire(message.body)
        self._validate_input(envelope)
        if envelope.sequence != self.input_sequence:
            raise WorkerError("stale_reply", "input sequence is not the next owner sequence")
        self.input_sequence += 1
        self.opening = True
        try:
            value = (
                self._open_instance(envelope)
                if self.instance is None
                else self.module.decode_input(envelope)
            )
            self.opening = False
            try:
                result = self.instance.step(value)
            finally:
                self.opening = True
            if isinstance(result, OutputResult):
                output = self.module.encode_output(result.output)
                self._validate_output(output)
                self.io.emit("output", message.header.key, {"schema_id": output.schema_id, "body": encode_bytes(output.body)})
                self.output_emitted = True
                self._send_proposal(message.header.key, result.checkpoint)
                self.latest_state = result.state
                self.io.emit("result", message.header.key, {"status": "output", "output_emitted": True})
            elif isinstance(result, ContinueResult):
                self._send_proposal(message.header.key, result.checkpoint)
                self.latest_state = result.state
                self.io.emit("result", message.header.key, {"status": "continue", "output_emitted": False})
            elif isinstance(result, FailureResult):
                failure: PolicyFailure = result.failure
                self._send_proposal(message.header.key, result.checkpoint)
                self.latest_state = result.state
                self.io.emit("failure", message.header.key, {"code": failure.code, "detail": failure.detail, "retryable": failure.retryable, "output_emitted": self.output_emitted})
            else:
                raise WorkerError("worker_failed", "step returned an unsupported result")
        except WorkerError:
            raise
        except BaseException as exc:
            raise WorkerError("worker_failed", f"author step failed: {type(exc).__name__}: {exc}") from exc

    def _checkpoint(self, message: WireMessage) -> None:
        if self.instance is None or self.module is None:
            raise WorkerError("worker_protocol_mismatch", "checkpoint arrived before ready")
        body = message.body
        _exact(body, {"request_id", "reason", "requested_at_sequence"}, "checkpoint_request")
        request = CheckpointRequest(body["request_id"], body["reason"], body["requested_at_sequence"])
        try:
            capture: CheckpointCapture = self.instance.checkpoint(request)
            if capture.refusal is not None:
                refusal = capture.refusal
                self.io.emit("result", message.header.key, {"status": "checkpoint_refused", "request_id": request.request_id, "code": refusal.code, "detail": refusal.detail, "retryable": refusal.retryable, "observed_sequence": capture.observed_sequence})
                return
            if capture.state is None:
                raise WorkerError("checkpoint_invalid", "checkpoint capture omitted state")
            proposal = self.module.encode_checkpoint(capture.state, source_generation_id=self.identity.generation_id, source_module_id=self.identity.module_id, source_instance_id=self.identity.instance_id, source_work_id=message.header.key.work_id, source_attempt_id=message.header.key.attempt_id, declared_at_sequence=capture.observed_sequence)
            self._send_proposal(message.header.key, proposal)
            self.io.emit("result", message.header.key, {"status": "checkpoint", "request_id": request.request_id, "observed_sequence": capture.observed_sequence})
        except WorkerError:
            raise
        except BaseException as exc:
            raise WorkerError("checkpoint_invalid", f"checkpoint failed: {type(exc).__name__}: {exc}") from exc

    def _prepare_checkpoint(self, message: WireMessage) -> None:
        if self.module is None or self.identity is None or self.instance is not None:
            raise WorkerError(
                "worker_protocol_mismatch",
                "checkpoint preparation requires a ready unopened target worker",
            )
        body = message.body
        _exact(
            body,
            {"source", "source_dependencies", "target_dependencies", "declared_at_sequence"},
            "checkpoint_prepare",
        )
        source = _checkpoint_from_wire(body["source"])
        if len(source.body) > self.io.max_checkpoint_bytes:
            raise WorkerError(
                "serialization_limit",
                "source checkpoint exceeds maximum size",
            )
        declared_at_sequence = _integer(
            body["declared_at_sequence"],
            "declared_at_sequence",
        )
        source_dependencies = tuple(
            _text(item, "source dependency")
            for item in _string_list(body["source_dependencies"], "source_dependencies")
        )
        target_dependencies = tuple(
            _text(item, "target dependency")
            for item in _string_list(body["target_dependencies"], "target_dependencies")
        )
        context = CheckpointCompatibilityContext(
            source_generation_id=source.source_generation_id,
            source_schema_id=source.schema_id,
            source_state_digest="sha256:" + hashlib.sha256(source.body).hexdigest(),
            source_dependencies=source_dependencies,
            target_dependencies=target_dependencies,
        )
        try:
            compatibility = self.module.assess_checkpoint(context)
            if not isinstance(compatibility, CheckpointCompatibility):
                raise WorkerError(
                    "checkpoint_invalid",
                    "assess_checkpoint returned an unsupported result",
                )
            if compatibility.disposition == "incompatible":
                self.io.emit(
                    "checkpoint_compatibility",
                    message.header.key,
                    {
                        "disposition": "incompatible",
                        "target_schema_id": compatibility.target_schema_id,
                        "reason": compatibility.reason,
                    },
                )
                return
            state = self.module.decode_checkpoint(source)
            if compatibility.disposition == "compatible":
                if compatibility.target_schema_id != source.schema_id:
                    raise WorkerError(
                        "checkpoint_invalid",
                        "compatible checkpoint changed its schema identity",
                    )
                proposal = CheckpointProposal(source, declared_at_sequence)
            elif compatibility.disposition == "migrate":
                proposal = self.module.encode_checkpoint(
                    state,
                    source_generation_id=self.identity.generation_id,
                    source_module_id=self.identity.module_id,
                    source_instance_id=self.identity.instance_id,
                    source_work_id=message.header.key.work_id,
                    source_attempt_id=message.header.key.attempt_id,
                    declared_at_sequence=declared_at_sequence,
                )
                if proposal.payload.schema_id != compatibility.target_schema_id:
                    raise WorkerError(
                        "checkpoint_invalid",
                        "migrated checkpoint schema differs from compatibility decision",
                    )
            else:
                raise WorkerError(
                    "checkpoint_invalid",
                    "checkpoint compatibility disposition is invalid",
                )
            self._send_proposal(message.header.key, proposal)
            self.io.emit(
                "checkpoint_compatibility",
                message.header.key,
                {
                    "disposition": compatibility.disposition,
                    "target_schema_id": compatibility.target_schema_id,
                    "reason": compatibility.reason,
                },
            )
        except WorkerError:
            raise
        except BaseException as exc:
            raise WorkerError(
                "checkpoint_invalid",
                f"checkpoint preparation failed: {type(exc).__name__}: {exc}",
            ) from exc

    def _send_proposal(self, key: RequestKey, proposal: CheckpointProposal | None) -> None:
        if proposal is None:
            return
        envelope = proposal.payload
        if len(envelope.body) > self.io.max_checkpoint_bytes:
            raise WorkerError("serialization_limit", "checkpoint exceeds maximum size")
        self.io.emit_chunks(
            "checkpoint",
            key,
            {
                "phase": "proposal",
                "schema_id": envelope.schema_id,
                "source_generation_id": envelope.source_generation_id,
                "source_module_id": envelope.source_module_id,
                "source_instance_id": envelope.source_instance_id,
                "source_work_id": envelope.source_work_id,
                "source_attempt_id": envelope.source_attempt_id,
                "declared_at_sequence": proposal.declared_at_sequence,
            },
            envelope.body,
            maximum=self.io.max_checkpoint_bytes,
        )

    def _validate_input(self, envelope: InputEnvelope) -> None:
        if envelope.schema_id not in self.input_schemas:
            raise WorkerError("closure_mismatch", f"input schema is not admitted: {envelope.schema_id}")
        if len(envelope.body) > self.io.max_message_bytes:
            raise WorkerError("serialization_limit", "input exceeds maximum size")

    def _validate_output(self, envelope: OutputEnvelope) -> None:
        if envelope.schema_id not in self.output_schemas:
            raise WorkerError("closure_mismatch", f"output schema is not admitted: {envelope.schema_id}")
        if len(envelope.body) > self.io.max_message_bytes:
            raise WorkerError("serialization_limit", "output exceeds maximum size")

    def _failure(self, exc: BaseException) -> None:
        if self.key is None:
            return
        if isinstance(exc, WorkerError):
            code, detail, retryable = exc.code, exc.detail, exc.retryable
        elif isinstance(exc, FrameLimitError):
            code, detail, retryable = "serialization_limit", str(exc), False
        else:
            code, detail, retryable = "worker_failed", f"worker failed: {type(exc).__name__}: {exc}", False
        try:
            self.io.emit("failure", self.key, {"code": code, "detail": detail, "retryable": retryable, "output_emitted": self.output_emitted})
        except BaseException:
            pass


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkerError("closure_mismatch", f"{label} must be an array")
    return tuple(_text(item, label) for item in value)


def _input_from_wire(value: object) -> InputEnvelope:
    raw = _mapping(value, "input")
    _exact(raw, {"schema_id", "sequence", "body", "final"}, "input")
    return InputEnvelope(schema_id=_text(raw["schema_id"], "input schema_id"), sequence=_integer(raw["sequence"], "input sequence"), body=decode_bytes(raw["body"]), final=raw["final"])





def _checkpoint_from_wire(value: object) -> CheckpointEnvelope:
    raw = _mapping(value, "checkpoint")
    _exact(raw, {"source_generation_id", "source_module_id", "source_instance_id", "source_work_id", "source_attempt_id", "schema_id", "body"}, "checkpoint")
    return CheckpointEnvelope(source_generation_id=raw["source_generation_id"], source_module_id=raw["source_module_id"], source_instance_id=raw["source_instance_id"], source_work_id=raw["source_work_id"], source_attempt_id=raw["source_attempt_id"], schema_id=_text(raw["schema_id"], "checkpoint schema_id"), body=decode_bytes(raw["body"], maximum=MAX_CHECKPOINT_BYTES))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in ([], ["--stdio"]):
        print("usage: python -m breadboard.modules.worker --stdio", file=sys.stderr)
        return 2
    return _Worker(sys.stdin.buffer, sys.stdout.buffer).run()


if __name__ == "__main__":
    raise SystemExit(main())
__all__ = ["main"]
