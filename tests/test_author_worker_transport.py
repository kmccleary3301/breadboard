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
from io import BytesIO
from pathlib import Path

import pytest

from breadboard_engine.compilation.contracts import BundleLimits
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
    iter_chunked_messages,
    read_message,
    write_message,
)
from breadboard.modules.worker import WorkerError, _Worker
import breadboard_engine.execution.author_worker as author_worker
from breadboard_engine.execution.author_worker import (
    AuthorWorker,
    AuthorWorkerCleanupResult,
    AuthorWorkerProfile,
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

    result = AuthorWorker(process, receipt, notices, fallback).close("server_shutdown")

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
    contracts: tuple[dict[str, object], ...] = (),
    child_targets: tuple[dict[str, object], ...] = (),
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
        "contracts": list(contracts),
        "child_targets": list(child_targets),
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
    dependencies: list[dict[str, object]] | None = None,
    child_targets: list[dict[str, object]] | None = None,
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
        "dependencies": [] if dependencies is None else dependencies,
        "child_targets": [] if child_targets is None else child_targets,
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
    maximum: int = MAX_FRAME_BYTES,
) -> WireMessage:
    reassembler = MessageReassembler(maximum=maximum)
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


def test_worker_resolves_compact_child_contract_from_captured_manifest(
    tmp_path: Path,
) -> None:
    captured_root = tmp_path / "captured"
    captured_root.mkdir()
    source = """
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
        return state

    def assess_checkpoint(self, context):
        return context

    def open_instance(self, **kwargs):
        raise AssertionError("no input was sent")

module = Module()
"""
    contract = {
        "contract_id": "child.contract.v1",
        "input_schema_ids": ["child.input.v1"],
        "output_schema_ids": ["child.output.v1"],
    }
    target = {
        "label": "child",
        "target": "child.module",
        "contract_id": "child.contract.v1",
    }
    package, digest = _write_checkpoint_worker_package(
        captured_root,
        logical_package="compact_child",
        source=source,
        contracts=(contract,),
        child_targets=(target,),
    )
    generation = "sha256:" + "a" * 64
    key = _worker_key(
        "start",
        session="compact-child-session",
        generation=generation,
        instance="compact-child-instance",
    )

    with _stdio_checkpoint_worker(captured_root) as worker:
        _send_worker_message(
            worker,
            "start",
            key,
            0,
            _worker_start_body(
                package,
                digest,
                module_id="compact_child",
                instance_id=key.instance_id,
                generation_id=generation,
                initial_input=None,
                resume=None,
                child_targets=[target],
            ),
        )
        ready = _receive_worker_message(worker)

    assert ready.header.kind == "ready"


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
    input_frames = list(iter_message_frames(input_message, max_bytes=MAX_FRAME_BYTES))
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


@pytest.mark.parametrize("status", ["ok", "failed"])
def test_service_result_roundtrip_under_small_physical_frames(status) -> None:
    key = _worker_key(
        "svc-0",
        session="fragment-session",
        generation="sha256:" + "e" * 64,
        instance="fragment-instance",
    )
    result = (
        {
            "outcomes": [{"output": encode_bytes(b"x" * MAX_FRAME_BYTES)}],
            "tool_result": {"text": "non-binary service data"},
        }
        if status == "ok"
        else {"code": "domain_error", "detail": "\0" * (60 * 1024)}
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "service_result", key, 0),
        {"request_id": key.request_id, "status": status, **result},
    )
    frames = list(iter_message_frames(message, max_bytes=4096))
    assert all(len(frame) <= 4096 for frame in frames)
    reassembler = MessageReassembler(maximum=4096)
    results = [
        result
        for frame in frames
        if (result := reassembler.accept(WireMessage.decode(frame))) is not None
    ]
    assert results == [message]


def test_worker_rejects_frame_budget_below_protocol_overhead() -> None:
    worker = _Worker(BytesIO(), BytesIO())
    key = _worker_key(
        "undersized-frame",
        session="frame-session",
        generation="sha256:" + "f" * 64,
        instance="frame-instance",
    )
    body = _worker_start_body(
        Path("unused.bbpkg"),
        "sha256:" + "e" * 64,
        module_id="frame.module",
        instance_id=key.instance_id,
        generation_id=key.generation_id,
        initial_input=None,
        resume=None,
        max_message_bytes=4095,
    )

    with pytest.raises(WorkerError, match="max_message_bytes"):
        worker._start(
            WireMessage(
                WireHeader(PROTOCOL_VERSION, "start", key, 0),
                body,
            )
        )


