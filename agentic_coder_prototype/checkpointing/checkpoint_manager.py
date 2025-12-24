from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CheckpointSummary:
    checkpoint_id: str
    created_at: int
    preview: str
    tracked_files: int = 0
    additions: int = 0
    deletions: int = 0
    has_untracked_changes: bool = False

    def as_payload(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "preview": self.preview,
            "tracked_files": self.tracked_files,
            "additions": self.additions,
            "deletions": self.deletions,
            "has_untracked_changes": self.has_untracked_changes,
        }


class CheckpointManager:
    """
    Git-backed checkpointing for a single session workspace.

    Implementation notes:
    - Uses a separate git directory stored under `.breadboard/checkpoints/git/`
      so we never touch the user's `.git`.
    - Operates with `--git-dir` + `--work-tree` so no `.git` marker is written
      into the workspace root.
    - Excludes `.breadboard/**` and `.git/**` from snapshots.
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace_dir = Path(workspace_dir).resolve()
        self._root = self._workspace_dir / ".breadboard" / "checkpoints"
        self._git_dir = self._root / "git"
        self._meta_path = self._root / "checkpoints.json"
        self._lock = threading.Lock()

    def _ensure_workspace(self) -> None:
        if not self._workspace_dir.exists() or not self._workspace_dir.is_dir():
            raise RuntimeError(f"workspace directory missing: {self._workspace_dir}")
        self._root.mkdir(parents=True, exist_ok=True)

    def _run_git(self, args: List[str]) -> str:
        cmd = [
            "git",
            f"--git-dir={self._git_dir}",
            f"--work-tree={self._workspace_dir}",
            *args,
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            details = stderr or stdout or f"exit={proc.returncode}"
            raise RuntimeError(f"checkpoint git command failed: {' '.join(args)} ({details})")
        return (proc.stdout or "").strip()

    def _ensure_repo(self) -> None:
        self._ensure_workspace()
        if not (self._git_dir / "HEAD").exists():
            self._git_dir.mkdir(parents=True, exist_ok=True)
            self._run_git(["init", "--quiet"])
            self._run_git(["config", "user.name", "Breadboard Checkpoints"])
            self._run_git(["config", "user.email", "breadboard@local"])
            self._configure_excludes()

    def _configure_excludes(self) -> None:
        info_dir = self._git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_path = info_dir / "exclude"
        patterns = [
            ".breadboard/",
            ".git/",
        ]
        existing = ""
        try:
            existing = exclude_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = ""
        merged = set(line.strip() for line in existing.splitlines() if line.strip())
        merged.update(patterns)
        exclude_path.write_text("\n".join(sorted(merged)) + "\n", encoding="utf-8")

    def _load_meta(self) -> List[Dict[str, Any]]:
        try:
            raw = self._meta_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return list(data) if isinstance(data, list) else []

    def _write_meta(self, entries: List[Dict[str, Any]]) -> None:
        tmp_path = self._meta_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(entries, indent=2, sort_keys=False), encoding="utf-8")
        tmp_path.replace(self._meta_path)

    @staticmethod
    def _next_checkpoint_id(entries: List[Dict[str, Any]]) -> str:
        best = 0
        for entry in entries:
            cid = str(entry.get("checkpoint_id") or entry.get("id") or "")
            if cid.startswith("ckpt-"):
                try:
                    best = max(best, int(cid.split("-", 1)[1]))
                except Exception:
                    continue
        return f"ckpt-{best + 1}"

    @staticmethod
    def _diff_stats(numstat: str) -> tuple[int, int, int]:
        tracked = 0
        additions = 0
        deletions = 0
        for line in (numstat or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            tracked += 1
            add_raw, del_raw = parts[0], parts[1]
            if add_raw.isdigit():
                additions += int(add_raw)
            if del_raw.isdigit():
                deletions += int(del_raw)
        return tracked, additions, deletions

    def create_checkpoint(self, preview: str) -> CheckpointSummary:
        preview_text = str(preview or "").strip() or "Checkpoint"
        with self._lock:
            self._ensure_repo()
            entries = self._load_meta()
            checkpoint_id = self._next_checkpoint_id(entries)
            created_at = int(time.time())

            # Stage the full workspace (excluding ignored paths) and commit.
            self._run_git(["add", "-A"])
            self._run_git(["commit", "--allow-empty", "-m", f"{checkpoint_id}: {preview_text}", "--quiet"])
            commit_hash = self._run_git(["rev-parse", "HEAD"]).strip()

            tracked_files = 0
            additions = 0
            deletions = 0
            parents = self._run_git(["rev-list", "--parents", "-n", "1", "HEAD"]).split()
            if len(parents) >= 2:
                parent_hash = parents[1]
                numstat = self._run_git(["diff", "--numstat", parent_hash, commit_hash])
                tracked_files, additions, deletions = self._diff_stats(numstat)

            entry = {
                "checkpoint_id": checkpoint_id,
                "created_at": created_at,
                "preview": preview_text,
                "git_commit": commit_hash,
                "tracked_files": tracked_files,
                "additions": additions,
                "deletions": deletions,
                "has_untracked_changes": False,
            }
            entries.append(entry)
            self._write_meta(entries)

            return CheckpointSummary(
                checkpoint_id=checkpoint_id,
                created_at=created_at,
                preview=preview_text,
                tracked_files=tracked_files,
                additions=additions,
                deletions=deletions,
                has_untracked_changes=False,
            )

    def list_checkpoints(self) -> List[CheckpointSummary]:
        with self._lock:
            self._ensure_repo()
            entries = self._load_meta()
            summaries: List[CheckpointSummary] = []
            for entry in entries:
                summaries.append(
                    CheckpointSummary(
                        checkpoint_id=str(entry.get("checkpoint_id") or entry.get("id") or ""),
                        created_at=int(entry.get("created_at") or entry.get("timestamp") or 0),
                        preview=str(entry.get("preview") or ""),
                        tracked_files=int(entry.get("tracked_files") or 0),
                        additions=int(entry.get("additions") or 0),
                        deletions=int(entry.get("deletions") or 0),
                        has_untracked_changes=bool(entry.get("has_untracked_changes") or False),
                    )
                )
            summaries.sort(key=lambda item: item.created_at, reverse=True)
            return summaries

    def restore_checkpoint(self, checkpoint_id: str, *, prune: bool = True) -> None:
        cid = str(checkpoint_id or "").strip()
        if not cid:
            raise ValueError("checkpoint_id must be provided")
        with self._lock:
            self._ensure_repo()
            entries = self._load_meta()
            index = None
            commit_hash = None
            for i, entry in enumerate(entries):
                entry_id = str(entry.get("checkpoint_id") or entry.get("id") or "")
                if entry_id == cid:
                    index = i
                    commit_hash = str(entry.get("git_commit") or entry.get("commit") or "")
                    break
            if index is None or not commit_hash:
                raise ValueError(f"unknown checkpoint_id: {cid}")

            # Deterministic restore of workspace state.
            self._run_git(["reset", "--hard", commit_hash])

            if prune:
                entries = entries[: index + 1]
                self._write_meta(entries)

