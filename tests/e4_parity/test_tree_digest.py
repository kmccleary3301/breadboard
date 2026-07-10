from __future__ import annotations

import hashlib
import os
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from scripts.e4_parity.tree_digest import digest_directory


EXPECTED_PREIMAGE = (
    b'{"files":['
    b'{"bytes":3,"path":".hidden","sha256":"e392dad8b08599f74d4819cd291feef81ab4389e0a6fae2b1286f99411b0c7ca"},'
    b'{"bytes":7,"path":"a/nested.txt","sha256":"370a8c04b8a65bb4494275eec227f1b694db04c76da6b0b8ae88ed1ab19790a3"},'
    b'{"bytes":3,"path":"z.txt","sha256":"37da93e5b4f2d2ee5d3551d287f7ecff03380e6fb84cce16405c16adbb10195e"},'
    b'{"bytes":3,"path":"\xe7\x8c\xab.txt","sha256":"77af778b51abd4a3c51c5ddd97204a9c3ae614ebccb75a606c3b6865aed6744e"}'
    b']}'
)
EMPTY_PREIMAGE = b'{"files":[]}'


def _assert_digest_matches_preimage(result: object, expected_preimage: bytes, expected_bytes: int) -> None:
    assert result.preimage == expected_preimage
    assert result.digest == f"sha256:{hashlib.sha256(expected_preimage).hexdigest()}"
    assert result.bytes == expected_bytes


def test_digest_directory_emits_exact_canonical_preimage_and_digest(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "z.txt").write_bytes(b"zee")
    (tmp_path / "猫.txt").write_bytes(b"cat")
    (tmp_path / ".hidden").write_bytes(b"dot")
    (tmp_path / "a" / "nested.txt").write_bytes(b"nested\n")

    _assert_digest_matches_preimage(digest_directory(tmp_path), EXPECTED_PREIMAGE, 16)


def test_empty_directory_has_canonical_empty_files_preimage(tmp_path: Path) -> None:
    _assert_digest_matches_preimage(digest_directory(tmp_path), EMPTY_PREIMAGE, 0)


def test_hard_links_are_hashed_as_distinct_regular_file_paths(tmp_path: Path) -> None:
    original = tmp_path / "original"
    linked = tmp_path / "linked"
    original.write_bytes(b"shared")
    try:
        os.link(original, linked)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    file_hash = hashlib.sha256(b"shared").hexdigest().encode("ascii")
    expected = (
        b'{"files":['
        b'{"bytes":6,"path":"linked","sha256":"' + file_hash + b'"},'
        b'{"bytes":6,"path":"original","sha256":"' + file_hash + b'"}'
        b']}'
    )

    _assert_digest_matches_preimage(digest_directory(tmp_path), expected, 12)


def test_repeated_calls_return_identical_preimage_digest_and_byte_sum(tmp_path: Path) -> None:
    (tmp_path / "payload").write_bytes(b"stable")

    first = digest_directory(tmp_path)
    second = digest_directory(tmp_path)

    assert (second.preimage, second.digest, second.bytes) == (first.preimage, first.digest, first.bytes)


def test_symlink_is_rejected_even_when_its_target_is_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"data")
    link = tmp_path / "link"
    try:
        link.symlink_to(target.name)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises((OSError, ValueError)):
        digest_directory(tmp_path)


def test_directory_symlink_cannot_escape_root_by_realpath(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"outside")
    escape = root / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises((OSError, ValueError)):
        digest_directory(root)


def test_fifo_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        pytest.skip(f"FIFOs are unavailable: {exc}")

    with pytest.raises((OSError, ValueError)):
        digest_directory(tmp_path)


def test_unix_socket_is_rejected() -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix sockets are unavailable")
    with tempfile.TemporaryDirectory(prefix="tree-digest-", dir="/tmp") as directory:
        root = Path(directory)
        socket_path = root / "service.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(socket_path))
            with pytest.raises((OSError, ValueError)):
                digest_directory(root)
        except OSError as exc:
            pytest.skip(f"Unix sockets are unavailable: {exc}")
        finally:
            server.close()


def test_unreadable_regular_file_is_rejected(tmp_path: Path) -> None:
    unreadable = tmp_path / "unreadable"
    unreadable.write_bytes(b"secret")
    original_mode = stat.S_IMODE(unreadable.stat().st_mode)
    unreadable.chmod(0)
    try:
        try:
            with unreadable.open("rb"):
                pass
        except PermissionError:
            with pytest.raises((OSError, ValueError)):
                digest_directory(tmp_path)
        else:
            pytest.skip("current user can read mode-000 files")
    finally:
        unreadable.chmod(original_mode)


def test_undecodable_filename_is_rejected(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("bytes filesystem paths require POSIX")
    root = os.fsencode(tmp_path)
    undecodable = root + b"/invalid-\xff"
    try:
        descriptor = os.open(undecodable, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"undecodable filenames are unavailable: {exc}")
    else:
        os.write(descriptor, b"data")
        os.close(descriptor)
    try:
        with pytest.raises((OSError, UnicodeError, ValueError)):
            digest_directory(tmp_path)
    finally:
        os.unlink(undecodable)


def test_non_directory_root_is_rejected(tmp_path: Path) -> None:
    regular_file = tmp_path / "file"
    regular_file.write_bytes(b"not a directory")

    with pytest.raises((NotADirectoryError, ValueError)):
        digest_directory(regular_file)
