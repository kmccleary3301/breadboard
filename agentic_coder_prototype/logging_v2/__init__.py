from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional


class LoggerV2Manager:
    """Lightweight logging manager for recovery builds."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        logging_cfg = (cfg.get("logging", {}) or {}) if isinstance(cfg, dict) else {}
        self.enabled = bool(logging_cfg.get("enabled", True))
        self.root_dir = str(logging_cfg.get("root_dir") or "logging")
        self.run_dir: Optional[str] = None
        self.include_raw = bool(logging_cfg.get("include_raw", False))
        self.include_structured_requests = bool(logging_cfg.get("include_structured_requests", True))

    def start_run(self, session_id: str) -> str:
        if not self.enabled:
            self.run_dir = None
            return ""
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        safe_id = session_id.replace(os.sep, "_") if session_id else "session"
        run_path = Path(self.root_dir) / f"{ts}_{safe_id}"
        run_path.mkdir(parents=True, exist_ok=True)
        self.run_dir = str(run_path)
        return self.run_dir

    def _resolve(self, rel_path: str) -> Optional[Path]:
        if not self.run_dir:
            return None
        path = Path(self.run_dir) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, rel_path: str, data: Any) -> str:
        path = self._resolve(rel_path)
        if not path:
            return ""
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return ""
        return str(path)

    def write_text(self, rel_path: str, content: str) -> str:
        path = self._resolve(rel_path)
        if not path:
            return ""
        try:
            path.write_text(str(content or ""), encoding="utf-8")
        except Exception:
            return ""
        return str(path)

    def append_text(self, rel_path: str, content: str) -> str:
        path = self._resolve(rel_path)
        if not path:
            return ""
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(str(content or ""))
        except Exception:
            return ""
        return str(path)
