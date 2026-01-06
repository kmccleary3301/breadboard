"""Adapter interface for harness-specific bus surfaces."""

from __future__ import annotations

from typing import Any, Dict, Optional


class BusAdapter:
    """Translate internal events to harness-specific model-visible payloads."""

    def format_spawn_ack(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        keys = ("job_id", "agent_id", "owner_agent", "seq")
        return {key: payload.get(key) for key in keys if key in payload}

    def format_wakeup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        keys = ("job_id", "agent_id", "owner_agent", "seq", "reason", "message")
        return {key: payload.get(key) for key in keys if key in payload}

    def format_tool_result(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload

    def format_transcript_write(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload

    def format_guard_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload

    def build_mvi_message(self, topic: str, payload: Dict[str, Any]) -> Optional[Any]:
        """Return a model-visible message for injection, or None."""
        topic_l = str(topic or "").strip().lower()
        if topic_l == "wakeup":
            message = str((payload or {}).get("message") or "").strip()
            if not message:
                return None
            return {"role": "system", "content": message}
        return None
