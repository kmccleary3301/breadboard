"""Project TodoStore state into a TUI-friendly envelope.

The engine may emit TODO updates as part of tool_call/tool_result payloads. The
TUI expects a permissive, forward-compatible envelope (see
docs/contracts/cli_bridge/schemas/session_event_payload_todo_update.schema.json).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def project_store_snapshot_to_tui_envelope(
    snapshot: Dict[str, Any],
    *,
    scope_key: Optional[str] = None,
    scope_label: Optional[str] = None,
) -> Dict[str, Any]:
    todos = snapshot.get("todos") if isinstance(snapshot, dict) else None
    if not isinstance(todos, list):
        todos = []

    items: List[Dict[str, Any]] = []
    for todo in todos:
        if isinstance(todo, dict):
            items.append(dict(todo))

    journal = snapshot.get("journal") if isinstance(snapshot, dict) else None
    revision = len(journal) if isinstance(journal, list) else None

    envelope: Dict[str, Any] = {
        "op": "snapshot",
        "items": items,
        "revision": revision,
    }
    if scope_key:
        envelope["scope_key"] = scope_key
    if scope_label:
        envelope["scope_label"] = scope_label
    return envelope

