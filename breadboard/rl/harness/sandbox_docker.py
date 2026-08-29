from __future__ import annotations

import asyncio
import base64
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

CONTAINER_WORKSPACE_ROOT = "/testbed"
VERIFIER_RESULT_MAX_BYTES = 1024 * 1024
_WORKSPACE_OUTPUT_LIMIT_FAILURE = 124
_WORKSPACE_AUTHORITY_FAILURE = 125


def _container_workspace_path(logical_path: str) -> str:
    if type(logical_path) is not str or not logical_path or "\x00" in logical_path:
        raise DockerAdapterError("workspace_escape", "workspace path is invalid")
    path = PurePosixPath(logical_path)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in logical_path.split("/"))
    ):
        raise DockerAdapterError("workspace_escape", "workspace path is invalid")
    return f"{CONTAINER_WORKSPACE_ROOT}/{path.as_posix()}"


_WORKSPACE_PYTHON = """\
import base64
import errno
import json
import hashlib
import os
import secrets
import stat
import sys

OUTPUT_LIMIT_FAILURE = 124
AUTHORITY_FAILURE = 125
PROTOCOL_FAILURE = 126
ROOT = "/testbed"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW
LEAF_FLAGS = NOFOLLOW | getattr(os, "O_NONBLOCK", 0)

def fail():
    raise PermissionError(errno.EACCES, "workspace authority denied")


def write_all(fd, payload):
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short workspace write")
        view = view[written:]


def workspace_parts(logical_path):
    if (
        not logical_path
        or chr(0) in logical_path
        or logical_path.startswith("/")
        or any(part in {"", ".", ".."} for part in logical_path.split("/"))
    ):
        fail()
    return logical_path.split("/")


def open_parent(parts, create=False):
    if not DIRECTORY_FLAGS & getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
        raise RuntimeError("required no-follow directory flags are unavailable")
    descriptor = os.open(ROOT, DIRECTORY_FLAGS)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            fail()
        for component in parts[:-1]:
            try:
                child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                metadata = os.fstat(child)
                if not stat.S_ISDIR(metadata.st_mode):
                    fail()
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def regular(metadata):
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


def read_file(logical_path, offset, limit, probe_extra):
    if offset < 0 or limit < 0 or probe_extra not in (0, 1):
        raise RuntimeError("workspace read envelope is malformed")
    parts = workspace_parts(logical_path)
    parent, name = open_parent(parts)
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDONLY | LEAF_FLAGS, dir_fd=parent)
        metadata = os.fstat(descriptor)
        if not regular(metadata):
            fail()
        os.lseek(descriptor, offset, os.SEEK_SET)
        chunks = []
        remaining = limit + probe_extra
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        json.dump(
            {
                "bytes": len(payload),
                "content_base64": base64.b64encode(payload).decode("ascii"),
            },
            sys.stdout,
            sort_keys=True,
            separators=(",", ":"),
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def write_file(logical_path, payload):
    parts = workspace_parts(logical_path)
    parent, name = open_parent(parts, create=True)
    descriptor = -1
    temporary_name = None
    try:
        try:
            existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not regular(existing):
            fail()
        for _ in range(16):
            candidate = ".breadboard-" + str(os.getpid()) + "-" + secrets.token_hex(8)
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | LEAF_FLAGS,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_name is None:
            raise RuntimeError("could not reserve workspace temporary file")
        before = os.fstat(descriptor)
        if not regular(before):
            fail()
        write_all(descriptor, payload)
        after = os.fstat(descriptor)
        if (
            not regular(after)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_size != len(payload)
        ):
            fail()
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        temporary_name = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def list_files(logical_path, depth, max_entries):
    parts = workspace_parts(logical_path)
    parent, name = open_parent(parts)
    descriptor = -1
    values = []
    charged = 0

    def walk(directory, prefix, level):
        nonlocal charged
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                metadata = entry.stat(follow_symlinks=False)
                charged += 1
                if charged > max_entries:
                    raise OverflowError("workspace listing exceeds inode ceiling")
                if stat.S_ISDIR(metadata.st_mode):
                    entries.append((entry.name, metadata, True))
                elif regular(metadata):
                    entries.append((entry.name, metadata, False))
                else:
                    fail()
        for child_name, before, is_directory in sorted(entries):
            relative = "/".join(prefix + (child_name,))
            values.append(relative)
            if not is_directory or level >= depth:
                continue
            child = os.open(child_name, DIRECTORY_FLAGS, dir_fd=directory)
            try:
                after = os.fstat(child)
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                ):
                    fail()
                walk(child, prefix + (child_name,), level + 1)
            finally:
                os.close(child)

    try:
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            fail()
        walk(descriptor, tuple(parts), 0)
        json.dump(sorted(values), sys.stdout, ensure_ascii=False, separators=(",", ":"))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def read_stdin_payload(expected_bytes, expected_digest):
    if expected_bytes < 0 or len(expected_digest) != 64:
        raise RuntimeError("workspace write envelope is malformed")
    chunks = []
    remaining = expected_bytes + 1
    while remaining:
        chunk = sys.stdin.buffer.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if (
        len(payload) != expected_bytes
        or hashlib.sha256(payload).hexdigest() != expected_digest
    ):
        raise RuntimeError("workspace write envelope is inconsistent")
    return payload


def main():
    if len(sys.argv) < 3:
        raise RuntimeError("workspace helper arguments are malformed")
    mode = sys.argv[1]
    logical_path = sys.argv[2]
    if mode == "read":
        read_file(
            logical_path,
            int(sys.argv[3]),
            int(sys.argv[4]),
            int(sys.argv[5]),
        )
    elif mode == "write":
        write_file(
            logical_path,
            read_stdin_payload(int(sys.argv[3]), sys.argv[4]),
        )
    elif mode == "list":
        list_files(logical_path, int(sys.argv[3]), int(sys.argv[4]))
    else:
        raise RuntimeError("workspace helper operation is malformed")


def exit_failure(code, label):
    sys.stderr.write("bb-workspace-helper:" + label + chr(10))
    sys.exit(code)


try:
    main()
except OverflowError:
    exit_failure(OUTPUT_LIMIT_FAILURE, "output-limit")
except OSError as exc:
    authority_errors = {
        errno.EACCES,
        errno.ELOOP,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EPERM,
        errno.EROFS,
        errno.EXDEV,
    }
    if exc.errno in authority_errors:
        exit_failure(AUTHORITY_FAILURE, "authority")
    exit_failure(PROTOCOL_FAILURE, "protocol")
except Exception:
    exit_failure(PROTOCOL_FAILURE, "protocol")
"""


def _workspace_python_argv(
    mode: str, logical_path: str, *arguments: str
) -> tuple[str, ...]:
    _container_workspace_path(logical_path)
    return ("python3", "-c", _WORKSPACE_PYTHON, mode, logical_path, *arguments)


def _require_workspace_command_success(
    result: Mapping[str, Any], *, operation: str
) -> None:
    returncode = result.get("returncode")
    if type(returncode) is not int:
        raise DockerAdapterError(
            "runtime_protocol_error",
            f"{operation} returned a malformed command status",
        )
    if returncode:
        stderr = result.get("stderr")
        expected = {
            _WORKSPACE_OUTPUT_LIMIT_FAILURE: "bb-workspace-helper:output-limit\n",
            _WORKSPACE_AUTHORITY_FAILURE: "bb-workspace-helper:authority\n",
            126: "bb-workspace-helper:protocol\n",
        }
        if returncode == 127:
            code = "runtime_unsupported"
        elif type(stderr) is not str or stderr != expected.get(returncode):
            code = "runtime_protocol_error"
        elif returncode == _WORKSPACE_OUTPUT_LIMIT_FAILURE:
            code = "output_limit_exceeded"
        elif returncode == _WORKSPACE_AUTHORITY_FAILURE:
            code = "workspace_escape"
        else:
            code = "runtime_protocol_error"
        raise DockerAdapterError(
            code,
            f"{operation} rejected by workspace authority",
            details={"returncode": returncode},
        )


@dataclass(frozen=True, slots=True)
class ExecutableInvocation:
    argv0: str
    executable_fd: int
    executable_descriptor_path: str
    digest: str


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limited: bool = False


class DockerCliExecutor(Protocol):
    async def execute(
        self,
        executable: ExecutableInvocation,
        argv_tail: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int,
        environment: tuple[tuple[str, str], ...],
        input_bytes: bytes = b"",
    ) -> DockerCommandResult: ...


class SubprocessDockerCliExecutor:
    async def execute(
        self,
        executable: ExecutableInvocation,
        argv_tail: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int,
        environment: tuple[tuple[str, str], ...],
        input_bytes: bytes = b"",
    ) -> DockerCommandResult:
        logical_argv = (executable.argv0, *tuple(argv_tail))
        process = await asyncio.create_subprocess_exec(
            *logical_argv,
            executable=executable.executable_descriptor_path,
            pass_fds=(executable.executable_fd,),
            env=dict(environment),
            start_new_session=True,
            stdin=(
                asyncio.subprocess.PIPE
                if input_bytes
                else asyncio.subprocess.DEVNULL
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        captured = [bytearray(), bytearray()]
        limited = False
        output_limit_reached = asyncio.Event()

        async def drain(stream: asyncio.StreamReader, destination: bytearray) -> None:
            nonlocal limited
            while chunk := await stream.read(64 * 1024):
                remaining = output_limit - len(captured[0]) - len(captured[1])
                if remaining > 0:
                    destination.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    limited = True
                    output_limit_reached.set()

        async def feed_input() -> None:
            if process.stdin is None:
                return
            try:
                process.stdin.write(input_bytes)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def process_group_exists() -> bool:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                if process.returncode is not None or process_wait.done():
                    return False
                raise
            return True

        async def terminate_process_group() -> None:
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                pass
            except PermissionError:
                if process.returncode is None and not process_wait.done():
                    raise
            deadline = asyncio.get_running_loop().time() + 0.25
            while (
                process_group_exists()
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.01)
            if process_group_exists():
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    if process.returncode is None and not process_wait.done():
                        raise
            deadline = asyncio.get_running_loop().time() + 0.75
            while (
                process_group_exists()
                and asyncio.get_running_loop().time() < deadline
            ):
                await asyncio.sleep(0.01)
            if process_group_exists():
                raise OSError("Docker CLI process group survived cleanup")
            await asyncio.wait_for(asyncio.shield(process_wait), 0.25)

        readers = (
            asyncio.create_task(drain(process.stdout, captured[0])),
            asyncio.create_task(drain(process.stderr, captured[1])),
        )
        writer = asyncio.create_task(feed_input())
        process_wait = asyncio.create_task(process.wait())
        output_wait = asyncio.create_task(output_limit_reached.wait())
        io_tasks = (*readers, writer)
        timed_out = False
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                completed, _ = await asyncio.wait(
                    (process_wait, output_wait),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if output_wait in completed and not process_wait.done():
                    await terminate_process_group()
                else:
                    await process_wait
                await asyncio.gather(*io_tasks)
        except TimeoutError:
            timed_out = True
            await terminate_process_group()
        except BaseException:
            await terminate_process_group()
            raise
        finally:
            output_wait.cancel()
            await asyncio.gather(output_wait, return_exceptions=True)
            if any(not task.done() for task in io_tasks):
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*io_tasks, return_exceptions=True),
                        1.0,
                    )
                except asyncio.TimeoutError:
                    for task in io_tasks:
                        task.cancel()
                    await asyncio.gather(*io_tasks, return_exceptions=True)
        return DockerCommandResult(
            logical_argv, process.returncode, bytes(captured[0]), bytes(captured[1]),
            timed_out=timed_out, output_limited=limited,
        )


class DockerAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def observe_binary_digest(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()

_O_PATH = getattr(os, "O_PATH", 0o10000000)
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_OPENAT2_RESOLVE = (
    _RESOLVE_BENEATH
    | _RESOLVE_NO_SYMLINKS
    | _RESOLVE_NO_MAGICLINKS
    | _RESOLVE_NO_XDEV
)


class _OpenHow(ctypes.Structure):
    _fields_ = (
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    )


def _openat2_beneath(
    directory_fd: int,
    relative_path: str,
    *,
    readable_regular: bool = False,
) -> int:
    if (
        sys.platform != "linux"
        or type(relative_path) is not str
        or not relative_path
        or relative_path.startswith("/")
        or "\x00" in relative_path
        or type(readable_regular) is not bool
    ):
        raise DockerAdapterError(
            "runtime_unsupported", "Linux openat2 descriptor mounts are required"
        )
    access = os.O_RDONLY if readable_regular else _O_PATH
    how = _OpenHow(
        access | getattr(os, "O_CLOEXEC", 0),
        0,
        _OPENAT2_RESOLVE,
    )
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(
        437,
        directory_fd,
        relative_path.encode(),
        ctypes.byref(how),
        ctypes.sizeof(how),
    )
    if result < 0:
        error = ctypes.get_errno()
        code = (
            "runtime_unsupported"
            if error in {errno.ENOSYS, errno.EPERM}
            else "workspace_authority_mismatch"
        )
        raise DockerAdapterError(
            code,
            "descriptor mount source is not admissible",
            details={"errno": error},
        )
    return int(result)




def _validate_mount_descriptor(
    descriptor: int,
    *,
    workspace_device: int,
    expected_identity: tuple[int, int] | None = None,
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
        or metadata.st_dev != workspace_device
        or metadata.st_nlink < 1
        or (
            expected_identity is not None
            and (metadata.st_dev, metadata.st_ino) != expected_identity
        )
    ):
        raise DockerAdapterError(
            "workspace_authority_mismatch",
            "descriptor mount identity is not admitted",
        )
    return metadata


_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_IDENTITY_LABELS = ("bb.lease_id", "bb.plan_digest", "bb.epoch", "bb.workspace_id", "bb.role")


def _regular_file_metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_ctime_ns,
        metadata.st_mtime_ns,
    )


def _bounded_regular_file_descriptor_bytes(
    descriptor: int,
    *,
    expected_metadata: os.stat_result,
    max_bytes: int,
) -> bytes:
    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise DockerAdapterError(
            "runtime_preflight_failed",
            "security profile descriptor is unavailable",
        ) from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > max_bytes
        or _regular_file_metadata_identity(before)
        != _regular_file_metadata_identity(expected_metadata)
    ):
        raise DockerAdapterError(
            "runtime_preflight_failed",
            "security profile descriptor metadata is not exact and bounded",
        )
    chunks: list[bytes] = []
    offset = 0
    while offset < before.st_size:
        try:
            chunk = os.pread(
                descriptor,
                min(before.st_size - offset, 1024 * 1024),
                offset,
            )
        except InterruptedError:
            continue
        if not chunk:
            raise DockerAdapterError(
                "runtime_preflight_failed",
                "security profile changed while reading its descriptor",
            )
        chunks.append(chunk)
        offset += len(chunk)
    try:
        extra = os.pread(descriptor, 1, offset)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise DockerAdapterError(
            "runtime_preflight_failed",
            "security profile descriptor changed while reading",
        ) from exc
    if (
        extra
        or _regular_file_metadata_identity(after)
        != _regular_file_metadata_identity(before)
    ):
        raise DockerAdapterError(
            "runtime_preflight_failed",
            "security profile descriptor metadata changed while reading",
        )
    return b"".join(chunks)


