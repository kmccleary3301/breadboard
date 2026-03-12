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
            threshold = float(
                completion_cfg.get("threshold", cfg.get("confidence_threshold", 0.6)) or 0.6
            )
        except Exception:
            threshold = 0.6
        self.threshold = max(0.0, min(1.0, threshold))
        self.completion_sentinel = completion_sentinel or ">>>>>> END RESPONSE"
        self.enable_text_sentinels = bool(completion_cfg.get("enable_text_sentinels", True))
        self.enable_provider_signals = bool(completion_cfg.get("enable_provider_signals", True))
        configured_sentinels = completion_cfg.get("text_sentinels") or []
        self.text_sentinels = [str(item) for item in configured_sentinels if str(item).strip()]
        if self.completion_sentinel not in self.text_sentinels:
            self.text_sentinels.append(self.completion_sentinel)

    @staticmethod
    def _completion_result(
        *,
        completed: bool,
        method: str,
        reason: str,
        confidence: float,
        signal_source_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = {
            "completed": completed,
            "method": method,
            "reason": reason,
            "confidence": confidence,
        }
        if completed:
            result["signal_code"] = "complete"
            result["signal_source_kind"] = signal_source_kind or "assistant_content"
        return result

    def detect_completion(
        self,
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
        if tool_results:
            for result in tool_results:
                if isinstance(result, dict) and result.get("action") == "complete":
                    return self._completion_result(
                        completed=True,
                        method="tool_based",
                        reason="mark_task_complete",
                        confidence=1.0,
                        signal_source_kind="tool_call",
                    )

        if self.enable_text_sentinels:
            if "task complete" in normalized:
                return self._completion_result(
                    completed=True,
                    method="assistant_content",
                    reason="explicit_completion_marker",
                    confidence=0.9,
                    signal_source_kind="text_sentinel",
                )
            for sentinel in self.text_sentinels:
                cleaned = str(sentinel or "").strip()
                if cleaned and cleaned.lower() in normalized:
                    return self._completion_result(
                        completed=True,
                        method="assistant_content",
                        reason="explicit_completion_marker",
                        confidence=0.9,
                        signal_source_kind="text_sentinel",
                    )

        finish_reason = str(choice_finish_reason or "").lower().strip()
        # Provider finish reasons differ (OpenAI: stop/length, Anthropic: end_turn/max_tokens).
        if self.enable_provider_signals and finish_reason in {"stop", "end_turn", "length", "max_tokens"} and text.strip():
            recent_tools = recent_tool_activity if isinstance(recent_tool_activity, dict) else {}
            tool_entries = recent_tools.get("tools") if isinstance(recent_tools, dict) else []
            read_only_plan = bool(tool_entries) and all(
                isinstance(entry, dict) and bool(entry.get("read_only", False))
                for entry in tool_entries
            )
            if read_only_plan and any(
                marker in normalized for marker in ("ready to close", "ready to wrap", "summary:", "highlighted")
            ):
                return self._completion_result(
                    completed=True,
                    method="plan_satisfied",
                    reason="plan_satisfied_after_read_only_work",
                    confidence=0.9,
                    signal_source_kind="assistant_content",
                )
            if isinstance(assistant_history, list):
                normalized_history = [str(item or "").strip().lower() for item in assistant_history if str(item or "").strip()]
                current = text.strip().lower()
                if current and current in normalized_history:
                    return self._completion_result(
                        completed=True,
                        method="idle_loop",
                        reason="repeated_assistant_output",
                        confidence=0.8,
                        signal_source_kind="assistant_content",
                    )
            return self._completion_result(
                completed=True,
                method="finish_reason",
                reason=f"finish_reason:{finish_reason}",
                confidence=0.65,
                signal_source_kind="provider_finish",
            )

        return self._completion_result(
            completed=False,
            method="none",
            reason="no_completion_signal",
            confidence=0.0,
        )

    def meets_threshold(self, analysis: Dict[str, Any]) -> bool:
        try:
            confidence = float(analysis.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        return confidence >= self.threshold
