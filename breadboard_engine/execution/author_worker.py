from __future__ import annotations

import base64
import json
import math
import os
import queue
import select
import subprocess
import threading
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable, Literal, Mapping

from breadboard.modules.transport import WireMessage, iter_message_frames


AUTHOR_FRAME_MAX_BYTES = 262_144
_STDERR_LIMIT = 64 * 1024
_NOTICE_PREFIX = b"BREADBOARD_AUTHOR_"
_MANAGEMENT_ENV = (
    "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
    "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "XDG_RUNTIME_DIR",
)


@dataclass(frozen=True, slots=True)
class AuthorWorkerProfile:
    cpu_count: int = 1
    memory_bytes: int = 64 * 1024 * 1024
    process_count: int = 1
    scratch_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AuthorWorkerSpec:
    owner_ref: str
    execution_id: str
    execution_token: str
    image_ref: str
    platform: str
    command: tuple[str, ...]
    captured_staging_root: str
    staging_owner_ref: str
    expected_package_sha256: str | None = None
    profile: AuthorWorkerProfile = AuthorWorkerProfile()
    capacity_authorization: str | None = None
    runtime: str = "docker"
    startup_timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class AuthorWorkerResourceReceipt:
    resource_id: str
    owner_ref: str
    execution_id: str
    container_id: str
    container_name: str
    image_id: str
    image_ref: str
    platform: str
    receiver_identity: str
    state: str


@dataclass(frozen=True, slots=True)
class AuthorWorkerCleanupResult:
    status: Literal["confirmed_absent", "unknown"]
    resource_id: str
    container_id: str
    owner_ref: str
    reason: str
    evidence: tuple[str, ...]


class AuthorWorkerLaunchError(RuntimeError):
    def __init__(self, message: str, cleanup: AuthorWorkerCleanupResult | None) -> None:
        super().__init__(message)
        self.cleanup = cleanup


def _text(value: Mapping[str, object], key: str) -> str:
    field = value[key]
    if not isinstance(field, str) or not field:
        raise ValueError(f"author management {key} must be a nonempty string")
    return field


def _receipt(value: Mapping[str, object]) -> AuthorWorkerResourceReceipt:
    return AuthorWorkerResourceReceipt(
        resource_id=_text(value, "resourceId"), owner_ref=_text(value, "ownerRef"),
        execution_id=_text(value, "executionId"), container_id=_text(value, "containerId"),
        container_name=_text(value, "containerName"), image_id=_text(value, "imageId"),
        image_ref=_text(value, "imageRef"), platform=_text(value, "platform"),
        receiver_identity=_text(value, "receiverIdentity"), state=_text(value, "state"),
    )


def _cleanup(value: Mapping[str, object]) -> AuthorWorkerCleanupResult:
    status = value["status"]
    if status not in ("confirmed_absent", "unknown"):
        raise ValueError("unknown author cleanup status")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ValueError("author cleanup evidence must be a string array")
    return AuthorWorkerCleanupResult(
        status=status, resource_id=_text(value, "resourceId"),
        container_id=_text(value, "containerId"), owner_ref=_text(value, "ownerRef"),
        reason=_text(value, "reason"), evidence=tuple(evidence),
    )


class _ManagementNotices:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.events: queue.Queue[tuple[str, Mapping[str, object]] | None] = queue.Queue(maxsize=16)
        self.cleanup: AuthorWorkerCleanupResult | None = None
        self.error: BaseException | None = None
        self.diagnostics: list[str] = []
        self._process = process
        self.thread = threading.Thread(target=self._read, name="author-management", daemon=True)
        self.thread.start()

    def _read(self) -> None:
        stream = self._process.stderr
        assert stream is not None
        consumed = 0
        try:
            for line in iter(lambda: stream.readline(_STDERR_LIMIT + 1), b""):
                consumed += len(line)
                if consumed > _STDERR_LIMIT:
                    raise ValueError("author management output exceeded its limit")
                if not line.startswith(_NOTICE_PREFIX):
                    self.diagnostics.append(line.decode("utf-8", errors="replace").rstrip())
                    continue
                kind, encoded = line[len(_NOTICE_PREFIX):].rstrip(b"\r\n").split(b"\t", 1)
                value = json.loads(base64.b64decode(encoded, validate=True))
                if not isinstance(value, dict):
                    raise ValueError("author management notice must be an object")
                name = kind.decode("ascii")
                if name == "CLEANUP":
                    if self.cleanup is not None:
                        raise ValueError("duplicate author cleanup receipt")
                    self.cleanup = _cleanup(value)
                elif name == "ERROR":
                    self.diagnostics.append(_text(value, "message"))
                elif name not in ("INTENT", "RECEIPT"):
                    raise ValueError("unknown author management notice")
                self.events.put_nowait((name, value))
        except BaseException as error:
            self.error = error
        finally:
            try:
                self.events.put_nowait(None)
            except queue.Full:
                pass


