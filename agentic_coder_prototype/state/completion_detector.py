from __future__ import annotations

from typing import Any, Dict, Optional


class CompletionDetector:
    """Lightweight completion detector for agent runs.

    This implementation intentionally keeps heuristics simple; it can be
    expanded once recovery stabilizes.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        completion_cfg = (cfg.get("completion", {}) or {}) if isinstance(cfg, dict) else {}
        try:
            threshold = float(completion_cfg.get("threshold", 0.6) or 0.6)
        except Exception:
            threshold = 0.6
        self.threshold = max(0.0, min(1.0, threshold))

    def detect_completion(
        self,
        *,
        msg_content: str,
        choice_finish_reason: Optional[str],
        tool_results: Optional[list],
        agent_config: Dict[str, Any],
        recent_tool_activity: Any = None,
        assistant_history: Optional[list] = None,
        mark_tool_available: Optional[bool] = None,
    ) -> Dict[str, Any]:
        text = str(msg_content or "")
        normalized = text.lower()
        if "task complete" in normalized or ">>>>>> end response" in normalized:
            return {
                "completed": True,
                "method": "assistant_content",
                "reason": "explicit_completion_marker",
                "confidence": 0.9,
            }

        if choice_finish_reason in {"stop", "length"} and text.strip():
            return {
                "completed": True,
                "method": "finish_reason",
                "reason": f"finish_reason:{choice_finish_reason}",
                "confidence": 0.65,
            }

        return {
            "completed": False,
            "method": "none",
            "reason": "no_completion_signal",
            "confidence": 0.0,
        }

    def meets_threshold(self, analysis: Dict[str, Any]) -> bool:
        try:
            confidence = float(analysis.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        return confidence >= self.threshold
