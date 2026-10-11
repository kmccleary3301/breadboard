"""Allocate session-scoped permission IDs across legacy and native request paths."""
from __future__ import annotations

import threading
from typing import Any


_ALLOCATION_LOCK = threading.Lock()


def allocate_permission_id(session_state: Any, state_key: str) -> str:
    # Shared across brokers so a session never has two independent counters.
    with _ALLOCATION_LOCK:
        state = session_state.get_provider_metadata(state_key, {})
        if not isinstance(state, dict):
            state = {}
        try:
            counter = int(state.get("counter") or 0) + 1
        except (TypeError, ValueError):
            counter = 1
        state["counter"] = counter
        session_state.set_provider_metadata(state_key, state)
        return f"permission_{counter}"
