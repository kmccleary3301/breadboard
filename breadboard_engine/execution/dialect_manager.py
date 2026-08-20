from __future__ import annotations

from typing import Any, Dict, List
import fnmatch


class DialectManager:
    """Minimal dialect selection manager.

    This version focuses on stable ordering and simple model-based overrides.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}

    def get_dialects_for_model(self, model_id: str, available: List[str]) -> List[str]:
        """Return ordered dialect names for the given model.

        Honors tools.dialects.selection.by_model if present; otherwise returns
        the provided list unchanged.
        """
        try:
            tools_cfg = (self.config.get("tools", {}) or {})
            dialects_cfg = (tools_cfg.get("dialects", {}) or {})
            selection_cfg = (dialects_cfg.get("selection", {}) or {})
            by_model = selection_cfg.get("by_model", {}) or {}
        except Exception:
            return list(available)

        if not model_id or not by_model:
            return list(available)

        ordered: List[str] = []
        seen = set()
        for pattern, names in by_model.items():
            try:
                if not fnmatch.fnmatch(model_id, str(pattern)):
                    continue
            except Exception:
                continue
            for name in names or []:
                name = str(name)
                if name in available and name not in seen:
                    ordered.append(name)
                    seen.add(name)

        if not ordered:
            return list(available)

        remaining = [d for d in available if d not in seen]
        return ordered + remaining