def _helper_path() -> Path:
    configured = os.environ.get("BREADBOARD_AUTHOR_WORKER_HELPER")
    if configured:
        return Path(configured)
    candidate = resources.files("breadboard_engine.execution").joinpath("node/author-bridge-helper.mjs")
    if not candidate.is_file():
        raise RuntimeError("The installed OCI author helper is missing; rebuild the wheel with scripts/build_author_worker_helper.py")
    return Path(candidate)


def _send(
    process: subprocess.Popen[bytes],
    body: bytes,
    *,
    deadline: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    if not isinstance(body, bytes) or not 0 < len(body) <= AUTHOR_FRAME_MAX_BYTES:
        raise ValueError("author frame has an invalid size or representation")
    stream = process.stdin
    if stream is None:
        raise RuntimeError("author channel has no input stream")
    descriptor = stream.fileno()
    os.set_blocking(descriptor, False)
    if deadline is None:
        deadline = time.monotonic() + 15.0
    for part in (len(body).to_bytes(4, "big"), body):
        remaining = memoryview(part)
        while remaining:
            if cancel_requested is not None and cancel_requested():
                raise InterruptedError("author launch was cancelled")
            timeout = max(0.0, deadline - time.monotonic())
            _, writable, _ = select.select(
                [], [descriptor], [], min(timeout, 0.1) if cancel_requested is not None else timeout,
            )
            if not writable:
                if time.monotonic() >= deadline:
                    raise TimeoutError("author frame write deadline expired")
                continue
            try:
                written = os.write(descriptor, remaining)
            except BlockingIOError:
                continue
            if written <= 0:
                raise BrokenPipeError("author frame write made no progress")
            remaining = remaining[written:]
            if remaining and time.monotonic() >= deadline:
                raise TimeoutError("author frame write deadline expired")


def _stop(process: subprocess.Popen[bytes], deadline: float) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic() - 1.0))
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass


