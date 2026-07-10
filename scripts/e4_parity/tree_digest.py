"""Canonical, fail-closed directory-tree digests for E4 declared inputs."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


class TreeDigestError(ValueError):
    """A directory contains an entry outside the canonical digest domain."""


@dataclass(frozen=True)
class TreeDigest:
    preimage: bytes
    digest: str
    bytes: int


def _strict_name(name: str, path: Path) -> bytes:
    try:
        return name.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise TreeDigestError(f"directory entry name is not strict UTF-8: {path!s}") from exc


def _confined(path: Path, root_real: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_real)
    except (OSError, ValueError) as exc:
        raise TreeDigestError(f"directory entry escapes digest root: {path!s}") from exc


def digest_directory(root: Path) -> TreeDigest:
    """Digest all regular files below *root* using the AM9a canonical domain."""
    root = Path(root)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise NotADirectoryError(str(root)) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise NotADirectoryError(str(root))
    try:
        root_real = root.resolve(strict=True)
    except OSError as exc:
        raise TreeDigestError(f"cannot resolve directory root: {root!s}") from exc

    files: list[tuple[bytes, dict[str, object]]] = []

    def visit(directory: Path, relative_parts: tuple[str, ...]) -> None:
        _confined(directory, root_real)
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise TreeDigestError(f"cannot read directory: {directory!s}") from exc
        ordered: list[tuple[bytes, os.DirEntry[str]]] = []
        for entry in entries:
            encoded = _strict_name(entry.name, directory / entry.name)
            ordered.append((encoded, entry))
        ordered.sort(key=lambda item: item[0])
        for _, entry in ordered:
            path = directory / entry.name
            parts = (*relative_parts, entry.name)
            relative = "/".join(parts)
            try:
                relative_bytes = relative.encode("utf-8", errors="strict")
                entry_stat = entry.stat(follow_symlinks=False)
            except UnicodeEncodeError as exc:
                raise TreeDigestError(f"directory entry path is not strict UTF-8: {path!s}") from exc
            except OSError as exc:
                raise TreeDigestError(f"cannot inspect directory entry: {path!s}") from exc
            mode = entry_stat.st_mode
            if stat.S_ISLNK(mode):
                raise TreeDigestError(f"symlinks are outside the directory-digest domain: {path!s}")
            _confined(path, root_real)
            if stat.S_ISDIR(mode):
                visit(path, parts)
                continue
            if not stat.S_ISREG(mode):
                raise TreeDigestError(f"non-regular entry is outside the directory-digest domain: {path!s}")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise TreeDigestError(f"cannot read regular file: {path!s}") from exc
            files.append(
                (
                    relative_bytes,
                    {
                        "path": relative,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                    },
                )
            )

    visit(root, ())
    rows = [row for _, row in sorted(files, key=lambda item: item[0])]
    preimage = json.dumps(
        {"files": rows},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return TreeDigest(
        preimage=preimage,
        digest="sha256:" + hashlib.sha256(preimage).hexdigest(),
        bytes=sum(int(row["bytes"]) for row in rows),
    )


__all__ = ["TreeDigest", "TreeDigestError", "digest_directory"]
