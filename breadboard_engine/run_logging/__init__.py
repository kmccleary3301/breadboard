from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, Optional

from ..security import redaction
from ..security.process_isolation import validate_workspace_credential_boundary
from ..security.workspace_files import WorkspaceFilesystem


class LoggerV2Manager:
    """Lightweight logging manager for recovery builds."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        logging_cfg = (cfg.get("logging", {}) or {}) if isinstance(cfg, dict) else {}
        self.enabled = bool(logging_cfg.get("enabled", True))
        self.root_dir = str(logging_cfg.get("root_dir") or "logging")
        self.run_dir: Optional[str] = None
        self.include_raw = bool(logging_cfg.get("include_raw", False))
        self.include_structured_requests = bool(
            logging_cfg.get("include_structured_requests", True)
        )
        self._filesystem: WorkspaceFilesystem | None = None
        self._run_logical: str | None = None
        self._write_lock = threading.RLock()

    def close(self) -> None:
        filesystem = self._filesystem
        self._filesystem = None
        self._run_logical = None
        if filesystem is not None:
            filesystem.close()

    def __del__(self) -> None:
        self.close()

    def start_run(self, session_id: str) -> str:
        self.close()
        if not self.enabled:
            self.run_dir = None
            return ""
        filesystem = WorkspaceFilesystem.open_anchored_root(
            self.root_dir,
            create=True,
        )
        try:
            validate_workspace_credential_boundary(filesystem.root)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            safe_id = session_id.replace(os.sep, "_") if session_id else "session"
            run_logical = f"{ts}_{safe_id}"
            filesystem.create_directory(run_logical, mode=0o700)
            self._filesystem = filesystem
            self._run_logical = run_logical
            self._bootstrap_run_tree()
            self.run_dir = filesystem.display_path(run_logical)
            return self.run_dir
        except BaseException:
            filesystem.close()
            self._filesystem = None
            self._run_logical = None
            self.run_dir = None
            raise

    def _bootstrap_run_tree(self) -> None:
        filesystem = self._filesystem
        run_logical = self._run_logical
        if filesystem is None or run_logical is None:
            return
        for rel_path in (
            "conversation",
            "raw/requests",
            "raw/responses",
            "prompts/per_turn",
            "prompts/catalogs",
            "meta/requests",
            "errors",
        ):
            filesystem.create_directory(f"{run_logical}/{rel_path}", mode=0o700)

    def _resolve(
        self,
        rel_path: str,
    ) -> tuple[WorkspaceFilesystem, str] | None:
        filesystem = self._filesystem
        run_logical = self._run_logical
        if filesystem is None or run_logical is None:
            return None
        relative = PurePosixPath(rel_path)
        logical = str(PurePosixPath(run_logical) / relative)
        parent = str(PurePosixPath(logical).parent)
        filesystem.create_directory(parent, mode=0o700)
        return filesystem, logical

    def write_json(self, rel_path: str, data: Any) -> str:
        try:
            target = self._resolve(rel_path)
            if target is None:
                return ""
            filesystem, logical = target
            scrubbed, _problems = redaction.scrub_structure(data)
            written = filesystem.write_text(
                logical,
                json.dumps(scrubbed, ensure_ascii=False, indent=2),
            )
            return written.path
        except Exception:
            return ""

    def write_text(self, rel_path: str, content: str) -> str:
        try:
            target = self._resolve(rel_path)
            if target is None:
                return ""
            filesystem, logical = target
            written = filesystem.write_text(
                logical,
                redaction.scrub_text(str(content or "")),
            )
            return written.path
        except Exception:
            return ""

    def append_text(self, rel_path: str, content: str) -> str:
        try:
            target = self._resolve(rel_path)
            if target is None:
                return ""
            filesystem, logical = target
            with self._write_lock:
                written = filesystem.append_text(
                    logical,
                    redaction.scrub_text(str(content or "")),
                )
            return written.path
        except Exception:
            return ""

    def append_jsonl(self, rel_path: str, data: Any) -> str:
        try:
            target = self._resolve(rel_path)
            if target is None:
                return ""
            filesystem, logical = target
            scrubbed, _problems = redaction.scrub_structure(data)
            payload = json.dumps(scrubbed, ensure_ascii=False) + "\n"
            with self._write_lock:
                written = filesystem.append_text(logical, payload)
            return written.path
        except Exception:
            return ""