def _identity_labels(
    plan: Any, *, lease_id: str, workspace_id: str, epoch: int, role: str
) -> dict[str, str]:
    return {
        "bb.lease_id": lease_id,
        "bb.plan_digest": plan.effective_plan_digest,
        "bb.epoch": str(epoch),
        "bb.workspace_id": workspace_id,
        "bb.role": role,
    }


def _container_name(*, role: str, workspace_id: str) -> str:
    return f"bb-{role}-{workspace_id}"


def _mount_argument(source: str, destination: str, *, readonly: bool) -> str:
    if (
        "," in source
        or type(destination) is not str
        or not destination.startswith("/")
        or any(character in destination for character in ("\x00", "\\", ",", ":"))
        or any(part in {"", ".", ".."} for part in destination.split("/")[1:])
    ):
        raise DockerAdapterError("workspace_escape", "invalid Docker mount path")
    value = f"type=bind,src={source},dst={destination}"
    return value + (",readonly" if readonly else "")

def _tmpfs_argument(destination: str, options: str) -> str:
    if (
        type(destination) is not str
        or not destination.startswith("/")
        or any(character in destination for character in ("\x00", "\\", ",", ":"))
        or any(part in {"", ".", ".."} for part in destination.split("/")[1:])
    ):
        raise DockerAdapterError("runtime_preflight_failed", "invalid tmpfs destination")
    tokens = options.split(",") if type(options) is str else []
    if not tokens or len(tokens) != len(set(tokens)):
        raise DockerAdapterError("runtime_preflight_failed", "invalid tmpfs options")
    required_flags = {"rw", "noexec", "nosuid"}
    allowed_flags = required_flags | {"nodev"}
    flags = {token for token in tokens if "=" not in token}
    assignments = [token for token in tokens if "=" in token]
    if flags < required_flags or not flags <= allowed_flags or len(assignments) != 1:
        raise DockerAdapterError("runtime_preflight_failed", "tmpfs options are not closed and bounded")
    key, separator, value = assignments[0].partition("=")
    if key != "size" or separator != "=" or not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise DockerAdapterError("runtime_preflight_failed", "tmpfs size must be a positive byte count")
    return f"{destination}:{options}"


def _validate_mount_authority(
    mounts: Sequence[tuple[Path, str, bool]],
    tmpfs_mounts: Sequence[tuple[str, str]],
) -> None:
    entries: list[tuple[str, bool, str]] = [
        (CONTAINER_WORKSPACE_ROOT, True, "workspace root")
    ]
    for _, destination, readonly in mounts:
        _mount_argument("authority-check", destination, readonly=readonly)
        entries.append((destination, readonly, "bind"))
    for destination, options in tmpfs_mounts:
        _tmpfs_argument(destination, options)
        entries.append((destination, False, "tmpfs"))

    trie: dict[str, Any] = {}
    for destination, readonly, kind in entries:
        components = tuple(part.casefold() for part in destination.split("/")[1:])
        node = trie
        for depth, component in enumerate(components):
            authority = node.get("")
            if authority is not None:
                ancestor_readonly, ancestor_kind = authority
                workspace_baseline = ancestor_kind == "workspace root" and depth == 1
                if not workspace_baseline:
                    detail = (
                        "writable child beneath read-only authority"
                        if not readonly and ancestor_readonly
                        else "nested"
                    )
                    raise DockerAdapterError(
                        "runtime_preflight_failed",
                        f"{detail} Docker mount destination ({ancestor_kind}/{kind})",
                    )
            node = node.setdefault(component, {})
        if "" in node:
            raise DockerAdapterError(
                "runtime_preflight_failed", "duplicate Docker mount destination"
            )
        pending = [child for key, child in node.items() if key and isinstance(child, dict)]
        descendants: list[tuple[bool, str]] = []
        while pending and not descendants:
            child = pending.pop()
            if "" in child:
                descendants.append(child[""])
            pending.extend(
                grandchild
                for key, grandchild in child.items()
                if key and isinstance(grandchild, dict)
            )
        if descendants:
            descendant_readonly, descendant_kind = descendants[0]
            detail = (
                "writable child beneath read-only authority"
                if not descendant_readonly and readonly
                else "nested"
            )
            raise DockerAdapterError(
                "runtime_preflight_failed",
                f"{detail} Docker mount destination ({kind}/{descendant_kind})",
            )
        node[""] = (readonly, kind)


def _validate_lsm_policy(security: Any) -> None:
    apparmor = security.apparmor_profile
    selinux = security.selinux_label
    reserved = {"disable", "disabled", "none", "unconfined"}
    if apparmor is not None:
        if (
            type(apparmor) is not str
            or apparmor.casefold() in reserved
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", apparmor) is None
        ):
            raise DockerAdapterError(
                "runtime_preflight_failed", "AppArmor authority is disabling or malformed"
            )
    elif selinux is not None:
        if (
            type(selinux) is not str
            or selinux.casefold() in reserved
            or selinux.count(":") < 2
            or re.fullmatch(r"[A-Za-z0-9_.:-]{3,255}", selinux) is None
        ):
            raise DockerAdapterError(
                "runtime_preflight_failed", "SELinux authority is disabling or malformed"
            )
    else:
        raise DockerAdapterError("runtime_preflight_failed", "an exact LSM authority is mandatory")


def build_create_argv(plan: Any, *, lease_id: str, workspace_id: str, epoch: int,
                      role: str, skeleton_path: Path,
                      mounts: Sequence[tuple[Path, str, bool]], security_profile_path: Path) -> tuple[str, ...]:
    runtime = plan.runtime
    security = plan.security_policy
    network = plan.network_policy
    resources = plan.resources
    if role not in {"primary", "verifier"}:
        raise DockerAdapterError("runtime_preflight_failed", "invalid container role")
    if (
        network.mode != "none"
        or network.docker_network != "none"
        or not network.default_deny
        or network.egress_route_ids
    ):
        raise DockerAdapterError("runtime_unsupported", "only Docker network none is supported")
    if (
        type(security.uid) is not int
        or type(security.gid) is not int
        or security.uid <= 0
        or security.gid <= 0
    ):
        raise DockerAdapterError("runtime_preflight_failed", "numeric non-root identity is mandatory")
    if not security.read_only_root or not security.drop_all_capabilities or not security.no_new_privileges:
        raise DockerAdapterError("runtime_preflight_failed", "hardened security flags are mandatory")
    if security.namespace_flags:
        raise DockerAdapterError(
            "runtime_preflight_failed",
            "raw Docker namespace flags are outside the closed policy",
        )
    _validate_lsm_policy(security)
    _validate_mount_authority(mounts, security.tmpfs_mounts)
    labels = _identity_labels(
        plan, lease_id=lease_id, workspace_id=workspace_id, epoch=epoch, role=role
    )
    argv: list[str] = [
        runtime.executable_path,
        "create",
        "--name",
        _container_name(role=role, workspace_id=workspace_id),
    ]
    for key in _IDENTITY_LABELS:
        argv += ["--label", f"{key}={labels[key]}"]
    argv += [
        "--runtime", runtime.oci_runtime_name,
        "--network", "none",
        "--cgroupns", "private",
        "--ipc", "private",
        "--user", f"{security.uid}:{security.gid}",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--security-opt", f"seccomp={security_profile_path}",
    ]
    if security.apparmor_profile is not None:
        argv += ["--security-opt", f"apparmor={security.apparmor_profile}"]
    elif security.selinux_label is not None:
        argv += ["--security-opt", f"label={security.selinux_label}"]
    argv += [
        "--pids-limit", str(resources.pids),
        "--memory", str(resources.memory_bytes),
        "--memory-swap", str(resources.memory_bytes),
        "--cpu-period", "100000",
        "--cpu-quota", str(resources.cpu_millis * 100),
        "--ulimit", f"nofile={resources.open_files}:{resources.open_files}",
        "--mount", _mount_argument(str(skeleton_path), CONTAINER_WORKSPACE_ROOT, readonly=True),
    ]
    destinations: set[str] = {CONTAINER_WORKSPACE_ROOT}
    for source, destination, readonly in sorted(mounts, key=lambda item: item[1]):
        if destination in destinations:
            raise DockerAdapterError("runtime_preflight_failed", "duplicate Docker mount destination")
        destinations.add(destination)
        argv += ["--mount", _mount_argument(str(source), destination, readonly=readonly)]
    for destination, options in security.tmpfs_mounts:
        if destination in destinations:
            raise DockerAdapterError("runtime_preflight_failed", "duplicate Docker mount destination")
        destinations.add(destination)
        argv += ["--tmpfs", _tmpfs_argument(destination, options)]
    argv += ["--workdir", CONTAINER_WORKSPACE_ROOT]
    for key, value in runtime.fixed_environment:
        argv += ["--env", f"{key}={value}"]
    argv += ["--pull", "never", plan.image.image_digest, *runtime.idle_argv]
    if any("docker.sock" in value for value in argv):
        raise DockerAdapterError("runtime_preflight_failed", "forbidden Docker authority")
    return tuple(argv)


