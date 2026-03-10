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
    repo_tmp_root = _resolve_existing(repo_root_resolved / "tmp")

    def _is_within(base: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(base)
            return True
        except ValueError:
            return False

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
    if resolved == repo_tmp_root:
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (repo tmp root)")
    if (resolved / ".git").exists():
        raise RuntimeError(f"[safety] Refusing {label}: '{resolved}' (contains .git)")
    if not _is_within(repo_tmp_root, resolved):
        raise RuntimeError(
            f"[safety] Refusing {label}: '{resolved}' (must live under '{repo_tmp_root}')"
        )
    return resolved
