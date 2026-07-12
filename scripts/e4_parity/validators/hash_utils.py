from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO


def sha256_bytes(data: bytes) -> str:
    """Return a sha256 digest with the repository-standard prefix."""

    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_hex(data: bytes) -> str:
    """Return a bare lowercase sha256 hex digest."""

    return hashlib.sha256(data).hexdigest()


def sha256_stream_hex(stream: BinaryIO) -> tuple[str, int]:
    """Return a bare digest and byte count for a binary stream."""

    digest = hashlib.sha256()
    total = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def copy_stream_sha256_hex(source: BinaryIO, target: BinaryIO) -> tuple[str, int]:
    """Copy a binary stream while returning its bare digest and byte count."""

    digest = hashlib.sha256()
    total = 0
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
        target.write(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def sha256_file(path: Path) -> str:
    """Return the prefixed sha256 digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_path(path: Path) -> str:
    """Alias for file hashes at call sites that already use path terminology."""

    return sha256_file(path)


def sha256_text(text: str) -> str:
    """Return the prefixed sha256 digest for UTF-8 text."""

    return sha256_bytes(text.encode("utf-8"))


def sha256_json(value: Any) -> str:
    """Return the prefixed sha256 digest for compact canonical JSON."""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)