def _json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DockerAdapterError("runtime_preflight_failed", f"Docker {label} output is malformed") from exc
    if type(value) is not dict:
        raise DockerAdapterError("runtime_preflight_failed", f"Docker {label} output has an unexpected schema")
    return value


def _registered_runtime(info: Mapping[str, Any], runtime_name: str) -> Mapping[str, Any] | None:
    if "Error" in info:
        return None
    runtimes = info.get("Runtimes")
    if type(runtimes) is not dict or any(
        type(key) is not str or type(value) is not dict for key, value in runtimes.items()
    ):
        return None
    return runtimes.get(runtime_name)


def _image_identity_matches(payload: bytes, expected_digest: str) -> bool:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DockerAdapterError("runtime_preflight_failed", "Docker image output is malformed") from exc
    if type(value) is list:
        if len(value) != 1 or type(value[0]) is not dict:
            raise DockerAdapterError("runtime_preflight_failed", "Docker image output has an unexpected schema")
        value = value[0]
    if type(value) is not dict or "Error" in value:
        return False
    image_id = value.get("Id")
    repo_digests = value.get("RepoDigests")
    if "Id" in value and type(image_id) is not str:
        return False
    if "RepoDigests" in value and (
        type(repo_digests) is not list
        or not all(type(reference) is str for reference in repo_digests)
    ):
        return False
    id_matches = type(image_id) is str and image_id == expected_digest
    refs_match = type(repo_digests) is list and any(
        reference.rpartition("@")[1] == "@" and reference.rpartition("@")[2] == expected_digest
        for reference in repo_digests
    )
    return id_matches or refs_match


def _platform_version(payload: Mapping[str, Any]) -> str:
    server = payload.get("Server")
    if type(server) is not dict:
        raise DockerAdapterError("runtime_unsupported", "Docker server version is unavailable")
    platform = server.get("Platform")
    if type(platform) is not dict:
        raise DockerAdapterError("runtime_unsupported", "Docker server platform is unavailable")
    name = platform.get("Name")
    version = server.get("Version")
    if type(name) is not str or not name or type(version) is not str or not version:
        raise DockerAdapterError("runtime_unsupported", "Docker server platform version is malformed")
    return f"{name}/{version}"


def _inspect_object(payload: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect output is malformed"
        ) from exc
    if type(decoded) is not list or len(decoded) != 1 or type(decoded[0]) is not dict:
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect output has an unexpected schema"
        )
    return decoded[0]


def _required_mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if type(value) is not dict:
        raise DockerAdapterError(
            "runtime_measurement_mismatch", f"Docker inspect field {key} is malformed"
        )
    return value


def _observed_identity(payload: bytes) -> tuple[str, str, Mapping[str, str]]:
    inspected = _inspect_object(payload)
    container_id = inspected.get("Id")
    name = inspected.get("Name")
    config = _required_mapping(inspected, "Config")
    labels = config.get("Labels")
    if (
        type(container_id) is not str
        or _CONTAINER_ID.fullmatch(container_id) is None
        or type(name) is not str
        or not name.startswith("/")
        or type(labels) is not dict
        or any(type(key) is not str or type(value) is not str for key, value in labels.items())
    ):
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect identity is malformed"
        )
    return container_id, name.removeprefix("/"), labels


def _validate_identity(
    payload: bytes,
    *,
    expected_id: str | None,
    expected_name: str,
    expected_labels: Mapping[str, str],
) -> str:
    container_id, name, labels = _observed_identity(payload)
    observed_binding = {key: value for key, value in labels.items() if key.startswith("bb.")}
    if (
        (expected_id is not None and container_id != expected_id)
        or name != expected_name
        or observed_binding != dict(expected_labels)
    ):
        raise DockerAdapterError(
            "stale_identity_uncertain", "Docker container identity does not match its binding"
        )
    return container_id


def decode_docker_inspect(
    payload: bytes,
    plan: Any,
    *,
    container_id: str,
    container_name: str,
    labels: Mapping[str, str],
    skeleton_path: Path,
    mounts: Sequence[tuple[Path, str, bool]],
    security_profile_path: Path,
    storage_bytes: int,
) -> dict[str, Any]:
    inspected = _inspect_object(payload)
    _validate_identity(
        payload,
        expected_id=container_id,
        expected_name=container_name,
        expected_labels=labels,
    )
    config = _required_mapping(inspected, "Config")
    host = _required_mapping(inspected, "HostConfig")
    network_settings = _required_mapping(inspected, "NetworkSettings")
    networks = network_settings.get("Networks")
    security_options = host.get("SecurityOpt")
    cap_add = host.get("CapAdd")
    cap_drop = host.get("CapDrop")
    mount_records = inspected.get("Mounts")
    ulimits = host.get("Ulimits")
    expected_tmpfs = dict(plan.security_policy.tmpfs_mounts)
    if (
        type(security_options) is not list
        or any(type(value) is not str for value in security_options)
        or len(security_options) != len(set(security_options))
        or cap_add not in (None, [])
        or type(cap_drop) is not list
        or cap_drop != ["ALL"]
        or type(mount_records) is not list
        or type(ulimits) is not list
        or type(networks) is not dict
        or set(networks) != {"none"}
        or any(
            key not in host or host.get(key) is not None
            for key in ("Devices", "DeviceRequests", "DeviceCgroupRules")
        )
        or "Tmpfs" not in host
        or type(host.get("Tmpfs")) is not dict
        or host.get("Tmpfs") != expected_tmpfs
    ):
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect security schema is not closed"
        )
    expected_security = {
        "no-new-privileges",
        f"seccomp={security_profile_path}",
    }
    lsm = plan.security_policy.apparmor_profile or plan.security_policy.selinux_label
    if plan.security_policy.apparmor_profile is not None:
        expected_security.add(f"apparmor={plan.security_policy.apparmor_profile}")
    elif plan.security_policy.selinux_label is not None:
        expected_security.add(f"label={plan.security_policy.selinux_label}")
    if set(security_options) != expected_security:
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect security options contradict the plan"
        )
    expected_mounts = [
        (str(skeleton_path), CONTAINER_WORKSPACE_ROOT, False),
        *[(str(source), destination, not readonly) for source, destination, readonly in mounts],
    ]
    observed_mounts: list[tuple[str, str, bool]] = []
    for record in mount_records:
        if type(record) is not dict:
            raise DockerAdapterError(
                "runtime_measurement_mismatch", "Docker inspect mount is malformed"
            )
        source = record.get("Source")
        destination = record.get("Destination")
        writable = record.get("RW")
        if (
            record.get("Type") != "bind"
            or type(source) is not str
            or type(destination) is not str
            or type(writable) is not bool
        ):
            raise DockerAdapterError(
                "runtime_measurement_mismatch", "Docker inspect mount is not a typed bind"
            )
        observed_mounts.append((source, destination, writable))
    if sorted(observed_mounts, key=lambda value: value[1]) != sorted(
        expected_mounts, key=lambda value: value[1]
    ):
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect mounts contradict the plan"
        )
    nofile = [
        value for value in ulimits
        if type(value) is dict and value.get("Name") == "nofile"
    ]
    if len(nofile) != 1:
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect nofile limit is ambiguous"
        )
    values = {
        "runtime": host.get("Runtime"),
        "image": inspected.get("Image"),
        "user": config.get("User"),
        "capabilities": "drop_all",
        "no_new_privileges": "no-new-privileges" in security_options,
        "seccomp": plan.security_policy.seccomp_digest,
        "lsm": lsm,
        "read_only_root": host.get("ReadonlyRootfs"),
        "mounts": tuple(
            (destination, readonly)
            for _, destination, readonly in sorted(mounts, key=lambda value: value[1])
        ),
        "mount_sources": tuple(
            (str(source), destination, readonly)
            for source, destination, readonly in sorted(mounts, key=lambda value: value[1])
        ),
        "workspace_root": str(skeleton_path),
        "tmpfs": tuple(sorted(plan.security_policy.tmpfs_mounts)),
        "network": host.get("NetworkMode"),
        "cpu_period": host.get("CpuPeriod"),
        "cpu_quota": host.get("CpuQuota"),
        "memory": host.get("Memory"),
        "memory_swap": host.get("MemorySwap"),
        "pids": host.get("PidsLimit"),
        "nofile": (
            nofile[0].get("Soft")
            if nofile[0].get("Soft") == nofile[0].get("Hard")
            else None
        ),
        "storage": storage_bytes,
        "output_limit": plan.limits.observation_bytes,
        "cgroups": (host.get("CgroupParent"), host.get("CgroupnsMode")),
        "namespaces": (
            f"cgroup:{host.get('CgroupnsMode')}",
            f"ipc:{host.get('IpcMode')}",
            f"pid:{'private' if host.get('PidMode') == '' else host.get('PidMode')}",
            f"uts:{'private' if host.get('UTSMode') == '' else host.get('UTSMode')}",
        ),
        "labels": tuple((key, labels[key]) for key in _IDENTITY_LABELS),
        "identity": (
            container_id,
            container_name,
            tuple((key, labels[key]) for key in _IDENTITY_LABELS),
        ),
    }
    if (
        config.get("Image") != plan.image.image_digest
        or host.get("Privileged") is not False
        or host.get("CgroupParent") != ""
        or host.get("CgroupnsMode") != "private"
        or host.get("IpcMode") != "private"
        or host.get("PidMode") != ""
        or host.get("UTSMode") != ""
    ):
        raise DockerAdapterError(
            "runtime_measurement_mismatch", "Docker inspect image or privilege state contradicts the plan"
        )
    return values


@dataclass(frozen=True, slots=True)
class StagedDockerDescriptorMount:
    source_path: str
    source_device: int
    source_inode: int
    source_mode: int
    descriptor_device: int
    descriptor_inode: int

    def __post_init__(self) -> None:
        if (
            type(self.source_path) is not str
            or not self.source_path.startswith("/")
            or os.path.normpath(self.source_path) != self.source_path
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.source_device,
                    self.source_inode,
                    self.source_mode,
                    self.descriptor_device,
                    self.descriptor_inode,
                )
            )
        ):
            raise ValueError("staged Docker descriptor mount is not exact")

    def validate_descriptor(self, descriptor: int) -> None:
        try:
            admitted = os.fstat(descriptor)
        except OSError as exc:
            raise DockerAdapterError(
                "runtime_unsupported", "staged Docker descriptor is unavailable"
            ) from exc
        if (admitted.st_dev, admitted.st_ino) != (
            self.descriptor_device,
            self.descriptor_inode,
        ):
            raise DockerAdapterError(
                "workspace_authority_mismatch",
                "staged Docker descriptor identity changed",
            )


class DockerDescriptorMountStager(Protocol):
    async def stage(
        self,
        descriptor: int,
        *,
        expected_device: int,
        expected_inode: int,
        directory: bool,
        lease_id: str,
        destination: str,
    ) -> StagedDockerDescriptorMount: ...

    async def validate(
        self,
        staged: StagedDockerDescriptorMount,
        descriptor: int,
    ) -> None: ...

    async def release(self, staged: StagedDockerDescriptorMount) -> None: ...


