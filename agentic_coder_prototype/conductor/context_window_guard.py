from __future__ import annotations

from typing import Any, Dict, List, Optional


class ContextWindowGuard:
    """Heuristic context-window guard that emits warnings before models overflow."""

    def __init__(self, max_tokens: int = 32000, warn_ratio: float = 0.9) -> None:
        self.max_tokens = max_tokens
        self.warn_ratio = warn_ratio

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(len(str(part.get("text") or "")) for part in content if isinstance(part, dict))
        return total // 4  # crude heuristic

    def maybe_warn(self, session_state, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        tokens = self.estimate_tokens(messages)
        if tokens >= int(self.max_tokens * self.warn_ratio):
            payload = {
                "approx_tokens": tokens,
                "limit": self.max_tokens,
                "ratio": round(tokens / self.max_tokens, 2),
            }
            try:
                session_state.add_transcript_entry({"context_window": payload})
                session_state.record_guardrail_event("context_window_warning", payload)
            except Exception:
                pass
            return payload
        return None
