from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import stat


@dataclass(frozen=True, slots=True)
class ModuleArtifactIdentity:
    digest: str
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int


def measure_module_artifact(path_value: str) -> ModuleArtifactIdentity:
    path = os.path.abspath(path_value)
    if os.path.normpath(path) != path:
        raise RuntimeError("runner module path is not normalized")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeError("runner module artifact is not a private regular file")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise RuntimeError("runner module artifact changed during measurement")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise RuntimeError("runner module artifact changed during measurement")
        after = os.fstat(fd)
        identity = ModuleArtifactIdentity(
            digest="sha256:" + digest.hexdigest(),
            device=after.st_dev,
            inode=after.st_ino,
            size_bytes=after.st_size,
            mtime_ns=after.st_mtime_ns,
        )
        if (
            identity.device,
            identity.inode,
            identity.size_bytes,
            identity.mtime_ns,
        ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise RuntimeError("runner module artifact changed during measurement")
        current = os.stat(path, follow_symlinks=False)
        if (
            identity.device,
            identity.inode,
            identity.size_bytes,
            identity.mtime_ns,
        ) != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            raise RuntimeError("runner module artifact was replaced during measurement")
        return identity
    finally:
        os.close(fd)


__all__ = ["ModuleArtifactIdentity", "measure_module_artifact"]
