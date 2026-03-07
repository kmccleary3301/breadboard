from __future__ import annotations

import socket
from pathlib import Path

from scripts.atp_snapshot_preflight import _snapshot_dirs_from_env, _validate_dir


def _make_snapshot_dir(tmp_path: Path) -> Path:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "lean.snap").write_bytes(b"snap")
    (snap_dir / "lean.mem").write_bytes(b"mem")
    (snap_dir / "rootfs.ext4").write_bytes(b"rootfs")

    sock_path = snap_dir / "vsock.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(sock_path))
    sock.close()
    return snap_dir


def test_validate_dir_ok(tmp_path: Path) -> None:
    snap_dir = _make_snapshot_dir(tmp_path)
    result = _validate_dir(snap_dir)
    assert result["ok"] is True
    assert result["hygiene"]["lock_present"] is False
    checks = {entry["name"]: entry for entry in result["checks"]}
    assert checks["vsock.sock"]["exists"] is True
    assert checks["vsock.sock"]["is_socket"] is True
    assert checks["lean.snap"]["size_bytes"] > 0


def test_validate_dir_missing_vsock(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "lean.snap").write_bytes(b"snap")
    (snap_dir / "lean.mem").write_bytes(b"mem")
    (snap_dir / "rootfs.ext4").write_bytes(b"rootfs")

    result = _validate_dir(snap_dir)
    assert result["ok"] is True
    assert result["hygiene"]["vsock_active"] is False
    checks = {entry["name"]: entry for entry in result["checks"]}
    assert checks["vsock.sock"]["exists"] is False
    assert checks["vsock.sock"]["parent_writable"] is True


def test_validate_dir_rejects_non_socket_vsock(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "lean.snap").write_bytes(b"snap")
    (snap_dir / "lean.mem").write_bytes(b"mem")
    (snap_dir / "rootfs.ext4").write_bytes(b"rootfs")
    (snap_dir / "vsock.sock").write_text("not-a-socket", encoding="utf-8")

    result = _validate_dir(snap_dir)
    assert result["ok"] is False
    checks = {entry["name"]: entry for entry in result["checks"]}
    assert checks["vsock.sock"]["exists"] is True
    assert checks["vsock.sock"]["is_socket"] is False


def test_validate_dir_strict_hygiene_fails_on_lock_file(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "lean.snap").write_bytes(b"snap")
    (snap_dir / "lean.mem").write_bytes(b"mem")
    (snap_dir / "rootfs.ext4").write_bytes(b"rootfs")
    (snap_dir / ".bb_snapshot_pool.lock").write_text("lock", encoding="utf-8")

    result = _validate_dir(snap_dir, strict_hygiene=True)
    assert result["ok"] is False
    assert result["hygiene"]["lock_present"] is True
    assert "snapshot lock file present" in result["hygiene"]["warnings"]


def test_snapshot_dirs_from_env_prefers_dirs(monkeypatch) -> None:
    monkeypatch.setenv("FIRECRACKER_SNAPSHOT_DIRS", "/tmp/a,/tmp/b")
    monkeypatch.setenv("FIRECRACKER_SNAPSHOT", "/tmp/c/lean.snap")
    dirs = _snapshot_dirs_from_env()
    assert [str(path) for path in dirs] == ["/tmp/a", "/tmp/b"]


def test_snapshot_dirs_from_env_accepts_pathsep(monkeypatch) -> None:
    monkeypatch.setenv("FIRECRACKER_SNAPSHOT_DIRS", "/tmp/a:/tmp/b")
    monkeypatch.setenv("FIRECRACKER_SNAPSHOT", "/tmp/c/lean.snap")
    dirs = _snapshot_dirs_from_env()
    assert [str(path) for path in dirs] == ["/tmp/a", "/tmp/b"]


def test_snapshot_dirs_from_env_falls_back_to_snapshot(monkeypatch) -> None:
    monkeypatch.delenv("FIRECRACKER_SNAPSHOT_DIRS", raising=False)
    monkeypatch.setenv("FIRECRACKER_SNAPSHOT", "/tmp/c/lean.snap")
    dirs = _snapshot_dirs_from_env()
    assert len(dirs) == 1
    assert str(dirs[0]) == "/tmp/c"
