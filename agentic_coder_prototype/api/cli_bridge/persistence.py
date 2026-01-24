from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .events import EventType, SessionEvent
from agentic_coder_prototype.state import session_jsonl
from .models import SessionStatus, SessionSummary

logger = logging.getLogger(__name__)
_EVENTLOG_CAPPED = False


def _event_log_max_bytes() -> Optional[int]:
    raw = (os.environ.get("BREADBOARD_EVENTLOG_MAX_MB") or "").strip()
    if not raw:
        return None
    try:
        mb = int(raw)
    except Exception:
        return None
    if mb <= 0:
        return None
    return mb * 1024 * 1024


def eventlog_capped() -> bool:
    return bool(_EVENTLOG_CAPPED)



class SessionEventLog:
    """Append-only JSONL event log for a single session."""

    def __init__(self, base_dir: str | Path, session_id: str) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.session_id = str(session_id)
        self.path = self.base_dir / self.session_id / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cap_warned = False

    async def append(self, event: SessionEvent) -> None:
        payload = event.asdict()
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        await asyncio.to_thread(self._write_line, line)

    def _write_line(self, line: str) -> None:
        max_bytes = _event_log_max_bytes()
        if max_bytes is not None:
            try:
                if self.path.exists() and self.path.stat().st_size > max_bytes:
                    if not self._cap_warned:
                        self._cap_warned = True
                        global _EVENTLOG_CAPPED
                        _EVENTLOG_CAPPED = True
                        logger.warning(
                            "Event log capped; skipping appends for %s (limit=%s MB)",
                            self.path,
                            os.environ.get("BREADBOARD_EVENTLOG_MAX_MB"),
                        )
                    return
            except Exception:
                pass
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def build_event_log(base_dir: Optional[str], session_id: str) -> Optional[SessionEventLog]:
    if not base_dir:
        return None
    return SessionEventLog(base_dir, session_id)


def export_session_jsonl_from_eventlog_path(
    eventlog_path: str | Path,
    *,
    overwrite: bool = False,
) -> Optional[str]:
    path = Path(eventlog_path).expanduser().resolve()
    if not path.exists():
        return None
    out_path = path.parent / "session.jsonl"
    try:
        session_jsonl.convert_eventlog_to_session_jsonl(
            eventlog_path=path,
            out_path=out_path,
            overwrite=overwrite,
        )
    except Exception:
        return None
    return str(out_path)


def iter_event_payloads(base_dir: str | Path, session_id: str) -> Iterable[dict]:
    base_path = Path(base_dir).expanduser().resolve()
    path = base_path / str(session_id) / "events.jsonl"
    if not path.exists():
        return []

    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def list_event_log_sessions(base_dir: str | Path) -> list[str]:
    base_path = Path(base_dir).expanduser().resolve()
    if not base_path.exists():
        return []
    session_ids: list[str] = []
    for path in base_path.glob("*/events.jsonl"):
        if path.parent.is_dir():
            session_ids.append(path.parent.name)
    return sorted(set(session_ids))


def eventlog_last_activity_ms(base_dir: str | Path) -> Optional[int]:
    base_path = Path(base_dir).expanduser().resolve()
    if not base_path.exists():
        return None
    last_ts: Optional[int] = None
    for session_id in list_event_log_sessions(base_dir):
        path = base_path / session_id / "events.jsonl"
        if not path.exists():
            continue
        try:
            last_line = None
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        last_line = line
            if not last_line:
                continue
            payload = json.loads(last_line)
            ts = payload.get("timestamp_ms") or payload.get("timestamp")
            if ts is None:
                continue
            ts_int = int(ts)
            if last_ts is None or ts_int > last_ts:
                last_ts = ts_int
        except Exception:
            continue
    return last_ts


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _decode_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return datetime.now(timezone.utc)
    return _ensure_tz(dt)


def _encode_summary(summary: SessionSummary) -> dict:
    return {
        "session_id": summary.session_id,
        "status": summary.status.value,
        "created_at": summary.created_at.isoformat(),
        "last_activity_at": summary.last_activity_at.isoformat(),
        "model": summary.model,
        "mode": summary.mode,
        "completion_summary": summary.completion_summary,
        "reward_summary": summary.reward_summary,
        "logging_dir": summary.logging_dir,
        "metadata": summary.metadata,
    }


