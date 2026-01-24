"""High level orchestration of session lifecycle for the CLI bridge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Sequence

from fastapi import HTTPException, UploadFile, status

from .events import EventType, SessionEvent
from .models import (
    AttachmentHandle,
    AttachmentUploadResponse,
    ModelCatalogEntry,
    ModelCatalogResponse,
    SkillCatalogResponse,
    CTreeDiskArtifact,
    CTreeDiskArtifactsResponse,
    CTreeEventsResponse,
    CTreeSnapshotResponse,
    CTreeTreeNode,
    CTreeTreeResponse,
    SessionCommandRequest,
    SessionCommandResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionFileContent,
    SessionFileInfo,
    SessionInputRequest,
    SessionInputResponse,
    SessionStatus,
)
from .registry import SessionRecord, SessionRegistry
from .persistence import build_event_log, build_session_index, iter_event_log
from .session_runner import SessionRunner
from ...compilation.v2_loader import load_agent_config
from ...policy_pack import PolicyPack
from ...state.session_jsonl import build_session_jsonl_writer
from ...ctrees.persistence import EVENTLOG_HEADER_TYPE, load_events, resolve_ctree_paths
from ...ctrees.schema import sanitize_ctree_payload
from ...ctrees.store import CTreeStore
from ...ctrees.tree_view import build_ctree_tree_view

logger = logging.getLogger(__name__)


def _env_flag(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_flag_default(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return _env_flag(name)


def _load_bridge_chaos_metadata() -> dict[str, float] | None:
    latency = max(0, int(os.environ.get("BREADBOARD_CLI_LATENCY_MS", "0")))
    jitter = max(0, int(os.environ.get("BREADBOARD_CLI_JITTER_MS", "0")))
    try:
        drop = float(os.environ.get("BREADBOARD_CLI_DROP_RATE", "0"))
    except ValueError:
        drop = 0.0
    drop = max(0.0, min(1.0, drop))
    if latency == 0 and jitter == 0 and drop == 0:
        return None
    return {
        "latencyMs": float(latency),
        "jitterMs": float(jitter),
        "dropRate": drop,
    }


class SessionService:
    """Facade that coordinates the registry, runners, and FastAPI endpoints."""

    def __init__(self, registry: SessionRegistry | None = None) -> None:
        self._bridge_chaos = _load_bridge_chaos_metadata()
        self._eventlog_dir = (os.environ.get("BREADBOARD_EVENTLOG_DIR") or "").strip()
        self._eventlog_bootstrap = _env_flag("BREADBOARD_EVENTLOG_BOOTSTRAP")
        self._eventlog_replay = _env_flag("BREADBOARD_EVENTLOG_REPLAY")
        self._eventlog_resume = _env_flag("BREADBOARD_EVENTLOG_RESUME")
        self._eventlog_canonical_only = _env_flag("BREADBOARD_EVENTLOG_CANONICAL_ONLY")
        self._session_jsonl_persist = _env_flag("BREADBOARD_SESSION_JSONL_PERSIST")
        self._session_jsonl_dir = (os.environ.get("BREADBOARD_SESSION_JSONL_DIR") or self._eventlog_dir).strip()
        self._session_jsonl_resume = _env_flag_default("BREADBOARD_SESSION_JSONL_RESUME", True)
        self._session_index_enabled = _env_flag("BREADBOARD_SESSION_INDEX")
        self._session_index_bootstrap = _env_flag("BREADBOARD_SESSION_INDEX_BOOTSTRAP")
        session_index_dir = (os.environ.get("BREADBOARD_SESSION_INDEX_DIR") or self._eventlog_dir).strip()
        session_index_engine = (os.environ.get("BREADBOARD_SESSION_INDEX_ENGINE") or "json").strip()
        self._session_index = (
            build_session_index(session_index_dir, engine=session_index_engine)
            if self._session_index_enabled
            else None
        )
        self.registry = registry or SessionRegistry(index=self._session_index)
        self._eventlog_enrich = _env_flag("BREADBOARD_EVENTLOG_ENRICH")

    async def bootstrap_event_logs(self) -> int:
        if not self._eventlog_bootstrap or not self._eventlog_dir:
            return 0
        return await self.registry.bootstrap_from_event_logs(
            self._eventlog_dir,
            populate_replay=self._eventlog_replay,
        )

    async def bootstrap_index(self) -> int:
        if not self._session_index_bootstrap:
            return 0
        return await self.registry.bootstrap_from_index(
            eventlog_dir=self._eventlog_dir,
            enrich_from_eventlog=self._eventlog_enrich,
        )

    async def create_session(self, request: SessionCreateRequest) -> SessionCreateResponse:
        session_id = str(uuid.uuid4())
        metadata = dict(request.metadata or {})
        if self._bridge_chaos:
            metadata.setdefault("bridgeChaos", self._bridge_chaos)
        metadata.setdefault("config_path", request.config_path)
        record = SessionRecord(session_id=session_id, status=SessionStatus.STARTING, metadata=metadata)
        record.event_log_sink = build_event_log(
            os.environ.get("BREADBOARD_EVENTLOG_DIR"),
            session_id,
        )
        if self._session_jsonl_persist and self._session_jsonl_dir:
            try:
                record.session_jsonl_writer = build_session_jsonl_writer(
                    base_dir=self._session_jsonl_dir,
                    session_id=session_id,
                    cwd=request.workspace or os.getcwd(),
                    metadata=metadata,
                    resume=self._session_jsonl_resume,
                )
            except Exception:
                record.session_jsonl_writer = None
        await self.registry.create(record)
        await self._ensure_dispatcher(record)
        runner = SessionRunner(session=record, registry=self.registry, request=request)
        record.runner = runner
        await runner.start()
        logger.info("Session %s created", session_id)
        return SessionCreateResponse(
            session_id=session_id,
            status=record.status,
            created_at=record.created_at,
            logging_dir=record.logging_dir,
        )

    async def ensure_session(self, session_id: str) -> SessionRecord:
        record = await self.registry.get(session_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        return record

    async def event_stream(
        self,
        session_id: str,
        *,
        replay: bool = False,
        limit: Optional[int] = None,
        from_id: Optional[str] = None,
        validated: bool = False,
    ) -> AsyncIterator[SessionEvent]:
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)
        subscriber: asyncio.Queue[Optional[SessionEvent]] = asyncio.Queue()
        await self._register_subscriber(
            record,
            subscriber,
            replay=replay,
            limit=limit,
            from_id=from_id,
            validated=validated,
        )
        try:
            while True:
                event = await subscriber.get()
                if event is None:
                    break
                yield event
        finally:
            await self._unregister_subscriber(record, subscriber)

    async def _ensure_dispatcher(self, record: SessionRecord) -> None:
        task = record.dispatcher_task
        if task and not task.done():
            return
        loop = asyncio.get_running_loop()
        record.dispatcher_task = loop.create_task(self._dispatch_events(record))

    async def _register_subscriber(
        self,
        record: SessionRecord,
        queue: "asyncio.Queue[Optional[SessionEvent]]",
        *,
        replay: bool = False,
        limit: Optional[int] = None,
        from_id: Optional[str] = None,
        validated: bool = False,
    ) -> None:
        replay_enabled = replay or bool(from_id)
        async with record.dispatch_lock:
            if replay_enabled:
                self._ensure_event_sequence(record)
                events = list(record.event_log)
                if from_id:
                    start_index = self._resolve_start_index(events, from_id)
                    if start_index is None:
                        if self._eventlog_resume and self._eventlog_dir:
                            disk_events = list(iter_event_log(self._eventlog_dir, record.session_id))
                            disk_index = self._resolve_start_index(disk_events, from_id)
                            if disk_index is not None:
                                events = disk_events[disk_index:]
                            elif not validated:
                                raise HTTPException(
                                    status_code=status.HTTP_409_CONFLICT,
                                    detail={
                                        "message": "resume window exceeded or event id not found",
                                        "code": "resume_window_exceeded",
                                        "last_event_id": from_id,
                                        "event_log_size": len(events),
                                        "first_seq": events[0].seq if events else None,
                                        "last_seq": events[-1].seq if events else None,
                                    },
                                )
                            else:
                                events = []
                                try:
                                    from_seq = int(from_id) if from_id is not None else None
                                except Exception:
                                    from_seq = None
                                last_seq = events[-1].seq if events else record.event_seq
                                gap_event = SessionEvent(
                                    type="stream.gap",
                                    session_id=record.session_id,
                                    payload={
                                        "from_seq": from_seq,
                                        "to_seq": last_seq,
                                        "reason": "resume_window_miss",
                                    },
                                )
                                record.event_seq += 1
                                gap_event.seq = record.event_seq
                                record.event_log.append(gap_event)
                                queue.put_nowait(gap_event)
                        elif not validated:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail={
                                    "message": "resume window exceeded or event id not found",
                                    "code": "resume_window_exceeded",
                                    "last_event_id": from_id,
                                    "event_log_size": len(events),
                                    "first_seq": events[0].seq if events else None,
                                    "last_seq": events[-1].seq if events else None,
                                },
                            )
                        else:
                            events = []
                            try:
                                from_seq = int(from_id) if from_id is not None else None
                            except Exception:
                                from_seq = None
                            last_seq = events[-1].seq if events else record.event_seq
                            gap_event = SessionEvent(
                                type="stream.gap",
                                session_id=record.session_id,
                                payload={
                                    "from_seq": from_seq,
                                    "to_seq": last_seq,
                                    "reason": "resume_window_miss",
                                },
                            )
                            record.event_seq += 1
                            gap_event.seq = record.event_seq
                            record.event_log.append(gap_event)
                            queue.put_nowait(gap_event)
                    else:
                        events = events[start_index:]
                if isinstance(limit, int) and limit > 0:
                    events = events[-limit:]
                for event in events:
                    queue.put_nowait(event)
            record.subscribers.add(queue)

    async def _unregister_subscriber(
        self,
        record: SessionRecord,
        queue: "asyncio.Queue[Optional[SessionEvent]]",
    ) -> None:
        async with record.dispatch_lock:
            try:
                record.subscribers.discard(queue)
            except Exception:
                pass

    async def _dispatch_events(self, record: SessionRecord) -> None:
        """Fan-out events from the producer queue to all subscribers."""
        while True:
            event = await record.event_queue.get()
            if event is None:
                break
            async with record.dispatch_lock:
                record.event_seq += 1
                if event.seq is None:
                    event.seq = record.event_seq
                else:
                    record.event_seq = max(record.event_seq, int(event.seq))
                skip_legacy = False
                if self._eventlog_canonical_only:
                    tags = event.tags if isinstance(event.tags, list) else []
                    skip_legacy = "legacy" in tags
                if not skip_legacy:
                    record.event_log.append(event)
                    if record.event_log_sink is not None:
                        try:
                            await record.event_log_sink.append(event)
                        except Exception:
                            pass
                    if record.session_jsonl_writer is not None:
                        try:
                            await record.session_jsonl_writer.append_event(event)
                        except Exception:
                            pass
                if record.subscribers:
                    for subscriber in list(record.subscribers):
                        try:
                            subscriber.put_nowait(event)
                        except asyncio.QueueFull:
                            # Drop on overflow; subscribers are best-effort observers.
                            continue
        async with record.dispatch_lock:
            if record.subscribers:
                for subscriber in list(record.subscribers):
                    try:
                        subscriber.put_nowait(None)
                    except asyncio.QueueFull:
                        continue

    async def list_sessions(self):
        use_index = _env_flag("BREADBOARD_SESSION_INDEX_READ")
        return await self.registry.list(use_index=use_index)

    async def list_skills(self, session_id: str) -> SkillCatalogResponse:
        record = await self.ensure_session(session_id)
        runner = record.runner
        if not runner:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session runner not found")
        payload = runner.get_skill_catalog()
        return SkillCatalogResponse(
            catalog=payload.get("catalog") or {},
            selection=payload.get("selection"),
            sources=payload.get("sources"),
        )

    async def get_ctree_snapshot(self, session_id: str) -> CTreeSnapshotResponse:
        record = await self.ensure_session(session_id)
        runner = record.runner
        if not runner:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session runner not found")
        payload = runner.get_ctree_snapshot()
        return CTreeSnapshotResponse(
            snapshot=payload.get("snapshot"),
            compiler=payload.get("compiler"),
            collapse=payload.get("collapse"),
            runner=payload.get("runner"),
            last_node=payload.get("last_node"),
            hash_summary=payload.get("hash_summary"),
            context_engine=payload.get("context_engine"),
        )

    async def get_ctree_disk_artifacts(self, session_id: str, *, with_sha256: bool = False) -> CTreeDiskArtifactsResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workspace not ready")

        candidate_roots: list[str] = []
        try:
            config = load_agent_config(runner.request.config_path)
            ctrees_cfg = (config.get("ctrees") or {}) if isinstance(config, dict) else {}
            persist_cfg = (ctrees_cfg.get("persist") or {}) if isinstance(ctrees_cfg, dict) else {}
            persist_root = persist_cfg.get("root") or persist_cfg.get("dir")
            if isinstance(persist_root, str):
                persist_root = persist_root.strip()
                if persist_root and not os.path.isabs(persist_root):
                    candidate_roots.append(persist_root)
        except Exception:
            pass
        candidate_roots.extend([".breadboard/ctrees", "."])
        seen: set[str] = set()
        roots: list[str] = []
        for entry in candidate_roots:
            entry = str(entry or "").strip()
            if not entry or entry in seen:
                continue
            seen.add(entry)
            roots.append(entry)

        chosen_root = roots[0] if roots else ".breadboard/ctrees"
        for root_rel in roots:
            meta = (workspace_dir / root_rel / "meta")
            if (meta / "ctree_events.jsonl").exists() or (meta / "ctree_snapshot.json").exists():
                chosen_root = root_rel
                break

        root = workspace_dir / chosen_root
        meta = root / "meta"
        candidates = {"events": meta / "ctree_events.jsonl", "snapshot": meta / "ctree_snapshot.json"}

        def file_sha256(path: Path, *, size_bytes: int) -> tuple[Optional[str], bool]:
            if size_bytes > 50 * 1024 * 1024:
                return None, True
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 256), b""):
                    digest.update(chunk)
            return digest.hexdigest(), False

        artifacts: dict[str, CTreeDiskArtifact] = {}
        for key, path in candidates.items():
            exists = path.exists() and path.is_file()
            size_bytes = None
            sha256 = None
            sha256_skipped = False
            if exists:
                try:
                    size_bytes = path.stat().st_size
                except Exception:
                    size_bytes = None
                if with_sha256 and size_bytes is not None:
                    try:
                        sha256, sha256_skipped = file_sha256(path, size_bytes=size_bytes)
                    except Exception:
                        sha256 = None
                        sha256_skipped = True
            rel_path = path.relative_to(workspace_dir).as_posix()
            artifacts[key] = CTreeDiskArtifact(
                path=rel_path,
                exists=exists,
                size_bytes=size_bytes,
                sha256=sha256,
                sha256_skipped=sha256_skipped,
            )

        return CTreeDiskArtifactsResponse(root=chosen_root, artifacts=artifacts)

    def _load_session_config(self, record: SessionRecord) -> Dict[str, Any]:
        config_path = None
        if isinstance(record.metadata, dict):
            config_path = record.metadata.get("config_path")
        if not config_path:
            try:
                runner = getattr(record, "runner", None)
                request = getattr(runner, "request", None) if runner else None
                config_path = getattr(request, "config_path", None) if request else None
            except Exception:
                config_path = None
        if not isinstance(config_path, str) or not config_path.strip():
            return {}
        try:
            loaded = load_agent_config(config_path)
        except Exception:
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}

    def _candidate_ctree_roots(
        self,
        *,
        record: SessionRecord,
        workspace_dir: Optional[Path],
        config: Dict[str, Any],
    ) -> list[tuple[str, Path, Optional[Path]]]:
        ctrees_cfg = (config.get("ctrees") or {}) if isinstance(config, dict) else {}
        persist_cfg = (ctrees_cfg.get("persist") or {}) if isinstance(ctrees_cfg, dict) else {}
        persist_root = persist_cfg.get("root") or persist_cfg.get("dir")

        roots: list[tuple[str, Path, Optional[Path]]] = []
        if workspace_dir is not None:
            candidate_roots: list[str] = []
            if isinstance(persist_root, str) and persist_root.strip():
                raw_root = persist_root.strip()
                if os.path.isabs(raw_root):
                    try:
                        resolved = Path(raw_root).resolve()
                        resolved.relative_to(workspace_dir.resolve())
                        candidate_roots.append(str(resolved.relative_to(workspace_dir.resolve())))
                    except Exception:
                        pass
                else:
                    candidate_roots.append(raw_root)
            candidate_roots.extend([".breadboard/ctrees", "."])
            seen: set[str] = set()
            for root_rel in candidate_roots:
                root_rel = str(root_rel or "").strip()
                if not root_rel or root_rel in seen:
                    continue
                seen.add(root_rel)
                roots.append((root_rel, workspace_dir / root_rel, workspace_dir))

        logging_dir = record.logging_dir if isinstance(record.logging_dir, str) else None
        if logging_dir:
            try:
                base = Path(logging_dir).resolve()
                roots.append(("logging:meta/ctrees", base / "meta" / "ctrees", base))
            except Exception:
                pass

        return roots

    @staticmethod
    def _file_sha256(path: Path, *, size_bytes: int) -> tuple[Optional[str], bool]:
        if size_bytes > 50 * 1024 * 1024:
            return None, True
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 256), b""):
                digest.update(chunk)
        return digest.hexdigest(), False

    def _read_ctree_events_file(
        self,
        events_path: Path,
        *,
        offset: int,
        limit: Optional[int],
    ) -> tuple[Optional[Dict[str, Any]], list[Dict[str, Any]], bool]:
        header: Optional[Dict[str, Any]] = None
        events: list[Dict[str, Any]] = []
        has_more = False
        seen = 0
        with events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if not isinstance(parsed, dict):
                    continue
                if header is None and parsed.get("_type") == EVENTLOG_HEADER_TYPE:
                    header = dict(parsed)
                    continue
                if not parsed.get("kind"):
                    continue
                if seen < offset:
                    seen += 1
                    continue
                if limit is not None and len(events) >= limit:
                    has_more = True
                    break
                sanitized = sanitize_ctree_payload(parsed)
                if isinstance(sanitized, dict):
                    events.append(dict(sanitized))
                seen += 1
        return header, events, has_more

    def _read_ctree_events_from_eventlog(
        self,
        eventlog_path: Path,
        *,
        offset: int,
        limit: Optional[int],
    ) -> tuple[list[Dict[str, Any]], bool]:
        events: list[Dict[str, Any]] = []
        has_more = False
        seen = 0
        with eventlog_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if not isinstance(parsed, dict):
                    continue
                if str(parsed.get("type") or "") != EventType.CTREE_NODE.value:
                    continue
                payload = parsed.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                node = payload.get("node")
                if not isinstance(node, dict):
                    continue
                kind = node.get("kind")
                if not kind:
                    continue
                event = {
                    "kind": kind,
                    "payload": node.get("payload"),
                    "turn": node.get("turn"),
                    "node_id": node.get("id"),
                }
                if seen < offset:
                    seen += 1
                    continue
                if limit is not None and len(events) >= limit:
                    has_more = True
                    break
                sanitized = sanitize_ctree_payload(event)
                if isinstance(sanitized, dict):
                    events.append(dict(sanitized))
                seen += 1
        return events, has_more

    async def get_ctree_events(
        self,
        session_id: str,
        *,
        source: str = "auto",
        offset: int = 0,
        limit: Optional[int] = None,
        with_sha256: bool = False,
    ) -> CTreeEventsResponse:
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)

        src = str(source or "auto").strip().lower()
        if src not in {"auto", "disk", "eventlog", "memory"}:
            src = "auto"

        offset_value = int(offset) if isinstance(offset, int) else 0
        if offset_value < 0:
            offset_value = 0
        limit_value = limit
        try:
            if limit_value is not None:
                limit_value = int(limit_value)
        except Exception:
            limit_value = None
        if isinstance(limit_value, int) and limit_value <= 0:
            limit_value = None

        config = self._load_session_config(record)
        workspace_dir = None
        runner = getattr(record, "runner", None)
        if runner is not None:
            try:
                workspace_dir = runner.get_workspace_dir()
            except Exception:
                workspace_dir = None

        if src in {"auto", "disk"}:
            for root_label, root_abs, base in self._candidate_ctree_roots(record=record, workspace_dir=workspace_dir, config=config):
                try:
                    paths = resolve_ctree_paths(root_abs)
                    events_path = paths["events"]
                except Exception:
                    continue
                if not events_path.exists():
                    continue

                header, events, has_more = self._read_ctree_events_file(
                    events_path,
                    offset=offset_value,
                    limit=limit_value,
                )
                artifact_rel = str(events_path)
                if base is not None:
                    try:
                        artifact_rel = events_path.relative_to(base).as_posix()
                    except Exception:
                        artifact_rel = str(events_path)
                size_bytes = None
                sha256 = None
                sha256_skipped = False
                try:
                    size_bytes = events_path.stat().st_size
                except Exception:
                    size_bytes = None
                if with_sha256 and size_bytes is not None:
                    try:
                        sha256, sha256_skipped = self._file_sha256(events_path, size_bytes=size_bytes)
                    except Exception:
                        sha256 = None
                        sha256_skipped = True
                return CTreeEventsResponse(
                    source="disk",
                    root=root_label,
                    offset=offset_value,
                    limit=limit_value,
                    has_more=has_more,
                    header=header,
                    events=events,
                    artifact=CTreeDiskArtifact(
                        path=artifact_rel,
                        exists=True,
                        size_bytes=size_bytes,
                        sha256=sha256,
                        sha256_skipped=sha256_skipped,
                    ),
                )

            if src == "disk":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ctree events not found on disk")

        if src in {"auto", "eventlog"} and self._eventlog_dir:
            eventlog_path = Path(self._eventlog_dir).expanduser().resolve() / str(record.session_id) / "events.jsonl"
            if eventlog_path.exists():
                events, has_more = self._read_ctree_events_from_eventlog(
                    eventlog_path,
                    offset=offset_value,
                    limit=limit_value,
                )
                size_bytes = None
                sha256 = None
                sha256_skipped = False
                try:
                    size_bytes = eventlog_path.stat().st_size
                except Exception:
                    size_bytes = None
                if with_sha256 and size_bytes is not None:
                    try:
                        sha256, sha256_skipped = self._file_sha256(eventlog_path, size_bytes=size_bytes)
                    except Exception:
                        sha256 = None
                        sha256_skipped = True
                return CTreeEventsResponse(
                    source="eventlog",
                    root="eventlog",
                    offset=offset_value,
                    limit=limit_value,
                    has_more=has_more,
                    header={"_type": EVENTLOG_HEADER_TYPE, "schema_version": None},
                    events=events,
                    artifact=CTreeDiskArtifact(
                        path=f"{record.session_id}/events.jsonl",
                        exists=True,
                        size_bytes=size_bytes,
                        sha256=sha256,
                        sha256_skipped=sha256_skipped,
                    ),
                )

            if src == "eventlog":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ctree events not found in eventlog")

        if src in {"auto", "memory"}:
            events: list[Dict[str, Any]] = []
            has_more = False
            seen = 0
            collected = 0
            for event in list(record.event_log):
                event_type = event.type.value if isinstance(event.type, EventType) else str(event.type)
                if event_type != EventType.CTREE_NODE.value:
                    continue
                node = (event.payload or {}).get("node") if isinstance(event.payload, dict) else None
                if not isinstance(node, dict):
                    continue
                kind = node.get("kind")
                if not kind:
                    continue
                entry = {
                    "kind": kind,
                    "payload": node.get("payload"),
                    "turn": node.get("turn"),
                    "node_id": node.get("id"),
                }
                if seen < offset_value:
                    seen += 1
                    continue
                if limit_value is not None and collected >= limit_value:
                    has_more = True
                    break
                sanitized = sanitize_ctree_payload(entry)
                if isinstance(sanitized, dict):
                    events.append(dict(sanitized))
                    collected += 1
                seen += 1

            truncated = False
            try:
                maxlen = getattr(record.event_log, "maxlen", None)
                if isinstance(maxlen, int) and maxlen > 0 and len(record.event_log) >= maxlen:
                    truncated = True
            except Exception:
                truncated = False
            return CTreeEventsResponse(
                source="memory",
                root="memory",
                offset=offset_value,
                limit=limit_value,
                has_more=has_more,
                truncated=truncated,
                header={"_type": EVENTLOG_HEADER_TYPE, "schema_version": None},
                events=events,
                artifact=None,
            )

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ctree events not found")

    async def get_ctree_tree_view(
        self,
        session_id: str,
        *,
        source: str = "auto",
        stage: str = "FROZEN",
        include_previews: bool = False,
    ) -> CTreeTreeResponse:
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)

        src = str(source or "auto").strip().lower()
        if src not in {"auto", "disk", "eventlog", "memory"}:
            src = "auto"

        config = self._load_session_config(record)
        ctrees_cfg = (config.get("ctrees") or {}) if isinstance(config, dict) else {}
        compiler_cfg = (ctrees_cfg.get("compiler") or {}) if isinstance(ctrees_cfg, dict) else {}
        collapse_cfg = (ctrees_cfg.get("collapse") or {}) if isinstance(ctrees_cfg, dict) else {}
        collapse_target = collapse_cfg.get("target")
        try:
            collapse_target = int(collapse_target) if collapse_target is not None else None
        except Exception:
            collapse_target = None
        collapse_mode = str(collapse_cfg.get("mode", "all_but_last") or "all_but_last").strip().lower()
        if collapse_mode not in {"none", "all_but_last"}:
            collapse_mode = "all_but_last"

        store: Optional[CTreeStore] = None
        resolved_source: str = "memory"

        workspace_dir = None
        runner = getattr(record, "runner", None)
        if runner is not None:
            try:
                workspace_dir = runner.get_workspace_dir()
            except Exception:
                workspace_dir = None

        if src in {"auto", "disk"}:
            for _, root_abs, _ in self._candidate_ctree_roots(record=record, workspace_dir=workspace_dir, config=config):
                try:
                    events_path = resolve_ctree_paths(root_abs)["events"]
                except Exception:
                    continue
                if events_path.exists():
                    events = load_events(events_path)
                    store = CTreeStore.from_events(events)
                    resolved_source = "disk"
                    break
            if src == "disk" and store is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ctree tree not found on disk")

        if store is None and src in {"auto", "eventlog"} and self._eventlog_dir:
            eventlog_path = Path(self._eventlog_dir).expanduser().resolve() / str(record.session_id) / "events.jsonl"
            if eventlog_path.exists():
                events, _ = self._read_ctree_events_from_eventlog(eventlog_path, offset=0, limit=None)
                store = CTreeStore.from_events(events)
                resolved_source = "eventlog"
            elif src == "eventlog":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ctree tree not found in eventlog")

        if store is None:
            # Memory fallback: reconstruct from current event_log ring buffer.
            events = []
            for event in list(record.event_log):
                event_type = event.type.value if isinstance(event.type, EventType) else str(event.type)
                if event_type != EventType.CTREE_NODE.value:
                    continue
                node = (event.payload or {}).get("node") if isinstance(event.payload, dict) else None
                if not isinstance(node, dict):
                    continue
                kind = node.get("kind")
                if not kind:
                    continue
                entry = {
                    "kind": kind,
                    "payload": node.get("payload"),
                    "turn": node.get("turn"),
                    "node_id": node.get("id"),
                }
                sanitized = sanitize_ctree_payload(entry)
                if isinstance(sanitized, dict):
                    events.append(dict(sanitized))
            store = CTreeStore.from_events(events)
            resolved_source = "memory"

        payload = build_ctree_tree_view(
            store,
            stage=stage,
            compiler_config=dict(compiler_cfg) if isinstance(compiler_cfg, dict) else {},
            collapse_target=collapse_target if isinstance(collapse_target, int) else None,
            collapse_mode=collapse_mode,
            include_previews=bool(include_previews),
        )
        nodes = payload.get("nodes") or []
        return CTreeTreeResponse(
            source=resolved_source,
            stage=str(payload.get("stage") or stage),
            root_id=str(payload.get("root_id") or ""),
            nodes=[CTreeTreeNode(**node) for node in nodes if isinstance(node, dict)],
            selection=payload.get("selection") if isinstance(payload.get("selection"), dict) else None,
            hashes=payload.get("hashes") if isinstance(payload.get("hashes"), dict) else None,
        )

    async def validate_event_stream(
        self,
        session_id: str,
        *,
        from_id: Optional[str] = None,
        replay: bool = False,
    ) -> None:
        if not from_id:
            return
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)
        async with record.dispatch_lock:
            if not (replay or from_id):
                return
            self._ensure_event_sequence(record)
            events = list(record.event_log)
            start_index = self._resolve_start_index(events, from_id)
            if start_index is None:
                if self._eventlog_resume and self._eventlog_dir:
                    try:
                        disk_events = list(iter_event_log(self._eventlog_dir, record.session_id))
                        disk_index = self._resolve_start_index(disk_events, from_id)
                        if disk_index is not None:
                            return
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "resume window exceeded or event id not found",
                        "code": "resume_window_exceeded",
                        "last_event_id": from_id,
                        "event_log_size": len(events),
                        "first_seq": events[0].seq if events else None,
                        "last_seq": events[-1].seq if events else None,
                    },
                )

    def _ensure_event_sequence(self, record: SessionRecord) -> None:
        seq = record.event_seq
        for event in record.event_log:
            if event.seq is None:
                seq += 1
                event.seq = seq
            else:
                seq = max(seq, int(event.seq))
        record.event_seq = seq

    def _resolve_start_index(self, events: list[SessionEvent], from_id: str) -> Optional[int]:
        seq_value: Optional[int] = None
        try:
            if from_id is not None:
                seq_value = int(from_id)
        except ValueError:
            seq_value = None
        if seq_value is not None:
            for idx, event in enumerate(events):
                if event.seq == seq_value:
                    return idx + 1
        for idx, event in enumerate(events):
            if event.event_id == from_id:
                return idx + 1
        return None

    async def stop_session(self, session_id: str) -> None:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if runner:
            await runner.stop()
        await self.registry.update_status(session_id, SessionStatus.STOPPED)
        await self.registry.delete(session_id)

    async def send_input(self, session_id: str, payload: SessionInputRequest) -> SessionInputResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        try:
            await runner.enqueue_input(payload.content, attachments=list(payload.attachments or []))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return SessionInputResponse()

    async def execute_command(self, session_id: str, payload: SessionCommandRequest) -> SessionCommandResponse:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        try:
            detail = await runner.handle_command(payload.command, payload.payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return SessionCommandResponse(detail=detail)

    async def upload_attachments(
        self,
        session_id: str,
        files: Sequence[UploadFile],
        metadata: Optional[dict[str, Any]] = None,
    ) -> AttachmentUploadResponse:
        if not files:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no files provided")
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workspace not ready")

        attachment_entries: list[dict[str, Any]] = []
        handles: list[AttachmentHandle] = []
        attachment_root = workspace_dir / ".breadboard" / "attachments"
        attachment_root.mkdir(parents=True, exist_ok=True)
        for index, upload in enumerate(files, start=1):
            try:
                data = await upload.read()
            except Exception as exc:  # pragma: no cover - FastAPI wraps errors
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"failed to read upload: {exc}") from exc
            if not data:
                continue
            attachment_id = f"att-{uuid.uuid4().hex[:10]}"
            filename = self._sanitize_filename(upload.filename or f"attachment-{index}.bin")
            target_dir = attachment_root / attachment_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / filename
            target_path.write_bytes(data)
            rel_path = target_path.relative_to(workspace_dir)
            handle = AttachmentHandle(
                id=attachment_id,
                filename=filename,
                mime=upload.content_type,
                size_bytes=len(data),
            )
            handles.append(handle)
            attachment_entries.append(
                {
                    "id": attachment_id,
                    "filename": filename,
                    "absolute_path": str(target_path),
                    "relative_path": str(rel_path),
                    "metadata": metadata or {},
                }
            )
        if not handles:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no attachment data found")
        runner.register_attachments(attachment_entries)
        return AttachmentUploadResponse(attachments=handles)

    @staticmethod
    def _resolve_workspace_path(workspace_dir: Path, requested_path: str) -> Path:
        candidate = (requested_path or ".").strip() or "."
        if os.path.isabs(candidate):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file paths must be workspace-relative")
        workspace_root = workspace_dir.resolve()
        resolved = (workspace_root / candidate).resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid path") from exc
        return resolved

    async def list_files(self, session_id: str, root: str = ".") -> list[SessionFileInfo]:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workspace not ready")

        target = self._resolve_workspace_path(workspace_dir, root)
        if not target.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="path not found")

        def to_info(path: Path) -> SessionFileInfo:
            rel = path.relative_to(workspace_dir).as_posix()
            if path.is_dir():
                return SessionFileInfo(path=rel, type="directory")
            stat = path.stat()
            return SessionFileInfo(path=rel, type="file", size=stat.st_size)

        if target.is_file():
            return [to_info(target)]

        children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        return [to_info(child) for child in children]

    async def read_file(
        self,
        session_id: str,
        file_path: str,
        *,
        mode: str = "cat",
        head_lines: int = 200,
        tail_lines: int = 80,
        max_bytes: int = 80_000,
    ) -> SessionFileContent:
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workspace not ready")

        if not file_path or not str(file_path).strip() or str(file_path).strip() == ".":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file path required")

        target = self._resolve_workspace_path(workspace_dir, str(file_path))
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")

        stat = target.stat()
        total_bytes = stat.st_size

        if mode == "snippet":
            snippet = self._read_snippet(target, head_lines=head_lines, tail_lines=tail_lines, max_bytes=max_bytes)
            return SessionFileContent(
                path=target.relative_to(workspace_dir).as_posix(),
                content=snippet,
                truncated=True if total_bytes > max_bytes else False,
                total_bytes=total_bytes,
            )

        try:
            content = target.read_text("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        return SessionFileContent(
            path=target.relative_to(workspace_dir).as_posix(),
            content=content,
            truncated=False,
            total_bytes=total_bytes,
        )

    async def list_models(self, config_path: str) -> ModelCatalogResponse:
        if not config_path or not str(config_path).strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="config_path required")
        try:
            config = load_agent_config(config_path)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"failed to load config: {exc}") from exc

        providers = config.get("providers") or {}
        default_model = providers.get("default_model") or config.get("model")
        models_cfg = providers.get("models") or []
        if not models_cfg and default_model:
            models_cfg = [{"id": default_model}]

        def infer_provider(model_id: str, provider: Optional[str], adapter: Optional[str]) -> Optional[str]:
            if provider and isinstance(provider, str) and provider.strip():
                return provider.strip()
            if isinstance(model_id, str) and "/" in model_id:
                return model_id.split("/", 1)[0]
            if adapter and isinstance(adapter, str) and adapter.strip():
                return adapter.strip()
            return None

        def normalize_entry(entry: Any) -> Optional[ModelCatalogEntry]:
            if isinstance(entry, str):
                model_id = entry
                adapter = None
                provider = infer_provider(model_id, None, None)
                return ModelCatalogEntry(id=model_id, provider=provider, name=model_id)
            if not isinstance(entry, dict):
                return None
            model_id = entry.get("id") or entry.get("model_id") or entry.get("model")
            if not model_id:
                return None
            model_id = str(model_id)
            adapter = entry.get("adapter") or entry.get("adapter_id")
            provider = infer_provider(model_id, entry.get("provider"), adapter)
            name = entry.get("name") or model_id
            context_length = entry.get("context_length") or entry.get("contextLength") or entry.get("context_tokens")
            context_length = int(context_length) if isinstance(context_length, (int, float)) else None
            params = entry.get("params") or entry.get("parameters") or None
            routing = entry.get("routing") or None
            metadata = entry.get("metadata") or None
            return ModelCatalogEntry(
                id=model_id,
                adapter=str(adapter) if adapter else None,
                provider=str(provider) if provider else None,
                name=str(name) if name else None,
                context_length=context_length,
                params=params if isinstance(params, dict) else None,
                routing=routing if isinstance(routing, dict) else None,
                metadata=metadata if isinstance(metadata, dict) else None,
            )

        entries: list[ModelCatalogEntry] = []
        for entry in models_cfg:
            normalized = normalize_entry(entry)
            if normalized:
                entries.append(normalized)

        policy = PolicyPack.from_config(config)
        if policy.model_allowlist is not None or policy.model_denylist:
            entries = [entry for entry in entries if policy.is_model_allowed(entry.id)]
            if default_model and not policy.is_model_allowed(str(default_model)):
                default_model = entries[0].id if entries else None

        return ModelCatalogResponse(
            models=entries,
            default_model=str(default_model) if default_model else None,
            config_path=str(config_path),
        )

    async def resolve_artifact_path(self, session_id: str, artifact: str) -> Path:
        if not artifact or not str(artifact).strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="artifact path required")
        record = await self.ensure_session(session_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        workspace_dir = runner.get_workspace_dir() if runner else None

        candidate_raw = str(artifact).strip()
        candidate_path = Path(candidate_raw)
        allowed_roots: list[Path] = []
        if workspace_dir:
            allowed_roots.append(workspace_dir.resolve())
        if record.logging_dir:
            try:
                allowed_roots.append(Path(record.logging_dir).resolve())
            except Exception:
                pass

        if candidate_path.is_absolute():
            resolved = candidate_path.resolve()
        else:
            resolved = None
            for root in allowed_roots:
                possible = (root / candidate_raw).resolve()
                try:
                    possible.relative_to(root)
                except ValueError:
                    continue
                if possible.exists():
                    resolved = possible
                    break
            if resolved is None and allowed_roots:
                resolved = (allowed_roots[0] / candidate_raw).resolve()

        if resolved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")

        if allowed_roots:
            permitted = False
            for root in allowed_roots:
                try:
                    resolved.relative_to(root)
                    permitted = True
                    break
                except ValueError:
                    continue
            if not permitted:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="artifact path outside workspace")

        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
        return resolved

    @staticmethod
    def _read_snippet(target: Path, *, head_lines: int, tail_lines: int, max_bytes: int) -> str:
        max_bytes = max(1, int(max_bytes))
        head_bytes = max(1, max_bytes // 2)
        tail_bytes = max(1, max_bytes - head_bytes)
        stat = target.stat()
        size = stat.st_size

        with target.open("rb") as handle:
            head_raw = handle.read(head_bytes)
            if size > tail_bytes:
                handle.seek(max(0, size - tail_bytes))
            tail_raw = handle.read(tail_bytes)

        head_text = head_raw.decode("utf-8", errors="replace")
        tail_text = tail_raw.decode("utf-8", errors="replace")
        head_list = head_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[: max(0, int(head_lines))]
        tail_list = tail_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[-max(0, int(tail_lines)) :]

        if size <= max_bytes:
            return "\n".join(head_list)

        parts: list[str] = []
        if head_list:
            parts.extend(head_list)
        parts.extend(["", "… (truncated) …", ""])
        if tail_list:
            parts.extend(tail_list)
        return "\n".join(parts)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        candidate = filename.strip() or "attachment.bin"
        candidate = candidate.replace("\\", "/")
        return os.path.basename(candidate)
