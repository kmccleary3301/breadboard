from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_now() -> str:
    return datetime.utcnow().strftime(ISO_FORMAT)


def _new_id() -> str:
    return f"todo_{uuid.uuid4().hex[:16]}"


TodoStatus = str  # alias for clarity

TODO_OPEN_STATUSES: Tuple[TodoStatus, ...] = ("todo", "in_progress", "blocked")


@dataclass
class TodoRef:
    kind: str
    value: str
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {"kind": self.kind, "value": self.value}
        if self.note:
            payload["note"] = self.note
        return payload

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "TodoRef":
        return TodoRef(kind=data.get("kind", ""), value=data.get("value", ""), note=data.get("note"))


@dataclass
class TodoNote:
    author: str
    text: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {"author": self.author, "text": self.text, "created_at": self.created_at}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "TodoNote":
        return TodoNote(
            author=data.get("author", "system"),
            text=data.get("text", ""),
            created_at=data.get("created_at") or _utc_now(),
        )


@dataclass
class Todo:
    id: str
    title: str
    status: TodoStatus = "todo"
    priority: Optional[str] = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    due_at: Optional[str] = None
    blocked_by: List[str] = field(default_factory=list)
    refs: List[TodoRef] = field(default_factory=list)
    notes: List[TodoNote] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "due_at": self.due_at,
            "blocked_by": list(self.blocked_by),
            "refs": [ref.to_dict() for ref in self.refs],
            "notes": [note.to_dict() for note in self.notes],
            "metadata": dict(self.metadata),
            "version": self.version,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Todo":
        return Todo(
            id=data["id"],
            title=data.get("title", ""),
            status=data.get("status", "todo"),
            priority=data.get("priority"),
            created_at=data.get("created_at") or _utc_now(),
            updated_at=data.get("updated_at") or _utc_now(),
            due_at=data.get("due_at"),
            blocked_by=list(data.get("blocked_by") or []),
            refs=[TodoRef.from_dict(ref) for ref in data.get("refs") or []],
            notes=[TodoNote.from_dict(note) for note in data.get("notes") or []],
            metadata=dict(data.get("metadata") or {}),
            version=int(data.get("version") or 1),
        )


