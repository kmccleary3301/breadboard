from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from breadboard.modules.author import InputEnvelope
from breadboard.modules.transport import (
    FrameLimitError,
    MAX_CHECKPOINT_BYTES,
    MAX_FRAME_BYTES,
    MessageReassembler,
    PROTOCOL_VERSION,
    RequestKey,
    WireHeader,
    WireMessage,
    WireProtocolError,
    decode_bytes,
    encode_bytes,
    iter_message_frames,
)
import breadboard_engine.execution.author_worker as author_worker
from breadboard_engine.execution.author_worker import (
    AuthorWorker,
    AuthorWorkerCleanupResult,
    AuthorWorkerResourceReceipt,
    AuthorWorkerSpec,
    _ManagementNotices,
)


def test_close_allows_helper_to_emit_authenticated_cleanup_receipt() -> None:
    receipt = AuthorWorkerResourceReceipt(
        resource_id="docker:bb-author-test",
        owner_ref="module:session:worker",
        execution_id="execution-test",
        container_id="a" * 64,
        container_name="bb-author-test",
        image_id="sha256:" + "b" * 64,
        image_ref="sha256:" + "b" * 64,
        platform="linux/arm64",
        receiver_identity="receiver-test",
        state="running",
    )
    cleanup = {
        "status": "confirmed_absent",
        "resourceId": receipt.resource_id,
        "containerId": receipt.container_id,
        "ownerRef": receipt.owner_ref,
        "reason": "owner_channel_closed",
        "evidence": ["owned_container_removed", "container_absence_observed"],
    }
    encoded = base64.b64encode(
        json.dumps(cleanup, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    code = (
        "import sys; "
        "sys.stdin.buffer.read(); "
        f"sys.stderr.write('BREADBOARD_AUTHOR_CLEANUP\\t{encoded}\\n'); "
        "sys.stderr.flush()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    notices = _ManagementNotices(process)
    worker = AuthorWorker(process, receipt, notices)

    result = worker.close("server_shutdown")

    assert result.status == "confirmed_absent"
    assert result.resource_id == receipt.resource_id
    assert result.container_id == receipt.container_id
    assert result.owner_ref == receipt.owner_ref
    assert result.evidence == (
        "owned_container_removed",
        "container_absence_observed",
    )


def test_close_uses_authenticated_fallback_when_helper_receipt_is_missing() -> None:
    receipt = AuthorWorkerResourceReceipt(
        resource_id="docker:bb-author-fallback",
        owner_ref="module:session:fallback",
        execution_id="execution-fallback",
        container_id="c" * 64,
        container_name="bb-author-fallback",
        image_id="sha256:" + "d" * 64,
        image_ref="sha256:" + "d" * 64,
        platform="linux/arm64",
        receiver_identity="receiver-fallback",
        state="running",
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    notices = _ManagementNotices(process)
    calls: list[str] = []

    def fallback(reason: str, _deadline: float) -> AuthorWorkerCleanupResult:
        calls.append(reason)
        return AuthorWorkerCleanupResult(
            status="confirmed_absent",
            resource_id=receipt.resource_id,
            container_id=receipt.container_id,
            owner_ref=receipt.owner_ref,
            reason=reason,
            evidence=("fallback_authenticated_owned_container_removed",),
        )

    result = AuthorWorker(process, receipt, notices, fallback).close(
        "server_shutdown"
    )

    assert result.status == "confirmed_absent"
    assert result.evidence == ("fallback_authenticated_owned_container_removed",)
    assert calls == ["server_shutdown"]


def test_cleanup_fallback_authenticates_container_before_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = AuthorWorkerResourceReceipt(
        resource_id="docker:bb-author-owned",
        owner_ref="module:session:owned",
        execution_id="execution-owned",
        container_id="e" * 64,
        container_name="bb-author-owned",
        image_id="sha256:" + "f" * 64,
        image_ref="sha256:" + "f" * 64,
        platform="linux/arm64",
        receiver_identity="receiver-owned",
        state="running",
    )
    spec = AuthorWorkerSpec(
        owner_ref=receipt.owner_ref,
        execution_id=receipt.execution_id,
        execution_token="token-" + "a" * 64,
        image_ref=receipt.image_ref,
        platform=receipt.platform,
        command=("python3", "--stdio"),
        captured_staging_root="/owned/captured",
        staging_owner_ref=receipt.owner_ref,
    )
    inspection = json.dumps(
        [
            {
                "Id": receipt.container_id,
                "Image": receipt.image_id,
                "Config": {
                    "Labels": {
                        "dev.breadboard.author.owner": receipt.owner_ref,
                        "dev.breadboard.author.execution": receipt.execution_id,
                        "dev.breadboard.author.token": spec.execution_token,
                    }
                },
            }
        ]
    ).encode("utf-8")
    responses: Iterator[subprocess.CompletedProcess[bytes]] = iter(
        (
            subprocess.CompletedProcess([], 0, inspection, b""),
            subprocess.CompletedProcess([], 0, receipt.container_id.encode(), b""),
            subprocess.CompletedProcess(
                [],
                1,
                b"",
                b"Error: No such container: " + receipt.container_id.encode(),
            ),
        )
    )
    commands: list[list[str]] = []

    def command(
        _runtime: str,
        arguments: list[str],
        _deadline: float,
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return next(responses)

    monkeypatch.setattr(author_worker, "_docker_command", command)

    result = author_worker._authenticated_cleanup_fallback(
        spec,
        receipt,
        "server_shutdown",
        float("inf"),
    )

    assert result.status == "confirmed_absent"
    assert result.evidence == (
        "fallback_authenticated_owned_container_removed",
        "container_absence_observed",
    )
    assert commands == [
        ["container", "inspect", receipt.container_id],
        ["rm", "--force", "--volumes", receipt.container_id],
        ["container", "inspect", receipt.container_id],
    ]


def _write_checkpoint_worker_package(
    root: Path,
    *,
    logical_package: str,
    source: str,
    output_schema_ids: tuple[str, ...] = (),
) -> tuple[Path, str]:
    source_bytes = source.encode("utf-8")
    source_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    source_path = "src/checkpoint_module.py"
    manifest = {
        "schema_version": "bb.module_manifest.v1",
        "logical_package": logical_package,
        "entrypoint": f"{source_path}:module",
        "input_schema_ids": ["input.v1"],
        "output_schema_ids": list(output_schema_ids),
        "source_members": [
            {
                "path": source_path,
                "sha256": source_digest,
                "size_bytes": len(source_bytes),
            }
        ],
        "import_members": [
            {
                "module": logical_package,
                "path": source_path,
                "sha256": source_digest,
                "size_bytes": len(source_bytes),
            }
        ],
        "schema_members": {},
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    package = root / f"{logical_package}.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("module.json", manifest_bytes)
        archive.writestr(source_path, source_bytes)
    package_bytes = package.read_bytes()
    return package, "sha256:" + hashlib.sha256(package_bytes).hexdigest()


def _worker_key(
    request_id: str,
    *,
    session: str,
    generation: str,
    instance: str,
) -> RequestKey:
    return RequestKey(
        worker_session_id=session,
        request_id=request_id,
        generation_id=generation,
        instance_id=instance,
        work_id=f"{session}-work",
        attempt_id=f"{session}-attempt",
        authority_epoch=1,
    )


def _worker_start_body(
    package: Path,
    digest: str,
    *,
    module_id: str,
    instance_id: str,
    generation_id: str,
    initial_input: bytes | None,
    resume: dict[str, object] | None,
    max_message_bytes: int = MAX_FRAME_BYTES,
    max_checkpoint_bytes: int = MAX_CHECKPOINT_BYTES,
    output_schema_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "package_path": str(package),
        "package_digest": digest,
        "module_id": module_id,
        "instance_id": instance_id,
        "generation_id": generation_id,
        "instance_label": "root",
        "input_schemas": ["input.v1"],
        "output_schemas": list(output_schema_ids),
        "checkpoint_schemas": ["state.v1", "state.v2"],
        "dependencies": [],
        "child_targets": [],
        "initial_input": (
            None
            if initial_input is None
            else {
                "schema_id": "input.v1",
                "sequence": 0,
                "body": encode_bytes(initial_input),
                "final": False,
            }
        ),
        "resume": resume,
        "next_input_sequence": 0,
        "max_message_bytes": max_message_bytes,
        "max_checkpoint_bytes": max_checkpoint_bytes,
    }


def _send_worker_message(
    worker: AuthorWorker,
    kind: str,
    key: RequestKey,
    sequence: int,
    body: dict[str, object],
    *,
    max_bytes: int = MAX_FRAME_BYTES,
) -> None:
    worker.send_message(
        WireMessage(
            WireHeader(PROTOCOL_VERSION, kind, key, sequence),
            body,
        ),
        max_bytes=max_bytes,
    )


def _receive_worker_message(
    worker: AuthorWorker,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
) -> WireMessage:
    reassembler = MessageReassembler(maximum=max_bytes)
    while True:
        payload = worker.receive_frame(5)
        assert payload is not None
        assert len(payload) <= max_bytes
        message = reassembler.accept(WireMessage.decode(payload))
        if message is not None:
            return message


def _receive_checkpoint(
    worker: AuthorWorker,
    *,
    terminal_kind: str,
    max_bytes: int = MAX_FRAME_BYTES,
) -> tuple[dict[str, object], WireMessage]:
    chunks: list[bytes] = []
    metadata: dict[str, object] | None = None
    while True:
        message = _receive_worker_message(worker, max_bytes=max_bytes)
        if message.header.kind != "checkpoint":
            assert message.header.kind == terminal_kind
            assert metadata is not None
            assert len(chunks) == metadata["chunk_count"]
            assert sum(map(len, chunks)) == metadata["total_bytes"]
            envelope = {
                name: value
                for name, value in metadata.items()
                if name
                not in {
                    "phase",
                    "chunk_count",
                    "total_bytes",
                    "body_sha256",
                    "declared_at_sequence",
                }
            }
            envelope["body"] = b"".join(chunks)
            return envelope, message
        body = dict(message.body)
        assert body["phase"] == "proposal"
        assert body["chunk_index"] == len(chunks)
        chunk = decode_bytes(
            body.pop("body"),
            maximum=MAX_CHECKPOINT_BYTES,
        )
        index = body.pop("chunk_index")
        assert index == len(chunks)
        if metadata is None:
            metadata = body
        else:
            assert body == metadata
        chunks.append(chunk)


@contextmanager
def _stdio_checkpoint_worker(
    captured_root: Path,
) -> Iterator[AuthorWorker]:
    process = subprocess.Popen(
        [sys.executable, "-m", "breadboard.modules.worker", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "BREADBOARD_CAPTURED_ROOT": str(captured_root)},
    )
    receipt = AuthorWorkerResourceReceipt(
        resource_id=f"process:{process.pid}",
        owner_ref="module:test:worker",
        execution_id=f"execution:{process.pid}",
        container_id=f"process-{process.pid}",
        container_name=f"process-{process.pid}",
        image_id="sha256:" + "0" * 64,
        image_ref="sha256:" + "0" * 64,
        platform=sys.platform,
        receiver_identity=f"pid:{process.pid}",
        state="running",
    )
    notices = _ManagementNotices(process)
    worker = AuthorWorker(process, receipt, notices)
    try:
        yield worker
    finally:
        worker.close("test_complete")


def test_checkpoint_migration_and_resume_respect_small_physical_frames(
    tmp_path: Path,
) -> None:
    captured_root = tmp_path / "captured"
    captured_root.mkdir()
    frame_budget = 64 * 1024
    checkpoint_bytes = 100 * 1024
    source_module = """
from breadboard.modules import CheckpointCapture, CheckpointEnvelope, CheckpointProposal

class Instance:
    def __init__(self):
        self.state = b"x" * CHECKPOINT_BYTES

    def checkpoint(self, request):
        return CheckpointCapture(request, 0, self.state, None)

class Module:
    def bind_dependencies(self, dependencies):
        return dependencies

    def decode_input(self, envelope):
        return envelope.body

    def decode_output(self, envelope):
        return envelope.body

    def decode_checkpoint(self, envelope):
        return envelope.body

    def encode_output(self, value):
        return value

    def encode_checkpoint(self, state, **owner):
        declared_at_sequence = owner.pop("declared_at_sequence")
        return CheckpointProposal(
            CheckpointEnvelope(schema_id="state.v1", body=state, **owner),
            declared_at_sequence,
        )

    def assess_checkpoint(self, context):
        raise AssertionError("source worker does not assess checkpoints")

    def open_instance(self, **kwargs):
        return Instance()

module = Module()
""".replace("CHECKPOINT_BYTES", str(checkpoint_bytes))
    target_module = """
from breadboard.modules import (
    CheckpointCapture,
    CheckpointCompatibility,
    CheckpointEnvelope,
    CheckpointProposal,
)

class Instance:
    def __init__(self, state):
        self.state = state

    def checkpoint(self, request):
        return CheckpointCapture(request, 0, self.state, None)

class Module:
    def bind_dependencies(self, dependencies):
        return dependencies

    def decode_input(self, envelope):
        return envelope.body

    def decode_output(self, envelope):
        return envelope.body

    def decode_checkpoint(self, envelope):
        return {"schema_id": envelope.schema_id, "body": envelope.body}

    def encode_output(self, value):
        return value

    def encode_checkpoint(self, state, **owner):
        declared_at_sequence = owner.pop("declared_at_sequence")
        schema_id = state["schema_id"]
        body = state["body"]
        if schema_id == "state.v1":
            schema_id = "state.v2"
            body += b"|migrated"
        return CheckpointProposal(
            CheckpointEnvelope(schema_id=schema_id, body=body, **owner),
            declared_at_sequence,
        )

    def assess_checkpoint(self, context):
        if context.source_schema_id == "state.v1":
            return CheckpointCompatibility("migrate", "state.v2", "schema upgrade")
        return CheckpointCompatibility("compatible", "state.v2", "")

    def open_instance(self, *, resume, **kwargs):
        return Instance(resume)

module = Module()
"""
    source_package, source_digest = _write_checkpoint_worker_package(
        captured_root,
        logical_package="checkpoint_source",
        source=source_module,
    )
    target_package, target_digest = _write_checkpoint_worker_package(
        captured_root,
        logical_package="checkpoint_target",
        source=target_module,
    )
    source_generation = "sha256:" + "a" * 64
    target_generation = "sha256:" + "b" * 64

    source_key = _worker_key(
        "start",
        session="source-session",
        generation=source_generation,
        instance="source-instance",
    )
    with _stdio_checkpoint_worker(captured_root) as worker:
        _send_worker_message(
            worker,
            "start",
            source_key,
            0,
            _worker_start_body(
                source_package,
                source_digest,
                module_id="checkpoint_source",
                instance_id=source_key.instance_id,
                generation_id=source_generation,
                initial_input=b"open",
                resume=None,
                max_message_bytes=frame_budget,
                max_checkpoint_bytes=MAX_CHECKPOINT_BYTES,
            ),
            max_bytes=frame_budget,
        )
        ready = _receive_worker_message(worker, max_bytes=frame_budget)
        assert ready.header.kind == "ready"
        checkpoint_key = _worker_key(
            "capture",
            session="source-session",
            generation=source_generation,
            instance="source-instance",
        )
        _send_worker_message(
            worker,
            "checkpoint_request",
            checkpoint_key,
            1,
            {
                "request_id": "capture",
                "reason": "adopt",
                "requested_at_sequence": 0,
            },
            max_bytes=frame_budget,
        )
        source_checkpoint, result = _receive_checkpoint(
            worker,
            terminal_kind="result",
            max_bytes=frame_budget,
        )
        assert result.body["status"] == "checkpoint"
        assert len(source_checkpoint["body"]) == checkpoint_bytes

    target_key = _worker_key(
        "start",
        session="target-session",
        generation=target_generation,
        instance="target-instance",
    )
    with _stdio_checkpoint_worker(captured_root) as worker:
        _send_worker_message(
            worker,
            "start",
            target_key,
            0,
            _worker_start_body(
                target_package,
                target_digest,
                module_id="checkpoint_target",
                instance_id=target_key.instance_id,
                generation_id=target_generation,
                initial_input=None,
                resume=None,
                max_message_bytes=frame_budget,
                max_checkpoint_bytes=MAX_CHECKPOINT_BYTES,
            ),
            max_bytes=frame_budget,
        )
        ready = _receive_worker_message(worker, max_bytes=frame_budget)
        assert ready.header.kind == "ready"
        adoption_key = _worker_key(
            "adopt",
            session="target-session",
            generation=target_generation,
            instance="target-instance",
        )
        source_wire = {
            **source_checkpoint,
            "body": encode_bytes(
                source_checkpoint["body"],
                maximum=MAX_CHECKPOINT_BYTES,
            ),
        }
        preparation = WireMessage(
            WireHeader(PROTOCOL_VERSION, "checkpoint_prepare", adoption_key, 1),
            {
                "source": source_wire,
                "source_dependencies": [],
                "target_dependencies": [],
                "declared_at_sequence": 0,
            },
        )
        with pytest.raises(FrameLimitError):
            preparation.encode(max_bytes=frame_budget)
        preparation_frames = list(
            iter_message_frames(preparation, max_bytes=frame_budget)
        )
        assert len(preparation_frames) > 1
        assert all(len(frame) <= frame_budget for frame in preparation_frames)
        worker.send_message(preparation, max_bytes=frame_budget)
        migrated, compatibility = _receive_checkpoint(
            worker,
            terminal_kind="checkpoint_compatibility",
            max_bytes=frame_budget,
        )
        assert compatibility.body == {
            "disposition": "migrate",
            "target_schema_id": "state.v2",
            "reason": "schema upgrade",
        }
        assert migrated["schema_id"] == "state.v2"
        assert migrated["body"] == source_checkpoint["body"] + b"|migrated"

    resume_key = _worker_key(
        "start",
        session="resume-session",
        generation=target_generation,
        instance="resume-instance",
    )
    resume_wire = {
        **migrated,
        "body": encode_bytes(
            migrated["body"],
            maximum=MAX_CHECKPOINT_BYTES,
        ),
    }
    with _stdio_checkpoint_worker(captured_root) as worker:
        _send_worker_message(
            worker,
            "start",
            resume_key,
            0,
            _worker_start_body(
                target_package,
                target_digest,
                module_id="checkpoint_target",
                instance_id=resume_key.instance_id,
                generation_id=target_generation,
                initial_input=b"fresh state must not win",
                resume=resume_wire,
                max_message_bytes=frame_budget,
                max_checkpoint_bytes=MAX_CHECKPOINT_BYTES,
            ),
            max_bytes=frame_budget,
        )
        ready = _receive_worker_message(worker, max_bytes=frame_budget)
        assert ready.header.kind == "ready"
        assert ready.body["instance_open"] is True
        capture_key = _worker_key(
            "capture-resumed",
            session="resume-session",
            generation=target_generation,
            instance="resume-instance",
        )
        _send_worker_message(
            worker,
            "checkpoint_request",
            capture_key,
            1,
            {
                "request_id": "capture-resumed",
                "reason": "verify resume",
                "requested_at_sequence": 0,
            },
            max_bytes=frame_budget,
        )
        resumed, result = _receive_checkpoint(
            worker,
            terminal_kind="result",
            max_bytes=frame_budget,
        )
        assert result.body["status"] == "checkpoint"
        assert resumed["schema_id"] == "state.v2"
        assert resumed["body"] == migrated["body"]


def test_near_budget_input_and_output_cross_fragmented_stdio_frames(
    tmp_path: Path,
) -> None:
    captured_root = tmp_path / "captured"
    captured_root.mkdir()
    module_source = """
from breadboard.modules import OutputEnvelope, OutputResult

class Instance:
    def step(self, value):
        return OutputResult(value, None, None)

class Module:
    def bind_dependencies(self, dependencies):
        return dependencies

    def decode_input(self, envelope):
        return envelope.body

    def decode_output(self, envelope):
        return envelope.body

    def decode_checkpoint(self, envelope):
        return envelope.body

    def encode_output(self, value):
        return OutputEnvelope("output.v1", value)

    def encode_checkpoint(self, state, **owner):
        raise AssertionError("checkpoint not requested")

    def assess_checkpoint(self, context):
        raise AssertionError("checkpoint not prepared")

    def open_instance(self, **kwargs):
        return Instance()

module = Module()
"""
    package, digest = _write_checkpoint_worker_package(
        captured_root,
        logical_package="fragmented_echo",
        source=module_source,
        output_schema_ids=("output.v1",),
    )
    generation = "sha256:" + "c" * 64
    start_key = _worker_key(
        "start",
        session="fragment-session",
        generation=generation,
        instance="fragment-instance",
    )
    envelope = InputEnvelope(
        "input.v1",
        0,
        b"input-body|" + b"x" * (200 * 1024 - len(b"input-body|")),
        False,
    )
    input_key = _worker_key(
        "echo",
        session="fragment-session",
        generation=generation,
        instance="fragment-instance",
    )
    input_message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "input", input_key, 1),
        {
            "schema_id": envelope.schema_id,
            "sequence": envelope.sequence,
            "body": encode_bytes(envelope.body),
            "final": envelope.final,
        },
    )
    with pytest.raises(FrameLimitError):
        input_message.encode(max_bytes=MAX_FRAME_BYTES)
    input_frames = list(
        iter_message_frames(input_message, max_bytes=MAX_FRAME_BYTES)
    )
    assert len(input_frames) > 1
    assert all(len(frame) <= MAX_FRAME_BYTES for frame in input_frames)

    with _stdio_checkpoint_worker(captured_root) as worker:
        _send_worker_message(
            worker,
            "start",
            start_key,
            0,
            _worker_start_body(
                package,
                digest,
                module_id="fragmented_echo",
                instance_id=start_key.instance_id,
                generation_id=generation,
                initial_input=None,
                resume=None,
                output_schema_ids=("output.v1",),
            ),
        )
        ready = _receive_worker_message(worker)
        assert ready.header.kind == "ready"
        worker.send_message(input_message)
        output = _receive_worker_message(worker)
        result = _receive_worker_message(worker)

    assert output.header.kind == "output"
    assert output.header.key == input_key
    assert decode_bytes(output.body["body"]) == envelope.body
    assert result.header.kind == "result"
    assert result.body == {"status": "output", "output_emitted": True}


def test_fragmented_message_rejects_corrupted_chunk_body() -> None:
    key = _worker_key(
        "corrupt",
        session="fragment-session",
        generation="sha256:" + "d" * 64,
        instance="fragment-instance",
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "input", key, 0),
        {
            "schema_id": "input.v1",
            "sequence": 0,
            "body": encode_bytes(b"x" * (200 * 1024)),
            "final": False,
        },
    )
    chunks = [
        WireMessage.decode(frame)
        for frame in iter_message_frames(message, max_bytes=MAX_FRAME_BYTES)
    ]
    assert len(chunks) > 1
    final_body = dict(chunks[-1].body)
    corrupted = bytearray(decode_bytes(final_body["body"]))
    corrupted[-1] ^= 1
    final_body["body"] = encode_bytes(bytes(corrupted))
    chunks[-1] = WireMessage(chunks[-1].header, final_body)

    reassembler = MessageReassembler(maximum=MAX_FRAME_BYTES)
    for chunk in chunks[:-1]:
        assert reassembler.accept(chunk) is None
    with pytest.raises(WireProtocolError, match="digest"):
        reassembler.accept(chunks[-1])


def test_worker_refuses_declared_import_already_loaded_in_worker_sys_modules(
    tmp_path: Path,
) -> None:
    captured_root = tmp_path / "captured"
    captured_root.mkdir()
    loaded_name = "test_custom_preloaded_module"
    module_source = """
from breadboard.modules import OutputEnvelope, OutputResult

class Instance:
    def step(self, value):
        return OutputResult(value, None, None)

class Module:
    def bind_dependencies(self, dependencies):
        return dependencies

    def decode_input(self, envelope):
        return envelope.body

    def decode_output(self, envelope):
        return envelope.body

    def decode_checkpoint(self, envelope):
        return envelope.body

    def encode_output(self, value):
        return OutputEnvelope("output.v1", value)

    def encode_checkpoint(self, state, **owner):
        raise AssertionError("checkpoint not requested")

    def assess_checkpoint(self, context):
        raise AssertionError("checkpoint not prepared")

    def open_instance(self, **kwargs):
        return Instance()

module = Module()
"""
    package, digest = _write_checkpoint_worker_package(
        captured_root,
        logical_package=loaded_name,
        source=module_source,
    )
    generation = "sha256:" + "e" * 64
    start_key = _worker_key(
        "start",
        session="preloaded-shadow-session",
        generation=generation,
        instance="preloaded-shadow-instance",
    )
    code = (
        "import types, sys; "
        f"sys.modules[{loaded_name!r}] = types.ModuleType({loaded_name!r}); "
        f"exec({module_source!r}, sys.modules[{loaded_name!r}].__dict__); "
        "from breadboard.modules.worker import main; "
        "raise SystemExit(main())"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "BREADBOARD_CAPTURED_ROOT": str(captured_root)},
    )
    receipt = AuthorWorkerResourceReceipt(
        resource_id=f"process:{process.pid}",
        owner_ref="module:test:preloaded-worker",
        execution_id=f"execution:{process.pid}",
        container_id=f"process-{process.pid}",
        container_name=f"process-{process.pid}",
        image_id="sha256:" + "0" * 64,
        image_ref="sha256:" + "0" * 64,
        platform=sys.platform,
        receiver_identity=f"pid:{process.pid}",
        state="running",
    )
    notices = _ManagementNotices(process)
    worker = AuthorWorker(process, receipt, notices)
    try:
        _send_worker_message(
            worker,
            "start",
            start_key,
            0,
            _worker_start_body(
                package,
                digest,
                module_id=loaded_name,
                instance_id=start_key.instance_id,
                generation_id=generation,
                initial_input=None,
                resume=None,
            ),
        )
        failure = _receive_worker_message(worker)
        assert failure.header.kind == "failure"
        assert failure.body["code"] == "closure_mismatch"
    finally:
        worker.close("test_complete")
