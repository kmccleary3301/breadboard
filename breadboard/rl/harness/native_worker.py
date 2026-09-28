"""Standalone framed worker protocol and owned Linux PID-namespace bootstrap.

This module intentionally imports only the Python standard library.  The SDK worker
loads its dependencies from a factory *after* the namespace child is established.
"""
from __future__ import annotations

import asyncio
import ctypes
import errno
import inspect
import json
import os
import select
import signal
import struct
import sys
from collections.abc import Callable, Mapping
from typing import Any

SCHEMA_VERSION = "bb.native-worker.rpc.v1"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_ERROR_MESSAGE_BYTES = 4096


class WorkerProtocolError(RuntimeError):
    """A malformed or out-of-order framed worker message."""


class WorkerBootstrapError(RuntimeError):
    """The required owned PID namespace could not be established."""


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object member")
        result[key] = value
    return result


def _bounded_message(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    encoded = message.encode("utf-8", "replace")
    if len(encoded) <= MAX_ERROR_MESSAGE_BYTES:
        return encoded.decode("utf-8", "replace")
    return encoded[:MAX_ERROR_MESSAGE_BYTES].decode("utf-8", "ignore")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkerProtocolError("worker response is not bounded JSON") from exc
    if len(encoded) > MAX_FRAME_BYTES:
        raise WorkerProtocolError("worker response exceeds the frame limit")
    return encoded


class WorkerChannel:
    """Read and write length-prefixed worker commands over binary stdio.

    A command frame is a four-byte unsigned big-endian length followed by UTF-8
    JSON.  ``receive`` validates monotonic request IDs and retains the current ID;
    ``respond`` replies to that current ID.  Actors may call ``receive`` while
    dispatching a command (for the provider_response exchange), in which case the
    nested command ID is intentionally the ID used by the eventual outer response.
    """

    __slots__ = (
        "_reader",
        "_writer",
        "_max_frame_bytes",
        "_last_request_id",
        "_current_request_id",
        "_closed",
    )

    def __init__(
        self,
        reader: Any = None,
        writer: Any = None,
        *,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        if type(max_frame_bytes) is not int or not 1 <= max_frame_bytes <= MAX_FRAME_BYTES:
            raise ValueError("max_frame_bytes is outside the admitted range")
        self._reader = reader if reader is not None else sys.stdin.buffer
        self._writer = writer if writer is not None else sys.stdout.buffer
        self._max_frame_bytes = max_frame_bytes
        self._last_request_id = 0
        self._current_request_id: int | None = None
        self._closed = False

    @property
    def current_request_id(self) -> int | None:
        return self._current_request_id

    def _read_exact(self, count: int) -> bytes | None:
        chunks: list[bytes] = []
        remaining = count
        while remaining:
            try:
                chunk = self._reader.read(remaining)
            except (OSError, ValueError) as exc:
                raise WorkerProtocolError("worker frame read failed") from exc
            if chunk is None:
                continue
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise WorkerProtocolError("worker frame reader returned non-bytes")
            if not chunk:
                if not chunks:
                    return None
                raise WorkerProtocolError("worker frame ended early")
            chunk_bytes = bytes(chunk)
            if len(chunk_bytes) > remaining:
                raise WorkerProtocolError("worker frame reader over-read")
            chunks.append(chunk_bytes)
            remaining -= len(chunk_bytes)
        return b"".join(chunks)

    def receive(self) -> Mapping[str, Any] | None:
        """Read and validate the next command, or return ``None`` on clean EOF."""
        if self._closed:
            raise WorkerProtocolError("worker channel is closed")
        header = self._read_exact(4)
        if header is None:
            self._closed = True
            return None
        (frame_bytes,) = struct.unpack(">I", header)
        if frame_bytes == 0 or frame_bytes > self._max_frame_bytes:
            raise WorkerProtocolError("worker frame length exceeds the admitted limit")
        encoded = self._read_exact(frame_bytes)
        if encoded is None:
            raise WorkerProtocolError("worker frame ended before its payload")
        try:
            payload = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=_pairs_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise WorkerProtocolError("worker command is not valid UTF-8 JSON") from exc
        if type(payload) is not dict or set(payload) != {
            "schema_version",
            "request_id",
            "operation",
            "payload",
        }:
            raise WorkerProtocolError("worker command fields are not exact")
        request_id = payload["request_id"]
        operation = payload["operation"]
        command_payload = payload["payload"]
        if (
            payload["schema_version"] != SCHEMA_VERSION
            or type(request_id) is not int
            or request_id <= self._last_request_id
            or type(operation) is not str
            or not operation
            or "\x00" in operation
            or type(command_payload) is not dict
        ):
            raise WorkerProtocolError("worker command authority is invalid")
        self._last_request_id = request_id
        self._current_request_id = request_id
        return payload

    def respond(self, result: Mapping[str, Any]) -> None:
        """Write a successful response for the current command ID."""
        request_id = self._current_request_id
        if request_id is None or self._closed:
            raise WorkerProtocolError("worker response has no current command")
        if not isinstance(result, Mapping):
            raise WorkerProtocolError("worker response result must be an object")
        envelope: Mapping[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "result": dict(result),
        }
        self._write(envelope)
        self._current_request_id = None

    def respond_error(self, exc: BaseException) -> None:
        """Write a bounded fatal response for the current command ID."""
        request_id = self._current_request_id
        if request_id is None or self._closed:
            raise WorkerProtocolError("worker fatal response has no current command")
        envelope: Mapping[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "error": {"type": type(exc).__name__, "message": _bounded_message(exc)},
        }
        self._write(envelope)
        self._current_request_id = None

    def _write(self, envelope: Mapping[str, Any]) -> None:
        encoded = _json_bytes(envelope)
        frame = struct.pack(">I", len(encoded)) + encoded
        try:
            self._writer.write(frame)
            flush = getattr(self._writer, "flush", None)
            if callable(flush):
                flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._closed = True
            raise WorkerProtocolError("worker frame write failed") from exc

    def close(self) -> None:
        self._closed = True


def _set_parent_death_signal() -> None:
    if sys.platform != "linux":
        raise WorkerBootstrapError("native worker PID ownership requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = getattr(libc, "prctl", None)
    if prctl is None:
        raise WorkerBootstrapError("Linux prctl is unavailable")
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    # PR_SET_PDEATHSIG = 1.
    if prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise WorkerBootstrapError(f"PR_SET_PDEATHSIG failed: {os.strerror(error)}")


def _pidfd_is_dead(pidfd: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLERR | select.POLLHUP)
    return bool(poller.poll(0))


_CLONE_NEWUSER = 0x10000000
_CLONE_NEWPID = 0x20000000


def _libc_unshare(flags: int) -> int:
    """Call libc ``unshare(2)``; return 0 or the errno."""
    libc_unshare = getattr(ctypes.CDLL(None, use_errno=True), "unshare", None)
    if libc_unshare is None:
        raise WorkerBootstrapError("Linux unshare is unavailable")
    libc_unshare.argtypes = [ctypes.c_int]
    libc_unshare.restype = ctypes.c_int
    return 0 if libc_unshare(flags) == 0 else ctypes.get_errno()


def _unshare_pid_namespace() -> None:
    """Enter a new PID namespace for the next fork via libc ``unshare(2)``.

    ``os.unshare`` exists only on Python 3.12+ and the project supports 3.11;
    like ``mount_namespace_broker``, call the syscall through libc.  A caller
    without CAP_SYS_ADMIN (EPERM) instead creates an owned user namespace with
    an identity uid/gid map and the PID namespace inside it; kernels that deny
    unprivileged user namespaces still fail closed.  The caller must be
    single-threaded, which ``serve`` is before it forks.
    """
    error = _libc_unshare(_CLONE_NEWPID)
    if error == 0:
        return
    if error != errno.EPERM:
        raise OSError(error, os.strerror(error))
    uid, gid = os.getuid(), os.getgid()
    error = _libc_unshare(_CLONE_NEWUSER | _CLONE_NEWPID)
    if error != 0:
        raise WorkerBootstrapError(
            "native worker PID namespace requires CAP_SYS_ADMIN or unprivileged "
            f"user namespaces: {os.strerror(error)}"
        )
    for path, content in (
        ("/proc/self/setgroups", "deny"),
        ("/proc/self/uid_map", f"{uid} {uid} 1"),
        ("/proc/self/gid_map", f"{gid} {gid} 1"),
    ):
        with open(path, "w", encoding="ascii") as handle:
            handle.write(content)


def _run_factory(factory: Callable[..., Any], channel: WorkerChannel) -> Any:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        accepts_channel = any(
            parameter.kind
            in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
            for parameter in signature.parameters.values()
        )
        actor = factory(channel) if accepts_channel else factory()
    else:
        actor = factory(channel)
    return actor


def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _serve_child(factory: Callable[..., Any], parent_pidfd: int) -> int:
    try:
        _set_parent_death_signal()
        # A parent outside this PID namespace is reported as PID zero.
        # Its inherited pidfd closes the fork/PDEATHSIG race without PID lookup.
        if _pidfd_is_dead(parent_pidfd):
            return 1
        os.close(parent_pidfd)
        parent_pidfd = -1
        channel = WorkerChannel()
        # SDK diagnostics must not corrupt the length-prefixed protocol.
        sys.stdout = sys.stderr
        actor = _run_factory(factory, channel)
        try:
            while True:
                command = channel.receive()
                if command is None:
                    close = getattr(actor, "close", None)
                    if callable(close):
                        _await_if_needed(close())
                    return 0
                dispatch = getattr(actor, "dispatch", None)
                if not callable(dispatch):
                    raise WorkerProtocolError("worker actor lacks dispatch(operation, payload)")
                result = _await_if_needed(dispatch(command["operation"], command["payload"]))
                if not isinstance(result, Mapping):
                    raise WorkerProtocolError("worker dispatch did not return an object")
                channel.respond(result)
        except BaseException as exc:
            try:
                if channel.current_request_id is not None:
                    channel.respond_error(exc)
            except BaseException:
                pass
            return 1
    finally:
        if parent_pidfd >= 0:
            try:
                os.close(parent_pidfd)
            except OSError:
                pass


def serve(factory: Callable[..., Any]) -> None:
    """Serve a factory-created actor as PID 1 in a private Linux PID namespace.

    The namespace parent closes all protocol stdio descriptors and waits for the
    actor child.  The outer process group therefore owns this launcher and its
    descendants; no PID file or detached daemon is created.
    """
    if not callable(factory):
        raise TypeError("factory must be callable")
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        raise WorkerBootstrapError("native worker requires Linux pidfd_open")
    parent_pid = os.getpid()
    try:
        parent_pidfd = os.pidfd_open(parent_pid)
    except OSError as exc:
        raise WorkerBootstrapError("could not open parent pidfd") from exc
    try:
        _unshare_pid_namespace()
        child_pid = os.fork()
    except BaseException:
        os.close(parent_pidfd)
        raise
    if child_pid == 0:
        status = _serve_child(factory, parent_pidfd)
        os._exit(status)
    os.close(parent_pidfd)
    # The namespace parent must not keep the protocol pipes alive while waiting.
    for descriptor in (0, 1, 2):
        try:
            os.close(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise
    _, status = os.waitpid(child_pid, 0)
    if os.WIFEXITED(status):
        os._exit(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        os._exit(128 + os.WTERMSIG(status))
    os._exit(1)


__all__ = [
    "MAX_ERROR_MESSAGE_BYTES",
    "MAX_FRAME_BYTES",
    "SCHEMA_VERSION",
    "WorkerBootstrapError",
    "WorkerChannel",
    "WorkerProtocolError",
    "serve",
]
