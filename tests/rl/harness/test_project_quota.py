from __future__ import annotations

import errno
import fcntl
import grp
import os
import pwd
import stat
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from breadboard.rl.harness.project_quota import ProjectQuotaStorageBackend


_TEST_ROOT_ENV = "BREADBOARD_PROJECT_QUOTA_TEST_ROOT"
_WRITER_UID = 65534
_WRITER_GID = 65534
_FS_IOC_FSGETXATTR = 0x801C581F
_FS_IOC_FSSETXATTR = 0x401C5820
_FS_XATTR_STRUCT = "=5I8s"
_CAPABILITY_ERRNOS = {
    errno.EACCES,
    errno.ENODEV,
    errno.ENOSYS,
    errno.ENOTBLK,
    errno.ENOTTY,
    errno.EOPNOTSUPP,
    errno.EPERM,
    errno.ENOTSUP,
}


def _fsxattr(fd: int) -> tuple[int, int, int, int, int, bytes]:
    payload = bytearray(struct.calcsize(_FS_XATTR_STRUCT))
    fcntl.ioctl(fd, _FS_IOC_FSGETXATTR, payload, True)
    return struct.unpack(_FS_XATTR_STRUCT, payload)


def _set_project_id(path: Path, project_id: int) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        xflags, extsize, nextents, _old_project_id, cowextsize, padding = _fsxattr(
            descriptor
        )
        payload = struct.pack(
            _FS_XATTR_STRUCT,
            xflags,
            extsize,
            nextents,
            project_id,
            cowextsize,
            padding,
        )
        fcntl.ioctl(descriptor, _FS_IOC_FSSETXATTR, payload)
    finally:
        os.close(descriptor)


def _project_id(path: Path) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        return _fsxattr(descriptor)[3]
    finally:
        os.close(descriptor)


def _set_writer_identity(path: Path) -> None:
    os.chown(path, _WRITER_UID, _WRITER_GID)
    os.chmod(path, 0o700)


def _child_write_until_quota(path: Path, target_bytes: int) -> tuple[int, int, int]:
    read_end, write_end = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_end)
        written = 0
        error = 0
        exit_code = 0
        descriptor = -1
        try:
            os.chdir(path)
            os.setgroups([])
            os.setgid(_WRITER_GID)
            os.setuid(_WRITER_UID)
            descriptor = os.open(
                "writer.bin",
                os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC,
                0o600,
            )
            block = b"q" * 4096
            while written < target_bytes:
                count = min(len(block), target_bytes - written)
                try:
                    written += os.write(descriptor, block[:count])
                except OSError as exc:
                    error = exc.errno or errno.EIO
                    break
        except OSError as exc:
            error = exc.errno or errno.EIO
            exit_code = 1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.write(write_end, struct.pack("!qii", written, error, exit_code))
            os.close(write_end)
            os._exit(exit_code)
    deadline = time.monotonic() + 10.0
    status: int | None = None
    timed_out = False
    try:
        while time.monotonic() < deadline:
            waited, observed = os.waitpid(child, os.WNOHANG)
            if waited == child:
                status = observed
                break
            time.sleep(0.01)
        if status is None:
            timed_out = True
            os.kill(child, 9)
            _, status = os.waitpid(child, 0)
        if timed_out:
            os.set_blocking(read_end, False)
        try:
            payload = os.read(read_end, struct.calcsize("!qii"))
        except BlockingIOError:
            payload = b""
    finally:
        os.close(read_end)
    assert status is not None
    assert len(payload) == struct.calcsize("!qii")
    written, error, child_error = struct.unpack("!qii", payload)
    return written, error, child_error


def _skip_if_unavailable(exc: BaseException) -> None:
    if isinstance(exc, OSError) and exc.errno in _CAPABILITY_ERRNOS:
        pytest.skip(f"project quota capability unavailable: {exc}")
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        if any(
            marker in message
            for marker in ("unavailable", "unsupported", "not supported")
        ):
            pytest.skip(f"project quota capability unavailable: {exc}")


@pytest.fixture
def project_quota_backend() -> tuple[ProjectQuotaStorageBackend, Path, list[tuple[Path, tuple[int, int]]]]:
    if sys.platform != "linux":
        pytest.skip("project quotas require Linux")
    if os.geteuid() != 0:
        pytest.skip("project quota probe requires effective uid 0")
    configured = os.environ.get(_TEST_ROOT_ENV)
    if not configured:
        pytest.skip(f"{_TEST_ROOT_ENV} is not configured")
    try:
        pwd.getpwuid(_WRITER_UID)
        grp.getgrgid(_WRITER_GID)
    except KeyError:
        pytest.skip("uid/gid 65534 is not available")

    root = Path(configured)
    if not root.is_dir():
        pytest.skip("configured project quota root is not a directory")
    root_descriptor = -1
    try:
        root_descriptor = os.open(
            root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        _fsxattr(root_descriptor)
    except OSError as exc:
        if exc.errno in _CAPABILITY_ERRNOS:
            pytest.skip(f"configured root lacks project quota support: {exc}")
        raise
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)

    backend = ProjectQuotaStorageBackend()
    bound_descriptor = os.open(
        root,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        try:
            backend.bind_root(bound_descriptor)
        except BaseException as exc:
            _skip_if_unavailable(exc)
            raise
    finally:
        os.close(bound_descriptor)

    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        yield backend, root, created
    finally:
        try:
            for backing, identity in reversed(created):
                try:
                    metadata = os.lstat(backing)
                except FileNotFoundError:
                    continue
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != identity
                    or metadata.st_uid not in {os.geteuid(), _WRITER_UID}
                ):
                    continue
                backend.release(backing)
                assert backend.verify_absent(backing)
                assert not os.path.lexists(backing)
        finally:
            backend.close_root()


