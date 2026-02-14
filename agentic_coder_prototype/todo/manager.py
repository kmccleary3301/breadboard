from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .store import (
    Todo,
    TodoDraft,
    TodoEvent,
    TodoPatch,
    TodoStore,
    TODO_OPEN_STATUSES,
)


def _as_todo_drafts(payload: Any) -> List[TodoDraft]:
    drafts: List[TodoDraft] = []
    items = payload if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)) else [payload]
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("todo.create requires objects with at least a 'title'.")
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError("todo.create requires 'title'.")
        drafts.append(
            TodoDraft(
                title=title,
                priority=item.get("priority"),
                blocked_by=item.get("blocked_by"),
                metadata=item.get("metadata"),
                refs=item.get("refs"),
            )
        )
    return drafts


class TodoManager:
    """
    Coordinates TodoStore operations and emits structured payloads for tool responses/events.
    """

    def __init__(self, store: TodoStore, event_callback):
        self.store = store
        self.event_callback = event_callback

    def _emit_snapshot_event(self, reason: str) -> None:
        """
        Emit a single best-effort "snapshot" signal after mutating operations.

        SessionState.emit_todo_event() will project the current store snapshot into
        a `payload.todo` replace envelope for the TUI.
        """
        try:
            self.event_callback({"type": "todo.snapshot", "todo_id": "_all_", "payload": {"reason": reason}})
        except Exception:
            pass

    # ------------------------------------------------------------------ tool handlers
    def handle_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        drafts = _as_todo_drafts(payload.get("items") or payload.get("drafts") or payload)
        todos = self.store.create(drafts)
        self._emit_snapshot_event("todo.create")
        return {"todos": [todo.to_dict() for todo in todos]}

    def handle_update(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        todo_id = _require_id(payload)
        patch = TodoPatch(
            title=payload.get("title"),
            status=payload.get("status"),
            priority=payload.get("priority"),
            blocked_by=payload.get("blocked_by"),
            due_at=payload.get("due_at"),
            metadata=payload.get("metadata"),
            version=payload.get("version"),
        )
        todo = self.store.update(todo_id, patch)
        self._emit_snapshot_event("todo.update")
        return {"todo": todo.to_dict()}

    def handle_complete(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        todo_id = _require_id(payload)
        summary = payload.get("summary")
        todo = self.store.complete(todo_id, summary)
        self._emit_snapshot_event("todo.complete")
        return {"todo": todo.to_dict()}

    def handle_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        todo_id = _require_id(payload)
        reason = payload.get("reason")
        todo = self.store.cancel(todo_id, reason)
        self._emit_snapshot_event("todo.cancel")
        return {"todo": todo.to_dict()}

    def handle_reorder(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        order = payload.get("order")
        if not isinstance(order, Sequence):
            raise ValueError("todo.reorder requires 'order' as a list of todo ids.")
        order_ids = [str(item) for item in order]
        updated = self.store.reorder(order_ids)
        self._emit_snapshot_event("todo.reorder")
        return {"order": updated}

    def handle_attach(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        todo_id = _require_id(payload)
        refs = payload.get("refs")
        if not isinstance(refs, Sequence):
            raise ValueError("todo.attach requires 'refs' list.")
        todo = self.store.attach(todo_id, refs)
        self._emit_snapshot_event("todo.attach")
        return {"todo": todo.to_dict()}

    def handle_note(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        todo_id = _require_id(payload)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("todo.note requires 'text'.")
        author = str(payload.get("author") or "assistant")
        todo = self.store.note(todo_id, author, text)
        self._emit_snapshot_event("todo.note")
        return {"todo": todo.to_dict()}

    def handle_list(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        todos = self.store.list_items()
        status_filter = None
        if payload and isinstance(payload, dict):
            status_filter = payload.get("status") or payload.get("filter")
        if status_filter:
            status_filter = {str(s) for s in status_filter} if isinstance(status_filter, Sequence) and not isinstance(status_filter, str) else {str(status_filter)}
            todos = [todo for todo in todos if todo.status in status_filter]
        return {"todos": [todo.to_dict() for todo in todos]}

    def handle_write_board(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        todos_payload = payload.get("todos")
        if not isinstance(todos_payload, Sequence):
            raise ValueError("TodoWrite requires a 'todos' array.")

        existing_by_title = {
            self._normalize_title(todo.title): todo
            for todo in self.store.list_items()
        }
        new_order: List[str] = []

        for entry in todos_payload:
            if not isinstance(entry, dict):
                raise ValueError("TodoWrite entries must be objects with 'content' and 'status'.")
            title = str(entry.get("content") or entry.get("title") or "").strip()
            if not title:
                raise ValueError("TodoWrite entries must include 'content'.")
            key = self._normalize_title(title)
            status = self._map_status(entry.get("status"))
            metadata: Dict[str, Any] = {}
            active_form = entry.get("activeForm")
            if isinstance(active_form, str) and active_form.strip():
                metadata["active_form"] = active_form.strip()

            todo = existing_by_title.get(key)
            if todo is None:
                draft = TodoDraft(title=title, metadata=metadata or None)
                todo = self.store.create([draft])[0]
                if status and status != todo.status:
                    todo = self.store.update(todo.id, TodoPatch(status=status))
                existing_by_title[key] = todo
            else:
                patch_kwargs: Dict[str, Any] = {}
                if status:
                    patch_kwargs["status"] = status
                if metadata:
                    patch_kwargs["metadata"] = metadata
                if patch_kwargs:
                    todo = self.store.update(todo.id, TodoPatch(**patch_kwargs))
                    existing_by_title[key] = todo
            new_order.append(todo.id)

        self.store.reorder(new_order)
        self.store.prune_to_ids(new_order, reason="todo.write_board")
        self._emit_snapshot_event("todo.write_board")
        return {"todos": [todo.to_dict() for todo in self.store.list_items()]}

    # ------------------------------------------------------------------ helpers
    def has_open_items(self) -> bool:
        return self.store.has_open_items()

    def snapshot(self) -> Dict[str, Any]:
        return self.store.snapshot()

    def _emit_latest_events(self, expected: int) -> None:
        """
        Emit the last `expected` events from the journal via callback. The store keeps
        an append-only journal, so we can safely slice from the end.
        """
        events = self.store.recent_events(expected)
        for event in events:
            self.event_callback(event.to_dict())

    @staticmethod
    def _normalize_title(title: str) -> str:
        return (title or "").strip().lower()

    @staticmethod
    def _map_status(status: Optional[str]) -> str:
        lookup = {
            "pending": "todo",
            "todo": "todo",
            "in_progress": "in_progress",
            "progress": "in_progress",
            "active": "in_progress",
            "completed": "done",
            "complete": "done",
            "done": "done",
            "blocked": "blocked",
            "canceled": "canceled",
            "cancelled": "canceled",
        }
        if not status:
            return "todo"
        return lookup.get(str(status).strip().lower(), "todo")


def _require_id(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Todo operations require an object payload.")
    todo_id = str(payload.get("id") or "").strip()
    if not todo_id:
        raise ValueError("Todo operation requires 'id'.")
    return todo_id
