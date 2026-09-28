"""Async persistent transport for the standalone native worker protocol."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import struct
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

from .native_worker import MAX_FRAME_BYTES, SCHEMA_VERSION

MAX_STDERR_BYTES = 64 * 1024
MAX_ERROR_MESSAGE_BYTES = 4096


class NativeSessionError(RuntimeError):
    """The worker transport or its owned process failed."""

    def __init__(self, message: str, *, code: str = "native_worker_failed") -> None:
        super().__init__(message)
        self.code = code


class NativeWorkerRemoteError(NativeSessionError):
    """The worker returned a bounded fatal error envelope."""

    def __init__(self, error: Mapping[str, Any]) -> None:
        error_type = error.get("type", "WorkerError")
        message = error.get("message", "native worker failed")
        super().__init__(
            str(message)[:MAX_ERROR_MESSAGE_BYTES],
            code="native_worker_remote_error",
        )
        self.error_type = str(error_type)
        self.error = dict(error)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise NativeSessionError("native worker message is not JSON", code="native_protocol_error") from exc
    if len(encoded) > MAX_FRAME_BYTES:
        raise NativeSessionError("native worker message exceeds the frame limit", code="native_frame_limit")
    return encoded


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object member")
        result[key] = value
    return result
RetireCallback = Callable[[], Awaitable[bool] | bool]


class NativeSession:
    """One serialized, lease-owned exchange stream to a persistent worker."""

    __slots__ = (
        "_process",
        "_max_frame_bytes",
        "_stderr_limit",
        "_retire_callback",
        "_write_lock",
        "_close_lock",
        "_next_request_id",
        "_closed",
        "_retiring",
        "_retired",
        "_stderr_task",
        "_provider_pending",
        "_stderr_bytes",
        "_stderr_error",
    )

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        stderr_limit: int = MAX_STDERR_BYTES,
        retire_callback: RetireCallback | None = None,
    ) -> None:
        if type(max_frame_bytes) is not int or not 1 <= max_frame_bytes <= MAX_FRAME_BYTES:
            raise ValueError("max_frame_bytes is outside the admitted range")
        if type(stderr_limit) is not int or stderr_limit < 1:
            raise ValueError("stderr_limit must be positive")
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ValueError("native worker requires stdin, stdout, and stderr pipes")
        self._process = process
        self._max_frame_bytes = max_frame_bytes
        self._stderr_limit = stderr_limit
        self._retire_callback = retire_callback
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._next_request_id = 1
        self._provider_pending = False
        self._closed = False
        self._retiring = False
        self._retired = False
        self._stderr_bytes = bytearray()
        self._stderr_error: NativeSessionError | None = None
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    @classmethod
    async def spawn(
        cls,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        stderr_limit: int = MAX_STDERR_BYTES,
    ) -> "NativeSession":
        if not argv or any(type(item) is not str or not item or "\x00" in item for item in argv):
            raise ValueError("native worker argv is invalid")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=None if env is None else dict(env),
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            raise NativeSessionError("native worker launch failed", code="native_launch_failed") from exc
        return cls(process, max_frame_bytes=max_frame_bytes, stderr_limit=stderr_limit)

    @property
    def process_id(self) -> int:
        return self._process.pid

    @property
    def stderr(self) -> bytes:
        return bytes(self._stderr_bytes)

    @property
    def closed(self) -> bool:
        return self._closed

    async def invoke_native_phase(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]:
        if type(operation) is not str or not operation or "\x00" in operation:
            raise NativeSessionError("native operation is invalid", code="native_protocol_error")
        if not isinstance(payload, Mapping):
            raise NativeSessionError("native payload must be an object", code="native_protocol_error")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise NativeSessionError("native operation timeout is invalid", code="native_timeout_invalid")
        async with self._write_lock:
            if self._stderr_error is not None:
                raise self._stderr_error
            if self._closed or self._retiring:
                raise NativeSessionError("native worker is closed", code="native_worker_closed")
            if self._provider_pending != (operation == "provider_response"):
                raise NativeSessionError(
                    "native worker exchange is awaiting provider_response"
                    if self._provider_pending
                    else "native provider_response has no pending request",
                    code="native_provider_exchange_order",
                )
            request_id = self._next_request_id
            self._next_request_id += 1
            command = {
                "schema_version": SCHEMA_VERSION,
                "request_id": request_id,
                "operation": operation,
                "payload": dict(payload),
            }
            try:
                await self._write(command)
                envelope = await asyncio.wait_for(
                    self._read_response(), timeout_ms / 1000
                )
                response_id = envelope["request_id"]
                if response_id != request_id:
                    raise NativeSessionError(
                        "native worker response ID does not match the outstanding command",
                        code="native_request_id_mismatch",
                    )
                if self._stderr_error is not None:
                    raise self._stderr_error
                if "error" in envelope:
                    raise NativeWorkerRemoteError(envelope["error"])
                result = envelope["result"]
                if not isinstance(result, Mapping):
                    raise NativeSessionError(
                        "native worker result is not an object",
                        code="native_protocol_error",
                    )
                if result.get("kind") == "provider_request":
                    self._provider_pending = True
                elif operation == "provider_response":
                    self._provider_pending = False
                return result
            except asyncio.TimeoutError as exc:
                await self._retire()
                raise NativeSessionError(
                    "native worker exchange timed out", code="native_timeout"
                ) from exc
            except asyncio.CancelledError:
                await self._retire()
                raise
            except NativeSessionError:
                await self._retire()
                if self._stderr_error is not None:
                    raise self._stderr_error
                raise
            except (
                BrokenPipeError,
                ConnectionError,
                EOFError,
                OSError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                await self._retire()
                raise NativeSessionError(
                    "native worker exchange failed", code="native_transport_failed"
                ) from exc

    async def _write(self, envelope: Mapping[str, Any]) -> None:
        encoded = _json_bytes(envelope)
        frame = struct.pack(">I", len(encoded)) + encoded
        stdin = self._process.stdin
        if stdin is None:
            raise NativeSessionError("native worker stdin is unavailable", code="native_transport_failed")
        try:
            stdin.write(frame)
            await stdin.drain()
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            raise NativeSessionError("native worker write failed", code="native_transport_failed") from exc

    async def _read_response(self) -> Mapping[str, Any]:
        stdout = self._process.stdout
        if stdout is None:
            raise NativeSessionError("native worker stdout is unavailable", code="native_transport_failed")
        try:
            header = await stdout.readexactly(4)
            (frame_bytes,) = struct.unpack(">I", header)
            if frame_bytes == 0 or frame_bytes > self._max_frame_bytes:
                raise NativeSessionError("native worker frame exceeds the admitted limit", code="native_frame_limit")
            encoded = await stdout.readexactly(frame_bytes)
        except asyncio.IncompleteReadError as exc:
            raise NativeSessionError("native worker closed its protocol stream", code="native_worker_exited") from exc
        try:
            envelope = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_pairs_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise NativeSessionError("native worker response is malformed", code="native_protocol_error") from exc
        if type(envelope) is not dict or envelope.get("schema_version") != SCHEMA_VERSION:
            raise NativeSessionError("native worker response schema is invalid", code="native_protocol_error")
        if set(envelope) not in (
            {"schema_version", "request_id", "result"},
            {"schema_version", "request_id", "error"},
        ):
            raise NativeSessionError("native worker response fields are invalid", code="native_protocol_error")
        request_id = envelope.get("request_id")
        if type(request_id) is not int or request_id <= 0:
            raise NativeSessionError("native worker response request ID is invalid", code="native_protocol_error")
        if "error" in envelope:
            error = envelope["error"]
            if type(error) is not dict or set(error) != {"type", "message"}:
                raise NativeSessionError("native worker fatal response is invalid", code="native_protocol_error")
            if type(error["type"]) is not str or type(error["message"]) is not str:
                raise NativeSessionError("native worker fatal response is invalid", code="native_protocol_error")
        return envelope

    async def _drain_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        try:
            while True:
                chunk = await stderr.read(min(8192, self._stderr_limit + 1))
                if not chunk:
                    return
                if len(self._stderr_bytes) + len(chunk) > self._stderr_limit:
                    self._stderr_error = NativeSessionError(
                        "native worker stderr exceeds the admitted limit",
                        code="native_stderr_limit",
                    )
                    retirement = asyncio.create_task(self._retire())
                    try:
                        while await stderr.read(8192):
                            pass
                    finally:
                        await retirement
                    return
                self._stderr_bytes.extend(chunk)
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError) as exc:
            self._stderr_error = NativeSessionError(
                "native worker stderr drain failed", code="native_stderr_failed"
            )
            self._stderr_error.__cause__ = exc

    async def _retire(self) -> bool:
        async with self._close_lock:
            if self._retiring:
                return self._retired
            self._retiring = True
            try:
                stdin = self._process.stdin
                if stdin is not None:
                    stdin.close()
                try:
                    await asyncio.wait_for(self._process.wait(), 0.25)
                except asyncio.TimeoutError:
                    pass
                if self._retire_callback is not None:
                    result = self._retire_callback()
                    if inspect.isawaitable(result):
                        result = await result
                    self._retired = bool(result)
                else:
                    self._retired = await self._kill_process_group()
                return self._retired
            finally:
                self._closed = True

    async def _kill_process_group(self) -> bool:
        pid = self._process.pid
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise NativeSessionError("native worker group cannot be retired", code="native_retire_failed") from exc
        try:
            await asyncio.wait_for(self._process.wait(), 0.25)
        except asyncio.TimeoutError:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), 0.75)
            except asyncio.TimeoutError as exc:
                raise NativeSessionError("native worker group did not retire", code="native_retire_failed") from exc
        return self._process.returncode is not None

    async def close(self) -> None:
        try:
            if not await self._retire():
                raise NativeSessionError(
                    "native worker ownership was not retired",
                    code="native_retire_failed",
                )
        finally:
            if self._stderr_task is not asyncio.current_task():
                if not self._stderr_task.done():
                    self._stderr_task.cancel()
                await asyncio.gather(self._stderr_task, return_exceptions=True)


__all__ = [
    "MAX_STDERR_BYTES",
    "NativeSession",
    "NativeSessionError",
    "NativeWorkerRemoteError",
]
