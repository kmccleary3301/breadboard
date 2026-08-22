from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class TelemetryLogger:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get("RAYCODE_TELEMETRY_PATH")
        self._fh = None
        if self.path:
            p = Path(self.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # append mode JSONL
            self._fh = p.open("a", encoding="utf-8")

    def log(self, payload: Dict[str, Any]) -> None:
        if not self._fh:
            return
        try:
            self._fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._fh.flush()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._fh:
                self._fh.close()
        except Exception:
            pass