def _docker_command(
    runtime: str,
    arguments: list[str],
    deadline: float,
) -> subprocess.CompletedProcess[bytes]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("author cleanup deadline expired")
    result = subprocess.run(
        [runtime, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=min(2.0, remaining),
        check=False,
        env={key: os.environ[key] for key in _MANAGEMENT_ENV if key in os.environ},
    )
    if len(result.stdout) + len(result.stderr) > _STDERR_LIMIT:
        raise ValueError("author cleanup management output exceeded its limit")
    return result


def _authenticated_cleanup_fallback(
    spec: AuthorWorkerSpec,
    receipt: AuthorWorkerResourceReceipt,
    reason: str,
    deadline: float,
) -> AuthorWorkerCleanupResult:
    def result(
        status: Literal["confirmed_absent", "unknown"],
        evidence: tuple[str, ...],
    ) -> AuthorWorkerCleanupResult:
        return AuthorWorkerCleanupResult(
            status=status,
            resource_id=receipt.resource_id,
            container_id=receipt.container_id,
            owner_ref=receipt.owner_ref,
            reason=reason,
            evidence=evidence,
        )

    def inspect_container() -> Mapping[str, object] | None:
        observed = _docker_command(
            spec.runtime,
            ["container", "inspect", receipt.container_id],
            deadline,
        )
        if observed.returncode != 0:
            if b"No such container:" in observed.stderr:
                return None
            raise RuntimeError("container presence could not be established")
        decoded = json.loads(observed.stdout)
        if not isinstance(decoded, list) or len(decoded) != 1:
            raise ValueError("Docker returned an invalid container inspection")
        record = decoded[0]
        if not isinstance(record, Mapping):
            raise ValueError("Docker returned an invalid container record")
        return record

    try:
        observed = inspect_container()
        if observed is None:
            return result("confirmed_absent", ("container_absence_observed",))
        config = observed.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        if (
            observed.get("Id") != receipt.container_id
            or observed.get("Image") != receipt.image_id
            or not isinstance(labels, Mapping)
            or labels.get("dev.breadboard.author.owner") != receipt.owner_ref
            or labels.get("dev.breadboard.author.execution") != spec.execution_id
            or labels.get("dev.breadboard.author.token") != spec.execution_token
        ):
            return result("unknown", ("container_ownership_mismatch",))
        removed = _docker_command(
            spec.runtime,
            ["rm", "--force", "--volumes", receipt.container_id],
            deadline,
        )
        if removed.returncode != 0:
            return result("unknown", ("container_removal_failed",))
        if inspect_container() is not None:
            return result("unknown", ("container_still_present",))
        return result(
            "confirmed_absent",
            (
                "fallback_authenticated_owned_container_removed",
                "container_absence_observed",
            ),
        )
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
        return result("unknown", ("authenticated_cleanup_fallback_failed",))


class AuthorWorker:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        receipt: AuthorWorkerResourceReceipt,
        notices: _ManagementNotices,
        fallback_cleanup: (
            Callable[[str, float], AuthorWorkerCleanupResult] | None
        ) = None,
    ) -> None:
        self._process = process
        self.receipt = receipt
        self._notices = notices
        self._fallback_cleanup = fallback_cleanup
        self._cleanup: AuthorWorkerCleanupResult | None = None
        self._incoming = bytearray()
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()

    @property
    def diagnostics(self) -> str:
        return "\n".join(self._notices.diagnostics)

    def send_message(
        self,
        message: WireMessage,
        *,
        max_bytes: int = AUTHOR_FRAME_MAX_BYTES,
    ) -> None:
        with self._write_lock:
            if self._cleanup is not None:
                raise RuntimeError("author channel is closed")
            for frame in iter_message_frames(message, max_bytes=max_bytes):
                _send(self._process, frame)

    def receive_frame(self, timeout_seconds: float | None = None) -> bytes | None:
        if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds < 0):
            raise ValueError("author read timeout must be finite and nonnegative")
        if not self._read_lock.acquire(blocking=False):
            raise RuntimeError("concurrent reads from one author channel are not permitted")
        try:
            stream = self._process.stdout
            if stream is None:
                raise RuntimeError("author channel has no output stream")
            deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
            while True:
                wanted = 4
                if len(self._incoming) >= 4:
                    size = int.from_bytes(self._incoming[:4], "big")
                    if not 0 < size <= AUTHOR_FRAME_MAX_BYTES:
                        raise ValueError("author frame exceeded its transport bound")
                    wanted += size
                    if len(self._incoming) == wanted:
                        body = bytes(self._incoming[4:])
                        self._incoming.clear()
                        return body
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                readable, _, _ = select.select([stream], [], [], remaining)
                if not readable:
                    raise TimeoutError("author frame read timed out")
                chunk = os.read(stream.fileno(), wanted - len(self._incoming))
                if not chunk:
                    if self._incoming:
                        raise RuntimeError("author channel closed inside a frame")
                    return None
                self._incoming.extend(chunk)
        finally:
            self._read_lock.release()

    def cancel(self, reason: str) -> AuthorWorkerCleanupResult:
        return self.close(f"cancelled:{reason}")

    def close(self, reason: str = "closed") -> AuthorWorkerCleanupResult:
        with self._close_lock:
            if self._cleanup is not None:
                return self._cleanup
            deadline = time.monotonic() + 15.0
            with self._write_lock:
                stream = self._process.stdin
                if stream is not None and not stream.closed:
                    stream.close()
            try:
                self._process.wait(
                    timeout=min(8.0, max(0.0, deadline - time.monotonic() - 5.0))
                )
            except subprocess.TimeoutExpired:
                _stop(
                    self._process,
                    min(deadline - 3.0, time.monotonic() + 2.0),
                )
            self._notices.thread.join(
                timeout=min(1.0, max(0.0, deadline - time.monotonic()))
            )
            observed = self._notices.cleanup
            authenticated = (
                observed is not None
                and self._process.poll() is not None
                and self._notices.error is None
                and observed.resource_id == self.receipt.resource_id
                and observed.container_id == self.receipt.container_id
                and observed.owner_ref == self.receipt.owner_ref
            )
            if authenticated and observed.status == "confirmed_absent":
                self._cleanup = observed
            elif self._fallback_cleanup is not None:
                self._cleanup = self._fallback_cleanup(reason, deadline)
            else:
                self._cleanup = AuthorWorkerCleanupResult(
                    status="unknown", resource_id=self.receipt.resource_id,
                    container_id=self.receipt.container_id, owner_ref=self.receipt.owner_ref,
                    reason=reason, evidence=("authenticated_cleanup_receipt_unavailable",),
                )
            for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                if stream is not None:
                    stream.close()
            return self._cleanup


