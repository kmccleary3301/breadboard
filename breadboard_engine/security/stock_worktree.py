"""Resolve stock's Instance.worktree using a credential-isolated metadata probe.

OpenCode 715b844c project/project.ts:90-169 chooses the closest .git,
uses its cached/root-commit ID, then git rev-parse --show-toplevel.
project/instance.ts:27-31 uses that sandbox, not the git-common-dir project root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence

from .launch_policy import build_restricted_process_command, ProcessIsolationUnavailable


_PROBE = r'''
import json, os, pathlib, shutil, subprocess, sys
marker = pathlib.Path(sys.argv[1])
sandbox = str(marker.parent)
git = shutil.which("git")
try:
    cached = (marker / "opencode").read_text().strip()
except OSError:
    cached = ""
identifier = cached
if git and not identifier:
    try:
        result = subprocess.run([git, "rev-list", "--max-parents=0", "--all"],
                                cwd=sandbox, capture_output=True, text=True)
        roots = sorted(line.strip() for line in result.stdout.splitlines() if line.strip())
        identifier = roots[0] if roots else ""
    except OSError:
        identifier = ""
if git and identifier:
    try:
        result = subprocess.run([git, "rev-parse", "--show-toplevel"],
                                cwd=sandbox, capture_output=True, text=True)
        top = result.stdout.strip()
        if top:
            sandbox = os.path.normpath(top if os.path.isabs(top) else os.path.join(sandbox, top))
    except OSError:
        pass
print(json.dumps(sandbox))
'''


_POINTER_PROBE = r'''
import json, os, pathlib, sys
pointer = pathlib.Path(sys.argv[1])
kind = sys.argv[2]
if not pointer.is_file():
    print("null")
else:
    value = pointer.read_text().strip()
    if kind == "gitdir":
        if not value.startswith("gitdir: "):
            raise ValueError("Invalid stock gitdir pointer")
        value = value.removeprefix("gitdir: ")
    if not value or "\n" in value:
        raise ValueError("Invalid stock git metadata pointer")
    target = pathlib.Path(value)
    print(json.dumps(os.path.abspath(target if target.is_absolute() else pointer.parent / target)))
'''


_MODULE_PROBE = r'''
import json, os, pathlib, shutil, subprocess, sys
git = shutil.which("git")
if not git:
    raise ValueError("Git is required to verify submodule provenance")
result = subprocess.run([git, "--git-dir=" + sys.argv[1], "config", "--get", "core.worktree"],
                        capture_output=True, text=True)
value = result.stdout.strip()
if result.returncode or not value:
    raise ValueError("Submodule metadata omitted core.worktree")
print(json.dumps(os.path.abspath(value if os.path.isabs(value) else os.path.join(sys.argv[1], value))))
'''


def _verify_pointer_provenance(marker, git_directory, common, directory, candidate,
                               environment, isolated_home, protected_paths, timeout):
    """Only genuine reciprocal worktree or superproject module links grant reads."""
    if marker.is_dir() and git_directory.resolve().is_relative_to(candidate.resolve()):
        return
    if common is not None:
        if git_directory.resolve().parent != (common / "worktrees").resolve():
            raise ProcessIsolationUnavailable("Stock git metadata lacks linked-worktree provenance")
        backlink = _read_pointer(
            git_directory / "gitdir", "backlink", directory, candidate, environment,
            isolated_home, protected_paths, timeout, (git_directory,),
        )
        if backlink is None or backlink.resolve() != marker.resolve():
            raise ProcessIsolationUnavailable("Stock linked-worktree metadata backlink does not match workspace")
        return
    super_marker = next((parent / ".git" for parent in candidate.parents
                         if (parent / ".git").exists()), None)
    if super_marker is None:
        raise ProcessIsolationUnavailable("Stock git pointer lacks reciprocal worktree or submodule provenance")
    super_git = super_marker if super_marker.is_dir() else _read_pointer(
        super_marker, "gitdir", directory, candidate, environment, isolated_home,
        protected_paths, timeout, (super_marker,),
    )
    if super_git is None:
        raise ProcessIsolationUnavailable("Stock superproject git pointer did not resolve")
    super_common = _read_pointer(
        super_git / "commondir", "commondir", directory, candidate, environment,
        isolated_home, protected_paths, timeout, (super_git,),
    )
    modules = ((super_common or super_git) / "modules").resolve()
    if not git_directory.resolve().is_relative_to(modules) or git_directory.resolve() == modules:
        raise ProcessIsolationUnavailable("Stock git metadata is not under the superproject modules directory")
    command, child_env = build_restricted_process_command(
        [sys.executable, "-I", "-c", _MODULE_PROBE, str(git_directory)],
        workspace=directory, shell=False, environment=environment, protected_paths=protected_paths,
        read_only_worktree_root=candidate, read_only_git_metadata_roots=(git_directory,),
    )
    child_env["HOME"] = isolated_home
    result = subprocess.run(command, cwd=directory, env=child_env, capture_output=True, text=True, timeout=timeout)
    if result.returncode or Path(json.loads(result.stdout)).resolve() != candidate.resolve():
        raise ProcessIsolationUnavailable("Stock submodule core.worktree does not match workspace")


def _read_pointer(pointer, kind, directory, candidate, environment, isolated_home, protected_paths, timeout, git_roots):
    command, child_env = build_restricted_process_command(
        [sys.executable, "-I", "-c", _POINTER_PROBE, str(pointer), kind],
        workspace=directory, shell=False, environment=environment,
        protected_paths=protected_paths, read_only_worktree_root=candidate,
        read_only_git_metadata_roots=git_roots,
    )
    child_env["HOME"] = isolated_home
    result = subprocess.run(command, cwd=directory, env=child_env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ProcessIsolationUnavailable(f"Stock git metadata discovery failed: {result.stderr}")
    value = json.loads(result.stdout)
    return Path(value) if value is not None else None


def resolve_stock_worktree(
    workspace: str,
    environment: Mapping[str, str],
    *,
    isolated_home: str,
    protected_paths: Sequence[str | os.PathLike[str]] = (),
    timeout: float = 180,
) -> tuple[Path | None, tuple[Path, ...]]:
    """Do not execute even metadata probes outside BB's credential boundary."""
    directory = Path(workspace).resolve()
    marker = next((parent / ".git" for parent in (directory, *directory.parents)
                   if (parent / ".git").exists()), None)
    if marker is None:
        return None, ()
    candidate = marker.parent
    git_directory = marker if marker.is_dir() else _read_pointer(
        marker, "gitdir", directory, candidate, environment, isolated_home, protected_paths, timeout, (),
    )
    if git_directory is None:
        raise ProcessIsolationUnavailable("Stock gitdir pointer did not resolve a metadata directory")
    common = _read_pointer(git_directory / "commondir", "commondir", directory, candidate,
                           environment, isolated_home, protected_paths, timeout, (git_directory,))
    git_roots = tuple(dict.fromkeys((git_directory, *((common,) if common is not None else ()))))
    _verify_pointer_provenance(marker, git_directory, common, directory, candidate,
                               environment, isolated_home, protected_paths, timeout)
    command, child_env = build_restricted_process_command(
        [sys.executable, "-I", "-c", _PROBE, str(marker)],
        workspace=directory, shell=False, environment=environment,
        protected_paths=protected_paths, read_only_worktree_root=candidate,
        read_only_git_metadata_roots=git_roots,
    )
    child_env["HOME"] = isolated_home
    result = subprocess.run(command, cwd=directory, env=child_env, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise ProcessIsolationUnavailable(f"Stock worktree discovery failed: {result.stderr}")
    root = Path(json.loads(result.stdout))
    if not root.is_absolute() or not root.is_dir():
        raise ProcessIsolationUnavailable(f"Stock resolved an invalid git worktree: {root}")
    return root, git_roots
