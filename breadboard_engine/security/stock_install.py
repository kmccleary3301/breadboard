"""Read-only stock installations explicitly pinned by an engine profile."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping, Sequence

from .credential_boundary import _paths_overlap
from .isolation_errors import ProcessIsolationUnavailable


def stock_install_digest(root: Path) -> str:
    """SHA256 tree-v1: relative UTF-8 names and each regular file's SHA256."""
    digest = hashlib.sha256(b"breadboard-stock-install-v1\0")
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ProcessIsolationUnavailable("stock installation contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ProcessIsolationUnavailable("stock installation contains a non-regular file")
        name = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
        count += 1
    if not count:
        raise ProcessIsolationUnavailable("stock installation is empty")
    return digest.hexdigest()


def validate_stock_read_only_roots(
    entries: Sequence[Mapping[str, str]], *, workspace: Path,
    protected_paths: Sequence[Path],
) -> tuple[Path, ...]:
    roots = []
    for entry in entries:
        try:
            lexical = Path(entry["path"]).expanduser()
            root = lexical.resolve(strict=True)
            expected = entry["digest"]
        except (KeyError, TypeError, OSError) as exc:
            raise ProcessIsolationUnavailable("stock installation pin is unavailable") from exc
        if lexical.is_symlink() or not root.is_dir() or _paths_overlap(root, workspace):
            raise ProcessIsolationUnavailable("stock installation requires an external read-only directory")
        if any(_paths_overlap(root, protected) for protected in protected_paths):
            raise ProcessIsolationUnavailable("stock installation overlaps a protected credential location")
        if stock_install_digest(root) != expected:
            raise ProcessIsolationUnavailable(f"stock installation digest mismatch: {root}")
        roots.append(root)
    return tuple(dict.fromkeys(roots))
