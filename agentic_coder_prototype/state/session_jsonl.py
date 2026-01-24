"""Session JSONL persistence utilities (draft).

Implements a minimal JSONL session format with tree semantics, inspired by Pi.
This module is intentionally standalone and does not alter runtime behavior yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import asyncio
import json
import os
import time
import uuid


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def build_session_header(
    *,
    session_id: str,
    cwd: str,
    version: int = 1,
    parent_session: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    leaf_id: Optional[str] = None,
) -> Dict[str, Any]:
    header: Dict[str, Any] = {
        "type": "session",
        "version": int(version),
        "id": str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "cwd": cwd,
        "session_id": str(session_id),
    }
    if parent_session:
        header["parent_session"] = str(parent_session)
    if metadata:
        header["metadata"] = dict(metadata)
    if leaf_id:
        header["leaf_id"] = str(leaf_id)
    return header


def build_entry_base(
    *,
    entry_type: str,
    parent_id: Optional[str],
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "type": str(entry_type),
        "id": entry_id or _short_id(),
        "parent_id": parent_id,
        "timestamp": timestamp or _now_iso(),
    }


def build_message_entry(
    *,
    parent_id: Optional[str],
    message: Dict[str, Any],
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = build_entry_base(entry_type="message", parent_id=parent_id, timestamp=timestamp, entry_id=entry_id)
    entry["message"] = dict(message)
    return entry


def build_compaction_entry(
    *,
    parent_id: str,
    summary: str,
    first_kept_id: Optional[str],
    tokens_before: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = build_entry_base(entry_type="compaction", parent_id=parent_id, timestamp=timestamp, entry_id=entry_id)
    entry["summary"] = summary
    if first_kept_id:
        entry["first_kept_id"] = first_kept_id
    if tokens_before is not None:
        entry["tokens_before"] = int(tokens_before)
    if details:
        entry["details"] = dict(details)
    return entry


def build_branch_summary_entry(
    *,
    parent_id: Optional[str],
    from_id: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = build_entry_base(entry_type="branch_summary", parent_id=parent_id, timestamp=timestamp, entry_id=entry_id)
    entry["from_id"] = from_id
    entry["summary"] = summary
    if details:
        entry["details"] = dict(details)
    return entry


def build_custom_entry(
    *,
    parent_id: Optional[str],
    custom_type: str,
    data: Optional[Dict[str, Any]] = None,
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = build_entry_base(entry_type="custom", parent_id=parent_id, timestamp=timestamp, entry_id=entry_id)
    entry["custom_type"] = str(custom_type)
    if data is not None:
        entry["data"] = dict(data)
    return entry


def build_custom_message_entry(
    *,
    parent_id: str,
    custom_type: str,
    content: Any,
    display: bool = True,
    details: Optional[Dict[str, Any]] = None,
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = build_entry_base(entry_type="custom_message", parent_id=parent_id, timestamp=timestamp, entry_id=entry_id)
    entry["custom_type"] = str(custom_type)
    entry["content"] = content
    entry["display"] = bool(display)
    if details:
        entry["details"] = dict(details)
    return entry


def build_label_entry(
    *,
    parent_id: str,
    target_id: str,
    label: Optional[str],
    timestamp: Optional[str] = None,
    entry_id: Optional[str] = None,
) -> Dict[str, Any]:
    entry = build_entry_base(entry_type="label", parent_id=parent_id, timestamp=timestamp, entry_id=entry_id)
    entry["target_id"] = target_id
    entry["label"] = label
    return entry


@dataclass
class SessionJsonlStore:
    path: Path

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write_header(self, header: Dict[str, Any]) -> None:
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        self.path.write_text(json.dumps(header, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")

    def append(self, entry: Dict[str, Any]) -> None:
        line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def iter_entries(self) -> Iterable[Dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        return entries


class SessionTree:
    """In-memory view of a session JSONL tree."""

    def __init__(self, entries: Iterable[Dict[str, Any]]) -> None:
        self.header: Optional[Dict[str, Any]] = None
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.children: Dict[str, List[str]] = {}
        self.order: List[str] = []
        self._leaf_id: Optional[str] = None
        self._load(entries)

    def _load(self, entries: Iterable[Dict[str, Any]]) -> None:
        for entry in entries:
            if entry.get("type") == "session" and self.header is None:
                self.header = dict(entry)
                self._leaf_id = entry.get("leaf_id") or None
                continue
            entry_id = entry.get("id")
            if not entry_id:
                continue
            self.entries[entry_id] = dict(entry)
            self.order.append(entry_id)
            parent_id = entry.get("parent_id")
            if parent_id:
                self.children.setdefault(parent_id, []).append(entry_id)

        if not self._leaf_id and self.order:
            self._leaf_id = self.order[-1]

    def leaf_id(self) -> Optional[str]:
        return self._leaf_id

    def set_leaf_id(self, entry_id: Optional[str]) -> None:
        self._leaf_id = entry_id

    def path_to_root(self, entry_id: Optional[str]) -> List[Dict[str, Any]]:
        if not entry_id:
            return []
        path: List[Dict[str, Any]] = []
        current = entry_id
        while current:
            entry = self.entries.get(current)
            if not entry:
                break
            path.append(entry)
            current = entry.get("parent_id")
        return list(reversed(path))

    def build_context_path(self, leaf_id: Optional[str] = None) -> List[Dict[str, Any]]:
        path = self.path_to_root(leaf_id or self._leaf_id)
        if not path:
            return []

        latest_compaction: Optional[Tuple[int, Dict[str, Any]]] = None
        for idx, entry in enumerate(path):
            if entry.get("type") == "compaction":
                latest_compaction = (idx, entry)

        if not latest_compaction:
            return path

        comp_idx, comp_entry = latest_compaction
        first_kept_id = comp_entry.get("first_kept_id")
        if not first_kept_id:
            return path

        kept_idx = None
        for idx, entry in enumerate(path):
            if entry.get("id") == first_kept_id:
                kept_idx = idx
                break

        if kept_idx is None:
            return path

        tail = [e for e in path[kept_idx:] if e.get("id") != comp_entry.get("id")]
        return [comp_entry, *tail]


def _coerce_event_type(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "value"):
        try:
            return str(value.value)
        except Exception:
            return str(value)
    return str(value)


def _event_timestamp_iso(event: Any) -> Optional[str]:
    ts_ms: Optional[int] = None
    if isinstance(event, dict):
        raw = event.get("timestamp_ms") or event.get("ts") or event.get("timestamp")
        if raw is not None:
            try:
                ts_ms = int(raw)
            except Exception:
                ts_ms = None
    else:
        raw = getattr(event, "created_at", None)
        if raw is not None:
            try:
                ts_ms = int(raw)
            except Exception:
                ts_ms = None
    if ts_ms is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_ms / 1000))
    except Exception:
        return None


@dataclass
class SessionJsonlWriter:
    store: SessionJsonlStore
    parent_id: Optional[str] = None
    assistant_buffers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_calls: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def _ensure_buffer(self, message_id: str, *, timestamp: Optional[str]) -> Dict[str, Any]:
        buffer = self.assistant_buffers.setdefault(message_id, {"chunks": []})
        if timestamp and not buffer.get("timestamp"):
            buffer["timestamp"] = timestamp
        return buffer

    def _ensure_tool_call(self, call_id: str, *, timestamp: Optional[str]) -> Dict[str, Any]:
        entry = self.tool_calls.setdefault(call_id, {})
        if timestamp and not entry.get("timestamp"):
            entry["timestamp"] = timestamp
        return entry

    def _build_entry(self, event: Any) -> Optional[Dict[str, Any]]:
        if isinstance(event, dict):
            event_type = _coerce_event_type(event.get("type") or "")
            payload = event.get("payload") or {}
        else:
            event_type = _coerce_event_type(getattr(event, "type", ""))
            payload = getattr(event, "payload", {}) or {}
        timestamp = _event_timestamp_iso(event)

        if event_type == "user.message":
            content = payload.get("content") or ""
            if not content:
                return None
            message = {"role": "user", "content": content}
            return build_message_entry(parent_id=self.parent_id, message=message, timestamp=timestamp)

        if event_type == "assistant.message.start":
            message_id = payload.get("message_id") or payload.get("messageId")
            if not message_id:
                return None
            self._ensure_buffer(str(message_id), timestamp=timestamp)
            return None

        if event_type == "assistant.message.delta":
            message_id = payload.get("message_id") or payload.get("messageId")
            delta = payload.get("delta") or ""
            if not message_id:
                return None
            buffer = self._ensure_buffer(str(message_id), timestamp=timestamp)
            if delta:
                buffer["chunks"].append(str(delta))
            return None

        if event_type == "assistant.message.end":
            message_id = payload.get("message_id") or payload.get("messageId")
            if not message_id:
                return None
            buffer = self.assistant_buffers.pop(str(message_id), None) or {}
            if timestamp and not buffer.get("timestamp"):
                buffer["timestamp"] = timestamp
            text = "".join(buffer.get("chunks") or [])
            if not text:
                return None
            message = {"role": "assistant", "content": text, "id": str(message_id)}
            msg_timestamp = buffer.get("timestamp") or timestamp
            return build_message_entry(parent_id=self.parent_id, message=message, timestamp=msg_timestamp)

        if event_type == "assistant.tool_call.start":
            call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("id")
            if not call_id:
                return None
            entry = self._ensure_tool_call(str(call_id), timestamp=timestamp)
            tool_name = payload.get("tool_name") or payload.get("toolName")
            if tool_name:
                entry.setdefault("tool_name", tool_name)
            return None

        if event_type == "assistant.tool_call.delta":
            call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("id")
            if not call_id:
                return None
            entry = self._ensure_tool_call(str(call_id), timestamp=timestamp)
            tool_name = payload.get("tool_name") or payload.get("toolName")
            if tool_name:
                entry.setdefault("tool_name", tool_name)
            args_delta = payload.get("args_text_delta") or payload.get("delta")
            if args_delta:
                entry.setdefault("args_text_chunks", []).append(str(args_delta))
            return None

        if event_type == "assistant.tool_call.end":
            call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("id")
            if not call_id:
                return None
            entry = self.tool_calls.pop(str(call_id), None) or {}
            if timestamp and not entry.get("timestamp"):
                entry["timestamp"] = timestamp
            tool_name = payload.get("tool_name") or payload.get("toolName")
            if tool_name:
                entry.setdefault("tool_name", tool_name)
            if payload.get("args") is not None:
                entry["args"] = payload.get("args")
            if payload.get("args_text"):
                entry.setdefault("args_text", payload.get("args_text"))
            data: Dict[str, Any] = {
                "tool_call_id": str(call_id),
                "tool_name": entry.get("tool_name"),
            }
            if entry.get("args") is not None:
                data["args"] = entry.get("args")
            args_text = entry.get("args_text")
            if not args_text:
                chunks = entry.get("args_text_chunks") or []
                if chunks:
                    args_text = "".join(chunks)
            if args_text:
                data["args_text"] = args_text
            tool_timestamp = entry.get("timestamp") or timestamp
            return build_custom_entry(
                parent_id=self.parent_id,
                custom_type="tool_call",
                data=data,
                timestamp=tool_timestamp,
            )

        if event_type == "tool.result":
            result = payload.get("result")
            if result is None:
                return None
            message: Dict[str, Any] = {"role": "tool_result", "content": result}
            if payload.get("tool_call_id") or payload.get("toolCallId"):
                message["tool_call_id"] = payload.get("tool_call_id") or payload.get("toolCallId")
            if payload.get("status"):
                message["status"] = payload.get("status")
            return build_message_entry(parent_id=self.parent_id, message=message, timestamp=timestamp)

        if event_type in {"session.compaction", "session.compaction.summary"}:
            summary = payload.get("summary") or payload.get("text") or payload.get("content")
            if not summary:
                return None
            parent = self.parent_id or _short_id()
            return build_compaction_entry(
                parent_id=parent,
                summary=str(summary),
                first_kept_id=payload.get("first_kept_id") or payload.get("firstKeptId"),
                tokens_before=payload.get("tokens_before") or payload.get("tokensBefore"),
                details=payload.get("details"),
                timestamp=timestamp,
            )

        if event_type in {"session.branch_summary", "session.branch.summary"}:
            summary = payload.get("summary") or payload.get("text") or payload.get("content")
            if not summary:
                return None
            return build_branch_summary_entry(
                parent_id=self.parent_id,
                from_id=str(payload.get("from_id") or payload.get("fromId") or ""),
                summary=str(summary),
                details=payload.get("details"),
                timestamp=timestamp,
            )

        return None

    async def append_event(self, event: Any) -> Optional[Dict[str, Any]]:
        entry = self._build_entry(event)
        if not entry:
            return None
        await asyncio.to_thread(self.store.append, entry)
        self.parent_id = entry.get("id") or self.parent_id
        return entry


def read_eventlog(path: str | Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return events
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def extract_session_meta(events: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    session_id: Optional[str] = None
    workspace: Optional[str] = None
    metadata: Dict[str, Any] = {}
    for event in events:
        session_id = session_id or event.get("session_id") or event.get("sessionId")
        if event.get("type") == "session.meta":
            payload = event.get("payload") or {}
            if not workspace:
                workspace = payload.get("workspace")
            meta = payload.get("metadata")
            if isinstance(meta, dict):
                metadata.update(meta)
        if event.get("type") == "session.start":
            payload = event.get("payload") or {}
            if not workspace:
                workspace = payload.get("workspace")
    return session_id, workspace, metadata


def collect_entry_descriptors(events: List[Dict[str, Any]]) -> List[Tuple[int, Dict[str, Any]]]:
    entries: List[Tuple[int, Dict[str, Any]]] = []

    assistant_buffers: Dict[str, Dict[str, Any]] = {}
    tool_calls: Dict[str, Dict[str, Any]] = {}

    for idx, event in enumerate(events):
        event_type = event.get("type") or ""
        payload = event.get("payload") or {}

        if event_type == "user.message":
            content = payload.get("content") or ""
            entries.append((idx, {"kind": "message", "role": "user", "content": content}))
            continue

        if event_type == "assistant.message.start":
            message_id = payload.get("message_id") or payload.get("messageId")
            if not message_id:
                continue
            assistant_buffers.setdefault(message_id, {"first_index": idx, "chunks": []})
            continue

        if event_type == "assistant.message.delta":
            message_id = payload.get("message_id") or payload.get("messageId")
            delta = payload.get("delta") or ""
            if not message_id:
                continue
            buffer = assistant_buffers.setdefault(message_id, {"first_index": idx, "chunks": []})
            buffer["chunks"].append(str(delta))
            continue

        if event_type == "assistant.message.end":
            message_id = payload.get("message_id") or payload.get("messageId")
            if not message_id:
                continue
            buffer = assistant_buffers.setdefault(message_id, {"first_index": idx, "chunks": []})
            buffer["end_index"] = idx
            continue

        if event_type == "assistant.tool_call.start":
            call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("id")
            if not call_id:
                continue
            entry = tool_calls.setdefault(call_id, {"first_index": idx})
            entry.setdefault("tool_name", payload.get("tool_name") or payload.get("toolName"))
            continue

        if event_type == "assistant.tool_call.delta":
            call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("id")
            if not call_id:
                continue
            entry = tool_calls.setdefault(call_id, {"first_index": idx})
            entry.setdefault("tool_name", payload.get("tool_name") or payload.get("toolName"))
            args_delta = payload.get("args_text_delta") or payload.get("delta")
            if args_delta:
                entry.setdefault("args_text_chunks", []).append(str(args_delta))
            continue

        if event_type == "assistant.tool_call.end":
            call_id = payload.get("tool_call_id") or payload.get("toolCallId") or payload.get("id")
            if not call_id:
                continue
            entry = tool_calls.setdefault(call_id, {"first_index": idx})
            entry["end_index"] = idx
            entry.setdefault("tool_name", payload.get("tool_name") or payload.get("toolName"))
            if payload.get("args") is not None:
                entry["args"] = payload.get("args")
            if payload.get("args_text"):
                entry.setdefault("args_text", payload.get("args_text"))
            continue

        if event_type == "tool.result":
            result = payload.get("result")
            entries.append(
                (
                    idx,
                    {
                        "kind": "message",
                        "role": "tool_result",
                        "content": result,
                        "tool_call_id": payload.get("tool_call_id") or payload.get("toolCallId"),
                        "status": payload.get("status"),
                    },
                )
            )
            continue

        if event_type in {"session.compaction", "session.compaction.summary"}:
            summary = payload.get("summary") or payload.get("text") or payload.get("content")
            if summary:
                entries.append(
                    (
                        idx,
                        {
                            "kind": "compaction",
                            "summary": summary,
                            "first_kept_id": payload.get("first_kept_id") or payload.get("firstKeptId"),
                            "tokens_before": payload.get("tokens_before") or payload.get("tokensBefore"),
                            "details": payload.get("details"),
                        },
                    )
                )
            continue

        if event_type in {"session.branch_summary", "session.branch.summary"}:
            summary = payload.get("summary") or payload.get("text") or payload.get("content")
            if summary:
                entries.append(
                    (
                        idx,
                        {
                            "kind": "branch_summary",
                            "summary": summary,
                            "from_id": payload.get("from_id") or payload.get("fromId"),
                            "details": payload.get("details"),
                        },
                    )
                )
            continue

    for message_id, buffer in assistant_buffers.items():
        text = "".join(buffer.get("chunks") or [])
        if not text:
            continue
        entries.append(
            (
                int(buffer.get("first_index", 0)),
                {
                    "kind": "message",
                    "role": "assistant",
                    "content": text,
                    "message_id": message_id,
                },
            )
        )

    for call_id, info in tool_calls.items():
        data: Dict[str, Any] = {
            "tool_call_id": call_id,
            "tool_name": info.get("tool_name"),
        }
        args = info.get("args")
        if args is not None:
            data["args"] = args
        args_text = info.get("args_text")
        if not args_text:
            chunks = info.get("args_text_chunks") or []
            if chunks:
                args_text = "".join(chunks)
        if args_text:
            data["args_text"] = args_text
        entries.append(
            (
                int(info.get("first_index", 0)),
                {
                    "kind": "custom",
                    "custom_type": "tool_call",
                    "data": data,
                },
            )
        )

    entries.sort(key=lambda item: item[0])
    return entries


def build_entries_from_descriptors(descriptors: List[Tuple[int, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    parent_id: Optional[str] = None
    for _, item in descriptors:
        kind = item.get("kind")
        if kind == "message":
            message: Dict[str, Any] = {
                "role": item.get("role"),
                "content": item.get("content"),
            }
            if item.get("tool_call_id"):
                message["tool_call_id"] = item.get("tool_call_id")
            if item.get("status"):
                message["status"] = item.get("status")
            if item.get("message_id"):
                message["id"] = item.get("message_id")
            entry = build_message_entry(parent_id=parent_id, message=message)
        elif kind == "compaction":
            summary = item.get("summary")
            if not summary:
                continue
            entry = build_compaction_entry(
                parent_id=parent_id or _short_id(),
                summary=str(summary),
                first_kept_id=item.get("first_kept_id"),
                tokens_before=item.get("tokens_before"),
                details=item.get("details"),
            )
        elif kind == "branch_summary":
            summary = item.get("summary")
            if not summary:
                continue
            entry = build_branch_summary_entry(
                parent_id=parent_id,
                from_id=str(item.get("from_id") or ""),
                summary=str(summary),
                details=item.get("details"),
            )
        elif kind == "custom":
            entry = build_custom_entry(
                parent_id=parent_id,
                custom_type=item.get("custom_type") or "custom",
                data=item.get("data") or {},
            )
        else:
            continue
        entries.append(entry)
        parent_id = entry.get("id")
    return entries


def convert_eventlog_to_session_jsonl(
    *,
    eventlog_path: str | Path,
    out_path: str | Path,
    overwrite: bool = False,
) -> int:
    events = read_eventlog(eventlog_path)
    session_id, workspace, metadata = extract_session_meta(events)
    if not session_id:
        raise ValueError("Missing session_id in event log")
    store = SessionJsonlStore(out_path)
    if store.path.exists():
        if not overwrite:
            return 0
        try:
            store.path.unlink()
        except Exception:
            pass
    header = build_session_header(
        session_id=session_id,
        cwd=workspace or str(Path.cwd()),
        metadata=metadata or None,
    )
    store.write_header(header)
    descriptors = collect_entry_descriptors(events)
    entries = build_entries_from_descriptors(descriptors)
    for entry in entries:
        store.append(entry)
    return len(entries)


def resolve_default_session_path(base_dir: str | Path, session_id: str) -> Path:
    base = Path(base_dir).expanduser().resolve()
    return base / str(session_id) / "session.jsonl"


def ensure_session_file(
    *,
    base_dir: str | Path,
    session_id: str,
    cwd: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> SessionJsonlStore:
    path = resolve_default_session_path(base_dir, session_id)
    store = SessionJsonlStore(path)
    if not store.path.exists() or store.path.stat().st_size == 0:
        header = build_session_header(
            session_id=session_id,
            cwd=cwd or os.getcwd(),
            metadata=metadata,
        )
        store.write_header(header)
    return store


def build_session_jsonl_writer(
    *,
    base_dir: str | Path,
    session_id: str,
    cwd: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    resume: bool = True,
) -> SessionJsonlWriter:
    store = ensure_session_file(base_dir=base_dir, session_id=session_id, cwd=cwd, metadata=metadata)
    writer = SessionJsonlWriter(store=store)
    if resume and store.path.exists():
        try:
            entries = list(store.iter_entries())
            tree = SessionTree(entries)
            writer.parent_id = tree.leaf_id()
        except Exception:
            writer.parent_id = None
    return writer
