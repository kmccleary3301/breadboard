"""Credential path and workspace boundary policy."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Mapping, Sequence

from .isolation_errors import ProcessIsolationUnavailable

_SQLITE_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")
_REGISTERED_PATHS_LOCK = threading.RLock()
_REGISTERED_PROTECTED_PATHS: dict[str, Path] = {}


def _normalized_path(path: str | os.PathLike[str]) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProcessIsolationUnavailable(
            "protected credential path is invalid"
        ) from exc


def register_protected_credential_path(
    path: str | os.PathLike[str],
    *,
    sqlite_sidecars: bool = False,
) -> tuple[Path, ...]:
    """Register a programmatic credential path for all later child launches."""
    base = _normalized_path(path)
    candidates = tuple(
        _normalized_path(f"{base}{suffix}")
        for suffix in (_SQLITE_SIDECAR_SUFFIXES if sqlite_sidecars else ("",))
    )
    with _REGISTERED_PATHS_LOCK:
        for candidate in candidates:
            _REGISTERED_PROTECTED_PATHS[str(candidate)] = candidate
    return candidates


def _sqlite_paths(path: str | os.PathLike[str]) -> tuple[Path, ...]:
    base = _normalized_path(path)
    return tuple(
        _normalized_path(f"{base}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES
    )


def protected_credential_paths(
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Return credential locations without opening or reading credential data."""
    source = os.environ if environment is None else environment
    raw_home = source.get("HOME")
    home = _normalized_path(raw_home) if raw_home else Path.home().resolve(strict=False)
    paths: list[Path] = [home / ".breadboard", home / ".codex"]
    for key in ("BREADBOARD_CREDENTIAL_STORE_PATH", "BREADBOARD_CREDENTIAL_DB"):
        explicit = source.get(key)
        if explicit:
            paths.extend(_sqlite_paths(explicit))
    state_dir = source.get("BREADBOARD_STATE_DIR")
    if state_dir:
        paths.append(_normalized_path(state_dir))
    with _REGISTERED_PATHS_LOCK:
        paths.extend(_REGISTERED_PROTECTED_PATHS.values())
    return tuple(dict.fromkeys(_normalized_path(path) for path in paths))


def _resolved_existing(path: str | os.PathLike[str]) -> Path | None:
    try:
        return Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _paths_overlap(left: Path, right: Path) -> bool:
    for candidate, root in ((left, right), (right, left)):
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _same_object(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _linked_regular_identities(
    root: Path,
) -> set[tuple[int, int]]:
    try:
        root_metadata = os.stat(root, follow_symlinks=False)
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "credential hardlink boundary is unavailable"
        ) from exc
    if stat.S_ISREG(root_metadata.st_mode):
        if root_metadata.st_nlink > 1:
            return {(root_metadata.st_dev, root_metadata.st_ino)}
        return set()
    if not stat.S_ISDIR(root_metadata.st_mode):
        return set()

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise ProcessIsolationUnavailable(
            "credential hardlink boundary is unavailable"
        ) from exc
    linked: set[tuple[int, int]] = set()

    def visit(descriptor: int) -> None:
        try:
            names = os.listdir(descriptor)
        except OSError as exc:
            raise ProcessIsolationUnavailable(
                "credential hardlink boundary is unavailable"
            ) from exc
        for name in names:
            try:
                before = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ProcessIsolationUnavailable(
                    "credential hardlink boundary is unavailable"
                ) from exc
            if stat.S_ISDIR(before.st_mode):
                child = -1
                try:
                    child = os.open(
                        name,
                        flags,
                        dir_fd=descriptor,
                    )
                    after = os.fstat(child)
                    if not _same_object(before, after):
                        raise ProcessIsolationUnavailable(
                            "credential hardlink boundary changed"
                        )
                    visit(child)
                except FileNotFoundError:
                    continue
                except ProcessIsolationUnavailable:
                    raise
                except OSError as exc:
                    raise ProcessIsolationUnavailable(
                        "credential hardlink boundary is unavailable"
                    ) from exc
                finally:
                    if child >= 0:
                        os.close(child)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink > 1:
                linked.add((before.st_dev, before.st_ino))

    try:
        if not _same_object(root_metadata, os.fstat(root_fd)):
            raise ProcessIsolationUnavailable("credential hardlink boundary changed")
        visit(root_fd)
    finally:
        os.close(root_fd)
    return linked


def _validate_hardlink_boundary(
    workspace: Path,
    protected_paths: Sequence[Path],
) -> None:
    workspace_links = _linked_regular_identities(workspace)
    if workspace_links:
        protected_links: set[tuple[int, int]] = set()
        for path in protected_paths:
            protected_links.update(_linked_regular_identities(path))
        if workspace_links.intersection(protected_links):
            raise ProcessIsolationUnavailable(
                "process workspace contains a protected credential hardlink"
            )


def _validate_workspace(
    workspace: str | os.PathLike[str],
    protected_paths: Sequence[Path],
) -> Path:
    resolved = _resolved_existing(workspace)
    if resolved is None or not resolved.is_dir():
        raise ProcessIsolationUnavailable("process workspace is unavailable")
    for protected in protected_paths:
        if _paths_overlap(resolved, protected.resolve(strict=False)):
            raise ProcessIsolationUnavailable(
                "process workspace overlaps a protected credential location"
            )
    return resolved


def _validate_working_directory(
    working_directory: str | os.PathLike[str] | None,
    workspace: Path,
) -> Path:
    resolved = _resolved_existing(working_directory or workspace)
    if resolved is None or not resolved.is_dir():
        raise ProcessIsolationUnavailable("process working directory is unavailable")
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ProcessIsolationUnavailable(
            "process working directory escapes the workspace"
        ) from exc
    return resolved


def validate_workspace_credential_boundary(
    workspace: str | os.PathLike[str],
    *,
    protected_paths: Sequence[str | os.PathLike[str]] = (),
) -> Path:
    """Reject workspace overlap or shared inodes before an external bind."""
    protected = tuple(
        dict.fromkeys(
            (
                *protected_credential_paths(),
                *(_normalized_path(path) for path in protected_paths),
            )
        )
    )
    root = _validate_workspace(workspace, protected)
    _validate_hardlink_boundary(root, protected)
    return root