from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import stat
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

from .sandbox_docker import (
    DockerCommandResult,
    ExecutableInvocation,
    PrivateDockerDaemonBinding,
    SubprocessDockerCliExecutor,
)

_SHA256_PREFIX = "sha256:"
_BUFFER_SIZE = 1024 * 1024
_COMMAND_OUTPUT_LIMIT = 4 * 1024 * 1024


class PrivateDockerDaemonError(RuntimeError):
    """Fail-closed private-daemon construction or lifecycle failure."""

    def __init__(
        self, code: str, message: str, *, details: Mapping[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = MappingProxyType(dict(details or {}))


@dataclass(frozen=True, slots=True)
class PinnedFileAuthority:
    path: str
    digest: str
    owner_uid: int
    mode: int
    executable: bool

    def __post_init__(self) -> None:
        _absolute(self.path)
        _sha256(self.digest)
        if type(self.owner_uid) is not int or self.owner_uid < 0:
            raise ValueError("file authority owner is invalid")
        if type(self.mode) is not int or not 0 <= self.mode <= 0o7777:
            raise ValueError("file authority mode is invalid")
        if type(self.executable) is not bool:
            raise ValueError("file executable authority is invalid")
        if self.executable != bool(self.mode & 0o111):
            raise ValueError("file executable authority contradicts its mode")


@dataclass(frozen=True, slots=True)
class PinnedFileObservation:
    path: str
    digest: str
    device: int
    inode: int
    ctime_ns: int
    size: int
    owner_uid: int
    mode: int
    executable: bool


@dataclass(frozen=True, slots=True)
class OfflineImageAuthority:
    archive: PinnedFileAuthority
    image_id: str
    source_image_digest: str

    def __post_init__(self) -> None:
        _sha256(self.image_id)
        _sha256(self.source_image_digest)


@dataclass(frozen=True, slots=True)
class PrivateContainerdObservation:
    pid: int
    starttime: str
    pid_namespace: str
    socket_path: str
    socket_mode: int
    socket_uid: int
    socket_gid: int
    socket_device: int
    socket_inode: int
    ttrpc_socket_path: str
    ttrpc_socket_mode: int
    ttrpc_socket_uid: int
    ttrpc_socket_gid: int
    ttrpc_socket_device: int
    ttrpc_socket_inode: int
    executable_digest: str
    executable_device: int
    executable_inode: int
    executable_ctime_ns: int
    executable_size: int


@dataclass(frozen=True, slots=True)
class PrivateDockerDaemonAuthority:
    daemon_instance_id: str
    dockerd: PinnedFileAuthority
    docker: PinnedFileAuthority
    runc: PinnedFileAuthority
    containerd: PinnedFileAuthority
    config_path: str
    socket_path: str
    pid_file: str
    data_root: str
    exec_root: str
    containerd_socket_path: str
    mount_stage_root: str
    containerd_root: str
    containerd_state: str
    log_root: str
    log_limit_bytes: int
    storage_driver: str
    runtime_name: str
    images: tuple[OfflineImageAuthority, ...] = ()

    def __post_init__(self) -> None:
        if not self.daemon_instance_id or type(self.daemon_instance_id) is not str:
            raise ValueError("daemon instance id is required")
        for value in (
            self.config_path,
            self.socket_path,
            self.pid_file,
            self.data_root,
            self.exec_root,
            self.containerd_socket_path,
            self.containerd_root,
            self.containerd_state,
            self.mount_stage_root,
            self.log_root,
        ):
            _absolute(value)
        if self.storage_driver not in {"vfs", "overlay2"}:
            raise ValueError("private daemon storage driver is unsupported")
        if self.runtime_name != "breadboard-runc":
            raise ValueError("private daemon runtime name must be breadboard-runc")
        if (
            type(self.log_limit_bytes) is not int
            or not 4096 <= self.log_limit_bytes <= 1024 * 1024
        ):
            raise ValueError("private daemon log bound is invalid")
        paths = {
            self.config_path,
            self.socket_path,
            self.pid_file,
            self.containerd_socket_path,
            self.containerd_ttrpc_socket_path,
            self.containerd_root,
            self.containerd_state,
            self.data_root,
            self.exec_root,
            self.mount_stage_root,
            self.log_root,
        }
        if len(paths) != 11:
            raise ValueError("private daemon paths must be distinct")
        output_parents = {os.path.dirname(path) for path in paths}
        if len(output_parents) != 1:
            raise ValueError(
                "private daemon outputs must be exact children of one authority root"
            )
        ids = tuple(image.image_id for image in self.images)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("offline image authorities must be sorted and unique")

    @property
    def containerd_ttrpc_socket_path(self) -> str:
        return self.containerd_socket_path + ".ttrpc"

    @property
    def daemon_root(self) -> str:
        return os.path.dirname(self.config_path)


@dataclass(frozen=True, slots=True)
class DaemonLogReceipt:
    role: str
    path: str
    argv: tuple[str, ...]
    pid: int | None
    returncode: int | None
    size_bytes: int
    sha256: str
    mode: int
    bytes_base64: str
    output_limited: bool


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limited: bool = False


class DaemonProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def send_signal(self, sig: int) -> None: ...
    def kill(self) -> None: ...


class ProcessLauncher(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        executable: str,
        pass_fds: Sequence[int],
        env: Mapping[str, str],
        log_fd: int,
        log_limit_bytes: int,
    ) -> DaemonProcess: ...


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        executable: str,
        pass_fds: Sequence[int],
        env: Mapping[str, str],
        timeout: float,
    ) -> CommandResult: ...


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("path must be absolute and normalized")
    return value


def _sha256(value: str) -> str:
    if (
        type(value) is not str
        or not value.startswith(_SHA256_PREFIX)
        or len(value) != 71
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("digest must be lowercase sha256")
    return value


def _digest_fd(fd: int) -> str:
    hasher = hashlib.sha256()
    offset = 0
    while chunk := os.pread(fd, _BUFFER_SIZE, offset):
        hasher.update(chunk)
        offset += len(chunk)
    return _SHA256_PREFIX + hasher.hexdigest()


def _proc_path(fd: int) -> str:
    return f"/proc/{os.getpid()}/fd/{fd}"


def _runtime_registration_evidence(
    info: Mapping[str, object], runtime_name: str, expected_path: str
) -> tuple[str, str]:
    if info.get("DefaultRuntime") != runtime_name:
        raise ValueError("private Docker default runtime is not exact")
    runtimes = info.get("Runtimes")
    registration = (
        runtimes.get(runtime_name) if type(runtimes) is dict else None
    )
    if type(registration) is not dict:
        raise ValueError("private Docker runtime registration is absent")
    if set(registration) - {"path", "runtimeArgs", "status"}:
        raise ValueError("private Docker runtime keys are not exact")
    if registration.get("path") != expected_path:
        raise ValueError("private Docker runtime path is not exact")
    if "runtimeArgs" in registration:
        arguments = registration["runtimeArgs"]
        if type(arguments) is not list or arguments:
            raise ValueError("private Docker runtime arguments are not exact")
    status = registration.get("status")
    if (
        type(status) is not dict
        or len(status) > 32
        or any(
            type(key) is not str
            or not key
            or len(key) > 256
            or "\x00" in key
            or type(value) is not str
            or len(value) > 64 * 1024
            or "\x00" in value
            for key, value in status.items()
        )
    ):
        raise ValueError("private Docker runtime status is not bounded")
    status_digest = _SHA256_PREFIX + hashlib.sha256(
        json.dumps(
            status, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("ascii")
    ).hexdigest()
    return expected_path, status_digest


class _CapturedProcess:
    def __init__(
        self, process: subprocess.Popen[bytes], log_fd: int, limit: int
    ) -> None:
        self._process = process
        self.output_limited = False
        self.pid = process.pid
        self._thread = threading.Thread(
            target=self._capture,
            args=(process.stdout, log_fd, limit, self),
            name=f"private-daemon-log-{process.pid}",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _capture(
        stream: Any, log_fd: int, limit: int, capture: "_CapturedProcess"
    ) -> None:
        written = 0
        while chunk := stream.read(64 * 1024):
            remaining = limit - written
            if remaining <= 0:
                capture.output_limited = True
                continue
            if len(chunk) > remaining:
                capture.output_limited = True
            payload = memoryview(chunk)[:remaining]
            while payload:
                count = os.write(log_fd, payload)
                payload = payload[count:]
                written += count

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        result = self._process.wait(timeout=timeout)
        self._thread.join(timeout=timeout)
        return result

    def send_signal(self, sig: int) -> None:
        os.killpg(self.pid, sig)

    def kill(self) -> None:
        os.killpg(self.pid, signal.SIGKILL)


def _default_launcher(
    argv: Sequence[str],
    *,
    executable: str,
    pass_fds: Sequence[int],
    env: Mapping[str, str],
    log_fd: int,
    log_limit_bytes: int,
) -> DaemonProcess:
    process = subprocess.Popen(
        tuple(argv),
        executable=executable,
        pass_fds=tuple(pass_fds),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
    )
    return _CapturedProcess(process, log_fd, log_limit_bytes)


def _default_runner(
    argv: Sequence[str],
    *,
    executable: str,
    pass_fds: Sequence[int],
    env: Mapping[str, str],
    timeout: float,
) -> CommandResult:
    process = subprocess.Popen(
        tuple(argv),
        executable=executable,
        pass_fds=tuple(pass_fds),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate(timeout=5.0)
    combined = len(stdout) + len(stderr)
    output_limited = combined > _COMMAND_OUTPUT_LIMIT
    if output_limited:
        stdout = stdout[:_COMMAND_OUTPUT_LIMIT]
        remaining = _COMMAND_OUTPUT_LIMIT - len(stdout)
        stderr = stderr[:max(0, remaining)]
    return CommandResult(
        process.returncode, stdout, stderr,
        timed_out=timed_out, output_limited=output_limited,
    )


def _process_starttime(pid: int) -> str:
    payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    suffix = payload[payload.rindex(")") + 2 :].split()
    if len(suffix) < 20 or not suffix[19].isdecimal():
        raise PrivateDockerDaemonError("runtime_unsupported", "daemon process stat is incomplete")
    return suffix[19]


def _require_target_prerequisites() -> None:
    if os.geteuid() != 0:
        raise PrivateDockerDaemonError("runtime_unsupported", "private rootful Docker requires effective uid 0")
    try:
        controllers = Path("/sys/fs/cgroup/cgroup.controllers").read_bytes()
        apparmor = Path("/sys/module/apparmor/parameters/enabled").read_text(encoding="ascii").strip()
        proc_namespace = os.readlink(f"/proc/{os.getpid()}/ns/pid")
    except OSError as exc:
        raise PrivateDockerDaemonError("runtime_unsupported", "frozen private Docker prerequisites are absent") from exc
    if not controllers.strip() or apparmor != "Y" or not proc_namespace.startswith("pid:["):
        raise PrivateDockerDaemonError("runtime_unsupported", "cgroup v2, AppArmor, and procfs are required")

class _OwnerDockerCliExecutor:
    def __init__(self, owner: "PrivateDockerDaemonOwner") -> None:
        self._owner = owner
        self._delegate = SubprocessDockerCliExecutor()

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
        self._owner._assert_docker_cli()
        expected = self._owner.docker_invocation
        if executable != expected:
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "Docker CLI invocation authority changed"
            )
        return await self._delegate.execute(
            executable,
            argv_tail,
            timeout_ms=timeout_ms,
            output_limit=output_limit,
            environment=environment,
            input_bytes=input_bytes,
        )


class PrivateDockerDaemonOwner:
    """Owns one rootful dockerd and every descriptor in its injected authority."""

    def __init__(
        self,
        authority: PrivateDockerDaemonAuthority,
        *,
        daemon_environment: Mapping[str, str] | None = None,
        launcher: ProcessLauncher = _default_launcher,
        runner: CommandRunner = _default_runner,
        prerequisite_check: Callable[[], None] = _require_target_prerequisites,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        pinned_fds: Mapping[str, int] | None = None,
        runtime_registration_path: str | None = None,
        runtime_effective_fd: int | None = None,
        export_log_fds: bool = False,
        progress_sink: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        prerequisite_check()
        self.authority = authority
        self._launcher = launcher
        self._daemon_environment = (
            None if daemon_environment is None else dict(daemon_environment)
        )
        if self._daemon_environment is not None:
            if (
                set(self._daemon_environment) != {"PATH"}
                or not _absolute(self._daemon_environment["PATH"])
                or ":" in self._daemon_environment["PATH"]
            ):
                raise ValueError("private daemon environment must be one exact PATH")
        self._runner = runner
        self._monotonic = monotonic
        self._progress_sink = progress_sink
        if type(export_log_fds) is not bool:
            raise TypeError("export_log_fds must be bool")
        self._export_log_fds = export_log_fds
        self._exported_log_fds: tuple[int, ...] = ()
        self._logs_detached = False
        self._startup_deadline: float | None = None
        self._sleep = sleep
        self._injected_fds = dict(pinned_fds or {})
        self._runtime_registration_path = (
            None
            if runtime_registration_path is None
            else _absolute(runtime_registration_path)
        )
        self._fds: dict[str, int] = {}
        self._file_observations: dict[str, PinnedFileObservation] = {}
        self._process: DaemonProcess | None = None
        self._binding: PrivateDockerDaemonBinding | None = None
        self._config_digest: str | None = None
        self._containerd_process: DaemonProcess | None = None
        self._containerd_observation: PrivateContainerdObservation | None = None
        self._quarantined = False
        self._log_receipts: dict[str, DaemonLogReceipt] = {}
        self._launch_argv: dict[str, tuple[str, ...]] = {}
        self._closed = False
        self._cleanup_file_identities: dict[str, tuple[int, int]] = {}
        self._emit_progress("owner_init", "begin")
        try:
            self._fds["dockerd"] = self._pin_file("dockerd", authority.dockerd)
            self._fds["docker"] = self._pin_file("docker", authority.docker)
            self._fds["containerd"] = self._pin_file("containerd", authority.containerd)
            self._fds["runc"] = self._pin_file("runc", authority.runc)
            if runtime_effective_fd is not None:
                effective_fd = os.dup(runtime_effective_fd)
                effective = os.fstat(effective_fd)
                if (
                    not stat.S_ISREG(effective.st_mode)
                    or stat.S_IMODE(effective.st_mode) != authority.runc.mode
                    or effective.st_size != os.fstat(self._fds["runc"]).st_size
                    or _digest_fd(effective_fd) != authority.runc.digest
                ):
                    os.close(effective_fd)
                    raise PrivateDockerDaemonError(
                        "runtime_unsupported",
                        "effective private runtime snapshot is not exact",
                    )
                self._fds["runtime-effective"] = effective_fd
            for index, image in enumerate(authority.images):
                name = f"image:{index}"
                self._fds[name] = self._pin_file(name, image.archive)
            self._prepare_owned_paths()
            self._open_logs()
            self._seal_config()
            self._emit_progress("owner_init", "end", {
                "config_digest": self._config_digest,
                "runtime_registered_path": (
                    self._runtime_registration_path
                    or _proc_path(self._fds["runc"])
                ),
            })
        except BaseException:
            try:
                self.close()
            except BaseException:
                self._close_fds()
            raise

    def _emit_progress(
        self,
        event: str,
        phase: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        sink = self._progress_sink
        if sink is None:
            return
        payload = MappingProxyType({
            "event": event,
            "phase": phase,
            "monotonic_ns": int(self._monotonic() * 1_000_000_000),
            "details": MappingProxyType(dict(details or {})),
        })
        try:
            sink(payload)
        except BaseException as exc:
            self._quarantined = True
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "private daemon progress journal failed"
            ) from exc

    def _remaining_startup(self, requested: float) -> float:
        deadline = self._startup_deadline
        if deadline is None:
            return requested
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            self._quarantine("private daemon constructor deadline exceeded")
        return min(requested, remaining)

    @property
    def docker_invocation(self) -> ExecutableInvocation:
        fd = self._fds["docker"]
        return ExecutableInvocation(
            argv0=self.authority.docker.path,
            executable_fd=fd,
            executable_descriptor_path=_proc_path(fd),
            digest=self.authority.docker.digest,
        )

    @property
    def docker_cli_executor(self) -> _OwnerDockerCliExecutor:
        return _OwnerDockerCliExecutor(self)

    @property
    def file_observations(self) -> Mapping[str, PinnedFileObservation]:
        return MappingProxyType(dict(self._file_observations))

    @property
    def binding(self) -> PrivateDockerDaemonBinding:
        if self._binding is None or self._closed or self._quarantined:
            raise PrivateDockerDaemonError("runtime_unsupported", "private Docker daemon authority is not live")
        self._assert_live()
        return self._binding


    @property
    def log_receipts(self) -> Mapping[str, DaemonLogReceipt]:
        return MappingProxyType(dict(self._log_receipts))
    def detach_log_fds(self) -> tuple[int, int]:
        if (
            not self._closed
            or not self._export_log_fds
            or self._logs_detached
            or len(self._exported_log_fds) != 2
        ):
            raise PrivateDockerDaemonError(
                "runtime_unsupported",
                "private daemon log descriptor export is unavailable",
            )
        exported = self._exported_log_fds
        self._exported_log_fds = ()
        self._logs_detached = True
        return exported  # type: ignore[return-value]

    def _assert_docker_cli(self) -> None:
        fd = self._fds.get("docker", -1)
        try:
            metadata = os.fstat(fd)
        except OSError as exc:
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "Docker CLI descriptor is unavailable"
            ) from exc
        observation = self._file_observations["docker"]
        if (
            (metadata.st_dev, metadata.st_ino, metadata.st_ctime_ns, metadata.st_size)
            != (
                observation.device,
                observation.inode,
                observation.ctime_ns,
                observation.size,
            )
            or _digest_fd(fd) != observation.digest
        ):
            self._quarantined = True
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "Docker CLI descriptor authority drifted"
            )

    @property
    def containerd_observation(self) -> PrivateContainerdObservation:
        if self._containerd_observation is None or self._closed or self._quarantined:
            raise PrivateDockerDaemonError("runtime_unsupported", "private containerd authority is not live")
        self._assert_containerd_live()
        return self._containerd_observation

    def _pin_file(self, name: str, authority: PinnedFileAuthority) -> int:
        injected = self._injected_fds.get(name)
        if injected is None:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(authority.path, flags)
        else:
            fd = os.dup(injected)
        try:
            metadata = os.fstat(fd)
            digest = _digest_fd(fd)
            mode = stat.S_IMODE(metadata.st_mode)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != authority.owner_uid
                or mode != authority.mode
                or bool(mode & 0o111) != authority.executable
                or digest != authority.digest
            ):
                raise PrivateDockerDaemonError("runtime_unsupported", "private Docker file authority mismatch")
            current = os.stat(authority.path, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise PrivateDockerDaemonError("runtime_unsupported", "private Docker path drifted while pinning")
            self._file_observations[name] = PinnedFileObservation(
                path=authority.path,
                digest=digest,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                ctime_ns=metadata.st_ctime_ns,
                size=metadata.st_size,
                owner_uid=metadata.st_uid,
                mode=mode,
                executable=authority.executable,
            )
            os.set_inheritable(fd, True)
            return fd
        except BaseException:
            os.close(fd)
            raise
    def _prepare_owned_paths(self) -> None:
        authority = self.authority
        for absent in (
            authority.config_path,
            authority.socket_path,
            authority.pid_file,
            authority.containerd_socket_path,
        ):
            if os.path.lexists(absent):
                raise PrivateDockerDaemonError(
                    "runtime_unsupported", "private daemon output path already exists"
                )
        for root in (
            authority.data_root,
            authority.exec_root,
            authority.containerd_root,
            authority.containerd_state,
            authority.log_root,
        ):
            os.mkdir(root, 0o700)
            metadata = os.stat(root, follow_symlinks=False)
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise PrivateDockerDaemonError("runtime_unsupported", "private daemon root mode is not exact")
            root_fd = os.open(
                root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            self._fds["root:" + root] = root_fd
        for parent in {
            os.path.dirname(authority.config_path),
            os.path.dirname(authority.socket_path),
            os.path.dirname(authority.containerd_socket_path),
            os.path.dirname(authority.pid_file),
        }:
            metadata = os.stat(parent, follow_symlinks=False)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise PrivateDockerDaemonError("runtime_unsupported", "private daemon parent is not owner-sealed")

    def _open_logs(self) -> None:
        for role in ("containerd", "dockerd"):
            path = os.path.join(self.authority.log_root, role + ".log")
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            fd = os.open(path, flags, 0o600)
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()
            ):
                os.close(fd)
                raise PrivateDockerDaemonError(
                    "runtime_unsupported", "private daemon log authority is unsafe"
                )
            self._fds["log:" + role] = fd

    def _log_receipt(self, role: str) -> DaemonLogReceipt:
        fd = self._fds["log:" + role]
        os.fsync(fd)
        metadata = os.fstat(fd)
        process = (
            self._containerd_process if role == "containerd" else self._process
        )
        payload = bytearray()
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(
                fd, min(_BUFFER_SIZE, metadata.st_size - offset), offset
            )
            if not chunk:
                raise PrivateDockerDaemonError(
                    "runtime_unsupported", "private daemon log was short-read"
                )
            payload.extend(chunk)
            offset += len(chunk)
        output_limited = bool(getattr(process, "output_limited", False))
        receipt = DaemonLogReceipt(
            role=role,
            path=os.path.join(self.authority.log_root, role + ".log"),
            argv=self._launch_argv.get(role, ()),
            pid=None if process is None else process.pid,
            returncode=None if process is None else process.poll(),
            size_bytes=metadata.st_size,
            sha256=_digest_fd(fd),
            mode=stat.S_IMODE(metadata.st_mode),
            bytes_base64=base64.b64encode(payload).decode("ascii"),
            output_limited=output_limited,
        )
        if receipt.size_bytes > self.authority.log_limit_bytes:
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "private daemon log exceeded its bound"
            )
        self._log_receipts[role] = receipt
        return receipt
    def _config_bytes(self) -> bytes:
        authority = self.authority
        document = {
            "bridge": "none",
            "data-root": authority.data_root,
            "default-runtime": authority.runtime_name,
            "containerd": authority.containerd_socket_path,
            "exec-root": authority.exec_root,
            "hosts": ["unix://" + authority.socket_path],
            "ip-forward": False,
            "ip-masq": False,
            "ip6tables": False,
            "iptables": False,
            "live-restore": False,
            "log-driver": "none",
            "no-new-privileges": True,
            "pidfile": authority.pid_file,
            "runtimes": {
                authority.runtime_name: {
                    "path": self._runtime_registration_path
                    or _proc_path(self._fds["runc"])
                }
            },
            "storage-driver": authority.storage_driver,
            "userland-proxy": False,
        }
        return json.dumps(document, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("ascii")

    def _seal_config(self) -> None:
        payload = self._config_bytes()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        write_fd = os.open(self.authority.config_path, flags, 0o600)
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(write_fd, payload[offset:])
            os.fsync(write_fd)
        finally:
            os.close(write_fd)
        config_metadata = os.stat(self.authority.config_path, follow_symlinks=False)
        config_authority = PinnedFileAuthority(
            path=self.authority.config_path,
            digest=_SHA256_PREFIX + hashlib.sha256(payload).hexdigest(),
            owner_uid=config_metadata.st_uid,
            mode=stat.S_IMODE(config_metadata.st_mode),
            executable=False,
        )
        self._fds["config"] = self._pin_file("config", config_authority)
        self._config_digest = config_authority.digest
        self._cleanup_file_identities[self.authority.config_path] = (
            config_metadata.st_dev,
            config_metadata.st_ino,
        )
    def start(self, *, readiness_timeout: float = 30.0) -> PrivateDockerDaemonBinding:
        if self._closed or self._quarantined or self._process is not None:
            raise PrivateDockerDaemonError("runtime_unsupported", "private daemon owner cannot be restarted")
        if self._daemon_environment is None:
            self._quarantine("private daemon runtime PATH authority is unavailable")
        if readiness_timeout <= 0:
            raise ValueError("private daemon constructor timeout must be positive")
        self._startup_deadline = self._monotonic() + readiness_timeout
        self._emit_progress("containerd_start", "begin")
        containerd_fd = self._fds["containerd"]
        containerd_argv = (
            self.authority.containerd.path,
            "--address",
            self.authority.containerd_socket_path,
            "--root",
            self.authority.containerd_root,
            "--state",
            self.authority.containerd_state,
        )
        self._launch_argv["containerd"] = tuple(containerd_argv)
        self._containerd_process = self._launcher(
            containerd_argv,
            executable=_proc_path(containerd_fd),
            pass_fds=tuple(self._fds.values()),
            env=self._daemon_environment,
            log_fd=self._fds["log:containerd"],
            log_limit_bytes=self.authority.log_limit_bytes,
        )
        self._emit_progress(
            "containerd_spawned", "end",
            {"pid": self._containerd_process.pid},
        )
        deadline = self._startup_deadline
        containerd_paths = (
            self.authority.containerd_socket_path,
            self.authority.containerd_ttrpc_socket_path,
        )
        while not all(os.path.exists(path) for path in containerd_paths):
            if self._containerd_process.poll() is not None:
                self._quarantine("private containerd exited before readiness")
            if self._monotonic() >= deadline:
                self._quarantine("private containerd readiness timed out")
            self._sleep(0.05)
        self._emit_progress("containerd_socket_ready", "end")
        for path in containerd_paths:
            os.chmod(path, 0o600, follow_symlinks=False)
        containerd_socket = os.stat(
            self.authority.containerd_socket_path, follow_symlinks=False
        )
        ttrpc_socket = os.stat(
            self.authority.containerd_ttrpc_socket_path, follow_symlinks=False
        )
        containerd_executable = os.fstat(containerd_fd)
        if any(
            not stat.S_ISSOCK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            for metadata in (containerd_socket, ttrpc_socket)
        ):
            self._quarantine("private containerd socket is not exact")
        self._cleanup_file_identities.update(
            {
                self.authority.containerd_socket_path: (
                    containerd_socket.st_dev,
                    containerd_socket.st_ino,
                ),
                self.authority.containerd_ttrpc_socket_path: (
                    ttrpc_socket.st_dev,
                    ttrpc_socket.st_ino,
                ),
            }
        )
        self._containerd_observation = PrivateContainerdObservation(
            pid=self._containerd_process.pid,
            starttime=_process_starttime(self._containerd_process.pid),
            pid_namespace=os.readlink(
                f"/proc/{self._containerd_process.pid}/ns/pid"
            ),
            socket_path=self.authority.containerd_socket_path,
            socket_mode=stat.S_IMODE(containerd_socket.st_mode),
            socket_uid=containerd_socket.st_uid,
            socket_gid=containerd_socket.st_gid,
            socket_device=containerd_socket.st_dev,
            socket_inode=containerd_socket.st_ino,
            ttrpc_socket_path=self.authority.containerd_ttrpc_socket_path,
            ttrpc_socket_mode=stat.S_IMODE(ttrpc_socket.st_mode),
            ttrpc_socket_uid=ttrpc_socket.st_uid,
            ttrpc_socket_gid=ttrpc_socket.st_gid,
            ttrpc_socket_device=ttrpc_socket.st_dev,
            ttrpc_socket_inode=ttrpc_socket.st_ino,
            executable_digest=self.authority.containerd.digest,
            executable_device=containerd_executable.st_dev,
            executable_inode=containerd_executable.st_ino,
            executable_ctime_ns=containerd_executable.st_ctime_ns,
            executable_size=containerd_executable.st_size,
        )
        dockerd_fd = self._fds["dockerd"]
        config_fd = self._fds["config"]
        argv = (self.authority.dockerd.path, "--config-file", _proc_path(config_fd))
        self._launch_argv["dockerd"] = tuple(argv)
        self._emit_progress("dockerd_start", "begin")
        self._process = self._launcher(
            argv,
            executable=_proc_path(dockerd_fd),
            pass_fds=tuple(self._fds.values()),
            env=self._daemon_environment,
            log_fd=self._fds["log:dockerd"],
            log_limit_bytes=self.authority.log_limit_bytes,
        )
        self._emit_progress(
            "dockerd_spawned", "end", {"pid": self._process.pid}
        )
        deadline = self._startup_deadline
        while True:
            if self._process.poll() is not None:
                self._quarantine("private dockerd exited before readiness")
            try:
                result = self._docker(("info", "--format", "{{json .}}"), timeout=min(readiness_timeout, 5.0))
                self._emit_progress("docker_info_attempt", "end", {
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "output_limited": result.output_limited,
                })
                if result.returncode == 0:
                    info = json.loads(result.stdout)
                    containerd = info.get("Containerd") if type(info) is dict else None
                    expected_runtime = (
                        self._runtime_registration_path
                        or _proc_path(self._fds["runc"])
                    )
                    registration = (
                        info.get("Runtimes", {}).get(self.authority.runtime_name)
                        if type(info) is dict
                        and type(info.get("Runtimes")) is dict
                        else None
                    )
                    advertised_runtime = (
                        registration.get("path")
                        if type(registration) is dict
                        else None
                    )
                    try:
                        _advertised, status_digest = (
                            _runtime_registration_evidence(
                                info, self.authority.runtime_name,
                                expected_runtime,
                            )
                        )
                    except ValueError:
                        self._emit_progress(
                            "runtime_registration", "error", {
                                "advertised_path": advertised_runtime,
                                "expected_path": expected_runtime,
                                "config_digest": self._config_digest,
                            },
                        )
                        self._quarantine(
                            "private Docker runtime registration is not exact"
                        )
                    self._emit_progress("runtime_registration", "observe", {
                        "advertised_path": advertised_runtime,
                        "expected_path": expected_runtime,
                        "status_digest": status_digest,
                        "config_digest": self._config_digest,
                    })
                    if (
                        type(info) is dict
                        and info.get("DockerRootDir") == self.authority.data_root
                        and info.get("Driver") == self.authority.storage_driver
                        and type(containerd) is dict
                        and containerd.get("Address")
                        == self.authority.containerd_socket_path
                        and advertised_runtime == expected_runtime
                    ):
                        break
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            if self._monotonic() >= deadline:
                self._quarantine("private dockerd readiness timed out")
            self._sleep(0.05)
        self._assert_docker_cli()
        os.chmod(self.authority.socket_path, 0o600, follow_symlinks=False)
        self._emit_progress("dockerd_ready", "end")
        self._emit_progress("probe_run", "end")
        self._binding = self._observe_binding()
        socket_metadata = os.stat(
            self.authority.socket_path, follow_symlinks=False
        )
        pid_metadata = os.stat(
            self.authority.pid_file, follow_symlinks=False
        )
        if not stat.S_ISSOCK(socket_metadata.st_mode) or not stat.S_ISREG(
            pid_metadata.st_mode
        ):
            self._quarantine("private dockerd cleanup authority is not exact")
        self._cleanup_file_identities.update(
            {
                self.authority.socket_path: (
                    socket_metadata.st_dev,
                    socket_metadata.st_ino,
                ),
                self.authority.pid_file: (
                    pid_metadata.st_dev,
                    pid_metadata.st_ino,
                ),
            }
        )
        for index, image in enumerate(self.authority.images):
            self._emit_progress(
                "image_load", "begin", {"index": index}
            )
            self._load_image(index, image)
            self._emit_progress(
                "image_load", "end", {"index": index}
            )
        self._startup_deadline = None
        return self.binding

    def _docker(self, tail: Sequence[str], *, timeout: float) -> CommandResult:
        self._assert_docker_cli()
        docker_fd = self._fds["docker"]
        argv = (
            self.authority.docker.path,
            "--host",
            "unix://" + self.authority.socket_path,
            *tuple(tail),
        )
        return self._runner(
            argv,
            executable=_proc_path(docker_fd),
            pass_fds=tuple(self._fds.values()),
            env={},
            timeout=self._remaining_startup(timeout),
        )

    def _observe_binding(self) -> PrivateDockerDaemonBinding:
        assert self._process is not None and self._config_digest is not None
        socket_metadata = os.stat(self.authority.socket_path, follow_symlinks=False)
        daemon_metadata = os.fstat(self._fds["dockerd"])
        config_metadata = os.fstat(self._fds["config"])
        runtime_fd = self._fds.get("runtime-effective", self._fds["runc"])
        runtime_metadata = os.fstat(runtime_fd)
        if not stat.S_ISSOCK(socket_metadata.st_mode) or stat.S_IMODE(socket_metadata.st_mode) != 0o600:
            raise PrivateDockerDaemonError("runtime_unsupported", "private daemon socket identity is unsafe")
        return PrivateDockerDaemonBinding(
            daemon_instance_id=self.authority.daemon_instance_id,
            socket_path=self.authority.socket_path,
            socket_device=socket_metadata.st_dev,
            socket_inode=socket_metadata.st_ino,
            socket_mode=stat.S_IMODE(socket_metadata.st_mode),
            socket_uid=socket_metadata.st_uid,
            socket_gid=socket_metadata.st_gid,
            daemon_pid=self._process.pid,
            daemon_starttime=_process_starttime(self._process.pid),
            daemon_executable_ctime_ns=daemon_metadata.st_ctime_ns,
            daemon_executable_size=daemon_metadata.st_size,
            daemon_pid_namespace=os.readlink(f"/proc/{self._process.pid}/ns/pid"),
            daemon_executable_digest=self.authority.dockerd.digest,
            daemon_executable_device=daemon_metadata.st_dev,
            daemon_executable_inode=daemon_metadata.st_ino,
            data_root=self.authority.data_root,
            config_fd=self._fds["config"],
            config_ctime_ns=config_metadata.st_ctime_ns,
            config_size=config_metadata.st_size,
            config_proc_path=_proc_path(self._fds["config"]),
            daemon_config_digest=self._config_digest,
            config_device=config_metadata.st_dev,
            config_inode=config_metadata.st_ino,
            runtime_fd=runtime_fd,
            runtime_ctime_ns=runtime_metadata.st_ctime_ns,
            runtime_size=runtime_metadata.st_size,
            runtime_proc_path=_proc_path(runtime_fd),
            runtime_registered_path=(
                self._runtime_registration_path
                or _proc_path(runtime_fd)
            ),
            runtime_digest=self.authority.runc.digest,
            runtime_device=runtime_metadata.st_dev,
            runtime_inode=runtime_metadata.st_ino,
        )

    def _load_image(self, index: int, authority: OfflineImageAuthority) -> None:
        archive_fd = self._fds[f"image:{index}"]
        loaded = self._docker(
            ("image", "load", "--input", _proc_path(archive_fd)), timeout=1800.0
        )
        if loaded.returncode != 0:
            self._quarantine(
                "offline Docker image import failed",
                failure_details={
                    "index": index,
                    "returncode": loaded.returncode,
                    "stdout": loaded.stdout.decode("utf-8", "replace")[:4096],
                    "stderr": loaded.stderr.decode("utf-8", "replace")[:4096],
                    "timed_out": loaded.timed_out,
                    "output_limited": loaded.output_limited,
                },
            )
        self._emit_progress(
            "image_inspect", "begin", {"index": index}
        )
        inspected = self._docker(
            ("image", "inspect", "--format", "{{json .}}", authority.image_id),
            timeout=30.0,
        )
        self._emit_progress("image_inspect", "end", {
            "index": index,
            "returncode": inspected.returncode,
            "timed_out": inspected.timed_out,
            "output_limited": inspected.output_limited,
        })
        try:
            document = json.loads(inspected.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        graph = document.get("GraphDriver") if type(document) is dict else None
        if (
            inspected.returncode != 0
            or type(document) is not dict
            or document.get("Id") != authority.image_id
            or type(graph) is not dict
            or graph.get("Name") != self.authority.storage_driver
        ):
            self._quarantine(
                "offline Docker image content or storage identity mismatch"
            )

    def _assert_containerd_live(self) -> None:
        process = self._containerd_process
        observation = self._containerd_observation
        if process is None or observation is None or process.poll() is not None:
            self._quarantine("private containerd crashed")
        executable_fd = -1
        try:
            socket_metadata = os.stat(
                observation.socket_path, follow_symlinks=False
            )
            ttrpc_metadata = os.stat(
                observation.ttrpc_socket_path, follow_symlinks=False
            )
            executable_fd = os.open(
                f"/proc/{observation.pid}/exe",
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
            executable_metadata = os.fstat(executable_fd)
            if (
                not stat.S_ISSOCK(socket_metadata.st_mode)
                or stat.S_IMODE(socket_metadata.st_mode) != observation.socket_mode
                or socket_metadata.st_uid != observation.socket_uid
                or socket_metadata.st_gid != observation.socket_gid
                or (socket_metadata.st_dev, socket_metadata.st_ino)
                != (observation.socket_device, observation.socket_inode)
                or not stat.S_ISSOCK(ttrpc_metadata.st_mode)
                or stat.S_IMODE(ttrpc_metadata.st_mode)
                != observation.ttrpc_socket_mode
                or ttrpc_metadata.st_uid != observation.ttrpc_socket_uid
                or ttrpc_metadata.st_gid != observation.ttrpc_socket_gid
                or (ttrpc_metadata.st_dev, ttrpc_metadata.st_ino)
                != (
                    observation.ttrpc_socket_device,
                    observation.ttrpc_socket_inode,
                )
                or (
                    executable_metadata.st_dev,
                    executable_metadata.st_ino,
                    executable_metadata.st_ctime_ns,
                    executable_metadata.st_size,
                )
                != (
                    observation.executable_device,
                    observation.executable_inode,
                    observation.executable_ctime_ns,
                    observation.executable_size,
                )
                or _digest_fd(executable_fd) != observation.executable_digest
                or _process_starttime(observation.pid) != observation.starttime
                or os.readlink(f"/proc/{observation.pid}/ns/pid")
                != observation.pid_namespace
            ):
                raise OSError("private containerd authority changed")
        except OSError as exc:
            self._quarantined = True
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "private containerd authority drifted"
            ) from exc
        finally:
            if executable_fd >= 0:
                os.close(executable_fd)

    def _assert_live(self) -> None:
        self._assert_containerd_live()
        if self._process is None or self._process.poll() is not None:
            self._quarantine("private dockerd crashed")
        assert self._binding is not None
        try:
            self._binding.validate_live()
            config_metadata = os.fstat(self._fds["config"])
            current = os.stat(self.authority.config_path, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (
                config_metadata.st_dev,
                config_metadata.st_ino,
            ):
                raise OSError("config authority changed")
            if _digest_fd(self._fds["config"]) != self._binding.daemon_config_digest:
                raise OSError("config authority content changed")
        except BaseException as exc:
            self._quarantined = True
            raise PrivateDockerDaemonError(
                "runtime_unsupported", "private daemon authority drifted"
            ) from exc

    def _quarantine(
        self,
        message: str,
        *,
        failure_details: Mapping[str, object] | None = None,
    ) -> None:
        self._quarantined = True
        self._emit_progress("owner", "error", {"message": message})
        receipts: list[dict[str, object]] = []
        for role in ("containerd", "dockerd"):
            if "log:" + role not in self._fds:
                continue
            try:
                receipts.append(asdict(self._log_receipt(role)))
            except BaseException as exc:
                receipts.append({"role": role, "capture_error": str(exc)})
        details: dict[str, object] = {"daemon_logs": receipts}
        if failure_details is not None:
            details["failure"] = dict(failure_details)
        raise PrivateDockerDaemonError(
            "runtime_unsupported",
            message,
            details=details,
        )

    def descriptor_mount_source(self, _fd: int) -> str:
        raise PrivateDockerDaemonError(
            "runtime_unsupported",
            "descriptor-derived shared procfd bind mounts are not stable on the frozen target",
        )

    @staticmethod
    def _empty_directory(fd: int) -> None:
        for name in os.listdir(fd):
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
                try:
                    PrivateDockerDaemonOwner._empty_directory(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)

    @staticmethod
    def _stop_process(
        process: DaemonProcess | None,
        *,
        label: str,
        errors: list[BaseException],
    ) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGTERM)
        except BaseException as exc:
            errors.append(exc)
        try:
            process.wait(timeout=10.0)
            return
        except subprocess.TimeoutExpired:
            pass
        except BaseException as exc:
            errors.append(exc)
        try:
            process.kill()
        except BaseException as exc:
            errors.append(exc)
        try:
            process.wait(timeout=5.0)
        except BaseException as exc:
            errors.append(exc)
        if process.poll() is None:
            errors.append(OSError(f"private {label} survived bounded cleanup"))

    def _remove_owned_file(self, path: str) -> None:
        try:
            current = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return
        expected = self._cleanup_file_identities.get(path)
        if expected is None or (current.st_dev, current.st_ino) != expected:
            raise PrivateDockerDaemonError(
                "runtime_unsupported",
                "private daemon cleanup file identity drifted",
                details={"path": path},
            )
        os.unlink(path)

    def _remove_owned_root(self, root: str) -> None:
        try:
            current = os.stat(root, follow_symlinks=False)
        except FileNotFoundError:
            return
        root_fd = self._fds["root:" + root]
        held = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise PrivateDockerDaemonError(
                "runtime_unsupported",
                "private daemon cleanup root drifted",
                details={"path": root},
            )
        self._empty_directory(root_fd)
        after = os.stat(root, follow_symlinks=False)
        if (after.st_dev, after.st_ino) != (held.st_dev, held.st_ino):
            raise PrivateDockerDaemonError(
                "runtime_unsupported",
                "private daemon cleanup root substituted",
                details={"path": root},
            )
        os.rmdir(root)
    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        try:
            self._emit_progress("close", "begin")
        except BaseException as exc:
            errors.append(exc)
        self._stop_process(self._process, label="dockerd", errors=errors)
        try:
            self._emit_progress("close", "process")
        except BaseException as exc:
            errors.append(exc)
        self._stop_process(
            self._containerd_process,
            label="containerd",
            errors=errors,
        )
        try:
            self._emit_progress("close", "containerd")
        except BaseException as exc:
            errors.append(exc)
        for role in ("containerd", "dockerd"):
            if "log:" + role not in self._fds:
                continue
            try:
                self._log_receipt(role)
            except BaseException as exc:
                errors.append(exc)
        if (
            self._export_log_fds
            and not self._exported_log_fds
            and not errors
        ):
            exported: list[int] = []
            try:
                for role in ("containerd", "dockerd"):
                    source = self._fds["log:" + role]
                    duplicate = os.dup(source)
                    os.set_inheritable(duplicate, False)
                    held = os.fstat(source)
                    copied = os.fstat(duplicate)
                    if (
                        held.st_dev,
                        held.st_ino,
                        held.st_mode,
                        held.st_size,
                    ) != (
                        copied.st_dev,
                        copied.st_ino,
                        copied.st_mode,
                        copied.st_size,
                    ):
                        raise OSError("private daemon log duplicate changed")
                    exported.append(duplicate)
                self._exported_log_fds = tuple(exported)
            except BaseException as exc:
                for duplicate in exported:
                    os.close(duplicate)
                errors.append(exc)
        owned_files = (
            self.authority.socket_path,
            self.authority.containerd_socket_path,
            self.authority.containerd_ttrpc_socket_path,
            self.authority.pid_file,
            self.authority.config_path,
        )
        for path in owned_files:
            try:
                self._remove_owned_file(path)
            except BaseException as exc:
                errors.append(exc)
        owned_roots = (
            self.authority.exec_root,
            self.authority.data_root,
            self.authority.containerd_root,
            self.authority.containerd_state,
            self.authority.log_root,
        )
        for root in owned_roots:
            try:
                self._remove_owned_root(root)
            except BaseException as exc:
                errors.append(exc)
        for path in (*owned_files, *owned_roots):
            try:
                present = os.path.lexists(path)
            except BaseException as exc:
                errors.append(exc)
                continue
            if present:
                errors.append(
                    PrivateDockerDaemonError(
                        "runtime_unsupported",
                        "private daemon cleanup absence failed",
                        details={"path": path},
                    )
                )
        try:
            self._emit_progress("close", "end")
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise BaseExceptionGroup(
                "private Docker daemon cleanup failed",
                errors,
            )
        self._close_fds()
        self._closed = True

    def _close_fds(self) -> None:
        for fd in self._fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()


__all__ = [
    "CommandResult",
    "DaemonLogReceipt",
    "OfflineImageAuthority",
    "PinnedFileAuthority",
    "PinnedFileObservation",
    "PrivateContainerdObservation",
    "PrivateDockerDaemonAuthority",
    "PrivateDockerDaemonError",
    "PrivateDockerDaemonOwner",
]
