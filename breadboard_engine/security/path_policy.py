"""Neutral path policy and descriptor-anchored process directories."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .isolation_errors import ProcessIsolationUnavailable

_VIRTUAL_READ_MOUNTS = tuple(Path(path) for path in ("/dev", "/proc", "/run", "/sys"))
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def under_virtual_read_mount(path: Path) -> bool:
    """Return whether a path is within a virtual system read mount."""
    return any(path == root or root in path.parents for root in _VIRTUAL_READ_MOUNTS)


def _same_directory(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def _require_anchored_directory_support() -> None:
    required_dir_fd = (os.open, os.mkdir, os.stat)
    supported_dir_fd = getattr(os, "supports_dir_fd", ())
    if any(operation not in supported_dir_fd for operation in required_dir_fd):
        raise ProcessIsolationUnavailable(
            "anchored process temporary directory is unavailable"
        )
    if not hasattr(os, "fchmod"):
        raise ProcessIsolationUnavailable(
            "descriptor-based process temporary directory mode is unavailable"
        )
    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise ProcessIsolationUnavailable(
            "no-follow anchored process temporary directory is unavailable"
        )


def _open_directory_component(
    parent_fd: int,
    component: str,
    *,
    create: bool,
) -> int:
    try:
        before = os.stat(
            component,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if not create:
            raise
        os.mkdir(component, 0o700, dir_fd=parent_fd)
        before = os.stat(
            component,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    if not stat.S_ISDIR(before.st_mode):
        raise ProcessIsolationUnavailable(
            "process temporary directory contains a non-directory component"
        )

    child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        after = os.fstat(child_fd)
        if not _same_directory(before, after):
            raise ProcessIsolationUnavailable(
                "process temporary directory component changed"
            )
    except BaseException:
        os.close(child_fd)
        raise
    return child_fd


def _open_anchored_root(root: Path) -> tuple[int, Path]:
    try:
        lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(root))))
    except (OSError, TypeError, ValueError) as exc:
        raise ProcessIsolationUnavailable("process workspace is unavailable") from exc
    if not lexical.is_absolute():
        raise ProcessIsolationUnavailable("process workspace is unavailable")

    descriptor = -1
    try:
        descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
        for component in lexical.parts[1:]:
            child = _open_directory_component(descriptor, component, create=False)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ProcessIsolationUnavailable("process workspace is unavailable")
        return descriptor, lexical
    except BaseException as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(exc, ProcessIsolationUnavailable):
            raise
        if isinstance(exc, (OSError, TypeError, NotImplementedError)):
            raise ProcessIsolationUnavailable(
                "anchored process temporary directory is unavailable"
            ) from exc
        raise


def prepare_workspace_temp_directory(workspace: Path) -> Path:
    """Create and secure ``.breadboard/tmp`` below an anchored workspace."""
    _require_anchored_directory_support()
    root_fd, lexical_root = _open_anchored_root(workspace)
    breadboard_fd = -1
    temp_fd = -1
    try:
        try:
            breadboard_fd = _open_directory_component(
                root_fd,
                ".breadboard",
                create=True,
            )
            temp_fd = _open_directory_component(
                breadboard_fd,
                "tmp",
                create=True,
            )
            before = os.fstat(temp_fd)
            if not stat.S_ISDIR(before.st_mode):
                raise ProcessIsolationUnavailable(
                    "process temporary directory is unavailable"
                )
            try:
                os.fchmod(temp_fd, 0o700)
            except (OSError, TypeError, NotImplementedError) as exc:
                raise ProcessIsolationUnavailable(
                    "descriptor-based process temporary directory mode is unavailable"
                ) from exc
            after = os.fstat(temp_fd)
            if not _same_directory(before, after):
                raise ProcessIsolationUnavailable(
                    "process temporary directory changed during mode setup"
                )
            if stat.S_IMODE(after.st_mode) != 0o700:
                raise ProcessIsolationUnavailable(
                    "process temporary directory mode could not be secured"
                )
        except ProcessIsolationUnavailable:
            raise
        except (OSError, TypeError, NotImplementedError) as exc:
            raise ProcessIsolationUnavailable(
                "anchored process temporary directory is unavailable"
            ) from exc
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if breadboard_fd >= 0:
            os.close(breadboard_fd)
        os.close(root_fd)
    return lexical_root / ".breadboard" / "tmp"
