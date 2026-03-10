from __future__ import annotations

from pathlib import Path
import os
import tempfile


def _resolve_existing(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except FileNotFoundError:
        return path.expanduser().absolute()


def assert_disposable_workspace_path(
    target: str | os.PathLike[str] | Path,
    *,
    repo_root: Path,
    label: str = "workspace",
) -> Path:
    target_path = Path(target)
    resolved = _resolve_existing(target_path)
    repo_root_resolved = _resolve_existing(repo_root)
    home = _resolve_existing(Path.home())
    tmp_root = _resolve_existing(Path(tempfile.gettempdir()))

    if resolved == Path("/"):
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}'")
    if resolved == repo_root_resolved:
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (repo root)")
    if resolved in repo_root_resolved.parents:
        raise RuntimeError(
            f"[safety] Refusing {label}: '{resolved}' (ancestor of repo root '{repo_root_resolved}')"
        )
    if resolved == home:
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (home dir)")
    if resolved == tmp_root:
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (tmp root)")
    if (resolved / ".git").exists():
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (contains .git)")
    return resolved