def test_start_metadata_roundtrips_under_small_physical_frames() -> None:
    key = _worker_key(
        "start-metadata",
        session="fragment-session",
        generation="sha256:" + "f" * 64,
        instance="fragment-instance",
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "start", key, 0),
        {
            "resume": None,
            "input_schemas": [f"schema.input.{index}" for index in range(1000)],
            "output_schemas": [f"schema.output.{index}" for index in range(1000)],
            "dependencies": [
                {"name": f"dependency-{index}", "contract_id": f"contract.{index}"}
                for index in range(250)
            ],
        },
    )
    frames = list(iter_message_frames(message, max_bytes=4096))
    assert len(frames) > 1
    assert all(len(frame) <= 4096 for frame in frames)
    reassembler = MessageReassembler(maximum=4096)
    results = [
        result
        for frame in frames
        if (result := reassembler.accept(WireMessage.decode(frame))) is not None
    ]
    assert results == [message]
    stream = BytesIO()
    write_message(stream, message, max_bytes=4096)
    stream.seek(0)
    assert read_message(stream, max_bytes=4096) == message


def test_start_metadata_can_use_the_package_manifest_member_ceiling() -> None:
    key = _worker_key(
        "start-large-metadata",
        session="fragment-session",
        generation="sha256:" + "a" * 64,
        instance="fragment-instance",
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "start", key, 0),
        {
            "resume": {
                "source_generation_id": key.generation_id,
                "source_module_id": "large.module",
                "source_instance_id": key.instance_id,
                "source_work_id": key.work_id,
                "source_attempt_id": key.attempt_id,
                "schema_id": "state.v1",
                "body": encode_bytes(
                    b"r" * MAX_CHECKPOINT_BYTES,
                    maximum=MAX_CHECKPOINT_BYTES,
                ),
            },
            "input_schemas": ["x" * (7 * MAX_CHECKPOINT_BYTES)],
        },
    )

    frames = list(iter_message_frames(message, max_bytes=MAX_FRAME_BYTES))
    reassembler = MessageReassembler(maximum=MAX_FRAME_BYTES)
    results = [
        result
        for frame in frames
        if (result := reassembler.accept(WireMessage.decode(frame))) is not None
    ]

    assert len(frames) > 1
    assert results == [message]


def test_start_fragmentation_rejects_oversized_resume_checkpoint() -> None:
    key = _worker_key(
        "oversized-resume",
        session="fragment-session",
        generation="sha256:" + "c" * 64,
        instance="fragment-instance",
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "start", key, 0),
        {
            "resume": {
                "source_generation_id": key.generation_id,
                "source_module_id": "large.module",
                "source_instance_id": key.instance_id,
                "source_work_id": key.work_id,
                "source_attempt_id": key.attempt_id,
                "schema_id": "state.v1",
                "body": base64.b64encode(
                    b"r" * (MAX_CHECKPOINT_BYTES + 1)
                ).decode("ascii"),
            },
            "input_schemas": ["x" * MAX_FRAME_BYTES],
        },
    )

    with pytest.raises(FrameLimitError):
        tuple(iter_message_frames(message, max_bytes=MAX_FRAME_BYTES))


def test_checkpoint_fragmentation_keeps_its_logical_ceiling() -> None:
    key = _worker_key(
        "checkpoint-bound",
        session="fragment-session",
        generation="sha256:" + "b" * 64,
        instance="fragment-instance",
    )

    with pytest.raises(ValueError, match="logical payload bound"):
        tuple(
            iter_chunked_messages(
                WireHeader(PROTOCOL_VERSION, "checkpoint_chunk", key, 0),
                "checkpoint_chunk",
                {},
                b"x" * (MAX_CHECKPOINT_BYTES + 1),
                maximum=MAX_CHECKPOINT_BYTES + 1,
            )
        )


def test_default_worker_profile_fits_maximum_extracted_package() -> None:
    profile = AuthorWorkerProfile()
    limits = BundleLimits()

    assert profile.scratch_bytes >= (
        limits.max_total_bytes + limits.max_members * 4096
    )
    assert profile.memory_bytes >= 2 * profile.scratch_bytes


