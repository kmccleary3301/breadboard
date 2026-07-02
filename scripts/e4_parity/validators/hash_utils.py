from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    """Return a sha256 digest with the repository-standard prefix."""

    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_hex(data: bytes) -> str:
    """Return a bare lowercase sha256 hex digest."""

    return hashlib.sha256(data).hexdigest()


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
