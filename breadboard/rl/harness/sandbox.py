from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import selectors
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol, Sequence

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes

from .contracts import (
    EffectiveExecutionPlan,
    RegistrySnapshotSet,
    RuntimeClass,
    SandboxBinding,
    SetupRegistryRecord,
    VerifierGrant,
)
from .materialization import (
    CleanupState,
    CleanupStepReceipt,
    FilesystemMaterializationStore,
    IsolationDisposition,
    MaterializationEntry,
    MaterializedWorkspace,
    SandboxCleanupReceipt,
    VerifierSnapshotReceipt,
    WorkspaceLeaseState,
    WorkspaceMaterializationPlan,
    WorkspaceOpenRequest,
)
from .runners.base import (
    JsonSnapshotError,
    RunnerToolBinding,
    freeze_json_object,
)
from .sandbox_docker import VERIFIER_RESULT_MAX_BYTES

VERIFIER_REQUEST_RELATIVE_PATH = "input/verifier-request.json"
VERIFIER_REQUEST_SCHEMA_VERSION = "bb.rl.verifier-request.v1"
SANDBOX_CAPABILITY_MATRIX_RESOURCE = "SANDBOX_CAPABILITY_MATRIX.json"
SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION = "bb.rl.sandbox-capability-matrix.v1"
SANDBOX_CAPABILITY_MATRIX_SHA256 = (
    "996389caba529c555c1d6755aeda0727ade5d29ae7fd83b0e1da51643dee7538"
)
_MAX_SANDBOX_CAPABILITY_MATRIX_BYTES = 64 * 1024
_SANDBOX_ADAPTER_STATUSES = {
    "docker": "experimental",
    "firecracker": "unsupported",
    "gvisor": "experimental",
    "process": "development_only",
}
_SANDBOX_CAPABILITY_KEYS = {
    "create",
    "execute",
    "file_access",
    "workspace_diff",
    "cancel",
    "destroy",
    "identity",
    "cleanup_receipt",
    "persistent_workspace",
    "isolated",
}