@dataclass(frozen=True, slots=True)
class PrivateDockerDaemonBinding:
    daemon_instance_id: str
    socket_path: str
    socket_device: int
    socket_inode: int
    socket_mode: int
    socket_uid: int
    socket_gid: int
    daemon_pid: int
    daemon_starttime: str
    daemon_pid_namespace: str
    daemon_executable_digest: str
    daemon_executable_device: int
    daemon_executable_inode: int
    daemon_executable_ctime_ns: int
    daemon_executable_size: int
    data_root: str
    config_fd: int
    config_proc_path: str
    daemon_config_digest: str
    config_device: int
    config_inode: int
    config_ctime_ns: int
    config_size: int
    runtime_fd: int
    runtime_proc_path: str
    runtime_registered_path: str
    runtime_digest: str
    runtime_device: int
    runtime_inode: int
    runtime_ctime_ns: int
    runtime_size: int

    def __post_init__(self) -> None:
        config_proc = f"/proc/{os.getpid()}/fd/{self.config_fd}"
        runtime_proc = f"/proc/{os.getpid()}/fd/{self.runtime_fd}"
        digests = (
            self.daemon_executable_digest,
            self.daemon_config_digest,
            self.runtime_digest,
        )
        identities = (
            self.socket_device,
            self.socket_inode,
            self.socket_uid,
            self.socket_gid,
            self.daemon_executable_device,
            self.daemon_executable_inode,
            self.daemon_executable_ctime_ns,
            self.daemon_executable_size,
            self.config_device,
            self.config_inode,
            self.config_ctime_ns,
            self.config_size,
            self.runtime_device,
            self.runtime_inode,
            self.runtime_ctime_ns,
            self.runtime_size,
        )
        if (
            type(self.daemon_instance_id) is not str
            or not self.daemon_instance_id
            or type(self.socket_path) is not str
            or not self.socket_path.startswith("/")
            or os.path.normpath(self.socket_path) != self.socket_path
            or self.socket_mode != 0o600
            or any(type(value) is not int or value < 0 for value in identities)
            or min(
                self.daemon_executable_size,
                self.config_size,
                self.runtime_size,
            ) <= 0
            or type(self.daemon_pid) is not int
            or self.daemon_pid <= 0
            or type(self.daemon_starttime) is not str
            or not self.daemon_starttime.isdecimal()
            or not re.fullmatch(r"pid:\[[0-9]+\]", self.daemon_pid_namespace)
            or type(self.data_root) is not str
            or not self.data_root.startswith("/")
            or os.path.normpath(self.data_root) != self.data_root
            or any(
                type(value) is not str
                or not value.startswith("sha256:")
                or _CONTAINER_ID.fullmatch(value.removeprefix("sha256:")) is None
                for value in digests
            )
            or type(self.config_fd) is not int
            or self.config_fd < 0
            or self.config_proc_path != config_proc
            or type(self.runtime_fd) is not int
            or self.runtime_fd < 0
            or self.runtime_proc_path != runtime_proc
            or type(self.runtime_registered_path) is not str
            or not self.runtime_registered_path.startswith("/")
            or os.path.normpath(self.runtime_registered_path)
            != self.runtime_registered_path
        ):
            raise ValueError("private Docker daemon binding is not exact")

    @staticmethod
    def _process_starttime(pid: int) -> str:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        suffix = payload[payload.rindex(")") + 2 :].split()
        if len(suffix) < 20:
            raise ValueError("daemon process stat is incomplete")
        return suffix[19]

    @staticmethod
    def _digest_fd(descriptor: int) -> str:
        hasher = hashlib.sha256()
        offset = 0
        while chunk := os.pread(descriptor, 1024 * 1024, offset):
            hasher.update(chunk)
            offset += len(chunk)
        return "sha256:" + hasher.hexdigest()

    def validate_live(self) -> os.stat_result:
        daemon_fd = -1
        try:
            socket_metadata = os.stat(self.socket_path, follow_symlinks=False)
            config_metadata = os.fstat(self.config_fd)
            runtime_metadata = os.fstat(self.runtime_fd)
            daemon_fd = os.open(
                f"/proc/{self.daemon_pid}/exe",
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
            daemon_metadata = os.fstat(daemon_fd)
            pid_namespace = os.readlink(f"/proc/{self.daemon_pid}/ns/pid")
            starttime = self._process_starttime(self.daemon_pid)
            daemon_digest = self._digest_fd(daemon_fd)
            config_digest = self._digest_fd(self.config_fd)
            runtime_digest = self._digest_fd(self.runtime_fd)
        except (OSError, ValueError) as exc:
            raise DockerAdapterError(
                "runtime_unsupported", "private Docker daemon authority is unavailable"
            ) from exc
        finally:
            if daemon_fd >= 0:
                os.close(daemon_fd)
        if (
            not stat.S_ISSOCK(socket_metadata.st_mode)
            or stat.S_IMODE(socket_metadata.st_mode) != self.socket_mode
            or (
                socket_metadata.st_dev,
                socket_metadata.st_ino,
                socket_metadata.st_uid,
                socket_metadata.st_gid,
            )
            != (
                self.socket_device,
                self.socket_inode,
                self.socket_uid,
                self.socket_gid,
            )
            or not stat.S_ISREG(config_metadata.st_mode)
            or (
                config_metadata.st_dev,
                config_metadata.st_ino,
                config_metadata.st_ctime_ns,
                config_metadata.st_size,
            )
            != (
                self.config_device,
                self.config_inode,
                self.config_ctime_ns,
                self.config_size,
            )
            or not stat.S_ISREG(runtime_metadata.st_mode)
            or (
                runtime_metadata.st_dev,
                runtime_metadata.st_ino,
                runtime_metadata.st_ctime_ns,
                runtime_metadata.st_size,
            )
            != (
                self.runtime_device,
                self.runtime_inode,
                self.runtime_ctime_ns,
                self.runtime_size,
            )
            or not stat.S_ISREG(daemon_metadata.st_mode)
            or (
                daemon_metadata.st_dev,
                daemon_metadata.st_ino,
                daemon_metadata.st_ctime_ns,
                daemon_metadata.st_size,
            )
            != (
                self.daemon_executable_device,
                self.daemon_executable_inode,
                self.daemon_executable_ctime_ns,
                self.daemon_executable_size,
            )
            or pid_namespace != self.daemon_pid_namespace
            or starttime != self.daemon_starttime
            or daemon_digest != self.daemon_executable_digest
            or config_digest != self.daemon_config_digest
            or runtime_digest != self.runtime_digest
        ):
            raise DockerAdapterError(
                "runtime_unsupported", "private Docker daemon authority changed"
            )
        return runtime_metadata


@dataclass(frozen=True, slots=True)
class DockerPreflightObservation:
    docker_cli_digest: str
    platform_version: str
    runtime_name: str
    advertised_path: str
    observed_oci_digest: str
    observed_oci_device: int
    observed_oci_inode: int
    version_payload: bytes
    info_payload: bytes
    image_payload: bytes
    daemon_binding: PrivateDockerDaemonBinding | None = None


def _require_daemon_runtime_binding(
    observation: DockerPreflightObservation, plan: Any
) -> None:
    binding = observation.daemon_binding
    if (
        binding is None
        or observation.runtime_name != plan.runtime.oci_runtime_name
        or observation.advertised_path != binding.runtime_registered_path
        or observation.observed_oci_digest != binding.runtime_digest
        or observation.observed_oci_device != binding.runtime_device
        or observation.observed_oci_inode != binding.runtime_inode
        or plan.runtime.oci_runtime_binary_digest != binding.runtime_digest
    ):
        raise DockerAdapterError(
            "runtime_unsupported",
            "Docker daemon cannot bind the admitted OCI executable object",
            details={
                "reason": "oci_runtime_exact_execution_unavailable",
                "runtime_name": plan.runtime.oci_runtime_name,
            },
        )
    binding.validate_live()


class DockerRuntimeAdapter:
    def __init__(self, *, executor: DockerCliExecutor, cli_environment: tuple[tuple[str, str], ...], mechanics_invocation: ExecutableInvocation | None = None, daemon_binding: PrivateDockerDaemonBinding | None = None) -> None:
        if cli_environment:
            raise ValueError(
                "Docker CLI mechanics environment must be empty; container environment is projected at create"
            )
        self._executor = executor
        self._environment: tuple[tuple[str, str], ...] = ()
        if daemon_binding is not None:
            daemon_binding.validate_live()
        self._daemon_binding = daemon_binding
        if mechanics_invocation is not None and (
            type(mechanics_invocation.executable_fd) is not int
            or mechanics_invocation.executable_fd < 0
            or not mechanics_invocation.executable_descriptor_path
        ):
            raise ValueError("mechanics invocation must identify an inherited executable descriptor")
        self._pinned: Any | None = None
        self._invocation: ExecutableInvocation | None = mechanics_invocation

    def _pin(self, plan: Any) -> ExecutableInvocation:
        current = self._invocation
        if current is not None:
            if (current.argv0 != plan.runtime.executable_path
                    or current.digest != plan.runtime.measured_binary_digest):
                raise DockerAdapterError("runtime_preflight_failed", "Docker CLI authority changed during adapter lifetime")
            return current
        from .sandbox import SandboxLaunchError, _snapshot_installed_executable
        try:
            pinned = _snapshot_installed_executable(
                plan.runtime.executable_path, plan.runtime.measured_binary_digest
            )
        except SandboxLaunchError as exc:
            raise DockerAdapterError(exc.code, str(exc), details=exc.details) from exc
        invocation = ExecutableInvocation(
            argv0=plan.runtime.executable_path,
            executable_fd=pinned.fd,
            executable_descriptor_path=pinned.proc_fd_path,
            digest=pinned.digest,
        )
        self._pinned = pinned
        self._invocation = invocation
        return invocation

    def close(self) -> None:
        pinned = self._pinned
        if pinned is None:
            return
        self._pinned = None
        self._invocation = None
        pinned.close()

    async def _raw(
        self,
        argv: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int,
        plan: Any | None = None,
        input_bytes: bytes = b"",
    ) -> DockerCommandResult:
        invocation = self._invocation if plan is None else self._pin(plan)
        if invocation is None:
            raise DockerAdapterError("runtime_preflight_failed", "Docker CLI invocation is not pinned")
        logical = tuple(argv)
        if not logical or logical[0] != invocation.argv0:
            raise DockerAdapterError("runtime_preflight_failed", "Docker CLI argv0 contradicts pinned authority")
        tail = logical[1:]
        if self._daemon_binding is not None:
            self._daemon_binding.validate_live()
            tail = (
                "--host",
                "unix://" + self._daemon_binding.socket_path,
                *tail,
            )
        if input_bytes:
            return await self._executor.execute(
                invocation,
                tail,
                timeout_ms=timeout_ms,
                output_limit=output_limit,
                environment=self._environment,
                input_bytes=input_bytes,
            )
        return await self._executor.execute(
            invocation,
            tail,
            timeout_ms=timeout_ms,
            output_limit=output_limit,
            environment=self._environment,
        )

    async def _execute(self, argv: Sequence[str], *, timeout_ms: int, output_limit: int,
                       code: str = "runtime_preflight_failed") -> DockerCommandResult:
        result = await self._raw(argv, timeout_ms=timeout_ms, output_limit=output_limit)
        if result.output_limited:
            raise DockerAdapterError("output_limit_exceeded", "Docker CLI output exceeded the admitted limit")
        if result.timed_out:
            raise DockerAdapterError(code, "Docker CLI operation timed out")
        if result.returncode:
            raise DockerAdapterError(code, "Docker CLI operation failed",
                                     details={"returncode": result.returncode})
        return result

    async def preflight(self, plan: Any) -> DockerPreflightObservation:
        runtime = plan.runtime
        invocation = self._pin(plan)
        output_limit = plan.limits.observation_bytes
        version = await self._execute(
            (invocation.argv0, "version", "--format", "{{json .}}"),
            timeout_ms=plan.limits.action_timeout_ms, output_limit=output_limit,
        )
        version_payload = _json_object(version.stdout, label="version")
        platform_version = _platform_version(version_payload)
        if not runtime.supported_platform_versions or platform_version not in runtime.supported_platform_versions:
            raise DockerAdapterError("runtime_unsupported", "Docker server platform version is not installed authority")
        info = await self._execute(
            (invocation.argv0, "info", "--format", "{{json .}}"),
            timeout_ms=plan.limits.action_timeout_ms, output_limit=output_limit,
        )
        info_payload = _json_object(info.stdout, label="info")
        registration = _registered_runtime(info_payload, runtime.oci_runtime_name)
        if registration is None:
            raise DockerAdapterError("runtime_unsupported", "requested OCI runtime is not registered")
        advertised = registration.get("path")
        arguments = registration.get("runtimeArgs", [])
        authority_path = runtime.oci_runtime_binary_path
        authority_digest = runtime.oci_runtime_binary_digest
        binding = self._daemon_binding
        if (
            type(advertised) is not str
            or not advertised.startswith("/")
            or type(arguments) is not list
            or arguments
            or type(authority_path) is not str
            or not authority_path.startswith("/")
            or type(authority_digest) is not str
        ):
            raise DockerAdapterError(
                "runtime_unsupported",
                "OCI runtime registration is not a closed executable observation",
            )
        if binding is None:
            if advertised != authority_path:
                raise DockerAdapterError(
                    "runtime_unsupported",
                    "OCI runtime registration is not installed authority",
                )
            from .sandbox import _open_installed_regular
            descriptor = -1
            try:
                descriptor = _open_installed_regular(authority_path)
                metadata = os.fstat(descriptor)
                hasher = hashlib.sha256()
                while chunk := os.read(descriptor, 1024 * 1024):
                    hasher.update(chunk)
                observed_digest = "sha256:" + hasher.hexdigest()
            except OSError as exc:
                raise DockerAdapterError(
                    "runtime_preflight_failed",
                    "registered OCI runtime binary is unavailable",
                ) from exc
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        else:
            metadata = binding.validate_live()
            if (
                advertised != binding.runtime_registered_path
                or authority_digest != binding.runtime_digest
                or info_payload.get("DockerRootDir") != binding.data_root
            ):
                raise DockerAdapterError(
                    "runtime_unsupported",
                    "private Docker daemon binding contradicts installed authority",
                )
            observed_digest = binding.runtime_digest
        if observed_digest != authority_digest:
            raise DockerAdapterError("runtime_preflight_failed", "registered OCI runtime binary identity mismatch")
        if runtime.runtime_class.value == "hardened_gvisor" and (
            runtime.runsc_binary_path != authority_path
            or runtime.runsc_binary_digest != observed_digest
        ):
            raise DockerAdapterError("runtime_unsupported", "runsc authority is contradictory")
        image = await self._execute(
            (invocation.argv0, "image", "inspect", plan.image.image_digest),
            timeout_ms=plan.limits.action_timeout_ms, output_limit=output_limit,
        )
        if not _image_identity_matches(image.stdout, plan.image.image_digest):
            raise DockerAdapterError("runtime_preflight_failed", "immutable image identity mismatch")
        return DockerPreflightObservation(
            docker_cli_digest=invocation.digest, platform_version=platform_version,
            runtime_name=runtime.oci_runtime_name, advertised_path=advertised,
            observed_oci_digest=observed_digest, observed_oci_device=metadata.st_dev,
            observed_oci_inode=metadata.st_ino, version_payload=version.stdout,
            info_payload=info.stdout, image_payload=image.stdout,
            daemon_binding=binding,
        )

    async def _inspect_raw(self, plan: Any, reference: str) -> DockerCommandResult:
        return await self._raw(
            (plan.runtime.executable_path, "inspect", reference),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
        )

    @staticmethod
    def _is_not_found(result: DockerCommandResult, reference: str) -> bool:
        if result.timed_out or result.output_limited or result.returncode == 0 or result.stdout.strip():
            return False
        stderr = result.stderr.strip()
        return stderr in {
            f"Error: No such object: {reference}".encode(),
            f"Error response from daemon: No such container: {reference}".encode(),
        }

    async def _bound_container(
        self,
        plan: Any,
        reference: str,
        *,
        expected_id: str | None,
        expected_name: str,
        labels: Mapping[str, str],
    ) -> tuple[str | None, str]:
        result = await self._inspect_raw(plan, reference)
        if self._is_not_found(result, reference):
            return None, "not_found"
        if result.timed_out or result.output_limited or result.returncode:
            return None, "stale_identity_uncertain"
        try:
            return _validate_identity(
                result.stdout,
                expected_id=expected_id,
                expected_name=expected_name,
                expected_labels=labels,
            ), ""
        except DockerAdapterError:
            return None, "stale_identity_uncertain"

    async def prepare(self, plan: Any, *, lease_id: str, workspace_id: str, epoch: int,
                      role: str, skeleton_path: Path,
                      mounts: Sequence[tuple[Path, str, bool]],
                      security_profile_path: Path,
                      security_profile_descriptor: int,
                      security_profile_metadata: os.stat_result) -> tuple[str, str, tuple[str, ...]]:
        self._pin(plan)
        profile = _bounded_regular_file_descriptor_bytes(
            security_profile_descriptor,
            expected_metadata=security_profile_metadata,
            max_bytes=len(plan.security_policy.seccomp_bytes),
        )
        if (
            profile != plan.security_policy.seccomp_bytes
            or "sha256:" + hashlib.sha256(profile).hexdigest()
            != plan.security_policy.seccomp_digest
        ):
            raise DockerAdapterError("runtime_preflight_failed", "seccomp profile identity mismatch")
        argv = build_create_argv(
            plan,
            lease_id=lease_id,
            workspace_id=workspace_id,
            epoch=epoch,
            role=role,
            skeleton_path=skeleton_path,
            mounts=mounts,
            security_profile_path=security_profile_path,
        )
        name = _container_name(role=role, workspace_id=workspace_id)
        labels = _identity_labels(
            plan, lease_id=lease_id, workspace_id=workspace_id, epoch=epoch, role=role
        )
        identifier: str | None = None
        try:
            created = await self._execute(
                argv,
                timeout_ms=plan.limits.action_timeout_ms,
                output_limit=plan.limits.observation_bytes,
                code="runtime_launch_failed",
            )
            decoded_identifier = created.stdout.strip().decode("ascii")
            if _CONTAINER_ID.fullmatch(decoded_identifier) is None:
                raise DockerAdapterError(
                    "runtime_launch_failed",
                    "Docker create did not return an immutable container ID",
                )
            identifier = decoded_identifier
            bound_id, detail = await self._bound_container(
                plan,
                name,
                expected_id=identifier,
                expected_name=name,
                labels=labels,
            )
            if bound_id is None:
                raise DockerAdapterError(
                    "runtime_launch_failed",
                    "Docker create identity could not be proven",
                    details={"cleanup": (("runtime_identity", "quarantined", detail),)},
                )
            return bound_id, name, argv
        except BaseException as primary:
            try:
                cleanup = await self.cleanup(
                    plan,
                    name,
                    expected_id=identifier,
                    expected_name=name,
                    labels=labels,
                )
            except BaseException as cleanup_error:
                cleanup = (
                    ("runtime_identity", "quarantined", type(cleanup_error).__name__),
                )
            details: dict[str, Any] = {
                "cleanup": cleanup,
                "container_name": name,
            }
            if identifier is not None:
                details["candidate_container_id"] = identifier
            if isinstance(primary, DockerAdapterError):
                primary.details.update(details)
                raise
            raise DockerAdapterError(
                "runtime_launch_failed",
                "Docker prepare failed",
                details=details,
            ) from primary

    async def start(self, plan: Any, container_id: str) -> None:
        self._pin(plan)
        await self._execute(
            (plan.runtime.executable_path, "start", container_id),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
            code="runtime_launch_failed",
        )

    async def create_start(self, plan: Any, *, lease_id: str, workspace_id: str, epoch: int,
                           role: str, skeleton_path: Path,
                           mounts: Sequence[tuple[Path, str, bool]],
                           security_profile_path: Path,
                           security_profile_descriptor: int,
                           security_profile_metadata: os.stat_result) -> tuple[str, str, tuple[str, ...]]:
        prepared = await self.prepare(
            plan,
            lease_id=lease_id,
            workspace_id=workspace_id,
            epoch=epoch,
            role=role,
            skeleton_path=skeleton_path,
            mounts=mounts,
            security_profile_path=security_profile_path,
            security_profile_descriptor=security_profile_descriptor,
            security_profile_metadata=security_profile_metadata,
        )
        container_id, name, _ = prepared
        labels = _identity_labels(
            plan, lease_id=lease_id, workspace_id=workspace_id, epoch=epoch, role=role
        )
        try:
            await self.start(plan, container_id)
            return prepared
        except BaseException as primary:
            try:
                cleanup = await self.cleanup(
                    plan,
                    container_id,
                    expected_id=container_id,
                    expected_name=name,
                    labels=labels,
                )
            except BaseException as cleanup_error:
                cleanup = (
                    ("runtime_identity", "quarantined", type(cleanup_error).__name__),
                )
            if isinstance(primary, DockerAdapterError):
                primary.details["cleanup"] = cleanup
            raise

    async def exec(
        self,
        plan: Any,
        container_id: str,
        argv: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int | None = None,
        input_bytes: bytes = b"",
    ) -> Mapping[str, Any]:
        self._pin(plan)
        captured_output_limit = (
            plan.limits.observation_bytes
            if output_limit is None
            else output_limit
        )
        raw_argv = (
            plan.runtime.executable_path,
            "exec",
            *(("-i",) if input_bytes else ()),
            container_id,
            *argv,
        )
        result = await self._raw(
            raw_argv,
            timeout_ms=timeout_ms,
            output_limit=captured_output_limit,
            input_bytes=input_bytes,
        )
        if result.output_limited:
            raise DockerAdapterError(
                "output_limit_exceeded",
                "Docker CLI output exceeded the admitted limit",
            )
        if result.timed_out:
            raise DockerAdapterError(
                "runtime_launch_failed", "Docker CLI operation timed out"
            )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.decode("utf-8", "replace"),
            "stderr": result.stderr.decode("utf-8", "replace"),
        }

    async def inspect(self, plan: Any, container_id: str) -> bytes:
        self._pin(plan)
        result = await self._execute(
            (plan.runtime.executable_path, "inspect", container_id),
            timeout_ms=plan.limits.action_timeout_ms,
            output_limit=plan.limits.observation_bytes,
            code="runtime_measurement_mismatch",
        )
        return result.stdout

    async def cleanup(
        self,
        plan: Any,
        reference: str,
        *,
        expected_id: str | None,
        expected_name: str,
        labels: Mapping[str, str],
    ) -> tuple[tuple[str, str, str], ...]:
        self._pin(plan)
        bound_id, detail = await self._bound_container(
            plan,
            reference,
            expected_id=expected_id,
            expected_name=expected_name,
            labels=labels,
        )
        if bound_id is None:
            state = "already_released" if detail == "not_found" else "quarantined"
            identity = (
                "runtime_identity",
                state,
                "" if detail == "not_found" else detail,
            )
            if state == "already_released":
                return (identity, ("runtime_absence", "already_released", ""))
            return (identity,)
        attempted: list[tuple[str, DockerCommandResult]] = []
        for resource, argv in (
            ("runtime_stop", (plan.runtime.executable_path, "stop", "--time", "5", bound_id)),
            ("runtime_remove", (plan.runtime.executable_path, "rm", "--force", bound_id)),
        ):
            attempted.append((
                resource,
                await self._raw(
                    argv,
                    timeout_ms=plan.limits.action_timeout_ms,
                    output_limit=plan.limits.observation_bytes,
                ),
            ))
        final = await self._inspect_raw(plan, bound_id)
        if not self._is_not_found(final, bound_id):
            reason = (
                "runtime_termination_failed"
                if not final.timed_out and not final.output_limited
                else "stale_identity_uncertain"
            )
            return tuple(
                (resource, "failed", reason) for resource, _ in attempted
            ) + (("runtime_absence", "failed", reason),)
        normalized: list[tuple[str, str, str]] = []
        for resource, result in attempted:
            if not result.timed_out and not result.output_limited and result.returncode == 0:
                normalized.append((resource, "released", ""))
            elif self._is_not_found(result, bound_id):
                normalized.append((resource, "already_released", ""))
            else:
                normalized.append((resource, "released", "final absence proven"))
        normalized.append(("runtime_absence", "released", ""))
        return tuple(normalized)



