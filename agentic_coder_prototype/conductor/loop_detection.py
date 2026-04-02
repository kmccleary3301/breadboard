from __future__ import annotations

from typing import Any, Dict, Optional


class LoopDetectionService:
    """Simple loop detector modeled after Gemini CLI heuristics."""

    def __init__(self, *, tool_threshold: int = 5, content_threshold: int = 10) -> None:
        self.tool_threshold = max(tool_threshold, 1)
        self.content_threshold = max(content_threshold, 1)
        self.tool_calls_in_turn = 0
        self.content_messages_in_turn = 0
        self.consecutive_repeat_messages = 0
        self._last_content_hash: Optional[str] = None

    def turn_started(self) -> None:
        self.tool_calls_in_turn = 0
        self.content_messages_in_turn = 0
        self.consecutive_repeat_messages = 0
        self._last_content_hash = None

    def observe_tool_call(self) -> None:
        self.tool_calls_in_turn += 1

    def observe_content(self, message: Dict[str, Any]) -> None:
        self.content_messages_in_turn += 1
        content = str(message.get("content") or "")
        digest = hash(content.strip())
        if digest == self._last_content_hash:
            self.consecutive_repeat_messages += 1
        else:
            self.consecutive_repeat_messages = 0
        self._last_content_hash = digest

    def check(self) -> Optional[str]:
        if self.tool_calls_in_turn >= self.tool_threshold:
            return "Repeated tool calls without progress"
        if self.content_messages_in_turn >= self.content_threshold:
            return "Repeated assistant content without tool usage"
        if self.consecutive_repeat_messages >= 3:
            return "Assistant repeated identical content multiple times"
        return None
