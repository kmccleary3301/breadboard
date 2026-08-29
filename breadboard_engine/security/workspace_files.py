"""Descriptor-anchored filesystem operations for model-controlled workspaces."""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Pattern, Sequence


class WorkspacePathError(OSError):
    """A workspace path could not be accessed without crossing a trust boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class WorkspaceFileInfo:
    path: str
    kind: str
    size: int
    mtime: float
    mode: int
    sha256: str | None = None


@dataclass(frozen=True)
class WorkspaceEntry:
    path: str
    kind: str
    size: int
    mtime: float


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_COPY_CHUNK_BYTES = 1024 * 1024


@functools.lru_cache(maxsize=1)
def _trusted_platform_root_aliases() -> tuple[tuple[Path, Path], ...]:
    if sys.platform != "darwin":
        return ()
    trusted: list[tuple[Path, Path]] = []
    for alias, target in (
        (Path("/etc"), Path("/private/etc")),
        (Path("/tmp"), Path("/private/tmp")),
        (Path("/var"), Path("/private/var")),
    ):
        try:
            alias_metadata = os.lstat(alias)
            target_metadata = os.stat(target)
            resolved = Path(os.path.realpath(alias))
        except OSError:
            continue
        if (
            stat.S_ISLNK(alias_metadata.st_mode)
            and alias_metadata.st_uid == 0
            and stat.S_ISDIR(target_metadata.st_mode)
            and target_metadata.st_uid == 0
            and resolved == target
        ):
            trusted.append((alias, target))
    return tuple(trusted)


def _canonicalize_platform_root_alias(path: Path) -> Path:
    for alias, target in _trusted_platform_root_aliases():
        try:
            relative = path.relative_to(alias)
        except ValueError:
            continue
        return target / relative
    return path


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _regular_single_link(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


class WorkspaceFilesystem:
    """Hold a workspace root descriptor and never reopen through ambient paths."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        filesystem = type(self).open_anchored_root(root)
        self.root = filesystem.root
        self._root_fd = filesystem._root_fd
        self._root_identity = filesystem._root_identity
        filesystem._root_fd = -1

    @classmethod
    def open_anchored_root(
        cls,
        root: str | os.PathLike[str],
        *,
        create: bool = False,
    ) -> "WorkspaceFilesystem":
        """Open an absolute root without following any lexical path component."""
        try:
            lexical = _canonicalize_platform_root_alias(
                Path(os.path.abspath(os.path.expanduser(os.fspath(root))))
            )
        except (OSError, TypeError, ValueError) as exc:
            raise WorkspacePathError("workspace_root_unavailable") from exc
        components = lexical.parts[1:]
        descriptor = -1
        try:
            descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
            for component in components:
                try:
                    before = os.stat(
                        component,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    if not create:
                        raise WorkspacePathError("workspace_root_unavailable") from None
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    before = os.stat(
                        component,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                if not stat.S_ISDIR(before.st_mode):
                    raise WorkspacePathError("workspace_root_ancestor_not_directory")
                child = os.open(
                    component,
                    _DIRECTORY_FLAGS,
                    dir_fd=descriptor,
                )
                after = os.fstat(child)
                if not stat.S_ISDIR(after.st_mode) or not _same_inode(
                    before,
                    after,
                ):
                    os.close(child)
                    raise WorkspacePathError("workspace_root_changed")
                os.close(descriptor)
                descriptor = child
            metadata = os.fstat(descriptor)
            filesystem = cls.__new__(cls)
            filesystem.root = lexical
            filesystem._root_fd = descriptor
            filesystem._root_identity = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
            )
            descriptor = -1
            return filesystem
        except WorkspacePathError:
            raise
        except OSError as exc:
            raise WorkspacePathError("workspace_root_open_failed") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def __enter__(self) -> "WorkspaceFilesystem":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        descriptor = getattr(self, "_root_fd", -1)
        if descriptor < 0:
            return
        self._root_fd = -1
        try:
            os.close(descriptor)
        except OSError:
            pass

    def _require_open(self) -> int:
        if self._root_fd < 0:
            raise WorkspacePathError("workspace_closed")
        return self._root_fd

    def _parts(self, logical_path: str | os.PathLike[str]) -> tuple[str, ...]:
        raw = os.fspath(logical_path)
        if not isinstance(raw, str):
            raw = os.fsdecode(raw)
        if "\x00" in raw:
            raise WorkspacePathError("workspace_path_invalid")
        path = Path(raw)
        if path.is_absolute():
            path = _canonicalize_platform_root_alias(path)
            try:
                path = path.relative_to(self.root)
            except ValueError as exc:
                raise WorkspacePathError("path_outside_workspace") from exc
        parts = tuple(part for part in path.parts if part != ".")
        if any(part in {"", ".", ".."} for part in parts):
            raise WorkspacePathError("path_outside_workspace")
        return parts

    def _display(self, parts: Sequence[str]) -> Path:
        return self.root.joinpath(*parts)

    def _admit(self, parts: Sequence[str]) -> None:
        """Advisory resolution catches current escapes; descriptors carry authority."""
        try:
            observed = self._display(parts).resolve(strict=False)
            observed.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspacePathError("path_outside_workspace") from exc

    def display_path(self, logical_path: str | os.PathLike[str]) -> str:
        parts = self._parts(logical_path)
        self._admit(parts)
        return str(self._display(parts))

    def _open_directory_parts(
        self,
        parts: Sequence[str],
        *,
        create: bool,
    ) -> int:
        descriptor = os.dup(self._require_open())
        try:
            for component in parts:
                try:
                    before = os.stat(
                        component, dir_fd=descriptor, follow_symlinks=False
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    before = os.stat(
                        component, dir_fd=descriptor, follow_symlinks=False
                    )
                if not stat.S_ISDIR(before.st_mode):
                    raise WorkspacePathError("workspace_ancestor_not_directory")
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                after = os.fstat(child)
                if not stat.S_ISDIR(after.st_mode) or not _same_inode(before, after):
                    os.close(child)
                    raise WorkspacePathError("workspace_ancestor_changed")
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _open_parent(
        self,
        parts: Sequence[str],
        *,
        create: bool,
    ) -> tuple[int, str]:
        if not parts:
            raise WorkspacePathError("workspace_file_path_required")
        return self._open_directory_parts(parts[:-1], create=create), parts[-1]

    def _lstat_target(self, parent_fd: int, name: str) -> os.stat_result:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise WorkspacePathError("workspace_symlink_rejected")
        return metadata

    def _open_regular_parts(
        self,
        parts: Sequence[str],
    ) -> tuple[int, os.stat_result]:
        self._admit(parts)
        parent_fd, name = self._open_parent(parts, create=False)
        descriptor = -1
        try:
            before = self._lstat_target(parent_fd, name)
            if not _regular_single_link(before):
                raise WorkspacePathError("workspace_regular_single_link_required")
            descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
            after = os.fstat(descriptor)
            if not _regular_single_link(after) or not _same_inode(before, after):
                raise WorkspacePathError("workspace_file_changed")
            return descriptor, after
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        finally:
            os.close(parent_fd)

    @staticmethod
    def _read_descriptor(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def exists(self, logical_path: str | os.PathLike[str]) -> bool:
        parts = self._parts(logical_path)
        self._admit(parts)
        if not parts:
            return True
        parent_fd = -1
        try:
            parent_fd, name = self._open_parent(parts, create=False)
            metadata = self._lstat_target(parent_fd, name)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
                try:
                    if not _same_inode(metadata, os.fstat(child)):
                        raise WorkspacePathError("workspace_file_changed")
                finally:
                    os.close(child)
                return True
            if not _regular_single_link(metadata):
                raise WorkspacePathError("workspace_regular_single_link_required")
            return True
        except FileNotFoundError:
            return False
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)

    def stat(self, logical_path: str | os.PathLike[str]) -> WorkspaceFileInfo:
        parts = self._parts(logical_path)
        self._admit(parts)
        if not parts:
            metadata = os.fstat(self._require_open())
            return WorkspaceFileInfo(
                path=str(self.root),
                kind="dir",
                size=metadata.st_size,
                mtime=metadata.st_mtime,
                mode=stat.S_IMODE(metadata.st_mode),
            )
        parent_fd, name = self._open_parent(parts, create=False)
        try:
            before = self._lstat_target(parent_fd, name)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
                try:
                    after = os.fstat(child)
                    if not _same_inode(before, after):
                        raise WorkspacePathError("workspace_file_changed")
                finally:
                    os.close(child)
                metadata = after
                kind = "dir"
            elif _regular_single_link(before):
                descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
                try:
                    after = os.fstat(descriptor)
                    if not _regular_single_link(after) or not _same_inode(
                        before, after
                    ):
                        raise WorkspacePathError("workspace_file_changed")
                finally:
                    os.close(descriptor)
                metadata = after
                kind = "file"
            else:
                raise WorkspacePathError("workspace_regular_single_link_required")
            return WorkspaceFileInfo(
                path=str(self._display(parts)),
                kind=kind,
                size=metadata.st_size,
                mtime=metadata.st_mtime,
                mode=stat.S_IMODE(metadata.st_mode),
            )
        finally:
            os.close(parent_fd)

    def inspect_file(
        self,
        logical_path: str | os.PathLike[str],
        *,
        sha256: bool = False,
    ) -> WorkspaceFileInfo:
        parts = self._parts(logical_path)
        descriptor, metadata = self._open_regular_parts(parts)
        digest = hashlib.sha256() if sha256 else None
        try:
            if digest is not None:
                while True:
                    chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
        finally:
            os.close(descriptor)
        return WorkspaceFileInfo(
            path=str(self._display(parts)),
            kind="file",
            size=metadata.st_size,
            mtime=metadata.st_mtime,
            mode=stat.S_IMODE(metadata.st_mode),
            sha256=digest.hexdigest() if digest is not None else None,
        )

    def read_bytes(self, logical_path: str | os.PathLike[str]) -> bytes:
        parts = self._parts(logical_path)
        descriptor, _metadata = self._open_regular_parts(parts)
        try:
            return self._read_descriptor(descriptor)
        finally:
            os.close(descriptor)

    def read_text(
        self,
        logical_path: str | os.PathLike[str],
        *,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> str:
        return self.read_bytes(logical_path).decode(encoding, errors=errors)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise WorkspacePathError("workspace_short_write")
            view = view[written:]

    def _atomic_write_from(
        self,
        parts: Sequence[str],
        source_fd: int | None,
        payload: bytes | None,
        *,
        overwrite: bool,
        mode: int | None,
    ) -> WorkspaceFileInfo:
        self._admit(parts)
        parent_fd, name = self._open_parent(parts, create=True)
        temporary = f".{name}.breadboard-{uuid.uuid4().hex}.tmp"
        descriptor = -1
        before: os.stat_result | None
        try:
            try:
                before = self._lstat_target(parent_fd, name)
            except FileNotFoundError:
                before = None
            if before is not None:
                if not _regular_single_link(before):
                    raise WorkspacePathError("workspace_regular_single_link_required")
                if not overwrite:
                    raise FileExistsError(name)
            descriptor = os.open(temporary, _WRITE_FLAGS, 0o600, dir_fd=parent_fd)
            if source_fd is not None:
                os.lseek(source_fd, 0, os.SEEK_SET)
                while True:
                    chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    self._write_all(descriptor, chunk)
            else:
                self._write_all(descriptor, payload or b"")
            if mode is not None:
                os.fchmod(descriptor, stat.S_IMODE(mode) & 0o777)
            elif before is not None:
                os.fchmod(descriptor, stat.S_IMODE(before.st_mode) & 0o777)
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                current = self._lstat_target(parent_fd, name)
            except FileNotFoundError:
                current = None
            if before is None:
                if current is not None:
                    raise WorkspacePathError("workspace_file_changed")
            elif current is None or not _same_inode(before, current):
                raise WorkspacePathError("workspace_file_changed")
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            return WorkspaceFileInfo(
                path=str(self._display(parts)),
                kind="file",
                size=written.st_size,
                mtime=written.st_mtime,
                mode=stat.S_IMODE(written.st_mode),
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def write_bytes(
        self,
        logical_path: str | os.PathLike[str],
        payload: bytes,
        *,
        overwrite: bool = True,
    ) -> WorkspaceFileInfo:
        parts = self._parts(logical_path)
        return self._atomic_write_from(
            parts,
            None,
            bytes(payload),
            overwrite=overwrite,
            mode=None,
        )

    def write_text(
        self,
        logical_path: str | os.PathLike[str],
        content: str,
        *,
        encoding: str = "utf-8",
        overwrite: bool = True,
    ) -> WorkspaceFileInfo:
        return self.write_bytes(
            logical_path,
            content.encode(encoding),
            overwrite=overwrite,
        )

    def append_bytes(
        self,
        logical_path: str | os.PathLike[str],
        payload: bytes,
    ) -> WorkspaceFileInfo:
        """Append through a verified descriptor without following the target."""
        parts = self._parts(logical_path)
        self._admit(parts)
        parent_fd, name = self._open_parent(parts, create=True)
        descriptor = -1
        before: os.stat_result | None
        try:
            try:
                before = self._lstat_target(parent_fd, name)
            except FileNotFoundError:
                before = None
            if before is not None and not _regular_single_link(before):
                raise WorkspacePathError("workspace_regular_single_link_required")
            flags = (
                os.O_WRONLY
                | os.O_APPEND
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            current = self._lstat_target(parent_fd, name)
            if (
                not _regular_single_link(opened)
                or not _same_inode(opened, current)
                or (before is not None and not _same_inode(before, opened))
            ):
                raise WorkspacePathError("workspace_file_changed")
            self._write_all(descriptor, bytes(payload))
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            if not _regular_single_link(written):
                raise WorkspacePathError("workspace_regular_single_link_required")
            return WorkspaceFileInfo(
                path=str(self._display(parts)),
                kind="file",
                size=written.st_size,
                mtime=written.st_mtime,
                mode=stat.S_IMODE(written.st_mode),
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)

    def append_text(
        self,
        logical_path: str | os.PathLike[str],
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> WorkspaceFileInfo:
        return self.append_bytes(logical_path, content.encode(encoding))

    def create_directory(
        self,
        logical_path: str | os.PathLike[str],
        *,
        mode: int | None = None,
    ) -> Path:
        parts = self._parts(logical_path)
        self._admit(parts)
        descriptor = self._open_directory_parts(parts, create=True)
        try:
            if mode is not None:
                os.fchmod(descriptor, stat.S_IMODE(mode) & 0o777)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return self._display(parts)

    def copy_file_to(
        self,
        logical_path: str | os.PathLike[str],
        destination: "WorkspaceFilesystem",
        destination_path: str | os.PathLike[str],
        *,
        overwrite: bool = True,
    ) -> WorkspaceFileInfo:
        source_parts = self._parts(logical_path)
        source_fd, metadata = self._open_regular_parts(source_parts)
        try:
            destination_parts = destination._parts(destination_path)
            return destination._atomic_write_from(
                destination_parts,
                source_fd,
                None,
                overwrite=overwrite,
                mode=metadata.st_mode,
            )
        finally:
            os.close(source_fd)

    def copy_tree_to(self, destination: "WorkspaceFilesystem") -> None:
        """Copy this tree without following links or reopening ambient paths."""
        if os.listdir(destination._require_open()):
            raise WorkspacePathError("workspace_destination_not_empty")

        def copy_directory(
            source_fd: int,
            prefix: tuple[str, ...],
        ) -> None:
            for name in sorted(os.listdir(source_fd)):
                before = os.stat(
                    name,
                    dir_fd=source_fd,
                    follow_symlinks=False,
                )
                relative = (*prefix, name)
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(
                        name,
                        _DIRECTORY_FLAGS,
                        dir_fd=source_fd,
                    )
                    try:
                        after = os.fstat(child)
                        if not _same_inode(before, after):
                            raise WorkspacePathError("workspace_ancestor_changed")
                        destination.create_directory(
                            Path(*relative),
                            mode=after.st_mode,
                        )
                        copy_directory(child, relative)
                        current = os.stat(
                            name,
                            dir_fd=source_fd,
                            follow_symlinks=False,
                        )
                        if not _same_inode(before, current):
                            raise WorkspacePathError("workspace_file_changed")
                    finally:
                        os.close(child)
                    continue
                if not _regular_single_link(before):
                    if stat.S_ISLNK(before.st_mode):
                        raise WorkspacePathError("workspace_symlink_rejected")
                    raise WorkspacePathError("workspace_regular_single_link_required")
                descriptor = os.open(
                    name,
                    _READ_FLAGS,
                    dir_fd=source_fd,
                )
                try:
                    after = os.fstat(descriptor)
                    if not _regular_single_link(after) or not _same_inode(
                        before, after
                    ):
                        raise WorkspacePathError("workspace_file_changed")
                    destination._atomic_write_from(
                        relative,
                        descriptor,
                        None,
                        overwrite=False,
                        mode=after.st_mode,
                    )
                finally:
                    os.close(descriptor)

        copy_directory(self._require_open(), ())

    def unlink(self, logical_path: str | os.PathLike[str]) -> None:
        parts = self._parts(logical_path)
        self._admit(parts)
        parent_fd, name = self._open_parent(parts, create=False)
        try:
            metadata = self._lstat_target(parent_fd, name)
            if not _regular_single_link(metadata):
                raise WorkspacePathError("workspace_regular_single_link_required")
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    @staticmethod
    def _remove_tree_descriptor(descriptor: int) -> None:
        """Remove a directory tree while retaining descriptor authority."""
        for name in sorted(os.listdir(descriptor)):
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    after = os.fstat(child)
                    if not _same_inode(before, after):
                        raise WorkspacePathError("workspace_ancestor_changed")
                    WorkspaceFilesystem._remove_tree_descriptor(child)
                finally:
                    os.close(child)
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or not _same_inode(
                    before, current
                ):
                    raise WorkspacePathError("workspace_file_changed")
                os.rmdir(name, dir_fd=descriptor)
            else:
                if not _regular_single_link(before):
                    if stat.S_ISLNK(before.st_mode):
                        raise WorkspacePathError("workspace_symlink_rejected")
                    raise WorkspacePathError("workspace_regular_single_link_required")
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not _same_inode(before, current):
                    raise WorkspacePathError("workspace_file_changed")
                os.unlink(name, dir_fd=descriptor)

    def remove_tree(self, logical_path: str | os.PathLike[str]) -> None:
        """Remove a trusted child tree without following links or hardlinks."""
        parts = self._parts(logical_path)
        if not parts:
            raise WorkspacePathError("workspace_file_path_required")
        self._admit(parts)
        parent_fd, name = self._open_parent(parts, create=False)
        child = -1
        try:
            before = self._lstat_target(parent_fd, name)
            if not stat.S_ISDIR(before.st_mode):
                raise WorkspacePathError("workspace_directory_required")
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            after = os.fstat(child)
            if not stat.S_ISDIR(after.st_mode) or not _same_inode(before, after):
                raise WorkspacePathError("workspace_ancestor_changed")
            self._remove_tree_descriptor(child)
            os.close(child)
            child = -1
            current = self._lstat_target(parent_fd, name)
            if not stat.S_ISDIR(current.st_mode) or not _same_inode(before, current):
                raise WorkspacePathError("workspace_file_changed")
            os.rmdir(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            if child >= 0:
                os.close(child)
            os.close(parent_fd)

    def _walk(
        self,
        start_parts: Sequence[str],
        *,
        max_depth: int | None,
    ) -> Iterator[tuple[tuple[str, ...], os.stat_result, str]]:
        self._admit(start_parts)
        directory_fd = self._open_directory_parts(start_parts, create=False)

        def visit(
            descriptor: int,
            prefix: tuple[str, ...],
            level: int,
        ) -> Iterator[tuple[tuple[str, ...], os.stat_result, str]]:
            for name in sorted(os.listdir(descriptor)):
                try:
                    before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                relative = prefix + (name,)
                if stat.S_ISDIR(before.st_mode):
                    child = -1
                    try:
                        child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                        after = os.fstat(child)
                        if not _same_inode(before, after):
                            raise WorkspacePathError("workspace_ancestor_changed")
                        yield relative, after, "dir"
                        if max_depth is None or level < max_depth:
                            yield from visit(child, relative, level + 1)
                    except (FileNotFoundError, WorkspacePathError, OSError):
                        continue
                    finally:
                        if child >= 0:
                            os.close(child)
                elif _regular_single_link(before):
                    yield relative, before, "file"
                else:
                    continue

        try:
            yield from visit(directory_fd, (), 1)
        finally:
            os.close(directory_fd)

    def list_entries(
        self,
        logical_path: str | os.PathLike[str] = ".",
        *,
        depth: int = 1,
    ) -> list[WorkspaceEntry]:
        start_parts = self._parts(logical_path)
        maximum = max(1, int(depth))
        return [
            WorkspaceEntry(
                path=PurePosixPath(*relative).as_posix(),
                kind=kind,
                size=metadata.st_size,
                mtime=metadata.st_mtime,
            )
            for relative, metadata, kind in self._walk(
                start_parts,
                max_depth=maximum,
            )
        ]

    @staticmethod
    def _valid_pattern(pattern: str) -> str:
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts or "\x00" in pattern:
            raise WorkspacePathError("workspace_pattern_invalid")
        return pattern

    @staticmethod
    def _pattern_matches(path: str, pattern: str) -> bool:
        pure = PurePosixPath(path)
        if pure.match(pattern):
            return True
        if pattern.startswith("**/") and pure.match(pattern[3:]):
            return True
        return False

    def glob(
        self,
        pattern: str,
        *,
        root: str | os.PathLike[str] = ".",
        limit: int | None = None,
    ) -> list[str]:
        admitted_pattern = self._valid_pattern(str(pattern))
        start_parts = self._parts(root)
        matches = [
            (PurePosixPath(*relative).as_posix(), metadata.st_mtime)
            for relative, metadata, _kind in self._walk(start_parts, max_depth=None)
            if self._pattern_matches(
                PurePosixPath(*relative).as_posix(), admitted_pattern
            )
        ]
        matches.sort(key=lambda item: item[1], reverse=True)
        paths = [path for path, _mtime in matches]
        if limit is not None and int(limit) >= 0:
            paths = paths[: int(limit)]
        return paths

    def grep(
        self,
        pattern: str | Pattern[str],
        *,
        root: str | os.PathLike[str] = ".",
        include: str | None = None,
        limit: int = 100,
        encoding: str = "utf-8",
    ) -> list[dict[str, object]]:
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        include_pattern = self._valid_pattern(include) if include else None
        start_parts = self._parts(root)
        matches: list[dict[str, object]] = []
        maximum = int(limit or 0)
        for relative, _metadata, kind in self._walk(start_parts, max_depth=None):
            if kind != "file":
                continue
            relative_text = PurePosixPath(*relative).as_posix()
            if include_pattern and not fnmatch.fnmatch(relative_text, include_pattern):
                continue
            try:
                text = self.read_text(
                    Path(*start_parts, *relative),
                    encoding=encoding,
                    errors="ignore",
                )
            except (FileNotFoundError, WorkspacePathError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        {"path": relative_text, "line": line_number, "text": line}
                    )
                    if maximum > 0 and len(matches) >= maximum:
                        return matches
        return matches


__all__ = [
    "WorkspaceEntry",
    "WorkspaceFileInfo",
    "WorkspaceFilesystem",
    "WorkspacePathError",
]