def _decode_summary(payload: dict) -> SessionSummary:
    status = payload.get("status") or SessionStatus.RUNNING.value
    try:
        status_enum = SessionStatus(status)
    except Exception:
        status_enum = SessionStatus.RUNNING

    def _maybe_json(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    return SessionSummary(
        session_id=str(payload.get("session_id") or ""),
        status=status_enum,
        created_at=_decode_dt(payload.get("created_at")),
        last_activity_at=_decode_dt(payload.get("last_activity_at")),
        model=payload.get("model"),
        mode=payload.get("mode"),
        completion_summary=_maybe_json(payload.get("completion_summary")),
        reward_summary=_maybe_json(payload.get("reward_summary")),
        logging_dir=payload.get("logging_dir"),
        metadata=_maybe_json(payload.get("metadata")),
    )


class SessionIndex:
    """Minimal JSON index for session metadata."""

    def __init__(self, base_dir: str | Path) -> None:
        base_path = Path(base_dir).expanduser().resolve()
        base_path.mkdir(parents=True, exist_ok=True)
        self.path = base_path / "index.json"

    async def write_summary(self, summary: SessionSummary) -> None:
        record = _encode_summary(summary)
        await asyncio.to_thread(self._write_record, record)

    async def delete(self, session_id: str) -> None:
        await asyncio.to_thread(self._delete_record, session_id)

    async def list_summaries(self) -> list[SessionSummary]:
        payload = await asyncio.to_thread(self._read_index)
        entries = payload.get("sessions") if isinstance(payload, dict) else {}
        if not isinstance(entries, dict):
            return []
        return [_decode_summary(entry) for entry in entries.values() if isinstance(entry, dict)]

    def _read_index(self) -> dict:
        if not self.path.exists():
            return {"sessions": {}}
        try:
            raw = self.path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except Exception:
            return {"sessions": {}}
        if not isinstance(parsed, dict):
            return {"sessions": {}}
        parsed.setdefault("sessions", {})
        return parsed

    def _write_record(self, record: dict) -> None:
        payload = self._read_index()
        sessions = payload.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        sessions[str(record.get("session_id") or "")] = record
        payload["sessions"] = sessions
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _delete_record(self, session_id: str) -> None:
        payload = self._read_index()
        sessions = payload.get("sessions")
        if not isinstance(sessions, dict):
            return
        sessions.pop(str(session_id), None)
        payload["sessions"] = sessions
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class SessionIndexSQLite:
    """SQLite-backed session index (minimal)."""

    SCHEMA_VERSION = 1

    def __init__(self, base_dir: str | Path) -> None:
        base_path = Path(base_dir).expanduser().resolve()
        base_path.mkdir(parents=True, exist_ok=True)
        self.path = base_path / "sessions.sqlite"
        self._init_db()

    async def write_summary(self, summary: SessionSummary) -> None:
        record = _encode_summary(summary)
        await asyncio.to_thread(self._upsert, record)

    async def delete(self, session_id: str) -> None:
        await asyncio.to_thread(self._delete, session_id)

    async def list_summaries(self) -> list[SessionSummary]:
        rows = await asyncio.to_thread(self._select_all)
        return [_decode_summary(dict(row)) for row in rows]

    async def get_schema_version(self) -> int:
        return await asyncio.to_thread(self._get_schema_version)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT,
                    created_at TEXT,
                    last_activity_at TEXT,
                    model TEXT,
                    mode TEXT,
                    completion_summary TEXT,
                    reward_summary TEXT,
                    logging_dir TEXT,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER
                )
                """
            )
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))
            elif row["version"] != self.SCHEMA_VERSION:
                # Migration stub: update to current version.
                conn.execute("UPDATE schema_version SET version = ?", (self.SCHEMA_VERSION,))

    def _upsert(self, record: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    status,
                    created_at,
                    last_activity_at,
                    model,
                    mode,
                    completion_summary,
                    reward_summary,
                    logging_dir,
                    metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=excluded.status,
                    created_at=excluded.created_at,
                    last_activity_at=excluded.last_activity_at,
                    model=excluded.model,
                    mode=excluded.mode,
                    completion_summary=excluded.completion_summary,
                    reward_summary=excluded.reward_summary,
                    logging_dir=excluded.logging_dir,
                    metadata=excluded.metadata
                """,
                (
                    record.get("session_id"),
                    record.get("status"),
                    record.get("created_at"),
                    record.get("last_activity_at"),
                    record.get("model"),
                    record.get("mode"),
                    json.dumps(record.get("completion_summary"), separators=(",", ":")) if record.get("completion_summary") is not None else None,
                    json.dumps(record.get("reward_summary"), separators=(",", ":")) if record.get("reward_summary") is not None else None,
                    record.get("logging_dir"),
                    json.dumps(record.get("metadata"), separators=(",", ":")) if record.get("metadata") is not None else None,
                ),
            )

    def _delete(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (str(session_id),))

    def _select_all(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM sessions ORDER BY last_activity_at DESC")
            return list(cur.fetchall())

    def _get_schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                return self.SCHEMA_VERSION
            return int(row["version"])


def _decode_event_type(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    try:
        return EventType(raw).value
    except Exception:
        try:
            return str(raw)
        except Exception:
            return None


def parse_event_payload(payload: dict) -> Optional[SessionEvent]:
    event_type = _decode_event_type(payload.get("type"))
    if not event_type:
        return None
    created_at_raw = payload.get("ts") or payload.get("timestamp_ms") or payload.get("timestamp") or 0
    try:
        created_at = int(created_at_raw)
    except Exception:
        created_at = 0
    return SessionEvent(
        type=event_type,
        session_id=str(payload.get("session_id") or ""),
        payload=dict(payload.get("payload") or {}),
        run_id=str(payload.get("run_id") or "") or None,
        turn=payload.get("turn"),
        turn_id=str(payload.get("turn_id") or "") or None,
        span_id=str(payload.get("span_id") or "") or None,
        parent_span_id=str(payload.get("parent_span_id") or "") or None,
        actor=payload.get("actor") if isinstance(payload.get("actor"), dict) else None,
        visibility=str(payload.get("visibility") or "") or None,
        tags=payload.get("tags") if isinstance(payload.get("tags"), list) else None,
        created_at=created_at,
        event_id=str(payload.get("id") or payload.get("event_id") or payload.get("eventId") or ""),
        seq=payload.get("seq"),
        schema_rev=str(payload.get("schema_rev") or "") or None,
        v=int(payload.get("v") or 2),
    )


def iter_event_log(base_dir: str | Path, session_id: str) -> Iterable[SessionEvent]:
    payloads = iter_event_payloads(base_dir, session_id)
    events: list[SessionEvent] = []
    for payload in payloads:
        event = parse_event_payload(payload)
        if event is not None:
            events.append(event)
    return events


def _datetime_from_ms(value: int) -> datetime:
    if value <= 0:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)


def _infer_status(events: Iterable[SessionEvent]) -> SessionStatus:
    seen_error = False
    for event in events:
        event_type = event.type.value if isinstance(event.type, EventType) else str(event.type)
        if event_type in {EventType.RUN_FINISHED.value, "run.end"}:
            return SessionStatus.COMPLETED
        if event_type == EventType.ERROR.value:
            seen_error = True
    if seen_error:
        return SessionStatus.FAILED
    return SessionStatus.RUNNING


@dataclass(frozen=True)
class SessionReplay:
    summary: SessionSummary
    events: list[SessionEvent]


def rehydrate_session_from_event_log(base_dir: str | Path, session_id: str) -> SessionReplay:
    events = list(iter_event_log(base_dir, session_id))
    if events:
        created_at = _datetime_from_ms(events[0].created_at)
        last_activity_at = _datetime_from_ms(events[-1].created_at)
    else:
        created_at = datetime.now(timezone.utc)
        last_activity_at = created_at
    status = _infer_status(events)
    summary = SessionSummary(
        session_id=str(session_id),
        status=status,
        created_at=created_at,
        last_activity_at=last_activity_at,
        model=None,
        mode=None,
        completion_summary=None,
        reward_summary=None,
        logging_dir=None,
        metadata=None,
    )
    return SessionReplay(summary=summary, events=events)


def build_session_index(base_dir: Optional[str], *, engine: str = "json") -> Optional[SessionIndex | SessionIndexSQLite]:
    if not base_dir:
        return None
    engine_norm = (engine or "json").strip().lower()
    if engine_norm == "sqlite":
        return SessionIndexSQLite(base_dir)
    return SessionIndex(base_dir)
