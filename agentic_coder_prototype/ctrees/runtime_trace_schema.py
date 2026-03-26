from __future__ import annotations

from typing import Any, Dict


def build_runtime_trace_event(
    *,
    event_type: str,
    phase: str,
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "phase13_runtime_trace_event_v1",
        "event_type": str(event_type),
        "phase": str(phase),
        "payload": dict(payload or {}),
    }