def open_author_worker(
    spec: AuthorWorkerSpec,
    *,
    on_intent: Callable[[Mapping[str, str]], None],
    on_receipt: Callable[[AuthorWorkerResourceReceipt], None],
    cancel_requested: Callable[[], bool] | None = None,
) -> AuthorWorker:
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("author launch was cancelled before acquisition")
    if not spec.owner_ref or not spec.execution_id or len(spec.execution_token) < 32:
        raise ValueError("author execution requires an owner-minted identity and token")
    if not math.isfinite(spec.startup_timeout_seconds) or not 0 < spec.startup_timeout_seconds <= 30:
        raise ValueError("author startup timeout must be within thirty seconds")
    helper = _helper_path()
    profile = spec.profile
    launch = {
        "ownerRef": spec.owner_ref, "executionId": spec.execution_id, "executionToken": spec.execution_token,
        "imageRef": spec.image_ref, "platform": spec.platform, "command": list(spec.command),
        "capturedStagingRoot": spec.captured_staging_root, "stagingOwnerRef": spec.staging_owner_ref,
        "expectedPackageSha256": spec.expected_package_sha256, "packageMountTarget": "/breadboard-captured",
        "profile": {"cpuCount": profile.cpu_count, "memoryBytes": profile.memory_bytes, "processCount": profile.process_count, "scratchBytes": profile.scratch_bytes},
        "capacityAuthorization": spec.capacity_authorization, "runtimeCommand": spec.runtime,
    }
    launch_frame = json.dumps(launch, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(launch_frame) > AUTHOR_FRAME_MAX_BYTES:
        raise ValueError("author launch specification exceeds its frame limit")
    process = subprocess.Popen(
        ["node", str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0, env={key: os.environ[key] for key in _MANAGEMENT_ENV if key in os.environ},
    )
    notices = _ManagementNotices(process)
    deadline = time.monotonic() + spec.startup_timeout_seconds
    intent: Mapping[str, str] | None = None
    try:
        _send(process, launch_frame, deadline=deadline, cancel_requested=cancel_requested)
        while True:
            if cancel_requested is not None and cancel_requested():
                raise InterruptedError("author launch was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("OCI author startup exceeded thirty seconds")
            try:
                notice = notices.events.get(
                    timeout=min(remaining, 0.1) if cancel_requested is not None else remaining,
                )
            except queue.Empty:
                continue
            if notice is None:
                if notices.error is not None:
                    raise notices.error
                raise RuntimeError("OCI author helper exited before readiness: " + "\n".join(notices.diagnostics))
            kind, value = notice
            if kind == "INTENT":
                if intent is not None:
                    raise ValueError("duplicate author launch intent")
                intent = {key: _text(value, key) for key in ("ownerRef", "executionId", "resourceId", "containerName")}
                if intent["ownerRef"] != spec.owner_ref or intent["executionId"] != spec.execution_id:
                    raise ValueError("author resource intent does not match admitted execution")
                on_intent(intent)
                _send(process, b"intent_committed", deadline=deadline, cancel_requested=cancel_requested)
            elif kind == "RECEIPT":
                receipt = _receipt(value)
                if intent is None or (
                    receipt.resource_id != intent["resourceId"] or receipt.container_name != intent["containerName"]
                    or receipt.owner_ref != spec.owner_ref or receipt.execution_id != spec.execution_id
                    or receipt.image_ref != spec.image_ref or receipt.platform != spec.platform
                ):
                    raise ValueError("observed author receiver differs from admitted execution")
                on_receipt(receipt)
                _send(process, b"receipt_committed", deadline=deadline, cancel_requested=cancel_requested)
                return AuthorWorker(
                    process,
                    receipt,
                    notices,
                    lambda reason, cleanup_deadline: _authenticated_cleanup_fallback(
                        spec,
                        receipt,
                        reason,
                        cleanup_deadline,
                    ),
                )
    except BaseException as error:
        _stop(process, time.monotonic() + 15.0)
        notices.thread.join(timeout=1.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        raise AuthorWorkerLaunchError(str(error), notices.cleanup) from error


__all__ = [
    "AUTHOR_FRAME_MAX_BYTES", "AuthorWorker", "AuthorWorkerCleanupResult", "AuthorWorkerLaunchError",
    "AuthorWorkerProfile", "AuthorWorkerResourceReceipt", "AuthorWorkerSpec", "open_author_worker",
]
