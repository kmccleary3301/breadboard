from __future__ import annotations

import base64
import binascii
import json
import os
import sys
from collections.abc import Mapping, Sequence
from threading import Event
from typing import Any, Protocol

from breadboard.product.evidence.workspace import BreadBoardWorkspace
from breadboard.product.runtime.artifacts import ArtifactRef

from .manifest import ReplayManifest, ReplayManifestEntry
from .plan import ReplayPlan, canonical_json
from .ports import (
    ReplayWorkerCanceled,
    ReplayWorkerProcessError,
    ReplayWorkerResult,
    ReplayWorkerTimedOut,
    TapeReplayWorker,
)
from .redaction import ReplayRedactor, is_secret_environment_name

_REQUEST_SCHEMA = "bb.replay_worker_request.v1"
_RESPONSE_SCHEMA = "bb.replay_worker_response.v1"


class IsolatedReplayHost(Protocol):
    def workspace(self) -> str: ...

    def execute_isolated(
        self,
        argv: Sequence[str],
        *,
        stdin_data: bytes,
        timeout_seconds: float,
        environment: Mapping[str, str],
        cancelled: Event | None = None,
        cancellation_grace_seconds: float = 1.0,
    ) -> Mapping[str, Any]: ...


def _unique_object(rows: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in rows:
        if name in result:
            raise ValueError("replay IPC contains a duplicate JSON key")
        result[name] = value
    return result


def _decode_document(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("replay IPC must be canonical JSON") from error
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise ValueError("replay IPC must be one canonical JSON object")
    return document


def _encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decoded(value: object) -> bytes:
    if not isinstance(value, str):
        raise TypeError("replay IPC binary value must be base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("replay IPC contains invalid base64") from error


def _artifact_ref(value: object) -> ArtifactRef:
    if not isinstance(value, dict) or set(value) != {
        "digest",
        "size_bytes",
        "media_type",
    }:
        raise ValueError("replay IPC artifact reference is malformed")
    return ArtifactRef(value["digest"], value["size_bytes"], value["media_type"])


def _manifest(value: object) -> ReplayManifest:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "artifacts"}
        or not isinstance(value["artifacts"], list)
    ):
        raise ValueError("replay IPC manifest is malformed")
    if value["schema_version"] != "bb.replay_manifest.v1":
        raise ValueError("replay IPC manifest schema is unsupported")
    entries = []
    for row in value["artifacts"]:
        if not isinstance(row, dict) or set(row) != {"path", "media_type"}:
            raise ValueError("replay IPC manifest entry is malformed")
        entries.append(ReplayManifestEntry(row["path"], row["media_type"]))
    return ReplayManifest(tuple(entries))


def _plan(value: object) -> ReplayPlan:
    expected = {
        "schema_version",
        "source_session_id",
        "input_artifact",
        "worker_id",
        "manifest",
        "transcript_path",
        "options",
        "plan_id",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or not isinstance(value["options"], dict)
    ):
        raise ValueError("replay IPC plan is malformed")
    plan = ReplayPlan(
        source_session_id=value["source_session_id"],
        input_artifact=_artifact_ref(value["input_artifact"]),
        worker_id=value["worker_id"],
        manifest=_manifest(value["manifest"]),
        transcript_path=value["transcript_path"],
        options=value["options"],
        schema_version=value["schema_version"],
    )
    if plan.plan_id != value["plan_id"]:
        raise ValueError("replay IPC plan identity does not match its content")
    return plan


def encode_worker_request(plan: ReplayPlan, input_bytes: bytes) -> bytes:
    if not isinstance(input_bytes, bytes):
        raise TypeError("replay IPC input must be bytes")
    return canonical_json(
        {
            "schema_version": _REQUEST_SCHEMA,
            "plan": plan.as_dict(),
            "input_base64": _encoded(input_bytes),
        }
    )


def decode_worker_request(payload: bytes) -> tuple[ReplayPlan, bytes]:
    document = _decode_document(payload)
    if (
        set(document) != {"schema_version", "plan", "input_base64"}
        or document["schema_version"] != _REQUEST_SCHEMA
    ):
        raise ValueError("replay IPC request envelope is malformed")
    return _plan(document["plan"]), _decoded(document["input_base64"])


def encode_worker_response(result: ReplayWorkerResult) -> bytes:
    return canonical_json(
        {
            "schema_version": _RESPONSE_SCHEMA,
            "status": "ok",
            "outputs": [
                {"path": path, "content_base64": _encoded(content)}
                for path, content in sorted(result.outputs.items())
            ],
            "transcript": list(result.transcript),
        }
    )


def encode_worker_error(error: BaseException) -> bytes:
    return canonical_json(
        {
            "schema_version": _RESPONSE_SCHEMA,
            "status": "error",
            "error_type": type(error).__name__,
        }
    )


def decode_worker_response(payload: bytes) -> ReplayWorkerResult:
    document = _decode_document(payload)
    if document.get("schema_version") != _RESPONSE_SCHEMA:
        raise ReplayWorkerProcessError(
            "isolated replay returned an unsupported response"
        )
    if document.get("status") == "error" and set(document) == {
        "schema_version",
        "status",
        "error_type",
    }:
        raise ReplayWorkerProcessError("isolated replay worker failed")
    if (
        set(document) != {"schema_version", "status", "outputs", "transcript"}
        or document["status"] != "ok"
    ):
        raise ReplayWorkerProcessError("isolated replay returned a malformed response")
    if not isinstance(document["outputs"], list) or not isinstance(
        document["transcript"], list
    ):
        raise ReplayWorkerProcessError(
            "isolated replay returned malformed output collections"
        )
    outputs: dict[str, bytes] = {}
    for row in document["outputs"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "content_base64"}
            or not isinstance(row["path"], str)
        ):
            raise ReplayWorkerProcessError(
                "isolated replay returned a malformed output"
            )
        if row["path"] in outputs:
            raise ReplayWorkerProcessError(
                "isolated replay returned duplicate output paths"
            )
        outputs[row["path"]] = _decoded(row["content_base64"])
    if any(not isinstance(row, dict) for row in document["transcript"]):
        raise ReplayWorkerProcessError(
            "isolated replay returned a malformed transcript"
        )
    return ReplayWorkerResult(outputs, tuple(document["transcript"]))