def _read_sandbox_capability_matrix_resource() -> bytes:
    resource = files(__package__).joinpath(SANDBOX_CAPABILITY_MATRIX_RESOURCE)
    limit = _MAX_SANDBOX_CAPABILITY_MATRIX_BYTES
    if isinstance(resource, Path):
        expected = os.stat(resource, follow_symlinks=False)
        if (
            not stat.S_ISREG(expected.st_mode)
            or expected.st_size > limit
        ):
            raise OSError("sandbox capability matrix resource is not regular and bounded")
        descriptor = os.open(
            resource,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size > limit
                or (opened.st_dev, opened.st_ino)
                != (expected.st_dev, expected.st_ino)
            ):
                raise OSError(
                    "sandbox capability matrix resource identity changed"
                )
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
    else:
        chunks = []
        remaining = limit + 1
        with resource.open("rb") as stream:
            while remaining:
                chunk = stream.read(min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
    encoded_matrix = b"".join(chunks)
    if len(encoded_matrix) > limit:
        raise OSError("sandbox capability matrix resource exceeds admitted size")
    return encoded_matrix


def load_sandbox_capability_matrix() -> Mapping[str, Any]:
    """Load and validate the installed canonical sandbox capability matrix."""
    try:
        encoded_matrix = _read_sandbox_capability_matrix_resource()
        if hashlib.sha256(encoded_matrix).hexdigest() != SANDBOX_CAPABILITY_MATRIX_SHA256:
            raise SandboxRuntimeError(
                "sandbox capability matrix digest is invalid",
                code="capability_matrix_invalid",
            )
        payload = json.loads(encoded_matrix.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SandboxRuntimeError(
            "sandbox capability matrix is unavailable",
            code="capability_matrix_invalid",
        ) from exc
    if (
        type(payload) is not dict
        or set(payload)
        != {
            "schema_version",
            "workspace_root",
            "verifier_result_max_bytes",
            "adapters",
        }
        or payload.get("schema_version") != SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION
        or payload.get("workspace_root") != "/testbed"
        or payload.get("verifier_result_max_bytes") != VERIFIER_RESULT_MAX_BYTES
        or type(payload.get("adapters")) is not list
    ):
        raise SandboxRuntimeError(
            "sandbox capability matrix is invalid",
            code="capability_matrix_invalid",
        )
    adapters = payload["adapters"]
    if [item.get("adapter_id") for item in adapters if type(item) is dict] != list(
        _SANDBOX_ADAPTER_STATUSES
    ):
        raise SandboxRuntimeError(
            "sandbox capability matrix adapters are invalid",
            code="capability_matrix_invalid",
        )
    for adapter in adapters:
        if (
            type(adapter) is not dict
            or set(adapter)
            != {
                "adapter_id",
                "status",
                "capabilities",
                "required_host_capabilities",
                "required_image_capabilities",
                "unavailable_code",
                "evidence_contracts",
            }
            or adapter["status"]
            != _SANDBOX_ADAPTER_STATUSES.get(adapter["adapter_id"])
            or type(adapter["capabilities"]) is not dict
            or set(adapter["capabilities"]) != _SANDBOX_CAPABILITY_KEYS
            or any(type(value) is not bool for value in adapter["capabilities"].values())
            or type(adapter["required_host_capabilities"]) is not list
            or any(
                type(value) is not str or not value
                for value in adapter["required_host_capabilities"]
            )
            or type(adapter["required_image_capabilities"]) is not list
            or any(
                type(value) is not str or not value
                for value in adapter["required_image_capabilities"]
            )
            or type(adapter["evidence_contracts"]) is not list
            or any(
                type(value) is not str or not value
                for value in adapter["evidence_contracts"]
            )
            or adapter["unavailable_code"] != "runtime_unsupported"
        ):
            raise SandboxRuntimeError(
                "sandbox capability matrix adapter is invalid",
                code="capability_matrix_invalid",
            )
    return freeze_json_object(
        payload,
        field_name="sandbox capability matrix",
        max_depth=8,
        max_nodes=256,
        max_encoded_bytes=64 * 1024,
    )


def _wp7_digest(value: Any) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _seal_tree_at(parent_fd: int, name: str) -> None:
    directory_fd = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    try:
        for child in os.listdir(directory_fd):
            metadata = os.stat(child, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                _seal_tree_at(directory_fd, child)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                os.chmod(child, 0o400, dir_fd=directory_fd, follow_symlinks=False)
            else:
                raise OSError("snapshot contains unsupported inode")
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    os.chmod(name, 0o500, dir_fd=parent_fd, follow_symlinks=False)


def _workspace_parts(logical_path: str) -> tuple[str, ...]:
    relative = Path(logical_path)
    if (
        not logical_path
        or relative.is_absolute()
        or "\x00" in logical_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("workspace path escapes")
    return relative.parts


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        return os.open(name, flags, dir_fd=parent_fd)


def _open_parent_descriptor(root: Path, parts: tuple[str, ...], *, create: bool) -> tuple[int, str]:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            child = _open_directory_at(descriptor, component, create=create)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _open_parent_descriptor_fd(root_fd: int, parts: tuple[str, ...], *, create: bool) -> tuple[int, str]:
    descriptor = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            child = _open_directory_at(descriptor, component, create=create)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _bounded_regular_read(root: Path | int, logical_path: str, *, offset: int, limit: int) -> bytes:
    parts = _workspace_parts(logical_path)
    parent_fd, name = (
        _open_parent_descriptor_fd(root, parts, create=False)
        if isinstance(root, int)
        else _open_parent_descriptor(root, parts, create=False)
    )
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("workspace result is not a regular single-link file")
        return os.pread(descriptor, limit, offset)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _runtime_cleanup_released(steps: Sequence[CleanupStepReceipt]) -> bool:
    return bool(steps) and all(
        step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
        for step in steps
    )


def _atomic_regular_write(root: Path, logical_path: str, payload: bytes) -> None:
    parts = _workspace_parts(logical_path)
    parent_fd, name = _open_parent_descriptor(root, parts, create=True)
    temporary = f".{name}.tmp-{uuid.uuid4().hex}"
    descriptor = -1
    try:
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            file_type = __import__("stat")
            if not file_type.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise OSError("workspace target is not a regular single-link file")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short workspace write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _descriptor_list(
    root: Path,
    logical_path: str,
    *,
    depth: int,
    max_entries: int,
    output_limit: int,
) -> list[str]:
    parts = _workspace_parts(logical_path)
    parent_fd, name = _open_parent_descriptor(root, parts, create=False)
    descriptor = -1
    values: list[str] = []
    charged_entries = 0
    charged_name_bytes = 0
    file_type = __import__("stat")

    def walk(directory_fd: int, prefix: tuple[str, ...], level: int) -> None:
        nonlocal charged_entries, charged_name_bytes
        names: list[str] = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                charged_entries += 1
                charged_name_bytes += len(entry.name.encode("utf-8", "surrogateescape"))
                if charged_entries > max_entries or charged_name_bytes > output_limit:
                    raise OverflowError("workspace listing exceeds admitted bounds")
                names.append(entry.name)
        directories: list[tuple[str, os.stat_result]] = []
        for child_name in sorted(names):
            before = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
            relative_parts = prefix + (child_name,)
            relative = Path(*relative_parts).as_posix()
            values.append(relative)
            if len(canonical_json_bytes(values)) > output_limit:
                raise OverflowError("workspace listing exceeds admitted output")
            if file_type.S_ISDIR(before.st_mode):
                directories.append((child_name, before))
            elif not file_type.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise OSError("workspace listing contains an unauthorized node")
        if level >= depth:
            return
        for child_name, before in directories:
            child_fd = _open_directory_at(directory_fd, child_name, create=False)
            try:
                after = os.fstat(child_fd)
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                ):
                    raise OSError("workspace directory identity changed")
                walk(child_fd, prefix + (child_name,), level + 1)
            finally:
                os.close(child_fd)

    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = _open_directory_at(parent_fd, name, create=False)
        after = os.fstat(descriptor)
        if (
            not file_type.S_ISDIR(before.st_mode)
            or (before.st_dev, before.st_ino, before.st_mode)
            != (after.st_dev, after.st_ino, after.st_mode)
        ):
            raise OSError("workspace listing root identity changed")
        walk(descriptor, parts, 0)
        return values
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _exact_sha256_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _exact_absolute_path(value: object) -> bool:
    if type(value) is not str or not value.startswith("/") or "\x00" in value:
        return False
    return all(component not in {"", ".", ".."} for component in value.split("/")[1:])


def _open_installed_regular(path: str) -> int:
    if not _exact_absolute_path(path):
        raise OSError("installed executable path is not lexically exact")
    directory_fd = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        components = path.split("/")[1:]
        for component in components[:-1]:
            child_fd = os.open(
                component,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            metadata = os.fstat(child_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child_fd)
                raise OSError("installed executable ancestor is not a directory")
            os.close(directory_fd)
            directory_fd = child_fd
        descriptor = os.open(
            components[-1],
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError("installed executable is not a regular file")
        return descriptor
    finally:
        os.close(directory_fd)


@dataclass(slots=True)
class _PinnedExecutable:
    fd: int
    source_path: str
    digest: str
    size: int
    source_device: int
    source_inode: int
    snapshot_device: int
    snapshot_inode: int
    proc_fd_path: str
    closed: bool = False

    def close(self) -> None:
        if not self.closed:
            os.close(self.fd)
            self.closed = True


def _snapshot_installed_executable(
    path: str, expected_digest: str | None
) -> _PinnedExecutable:
    required_fcntl = (
        "F_ADD_SEALS",
        "F_GET_SEALS",
        "F_SEAL_WRITE",
        "F_SEAL_SHRINK",
        "F_SEAL_GROW",
        "F_SEAL_SEAL",
    )
    if (
        sys.platform != "linux"
        or not hasattr(os, "memfd_create")
        or not hasattr(os, "MFD_ALLOW_SEALING")
        or any(not hasattr(fcntl, name) for name in required_fcntl)
        or not os.path.isdir("/proc/self/fd")
    ):
        raise SandboxLaunchError(
            "sealed executable snapshots are unavailable",
            code="runtime_unsupported",
        )
    source_fd = snapshot_fd = -1
    try:
        source_fd = _open_installed_regular(path)
        source = os.fstat(source_fd)
        snapshot_fd = os.memfd_create(
            "breadboard-runtime",
            getattr(os, "MFD_CLOEXEC", 0) | os.MFD_ALLOW_SEALING,
        )
        hasher = __import__("hashlib").sha256()
        size = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(snapshot_fd, view)
                if written <= 0:
                    raise OSError("short executable snapshot write")
                view = view[written:]
        digest = "sha256:" + hasher.hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise SandboxLaunchError(
                "trusted process executable identity mismatch",
                code="runtime_preflight_failed",
            )
        os.fchmod(snapshot_fd, 0o500)
        os.lseek(snapshot_fd, 0, os.SEEK_SET)
        seals = (
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SEAL
        )
        fcntl.fcntl(snapshot_fd, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(snapshot_fd, fcntl.F_GET_SEALS) & seals != seals:
            raise OSError("executable snapshot sealing was incomplete")
        snapshot = os.fstat(snapshot_fd)
        proc_fd_path = f"/proc/self/fd/{snapshot_fd}"
        proc_snapshot = os.stat(proc_fd_path)
        if (proc_snapshot.st_dev, proc_snapshot.st_ino) != (
            snapshot.st_dev,
            snapshot.st_ino,
        ):
            raise OSError("descriptor namespace does not identify the snapshot")
        pinned = _PinnedExecutable(
            fd=snapshot_fd,
            source_path=path,
            digest=digest,
            size=size,
            source_device=source.st_dev,
            source_inode=source.st_ino,
            snapshot_device=snapshot.st_dev,
            snapshot_inode=snapshot.st_ino,
            proc_fd_path=proc_fd_path,
        )
        snapshot_fd = -1
        return pinned
    except SandboxLaunchError:
        raise
    except OSError as exc:
        raise SandboxLaunchError(
            "trusted process executable snapshot failed",
            code="runtime_preflight_failed",
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if snapshot_fd >= 0:
            os.close(snapshot_fd)


@dataclass(frozen=True, slots=True)
class InstalledRuntime:
    runtime_id: str
    runtime_class: RuntimeClass
    driver_implementation_digest: str
    executable_path: str
    measured_binary_digest: str
    oci_runtime_name: str
    supported_platform_versions: tuple[str, ...]
    fixed_environment: tuple[tuple[str, str], ...] = ()
    idle_argv: tuple[str, ...] = ("sh", "-lc", "trap : TERM INT; sleep infinity & wait")
    runsc_binary_path: str | None = None
    runsc_binary_digest: str | None = None
    oci_runtime_binary_path: str | None = None
    oci_runtime_binary_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.runtime_class) is not RuntimeClass
            or not _exact_absolute_path(self.executable_path)
            or not _exact_sha256_digest(self.measured_binary_digest)
        ):
            raise ValueError("runtime authority requires an exact class, path, and digest")
        hardened = self.runtime_class in {
            RuntimeClass.HARDENED_DOCKER,
            RuntimeClass.HARDENED_GVISOR,
        }
        oci_identity_valid = (
            _exact_absolute_path(self.oci_runtime_binary_path)
            and _exact_sha256_digest(self.oci_runtime_binary_digest)
        )
        if hardened and (not self.oci_runtime_name or not oci_identity_valid):
            raise ValueError("hardened runtime requires an exact OCI binary authority")
        if not hardened and (
            self.oci_runtime_binary_path is not None
            or self.oci_runtime_binary_digest is not None
        ):
            raise ValueError("trusted process cannot carry OCI binary authority")
        if self.runtime_class is RuntimeClass.HARDENED_GVISOR and (
            self.runsc_binary_path != self.oci_runtime_binary_path
            or self.runsc_binary_digest != self.oci_runtime_binary_digest
        ):
            raise ValueError("runsc registration authority must equal the pinned OCI binary")
        if self.fixed_environment != tuple(sorted(self.fixed_environment)):
            raise ValueError("fixed environment must be sorted")
        if len({key for key, _ in self.fixed_environment}) != len(self.fixed_environment):
            raise ValueError("fixed environment keys must be unique")
        if any(not key or "=" in key or "\x00" in key + value for key, value in self.fixed_environment):
            raise ValueError("invalid fixed environment")


@dataclass(frozen=True, slots=True)
class InstalledImage:
    image_digest: str
    runtime_id: str
    immutable_reference: str

    def __post_init__(self) -> None:
        if not self.image_digest.startswith("sha256:") or "@sha256:" not in self.immutable_reference:
            raise ValueError("installed image must use an immutable digest reference")


@dataclass(frozen=True, slots=True)
class SandboxSecurityPolicy:
    policy_digest: str
    uid: int
    gid: int
    read_only_root: bool
    drop_all_capabilities: bool
    no_new_privileges: bool
    seccomp_bytes: bytes
    seccomp_digest: str
    apparmor_profile: str | None
    selinux_label: str | None
    namespace_flags: tuple[str, ...]
    privileged: bool
    devices: tuple[str, ...]
    docker_socket_forbidden: bool
    tmpfs_mounts: tuple[tuple[str, str], ...]
    snapshot_max_depth: int
    snapshot_max_files: int
    snapshot_max_inodes: int

    def __post_init__(self) -> None:
        if _wp7_digest(self.seccomp_bytes.decode("utf-8")) != self.seccomp_digest and (
            "sha256:" + __import__("hashlib").sha256(self.seccomp_bytes).hexdigest()
        ) != self.seccomp_digest:
            raise ValueError("seccomp digest mismatch")
        if self.privileged or self.devices or not self.docker_socket_forbidden:
            raise ValueError("security policy admits forbidden container authority")
        if (self.apparmor_profile is None) == (self.selinux_label is None):
            raise ValueError("exactly one LSM authority is required")
        if min(self.uid, self.gid, self.snapshot_max_depth, self.snapshot_max_files, self.snapshot_max_inodes) < 0:
            raise ValueError("invalid security policy numeric value")

    def projection(self) -> dict[str, Any]:
        return {"uid": self.uid, "gid": self.gid, "read_only_root": self.read_only_root,
                "drop_all_capabilities": self.drop_all_capabilities,
                "no_new_privileges": self.no_new_privileges, "seccomp_digest": self.seccomp_digest,
                "apparmor_profile": self.apparmor_profile, "selinux_label": self.selinux_label,
                "namespace_flags": list(self.namespace_flags), "privileged": self.privileged,
                "devices": list(self.devices), "docker_socket_forbidden": self.docker_socket_forbidden,
                "tmpfs_mounts": [list(item) for item in self.tmpfs_mounts],
                "snapshot_max_depth": self.snapshot_max_depth, "snapshot_max_files": self.snapshot_max_files,
                "snapshot_max_inodes": self.snapshot_max_inodes}

    @staticmethod
    def derive_digest(projection: Mapping[str, Any]) -> str:
        return _wp7_digest(dict(projection))


@dataclass(frozen=True, slots=True)
class SandboxNetworkPolicy:
    policy_digest: str
    mode: str
    docker_network: str
    egress_route_ids: tuple[str, ...]
    default_deny: bool

    def projection(self) -> dict[str, Any]:
        return {"mode": self.mode, "docker_network": self.docker_network,
                "egress_route_ids": list(self.egress_route_ids), "default_deny": self.default_deny}

    @staticmethod
    def derive_digest(projection: Mapping[str, Any]) -> str:
        return _wp7_digest(dict(projection))


@dataclass(frozen=True, slots=True)
class InstalledVerifier:
    grant: VerifierGrant
    runtime_id: str
    runtime_class: RuntimeClass
    security_policy_digest: str
    argv: tuple[str, ...]
    result_relative_path: str
    executable_digest: str
    code_digest: str
    input_schema_digest: str
    result_schema_digest: str

    def __post_init__(self) -> None:
        if not self.argv or Path(self.result_relative_path).is_absolute() or ".." in Path(self.result_relative_path).parts:
            raise ValueError("invalid installed verifier")
        if (self.executable_digest, self.code_digest, self.input_schema_digest, self.result_schema_digest) != (
            self.grant.executable_digest, self.grant.code_digest, self.grant.input_schema_digest,
            self.grant.result_schema_digest):
            raise ValueError("verifier authority digest mismatch")


@dataclass(frozen=True, slots=True)
class InstalledSandboxAuthoritySet:
    runtimes: tuple[InstalledRuntime, ...]
    images: tuple[InstalledImage, ...]
    security_policies: tuple[SandboxSecurityPolicy, ...]
    network_policies: tuple[SandboxNetworkPolicy, ...]
    verifiers: tuple[InstalledVerifier, ...]

    def __post_init__(self) -> None:
        for values, key in ((self.runtimes, lambda value: value.runtime_id),
                            (self.images, lambda value: value.image_digest),
                            (self.security_policies, lambda value: value.policy_digest),
                            (self.network_policies, lambda value: value.policy_digest),
                            (self.verifiers, lambda value: value.grant.verifier_id)):
            keys = tuple(key(value) for value in values)
            if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
                raise ValueError("installed authority catalogs must be sorted and unique")


@dataclass(frozen=True, slots=True)
class SandboxExecutionPlan:
    episode_id: str
    effective_plan_digest: str
    subject_digest: str
    final_receipt_digest: str
    runtime: InstalledRuntime
    image: InstalledImage
    security_policy: SandboxSecurityPolicy
    network_policy: SandboxNetworkPolicy
    setups: tuple[SetupRegistryRecord, ...]
    verifier: InstalledVerifier
    resources: Any
    limits: Any
    materialization_plan: WorkspaceMaterializationPlan
    tool_bindings: tuple[RunnerToolBinding, ...]
    isolation_disposition: IsolationDisposition


@dataclass(frozen=True, slots=True)
class WorkspaceStorageIdentity:
    authority_id: str
    quota_enforced: bool
    quota_bytes: int
    owner_uid: int
    owner_gid: int

    def __post_init__(self) -> None:
        if (
            type(self.authority_id) is not str
            or not self.authority_id
            or type(self.quota_enforced) is not bool
            or type(self.quota_bytes) is not int
            or self.quota_bytes <= 0
            or type(self.owner_uid) is not int
            or type(self.owner_gid) is not int
            or self.owner_uid < 0
            or self.owner_gid < 0
        ):
            raise ValueError("invalid storage authority identity")


@dataclass(frozen=True, slots=True)
class RuntimePreparedIdentity:
    runtime_resource_id: str
    labels: Mapping[str, str]

    def __post_init__(self) -> None:
        if (
            type(self.runtime_resource_id) is not str
            or not self.runtime_resource_id
            or any(type(key) is not str or not key or type(value) is not str
                   for key, value in self.labels.items())
        ):
            raise ValueError("invalid prepared runtime identity")
        object.__setattr__(
            self, "labels", MappingProxyType(dict(sorted(self.labels.items())))
        )


@dataclass(frozen=True, slots=True)
class RuntimeLaunchContext:
    role: Literal["primary", "verifier"]
    lease_id: str
    workspace_id: str
    epoch: int
    storage: WorkspaceStorageIdentity
    snapshot_relative_path: str | None
    result_relative_path: str | None
    publish_prepared_identity: Callable[[RuntimePreparedIdentity], Awaitable[None]]
    workspace_fd: int | None = None
    workspace_identity: tuple[int, int] | None = None
    owner_token: str | None = None

    def __post_init__(self) -> None:
        if (
            self.role not in {"primary", "verifier"}
            or not self.lease_id
            or not self.workspace_id
            or type(self.epoch) is not int
            or self.epoch <= 0
            or type(self.storage) is not WorkspaceStorageIdentity
            or not callable(self.publish_prepared_identity)
            or (self.workspace_fd is None) != (self.workspace_identity is None)
            or (
                self.workspace_fd is not None
                and (
                    type(self.workspace_fd) is not int
                    or self.workspace_fd < 0
                    or type(self.workspace_identity) is not tuple
                    or len(self.workspace_identity) != 2
                    or any(type(value) is not int or value < 0 for value in self.workspace_identity)
                )
            )
            or (
                self.owner_token is not None
                and (
                    type(self.owner_token) is not str
                    or not self.owner_token
                    or len(self.owner_token) > 512
                )
            )
        ):
            raise ValueError("invalid runtime launch identity")
        expected = (None, None) if self.role == "primary" else ("snapshot", "result")
        if (self.snapshot_relative_path, self.result_relative_path) != expected:
            raise ValueError("runtime role paths are not exact")


@dataclass(frozen=True, slots=True)
class SandboxMeasurement:
    effective_plan_digest: str
    lease_id: str
    workspace_id: str
    runtime_id: str
    runtime_class: str
    driver_binary_digest: str
    image_digest: str
    requested: Mapping[str, Any]
    effective: Mapping[str, Any]
    measured: Mapping[str, Any]
    runtime_resource_id: str
    mismatch: tuple[str, ...]
    isolation_disposition: IsolationDisposition
    isolated: bool
    reward_eligible: bool

    def __post_init__(self) -> None:
        for name in ("requested", "effective", "measured"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


class SandboxRuntimeError(RuntimeError):
    def __init__(self, message: str, *, code: str, episode_id: str | None = None,
                 effective_plan_digest: str | None = None, lease_id: str | None = None,
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.episode_id = episode_id
        self.effective_plan_digest = effective_plan_digest
        self.lease_id = lease_id
        self.details = MappingProxyType(dict(details or {}))


class SandboxPlanError(SandboxRuntimeError): pass
class MaterializationError(SandboxRuntimeError): pass
class CacheLeaseError(MaterializationError): pass
class SandboxLaunchError(SandboxRuntimeError): pass
class SandboxAttestationError(SandboxRuntimeError): pass
class WorkspaceStateError(SandboxRuntimeError): pass
class VerifierSnapshotError(SandboxRuntimeError): pass
class VerifierExecutionError(SandboxRuntimeError): pass


class SandboxFault(SandboxRuntimeError):
    def __init__(self, primary: BaseException, cleanup_receipt: SandboxCleanupReceipt,
                 cleanup_errors: tuple[str, ...]) -> None:
        super().__init__("sandbox operation and cleanup both failed", code="cleanup_incomplete")
        self.primary = primary
        self.cleanup_receipt = cleanup_receipt
        self.cleanup_errors = cleanup_errors
        self.__cause__ = primary


def _exact_one(values: Sequence[Any], predicate: Any, *, missing_code: str) -> Any:
    found = [value for value in values if predicate(value)]
    if len(found) != 1:
        raise SandboxPlanError("installed authority did not resolve exactly once", code=missing_code)
    return found[0]


def build_sandbox_execution_plan(request: WorkspaceOpenRequest, registries: RegistrySnapshotSet,
                                 installed_authorities: InstalledSandboxAuthoritySet) -> SandboxExecutionPlan:
    if type(request) is not WorkspaceOpenRequest or type(request.effective_plan) is not EffectiveExecutionPlan:
        raise SandboxPlanError("exact workspace request required", code="plan_type_invalid")
    if type(registries) is not RegistrySnapshotSet or type(installed_authorities) is not InstalledSandboxAuthoritySet:
        raise SandboxPlanError("exact installed catalogs required", code="plan_type_invalid")
    plan = request.effective_plan
    if request.effective_plan_digest != plan.canonical_digest():
        raise SandboxPlanError("effective plan digest mismatch", code="plan_digest_mismatch")
    binding_record = _exact_one(registries.sandbox_runtimes,
                                lambda item: item.binding.runtime_id == plan.sandbox.runtime_id,
                                missing_code="runtime_authority_missing")
    expected_binding = SandboxBinding(runtime_id=plan.sandbox.runtime_id,
        runtime_class=plan.sandbox.runtime_class,
        driver_implementation_digest=plan.sandbox.driver_implementation_digest,
        runtime_binary_digest=plan.sandbox.runtime_binary_digest,
        security_policy_digest=plan.sandbox.security_policy_digest,
        image_digest=plan.sandbox.image_digest,
        network_policy_digest=plan.sandbox.network_policy_digest)
    if binding_record.binding != expected_binding:
        raise SandboxPlanError("runtime registry binding mismatch", code="runtime_identity_mismatch")
    runtime = _exact_one(installed_authorities.runtimes,
                         lambda item: item.runtime_id == plan.sandbox.runtime_id,
                         missing_code="runtime_authority_missing")
    if (runtime.runtime_class, runtime.driver_implementation_digest, runtime.measured_binary_digest) != (
        plan.sandbox.runtime_class, plan.sandbox.driver_implementation_digest, plan.sandbox.runtime_binary_digest):
        raise SandboxPlanError("installed runtime mismatch", code="runtime_identity_mismatch")
    if runtime.runtime_class not in {RuntimeClass.TRUSTED_PROCESS, RuntimeClass.HARDENED_DOCKER, RuntimeClass.HARDENED_GVISOR}:
        raise SandboxPlanError("runtime class unsupported", code="runtime_unsupported")
    if runtime.runtime_class is RuntimeClass.HARDENED_GVISOR and runtime.oci_runtime_name != "runsc":
        raise SandboxPlanError("runsc authority is not exact", code="runtime_unsupported")
    image = _exact_one(installed_authorities.images, lambda item: item.image_digest == plan.sandbox.image_digest,
                       missing_code="runtime_authority_missing")
    if image.runtime_id != runtime.runtime_id:
        raise SandboxPlanError("image runtime mismatch", code="runtime_identity_mismatch")
    image_record = _exact_one(registries.images, lambda item: item.image_digest == image.image_digest,
                              missing_code="runtime_authority_missing")
    if image_record.runtime_id != runtime.runtime_id:
        raise SandboxPlanError("image registry mismatch", code="runtime_identity_mismatch")
    security = _exact_one(installed_authorities.security_policies,
                          lambda item: item.policy_digest == plan.sandbox.security_policy_digest,
                          missing_code="runtime_authority_missing")
    if security.policy_digest != SandboxSecurityPolicy.derive_digest(security.projection()):
        raise SandboxPlanError("security policy content mismatch", code="runtime_identity_mismatch")
    if runtime.runtime_class in {RuntimeClass.HARDENED_DOCKER, RuntimeClass.HARDENED_GVISOR} and (
        security.uid == 0 or security.gid == 0
    ):
        raise SandboxPlanError("hardened runtime cannot run as root", code="runtime_identity_mismatch")
    network = _exact_one(installed_authorities.network_policies,
                         lambda item: item.policy_digest == plan.sandbox.network_policy_digest,
                         missing_code="runtime_authority_missing")
    if network.policy_digest != SandboxNetworkPolicy.derive_digest(network.projection()):
        raise SandboxPlanError("network policy content mismatch", code="runtime_identity_mismatch")
    if tuple(network.egress_route_ids) != tuple(plan.sandbox.egress_route_ids):
        raise SandboxPlanError("network route authority mismatch", code="runtime_identity_mismatch")
    if network.mode != "none" or not network.default_deny or network.egress_route_ids:
        raise SandboxPlanError("installed network enforcement is unsupported", code="runtime_unsupported")
    setup_records: list[SetupRegistryRecord] = []
    for grant in plan.effective_capabilities.setup_plans:
        record = _exact_one(registries.setups, lambda item, grant=grant: item.grant.setup_id == grant.setup_id,
                            missing_code="setup_plan_unresolvable")
        if record.grant != grant or record.derived_plan_digest() != grant.plan_digest or record.route_ids or record.secret_handle_ids:
            raise SandboxPlanError("setup plan is not exactly resolvable", code="setup_plan_unresolvable")
        setup_records.append(record)
    verifier = _exact_one(installed_authorities.verifiers,
                          lambda item: item.grant.verifier_id == plan.verifier.verifier_id,
                          missing_code="verifier_authority_mismatch")
    verifier_registry = _exact_one(registries.verifiers,
                                   lambda item: item.grant.verifier_id == plan.verifier.verifier_id,
                                   missing_code="verifier_authority_mismatch")
    if (verifier.grant != plan.verifier or verifier_registry.grant != plan.verifier
        or verifier_registry.runtime_id != verifier.runtime_id
        or verifier_registry.runtime_class is not verifier.runtime_class
        or verifier_registry.security_policy_digest != verifier.security_policy_digest
        or plan.verifier.secret_handle_ids):
        raise SandboxPlanError("verifier authority mismatch", code="verifier_authority_mismatch")
    tools = tuple(RunnerToolBinding(item.tool_id, item.implementation_digest, item.capability_ids)
                  for item in plan.effective_capabilities.tools)
    mounts_by_digest = {mount.source_artifact_digest: mount for mount in plan.sandbox.mounts}
    required: list[tuple[str, str]] = []
    if plan.task.repository_snapshot_digest is not None:
        required.append((plan.task.repository_snapshot_digest, "repository"))
    required += [(value, "dataset") for value in plan.task.dataset_digests]
    required += [(value, "input") for value in plan.task.input_artifact_digests]
    required += [(value, "setup_input") for record in setup_records for value in record.input_digests]
    if any(digest not in mounts_by_digest for digest, _ in required):
        raise SandboxPlanError("task or setup input has no admitted target", code="task_input_unmapped")
    role_by_digest = {digest: role for digest, role in required}
    entries = tuple(sorted((MaterializationEntry(mount.source_artifact_digest, mount.target_logical_path,
                                                  mount.access, mount.max_bytes,
                                                  role_by_digest.get(mount.source_artifact_digest, "mount"))
                            for mount in plan.sandbox.mounts), key=lambda item: (item.target_logical_path, item.source_digest, item.role)))
    materialization = WorkspaceMaterializationPlan(
        request.episode_id, plan.subject_digest, plan.final_receipt_digest, request.effective_plan_digest,
        plan.sandbox.model_dump(mode="json"), plan.task.model_dump(mode="json"),
        tuple(record.plan_projection() for record in setup_records), entries, tools,
        plan.effective_capabilities.resources.model_dump(mode="json"),
        plan.effective_capabilities.limits.model_dump(mode="json"))
    disposition = IsolationDisposition.TRUSTED_PROCESS if runtime.runtime_class is RuntimeClass.TRUSTED_PROCESS else IsolationDisposition.ISOLATED
    return SandboxExecutionPlan(request.episode_id, request.effective_plan_digest, plan.subject_digest,
                                plan.final_receipt_digest, runtime, image, security, network,
                                tuple(setup_records), verifier, plan.effective_capabilities.resources,
                                plan.effective_capabilities.limits, materialization, tools, disposition)


class RuntimeHandle(Protocol):
    runtime_id: str
    async def run_shell(self, command: str, *, timeout_ms: int, output_limit: int) -> Mapping[str, Any]: ...
    async def terminate(self) -> tuple[CleanupStepReceipt, ...]: ...

    async def run_argv(self, argv: Sequence[str], *, timeout_ms: int, output_limit: int) -> Mapping[str, Any]: ...

def _sealed_repository_diff(
    *,
    repository: Path,
    base_commit: str,
    plan: SandboxExecutionPlan,
) -> Mapping[str, Any]:
    if (
        len(base_commit) != 40
        or base_commit != base_commit.lower()
        or any(character not in "0123456789abcdef" for character in base_commit)
    ):
        raise VerifierSnapshotError(
            "workspace base commit is invalid", code="snapshot_tampered"
        )
    git_path = shutil.which(
        "git", path=dict(plan.runtime.fixed_environment).get("PATH", os.defpath)
    )
    if git_path is None:
        raise VerifierSnapshotError(
            "sealed workspace diff requires installed host git",
            code="runtime_unsupported",
        )
    pinned = _snapshot_installed_executable(git_path, None)
    timeout_seconds = max(1, (plan.limits.action_timeout_ms + 999) // 1000)

    def invoke(
        arguments: tuple[str, ...],
        *,
        environment: Mapping[str, str],
        stdout_limit: int,
        cwd: Path = repository,
    ) -> tuple[int, bytes, bytes]:
        try:
            process = subprocess.Popen(
                (pinned.proc_fd_path, *arguments),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(pinned.fd,),
                start_new_session=True,
            )
        except OSError as exc:
            raise VerifierSnapshotError(
                "sealed workspace diff command failed", code="snapshot_tampered"
            ) from exc
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise VerifierSnapshotError(
                "sealed workspace diff pipes are unavailable", code="snapshot_tampered"
            )
        stdout = bytearray()
        stderr = bytearray()
        streams = {
            process.stdout.fileno(): (stdout, stdout_limit),
            process.stderr.fileno(): (stderr, 64 * 1024),
        }
        deadline = time.monotonic() + timeout_seconds
        selector = selectors.DefaultSelector()

        def kill_process_group() -> None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

        try:
            for descriptor in streams:
                os.set_blocking(descriptor, False)
                selector.register(descriptor, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    kill_process_group()
                    raise VerifierSnapshotError(
                        "sealed workspace diff command timed out",
                        code="snapshot_tampered",
                    )
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _ in events:
                    descriptor = key.fd
                    buffer, limit = streams[descriptor]
                    chunk = os.read(descriptor, min(65536, limit - len(buffer) + 1))
                    if not chunk:
                        selector.unregister(descriptor)
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        kill_process_group()
                        raise VerifierSnapshotError(
                            "sealed workspace diff exceeded its output limit",
                            code="output_limit_exceeded",
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_process_group()
                raise VerifierSnapshotError(
                    "sealed workspace diff command timed out",
                    code="snapshot_tampered",
                )
            try:
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                kill_process_group()
                raise VerifierSnapshotError(
                    "sealed workspace diff command timed out",
                    code="snapshot_tampered",
                ) from exc
            return returncode, bytes(stdout), bytes(stderr)
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
            if process.poll() is None:
                kill_process_group()

    try:
        identity = repository.stat(follow_symlinks=False)
        source_git_directory = repository / ".git"
        source_objects = source_git_directory / "objects"
        if (
            not stat.S_ISDIR(identity.st_mode)
            or not source_git_directory.is_dir()
            or not source_objects.is_dir()
        ):
            raise VerifierSnapshotError(
                "sealed workspace repository layout is unsupported",
                code="snapshot_tampered",
            )
        forbidden_object_authorities = (
            source_objects / "info" / "alternates",
            source_objects / "info" / "http-alternates",
            source_git_directory / "info" / "grafts",
            source_git_directory / "shallow",
        )
        if any(path.exists() for path in forbidden_object_authorities):
            raise VerifierSnapshotError(
                "sealed workspace contains external Git object authority",
                code="snapshot_tampered",
            )
        for current, directories, files in os.walk(repository):
            current_path = Path(current)
            if current_path == repository:
                directories.remove(".git")
                continue
            if ".git" in directories or ".git" in files:
                raise VerifierSnapshotError(
                    "sealed workspace contains an embedded Git repository",
                    code="snapshot_tampered",
                )
        with tempfile.TemporaryDirectory(
            prefix="breadboard-sealed-diff-"
        ) as temporary_text:
            temporary = Path(temporary_text)
            private_git_directory = temporary / "git"
            template_directory = temporary / "template"
            template_directory.mkdir(mode=0o700)
            base_environment = {
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": temporary_text,
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": os.pathsep.join(
                    (os.path.dirname(git_path), "/usr/local/bin", "/usr/bin", "/bin")
                ),
            }
            returncode, _, stderr = invoke(
                (
                    "init",
                    "--quiet",
                    "--bare",
                    f"--template={template_directory}",
                    str(private_git_directory),
                ),
                environment=base_environment,
                stdout_limit=64 * 1024,
                cwd=temporary,
            )
            if returncode != 0:
                raise VerifierSnapshotError(
                    "sealed workspace diff repository initialization failed",
                    code="snapshot_tampered",
                    details={"stderr": stderr.decode("utf-8", "replace")[:4096]},
                )
            attributes_directory = private_git_directory / "info"
            attributes_directory.mkdir(mode=0o700, exist_ok=True)
            (attributes_directory / "attributes").write_text(
                "* -text -filter -diff -working-tree-encoding -eol\n",
                encoding="utf-8",
            )
            environment = {
                **base_environment,
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(source_objects),
                "GIT_DIR": str(private_git_directory),
                "GIT_INDEX_FILE": str(temporary / "index"),
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OBJECT_DIRECTORY": str(private_git_directory / "objects"),
                "GIT_WORK_TREE": str(repository),
            }
            common = (
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.safecrlf=false",
                "-c",
                "diff.external=",
            )
            for command in (
                (*common, "read-tree", base_commit),
                (
                    *common,
                    "add",
                    "--all",
                    "--force",
                    "--",
                    ".",
                    ":(top,exclude).git",
                ),
            ):
                returncode, _, stderr = invoke(
                    command, environment=environment, stdout_limit=64 * 1024
                )
                if returncode != 0:
                    raise VerifierSnapshotError(
                        "sealed workspace diff preparation failed",
                        code="snapshot_tampered",
                        details={"stderr": stderr.decode("utf-8", "replace")[:4096]},
                    )
            returncode, stdout, stderr = invoke(
                (
                    *common,
                    "diff",
                    "--cached",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--binary",
                    "--full-index",
                    "--no-renames",
                    "--ignore-submodules=none",
                    base_commit,
                    "--",
                    ".",
                ),
                environment=environment,
                stdout_limit=plan.limits.artifact_bytes_each,
            )
            if returncode != 0:
                raise VerifierSnapshotError(
                    "sealed workspace diff failed",
                    code="snapshot_tampered",
                    details={"stderr": stderr.decode("utf-8", "replace")[:4096]},
                )
            return MappingProxyType(
                {
                    "returncode": 0,
                    "stdout": stdout.decode("utf-8", "strict"),
                    "stderr": stderr.decode("utf-8", "replace"),
                    "base_commit": base_commit,
                    "git_executable_digest": pinned.digest,
                }
            )
    except UnicodeDecodeError as exc:
        raise VerifierSnapshotError(
            "sealed workspace diff is not UTF-8", code="snapshot_tampered"
        ) from exc
    finally:
        pinned.close()


class RuntimeBackend(Protocol):
    async def launch(self, plan: SandboxExecutionPlan, workspace: Path, *,
                     context: RuntimeLaunchContext) -> tuple[RuntimeHandle, SandboxMeasurement]: ...


class TrustedProcessHandle:
    def __init__(
        self,
        plan: SandboxExecutionPlan,
        workspace: Path,
        lease_id: str,
        executable: _PinnedExecutable,
        git_executable: str,
        workspace_fd: int,
        workspace_identity: tuple[int, int],
    ) -> None:
        self.plan = plan
        self.workspace = workspace
        self.lease_id = lease_id
        self.runtime_id = "process-group-" + lease_id
        self._executable = executable
        self._git_executable = git_executable
        self._workspace_fd = workspace_fd
        self._workspace_identity = workspace_identity
        self._groups: dict[int, Mapping[str, Any]] = {}
        self._launch_lock = asyncio.Lock()
        self._closing = False
        self._closed = False
        self.repository_base_commit: str | None = None
        self.repository_relative_path: str | None = None

    def bind_identity_recorder(self, recorder: Any) -> None:
        if getattr(self, "_identity_recorder", None) is not None or not callable(recorder):
            raise ValueError("trusted process identity recorder is not exact")
        self._identity_recorder = recorder

    @staticmethod
    def _proc_fields(pid: int) -> list[str]:
        raw = _bounded_regular_read(Path("/proc"), f"{pid}/stat", offset=0, limit=16_385)
        if len(raw) > 16_384:
            raise OSError("process identity is oversized")
        return raw.decode("ascii", "strict").rsplit(")", 1)[1].split()

    @classmethod
    def _start_identity(cls, pid: int) -> str:
        return "linux-proc-start:" + cls._proc_fields(pid)[19]

    @staticmethod
    def _cgroup_identity(pid: int) -> str:
        raw = _bounded_regular_read(Path("/proc"), f"{pid}/cgroup", offset=0, limit=16_385)
        if len(raw) > 16_384:
            raise OSError("process cgroup identity is oversized")
        return "sha256:" + __import__("hashlib").sha256(raw).hexdigest()

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _observe_group_identity(cls, pid: int) -> Mapping[str, Any]:
        process_group = os.getpgid(pid)
        session_id = os.getsid(pid)
        if process_group != pid or session_id != pid:
            raise RuntimeError("trusted process group identity mismatch")
        return MappingProxyType(
            {
                "process_pid": pid,
                "process_group_id": process_group,
                "process_session_id": session_id,
                "process_start_identity": cls._start_identity(pid),
                "process_cgroup_identity": cls._cgroup_identity(pid),
            }
        )

    def _orphaned_group_identity_matches(
        self,
        process_group: int,
        identity: Mapping[str, Any],
    ) -> bool:
        try:
            proc_fd = os.open(
                "/proc",
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError:
            return False
        try:
            for name in os.listdir(proc_fd):
                if not name.isdecimal():
                    continue
                pid = int(name)
                try:
                    fields = self._proc_fields(pid)
                    if (
                        len(fields) <= 3
                        or int(fields[2]) != process_group
                        or int(fields[3])
                        != identity.get("process_session_id")
                        or self._cgroup_identity(pid)
                        != identity.get("process_cgroup_identity")
                    ):
                        continue
                except (OSError, ValueError, IndexError):
                    continue
                return True
        finally:
            os.close(proc_fd)
        return False

    def _group_identity_matches(
        self,
        process_group: int,
        identity: Mapping[str, Any],
    ) -> bool:
        if (
            identity.get("process_pid") != process_group
            or identity.get("process_group_id") != process_group
            or identity.get("process_session_id") != process_group
        ):
            return False
        try:
            return (
                os.getpgid(process_group) == process_group
                and self._start_identity(process_group)
                == identity.get("process_start_identity")
                and self._cgroup_identity(process_group)
                == identity.get("process_cgroup_identity")
            )
        except (ProcessLookupError, FileNotFoundError):
            return self._orphaned_group_identity_matches(
                process_group,
                identity,
            )
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return False



    async def _drain_group(
        self,
        process_group: int,
        identity: Mapping[str, Any],
    ) -> bool:
        if not self._group_exists(process_group):
            return True
        if not self._group_identity_matches(process_group, identity):
            return False
        try:
            os.killpg(process_group, 15)
        except ProcessLookupError:
            return True
        deadline = asyncio.get_running_loop().time() + 0.25
        while self._group_exists(process_group) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        if self._group_exists(process_group):
            if not self._group_identity_matches(process_group, identity):
                return False
            try:
                os.killpg(process_group, 9)
            except ProcessLookupError:
                return True
        deadline = asyncio.get_running_loop().time() + 0.75
        while self._group_exists(process_group) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        return not self._group_exists(process_group)

    async def _cleanup_process(
        self, process: asyncio.subprocess.Process, *, clear_identity: bool
    ) -> bool:
        identity = self._groups.get(process.pid)
        if identity is None:
            try:
                identity = self._observe_group_identity(process.pid)
            except (OSError, RuntimeError):
                identity = None
            else:
                self._groups[process.pid] = identity
        absent = (
            False
            if identity is None
            else await self._drain_group(process.pid, identity)
        )
        try:
            await asyncio.wait_for(process.wait(), 0.25)
        except asyncio.TimeoutError:
            absent = False
        if absent:
            self._groups.pop(process.pid, None)
            if clear_identity:
                recorder = getattr(self, "_identity_recorder", None)
                if recorder is not None:
                    recorder(f"process-group-{process.pid}", None)
        return absent

    async def _cleanup_process_shielded(
        self, process: asyncio.subprocess.Process, *, clear_identity: bool
    ) -> bool:
        cleanup = asyncio.create_task(
            self._cleanup_process(process, clear_identity=clear_identity)
        )
        try:
            return await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def run_shell(
        self, command: str, *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        return await self._run_pinned_argv(
            (self._executable.proc_fd_path, "-lc", command),
            timeout_ms=timeout_ms,
            output_limit=output_limit,
        )

    async def run_argv(
        self, argv: Sequence[str], *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        if not argv or any(
            type(item) is not str or not item or "\x00" in item for item in argv
        ):
            raise SandboxLaunchError(
                "fixed argv is invalid",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        return await self._run_pinned_argv(
            (
                self._executable.proc_fd_path,
                "-lc",
                'exec "$@"',
                "breadboard-execute",
                *argv,
            ),
            timeout_ms=timeout_ms,
            output_limit=output_limit,
        )

    async def _run_pinned_argv(
        self, argv: Sequence[str], *, timeout_ms: int, output_limit: int
    ) -> Mapping[str, Any]:
        if not argv or any(type(item) is not str or "\x00" in item for item in argv):
            raise SandboxLaunchError(
                "fixed argv is invalid",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        read_fd = write_fd = -1
        process: asyncio.subprocess.Process | None = None
        identity_published = False
        try:
            async with self._launch_lock:
                if self._closing or self._closed:
                    raise WorkspaceStateError(
                        "runtime is not active",
                        code="lease_not_active",
                        lease_id=self.lease_id,
                    )
                metadata = os.fstat(self._workspace_fd)
                if (metadata.st_dev, metadata.st_ino) != self._workspace_identity:
                    raise WorkspaceStateError(
                        "workspace descriptor identity changed",
                        code="workspace_authority_mismatch",
                        lease_id=self.lease_id,
                    )
                read_fd, write_fd = os.pipe()
                os.set_inheritable(write_fd, True)
                bootstrap = (
                    f"printf B >&{write_fd}; "
                    'kill -STOP $$; exec "$@"'
                )
                process = await asyncio.create_subprocess_exec(
                    self._executable.proc_fd_path,
                    "-c",
                    bootstrap,
                    "breadboard-bootstrap",
                    *argv,
                    executable=self._executable.proc_fd_path,
                    pass_fds=(
                        self._executable.fd,
                        self._workspace_fd,
                        write_fd,
                    ),
                    preexec_fn=lambda: os.fchdir(self._workspace_fd),
                    env=dict(self.plan.runtime.fixed_environment),
                    start_new_session=True,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                os.close(write_fd)
                write_fd = -1
                if os.read(read_fd, 1) != b"B":
                    raise RuntimeError("trusted process bootstrap failed")
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout_ms / 1000
                stop_deadline = min(deadline, loop.time() + 0.25)
                while True:
                    fields = self._proc_fields(process.pid)
                    if fields[0] in {"T", "t"}:
                        break
                    if loop.time() >= stop_deadline:
                        raise RuntimeError(
                            "trusted process did not stop before admission"
                        )
                    await asyncio.sleep(0.001)
                identity = self._observe_group_identity(process.pid)
                process_group = int(identity["process_group_id"])
                self._groups[process_group] = identity
                recorder = getattr(self, "_identity_recorder", None)
                if recorder is None:
                    raise RuntimeError(
                        "trusted process identity recorder is unavailable"
                    )
                recorder(f"process-group-{process_group}", identity)
                identity_published = True
                os.kill(process.pid, signal.SIGCONT)
        except BaseException:
            if process is not None:
                await self._cleanup_process_shielded(
                    process, clear_identity=identity_published
                )
            raise
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

        total = 0
        count_lock = asyncio.Lock()

        async def consume(stream: asyncio.StreamReader) -> bytes:
            nonlocal total
            chunks: list[bytes] = []
            while True:
                chunk = await stream.read(min(65536, output_limit + 1))
                if not chunk:
                    return b"".join(chunks)
                async with count_lock:
                    if total + len(chunk) > output_limit:
                        raise SandboxLaunchError(
                            "process output exceeded admitted limit",
                            code="output_limit_exceeded",
                            lease_id=self.lease_id,
                        )
                    total += len(chunk)
                chunks.append(chunk)

        primary_error: BaseException | None = None
        stream_tasks = (
            asyncio.create_task(consume(process.stdout)),
            asyncio.create_task(consume(process.stderr)),
        )
        wait_task = asyncio.create_task(process.wait())
        stream_result: tuple[bytes, bytes] | None = None
        try:
            async with asyncio.timeout_at(deadline):
                stdout, stderr, _ = await asyncio.gather(*stream_tasks, wait_task)
            stream_result = (stdout, stderr)
        except TimeoutError as exc:
            primary_error = SandboxLaunchError(
                "process action timed out",
                code="runtime_launch_failed",
                lease_id=self.lease_id,
            )
            primary_error.__cause__ = exc
        except BaseException as exc:
            primary_error = exc
        finally:
            for task in (*stream_tasks, wait_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*stream_tasks, wait_task, return_exceptions=True)
            try:
                group_absent = await self._cleanup_process_shielded(
                    process, clear_identity=True
                )
            except asyncio.CancelledError as exc:
                if primary_error is None:
                    primary_error = exc
                group_absent = not self._group_exists(process.pid)
            if not group_absent and primary_error is None:
                primary_error = SandboxLaunchError(
                    "process group cleanup could not be proven",
                    code="runtime_launch_failed",
                    lease_id=self.lease_id,
                )
        if stream_result is not None:
            stdout, stderr = stream_result
        if primary_error is not None:
            raise primary_error
        return {
            "returncode": process.returncode,
            "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
        }

    async def measure_repository_base_commit(self) -> str | None:
        repositories = tuple(
            entry
            for entry in self.plan.materialization_plan.entries
            if entry.role == "repository"
        )
        if not repositories:
            return None
        if len(repositories) != 1:
            raise SandboxLaunchError(
                "workspace base measurement requires exactly one repository mount",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        relative_path = repositories[0].target_logical_path
        result = await self._run_pinned_argv(
            (
                self._executable.proc_fd_path,
                "-lc",
                'exec "$2" -C "$1" rev-parse --verify "HEAD^{commit}"',
                "breadboard-workspace-base",
                relative_path,
                self._git_executable,
            ),
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
            raise SandboxLaunchError(
                "workspace base commit measurement failed",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
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
            raise SandboxLaunchError(
                "workspace diff requires exactly one repository mount",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        result = await self._run_pinned_argv(
            (
                self._executable.proc_fd_path,
                "-lc",
                'exec "$2" -C "$1" diff --no-ext-diff --binary',
                "breadboard-workspace-diff",
                repositories[0].target_logical_path,
                self._git_executable,
            ),
            timeout_ms=self.plan.limits.action_timeout_ms,
            output_limit=self.plan.limits.observation_bytes,
        )
        if result.get("returncode") == 127:
            raise SandboxLaunchError(
                "workspace diff requires installed host git",
                code="runtime_unsupported",
                lease_id=self.lease_id,
            )
        return result

    async def terminate(self) -> tuple[CleanupStepReceipt, ...]:
        async with self._launch_lock:
            if self._closed:
                return (CleanupStepReceipt("runtime", CleanupState.ALREADY_RELEASED),)
            self._closing = True
        failed = False
        for process_group, identity in tuple(self._groups.items()):
            if not await self._drain_group(process_group, identity):
                failed = True
            else:
                self._groups.pop(process_group, None)
                recorder = getattr(self, "_identity_recorder", None)
                if recorder is not None:
                    recorder(f"process-group-{process_group}", None)
        async with self._launch_lock:
            if not failed and not self._groups:
                self._executable.close()
                if self._workspace_fd >= 0:
                    os.close(self._workspace_fd)
                    self._workspace_fd = -1
                self._closed = True
        return (
            CleanupStepReceipt(
                "runtime", CleanupState.FAILED if failed else CleanupState.RELEASED
            ),
        )


class TrustedProcessBackend:
    async def launch(self, plan: SandboxExecutionPlan, workspace: Path, *,
                     context: RuntimeLaunchContext) -> tuple[RuntimeHandle, SandboxMeasurement]:
        lease_id = context.lease_id
        workspace_id = context.workspace_id
        if plan.runtime.runtime_class is not RuntimeClass.TRUSTED_PROCESS:
            raise SandboxLaunchError("trusted process backend class mismatch", code="runtime_unsupported")
        if context.workspace_fd is None or context.workspace_identity is None:
            raise SandboxLaunchError(
                "pinned workspace descriptor required",
                code="workspace_authority_missing",
                lease_id=lease_id,
            )
        git_executable = shutil.which(
            "git",
            path=dict(plan.runtime.fixed_environment).get("PATH", os.defpath),
        )
        if git_executable is None:
            os.close(context.workspace_fd)
            raise SandboxLaunchError(
                "trusted process workspace diff requires installed host git",
                code="runtime_unsupported",
                lease_id=lease_id,
            )
        executable: _PinnedExecutable | None = None
        try:
            executable = _snapshot_installed_executable(
                plan.runtime.executable_path,
                plan.runtime.measured_binary_digest,
            )
            handle = TrustedProcessHandle(
                plan, workspace, lease_id, executable, git_executable,
                context.workspace_fd, context.workspace_identity,
            )
            repository_base_commit = await handle.measure_repository_base_commit()
            requested = {
                "runtime": plan.runtime.runtime_id,
                "image": plan.image.image_digest,
                "network": plan.network_policy.mode,
                "storage_bytes": plan.resources.storage_bytes,
            }
            effective = dict(requested)
            effective["trusted_executable"] = {
                "source_path": executable.source_path,
                "digest": executable.digest,
                "size": executable.size,
                "execution": "linux-sealed-memfd",
            }
            effective["workspace_diff_git_path"] = git_executable
            if repository_base_commit is not None:
                effective["workspace_base_commit"] = repository_base_commit
            measured = dict(effective)
            measurement = SandboxMeasurement(
                plan.effective_plan_digest,
                lease_id,
                workspace_id,
                plan.runtime.runtime_id,
                plan.runtime.runtime_class.value,
                executable.digest,
                plan.image.image_digest,
                requested,
                effective,
                measured,
                handle.runtime_id,
                (),
                IsolationDisposition.TRUSTED_PROCESS,
                False,
                False,
            )
        except BaseException:
            if executable is not None:
                executable.close()
            os.close(context.workspace_fd)
            raise
        return handle, measurement

    async def reconcile(
        self,
        record: Mapping[str, Any],
    ) -> tuple[CleanupStepReceipt, ...]:
        raw_identities = record.get("process_identities")
        if raw_identities is None:
            if any(
                key in record
                for key in (
                    "process_pid",
                    "process_group_id",
                    "process_session_id",
                    "process_start_identity",
                    "process_cgroup_identity",
                )
            ):
                return (
                    CleanupStepReceipt(
                        "runtime",
                        CleanupState.QUARANTINED,
                        "stale_identity_uncertain",
                    ),
                )
            raw_identities = ()
        if type(raw_identities) not in {list, tuple}:
            return (
                CleanupStepReceipt(
                    "runtime",
                    CleanupState.QUARANTINED,
                    "stale_identity_uncertain",
                ),
            )
        if not raw_identities:
            return (
                CleanupStepReceipt(
                    "runtime",
                    CleanupState.ALREADY_RELEASED,
                ),
            )
        return (
            CleanupStepReceipt(
                "runtime",
                CleanupState.QUARANTINED,
                "stale_identity_uncertain",
            ),
        )


class LeaseBackedRunnerWorkspace:
    def __init__(self, lease: SandboxWorkspaceLease, effective_plan_digest: str,
                 tool_bindings: tuple[RunnerToolBinding, ...]) -> None:
        if lease.plan.effective_plan_digest != effective_plan_digest or lease.plan.tool_bindings != tool_bindings:
            raise SandboxPlanError("runner workspace identity mismatch", code="tool_binding_projection_mismatch")
        bindings = tuple(tool_bindings)
        if (
            any(type(binding) is not RunnerToolBinding for binding in bindings)
            or len({binding.tool_id for binding in bindings}) != len(bindings)
        ):
            raise SandboxPlanError(
                "runner tool bindings are not exact and unique",
                code="tool_binding_projection_mismatch",
            )
        self.__lease = lease
        self.__tool_bindings = bindings

    @property
    def tool_bindings(self) -> tuple[RunnerToolBinding, ...]: return self.__tool_bindings
    async def invoke_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Mapping[str, Any]:
        lease = self.__lease
        if type(tool_id) is not str:
            raise WorkspaceStateError(
                "tool is not exactly admitted",
                code="tool_binding_projection_mismatch",
                lease_id=lease.lease_id,
            )
        try:
            frozen_arguments = freeze_json_object(
                arguments,
                field_name="workspace tool arguments",
                max_depth=8,
                max_nodes=64,
                max_encoded_bytes=lease.plan.limits.observation_bytes,
            )
        except (JsonSnapshotError, TypeError, ValueError):
            raise WorkspaceStateError(
                "tool arguments are invalid",
                code="runtime_preflight_failed",
                lease_id=lease.lease_id,
            ) from None
        await lease._begin_operation()
        try:
            bindings = tuple(
                binding for binding in self.__tool_bindings
                if binding.tool_id == tool_id
            )
            if tool_id != "terminal" or len(bindings) != 1:
                raise WorkspaceStateError(
                    "tool is not exactly admitted",
                    code="tool_binding_projection_mismatch",
                    lease_id=lease.lease_id,
                )
            if set(frozen_arguments) != {"command"}:
                raise WorkspaceStateError(
                    "tool arguments are invalid",
                    code="runtime_preflight_failed",
                    lease_id=lease.lease_id,
                )
            command = frozen_arguments["command"]
            if type(command) is not str or not command:
                raise WorkspaceStateError(
                    "tool arguments are invalid",
                    code="runtime_preflight_failed",
                    lease_id=lease.lease_id,
                )
            if (
                type(timeout_ms) is not int
                or timeout_ms <= 0
                or timeout_ms > lease.plan.limits.action_timeout_ms
            ):
                raise WorkspaceStateError(
                    "timeout exceeds admitted ceiling",
                    code="runtime_preflight_failed",
                    lease_id=lease.lease_id,
                )
            return await lease._runtime.run_shell(
                command,
                timeout_ms=timeout_ms,
                output_limit=lease.plan.limits.observation_bytes,
            )
        finally:
            await lease._end_operation()


    async def run_shell(self, command: str, *, timeout: int) -> Mapping[str, Any]:
        lease = self.__lease
        await lease._begin_operation()
        try:
            if type(timeout) is not int or timeout <= 0:
                raise WorkspaceStateError("timeout exceeds admitted ceiling", code="runtime_preflight_failed", lease_id=lease.lease_id)
            timeout_ms = timeout * 1000
            if timeout_ms > lease.plan.limits.action_timeout_ms:
                raise WorkspaceStateError("timeout exceeds admitted ceiling", code="runtime_preflight_failed", lease_id=lease.lease_id)
            return await lease._runtime.run_shell(command, timeout_ms=timeout_ms,
                                                  output_limit=lease.plan.limits.observation_bytes)
        finally:
            await lease._end_operation()

    async def read_text(self, path: str, *, offset: int = 0, limit: int | None = None) -> Mapping[str, Any]:
        lease = self.__lease
        await lease._begin_operation()
        remote_read = getattr(lease._runtime, "read_text", None)
        if callable(remote_read):
            try:
                return await remote_read(path, offset=offset, limit=limit)
            finally:
                await lease._end_operation()
        try:
            async with lease._io_lock:
                lease._resolve(path)
                ceiling = lease.plan.limits.observation_bytes
                if offset < 0 or limit is not None and (limit < 0 or limit > ceiling):
                    raise WorkspaceStateError("read limit invalid", code="output_limit_exceeded", lease_id=lease.lease_id)
                read_limit = limit if limit is not None else ceiling + 1
                try:
                    selected = await asyncio.to_thread(
                        _bounded_regular_read,
                        lease._materialized.workspace_path,
                        path,
                        offset=offset,
                        limit=read_limit,
                    )
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise WorkspaceStateError("workspace link authority denied", code="workspace_escape", lease_id=lease.lease_id) from exc
                if len(selected) > ceiling:
                    raise WorkspaceStateError("read exceeds admitted ceiling", code="output_limit_exceeded", lease_id=lease.lease_id)
                return {"path": path, "content": selected.decode("utf-8"), "offset": offset, "bytes": len(selected)}
        finally:
            await lease._end_operation()

    async def write_text(self, path: str, content: str) -> Mapping[str, Any]:
        lease = self.__lease
        payload = content.encode("utf-8")
        await lease._begin_operation()
        remote_write = getattr(lease._runtime, "write_text", None)
        if callable(remote_write):
            try:
                return await remote_write(path, content)
            finally:
                await lease._end_operation()
        try:
            async with lease._io_lock:
                lease._resolve(path, writable=True)
                if len(payload) > lease.plan.limits.artifact_bytes_each:
                    raise WorkspaceStateError("write exceeds admitted ceiling", code="output_limit_exceeded", lease_id=lease.lease_id)
                try:
                    await asyncio.to_thread(
                        _atomic_regular_write, lease._materialized.workspace_path, path, payload
                    )
                except OSError as exc:
                    raise WorkspaceStateError("workspace link authority denied", code="workspace_escape", lease_id=lease.lease_id) from exc
                return {"path": path, "bytes": len(payload)}
        finally:
            await lease._end_operation()

    async def list_files(self, path: str, *, depth: int) -> Mapping[str, Any]:
        lease = self.__lease
        await lease._begin_operation()
        remote_list = getattr(lease._runtime, "list_files", None)
        if callable(remote_list):
            try:
                return await remote_list(path, depth=depth)
            finally:
                await lease._end_operation()
        try:
            async with lease._io_lock:
                if depth < 0 or depth > lease.plan.security_policy.snapshot_max_depth:
                    raise WorkspaceStateError("list depth invalid", code="output_limit_exceeded", lease_id=lease.lease_id)
                try:
                    values = await asyncio.to_thread(
                        _descriptor_list,
                        lease._materialized.workspace_path,
                        path,
                        depth=depth,
                        max_entries=lease.plan.security_policy.snapshot_max_inodes,
                        output_limit=lease.plan.limits.observation_bytes,
                    )
                except OverflowError as exc:
                    raise WorkspaceStateError(
                        "listing exceeds admitted ceiling",
                        code="output_limit_exceeded",
                        lease_id=lease.lease_id,
                    ) from exc
                except OSError as exc:
                    raise WorkspaceStateError(
                        "workspace link authority denied",
                        code="workspace_escape",
                        lease_id=lease.lease_id,
                    ) from exc
                return {"path": path, "files": values}
        finally:
            await lease._end_operation()


class SandboxWorkspaceLease:
    def __init__(self, *, manager: SandboxRuntimeManager, lease_id: str, plan: SandboxExecutionPlan,
                 materialized: MaterializedWorkspace, runtime: RuntimeHandle,
                 measurement: SandboxMeasurement, owner_token: str, epoch: int) -> None:
        self.lease_id = lease_id; self.plan = plan; self.measurement = measurement
        self._manager = manager; self._materialized = materialized; self._runtime = runtime
        self._owner_token = owner_token; self._epoch = epoch
        self._state = WorkspaceLeaseState.ACTIVE
        self._lock = asyncio.Lock()
        self._operations_drained = asyncio.Condition(self._lock)
        self._active_operations = 0
        self._active_operation_tasks: dict[asyncio.Task[Any], int] = {}
        self._io_lock = asyncio.Lock()
        self._cleanup = None
        self._latest_cleanup = None
        self._close_task_lock = asyncio.Lock()
        self._close_task: asyncio.Task[SandboxCleanupReceipt] | None = None
        self.runner_workspace = LeaseBackedRunnerWorkspace(self, plan.effective_plan_digest, plan.tool_bindings)
        self._verifier_children: list[VerifierWorkspaceLease] = []
        self._sealed_workspace_diff: Mapping[str, Any] | None = None
    @property
    def identity(self) -> SandboxMeasurement:
        return self.measurement

    @property
    def capabilities(self) -> Any:
        return self.plan

    @property
    def cleanup_receipt(self) -> SandboxCleanupReceipt | None:
        return self._latest_cleanup
    async def execute(
        self, argv: Sequence[str], *, timeout_ms: int | None = None
    ) -> Mapping[str, Any]:
        self._assert_active()
        if not argv or any(
            type(item) is not str or not item or "\x00" in item for item in argv
        ):
            raise WorkspaceStateError(
                "execution argv is invalid",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        effective_timeout = self.plan.limits.action_timeout_ms if timeout_ms is None else timeout_ms
        if type(effective_timeout) is not int or effective_timeout <= 0 or effective_timeout > self.plan.limits.action_timeout_ms:
            raise WorkspaceStateError("timeout exceeds admitted ceiling", code="runtime_preflight_failed", lease_id=self.lease_id)
        await self._begin_operation()
        try:
            return await self._runtime.run_argv(
                tuple(argv), timeout_ms=effective_timeout,
                output_limit=self.plan.limits.observation_bytes,
            )
        finally:
            await self._end_operation()

    async def read_file(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> Mapping[str, Any]:
        return await self.runner_workspace.read_text(path, offset=offset, limit=limit)

    async def write_file(self, path: str, content: str) -> Mapping[str, Any]:
        return await self.runner_workspace.write_text(path, content)

    async def list_workspace_files(self, path: str, *, depth: int) -> Mapping[str, Any]:
        return await self.runner_workspace.list_files(path, depth=depth)

    async def workspace_diff(self) -> Mapping[str, Any]:
        self._assert_active()
        await self._begin_operation()
        try:
            remote_diff = getattr(self._runtime, "workspace_diff", None)
            if callable(remote_diff):
                return await remote_diff()
            raise WorkspaceStateError(
                "runtime does not implement workspace diff",
                code="runtime_unsupported",
                lease_id=self.lease_id,
            )
        finally:
            await self._end_operation()
    def sealed_workspace_diff(self) -> Mapping[str, Any] | None:
        return self._sealed_workspace_diff


    async def cancel(self) -> SandboxCleanupReceipt:
        return await self._close_shared(preempt=True)

    async def _preempt_and_close(self) -> SandboxCleanupReceipt:
        current = asyncio.current_task()
        async with self._lock:
            if self._cleanup is not None:
                return self._cleanup
            self._state = WorkspaceLeaseState.RELEASING
            active_tasks = tuple(
                task
                for task in self._active_operation_tasks
                if task is not current and not task.done()
            )
        for task in active_tasks:
            task.cancel()
        return await self._manager._close_lease(self)

    async def _preempt_operations(self) -> None:
        current = asyncio.current_task()
        async with self._lock:
            self._state = WorkspaceLeaseState.RELEASING
            active_tasks = tuple(
                task
                for task in self._active_operation_tasks
                if task is not current and not task.done()
            )
        for task in active_tasks:
            task.cancel()

    async def _close_shared(self, *, preempt: bool = False) -> SandboxCleanupReceipt:
        preempt_task: asyncio.Task[None] | None = None
        async with self._close_task_lock:
            if self._cleanup is not None:
                return self._cleanup
            close_task = self._close_task
            if close_task is None:
                close_task = asyncio.create_task(
                    self._preempt_and_close()
                    if preempt
                    else self._manager._close_lease(self)
                )
                self._close_task = close_task
            elif preempt:
                preempt_task = asyncio.create_task(self._preempt_operations())
        if preempt_task is not None:
            await asyncio.shield(preempt_task)
        try:
            return await asyncio.shield(close_task)
        finally:
            if close_task.done() and self._cleanup is None:
                async with self._close_task_lock:
                    if self._close_task is close_task:
                        self._close_task = None

    async def destroy(self) -> SandboxCleanupReceipt:
        return await self.close()

    @property
    def state(self) -> WorkspaceLeaseState: return self._state

    def _assert_active(self) -> None:
        if self._state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceStateError("workspace lease is not active", code="lease_not_active",
                                      episode_id=self.plan.episode_id,
                                      effective_plan_digest=self.plan.effective_plan_digest, lease_id=self.lease_id)

    async def _begin_operation(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise WorkspaceStateError(
                "workspace operation requires an asyncio task",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        async with self._lock:
            self._assert_active()
            self._active_operations += 1
            self._active_operation_tasks[task] = (
                self._active_operation_tasks.get(task, 0) + 1
            )

    async def _end_operation(self) -> None:
        task = asyncio.current_task()
        async with self._lock:
            self._active_operations -= 1
            if task is not None:
                remaining = self._active_operation_tasks.get(task, 0) - 1
                if remaining > 0:
                    self._active_operation_tasks[task] = remaining
                else:
                    self._active_operation_tasks.pop(task, None)
            if self._active_operations == 0:
                self._operations_drained.notify_all()

    async def _fence_and_drain(self, state: WorkspaceLeaseState) -> None:
        self._state = state
        while self._active_operations:
            await self._operations_drained.wait()

    def _resolve(self, logical_path: str, *, writable: bool = False) -> Path:
        relative = Path(logical_path)
        if not logical_path or relative.is_absolute() or ".." in relative.parts or "\x00" in logical_path:
            raise WorkspaceStateError("workspace path escapes", code="workspace_escape", lease_id=self.lease_id)
        cursor = self._materialized.workspace_path
        for part in relative.parts:
            cursor = cursor / part
            try:
                metadata = cursor.lstat()
            except FileNotFoundError:
                continue
            if __import__("stat").S_ISLNK(metadata.st_mode) or (__import__("stat").S_ISREG(metadata.st_mode) and metadata.st_nlink != 1):
                raise WorkspaceStateError("workspace link authority denied", code="workspace_escape", lease_id=self.lease_id)
        target = (self._materialized.workspace_path / relative).resolve(strict=False)
        if self._materialized.workspace_path != target and self._materialized.workspace_path not in target.parents:
            raise WorkspaceStateError("workspace path escapes", code="workspace_escape", lease_id=self.lease_id)
        if writable:
            writable_roots = [(self._materialized.workspace_path / item.target_logical_path).resolve()
                              for item in self.plan.materialization_plan.entries if item.access.value == "rw"]
            if not any(root == target or root in target.parents for root in writable_roots):
                raise WorkspaceStateError("path is outside writable authority", code="workspace_escape", lease_id=self.lease_id)
        return target

    async def seal_for_verifier(self) -> VerifierSnapshotReceipt:
        async with self._lock:
            self._assert_active()
            await self._fence_and_drain(WorkspaceLeaseState.QUIESCING)
            runtime_steps = await self._runtime.terminate()
            if any(
                step.state
                not in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in runtime_steps
            ):
                self._state = WorkspaceLeaseState.QUARANTINED
                raise VerifierSnapshotError(
                    "runtime did not quiesce",
                    code="snapshot_not_quiescent",
                    lease_id=self.lease_id,
                )
            base_commit = getattr(self._runtime, "repository_base_commit", None)
            relative_path = getattr(self._runtime, "repository_relative_path", None)
            if (base_commit is None) != (relative_path is None):
                self._state = WorkspaceLeaseState.QUARANTINED
                raise VerifierSnapshotError(
                    "workspace base authority is incomplete",
                    code="snapshot_tampered",
                    lease_id=self.lease_id,
                )
            try:
                seal_task = asyncio.create_task(
                    asyncio.to_thread(
                        self._manager.materialization_store.seal_snapshot,
                        self._materialized,
                        source_lease_id=self.lease_id,
                        effective_plan_digest=self.plan.effective_plan_digest,
                        task_digest=_wp7_digest(
                            dict(self.plan.materialization_plan.task_projection)
                        ),
                        verifier_digest=self.plan.verifier.grant.implementation_digest,
                        max_depth=self.plan.security_policy.snapshot_max_depth,
                        max_files=self.plan.security_policy.snapshot_max_files,
                        max_inodes=self.plan.security_policy.snapshot_max_inodes,
                        max_bytes=self.plan.limits.artifact_bytes_total,
                    )
                )
                try:
                    receipt, path = await asyncio.shield(seal_task)
                except asyncio.CancelledError as cancellation:
                    try:
                        receipt, path = await seal_task
                    except BaseException:
                        self._state = WorkspaceLeaseState.QUARANTINED
                        raise cancellation from None
                    self._manager._snapshots[receipt.snapshot_id] = (receipt, path)
                    snapshot_cleanup = await self._manager._release_snapshot(
                        receipt.snapshot_id
                    )
                    if snapshot_cleanup.state not in {
                        CleanupState.RELEASED,
                        CleanupState.ALREADY_RELEASED,
                    }:
                        self._state = WorkspaceLeaseState.QUARANTINED
                    raise
                self._manager._snapshots[receipt.snapshot_id] = (receipt, path)
                if base_commit is not None:
                    snapshot_repository = (
                        path if relative_path == "." else path.joinpath(*_workspace_parts(relative_path))
                    )
                    diff_task = asyncio.create_task(
                        asyncio.to_thread(
                            _sealed_repository_diff,
                            repository=snapshot_repository,
                            base_commit=base_commit,
                            plan=self.plan,
                        )
                    )
                    try:
                        sealed_diff = await asyncio.shield(diff_task)
                    except BaseException:
                        try:
                            await diff_task
                        except BaseException:
                            pass
                        snapshot_cleanup = await self._manager._release_snapshot(
                            receipt.snapshot_id
                        )
                        if snapshot_cleanup.state not in {
                            CleanupState.RELEASED,
                            CleanupState.ALREADY_RELEASED,
                        }:
                            self._state = WorkspaceLeaseState.QUARANTINED
                        raise
                    patch_bytes = sealed_diff["stdout"].encode("utf-8")
                    self._sealed_workspace_diff = MappingProxyType(
                        {
                            **sealed_diff,
                            "patch_digest": (
                                "sha256:" + hashlib.sha256(patch_bytes).hexdigest()
                            ),
                            "snapshot_root_digest": receipt.root_digest,
                        }
                    )
                return receipt
            except Exception as exc:
                self._state = WorkspaceLeaseState.QUARANTINED
                raise VerifierSnapshotError(
                    "verifier snapshot failed", code=str(exc), lease_id=self.lease_id
                ) from exc

    async def close(self) -> SandboxCleanupReceipt:
        return await self._close_shared()

class VerifierWorkspaceLease:
    def __init__(self, *, manager: SandboxRuntimeManager, primary: SandboxWorkspaceLease,
                 lease_id: str, plan: SandboxExecutionPlan, snapshot: VerifierSnapshotReceipt,
                 workspace: Path, runtime: RuntimeHandle, measurement: SandboxMeasurement) -> None:
        self._manager = manager; self._primary = primary; self.lease_id = lease_id
        self.plan = plan; self.snapshot = snapshot; self.workspace = workspace
        self._runtime = runtime; self.measurement = measurement
        self._closed = False
        self._closing = False
        self._fenced = False
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._operations_drained = asyncio.Condition(self._lock)
        self._active_operation_tasks: dict[asyncio.Task[Any], int] = {}
        self._cleanup: SandboxCleanupReceipt | None = None

        self._close_task: asyncio.Task[SandboxCleanupReceipt] | None = None
    async def execute(self) -> Mapping[str, Any]:
        task = asyncio.current_task()
        if task is None:
            raise WorkspaceStateError(
                "verifier operation requires an asyncio task",
                code="runtime_preflight_failed",
                lease_id=self.lease_id,
            )
        async with self._lock:
            if self._closed or self._closing or self._fenced:
                raise WorkspaceStateError(
                    "verifier lease is not active",
                    code="lease_not_active",
                    lease_id=self.lease_id,
                )
            self._active_operation_tasks[task] = (
                self._active_operation_tasks.get(task, 0) + 1
            )
        try:
            return await self._execute_active()
        finally:
            await asyncio.shield(self._finish_operation(task))

    async def _finish_operation(self, task: asyncio.Task[Any]) -> None:
        async with self._lock:
            remaining = self._active_operation_tasks.get(task, 0) - 1
            if remaining > 0:
                self._active_operation_tasks[task] = remaining
            else:
                self._active_operation_tasks.pop(task, None)
            if not self._active_operation_tasks:
                self._operations_drained.notify_all()

    async def _execute_active(self) -> Mapping[str, Any]:
        result = await self._runtime.run_argv(
            self.plan.verifier.argv,
            timeout_ms=self.plan.limits.verifier_timeout_ms,
            output_limit=self.plan.limits.observation_bytes,
        )
        if result.get("returncode") != 0:
            raise VerifierExecutionError(
                "verifier exited unsuccessfully",
                code="verifier_result_malformed",
                lease_id=self.lease_id,
            )
        result_root = self.workspace / "result"
        ceiling = min(
            self.plan.limits.artifact_bytes_each,
            self.plan.limits.artifact_bytes_total,
            VERIFIER_RESULT_MAX_BYTES,
        )
        try:
            remote_read = getattr(self._runtime, "read_artifact_text", None)
            if callable(remote_read):
                remote = await remote_read(
                    "result/" + self.plan.verifier.result_relative_path
                )
                raw = str(remote["content"]).encode("utf-8")
            else:
                raw = await asyncio.to_thread(
                    _bounded_regular_read,
                    result_root,
                    self.plan.verifier.result_relative_path,
                    offset=0,
                    limit=ceiling + 1,
                )
            if len(raw) > ceiling:
                raise ValueError("result too large")
            payload = __import__("json").loads(raw)
        except Exception as exc:
            raise VerifierExecutionError(
                "verifier result is malformed",
                code="verifier_result_malformed",
                lease_id=self.lease_id,
            ) from exc
        expected = {
            "episode_id": self.plan.episode_id,
            "effective_plan_digest": self.plan.effective_plan_digest,
            "task_digest": self.snapshot.task_digest,
            "snapshot_digest": self.snapshot.root_digest,
            "verifier_digest": self.plan.verifier.grant.implementation_digest,
        }
        if not isinstance(payload, dict) or any(
            payload.get(key) != value for key, value in expected.items()
        ):
            raise VerifierExecutionError(
                "verifier result identity mismatch",
                code="verifier_result_identity_mismatch",
                lease_id=self.lease_id,
            )
        return MappingProxyType(payload)

    async def close(self) -> SandboxCleanupReceipt:
        async with self._close_lock:
            if self._cleanup is not None:
                return self._cleanup
            close_task = self._close_task
            if close_task is None:
                close_task = asyncio.create_task(self._close_attempt())
                self._close_task = close_task
        try:
            return await asyncio.shield(close_task)
        finally:
            if close_task.done() and self._cleanup is None:
                async with self._close_lock:
                    if self._close_task is close_task:
                        self._close_task = None

    async def _close_attempt(self) -> SandboxCleanupReceipt:
        current = asyncio.current_task()
        async with self._lock:
            if self._cleanup is not None:
                return self._cleanup
            self._closing = True
            self._fenced = True
            active_tasks = tuple(
                task
                for task in self._active_operation_tasks
                if task is not current and not task.done()
            )
        for task in active_tasks:
            task.cancel()
        async with self._lock:
            if self._cleanup is not None:
                return self._cleanup
            while self._active_operation_tasks:
                await self._operations_drained.wait()
            steps = list(await self._runtime.terminate())
            runtime_released = all(
                step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in steps
            )
            if runtime_released:
                try:
                    for root, dirs, files in os.walk(self.workspace, topdown=True, followlinks=False):
                        root_path = Path(root)
                        os.chmod(root_path, 0o700, follow_symlinks=False)
                        for name in dirs + files:
                            candidate = root_path / name
                            if candidate.is_symlink():
                                continue
                            os.chmod(candidate, 0o700 if candidate.is_dir() else 0o600,
                                     follow_symlinks=False)
                    await asyncio.to_thread(self._manager.materialization_store.storage_backend.release, self.workspace)
                    absent = self._manager.materialization_store.storage_backend.verify_absent(self.workspace)
                    steps.append(CleanupStepReceipt("workspace", CleanupState.RELEASED if absent else CleanupState.FAILED))
                except FileNotFoundError:
                    steps.append(CleanupStepReceipt("workspace", CleanupState.ALREADY_RELEASED))
                except Exception as exc:
                    steps.append(CleanupStepReceipt("workspace", CleanupState.FAILED, type(exc).__name__))
            else:
                steps.append(CleanupStepReceipt(
                    "workspace", CleanupState.QUARANTINED, "dependent runtime cleanup incomplete"
                ))
            dependencies_released = all(
                step.state in {
                    CleanupState.RELEASED,
                    CleanupState.ALREADY_RELEASED,
                }
                for step in steps
            )
            if dependencies_released:
                steps.append(
                    await self._manager._release_snapshot(self.snapshot.snapshot_id)
                )
            else:
                steps.append(
                    CleanupStepReceipt(
                        "snapshot",
                        CleanupState.QUARANTINED,
                        "dependent runtime cleanup incomplete",
                    )
                )
            prior_ok = all(
                step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in steps
            )
            try:
                if prior_ok:
                    self._manager._unlink_lease_record(self.lease_id)
                    absent = not self._manager._lease_record_exists(self.lease_id)
                    steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.RELEASED if absent else CleanupState.FAILED
                    ))
                else:
                    steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.QUARANTINED, "dependent cleanup incomplete"
                    ))
            except Exception as exc:
                steps.append(CleanupStepReceipt("lease_record", CleanupState.FAILED, type(exc).__name__))
            receipt = SandboxCleanupReceipt.from_steps(self.lease_id, tuple(steps))
            if receipt.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}:
                self._closed = True; self._cleanup = receipt
            return receipt


@dataclass
class _PendingLaunchCleanup:
    workspace: Path
    materialized: MaterializedWorkspace | None
    runtime: RuntimeHandle | None
    backend_cleanup_pending: bool


class SandboxRuntimeManager:
    def __init__(self, *, registries: RegistrySnapshotSet,
                 installed_authorities: InstalledSandboxAuthoritySet,
                 materialization_store: FilesystemMaterializationStore,
                 lease_root: str | Path, process_backend: RuntimeBackend,
                 docker_backend: RuntimeBackend | None, random_bytes: Any,
                 lease_root_fd: int | None = None) -> None:
        self.registries = registries; self.installed_authorities = installed_authorities
        self.materialization_store = materialization_store
        supplied_lease_root = Path(lease_root).resolve(strict=True)
        self._lease_root_fd = (
            os.dup(lease_root_fd)
            if lease_root_fd is not None
            else os.open(supplied_lease_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        )
        try:
            opened_root = os.fstat(self._lease_root_fd)
            supplied_root = os.stat(supplied_lease_root, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened_root.st_mode)
                or (opened_root.st_dev, opened_root.st_ino)
                != (supplied_root.st_dev, supplied_root.st_ino)
            ):
                raise ValueError("lease root descriptor identity mismatch")
        except BaseException:
            os.close(self._lease_root_fd)
            self._lease_root_fd = None
            raise
        self.lease_root = supplied_lease_root
        self.process_backend = process_backend; self.docker_backend = docker_backend
        self._random_bytes = random_bytes; self._leases: dict[str, SandboxWorkspaceLease] = {}
        self._snapshots: dict[str, tuple[VerifierSnapshotReceipt, Path]] = {}
        self._pending_launch_cleanups: dict[str, _PendingLaunchCleanup] = {}
        self._lease_owner_locks: dict[str, int] = {}
        self._lock = asyncio.Lock(); self._closed = False
        self._reconcile_lock = asyncio.Lock()
        self._close_task: asyncio.Future[list[SandboxCleanupReceipt]] | None = None
        self._last_close_receipts: tuple[SandboxCleanupReceipt, ...] | None = None

    def abort_bootstrap(self) -> None:
        """Release constructor-owned descriptors before any lease can be admitted."""
        if (
            self._leases
            or self._snapshots
            or self._lease_owner_locks
            or self._close_task is not None
        ):
            raise RuntimeError("cannot abort sandbox manager after runtime admission")
        self._closed = True
        if self._lease_root_fd is not None:
            os.close(self._lease_root_fd)
            self._lease_root_fd = None

    def _nonce(self) -> str:
        value = self._random_bytes(16)
        if type(value) is not bytes or len(value) < 16: raise ValueError("random source returned insufficient bytes")
        return value.hex()

    @staticmethod
    def _make_workspace_releasable(workspace: Path) -> None:
        for root, dirs, filenames in os.walk(workspace, topdown=True, followlinks=False):
            root_path = Path(root)
            os.chmod(root_path, 0o700, follow_symlinks=False)
            for name in dirs + filenames:
                candidate = root_path / name
                if candidate.is_symlink():
                    continue
                os.chmod(
                    candidate,
                    0o700 if candidate.is_dir() else 0o600,
                    follow_symlinks=False,
                )

    def _launch_context(
        self,
        *,
        plan: SandboxExecutionPlan,
        workspace: Path,
        role: Literal["primary", "verifier"],
        lease_id: str,
        workspace_id: str,
        epoch: int,
        quota_bytes: int,
        workspace_fd: int,
        workspace_identity: tuple[int, int],
        owner_token: str,
    ) -> RuntimeLaunchContext:
        measured = dict(self.materialization_store.storage_backend.measure(workspace))
        authority = measured.get("authority_id")
        quota_enforced = measured.get("quota_enforced")
        measured_quota = measured.get("quota_bytes")
        owner_uid = measured.get("owner_uid")
        owner_gid = measured.get("owner_gid")
        isolated = plan.isolation_disposition is IsolationDisposition.ISOLATED
        if isolated and (
            type(authority) is not str
            or not authority
            or quota_enforced is not True
            or type(measured_quota) is not int
            or measured_quota != quota_bytes
        ):
            raise SandboxLaunchError(
                "isolated runtime requires exact quota storage authority",
                code="runtime_preflight_failed",
                lease_id=lease_id,
            )
        if type(owner_uid) is not int or type(owner_gid) is not int:
            raise SandboxLaunchError(
                "workspace ownership measurement is incomplete",
                code="runtime_preflight_failed",
                lease_id=lease_id,
            )
        storage = WorkspaceStorageIdentity(
            authority_id=(
                authority
                if type(authority) is str and authority
                else f"{type(self.materialization_store.storage_backend).__module__}."
                     f"{type(self.materialization_store.storage_backend).__qualname__}"
            ),
            quota_enforced=quota_enforced is True,
            quota_bytes=measured_quota if type(measured_quota) is int and measured_quota > 0 else quota_bytes,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )
        return RuntimeLaunchContext(
            role=role,
            lease_id=lease_id,
            workspace_id=workspace_id,
            epoch=epoch,
            storage=storage,
            snapshot_relative_path=None if role == "primary" else "snapshot",
            result_relative_path=None if role == "primary" else "result",
            publish_prepared_identity=lambda identity, lease_id=lease_id:
                self._publish_runtime_identity(lease_id, identity),
            workspace_fd=workspace_fd,
            workspace_identity=workspace_identity,
            owner_token=owner_token,
        )

    def _claim_lease_owner_lock(self, lease_id: str) -> bool:
        if lease_id in self._lease_owner_locks:
            return True
        if self._lease_root_fd is None:
            raise RuntimeError("sandbox manager is closed")
        lock_name = lease_id + ".owner.lock"
        descriptor = os.open(
            lock_name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self._lease_root_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise WorkspaceStateError(
                    "lease owner lock identity is invalid",
                    code="stale_identity_uncertain",
                    lease_id=lease_id,
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self._lease_owner_locks[lease_id] = descriptor
            return True
        finally:
            if self._lease_owner_locks.get(lease_id) != descriptor:
                os.close(descriptor)

    def _release_lease_owner_lock(
        self,
        lease_id: str,
        *,
        unlink: bool,
    ) -> None:
        descriptor = self._lease_owner_locks.pop(lease_id, None)
        if descriptor is None:
            return
        try:
            if unlink and self._lease_root_fd is not None:
                try:
                    os.unlink(
                        lease_id + ".owner.lock",
                        dir_fd=self._lease_root_fd,
                    )
                except FileNotFoundError:
                    pass
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


    def _lease_record_path(self, lease_id: str) -> Path:
        return self.lease_root / (lease_id + ".json")
    def _lease_record_exists(self, lease_id: str) -> bool:
        if self._lease_root_fd is None:
            return False
        try:
            metadata = os.stat(
                lease_id + ".json",
                dir_fd=self._lease_root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1

    def _unlink_lease_record(self, lease_id: str) -> None:
        if self._lease_root_fd is None:
            raise RuntimeError("sandbox manager is closed")
        try:
            os.unlink(lease_id + ".json", dir_fd=self._lease_root_fd)
        except FileNotFoundError:
            pass
        self._release_lease_owner_lock(lease_id, unlink=True)
        os.fsync(self._lease_root_fd)


    def _write_lease_record(self, lease_id: str, payload: Mapping[str, Any]) -> None:
        if payload.get("lease_id") != lease_id:
            raise WorkspaceStateError(
                "workspace lease record identity mismatch",
                code="stale_identity_uncertain",
                lease_id=lease_id,
            )
        record = canonical_json_bytes({"payload": dict(payload), "checksum": _wp7_digest(dict(payload))})
        path = self._lease_record_path(lease_id)
        temporary = path.name + ".tmp-" + self._nonce()
        if self._lease_root_fd is None:
            raise RuntimeError("sandbox manager is closed")
        directory_fd = os.dup(self._lease_root_fd)
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            view = memoryview(record)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short lease record write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)

    def _read_lease_record(self, path: Path) -> Mapping[str, Any]:
        try:
            if self._lease_root_fd is None:
                raise RuntimeError("sandbox manager is closed")
            raw = _bounded_regular_read(
                self._lease_root_fd, path.name, offset=0, limit=65_537
            )
            if len(raw) > 65_536:
                raise ValueError
            envelope = __import__("json").loads(raw)
            payload = envelope["payload"]
            if (
                envelope["checksum"] != _wp7_digest(payload)
                or payload["schema_version"] != "bb.rl.workspace-lease.v1"
                or payload.get("lease_id") != path.stem
            ):
                raise ValueError
            return MappingProxyType(payload)
        except Exception as exc:
            raise WorkspaceStateError("workspace lease record is corrupt", code="stale_identity_uncertain") from exc

    async def _publish_runtime_identity(
        self, lease_id: str, identity: RuntimePreparedIdentity
    ) -> None:
        if type(identity) is not RuntimePreparedIdentity:
            raise WorkspaceStateError(
                "prepared runtime identity is invalid",
                code="stale_identity_uncertain",
                lease_id=lease_id,
            )
        path = self._lease_record_path(lease_id)
        record = dict(self._read_lease_record(path))
        if (
            record.get("lease_id") != lease_id
            or record.get("role") not in {"primary", "verifier"}
            or record.get("state") != "allocating"
            or "runtime_resource_id" in record
        ):
            raise WorkspaceStateError(
                "prepared runtime publication is not exact",
                code="stale_identity_uncertain",
                lease_id=lease_id,
            )
        record["runtime_resource_id"] = identity.runtime_resource_id
        record["runtime_labels"] = dict(identity.labels)
        self._write_lease_record(lease_id, record)


    def _record_process_identity(
        self, lease_id: str, resource_id: str, identity: Mapping[str, Any] | None
    ) -> None:
        path = self._lease_record_path(lease_id)
        record = dict(self._read_lease_record(path))
        if (
            record.get("lease_id") != lease_id
            or record.get("role") not in {"primary", "verifier"}
            or type(resource_id) is not str
            or not resource_id
        ):
            raise WorkspaceStateError(
                "process lease identity changed", code="stale_identity_uncertain", lease_id=lease_id
            )
        identities = {
            item["resource_id"]: dict(item)
            for item in record.get("process_identities", ())
            if type(item) is dict and type(item.get("resource_id")) is str
        }
        if identity is None:
            identities.pop(resource_id, None)
        else:
            if (
                type(identity.get("process_pid")) is not int
                or type(identity.get("process_group_id")) is not int
                or type(identity.get("process_session_id")) is not int
                or type(identity.get("process_start_identity")) is not str
                or type(identity.get("process_cgroup_identity")) is not str
                or resource_id != f"process-group-{identity.get('process_group_id')}"
            ):
                raise WorkspaceStateError(
                    "process identity is incomplete", code="stale_identity_uncertain", lease_id=lease_id
                )
            if resource_id in identities and identities[resource_id] != {
                "resource_id": resource_id, **identity
            }:
                raise WorkspaceStateError(
                    "process identity changed", code="stale_identity_uncertain", lease_id=lease_id
                )
            identities[resource_id] = {"resource_id": resource_id, **identity}
        record["process_identities"] = [
            identities[key] for key in sorted(identities)
        ]
        for key in (
            "process_pid", "process_group_id", "process_session_id",
            "process_start_identity", "process_cgroup_identity",
        ):
            record.pop(key, None)
        self._write_lease_record(lease_id, record)

    async def _release_snapshot(self, snapshot_id: str) -> CleanupStepReceipt:
        canonical = self._snapshots.get(snapshot_id)
        if canonical is None:
            return CleanupStepReceipt("snapshot", CleanupState.ALREADY_RELEASED)
        receipt, path = canonical
        release_task = asyncio.create_task(
            asyncio.to_thread(
                self.materialization_store.release_snapshot,
                receipt,
                path,
            )
        )
        try:
            absent = await asyncio.shield(release_task)
        except asyncio.CancelledError:
            try:
                await release_task
            finally:
                raise
        except BaseException as exc:
            return CleanupStepReceipt(
                "snapshot",
                CleanupState.FAILED,
                type(exc).__name__,
            )
        if absent:
            self._snapshots.pop(snapshot_id, None)
        return CleanupStepReceipt(
            "snapshot",
            CleanupState.RELEASED if absent else CleanupState.FAILED,
        )

    async def _release_durable_snapshots_for_lease(
        self,
        lease_id: str,
    ) -> CleanupStepReceipt | None:
        release_task = asyncio.create_task(
            asyncio.to_thread(
                self.materialization_store.release_snapshots_for_lease,
                lease_id,
            )
        )
        try:
            released = await asyncio.shield(release_task)
        except asyncio.CancelledError:
            try:
                await release_task
            finally:
                raise
        except BaseException as exc:
            return CleanupStepReceipt(
                "snapshot",
                CleanupState.FAILED,
                type(exc).__name__,
            )
        if not released:
            return None
        return CleanupStepReceipt(
            "snapshot",
            CleanupState.RELEASED,
            ",".join(released),
        )


    async def open(self, request: WorkspaceOpenRequest) -> SandboxWorkspaceLease:
        plan = build_sandbox_execution_plan(request, self.registries, self.installed_authorities)
        async with self._lock:
            if self._closed: raise WorkspaceStateError("lease manager closed", code="lease_manager_closed")
            lease_id = "lease-" + self._nonce()
            owner_token = self._nonce(); epoch = 1
            materialized: MaterializedWorkspace | None = None
            runtime: RuntimeHandle | None = None
            backend: RuntimeBackend | None = None
            record_written = False
            if not self._claim_lease_owner_lock(lease_id):
                raise WorkspaceStateError(
                    "lease owner identity is already active",
                    code="stale_identity_uncertain",
                    lease_id=lease_id,
                )
            try:
                materialize_task = asyncio.create_task(
                    asyncio.to_thread(
                        self.materialization_store.materialize,
                        plan.materialization_plan,
                    )
                )
                try:
                    materialized = await asyncio.shield(materialize_task)
                except asyncio.CancelledError as cancellation:
                    try:
                        materialized = await materialize_task
                    except BaseException:
                        raise cancellation from None
                    raise
                issued = self.materialization_store.clock.current()
                cache_identity = {
                    "cache_lease_id": materialized.cache_token.lease_id,
                    "cache_holder_id": materialized.cache_token.holder_id,
                    "cache_token_value": materialized.cache_token.owner_token,
                    "cache_epoch": materialized.cache_token.epoch,
                    "cache_key_digest": materialized.cache_token.cache_key.digest,
                    "cache_manifest_digest": materialized.cache_receipt.immutable_object_manifest_digest,
                    "cache_source_digests": [
                        entry.source_digest for entry in plan.materialization_plan.entries
                    ],
                }
                self._write_lease_record(lease_id, {"schema_version": "bb.rl.workspace-lease.v1",
                    "lease_id": lease_id, "workspace_id": materialized.receipt.workspace_id,
                    "workspace_path": str(materialized.workspace_path), "cache_lease_id": materialized.receipt.cache_lease_id,
                    "effective_plan_digest": plan.effective_plan_digest, "owner_token": owner_token, "epoch": epoch,
                    "expires_at": (issued + self.materialization_store.lease_ttl).isoformat(), "role": "primary",
                    **cache_identity,
                    "runtime_id": plan.runtime.runtime_id, "state": "allocating"})
                record_written = True
                backend = self.process_backend if plan.runtime.runtime_class is RuntimeClass.TRUSTED_PROCESS else self.docker_backend
                if backend is None: raise SandboxLaunchError("runtime backend unavailable", code="runtime_unsupported")
                context = self._launch_context(
                    plan=plan,
                    workspace=materialized.workspace_path,
                    role="primary",
                    lease_id=lease_id,
                    workspace_id=materialized.receipt.workspace_id,
                    epoch=epoch,
                    quota_bytes=plan.resources.storage_bytes,
                    workspace_fd=materialized.duplicate_workspace_fd(),
                    workspace_identity=materialized.workspace_identity,
                    owner_token=owner_token,
                )
                runtime, measurement = await backend.launch(
                    plan, materialized.workspace_path, context=context
                )
                if isinstance(runtime, TrustedProcessHandle):
                    runtime.bind_identity_recorder(
                        lambda resource_id, identity, lease_id=lease_id:
                            self._record_process_identity(lease_id, resource_id, identity)
                    )
                if measurement.mismatch:
                    raise SandboxAttestationError("runtime measurement mismatch", code="runtime_measurement_mismatch",
                                                  lease_id=lease_id)
                for setup in plan.setups:
                    result = await runtime.run_argv(setup.argv,
                                                    timeout_ms=min(setup.timeout_ms, plan.limits.setup_timeout_ms),
                                                    output_limit=plan.limits.observation_bytes)
                    if result.get("returncode") != 0:
                        raise SandboxLaunchError("setup failed", code="runtime_launch_failed", lease_id=lease_id)
                lease = SandboxWorkspaceLease(manager=self, lease_id=lease_id, plan=plan,
                    materialized=materialized, runtime=runtime, measurement=measurement,
                    owner_token=owner_token, epoch=epoch)
                active_record = dict(self._read_lease_record(self._lease_record_path(lease_id)))
                prepared_resource_id = active_record.get("runtime_resource_id")
                if (
                    prepared_resource_id is not None
                    and prepared_resource_id != runtime.runtime_id
                ):
                    raise WorkspaceStateError(
                        "prepared runtime identity changed",
                        code="stale_identity_uncertain",
                        lease_id=lease_id,
                    )
                active_record.update({
                    "runtime_authority_id": plan.runtime.runtime_id,
                    "runtime_resource_id": runtime.runtime_id,
                    "storage_authority_id": context.storage.authority_id,
                    "storage_quota_bytes": context.storage.quota_bytes,
                    "runtime_executable_path": plan.runtime.executable_path,
                    "runtime_binary_digest": plan.runtime.measured_binary_digest,
                    "action_timeout_ms": plan.limits.action_timeout_ms,
                    "observation_bytes": plan.limits.observation_bytes,
                    "state": "active"})
                self._write_lease_record(lease_id, active_record)
                self._leases[lease_id] = lease
                return lease
            except BaseException as primary:
                cleanup_steps: list[CleanupStepReceipt] = []
                cleanup_errors: list[str] = []
                if runtime is not None:
                    try:
                        cleanup_steps.extend(await runtime.terminate())
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(f"runtime:{type(cleanup_exc).__name__}")
                        cleanup_steps.append(CleanupStepReceipt("runtime", CleanupState.FAILED,
                                                                type(cleanup_exc).__name__))
                backend_cleanup_pending = (
                    runtime is None
                    and backend is self.docker_backend
                    and bool(getattr(backend, "cleanup_pending", False))
                )
                runtime_cleanup_pending = (
                    runtime is not None
                    and not _runtime_cleanup_released(cleanup_steps)
                )
                if backend_cleanup_pending:
                    cleanup_steps.append(CleanupStepReceipt(
                        "runtime", CleanupState.QUARANTINED,
                        "backend retained launch cleanup authority",
                    ))
                if (
                    materialized is not None
                    and (backend_cleanup_pending or runtime_cleanup_pending)
                ):
                    self._pending_launch_cleanups[lease_id] = _PendingLaunchCleanup(
                        workspace=materialized.workspace_path,
                        materialized=materialized,
                        runtime=runtime if runtime_cleanup_pending else None,
                        backend_cleanup_pending=backend_cleanup_pending,
                    )
                runtime_released = (
                    runtime is None and not backend_cleanup_pending
                ) or _runtime_cleanup_released(cleanup_steps)
                if materialized is not None and runtime_released:
                    try:
                        await asyncio.to_thread(materialized.close)
                        cleanup_steps.append(CleanupStepReceipt("workspace", CleanupState.RELEASED))
                        cleanup_steps.append(CleanupStepReceipt("cache_holder", CleanupState.RELEASED))
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(f"workspace:{type(cleanup_exc).__name__}")
                        cleanup_steps.append(CleanupStepReceipt("workspace", CleanupState.FAILED,
                                                                type(cleanup_exc).__name__))
                elif materialized is not None:
                    cleanup_steps.append(CleanupStepReceipt(
                        "workspace", CleanupState.QUARANTINED, "dependent runtime cleanup incomplete"
                    ))
                    cleanup_steps.append(CleanupStepReceipt(
                        "cache_holder", CleanupState.QUARANTINED, "dependent runtime cleanup incomplete"
                    ))
                dependencies_released = all(
                    step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                    for step in cleanup_steps
                )
                try:
                    if record_written and dependencies_released:
                        self._unlink_lease_record(lease_id)
                        cleanup_steps.append(CleanupStepReceipt("lease_record", CleanupState.RELEASED))
                    elif record_written:
                        cleanup_steps.append(CleanupStepReceipt(
                            "lease_record", CleanupState.QUARANTINED,
                            "dependent cleanup incomplete"
                        ))
                    else:
                        self._release_lease_owner_lock(lease_id, unlink=True)
                        cleanup_steps.append(CleanupStepReceipt("lease_record", CleanupState.ALREADY_RELEASED))
                except BaseException as cleanup_exc:
                    cleanup_errors.append(f"lease_record:{type(cleanup_exc).__name__}")
                    cleanup_steps.append(CleanupStepReceipt("lease_record", CleanupState.FAILED,
                                                            type(cleanup_exc).__name__))
                receipt = SandboxCleanupReceipt.from_steps(lease_id, tuple(cleanup_steps))
                if cleanup_errors or receipt.state not in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}:
                    raise SandboxFault(primary, receipt, tuple(cleanup_errors)) from primary
                raise

    async def open_verifier(self, primary: SandboxWorkspaceLease,
                            snapshot: VerifierSnapshotReceipt) -> VerifierWorkspaceLease:
        if (
            type(primary) is not SandboxWorkspaceLease
            or primary._manager is not self
            or self._leases.get(primary.lease_id) is not primary
        ):
            raise VerifierSnapshotError(
                "primary lease is not canonically owned by this manager",
                code="snapshot_not_quiescent",
            )
        async with primary._lock:
            if primary.state is not WorkspaceLeaseState.QUIESCING:
                raise VerifierSnapshotError("snapshot does not bind quiesced primary", code="snapshot_not_quiescent")
            if (
                primary.plan.episode_id
                != primary.plan.materialization_plan.episode_id
            ):
                raise VerifierSnapshotError(
                    "primary episode identity mismatch", code="snapshot_not_quiescent"
                )
            if (
                primary.plan.effective_plan_digest
                != primary._materialized.receipt.effective_plan_digest
                or primary.measurement.effective_plan_digest
                != primary.plan.effective_plan_digest
                or primary.measurement.lease_id != primary.lease_id
            ):
                raise VerifierSnapshotError(
                    "primary plan identity mismatch", code="snapshot_not_quiescent"
                )
            verifier = _exact_one(
                self.installed_authorities.verifiers,
                lambda item: item.grant.verifier_id
                == primary.plan.verifier.grant.verifier_id,
                missing_code="verifier_authority_mismatch",
            )
            if verifier != primary.plan.verifier:
                raise VerifierExecutionError(
                    "verifier authority is not canonical",
                    code="verifier_authority_mismatch",
                )
            runtime = _exact_one(
                self.installed_authorities.runtimes,
                lambda item: item.runtime_id == verifier.runtime_id,
                missing_code="verifier_authority_mismatch",
            )
            if (
                primary.measurement.reward_eligible
                and runtime.runtime_class not in {
                    RuntimeClass.HARDENED_DOCKER,
                    RuntimeClass.HARDENED_GVISOR,
                }
            ):
                raise VerifierExecutionError(
                    "reward-eligible primary requires an isolated verifier",
                    code="verifier_authority_mismatch",
                )
            canonical = self._snapshots.get(snapshot.snapshot_id)
            if canonical is None or canonical[0] != snapshot:
                raise VerifierSnapshotError("snapshot receipt is not canonical", code="snapshot_tampered")
            canonical_receipt, snapshot_path = canonical
            if snapshot.source_lease_id != primary.lease_id or (
                snapshot.effective_plan_digest != primary.plan.effective_plan_digest
            ):
                raise VerifierSnapshotError("snapshot identity mismatch", code="snapshot_tampered")
            if not snapshot_path.is_dir():
                raise VerifierSnapshotError("sealed snapshot storage missing", code="snapshot_tampered")
            image = _exact_one(self.installed_authorities.images,
                               lambda item: item.image_digest == verifier.grant.image_digest,
                               missing_code="verifier_authority_mismatch")
            security = _exact_one(self.installed_authorities.security_policies,
                                  lambda item: item.policy_digest == verifier.security_policy_digest,
                                  missing_code="verifier_authority_mismatch")
            network = _exact_one(self.installed_authorities.network_policies,
                                 lambda item: item.policy_digest == verifier.grant.network_policy_digest,
                                 missing_code="verifier_authority_mismatch")
            if runtime.runtime_class is not verifier.runtime_class or image.runtime_id != runtime.runtime_id:
                raise VerifierExecutionError("verifier runtime authority mismatch", code="verifier_authority_mismatch")
            verifier_plan = replace(primary.plan, runtime=runtime, image=image, security_policy=security,
                                    network_policy=network,
                                    isolation_disposition=(IsolationDisposition.TRUSTED_PROCESS
                                        if runtime.runtime_class is RuntimeClass.TRUSTED_PROCESS else IsolationDisposition.ISOLATED))
            workspace_id = "verifier-workspace-" + self._nonce()
            lease_id = "verifier-lease-" + self._nonce()
            owner_token = self._nonce()
            issued = self.materialization_store.clock.current()
            quota_bytes = min(primary.plan.resources.storage_bytes, primary.plan.limits.artifact_bytes_total)
            workspace = self.materialization_store.workspace_root / workspace_id
            try:
                if not self._claim_lease_owner_lock(lease_id):
                    raise WorkspaceStateError(
                        "verifier lease owner identity is already active",
                        code="stale_identity_uncertain",
                        lease_id=lease_id,
                    )
                self._write_lease_record(
                    lease_id,
                    {
                        "schema_version": "bb.rl.workspace-lease.v1",
                        "lease_id": lease_id,
                        "parent_lease_id": primary.lease_id,
                        "workspace_id": workspace_id,
                        "workspace_path": str(workspace),
                        "cache_lease_id": None,
                        "effective_plan_digest": verifier_plan.effective_plan_digest,
                        "owner_token": owner_token,
                        "epoch": 1,
                        "expires_at": (
                            issued + self.materialization_store.lease_ttl
                        ).isoformat(),
                        "role": "verifier",
                        "snapshot_id": snapshot.snapshot_id,
                        "snapshot_root_digest": snapshot.root_digest,
                        "runtime_authority_id": runtime.runtime_id,
                        "state": "allocating",
                    },
                )
            except BaseException:
                self._release_lease_owner_lock(lease_id, unlink=True)
                raise
            launched: RuntimeHandle | None = None
            backend: RuntimeBackend | None = None
            try:
                workspace = self.materialization_store.storage_backend.allocate(
                    workspace_id=workspace_id, root=self.materialization_store.workspace_root,
                    max_bytes=quota_bytes)
                workspace_fd = self.materialization_store._workspace.open_dir(workspace_id)
                workspace_metadata = os.fstat(workspace_fd)
                copy_snapshot = getattr(self.materialization_store, "copy_snapshot", None)
                if not callable(copy_snapshot):
                    raise VerifierSnapshotError("snapshot copier unavailable", code="snapshot_tampered")
                try:
                    copy_task = asyncio.create_task(
                        asyncio.to_thread(
                            copy_snapshot,
                            canonical_receipt,
                            snapshot_path,
                            workspace / "snapshot",
                            max_depth=primary.plan.security_policy.snapshot_max_depth,
                            max_files=primary.plan.security_policy.snapshot_max_files,
                            max_inodes=primary.plan.security_policy.snapshot_max_inodes,
                            max_bytes=primary.plan.limits.artifact_bytes_total,
                        )
                    )
                    try:
                        await asyncio.shield(copy_task)
                    except asyncio.CancelledError as cancellation:
                        try:
                            await copy_task
                        except BaseException:
                            raise cancellation from None
                        raise
                except Exception as exc:
                    raise VerifierSnapshotError(
                        "sealed snapshot authentication failed", code="snapshot_tampered"
                    ) from exc
                _seal_tree_at(workspace_fd, "snapshot")
                os.mkdir("result", mode=0o700, dir_fd=workspace_fd)
                backend = self.process_backend if runtime.runtime_class is RuntimeClass.TRUSTED_PROCESS else self.docker_backend
                if backend is None:
                    raise VerifierExecutionError("verifier runtime backend unavailable", code="runtime_unsupported")
                request_bytes = canonical_json_bytes({
                    "schema_version": VERIFIER_REQUEST_SCHEMA_VERSION,
                    "episode_id": verifier_plan.episode_id,
                    "effective_plan_digest": verifier_plan.effective_plan_digest,
                    "task_digest": snapshot.task_digest,
                    "snapshot_digest": snapshot.root_digest,
                    "verifier_digest": verifier_plan.verifier.grant.implementation_digest,
                })
                os.mkdir("input", mode=0o700, dir_fd=workspace_fd)
                input_dir_fd = os.open(
                    "input", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=workspace_fd,
                )
                try:
                    request_fd = os.open(
                        "verifier-request.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o400,
                        dir_fd=input_dir_fd,
                    )
                    try:
                        view = memoryview(request_bytes)
                        while view:
                            written = os.write(request_fd, view)
                            if written <= 0:
                                raise OSError("short verifier request write")
                            view = view[written:]
                        os.fchmod(request_fd, 0o400)
                        os.fsync(request_fd)
                        request_metadata = os.fstat(request_fd)
                        if (
                            not stat.S_ISREG(request_metadata.st_mode)
                            or request_metadata.st_nlink != 1
                            or stat.S_IMODE(request_metadata.st_mode) != 0o400
                            or request_metadata.st_uid != workspace_metadata.st_uid
                            or request_metadata.st_gid != workspace_metadata.st_gid
                        ):
                            raise WorkspaceStateError(
                                "verifier request authority is invalid",
                                code="workspace_authority_mismatch",
                                lease_id=lease_id,
                            )
                    finally:
                        os.close(request_fd)
                    read_fd = os.open(
                        "verifier-request.json",
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=input_dir_fd,
                    )
                    try:
                        if os.read(read_fd, len(request_bytes) + 1) != request_bytes:
                            raise WorkspaceStateError(
                                "verifier request readback mismatch",
                                code="workspace_authority_mismatch",
                                lease_id=lease_id,
                            )
                    finally:
                        os.close(read_fd)
                    os.fchmod(input_dir_fd, 0o500)
                    os.fsync(input_dir_fd)
                finally:
                    os.close(input_dir_fd)
                os.fsync(workspace_fd)
                context = self._launch_context(
                    plan=verifier_plan,
                    workspace=workspace,
                    role="verifier",
                    lease_id=lease_id,
                    workspace_id=workspace_id,
                    epoch=1,
                    quota_bytes=quota_bytes,
                    workspace_fd=workspace_fd,
                    workspace_identity=(workspace_metadata.st_dev, workspace_metadata.st_ino),
                    owner_token=owner_token,
                )
                workspace_fd = -1
                launched, measurement = await backend.launch(
                    verifier_plan, workspace, context=context
                )
                if isinstance(launched, TrustedProcessHandle):
                    launched.bind_identity_recorder(
                        lambda resource_id, identity, lease_id=lease_id:
                            self._record_process_identity(lease_id, resource_id, identity)
                    )
                if measurement.mismatch:
                    raise SandboxAttestationError("verifier runtime measurement mismatch", code="runtime_measurement_mismatch",
                                                  lease_id=lease_id)
                if primary.measurement.reward_eligible and (
                    measurement.isolation_disposition is not IsolationDisposition.ISOLATED
                    or not measurement.isolated
                    or not measurement.reward_eligible
                ):
                    raise SandboxAttestationError(
                        "verifier measurement is weaker than reward-eligible primary",
                        code="runtime_measurement_mismatch",
                        lease_id=lease_id,
                    )
                lease = VerifierWorkspaceLease(manager=self, primary=primary, lease_id=lease_id,
                    plan=verifier_plan, snapshot=snapshot, workspace=workspace, runtime=launched,
                    measurement=measurement)
                active_record = dict(self._read_lease_record(self._lease_record_path(lease_id)))
                prepared_resource_id = active_record.get("runtime_resource_id")
                if (
                    prepared_resource_id is not None
                    and prepared_resource_id != launched.runtime_id
                ):
                    raise WorkspaceStateError(
                        "prepared runtime identity changed",
                        code="stale_identity_uncertain",
                        lease_id=lease_id,
                    )
                active_record.update({
                    "runtime_authority_id": runtime.runtime_id,
                    "runtime_resource_id": launched.runtime_id,
                    "storage_authority_id": context.storage.authority_id,
                    "storage_quota_bytes": context.storage.quota_bytes,
                    "runtime_executable_path": runtime.executable_path,
                    "runtime_binary_digest": runtime.measured_binary_digest,
                    "action_timeout_ms": verifier_plan.limits.verifier_timeout_ms,
                    "observation_bytes": verifier_plan.limits.observation_bytes,
                    "state": "active",
                })
                self._write_lease_record(lease_id, active_record)
                primary._verifier_children.append(lease)
                return lease
            except BaseException as primary_error:
                if "workspace_fd" in locals() and workspace_fd >= 0:
                    os.close(workspace_fd)
                    workspace_fd = -1
                cleanup_steps: list[CleanupStepReceipt] = []
                cleanup_errors: list[str] = []
                if launched is not None:
                    try:
                        cleanup_steps.extend(await launched.terminate())
                    except BaseException as cleanup_error:
                        cleanup_errors.append(f"runtime:{type(cleanup_error).__name__}")
                        cleanup_steps.append(CleanupStepReceipt(
                            "runtime", CleanupState.FAILED, type(cleanup_error).__name__
                        ))
                backend_cleanup_pending = (
                    launched is None
                    and backend is self.docker_backend
                    and bool(getattr(backend, "cleanup_pending", False))
                )
                runtime_cleanup_pending = (
                    launched is not None
                    and not _runtime_cleanup_released(cleanup_steps)
                )
                if backend_cleanup_pending:
                    cleanup_steps.append(CleanupStepReceipt(
                        "runtime", CleanupState.QUARANTINED,
                        "backend retained launch cleanup authority",
                    ))
                if backend_cleanup_pending or runtime_cleanup_pending:
                    self._pending_launch_cleanups[lease_id] = _PendingLaunchCleanup(
                        workspace=workspace,
                        materialized=None,
                        runtime=launched if runtime_cleanup_pending else None,
                        backend_cleanup_pending=backend_cleanup_pending,
                    )
                runtime_released = (
                    launched is None and not backend_cleanup_pending
                ) or _runtime_cleanup_released(cleanup_steps)
                if runtime_released:
                    try:
                        await asyncio.to_thread(
                            self._make_workspace_releasable, workspace
                        )
                        await asyncio.to_thread(
                            self.materialization_store.storage_backend.release, workspace
                        )
                        absent = self.materialization_store.storage_backend.verify_absent(workspace)
                        cleanup_steps.append(CleanupStepReceipt(
                            "workspace", CleanupState.RELEASED if absent else CleanupState.FAILED
                        ))
                    except FileNotFoundError:
                        cleanup_steps.append(CleanupStepReceipt("workspace", CleanupState.ALREADY_RELEASED))
                    except BaseException as cleanup_error:
                        cleanup_errors.append(f"workspace:{type(cleanup_error).__name__}")
                        cleanup_steps.append(CleanupStepReceipt(
                            "workspace", CleanupState.FAILED, type(cleanup_error).__name__
                        ))
                else:
                    cleanup_steps.append(CleanupStepReceipt(
                        "workspace", CleanupState.QUARANTINED,
                        "dependent runtime cleanup incomplete",
                    ))
                dependencies_released = all(
                    step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                    for step in cleanup_steps
                )
                if dependencies_released:
                    self._unlink_lease_record(lease_id)
                    cleanup_steps.append(CleanupStepReceipt("lease_record", CleanupState.RELEASED))
                else:
                    cleanup_steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.FAILED, "dependent cleanup incomplete"
                    ))
                cleanup_receipt = SandboxCleanupReceipt.from_steps(
                    lease_id, tuple(cleanup_steps)
                )
                if cleanup_errors or cleanup_receipt.state not in {
                    CleanupState.RELEASED, CleanupState.ALREADY_RELEASED
                }:
                    raise SandboxFault(
                        primary_error, cleanup_receipt, tuple(cleanup_errors)
                    ) from primary_error
                raise

    async def _close_lease(self, lease: SandboxWorkspaceLease) -> SandboxCleanupReceipt:
        async with lease._lock:
            if lease._cleanup is not None: return lease._cleanup
            await lease._fence_and_drain(WorkspaceLeaseState.RELEASING)
            steps: list[CleanupStepReceipt] = []
            child_states: list[CleanupState] = []
            incomplete_child_ids: list[str] = []
            for child in tuple(lease._verifier_children):
                child_receipt = await child.close()
                child_states.append(child_receipt.state)
                if child_receipt.state not in {
                    CleanupState.RELEASED,
                    CleanupState.ALREADY_RELEASED,
                }:
                    incomplete_child_ids.append(child.lease_id)
            if CleanupState.QUARANTINED in child_states:
                child_state = CleanupState.QUARANTINED
            elif CleanupState.FAILED in child_states:
                child_state = CleanupState.FAILED
            elif CleanupState.RELEASED in child_states:
                child_state = CleanupState.RELEASED
            else:
                child_state = CleanupState.ALREADY_RELEASED
            steps.append(
                CleanupStepReceipt(
                    "child_verifier",
                    child_state,
                    ",".join(sorted(incomplete_child_ids)),
                )
            )
            lease._verifier_children[:] = [
                child
                for child in lease._verifier_children
                if not child._closed
            ]
            if not incomplete_child_ids:
                snapshot_ids = tuple(
                    snapshot_id
                    for snapshot_id, (snapshot, _path) in self._snapshots.items()
                    if snapshot.source_lease_id == lease.lease_id
                )
                for snapshot_id in snapshot_ids:
                    steps.append(await self._release_snapshot(snapshot_id))
            steps.extend(await lease._runtime.terminate())
            dependencies_released = all(
                step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in steps
            )
            if dependencies_released:
                try:
                    cache = await asyncio.to_thread(lease._materialized.close)
                    steps.append(CleanupStepReceipt("workspace", CleanupState.RELEASED))
                    steps.append(CleanupStepReceipt(
                        "cache_holder",
                        CleanupState.RELEASED
                        if cache.release_state.value == "released"
                        else CleanupState.FAILED,
                    ))
                except Exception as exc:
                    steps.append(CleanupStepReceipt("workspace", CleanupState.FAILED, type(exc).__name__))
                    steps.append(CleanupStepReceipt("cache_holder", CleanupState.FAILED, type(exc).__name__))
            else:
                steps.append(CleanupStepReceipt(
                    "workspace", CleanupState.QUARANTINED, "dependent runtime cleanup incomplete"
                ))
                steps.append(CleanupStepReceipt(
                    "cache_holder", CleanupState.QUARANTINED, "dependent runtime cleanup incomplete"
                ))
            prior_ok = all(step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED} for step in steps)
            try:
                if prior_ok:
                    self._unlink_lease_record(lease.lease_id)
                    absent = not self._lease_record_exists(lease.lease_id)
                    steps.append(CleanupStepReceipt("lease_record", CleanupState.RELEASED if absent else CleanupState.FAILED))
                else:
                    steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.QUARANTINED, "dependent cleanup incomplete"
                    ))
            except Exception as exc:
                steps.append(CleanupStepReceipt("lease_record", CleanupState.FAILED, type(exc).__name__))
            receipt = SandboxCleanupReceipt.from_steps(lease.lease_id, tuple(steps))
            lease._latest_cleanup = receipt
            lease._state = (
                WorkspaceLeaseState.RELEASED
                if receipt.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                else WorkspaceLeaseState.QUARANTINED
                if receipt.state is CleanupState.QUARANTINED
                else WorkspaceLeaseState.FAILED
            )
            if receipt.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}:
                lease._cleanup = receipt
                self._leases.pop(lease.lease_id, None)
            return receipt

    async def _reconcile_pending_launch_cleanups(
        self,
    ) -> tuple[SandboxCleanupReceipt, ...]:
        pending = tuple(self._pending_launch_cleanups.items())
        if not pending:
            return ()
        backend_failure: BaseException | None = None
        if any(retained.backend_cleanup_pending for _, retained in pending):
            reconcile = getattr(self.docker_backend, "reconcile_quarantined", None)
            if not callable(reconcile):
                backend_failure = RuntimeError(
                    "backend cleanup authority unavailable"
                )
            else:
                try:
                    await reconcile()
                except BaseException as exc:
                    backend_failure = exc
        receipts: list[SandboxCleanupReceipt] = []
        for lease_id, retained in pending:
            steps: list[CleanupStepReceipt] = []
            if retained.runtime is not None:
                try:
                    steps.extend(await retained.runtime.terminate())
                except BaseException as exc:
                    steps.append(CleanupStepReceipt(
                        "runtime", CleanupState.FAILED, type(exc).__name__
                    ))
                runtime_released = _runtime_cleanup_released(steps)
            elif retained.backend_cleanup_pending and backend_failure is not None:
                steps.append(CleanupStepReceipt(
                    "runtime", CleanupState.QUARANTINED,
                    type(backend_failure).__name__,
                ))
                runtime_released = False
            elif retained.backend_cleanup_pending:
                steps.append(CleanupStepReceipt(
                    "runtime", CleanupState.RELEASED
                ))
                runtime_released = True
            else:
                steps.append(CleanupStepReceipt(
                    "runtime", CleanupState.FAILED,
                    "retained cleanup authority is incomplete",
                ))
                runtime_released = False
            if not runtime_released:
                steps.append(CleanupStepReceipt(
                    "workspace", CleanupState.QUARANTINED,
                    "dependent runtime cleanup incomplete",
                ))
                if retained.materialized is not None:
                    steps.append(CleanupStepReceipt(
                        "cache_holder", CleanupState.QUARANTINED,
                        "dependent runtime cleanup incomplete",
                    ))
                steps.append(CleanupStepReceipt(
                    "lease_record", CleanupState.QUARANTINED,
                    "dependent cleanup incomplete",
                ))
                receipts.append(SandboxCleanupReceipt.from_steps(
                    lease_id, tuple(steps)
                ))
                continue
            try:
                if retained.materialized is not None:
                    await asyncio.to_thread(retained.materialized.close)
                    steps.extend((
                        CleanupStepReceipt("workspace", CleanupState.RELEASED),
                        CleanupStepReceipt("cache_holder", CleanupState.RELEASED),
                    ))
                else:
                    await asyncio.to_thread(
                        self._make_workspace_releasable, retained.workspace
                    )
                    await asyncio.to_thread(
                        self.materialization_store.storage_backend.release,
                        retained.workspace,
                    )
                    absent = self.materialization_store.storage_backend.verify_absent(
                        retained.workspace
                    )
                    steps.extend((
                        CleanupStepReceipt(
                            "workspace",
                            CleanupState.RELEASED if absent else CleanupState.FAILED,
                        ),
                        CleanupStepReceipt(
                            "cache_holder", CleanupState.ALREADY_RELEASED
                        ),
                    ))
            except FileNotFoundError:
                steps.extend((
                    CleanupStepReceipt("workspace", CleanupState.ALREADY_RELEASED),
                    CleanupStepReceipt("cache_holder", CleanupState.ALREADY_RELEASED),
                ))
            except BaseException as exc:
                steps.append(CleanupStepReceipt(
                    "workspace", CleanupState.FAILED, type(exc).__name__
                ))
            dependencies_released = all(
                step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in steps
            )
            if dependencies_released:
                try:
                    self._unlink_lease_record(lease_id)
                    steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.RELEASED
                    ))
                    self._pending_launch_cleanups.pop(lease_id, None)
                except BaseException as exc:
                    steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.FAILED, type(exc).__name__
                    ))
            else:
                steps.append(CleanupStepReceipt(
                    "lease_record", CleanupState.QUARANTINED,
                    "dependent cleanup incomplete",
                ))
            receipts.append(SandboxCleanupReceipt.from_steps(
                lease_id, tuple(steps)
            ))
        return tuple(receipts)


    async def reconcile_stale(self) -> tuple[SandboxCleanupReceipt, ...]:
        async with self._reconcile_lock:
            if self._lease_root_fd is None:
                return ()
            return await self._reconcile_stale_owner()


    async def _reconcile_stale_owner(
        self,
    ) -> tuple[SandboxCleanupReceipt, ...]:
        receipts: list[SandboxCleanupReceipt] = []
        receipts.extend(await self._reconcile_pending_launch_cleanups())
        now = self.materialization_store.clock.current()
        if self._lease_root_fd is None:
            return ()
        names = tuple(
            name for name in os.listdir(self._lease_root_fd)
            if "/" not in name and name not in {".", ".."}
        )
        record_names = frozenset(
            name for name in names if name.endswith(".json")
        )
        for lock_name in sorted(
            name for name in names if name.endswith(".owner.lock")
        ):
            lease_id = lock_name.removesuffix(".owner.lock")
            if (
                lease_id + ".json" in record_names
                or lease_id in self._lease_owner_locks
            ):
                continue
            try:
                owner_claimed = self._claim_lease_owner_lock(lease_id)
            except Exception:
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        lease_id,
                        (
                            CleanupStepReceipt(
                                "owner_lock",
                                CleanupState.QUARANTINED,
                                "owner_lock_invalid",
                            ),
                        ),
                    )
                )
                continue
            if not owner_claimed:
                continue
            if self._lease_record_exists(lease_id):
                self._release_lease_owner_lock(lease_id, unlink=False)
                continue
            try:
                self._release_lease_owner_lock(lease_id, unlink=True)
                os.fsync(self._lease_root_fd)
            except Exception:
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        lease_id,
                        (
                            CleanupStepReceipt(
                                "owner_lock",
                                CleanupState.QUARANTINED,
                                "owner_lock_cleanup_failed",
                            ),
                        ),
                    )
                )
            else:
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        lease_id,
                        (
                            CleanupStepReceipt(
                                "owner_lock",
                                CleanupState.RELEASED,
                            ),
                        ),
                    )
                )
        names = tuple(sorted(record_names))
        paths = [
            self.lease_root / name
            for name in sorted(
                names,
                key=lambda name: (
                    not name.startswith("verifier-lease-"),
                    name,
                ),
            )
        ]
        for path in paths:
            if path.stem in self._pending_launch_cleanups:
                continue
            owned = self._leases.get(path.stem)
            if owned is None:
                owned = next(
                    (
                        child
                        for primary in self._leases.values()
                        for child in primary._verifier_children
                        if child.lease_id == path.stem
                    ),
                    None,
                )
            if (
                owned is not None
                and owned._manager is self
                and (
                    isinstance(owned, VerifierWorkspaceLease)
                    and not owned._closed
                    or isinstance(owned, SandboxWorkspaceLease)
                    and owned.state is not WorkspaceLeaseState.RELEASED
                )
            ):
                continue
            try:
                record = self._read_lease_record(path)
            except WorkspaceStateError as exc:
                receipts.append(SandboxCleanupReceipt.from_steps(path.stem, (
                    CleanupStepReceipt("lease_record", CleanupState.QUARANTINED, exc.code),)))
                continue
            try:
                expires_at = record["expires_at"]
                if type(expires_at) is not str:
                    raise ValueError
                expires = datetime.fromisoformat(expires_at)
                if expires.tzinfo is None:
                    raise ValueError
            except (KeyError, ValueError, TypeError):
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        path.stem,
                        (
                            CleanupStepReceipt(
                                "lease_record",
                                CleanupState.QUARANTINED,
                                "stale_identity_uncertain",
                            ),
                        ),
                    )
                )
                continue
            if now < expires:
                continue
            try:
                owner_claimed = self._claim_lease_owner_lock(path.stem)
            except Exception:
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        path.stem,
                        (
                            CleanupStepReceipt(
                                "lease_record",
                                CleanupState.QUARANTINED,
                                "owner_lock_invalid",
                            ),
                        ),
                    )
                )
                continue
            if not owner_claimed:
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        path.stem,
                        (
                            CleanupStepReceipt(
                                "lease_record",
                                CleanupState.QUARANTINED,
                                "live_owner",
                            ),
                        ),
                    )
                )
                continue
            role = record.get("role")
            child_step: CleanupStepReceipt | None = None
            if role == "primary":
                remaining_children: list[str] = []
                for child_name in os.listdir(self._lease_root_fd):
                    if not (
                        child_name.startswith("verifier-lease-")
                        and child_name.endswith(".json")
                    ):
                        continue
                    child_path = self.lease_root / child_name
                    try:
                        child_record = self._read_lease_record(child_path)
                    except WorkspaceStateError:
                        remaining_children.append(child_path.stem)
                        continue
                    if child_record.get("parent_lease_id") == record["lease_id"]:
                        remaining_children.append(str(child_record["lease_id"]))
                child_step = CleanupStepReceipt(
                    "child_verifier",
                    CleanupState.QUARANTINED
                    if remaining_children
                    else CleanupState.ALREADY_RELEASED,
                    ",".join(sorted(remaining_children)),
                )
            snapshot_step: CleanupStepReceipt | None = None
            if role == "primary":
                if child_step is not None and child_step.state in {
                    CleanupState.RELEASED,
                    CleanupState.ALREADY_RELEASED,
                }:
                    snapshot_step = (
                        await self._release_durable_snapshots_for_lease(
                            path.stem
                        )
                    )
                else:
                    snapshot_step = CleanupStepReceipt(
                        "snapshot",
                        CleanupState.QUARANTINED,
                        "dependent verifier cleanup incomplete",
                    )
            reconciliation_prefix = tuple(
                step
                for step in (child_step, snapshot_step)
                if step is not None
            )
            runtime_authority_id = str(
                record.get("runtime_authority_id", record.get("runtime_id", ""))
            )
            trusted_runtime_ids = {
                item.runtime_id for item in self.installed_authorities.runtimes
                if item.runtime_class is RuntimeClass.TRUSTED_PROCESS
            }
            backend = self.process_backend if runtime_authority_id in trusted_runtime_ids else self.docker_backend
            reconcile = getattr(backend, "reconcile", None) if backend is not None else None
            if not callable(reconcile):
                unavailable_steps = (
                    CleanupStepReceipt(
                        "runtime",
                        CleanupState.QUARANTINED,
                        "stale_identity_uncertain",
                    ),
                    CleanupStepReceipt(
                        "workspace",
                        CleanupState.QUARANTINED,
                        "stale_identity_uncertain",
                    ),
                    CleanupStepReceipt(
                        "cache_holder",
                        CleanupState.QUARANTINED,
                        "stale_identity_uncertain",
                    ),
                    CleanupStepReceipt(
                        "lease_record",
                        CleanupState.QUARANTINED,
                        "stale_identity_uncertain",
                    ),
                )
                receipts.append(
                    SandboxCleanupReceipt.from_steps(
                        str(record["lease_id"]),
                        reconciliation_prefix + unavailable_steps,
                    )
                )
                continue
            raw_steps = reconciliation_prefix + tuple(await reconcile(record))
            if (
                {step.resource for step in raw_steps}
                == (
                    {
                        "child_verifier",
                        *(("snapshot",) if snapshot_step is not None else ()),
                        "runtime",
                        "workspace",
                        "cache_holder",
                        "lease_record",
                    }
                    if role == "primary"
                    else {
                        "runtime",
                        "workspace",
                        "cache_holder",
                        "lease_record",
                    }
                )
                and all(
                    step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                    for step in raw_steps
                )
            ):
                self._unlink_lease_record(path.stem)
                receipts.append(SandboxCleanupReceipt.from_steps(
                    str(record["lease_id"]), raw_steps
                ))
                continue
            runtime_steps = tuple(
                step for step in raw_steps if step.resource == "runtime"
            )
            runtime_released = bool(runtime_steps) and all(
                step.state
                in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in runtime_steps
            )
            steps = list(raw_steps)
            if (
                runtime_released
                and (
                    child_step is None
                    or child_step.state
                    in {
                        CleanupState.RELEASED,
                        CleanupState.ALREADY_RELEASED,
                    }
                )
            ):
                workspace_id = record.get("workspace_id")
                workspace_path = record.get("workspace_path")
                workspace_prefix = (
                    "verifier-workspace-"
                    if role == "verifier"
                    else "workspace-"
                )
                valid_workspace_id = (
                    type(workspace_id) is str
                    and workspace_id.startswith(workspace_prefix)
                    and len(workspace_id) == len(workspace_prefix) + 32
                    and all(
                        character in "0123456789abcdef"
                        for character in workspace_id[len(workspace_prefix):]
                    )
                )
                expected_workspace = (
                    self.materialization_store.workspace_root / workspace_id
                    if valid_workspace_id
                    else None
                )
                if (
                    type(workspace_path) is str
                    and expected_workspace is not None
                    and Path(workspace_path) == expected_workspace
                ):
                    await asyncio.to_thread(
                        self._make_workspace_releasable, expected_workspace
                    )
                    try:
                        await asyncio.to_thread(
                            self.materialization_store.storage_backend.release,
                            expected_workspace,
                        )
                        absent = self.materialization_store.storage_backend.verify_absent(
                            expected_workspace
                        )
                        steps.append(CleanupStepReceipt(
                            "workspace",
                            CleanupState.RELEASED if absent else CleanupState.FAILED,
                        ))
                    except FileNotFoundError:
                        steps.append(CleanupStepReceipt(
                            "workspace", CleanupState.ALREADY_RELEASED
                        ))
                    except Exception as exc:
                        steps.append(CleanupStepReceipt(
                            "workspace", CleanupState.FAILED, type(exc).__name__
                        ))
                else:
                    steps.append(CleanupStepReceipt(
                        "workspace", CleanupState.QUARANTINED, "stale_identity_uncertain"
                    ))
            else:
                steps.append(CleanupStepReceipt(
                    "workspace", CleanupState.QUARANTINED, "stale_identity_uncertain"
                ))
            dependencies_released = all(
                step.state in {CleanupState.RELEASED, CleanupState.ALREADY_RELEASED}
                for step in steps
            )
            if role == "verifier" and dependencies_released:
                steps.append(CleanupStepReceipt("cache_holder", CleanupState.ALREADY_RELEASED))
                self._unlink_lease_record(path.stem)
                steps.append(CleanupStepReceipt("lease_record", CleanupState.RELEASED))
            elif role == "primary" and dependencies_released:
                recover_cache = getattr(
                    self.materialization_store, "recover_stale_cache_holder", None
                )
                if callable(recover_cache):
                    cache_step = await asyncio.to_thread(recover_cache, record)
                else:
                    cache_step = CleanupStepReceipt(
                        "cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"
                    )
                steps.append(cache_step)
                if cache_step.state in {
                    CleanupState.RELEASED, CleanupState.ALREADY_RELEASED
                }:
                    self._unlink_lease_record(path.stem)
                    steps.append(CleanupStepReceipt("lease_record", CleanupState.RELEASED))
                else:
                    steps.append(CleanupStepReceipt(
                        "lease_record", CleanupState.QUARANTINED, "stale_identity_uncertain"
                    ))
            else:
                steps.append(CleanupStepReceipt(
                    "cache_holder", CleanupState.QUARANTINED, "stale_identity_uncertain"
                ))
                steps.append(CleanupStepReceipt(
                    "lease_record", CleanupState.QUARANTINED, "stale_identity_uncertain"
                ))
            receipts.append(SandboxCleanupReceipt.from_steps(
                str(record["lease_id"]), tuple(steps)
            ))
        receipts.extend([await self._close_lease(lease) for lease in tuple(self._leases.values())
                         if lease.state in {WorkspaceLeaseState.FAILED, WorkspaceLeaseState.QUARANTINED}])
        return tuple(receipts)

    async def _close_all(
        self, leases: Sequence[SandboxWorkspaceLease]
    ) -> list[SandboxCleanupReceipt]:
        receipts = list(await asyncio.gather(*(lease.close() for lease in leases)))
        receipts.extend(await self._reconcile_pending_launch_cleanups())
        for snapshot_id in tuple(self._snapshots):
            step = await self._release_snapshot(snapshot_id)
            receipts.append(
                SandboxCleanupReceipt.from_steps(snapshot_id, (step,))
            )
        return receipts

    async def _close_all_serialized(
        self,
        leases: Sequence[SandboxWorkspaceLease],
    ) -> list[SandboxCleanupReceipt]:
        async with self._reconcile_lock:
            return await self._close_all(leases)

    async def close(self) -> tuple[SandboxCleanupReceipt, ...]:
        async with self._lock:
            if self._close_task is not None:
                close_task = self._close_task
            else:
                if (
                    self._closed
                    and not self._leases
                    and not self._pending_launch_cleanups
                    and not self._snapshots
                    and not self._lease_owner_locks
                ):
                    return self._last_close_receipts or ()
                self._closed = True
                leases = tuple(self._leases.values())
                close_task = asyncio.create_task(
                    self._close_all_serialized(leases)
                )
                self._close_task = close_task
        try:
            result = tuple(await asyncio.shield(close_task))
        finally:
            if close_task.done():
                async with self._lock:
                    if self._close_task is close_task:
                        self._close_task = None
        pending = tuple(
            receipt for receipt in result
            if receipt.state not in {
                CleanupState.RELEASED, CleanupState.ALREADY_RELEASED
            }
        )
        if not pending:
            self._last_close_receipts = result
        async with self._reconcile_lock:
            if (
                not self._leases
                and not self._pending_launch_cleanups
                and not self._snapshots
            ):
                for lease_id in tuple(self._lease_owner_locks):
                    self._release_lease_owner_lock(lease_id, unlink=False)
                if self._lease_root_fd is not None:
                    os.close(self._lease_root_fd)
                    self._lease_root_fd = None
        return result


