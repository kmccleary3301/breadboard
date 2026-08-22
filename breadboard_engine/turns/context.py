from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TurnContext:
    """Lightweight turn metadata shared between guard orchestration helpers."""

    index: Optional[int]
    mode: Optional[str]
    replay_mode: bool
    parsed_calls: List[Any] = field(default_factory=list)
    blocked_calls: List[Dict[str, Any]] = field(default_factory=list)
    plan_metadata: Dict[str, Any] = field(default_factory=dict)
    recent_tools_summary: List[Dict[str, Any]] = field(default_factory=list)
    test_success: Optional[float] = None
    loop_payload: Optional[Dict[str, Any]] = None
    context_warning: Optional[Dict[str, Any]] = None
    reward_metrics: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_session(cls, session_state, mode: Optional[str], parsed_calls: List[Any]) -> TurnContext:
        turn_index = session_state.get_provider_metadata("current_turn_index")
        replay_mode = bool(session_state.get_provider_metadata("replay_mode"))
        normalized_index = turn_index if isinstance(turn_index, int) else None
        ctx = cls(
            index=normalized_index,
            mode=mode,
            replay_mode=replay_mode,
            parsed_calls=list(parsed_calls),
        )
        loop_payload = session_state.get_provider_metadata("loop_detection_payload")
        if loop_payload:
            ctx.loop_payload = loop_payload
            session_state.set_provider_metadata("loop_detection_payload", None)
        context_payload = session_state.get_provider_metadata("context_window_warning")
        if context_payload:
            ctx.context_warning = context_payload
            session_state.set_provider_metadata("context_window_warning", None)
        return ctx

    def record_block(self, call: Any, reason: str, source: str) -> None:
        self.blocked_calls.append(
            {
                "call": call,
                "reason": reason,
                "source": source,
            }
        )

    def snapshot(self) -> Dict[str, Any]:
        blocked = []
        for entry in self.blocked_calls:
            call = entry.get("call")
            blocked.append(
                {
                    "function": getattr(call, "function", None),
                    "reason": entry.get("reason"),
                    "source": entry.get("source"),
                }
            )
        return {
            "index": self.index,
            "mode": self.mode,
            "replay_mode": self.replay_mode,
            "blocked_calls": blocked,
            "plan_metadata": self.plan_metadata or {},
            "recent_tools": list(self.recent_tools_summary),
            "test_success": self.test_success,
            "loop_detection": self.loop_payload,
            "context_warning": self.context_warning,
            "reward_metrics": dict(self.reward_metrics),
        }
