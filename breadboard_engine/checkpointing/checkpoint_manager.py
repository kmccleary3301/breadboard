from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from ..security import (
    WorkspaceFilesystem,
    WorkspacePathError,
    build_child_environment,
    build_restricted_process_command,
    provider_credential_values,
    redaction,
)


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


def build_checkpoint_metadata_record(
    summary: CheckpointSummary,
    *,
    source_kind: str = "workspace_checkpoint",
) -> Dict[str, Any]:
    """
    Build the first shared checkpoint metadata record.

    This intentionally captures only the portable checkpoint summary surface,
    not the full Python-backed git/checkpoint implementation details.
    """

    return {
        "schema_version": "bb.checkpoint_metadata.v1",
        "source_kind": str(source_kind),
        "checkpoint_ref": str(summary.checkpoint_id),
        "created_at": int(summary.created_at),
        "summary": summary.as_payload(),
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

    _ROOT_LOGICAL = ".breadboard/checkpoints"
    _GIT_LOGICAL = ".breadboard/checkpoints/git"
    _GIT_HEAD_LOGICAL = ".breadboard/checkpoints/git/HEAD"
    _META_LOGICAL = ".breadboard/checkpoints/checkpoints.json"

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace_dir = Path(workspace_dir).expanduser().absolute()
        self._git_dir = self._workspace_dir / self._GIT_LOGICAL
        self._workspace_files: WorkspaceFilesystem | None = None
        self._lock = threading.Lock()

    def _ensure_workspace(self) -> WorkspaceFilesystem:
        if self._workspace_files is not None:
            return self._workspace_files
        try:
            self._workspace_files = WorkspaceFilesystem.open_anchored_root(
                self._workspace_dir,
                create=False,
            )
        except WorkspacePathError as exc:
            raise RuntimeError(
                f"workspace directory missing: {self._workspace_dir}"
            ) from exc
        return self._workspace_files

    def _ensure_state_root(self) -> WorkspaceFilesystem:
        filesystem = self._ensure_workspace()
        filesystem.create_directory(self._ROOT_LOGICAL)
        return filesystem

    def _run_git(self, args: List[str]) -> str:
        cmd = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            f"--git-dir={self._git_dir}",
            f"--work-tree={self._workspace_dir}",
            *args,
        ]
        child_environment = build_child_environment()
        isolated_command, child_environment = build_restricted_process_command(
            cmd,
            workspace=self._workspace_dir,
            working_directory=self._workspace_dir,
            shell=False,
            environment=child_environment,
        )
        with redaction.secret_value_scope(*provider_credential_values()):
            proc = subprocess.run(
                isolated_command,
                check=False,
                capture_output=True,
                text=True,
                cwd=self._workspace_dir,
                env=child_environment,
                shell=False,
            )
            stdout = redaction.scrub_text(proc.stdout or "")
            stderr = redaction.scrub_text(proc.stderr or "")
        if proc.returncode != 0:
            details = stderr.strip() or stdout.strip() or f"exit={proc.returncode}"
            raise RuntimeError(
                f"checkpoint git command failed: {' '.join(args)} ({details})"
            )
        return stdout.strip()

    def _ensure_repo(self) -> None:
        filesystem = self._ensure_state_root()
        if not filesystem.exists(self._GIT_HEAD_LOGICAL):
            filesystem.create_directory(self._GIT_LOGICAL)
            self._run_git(["init", "--quiet"])
            self._run_git(["config", "user.name", "Breadboard Checkpoints"])
            self._run_git(["config", "user.email", "breadboard@local"])
        self._configure_excludes()

    def _configure_excludes(self) -> None:
        filesystem = self._ensure_state_root()
        filesystem.create_directory(f"{self._GIT_LOGICAL}/info")
        exclude_logical = f"{self._GIT_LOGICAL}/info/exclude"
        patterns = [
            ".breadboard/",
            ".git/",
        ]
        existing = ""
        try:
            existing = filesystem.read_text(
                exclude_logical,
                encoding="utf-8",
                errors="strict",
            )
        except FileNotFoundError:
            existing = ""
        merged = set(line.strip() for line in existing.splitlines() if line.strip())
        merged.update(patterns)
        filesystem.write_text(
            exclude_logical,
            "\n".join(sorted(merged)) + "\n",
            encoding="utf-8",
        )

    def _load_meta(self) -> List[Dict[str, Any]]:
        filesystem = self._ensure_state_root()
        try:
            raw = filesystem.read_text(
                self._META_LOGICAL,
                encoding="utf-8",
                errors="strict",
            )
        except FileNotFoundError:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return list(data) if isinstance(data, list) else []

    def _write_meta(self, entries: List[Dict[str, Any]]) -> None:
        filesystem = self._ensure_state_root()
        filesystem.write_text(
            self._META_LOGICAL,
            json.dumps(entries, indent=2, sort_keys=False),
            encoding="utf-8",
        )

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

    def create_checkpoint(
        self, preview: str, *, snapshot: Optional[Dict[str, Any]] = None
    ) -> CheckpointSummary:
        preview_text = str(preview or "").strip() or "Checkpoint"
        with self._lock:
            self._ensure_repo()
            entries = self._load_meta()
            checkpoint_id = self._next_checkpoint_id(entries)
            created_at = int(time.time())

            # Stage the full workspace (excluding ignored paths) and commit.
            self._run_git(["add", "-A"])
            self._run_git(
                [
                    "commit",
                    "--allow-empty",
                    "-m",
                    f"{checkpoint_id}: {preview_text}",
                    "--quiet",
                ]
            )
            commit_hash = self._run_git(["rev-parse", "HEAD"]).strip()

            tracked_files = 0
            additions = 0
            deletions = 0
            parents = self._run_git(
                ["rev-list", "--parents", "-n", "1", "HEAD"]
            ).split()
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
            if isinstance(snapshot, dict):
                snapshot_path = self._write_snapshot(checkpoint_id, snapshot)
                if snapshot_path:
                    entry["snapshot_path"] = snapshot_path
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
                        checkpoint_id=str(
                            entry.get("checkpoint_id") or entry.get("id") or ""
                        ),
                        created_at=int(
                            entry.get("created_at") or entry.get("timestamp") or 0
                        ),
                        preview=str(entry.get("preview") or ""),
                        tracked_files=int(entry.get("tracked_files") or 0),
                        additions=int(entry.get("additions") or 0),
                        deletions=int(entry.get("deletions") or 0),
                        has_untracked_changes=bool(
                            entry.get("has_untracked_changes") or False
                        ),
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
                    commit_hash = str(
                        entry.get("git_commit") or entry.get("commit") or ""
                    )
                    break
            if index is None or not commit_hash:
                raise ValueError(f"unknown checkpoint_id: {cid}")

            # Deterministic restore of workspace state.
            self._run_git(["reset", "--hard", commit_hash])

            if prune:
                entries = entries[: index + 1]
                self._write_meta(entries)

    def _write_snapshot(
        self, checkpoint_id: str, snapshot: Dict[str, Any]
    ) -> Optional[str]:
        try:
            filesystem = self._ensure_state_root()
            logical = f"{self._ROOT_LOGICAL}/snapshots/{checkpoint_id}.json"
            filesystem.create_directory(f"{self._ROOT_LOGICAL}/snapshots")
            filesystem.write_text(
                logical,
                json.dumps(snapshot, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return logical
        except WorkspacePathError:
            raise
        except Exception:
            return None

    def load_snapshot(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        cid = str(checkpoint_id or "").strip()
        if not cid:
            return None
        with self._lock:
            entries = self._load_meta()
            snapshot_path = None
            for entry in entries:
                entry_id = str(entry.get("checkpoint_id") or entry.get("id") or "")
                if entry_id == cid:
                    snapshot_path = entry.get("snapshot_path") or entry.get("snapshot")
                    break
        if not snapshot_path:
            return None
        try:
            filesystem = self._ensure_workspace()
            return json.loads(
                filesystem.read_text(
                    str(snapshot_path),
                    encoding="utf-8",
                    errors="strict",
                )
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return None