def test_near_budget_service_result_crosses_fragmented_stdio_frames(
    tmp_path: Path,
) -> None:
    captured_root = tmp_path / "captured"
    captured_root.mkdir()
    frame_budget = 65536
    module_source = """
from breadboard.modules import ModuleInput, OutputEnvelope, OutputResult

class Instance:
    def __init__(self, dependencies):
        self.dependencies = dependencies

    def step(self, value):
        output = self.dependencies.exchange(ModuleInput("input.v1", value))
        return OutputResult(output.body, None, None)

class Module:
    def bind_dependencies(self, dependencies):
        return dependencies.dependency("upstream", "upstream.v1")

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

    def open_instance(self, dependencies, **kwargs):
        return Instance(dependencies)

module = Module()
"""
    package, digest = _write_checkpoint_worker_package(
        captured_root,
        logical_package="fragmented_service",
        source=module_source,
        output_schema_ids=("output.v1",),
    )
    generation = "sha256:" + "f" * 64
    start_key = _worker_key(
        "start",
        session="service-session",
        generation=generation,
        instance="service-instance",
    )
    raw_service_payload = b"service-payload|" + b"y" * (
        60 * 1024 - len(b"service-payload|")
    )
    input_key = _worker_key(
        "step-0",
        session="service-session",
        generation=generation,
        instance="service-instance",
    )
    input_message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "input", input_key, 1),
        {
            "schema_id": "input.v1",
            "sequence": 0,
            "body": encode_bytes(raw_service_payload),
            "final": False,
        },
    )

    with _stdio_checkpoint_worker(captured_root) as worker:
        _send_worker_message(
            worker,
            "start",
            start_key,
            0,
            _worker_start_body(
                package,
                digest,
                module_id="fragmented_service",
                instance_id=start_key.instance_id,
                generation_id=generation,
                initial_input=None,
                resume=None,
                output_schema_ids=("output.v1",),
                dependencies=[{"name": "upstream", "contract_id": "upstream.v1"}],
                max_message_bytes=frame_budget,
            ),
            max_bytes=frame_budget,
        )
        ready = _receive_worker_message(worker, max_bytes=frame_budget)
        assert ready.header.kind == "ready"

        worker.send_message(input_message, max_bytes=frame_budget)
        request = _receive_worker_message(worker, max_bytes=frame_budget)
        assert request.header.kind == "dependency_request"
        assert request.body["dependency"] == "upstream"
        assert decode_bytes(request.body["input"]["body"]) == raw_service_payload

        service_reply = WireMessage(
            WireHeader(
                PROTOCOL_VERSION,
                "service_result",
                request.header.key,
                2,
            ),
            {
                "request_id": request.body["request_id"],
                "status": "ok",
                "schema_id": "output.v1",
                "body": encode_bytes(raw_service_payload),
            },
        )
        worker.send_message(service_reply, max_bytes=frame_budget)

        output = _receive_worker_message(worker, max_bytes=frame_budget)
        assert output.header.kind == "output", output.body
        assert decode_bytes(output.body["body"]) == raw_service_payload

        result = _receive_worker_message(worker, max_bytes=frame_budget)
        assert result.header.kind == "result"
        assert result.body == {"status": "output", "output_emitted": True}


def test_fragmented_service_result_rejects_corrupted_chunk_body() -> None:
    key = _worker_key(
        "service-corrupt",
        session="fragment-session",
        generation="sha256:" + "1" * 64,
        instance="fragment-instance",
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "service_result", key, 0),
        {
            "request_id": "svc-0",
            "status": "ok",
            "output": {
                "schema_id": "output.v1",
                "body": encode_bytes(b"z" * (60 * 1024)),
                "final": True,
            },
        },
    )
    chunks = [
        WireMessage.decode(frame)
        for frame in iter_message_frames(message, max_bytes=4096)
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


@pytest.mark.parametrize("payload", [b"[]", b'{"duplicate":1,"duplicate":2}'])
def test_fragmented_service_result_rejects_invalid_json_object(payload) -> None:
    key = _worker_key(
        "svc-0",
        session="fragment-session",
        generation="sha256:" + "2" * 64,
        instance="fragment-instance",
    )
    chunks = iter_chunked_messages(
        WireHeader(PROTOCOL_VERSION, "message_chunk", key, 0),
        "message_chunk",
        {"message_kind": "service_result", "context": {}},
        payload,
        max_bytes=4096,
        maximum=MAX_CHECKPOINT_BYTES,
    )
    reassembler = MessageReassembler()
    with pytest.raises(WireProtocolError):
        for chunk in chunks:
            reassembler.accept(chunk)


def test_fragmented_service_result_rejects_exceeded_total_bound() -> None:
    key = _worker_key(
        "service-bound",
        session="fragment-session",
        generation="sha256:" + "3" * 64,
        instance="fragment-instance",
    )
    message = WireMessage(
        WireHeader(PROTOCOL_VERSION, "service_result", key, 0),
        {
            "request_id": "svc-0",
            "status": "ok",
            "output": {
                "schema_id": "output.v1",
                "body": encode_bytes(b"u" * (60 * 1024)),
                "final": True,
            },
        },
    )
    chunks = [
        WireMessage.decode(frame)
        for frame in iter_message_frames(message, max_bytes=4096)
    ]
    assert len(chunks) > 1
    oversized = dict(chunks[0].body)
    oversized["total_bytes"] = MAX_CHECKPOINT_BYTES + 1
    reassembler = MessageReassembler()
    with pytest.raises(WireProtocolError):
        reassembler.accept(WireMessage(chunks[0].header, oversized))


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