@dataclass
class TodoDraft:
    title: str
    priority: Optional[str] = None
    blocked_by: Optional[Sequence[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    refs: Optional[Sequence[Dict[str, Any]]] = None


@dataclass
class TodoPatch:
    title: Optional[str] = None
    status: Optional[TodoStatus] = None
    priority: Optional[str] = None
    blocked_by: Optional[Sequence[str]] = None
    due_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    version: Optional[int] = None


@dataclass
class TodoEvent:
    type: str
    todo_id: str
    payload: Dict[str, Any]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "todo_id": self.todo_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


class TodoStore:
    """
    In-memory and on-disk store for todos and their event journal.

    Thread safety is handled by the caller (session loop is single threaded per run).
    """

    FILENAME = ".breadboard/todos.json"

    def __init__(self, workspace: str, *, load_existing: bool = True):
        self.workspace = Path(workspace)
        self._todos: Dict[str, Todo] = {}
        self._order: List[str] = []
        self._journal: List[TodoEvent] = []
        if load_existing:
            self._load_from_disk()

    # ------------------------------------------------------------------ public API
    def snapshot(self) -> Dict[str, Any]:
        return {
            "todos": [todo.to_dict() for todo in self._ordered_todos()],
            "order": list(self._order),
            "journal": [event.to_dict() for event in self._journal],
        }

    def list_items(self) -> List[Todo]:
        return list(self._ordered_todos())

    def get(self, todo_id: str) -> Optional[Todo]:
        return self._todos.get(todo_id)

    def create(self, drafts: Sequence[TodoDraft]) -> List[Todo]:
        created: List[Todo] = []
        for draft in drafts:
            todo_id = _new_id()
            todo = Todo(
                id=todo_id,
                title=draft.title,
                priority=draft.priority,
                blocked_by=list(draft.blocked_by or []),
                metadata=dict(draft.metadata or {}),
                refs=[TodoRef.from_dict(ref) for ref in (draft.refs or [])],
            )
            self._todos[todo_id] = todo
            self._order.append(todo_id)
            created.append(todo)
            self._append_event("todo.created", todo_id, {"todo": todo.to_dict()})
        self._persist()
        return created

    def update(self, todo_id: str, patch: TodoPatch) -> Todo:
        todo = self._require(todo_id)
        if patch.version is not None and patch.version != todo.version:
            raise ValueError(f"Version mismatch for {todo_id}: expected {todo.version}, got {patch.version}")

        changed_fields: Dict[str, Any] = {}
        if patch.title is not None and patch.title != todo.title:
            todo.title = patch.title
            changed_fields["title"] = todo.title
        if patch.status is not None and patch.status != todo.status:
            todo.status = patch.status
            changed_fields["status"] = todo.status
        if patch.priority is not None and patch.priority != todo.priority:
            todo.priority = patch.priority
            changed_fields["priority"] = todo.priority
        if patch.blocked_by is not None:
            todo.blocked_by = list(patch.blocked_by)
            changed_fields["blocked_by"] = list(todo.blocked_by)
        if patch.due_at is not None:
            todo.due_at = patch.due_at
            changed_fields["due_at"] = todo.due_at
        if patch.metadata is not None:
            todo.metadata.update(patch.metadata)
            changed_fields["metadata"] = dict(todo.metadata)

        if not changed_fields:
            return todo

        todo.updated_at = _utc_now()
        todo.version += 1
        changed_fields["updated_at"] = todo.updated_at
        changed_fields["version"] = todo.version
        self._append_event("todo.updated", todo_id, changed_fields)
        self._persist()
        return todo

    def complete(self, todo_id: str, summary: Optional[str] = None) -> Todo:
        todo = self.update(todo_id, TodoPatch(status="done"))
        payload = {"status": "done"}
        if summary:
            payload["summary"] = summary
        self._append_event("todo.completed", todo_id, payload)
        self._persist()
        return todo

    def cancel(self, todo_id: str, reason: Optional[str] = None) -> Todo:
        todo = self.update(todo_id, TodoPatch(status="canceled"))
        payload = {"status": "canceled"}
        if reason:
            payload["reason"] = reason
        self._append_event("todo.canceled", todo_id, payload)
        self._persist()
        return todo

    def reorder(self, order: Sequence[str]) -> List[str]:
        missing = [todo_id for todo_id in order if todo_id not in self._todos]
        if missing:
            raise ValueError(f"Unknown todo ids in reorder: {missing}")
        self._order = list(order)
        self._append_event("todo.reordered", "_all_", {"order": list(self._order)})
        self._persist()
        return self._order

    def attach(self, todo_id: str, refs: Sequence[Dict[str, Any]]) -> Todo:
        todo = self._require(todo_id)
        todo.refs.extend([TodoRef.from_dict(ref) for ref in refs])
        todo.updated_at = _utc_now()
        todo.version += 1
        self._append_event(
            "todo.attached",
            todo_id,
            {"refs": [ref.to_dict() for ref in refs], "updated_at": todo.updated_at, "version": todo.version},
        )
        self._persist()
        return todo

    def note(self, todo_id: str, author: str, text: str) -> Todo:
        todo = self._require(todo_id)
        note = TodoNote(author=author, text=text, created_at=_utc_now())
        todo.notes.append(note)
        todo.updated_at = note.created_at
        todo.version += 1
        self._append_event(
            "todo.noted",
            todo_id,
            {"note": note.to_dict(), "updated_at": todo.updated_at, "version": todo.version},
        )
        self._persist()
        return todo

    def has_open_items(self) -> bool:
        return any(todo.status in TODO_OPEN_STATUSES for todo in self._todos.values())

    # ------------------------------------------------------------------ helpers
    def _require(self, todo_id: str) -> Todo:
        todo = self._todos.get(todo_id)
        if not todo:
            raise ValueError(f"Unknown todo id: {todo_id}")
        return todo

    def _ordered_todos(self) -> Iterable[Todo]:
        for todo_id in self._order:
            todo = self._todos.get(todo_id)
            if todo:
                yield todo
        # append any stray todos not in order (should not happen but keep deterministic)
        for todo_id, todo in self._todos.items():
            if todo_id not in self._order:
                yield todo

    def _append_event(self, event_type: str, todo_id: str, payload: Dict[str, Any]) -> None:
        self._journal.append(TodoEvent(event_type, todo_id, payload, _utc_now()))
        # limit journal growth? keep full for now

    def _persist(self) -> None:
        blob = {
            "version": 1,
            "todos": [todo.to_dict() for todo in self._todos.values()],
            "order": list(self._order),
            "journal": [event.to_dict() for event in self._journal],
        }
        path = self.workspace / self.FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _load_from_disk(self) -> None:
        path = self.workspace / self.FILENAME
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        todos = data.get("todos") or []
        self._todos = {}
        for item in todos:
            try:
                todo = Todo.from_dict(item)
                self._todos[todo.id] = todo
            except Exception:
                continue
        order = data.get("order") or []
        # ensure only known ids
        self._order = [todo_id for todo_id in order if todo_id in self._todos]
        # append any missing
        for todo_id in self._todos:
            if todo_id not in self._order:
                self._order.append(todo_id)
        journal_raw = data.get("journal") or []
        self._journal = []
        for event in journal_raw:
            try:
                self._journal.append(
                    TodoEvent(
                        type=event.get("type", ""),
                        todo_id=event.get("todo_id", ""),
                        payload=event.get("payload") or {},
                        timestamp=event.get("timestamp") or _utc_now(),
                    )
                )
            except Exception:
                continue

    def recent_events(self, count: int) -> List[TodoEvent]:
        if count <= 0:
            return []
        return list(self._journal[-count:])

    def prune_to_ids(self, keep_ids: Sequence[str], reason: str = "todo.write_board") -> None:
        keep = {str(todo_id) for todo_id in keep_ids}
        removed = [todo_id for todo_id in list(self._todos.keys()) if todo_id not in keep]
        if not removed:
            return
        for todo_id in removed:
            self._todos.pop(todo_id, None)
            if todo_id in self._order:
                self._order.remove(todo_id)
            self._append_event("todo.removed", todo_id, {"reason": reason})
        self._persist()