__all__ = [
    "CacheLeaseError", "InstalledImage", "InstalledRuntime", "InstalledSandboxAuthoritySet",
    "InstalledVerifier", "LeaseBackedRunnerWorkspace", "MaterializationError", "RuntimeBackend",
    "RuntimeHandle", "RuntimeLaunchContext", "RuntimePreparedIdentity", "SandboxAttestationError",
    "SandboxCleanupReceipt", "SandboxExecutionPlan", "SandboxFault", "SandboxLaunchError",
    "SandboxMeasurement", "SandboxNetworkPolicy",
    "SandboxPlanError", "SandboxRuntimeError", "SandboxRuntimeManager", "SandboxSecurityPolicy",
    "SandboxWorkspaceLease", "TrustedProcessBackend", "TrustedProcessHandle",
    "SANDBOX_CAPABILITY_MATRIX_RESOURCE", "SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION",
    "SANDBOX_CAPABILITY_MATRIX_SHA256",
    "VERIFIER_REQUEST_RELATIVE_PATH", "VERIFIER_REQUEST_SCHEMA_VERSION",
    "VerifierExecutionError", "VerifierSnapshotError", "VerifierWorkspaceLease",
    "WorkspaceStateError", "WorkspaceStorageIdentity", "build_sandbox_execution_plan",
    "load_sandbox_capability_matrix",
]
