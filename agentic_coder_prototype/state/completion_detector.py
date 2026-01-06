from __future__ import annotations

from typing import Any, Dict, Optional


class CompletionDetector:
    """Lightweight completion detector for agent runs.

    This implementation intentionally keeps heuristics simple; it can be
    expanded once recovery stabilizes.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        completion_sentinel: Optional[str] = None,
    ) -> None:
        cfg = config or {}
        completion_cfg = (cfg.get("completion", {}) or {}) if isinstance(cfg, dict) else {}
        try:
            threshold = float(completion_cfg.get("threshold", 0.6) or 0.6)
        except Exception:
            threshold = 0.6
        self.threshold = max(0.0, min(1.0, threshold))
        self.completion_sentinel = completion_sentinel or ">>>>>> END RESPONSE"

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
        if "task complete" in normalized:
            return {
                "completed": True,
                "method": "assistant_content",
                "reason": "explicit_completion_marker",
                "confidence": 0.9,
            }
        sentinel = str(self.completion_sentinel or "").strip()
        if sentinel and sentinel.lower() in normalized:
            return {
                "completed": True,
                "method": "assistant_content",
                "reason": "explicit_completion_marker",
                "confidence": 0.9,
            }

        finish_reason = str(choice_finish_reason or "").lower().strip()
        # Provider finish reasons differ (OpenAI: stop/length, Anthropic: end_turn/max_tokens).
        if finish_reason in {"stop", "end_turn", "length", "max_tokens"} and text.strip():
            return {
                "completed": True,
                "method": "finish_reason",
                "reason": f"finish_reason:{finish_reason}",
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