class SandboxedReplayWorker:
    """Run the deterministic tape worker through the existing sandbox host port."""

    worker_id = TapeReplayWorker.worker_id

    def __init__(
        self,
        host: IsolatedReplayHost,
        *,
        environment_names: Sequence[str] = (),
        secret_values: Sequence[str] = (),
        timeout_seconds: float = 30.0,
        cancellation_grace_seconds: float = 1.0,
        cancelled: Event | None = None,
    ) -> None:
        names = tuple(environment_names)
        if any(
            not isinstance(name, str) or not name or is_secret_environment_name(name)
            for name in names
        ):
            raise ValueError(
                "replay environment allowlist contains a secret-bearing or invalid name"
            )
        if len(names) != len(set(names)):
            raise ValueError("replay environment allowlist contains duplicates")
        if timeout_seconds <= 0 or cancellation_grace_seconds <= 0:
            raise ValueError("replay worker deadlines must be positive")
        self.host = host
        self.environment_names = names
        self.timeout_seconds = timeout_seconds
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self.cancelled = cancelled
        self.redactor = ReplayRedactor(
            secret_values, BreadBoardWorkspace(host.workspace()).root
        )

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        if plan.worker_id != self.worker_id:
            raise ValueError(
                "replay plan worker_id does not select the isolated tape worker"
            )
        if self.cancelled is not None and self.cancelled.is_set():
            raise ReplayWorkerCanceled("replay canceled before isolated execution")
        workspace = BreadBoardWorkspace(self.host.workspace())
        environment = {
            name: os.environ[name]
            for name in self.environment_names
            if name in os.environ
        }
        result = self.host.execute_isolated(
            (
                sys.executable,
                "-I",
                "-m",
                "breadboard.product.evidence.replay.worker_entrypoint",
            ),
            stdin_data=encode_worker_request(plan, input_bytes),
            timeout_seconds=self.timeout_seconds,
            environment=environment,
            cancelled=self.cancelled,
            cancellation_grace_seconds=self.cancellation_grace_seconds,
        )
        if (
            self.cancelled is not None
            and self.cancelled.is_set()
            or result.get("cancelled") is True
        ):
            raise ReplayWorkerCanceled("isolated replay was canceled")
        exit_code = result.get("exit_code", result.get("exit"))
        if exit_code == 124 or result.get("timed_out") is True:
            raise ReplayWorkerTimedOut("isolated replay exceeded its deadline")
        if exit_code != 0:
            raise ReplayWorkerProcessError(
                "isolated replay worker exited unsuccessfully"
            )
        stdout = result.get("stdout")
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if not isinstance(stdout, bytes):
            raise ReplayWorkerProcessError(
                "isolated replay returned no canonical stdout"
            )
        if workspace.root != BreadBoardWorkspace(self.host.workspace()).root:
            raise ReplayWorkerProcessError(
                "isolated replay workspace identity changed during execution"
            )
        return decode_worker_response(stdout)


def main() -> int:
    try:
        plan, input_bytes = decode_worker_request(sys.stdin.buffer.read())
        result = TapeReplayWorker().execute(plan, input_bytes)
    except Exception as error:  # noqa: BLE001 - the child emits only a redacted error type.
        sys.stdout.buffer.write(encode_worker_error(error))
        sys.stdout.buffer.flush()
        return 1
    sys.stdout.buffer.write(encode_worker_response(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
