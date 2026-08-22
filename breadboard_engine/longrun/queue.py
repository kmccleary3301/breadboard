from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol

from ..todo.store import TODO_OPEN_STATUSES, TodoPatch, TodoStore


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


class WorkQueue(Protocol):
    def peek_next(self, state: Mapping[str, Any] | None = None) -> Optional[Dict[str, Any]]:
        ...

    def claim(self, item_id: str, episode_id: int) -> Dict[str, Any]:
        ...

    def complete(self, item_id: str, outcome: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        ...

    def defer(self, item_id: str, reason: str) -> Dict[str, Any]:
        ...

    def snapshot(self) -> Dict[str, Any]:
        ...


def _item_payload(item_id: str, title: str, status: str, metadata: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "id": item_id,
        "title": title,
        "status": status,
        "metadata": dict(metadata or {}),
    }


class TodoStoreQueue:
    def __init__(self, *, workspace: str | None = None, store: TodoStore | None = None) -> None:
        if store is not None:
            self._store = store
        elif workspace is not None:
            self._store = TodoStore(workspace)
        else:
            raise ValueError("TodoStoreQueue requires either workspace or store")

    def peek_next(self, state: Mapping[str, Any] | None = None) -> Optional[Dict[str, Any]]:
        todos = list(self._store.list_items())
        if not todos:
            return None
        # Prefer actively actionable todos first.
        for preferred in ("todo", "in_progress", "blocked"):
            for todo in todos:
                if todo.status == preferred and todo.status in TODO_OPEN_STATUSES:
                    return _item_payload(todo.id, todo.title, todo.status, todo.metadata)
        return None

    def claim(self, item_id: str, episode_id: int) -> Dict[str, Any]:
        todo = self._store.get(item_id)
        if todo is None:
            return self.snapshot()
        metadata = dict(todo.metadata or {})
        metadata.update(
            {
                "longrun_last_claim_episode": int(episode_id),
                "longrun_last_claimed_at": _utc_now(),
            }
        )
        patch = TodoPatch(metadata=metadata)
        if todo.status == "todo":
            patch.status = "in_progress"
        self._store.update(item_id, patch)
        return self.snapshot()

    def complete(self, item_id: str, outcome: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        summary = None
        if isinstance(outcome, Mapping):
            summary = outcome.get("summary") or outcome.get("completion_reason")
            if summary is not None:
                summary = str(summary)
        self._store.complete(item_id, summary=summary)
        return self.snapshot()

    def defer(self, item_id: str, reason: str) -> Dict[str, Any]:
        todo = self._store.get(item_id)
        if todo is None:
            return self.snapshot()
        metadata = dict(todo.metadata or {})
        metadata.update(
            {
                "longrun_deferred_at": _utc_now(),
                "longrun_defer_reason": str(reason),
            }
        )
        self._store.update(item_id, TodoPatch(status="blocked", metadata=metadata))
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        items = [
            _item_payload(todo.id, todo.title, todo.status, todo.metadata)
            for todo in self._store.list_items()
        ]
        open_count = sum(1 for item in items if item["status"] in TODO_OPEN_STATUSES)
        return {
            "backend": "todo_store",
            "open_count": open_count,
            "items": items,
        }


class FeatureFileQueue:
    """
    File-backed queue backend for long-running feature/task execution.

    Format:
    {
      "version": 1,
      "items": [
        {"id":"feat_1","title":"...", "status":"todo|in_progress|blocked|done|canceled", "metadata": {...}}
      ]
    }
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write({"version": 1, "items": []})

    def peek_next(self, state: Mapping[str, Any] | None = None) -> Optional[Dict[str, Any]]:
        payload = self._read()
        items = payload.get("items", [])
        for preferred in ("todo", "in_progress", "blocked"):
            for item in items:
                if str(item.get("status")) == preferred and preferred in TODO_OPEN_STATUSES:
                    return _item_payload(
                        str(item.get("id", "")),
                        str(item.get("title", "")),
                        str(item.get("status", "todo")),
                        item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {},
                    )
        return None

    def claim(self, item_id: str, episode_id: int) -> Dict[str, Any]:
        payload = self._read()
        for item in payload.get("items", []):
            if str(item.get("id")) != item_id:
                continue
            if str(item.get("status")) == "todo":
                item["status"] = "in_progress"
            metadata = item.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            metadata["longrun_last_claim_episode"] = int(episode_id)
            metadata["longrun_last_claimed_at"] = _utc_now()
            item["metadata"] = metadata
            break
        self._write(payload)
        return self.snapshot()

    def complete(self, item_id: str, outcome: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        payload = self._read()
        for item in payload.get("items", []):
            if str(item.get("id")) != item_id:
                continue
            item["status"] = "done"
            metadata = item.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            metadata["longrun_completed_at"] = _utc_now()
            if isinstance(outcome, Mapping):
                completion_reason = outcome.get("completion_reason")
                if completion_reason is not None:
                    metadata["longrun_completion_reason"] = str(completion_reason)
            item["metadata"] = metadata
            break
        self._write(payload)
        return self.snapshot()

    def defer(self, item_id: str, reason: str) -> Dict[str, Any]:
        payload = self._read()
        for item in payload.get("items", []):
            if str(item.get("id")) != item_id:
                continue
            item["status"] = "blocked"
            metadata = item.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            metadata["longrun_deferred_at"] = _utc_now()
            metadata["longrun_defer_reason"] = str(reason)
            item["metadata"] = metadata
            break
        self._write(payload)
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        payload = self._read()
        out_items: List[Dict[str, Any]] = []
        for item in payload.get("items", []):
            out_items.append(
                _item_payload(
                    str(item.get("id", "")),
                    str(item.get("title", "")),
                    str(item.get("status", "todo")),
                    item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {},
                )
            )
        open_count = sum(1 for item in out_items if item["status"] in TODO_OPEN_STATUSES)
        return {"backend": "feature_file", "open_count": open_count, "items": out_items}

    def _read(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        items = raw.get("items")
        if not isinstance(items, list):
            items = []
        normalized: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            normalized.append(
                {
                    "id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "status": str(item.get("status", "todo")),
                    "metadata": dict(item.get("metadata")) if isinstance(item.get("metadata"), Mapping) else {},
                }
            )
        return {"version": int(raw.get("version") or 1), "items": normalized}

    def _write(self, payload: Mapping[str, Any]) -> None:
        self._path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def build_work_queue(
    config: Mapping[str, Any] | None,
    *,
    workspace: str,
    todo_manager: Any | None = None,
) -> WorkQueue | None:
    """
    Build queue backend from long_running.queue config.

    Supported backends:
    - todo_store (default)
    - feature_file
    """
    cfg = config if isinstance(config, Mapping) else {}
    long_running = cfg.get("long_running")
    long_running = long_running if isinstance(long_running, Mapping) else {}
    queue_cfg = long_running.get("queue")
    queue_cfg = queue_cfg if isinstance(queue_cfg, Mapping) else {}
    backend = str(queue_cfg.get("backend") or "todo_store").strip().lower()

    if backend == "todo_store":
        if todo_manager is not None and hasattr(todo_manager, "store"):
            try:
                return TodoStoreQueue(store=getattr(todo_manager, "store"))
            except Exception:
                pass
        return TodoStoreQueue(workspace=workspace)

    if backend == "feature_file":
        configured_path = queue_cfg.get("path")
        if configured_path:
            path = Path(str(configured_path))
            if not path.is_absolute():
                path = Path(workspace) / path
        else:
            path = Path(workspace) / ".breadboard" / "longrun_feature_queue.json"
        return FeatureFileQueue(path)

    return None