class DockerMeasurementProvider(Protocol):
    async def measure(self, plan: Any, container_name: str, inspect_payload: bytes) -> Mapping[str, Any]: ...


def requested_measurement(
    plan: Any,
    mounts: Sequence[tuple[Path, str, bool]],
    *,
    storage_bytes: int | None = None,
    identity: tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    measured = {
        "runtime": plan.runtime.oci_runtime_name,
        "image": plan.image.image_digest,
        "user": f"{plan.security_policy.uid}:{plan.security_policy.gid}",
        "capabilities": "drop_all",
        "no_new_privileges": plan.security_policy.no_new_privileges,
        "seccomp": plan.security_policy.seccomp_digest,
        "lsm": plan.security_policy.apparmor_profile or plan.security_policy.selinux_label,
        "read_only_root": plan.security_policy.read_only_root,
        "mounts": tuple(
            (destination, readonly)
            for _, destination, readonly in sorted(mounts, key=lambda item: item[1])
        ),
        "mount_sources": tuple(
            (str(source), destination, readonly)
            for source, destination, readonly in sorted(mounts, key=lambda item: item[1])
        ),
        "tmpfs": tuple(sorted(plan.security_policy.tmpfs_mounts)),
        "network": "none",
        "cpu_period": 100000,
        "cpu_quota": plan.resources.cpu_millis * 100,
        "memory": plan.resources.memory_bytes,
        "memory_swap": plan.resources.memory_bytes,
        "pids": plan.resources.pids,
        "nofile": plan.resources.open_files,
        "storage": plan.resources.storage_bytes if storage_bytes is None else storage_bytes,
        "output_limit": plan.limits.observation_bytes,
        "cgroups": ("", "private"),
        "namespaces": (
            "cgroup:private",
            "ipc:private",
            "pid:private",
            "uts:private",
        ),
        "labels": identity[2] if identity is not None else (),
    }
    if identity is not None:
        measured["identity"] = identity
    return measured


def measurement_mismatches(requested: Mapping[str, Any], measured: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(key for key, value in requested.items() if measured.get(key) != value))


class InspectDockerMeasurementProvider:
    """Production provider for host observations not encoded by Docker inspect."""

    async def measure(
        self, plan: Any, container_name: str, inspect_payload: bytes
    ) -> Mapping[str, Any]:
        return {}


class DockerRuntimeHandle:
    def __init__(
        self,
        *,
        adapter: DockerRuntimeAdapter,
        plan: Any,
        container_id: str | None,
        container_name: str,
        labels: Mapping[str, str],
        held_fds: Sequence[int] = (),
        mount_stager: DockerDescriptorMountStager | None = None,
        staged_mounts: Sequence[StagedDockerDescriptorMount] = (),
        lease_id: str | None = None,
    ) -> None:
        self.adapter = adapter
        self.plan = plan
        self.container_id = container_id
        self.container_name = container_name
        self.labels = dict(labels)
        self.runtime_id = container_id or container_name
        self._closed = False
        self._fenced = False
        self._cleanup_lock = asyncio.Lock()
        self._terminal_cleanup: tuple[Any, ...] | None = None
        self._held_fds = list(held_fds)
        self._mount_stager = mount_stager
        self._staged_mounts = list(staged_mounts)
        self._lease_id = lease_id
        self.repository_base_commit: str | None = None
        self.repository_relative_path: str | None = None

    def _record_terminal_cleanup(self, receipts: tuple[Any, ...]) -> None:
        from .materialization import CleanupState
        states = {item.state for item in receipts}
        if states <= {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}:
            self._closed = True
            self._terminal_cleanup = receipts
            while self._held_fds:
                os.close(self._held_fds.pop())

    async def _run(
        self,
        argv: Sequence[str],
        *,
        timeout_ms: int,
        output_limit: int | None = None,
        input_bytes: bytes = b"",
    ) -> Mapping[str, Any]:
        if self._closed or self._fenced:
            raise DockerAdapterError("lease_not_active", "container lease is closed")
        try:
            return await self.adapter.exec(
                self.plan,
                self.container_id,
                tuple(argv),
                timeout_ms=timeout_ms,
                output_limit=output_limit,
                input_bytes=input_bytes,
            )
        except BaseException as exc:
            indeterminate = not isinstance(exc, DockerAdapterError) or (
                exc.code == "output_limit_exceeded"
                or (exc.code == "runtime_launch_failed" and "returncode" not in exc.details)
            )
            if indeterminate:
                self._fenced = True
                cleanup = await asyncio.shield(self._retry_cleanup())
                if isinstance(exc, DockerAdapterError):
                    exc.details["cleanup"] = cleanup
            raise

    async def run_shell(
        self, command: str, *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        return await self._run(
            ("sh", "-lc", command),
            timeout_ms=timeout_ms,
            output_limit=output_limit,
        )

    async def run_argv(
        self, argv: Sequence[str], *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        return await self._run(
            tuple(argv), timeout_ms=timeout_ms, output_limit=output_limit
        )

    async def read_text(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> Mapping[str, Any]:
        return await self._read_text_bounded(
            path,
            offset=offset,
            limit=limit,
            ceiling=self.plan.limits.observation_bytes,
        )

    async def read_artifact_text(self, path: str) -> Mapping[str, Any]:
        if not path.startswith("result/"):
            raise DockerAdapterError(
                "runtime_preflight_failed",
                "verifier artifact read must target the result directory",
            )
        return await self._read_text_bounded(
            path,
            offset=0,
            limit=None,
            ceiling=min(
                self.plan.limits.artifact_bytes_each,
                self.plan.limits.artifact_bytes_total,
                VERIFIER_RESULT_MAX_BYTES,
            ),
        )

    async def _read_text_bounded(
        self,
        path: str,
        *,
        offset: int,
        limit: int | None,
        ceiling: int,
    ) -> Mapping[str, Any]:
        if type(offset) is not int or offset < 0:
            raise DockerAdapterError("runtime_preflight_failed", "read offset is invalid")
        if limit is not None and (
            type(limit) is not int or limit < 0 or limit > ceiling
        ):
            raise DockerAdapterError(
                "output_limit_exceeded", "read limit exceeds admitted ceiling"
            )
        selected_limit = ceiling if limit is None else limit
        probe_extra = limit is None
        helper_read_limit = selected_limit
        wire_bytes = helper_read_limit + int(probe_extra)
        protocol_output_limit = 4 * ((wire_bytes + 2) // 3) + 128
        result = await self._run(
            _workspace_python_argv(
                "read",
                path,
                str(offset),
                str(helper_read_limit),
                str(int(probe_extra)),
            ),
            timeout_ms=self.plan.limits.action_timeout_ms,
            output_limit=protocol_output_limit,
        )
        _require_workspace_command_success(result, operation="read")
        raw_output = result.get("stdout", "")
        if type(raw_output) is not str:
            raise DockerAdapterError(
                "runtime_protocol_error", "read returned malformed output"
            )
        try:
            payload = json.loads(raw_output)
            if type(payload) is not dict or set(payload) != {
                "bytes",
                "content_base64",
            }:
                raise ValueError("read payload fields are malformed")
            declared_bytes = payload["bytes"]
            encoded_content = payload["content_base64"]
            if type(declared_bytes) is not int or type(encoded_content) is not str:
                raise ValueError("read payload values are malformed")
            decoded_content = base64.b64decode(encoded_content, validate=True)
            if declared_bytes != len(decoded_content):
                raise ValueError("read payload byte count is malformed")
            content = decoded_content.decode("utf-8")
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise DockerAdapterError(
                "runtime_protocol_error", "read returned malformed output"
            ) from exc
        if len(decoded_content) > selected_limit:
            raise DockerAdapterError(
                "output_limit_exceeded", "read exceeds admitted ceiling"
            )
        return {
            "path": path,
            "content": content,
            "offset": offset,
            "bytes": len(decoded_content),
        }

    async def write_text(self, path: str, content: str) -> Mapping[str, Any]:
        if type(content) is not str:
            raise DockerAdapterError("runtime_preflight_failed", "write content is invalid")
        payload = content.encode("utf-8")
        if len(payload) > self.plan.limits.artifact_bytes_each:
            raise DockerAdapterError("output_limit_exceeded", "write exceeds admitted ceiling")
        result = await self._run(
            _workspace_python_argv(
                "write",
                path,
                str(len(payload)),
                hashlib.sha256(payload).hexdigest(),
            ),
            timeout_ms=self.plan.limits.action_timeout_ms,
            input_bytes=payload,
        )
        _require_workspace_command_success(result, operation="write")
        return {"path": path, "bytes": len(payload)}

    async def list_files(self, path: str, *, depth: int) -> Mapping[str, Any]:
        if type(depth) is not int or depth < 0 or depth > self.plan.security_policy.snapshot_max_depth:
            raise DockerAdapterError("output_limit_exceeded", "list depth exceeds admitted ceiling")
        result = await self._run(
            _workspace_python_argv(
                "list",
                path,
                str(depth),
                str(self.plan.security_policy.snapshot_max_inodes),
            ),
            timeout_ms=self.plan.limits.action_timeout_ms,
        )
        _require_workspace_command_success(result, operation="list")
        raw_output = result.get("stdout", "")
        if type(raw_output) is not str:
            raise DockerAdapterError(
                "runtime_protocol_error", "list returned malformed output"
            )
        try:
            decoded_values = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise DockerAdapterError(
                "runtime_protocol_error", "list returned malformed output"
            ) from exc
        if type(decoded_values) is not list or any(
            type(value) is not str for value in decoded_values
        ):
            raise DockerAdapterError(
                "runtime_protocol_error", "list returned malformed output"
            )
        values = sorted(decoded_values)
        if values != decoded_values or len(values) != len(set(values)):
            raise DockerAdapterError(
                "runtime_protocol_error", "list returned malformed output"
            )
        for value in values:
            try:
                _container_workspace_path(value)
            except DockerAdapterError as exc:
                raise DockerAdapterError(
                    "runtime_protocol_error", "list returned malformed paths"
                ) from exc
            if value == path or not value.startswith(f"{path}/"):
                raise DockerAdapterError(
                    "runtime_protocol_error",
                    "list returned paths outside its authority",
                )
        if len(values) > self.plan.security_policy.snapshot_max_inodes:
            raise DockerAdapterError(
                "output_limit_exceeded", "list exceeds admitted inode ceiling"
            )
        return {"path": path, "files": values}

    async def measure_repository_base_commit(self) -> str | None:
        if not callable(getattr(self.adapter, "exec", None)):
            return None
        repositories = tuple(
            entry
            for entry in self.plan.materialization_plan.entries
            if entry.role == "repository"
        )
        if len(repositories) > 1:
            raise DockerAdapterError(
                "runtime_preflight_failed",
                "workspace base measurement requires at most one repository mount",
            )
        relative_path = repositories[0].target_logical_path if repositories else "."
        repository_root = (
            _container_workspace_path(relative_path)
            if repositories
            else CONTAINER_WORKSPACE_ROOT
        )
        result = await self._run(
            ("git", "-C", repository_root, "rev-parse", "--verify", "HEAD^{commit}"),
            timeout_ms=self.plan.limits.action_timeout_ms,
            output_limit=256,
        )
        commit = result.get("stdout", "").strip()
        if result.get("returncode") != 0:
            return None
        if (
            len(commit) != 40
            or commit != commit.lower()
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise DockerAdapterError(
                "runtime_preflight_failed",
                "workspace base commit measurement failed",
            )
        self.repository_base_commit = commit
        self.repository_relative_path = relative_path
        return commit

    async def workspace_diff(self) -> Mapping[str, Any]:
        repositories = tuple(
            entry
            for entry in self.plan.materialization_plan.entries
            if entry.role == "repository"
        )
        if len(repositories) != 1:
            raise DockerAdapterError(
                "runtime_preflight_failed",
                "workspace diff requires exactly one repository mount",
            )
        repository_root = _container_workspace_path(
            repositories[0].target_logical_path
        )
        result = await self._run(
            ("git", "-C", repository_root, "diff", "--no-ext-diff", "--binary"),
            timeout_ms=self.plan.limits.action_timeout_ms,
        )
        if result.get("returncode") == 127:
            raise DockerAdapterError(
                "runtime_unsupported",
                "workspace diff requires git in the admitted image",
            )
        return result

    async def _terminate_bound(self) -> tuple[Any, ...]:
        from .materialization import CleanupState, CleanupStepReceipt
        try:
            reference = self.container_id or self.container_name
            raw = await self.adapter.cleanup(
                self.plan,
                reference,
                expected_id=self.container_id,
                expected_name=self.container_name,
                labels=self.labels,
            )
        except BaseException as exc:
            return (
                CleanupStepReceipt(
                    "runtime_identity",
                    CleanupState.QUARANTINED,
                    type(exc).__name__,
                ),
            )
        states = {
            "released": CleanupState.RELEASED,
            "already_released": CleanupState.ALREADY_RELEASED,
            "failed": CleanupState.FAILED,
            "quarantined": CleanupState.QUARANTINED,
        }
        return tuple(
            CleanupStepReceipt(name, states[state], detail) for name, state, detail in raw
        )

    async def _retry_cleanup(self) -> tuple[Any, ...]:
        from .materialization import CleanupState, CleanupStepReceipt

        async with self._cleanup_lock:
            if self._terminal_cleanup is not None:
                return self._terminal_cleanup
            receipts = await self._terminate_bound()
            states = {item.state for item in receipts}
            if (
                states <= {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                and self._staged_mounts
            ):
                release_failures: list[tuple[int, int, str]] = []
                failed_stages: list[StagedDockerDescriptorMount] = []
                if self._mount_stager is None:
                    failed_stages = list(reversed(self._staged_mounts))
                    release_failures = [
                        (
                            staged.source_device,
                            staged.source_inode,
                            "mount_stager_unavailable",
                        )
                        for staged in failed_stages
                    ]
                else:
                    for staged in reversed(self._staged_mounts):
                        try:
                            await self._mount_stager.release(staged)
                        except BaseException as exc:
                            failed_stages.append(staged)
                            release_failures.append(
                                (
                                    staged.source_device,
                                    staged.source_inode,
                                    type(exc).__name__,
                                )
                            )
                self._staged_mounts = list(reversed(failed_stages))
                receipts += (
                    CleanupStepReceipt(
                        "descriptor_staging",
                        (
                            CleanupState.QUARANTINED
                            if release_failures
                            else CleanupState.RELEASED
                        ),
                        (
                            json.dumps(
                                release_failures,
                                separators=(",", ":"),
                            )
                            if release_failures
                            else ""
                        ),
                    ),
                )
            if self._mount_stager is not None and self._lease_id is not None:
                released_states = {
                    CleanupState.RELEASED,
                    CleanupState.ALREADY_RELEASED,
                }
                runtime_absent = any(
                    receipt.resource == "runtime_absence"
                    and receipt.state in released_states
                    for receipt in receipts
                )
                stages_absent = not self._staged_mounts
                record_cleanup = getattr(
                    self._mount_stager, "record_cleanup_receipt", None
                )
                if callable(record_cleanup):
                    record_cleanup(
                        self._lease_id,
                        proof={
                            "container_absence": runtime_absent,
                            "stages_absence": stages_absent,
                            "daemon_absence": False,
                            "containerd_absence": False,
                            "runtime_absence": False,
                            "config_absence": False,
                            "root_absence": False,
                        },
                        state=(
                            "ACTIVE"
                            if runtime_absent and stages_absent
                            else "QUARANTINED"
                        ),
                    )
            self._record_terminal_cleanup(receipts)
            return receipts

    async def terminate(self) -> tuple[Any, ...]:
        from .materialization import CleanupState, CleanupStepReceipt
        if self._terminal_cleanup is not None:
            return self._terminal_cleanup
        if self._closed:
            return (CleanupStepReceipt("runtime", CleanupState.ALREADY_RELEASED),)
        return await self._retry_cleanup()


class DockerSandboxBackend:
    def __init__(self, *, adapter: DockerRuntimeAdapter, measurement_provider: DockerMeasurementProvider,
                 security_profile_root: str | Path,
                 mount_stager: DockerDescriptorMountStager | None = None,
                 skeleton_path: str | Path | None = None) -> None:
        self.adapter = adapter
        self.measurement_provider = measurement_provider
        self.mount_stager = mount_stager
        self.security_profile_root = Path(security_profile_root)
        if (
            not self.security_profile_root.is_absolute()
            or os.path.normpath(self.security_profile_root) != str(self.security_profile_root)
        ):
            raise ValueError("Docker security profile root must be absolute and normalized")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        self._security_root_fd = os.open(self.security_profile_root, flags)
        self._quarantined_fds: list[int] = []
        self._quarantined_handles: list[DockerRuntimeHandle] = []
        self._quarantined_stages: list[StagedDockerDescriptorMount] = []

    @property
    def cleanup_pending(self) -> bool:
        return bool(self._quarantined_handles or self._quarantined_stages)


    def close(self) -> None:
        if self._quarantined_handles or self._quarantined_stages:
            raise RuntimeError("Docker backend has quarantined runtime resources")
        while self._quarantined_fds:
            os.close(self._quarantined_fds.pop())
        if self._security_root_fd >= 0:
            os.close(self._security_root_fd)
            self._security_root_fd = -1

    async def _reconcile_quarantined(self) -> tuple[tuple[str, str], ...]:
        failures: list[tuple[str, str]] = []
        retained_handles: list[DockerRuntimeHandle] = []
        for handle in self._quarantined_handles:
            receipts = await handle.terminate()
            if not handle._closed:
                retained_handles.append(handle)
                failures.extend(
                    (receipt.resource, receipt.state.value)
                    for receipt in receipts
                    if receipt.state.value not in {"released", "already_released"}
                )
        self._quarantined_handles = retained_handles
        failed_stages: list[StagedDockerDescriptorMount] = []
        if self._quarantined_stages:
            if self.mount_stager is None:
                failed_stages = list(self._quarantined_stages)
                failures.extend(
                    ("descriptor_staging", "mount_stager_unavailable")
                    for _ in failed_stages
                )
            else:
                for staged in reversed(self._quarantined_stages):
                    try:
                        await self.mount_stager.release(staged)
                    except BaseException as exc:
                        failed_stages.append(staged)
                        failures.append(
                            (
                                f"descriptor_staging:{staged.source_device}:{staged.source_inode}",
                                type(exc).__name__,
                            )
                        )
        self._quarantined_stages = list(reversed(failed_stages))
        if not self._quarantined_stages:
            while self._quarantined_fds:
                os.close(self._quarantined_fds.pop())
        return tuple(failures)

    async def reconcile_quarantined(self) -> None:
        failures = await self._reconcile_quarantined()
        if failures:
            raise DockerAdapterError(
                "runtime_cleanup_pending",
                "Docker backend cleanup remains quarantined",
                details={"residuals": failures},
            )

    async def close_runtime(self) -> None:
        await self.reconcile_quarantined()
        self.close()

    def _security_profile(self, plan: Any) -> Path:
        name = plan.security_policy.seccomp_digest.removeprefix("sha256:") + ".json"
        expected = plan.security_policy.seccomp_bytes

        def read_installed() -> bytes:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._security_root_fd,
            )
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_size > len(expected)
                ):
                    raise DockerAdapterError(
                        "runtime_preflight_failed",
                        "installed seccomp profile is not a bounded regular file",
                    )
                chunks: list[bytes] = []
                remaining = len(expected) + 1
                while remaining and (chunk := os.read(descriptor, remaining)):
                    chunks.append(chunk)
                    remaining -= len(chunk)
                return b"".join(chunks)
            finally:
                os.close(descriptor)

        try:
            current = read_installed()
        except FileNotFoundError:
            directory = os.dup(self._security_root_fd)
            temporary = f".{name}.tmp-{os.getpid()}-{id(plan)}"
            descriptor = -1
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o400,
                    dir_fd=directory,
                )
                view = memoryview(expected)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise DockerAdapterError(
                            "runtime_preflight_failed", "short seccomp profile write"
                        )
                    view = view[written:]
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = -1
                os.replace(
                    temporary, name, src_dir_fd=directory, dst_dir_fd=directory
                )
                os.fsync(directory)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.close(directory)
            current = read_installed()
        except OSError as exc:
            raise DockerAdapterError(
                "runtime_preflight_failed", "installed seccomp profile is unavailable"
            ) from exc
        if (
            current != expected
            or "sha256:" + hashlib.sha256(current).hexdigest()
            != plan.security_policy.seccomp_digest
        ):
            raise DockerAdapterError("runtime_preflight_failed", "installed seccomp profile is tampered")
        return Path(name)

    @staticmethod
    def _validate_context(plan: Any, context: Any) -> None:
        from .sandbox import RuntimeLaunchContext, WorkspaceStorageIdentity
        if type(context) is not RuntimeLaunchContext or type(context.storage) is not WorkspaceStorageIdentity:
            raise DockerAdapterError("runtime_preflight_failed", "exact runtime launch context required")
        storage = context.storage
        if (
            type(storage.authority_id) is not str
            or not storage.authority_id
            or storage.quota_enforced is not True
            or type(storage.quota_bytes) is not int
            or storage.quota_bytes <= 0
            or type(storage.owner_uid) is not int
            or type(storage.owner_gid) is not int
        ):
            raise DockerAdapterError("runtime_preflight_failed", "workspace quota authority is not enforced")
        if context.role == "primary":
            valid = (
                context.snapshot_relative_path is None
                and context.result_relative_path is None
                and storage.quota_bytes == plan.resources.storage_bytes
            )
        elif context.role == "verifier":
            valid = (
                context.snapshot_relative_path == "snapshot"
                and context.result_relative_path == "result"
                and storage.quota_bytes
                <= min(plan.resources.storage_bytes, plan.limits.artifact_bytes_total)
            )
        else:
            valid = False
        if not valid:
            raise DockerAdapterError("runtime_preflight_failed", "runtime launch context is contradictory")

    @staticmethod
    def _mount_specs(plan: Any, context: Any) -> tuple[tuple[str, str, bool], ...]:
        if context.role == "verifier":
            return (
                (context.snapshot_relative_path, f"{CONTAINER_WORKSPACE_ROOT}/snapshot", True),
                (context.result_relative_path, f"{CONTAINER_WORKSPACE_ROOT}/result", False),
            )
        return tuple(
            (
                entry.target_logical_path,
                f"{CONTAINER_WORKSPACE_ROOT}/{entry.target_logical_path}",
                entry.access.value == "ro",
            )
            for entry in plan.materialization_plan.entries
        )
    async def launch(
        self, plan: Any, workspace: Path, *, context: Any
    ) -> tuple[DockerRuntimeHandle, Any]:
        from .sandbox import (
            IsolationDisposition,
            RuntimePreparedIdentity,
            SandboxMeasurement,
        )

        workspace_fd = getattr(context, "workspace_fd", None)
        workspace_identity = getattr(context, "workspace_identity", None)
        held_fds = (
            [workspace_fd]
            if type(workspace_fd) is int and workspace_fd >= 0
            else []
        )
        creation_attempted = False
        container_id: str | None = None
        container_name = ""
        labels: dict[str, str] = {}
        cleanup: tuple[tuple[str, str, str], ...] = ()
        staged_mounts: list[StagedDockerDescriptorMount] = []
        journal_bound = False
        try:
            residuals = await self._reconcile_quarantined()
            if residuals:
                raise DockerAdapterError(
                    "runtime_cleanup_pending",
                    "prior Docker cleanup remains quarantined",
                    details={"residuals": residuals},
                )
            self._validate_context(plan, context)
            _validate_lsm_policy(plan.security_policy)
            if (
                type(workspace_fd) is not int
                or workspace_fd < 0
                or workspace_identity is None
            ):
                raise DockerAdapterError(
                    "workspace_descriptor_required",
                    "pinned workspace descriptor required before Docker daemon access",
                )
            if sys.platform != "linux":
                raise DockerAdapterError(
                    "runtime_unsupported",
                    "Linux descriptor-derived Docker mounts are required",
                    details={"platform": sys.platform},
                )
            if self.mount_stager is None:
                raise DockerAdapterError(
                    "runtime_unsupported",
                    "private descriptor mount staging authority is required",
                    details={"reason": "descriptor_mount_staging_unavailable"},
                )
            container_name = _container_name(
                role=context.role, workspace_id=context.workspace_id
            )
            journal_binding = getattr(self.mount_stager, "record_lease_binding", None)
            if callable(journal_binding):
                if context.owner_token is None:
                    raise DockerAdapterError(
                        "runtime_preflight_failed",
                        "durable supervisor journal owner token is required",
                    )
                journal_binding(
                    lease_id=context.lease_id,
                    workspace_id=context.workspace_id,
                    epoch=context.epoch,
                    role=context.role,
                    plan_digest=plan.effective_plan_digest,
                    owner_token=context.owner_token,
                )
                journal_bound = True
            labels = _identity_labels(
                plan,
                lease_id=context.lease_id,
                workspace_id=context.workspace_id,
                epoch=context.epoch,
                role=context.role,
            )
            workspace_metadata = _validate_mount_descriptor(
                workspace_fd,
                workspace_device=workspace_identity[0],
                expected_identity=workspace_identity,
            )
            admitted_mounts: list[tuple[int, str, bool, os.stat_result]] = []
            for relative_path, destination, readonly in self._mount_specs(plan, context):
                child_fd = _openat2_beneath(workspace_fd, relative_path)
                held_fds.append(child_fd)
                child_metadata = _validate_mount_descriptor(
                    child_fd, workspace_device=workspace_metadata.st_dev
                )
                admitted_mounts.append(
                    (child_fd, destination, readonly, child_metadata)
                )
            observation = await self.adapter.preflight(plan)
            _require_daemon_runtime_binding(observation, plan)
            workspace_stage = await self.mount_stager.stage(
                workspace_fd,
                expected_device=workspace_metadata.st_dev,
                expected_inode=workspace_metadata.st_ino,
                directory=True,
                lease_id=context.lease_id,
                destination=CONTAINER_WORKSPACE_ROOT,
            )
            staged_mounts.append(workspace_stage)
            workspace_stage.validate_descriptor(workspace_fd)
            await self.mount_stager.validate(workspace_stage, workspace_fd)
            workspace_source = Path(workspace_stage.source_path)
            descriptor_mounts: list[tuple[Path, str, bool]] = []
            for child_fd, destination, readonly, child_metadata in admitted_mounts:
                staged = await self.mount_stager.stage(
                    child_fd,
                    expected_device=child_metadata.st_dev,
                    expected_inode=child_metadata.st_ino,
                    directory=stat.S_ISDIR(child_metadata.st_mode),
                    lease_id=context.lease_id,
                    destination=destination,
                )
                staged_mounts.append(staged)
                staged.validate_descriptor(child_fd)
                await self.mount_stager.validate(staged, child_fd)
                descriptor_mounts.append(
                    (Path(staged.source_path), destination, readonly)
                )
            installed_profile = self._security_profile(plan)
            profile_fd = _openat2_beneath(
                self._security_root_fd,
                installed_profile.name,
                readable_regular=True,
            )
            held_fds.append(profile_fd)
            profile_metadata = os.fstat(profile_fd)
            if not stat.S_ISREG(profile_metadata.st_mode) or profile_metadata.st_nlink != 1:
                raise DockerAdapterError(
                    "runtime_preflight_failed", "seccomp profile descriptor is not immutable authority"
                )
            profile_stage = await self.mount_stager.stage(
                profile_fd,
                expected_device=profile_metadata.st_dev,
                expected_inode=profile_metadata.st_ino,
                directory=False,
                lease_id=context.lease_id,
                destination="/.breadboard/seccomp",
            )
            staged_mounts.append(profile_stage)
            profile_stage.validate_descriptor(profile_fd)
            await self.mount_stager.validate(profile_stage, profile_fd)
            profile_source = Path(profile_stage.source_path)
            for staged, descriptor in zip(
                staged_mounts,
                (workspace_fd, *(item[0] for item in admitted_mounts), profile_fd),
                strict=True,
            ):
                staged.validate_descriptor(descriptor)
                await self.mount_stager.validate(staged, descriptor)
            creation_attempted = True
            container_id, container_name, _ = await self.adapter.prepare(
                plan, lease_id=context.lease_id, workspace_id=context.workspace_id,
                epoch=context.epoch, role=context.role, skeleton_path=workspace_source,
                mounts=tuple(descriptor_mounts), security_profile_path=profile_source,
                security_profile_descriptor=profile_fd,
                security_profile_metadata=profile_metadata,
            )
            journal_container = getattr(
                self.mount_stager, "record_container_identity", None
            )
            if callable(journal_container):
                journal_container(
                    context.lease_id,
                    container_id=container_id,
                    name=container_name,
                    labels=labels,
                )
            identity_labels = tuple((key, labels[key]) for key in _IDENTITY_LABELS)
            await context.publish_prepared_identity(
                RuntimePreparedIdentity(container_id, labels)
            )
            await self.adapter.start(plan, container_id)
            inspect_payload = await self.adapter.inspect(plan, container_id)
            for staged, descriptor in zip(
                staged_mounts,
                (workspace_fd, *(item[0] for item in admitted_mounts), profile_fd),
                strict=True,
            ):
                staged.validate_descriptor(descriptor)
                await self.mount_stager.validate(staged, descriptor)
            measured = decode_docker_inspect(
                inspect_payload, plan, container_id=container_id,
                container_name=container_name, labels=labels,
                skeleton_path=workspace_source, mounts=tuple(descriptor_mounts),
                security_profile_path=profile_source,
                storage_bytes=context.storage.quota_bytes,
            )
            effective = dict(measured)
            external = dict(await self.measurement_provider.measure(
                plan, container_name, inspect_payload
            ))
            unknown = set(external) - set(measured)
            if unknown:
                raise DockerAdapterError(
                    "runtime_measurement_mismatch",
                    "measurement provider returned unknown controls",
                    details={"unknown": tuple(sorted(unknown))},
                )
            measured.update(external)
            identity = (container_id, container_name, identity_labels)
            requested = requested_measurement(
                plan, tuple(descriptor_mounts),
                storage_bytes=context.storage.quota_bytes, identity=identity,
            )
            storage_identity = (
                context.storage.authority_id,
                context.storage.quota_enforced,
                context.storage.quota_bytes,
                context.storage.owner_uid,
                context.storage.owner_gid,
            )
            requested["workspace_root"] = str(workspace_source)
            requested["storage_identity"] = storage_identity
            measured["storage_identity"] = storage_identity
            effective["storage_identity"] = storage_identity
            handle = DockerRuntimeHandle(
                adapter=self.adapter, plan=plan, container_id=container_id,
                container_name=container_name, labels=labels, held_fds=held_fds,
                mount_stager=self.mount_stager, staged_mounts=staged_mounts,
                lease_id=context.lease_id,
            )
            repository_base_commit = await handle.measure_repository_base_commit()
            if repository_base_commit is not None:
                requested["workspace_base_commit"] = repository_base_commit
                effective["workspace_base_commit"] = repository_base_commit
                measured["workspace_base_commit"] = repository_base_commit
            mismatch = tuple(sorted({
                *measurement_mismatches(requested, effective),
                *measurement_mismatches(requested, measured),
            }))
            if mismatch:
                raise DockerAdapterError(
                    "runtime_measurement_mismatch",
                    "effective Docker controls contradict requested controls",
                    details={"mismatch": mismatch},
                )
            held_fds = []
            return handle, SandboxMeasurement(
                effective_plan_digest=plan.effective_plan_digest,
                lease_id=context.lease_id, workspace_id=context.workspace_id,
                runtime_id=plan.runtime.runtime_id,
                runtime_class=plan.runtime.runtime_class.value,
                driver_binary_digest=plan.runtime.measured_binary_digest,
                image_digest=plan.image.image_digest, requested=requested,
                effective=effective, measured=measured,
                runtime_resource_id=container_id, mismatch=(),
                isolation_disposition=IsolationDisposition.ISOLATED,
                isolated=True, reward_eligible=True,
            )
        except BaseException as primary:
            if isinstance(primary, DockerAdapterError):
                candidate = primary.details.get("candidate_container_id")
                if (
                    type(candidate) is str
                    and _CONTAINER_ID.fullmatch(candidate) is not None
                ):
                    container_id = candidate
                reported_cleanup = primary.details.get("cleanup")
                if (
                    type(reported_cleanup) in {list, tuple}
                    and all(
                        type(item) in {list, tuple}
                        and len(item) == 3
                        and all(type(value) is str for value in item)
                        for item in reported_cleanup
                    )
                ):
                    cleanup = tuple(tuple(item) for item in reported_cleanup)
            if container_id is not None:
                try:
                    cleanup = await self.adapter.cleanup(
                        plan, container_id, expected_id=container_id,
                        expected_name=container_name, labels=labels,
                    )
                except BaseException as cleanup_error:
                    cleanup = (
                        ("runtime_identity", "quarantined", type(cleanup_error).__name__),
                    )
                if isinstance(primary, DockerAdapterError):
                    primary.details["cleanup"] = cleanup
            absence = any(
                name == "runtime_absence"
                and state in {"released", "already_released"}
                for name, state, _ in cleanup
            )
            runtime_cleanup_pending = creation_attempted and not absence
            stage_cleanup_pending = False
            if runtime_cleanup_pending:
                self._quarantined_handles.append(
                    DockerRuntimeHandle(
                        adapter=self.adapter,
                        plan=plan,
                        container_id=container_id,
                        container_name=container_name,
                        labels=labels,
                        held_fds=held_fds,
                        mount_stager=self.mount_stager,
                        staged_mounts=staged_mounts,
                        lease_id=context.lease_id,
                    )
                )
                held_fds = []
                staged_mounts = []
                if isinstance(primary, DockerAdapterError):
                    primary.details["pending_runtime_cleanup"] = (
                        container_id or container_name
                    )
            else:
                release_failures: list[tuple[int, int, str]] = []
                failed_stages: list[StagedDockerDescriptorMount] = []
                while staged_mounts:
                    staged = staged_mounts.pop()
                    try:
                        await self.mount_stager.release(staged)
                    except BaseException as release_error:
                        failed_stages.append(staged)
                        release_failures.append(
                            (
                                staged.source_device,
                                staged.source_inode,
                                type(release_error).__name__,
                            )
                        )
                if failed_stages:
                    stage_cleanup_pending = True
                    self._quarantined_stages.extend(reversed(failed_stages))
                    self._quarantined_fds.extend(held_fds)
                    held_fds = []
                    if isinstance(primary, DockerAdapterError):
                        primary.details["staged_mount_cleanup"] = tuple(
                            release_failures
                        )
            journal_cleanup = getattr(self.mount_stager, "record_cleanup_receipt", None)
            if journal_bound and callable(journal_cleanup):
                container_absent = not creation_attempted or absence
                stages_absent = (
                    not runtime_cleanup_pending
                    and not stage_cleanup_pending
                    and not staged_mounts
                )
                try:
                    journal_cleanup(
                        context.lease_id,
                        proof={
                            "container_absence": container_absent,
                            "stages_absence": stages_absent,
                            "daemon_absence": False,
                            "containerd_absence": False,
                            "runtime_absence": False,
                            "config_absence": False,
                            "root_absence": False,
                        },
                        state=(
                            "ACTIVE"
                            if container_absent and stages_absent
                            else "QUARANTINED"
                        ),
                    )
                except BaseException as journal_error:
                    if isinstance(primary, DockerAdapterError):
                        primary.details["cleanup_journal"] = type(
                            journal_error
                        ).__name__
            raise
        finally:
            while held_fds:
                os.close(held_fds.pop())


    async def reconcile(self, record: Mapping[str, Any]) -> tuple[Any, ...]:
        from .materialization import CleanupState, CleanupStepReceipt
        # Legacy records contain only pathname/digest observations.  They do not
        # carry a durable daemon binding proving which OCI executable dockerd
        # consumed, so executing any CLI command could invoke unadmitted code.
        return (
            CleanupStepReceipt(
                "runtime",
                CleanupState.QUARANTINED,
                "runtime_identity=quarantined:stale_identity_uncertain",
            ),
        )


__all__ = ["DockerAdapterError", "DockerCliExecutor", "SubprocessDockerCliExecutor", "DockerCommandResult", "ExecutableInvocation",
           "DockerPreflightObservation", "PrivateDockerDaemonBinding",
           "StagedDockerDescriptorMount", "DockerDescriptorMountStager",
           "DockerRuntimeAdapter",
           "DockerMeasurementProvider", "InspectDockerMeasurementProvider",
           "DockerRuntimeHandle", "DockerSandboxBackend",
           "build_create_argv", "decode_docker_inspect", "observe_binary_digest",
           "measurement_mismatches", "requested_measurement"]
