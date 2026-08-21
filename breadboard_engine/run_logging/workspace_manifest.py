from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


DEFAULT_IGNORE_PATTERNS: Sequence[str] = (
    ".git",
    ".hg",
    ".svn",
    ".breadboard",
    ".claude",
    ".cache",
    "__pycache__",
    "node_modules",
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
)


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    sha256: str
    size: int
    is_binary: bool


def _is_binary_content(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _matches_ignore(rel_path: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def build_workspace_manifest(
    workspace_root: str | os.PathLike[str],
    *,
    ignore_patterns: Sequence[str] | None = None,
) -> dict:
    root = Path(workspace_root).resolve()
    patterns: Sequence[str] = ignore_patterns or DEFAULT_IGNORE_PATTERNS
    entries: List[ManifestEntry] = []
    for file_path in _iter_files(root):
        rel_path = file_path.relative_to(root).as_posix()
        if _matches_ignore(rel_path, patterns):
            continue
        data = file_path.read_bytes()
        entries.append(
            ManifestEntry(
                path=rel_path,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                is_binary=_is_binary_content(data),
            )
        )
    manifest = {
        "root": str(root),
        "ignore": list(patterns),
        "hash": "sha256",
        "files": [entry.__dict__ for entry in sorted(entries, key=lambda item: item.path)],
    }
    return manifest
