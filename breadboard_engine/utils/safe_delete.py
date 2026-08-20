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
    resolved = validate_workspace_path(target, repo_root=repo_root, label=label)
    if (resolved / ".git").exists():
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (contains .git)")
    repo_root_resolved = _resolve_existing(repo_root)
    repo_tmp_root = _resolve_existing(repo_root_resolved / "tmp")
    if not _is_within(repo_tmp_root, resolved):
        raise RuntimeError(
            f"[safety] Refusing {label}: '{resolved}' (must live under '{repo_tmp_root}')"
        )
    return resolved


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def validate_workspace_path(
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
    if resolved == home:
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (home dir)")
    if resolved == tmp_root:
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (tmp root)")
    return resolved


def is_disposable_workspace_path(
    target: str | os.PathLike[str] | Path,
    *,
    repo_root: Path,
) -> bool:
    resolved = validate_workspace_path(target, repo_root=repo_root)
    if (resolved / ".git").exists():
        return False
    repo_root_resolved = _resolve_existing(repo_root)
    repo_tmp_root = _resolve_existing(repo_root_resolved / "tmp")
    return _is_within(repo_tmp_root, resolved)