def _allocate(
    backend: ProjectQuotaStorageBackend,
    root: Path,
    created: list[tuple[Path, tuple[int, int]]],
    workspace_id: str,
    max_bytes: int,
    *,
    skip_capability: bool = True,
) -> Path:
    try:
        backing = backend.allocate(
            workspace_id=workspace_id,
            root=root,
            max_bytes=max_bytes,
        )
    except BaseException as exc:
        if skip_capability:
            _skip_if_unavailable(exc)
        raise
    metadata = os.lstat(backing)
    assert stat.S_ISDIR(metadata.st_mode)
    created.append((backing, (metadata.st_dev, metadata.st_ino)))
    return backing


def _measure_after_writer_identity(
    backend: ProjectQuotaStorageBackend, backing: Path
) -> dict[str, Any]:
    _set_writer_identity(backing)
    measured = dict(backend.measure(backing))
    assert measured["quota_enforced"] is True
    assert measured["quota_bytes"] > 0
    assert measured["owner_uid"] == _WRITER_UID
    assert measured["owner_gid"] == _WRITER_GID
    assert measured["mode"] == 0o700
    assert measured["authority_id"]
    return measured


def test_project_quota_enforces_exact_limit_for_uid65534_writer(
    project_quota_backend: tuple[ProjectQuotaStorageBackend, Path, list[tuple[Path, tuple[int, int]]]],
) -> None:
    backend, root, created = project_quota_backend
    limit = 64 * 1024
    backing = _allocate(backend, root, created, f"quota-{uuid.uuid4().hex}", limit)
    measured = _measure_after_writer_identity(backend, backing)
    assert measured["quota_bytes"] == limit

    written, error, child_error = _child_write_until_quota(backing, limit + 4096)
    assert child_error == 0
    assert error == errno.EDQUOT
    assert 0 < written <= limit
    after_write = backend.measure(backing)
    assert after_write["quota_bytes"] == limit
    assert after_write["quota_usage_bytes"] >= written


def test_project_quota_limits_are_independent_per_workspace(
    project_quota_backend: tuple[ProjectQuotaStorageBackend, Path, list[tuple[Path, tuple[int, int]]]],
) -> None:
    backend, root, created = project_quota_backend
    first_limit = 64 * 1024
    second_limit = 128 * 1024
    first = _allocate(backend, root, created, f"quota-{uuid.uuid4().hex}", first_limit)
    second = _allocate(
        backend,
        root,
        created,
        f"quota-{uuid.uuid4().hex}",
        second_limit,
        skip_capability=False,
    )
    assert _measure_after_writer_identity(backend, first)["quota_bytes"] == first_limit
    assert _measure_after_writer_identity(backend, second)["quota_bytes"] == second_limit

    first_written, first_error, first_child_error = _child_write_until_quota(
        first, first_limit + 4096
    )
    second_written, second_error, second_child_error = _child_write_until_quota(
        second, second_limit + 4096
    )
    assert first_child_error == 0
    assert second_child_error == 0
    assert first_error == errno.EDQUOT
    assert second_error == errno.EDQUOT
    assert 0 < first_written <= first_limit
    assert second_written >= first_limit + 4096
    assert second_written <= second_limit
    assert backend.measure(first)["quota_usage_bytes"] >= first_written
    assert backend.measure(second)["quota_usage_bytes"] >= second_written


def test_project_quota_rejects_project_identity_drift_before_reuse(
    project_quota_backend: tuple[ProjectQuotaStorageBackend, Path, list[tuple[Path, tuple[int, int]]]],
) -> None:
    backend, root, created = project_quota_backend
    workspace_id = f"quota-{uuid.uuid4().hex}"
    backing = _allocate(backend, root, created, workspace_id, 64 * 1024)
    _measure_after_writer_identity(backend, backing)
    original_project_id = _project_id(backing)
    drifted_project_id = original_project_id + 1 or 1

    _set_project_id(backing, drifted_project_id)
    try:
        with pytest.raises((OSError, RuntimeError, ValueError)):
            backend.measure(backing)
    finally:
        _set_project_id(backing, original_project_id)

    restored = backend.measure(backing)
    assert restored["quota_enforced"] is True
    assert restored["quota_bytes"] == 64 * 1024
    backend.release(backing)
    for index, (candidate, _identity) in enumerate(created):
        if candidate == backing:
            created.pop(index)
            break
    assert backend.verify_absent(backing)
    assert not os.path.lexists(backing)

    reused = _allocate(backend, root, created, workspace_id, 64 * 1024, skip_capability=False)
    assert backend.measure(reused)["quota_bytes"] == 64 * 1024
