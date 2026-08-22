from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

import breadboard.rl.harness.mount_namespace_broker as broker


def test_runtime_path_reopened_after_unshare_binds_exact_authority(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        pytest.skip("requires rootful Linux mount namespace capability")
    source = tmp_path / "runc"
    source.write_bytes(b"runtime-authority")
    source.chmod(0o500)
    inherited_fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
    read_end, write_end = os.pipe()
    parent_pid = os.getpid()
    child = os.fork()
    if child == 0:
        os.close(read_end)
        result: dict[str, object]
        reopened_fd = -1
        target = tmp_path / "runtime-bind"
        try:
            broker._enter_private_mount_namespace()
            target.write_bytes(b"")
            reopened_fd = os.open(
                source,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            inherited = os.fstat(inherited_fd)
            reopened = os.fstat(reopened_fd)
            path_metadata = os.stat(source, follow_symlinks=False)
            broker._bind(reopened_fd, str(target), readonly=False)
            mounted = os.stat(target, follow_symlinks=False)
            result = {
                "ok": True,
                "identity_equal": (
                    inherited.st_dev,
                    inherited.st_ino,
                    inherited.st_ctime_ns,
                    inherited.st_size,
                    stat.S_IFMT(inherited.st_mode),
                ) == (
                    reopened.st_dev,
                    reopened.st_ino,
                    reopened.st_ctime_ns,
                    reopened.st_size,
                    stat.S_IFMT(reopened.st_mode),
                ),
                "path_identity_equal": (
                    path_metadata.st_dev,
                    path_metadata.st_ino,
                ) == (reopened.st_dev, reopened.st_ino),
                "mounted_identity_equal": (
                    mounted.st_dev,
                    mounted.st_ino,
                ) == (reopened.st_dev, reopened.st_ino),
            }
            broker._unmount(str(target))
            target.unlink()
        except OSError as exc:
            result = {"ok": False, "errno": exc.errno, "message": str(exc)}
        finally:
            if reopened_fd >= 0:
                os.close(reopened_fd)
            os.write(write_end, json.dumps(result, sort_keys=True).encode("ascii"))
            os.close(write_end)
            os._exit(0)
    os.close(write_end)
    raw = os.read(read_end, 16 * 1024)
    os.close(read_end)
    _, status = os.waitpid(child, 0)
    os.close(inherited_fd)
    assert status == 0
    result = json.loads(raw)
    if not result["ok"] and result.get("errno") in {1, 13}:
        pytest.skip("kernel denied mount namespace or bind capability")
    assert result == {
        "identity_equal": True,
        "mounted_identity_equal": True,
        "path_identity_equal": True,
        "ok": True,
    }
