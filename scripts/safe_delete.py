#!/usr/bin/env python3
"""Filesystem deletion helpers with hard safety rails.

These scripts orchestrate parity/golden runs and frequently need to delete and
recreate workspaces. A misconfigured workspace path must never be able to delete
the repo root (or any ancestor directory).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Union


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_existing(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except FileNotFoundError:
        # When the path doesn't exist, resolve() can fail in strict mode on some
        # Python versions. Fall back to an absolute path.
        return path.expanduser().absolute()


def safe_rmtree(
    target: Union[str, os.PathLike[str], Path],
    *,
    repo_root: Optional[Path] = None,
    label: str = "path",
    ignore_errors: bool = False,
) -> None:
    """`shutil.rmtree` with strong guards against catastrophic deletions.

    Refuses to delete:
      - filesystem root (/)
      - the repo root (and any ancestor of repo root)
      - the user's home directory
      - the system temp directory root (e.g., /tmp)
      - any directory that itself contains a `.git/` entry
    """

    if repo_root is None:
        repo_root = _DEFAULT_REPO_ROOT

    target_path = Path(target)
    if not target_path.exists():
        return

    resolved = _resolve_existing(target_path)
    repo_root_resolved = _resolve_existing(repo_root)
    home = _resolve_existing(Path.home())
    tmp_root = _resolve_existing(Path(tempfile.gettempdir()))

    # Hard stops.
    if resolved == Path("/"):
        raise RuntimeError(f"[safety] Refusing to delete {label}: '{resolved}'")
    if resolved == repo_root_resolved:
        raise RuntimeError(
            f"[safety] Refusing to delete {label}: '{resolved}' (repo root)"
        )
    if resolved in repo_root_resolved.parents:
        raise RuntimeError(
            f"[safety] Refusing to delete {label}: '{resolved}' (ancestor of repo root '{repo_root_resolved}')"
        )
    if resolved == home:
        raise RuntimeError(f"[safety] Refusing to delete {label}: '{resolved}' (home dir)")
    if resolved == tmp_root:
        raise RuntimeError(f"[safety] Refusing to delete {label}: '{resolved}' (tmp dir root)")
    if (resolved / ".git").exists():
        raise RuntimeError(
            f"[safety] Refusing to delete {label}: '{resolved}' (contains .git)"
        )

    # Soft stop: allow opting into deleting outside repo/tmp explicitly.
    def _is_within(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    if not (_is_within(resolved, repo_root_resolved) or _is_within(resolved, tmp_root)):
        if os.environ.get("KYLECODE_ALLOW_UNSAFE_RMTREE") != "1":
            raise RuntimeError(
                f"[safety] Refusing to delete {label}: '{resolved}' (outside repo/tmp). "
                "Set KYLECODE_ALLOW_UNSAFE_RMTREE=1 to override."
            )

    if not resolved.is_dir():
        raise RuntimeError(
            f"[safety] Refusing to delete {label}: '{resolved}' (not a directory)"
        )

    shutil.rmtree(resolved, ignore_errors=ignore_errors)

