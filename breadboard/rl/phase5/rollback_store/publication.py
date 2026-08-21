from __future__ import annotations

from ._imports import *
from .models import *

def _rename_noreplace(
    source: str,
    destination: str,
    directory_fd: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = libc.renameatx_np
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            directory_fd,
            source_bytes,
            directory_fd,
            destination_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            directory_fd,
            source_bytes,
            directory_fd,
            destination_bytes,
            0x00000001,
        )
    else:
        raise RollbackCorruptionError("atomic no-replace rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination,
        )


def _rename_noreplace_between(
    source: str,
    destination: str,
    source_directory_fd: int,
    destination_directory_fd: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = libc.renameatx_np
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            source_directory_fd,
            source_bytes,
            destination_directory_fd,
            destination_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        result = function(
            source_directory_fd,
            source_bytes,
            destination_directory_fd,
            destination_bytes,
            0x00000001,
        )
    else:
        raise RollbackCorruptionError("atomic no-replace rename is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination,
        )


@dataclass(slots=True)
class _HeldStoreFile:
    name: str
    fd: int
    path_directory_fd: int
    identity: tuple[int, int, int, int, int, int, int, int]
    raw: bytes

    @staticmethod
    def _identity(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_uid,
            value.st_gid,
            stat.S_IMODE(value.st_mode),
            value.st_nlink,
            value.st_size,
            value.st_ctime_ns,
        )

    @classmethod
    def capture(
        cls,
        store: "_PinnedSignedDirectory",
        name: str,
        *,
        directory_fd: int | None = None,
    ) -> "_HeldStoreFile":
        path_directory_fd = (
            store._path_directory_fd(name) if directory_fd is None else directory_fd
        )
        if directory_fd is None:
            fd = store._open_regular(name, os.O_RDONLY)
        else:
            fd = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        try:
            value = os.fstat(fd)
            if (
                not stat.S_ISREG(value.st_mode)
                or stat.S_IMODE(value.st_mode) != 0o600
                or value.st_nlink != 1
                or (value.st_uid, value.st_gid) != store._owner
                or value.st_size > _MAX_RECORD_BYTES
            ):
                raise RollbackCorruptionError("recovery authority file is not exact")
            raw = os.pread(fd, value.st_size + 1, 0)
            if len(raw) != value.st_size:
                raise RollbackCorruptionError(
                    "recovery authority file changed during capture"
                )
            return cls(
                name,
                fd,
                path_directory_fd,
                cls._identity(value),
                raw,
            )
        except BaseException:
            os.close(fd)
            raise

    def revalidate(
        self,
        store: "_PinnedSignedDirectory",
        *,
        path_name: str | None = None,
    ) -> None:
        expected_name = self.name if path_name is None else path_name
        descriptor_value = os.fstat(self.fd)
        path_value = os.stat(
            expected_name,
            dir_fd=self.path_directory_fd,
            follow_symlinks=False,
        )
        if (
            self._identity(descriptor_value) != self.identity
            or self._identity(path_value) != self.identity
            or os.pread(self.fd, len(self.raw) + 1, 0) != self.raw
        ):
            raise RollbackCorruptionError("recovery authority identity changed")

    def refresh_path_identity(
        self,
        store: "_PinnedSignedDirectory",
        path_name: str,
    ) -> None:
        descriptor_value = os.fstat(self.fd)
        path_value = os.stat(
            path_name,
            dir_fd=self.path_directory_fd,
            follow_symlinks=False,
        )
        descriptor_identity = self._identity(descriptor_value)
        path_identity = self._identity(path_value)
        if (
            descriptor_identity[:7] != self.identity[:7]
            or path_identity != descriptor_identity
            or os.pread(self.fd, len(self.raw) + 1, 0) != self.raw
        ):
            raise RollbackCorruptionError("renamed recovery authority identity changed")
        self.identity = descriptor_identity

    def close(self) -> None:
        os.close(self.fd)


@dataclass(slots=True)
class _RollbackRecoveryCapsule:
    transaction_id: str
    intent: _HeldStoreFile
    predecessor: _HeldStoreFile
    predecessor_commit: _HeldStoreFile
    successor: _HeldStoreFile | None
    head_name: str
    displaced_name: str
    candidate_name: str
    quarantine_name: str
    tombstone_name: str
    successor_history_name: str
    successor_commit_name: str
    state: str
    candidate: _HeldStoreFile | None = None
    installed_head: _HeldStoreFile | None = None

    def close(self) -> None:
        if self.successor is not None:
            self.successor.close()
        if self.candidate is not None:
            self.candidate.close()
        if self.installed_head is not None:
            self.installed_head.close()
        self.predecessor_commit.close()
        self.predecessor.close()
        self.intent.close()


class _PublicationTransaction:
    def __init__(
        self,
        store: "_PinnedSignedDirectory",
        revalidate: Any,
    ) -> None:
        self.store = store
        self.revalidate = revalidate
        self.created: set[str] = set()
        self.replaced: dict[str, bytes | None] = {}
        self.mutated_replacements: set[str] = set()
        self.temps: set[str] = set()
        self.transaction_id = uuid.uuid4().hex

    def capture_replaced(self, name: str, old_payload: bytes | None) -> None:
        self.replaced.setdefault(name, old_payload)

    def mark_replaced(self, name: str) -> None:
        self.mutated_replacements.add(name)

    def rollback(self) -> None:
        failures: list[BaseException] = []
        for name in sorted(self.created):
            try:
                os.unlink(name, dir_fd=self.store._root_fd)
            except FileNotFoundError:
                pass
            except BaseException as error:
                failures.append(error)
        for name in sorted(self.mutated_replacements):
            old_payload = self.replaced[name]
            try:
                if old_payload is None:
                    try:
                        os.unlink(name, dir_fd=self.store._root_fd)
                    except FileNotFoundError:
                        pass
                else:
                    self.store._rollback_replaced_head(
                        name,
                        old_payload,
                        self.transaction_id,
                    )
            except BaseException as error:
                failures.append(error)
        for name in sorted(self.temps):
            try:
                os.unlink(name, dir_fd=self.store._root_fd)
            except FileNotFoundError:
                pass
            except BaseException as error:
                failures.append(error)
        try:
            os.fsync(self.store._root_fd)
        except BaseException as error:
            failures.append(error)
        if failures:
            raise RollbackCorruptionError(
                "rollback publication transaction could not restore prior state"
            ) from failures[0]

__all__ = ['_rename_noreplace', '_rename_noreplace_between', '_HeldStoreFile', '_RollbackRecoveryCapsule', '_PublicationTransaction']
