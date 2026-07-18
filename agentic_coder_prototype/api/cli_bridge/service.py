"""High level orchestration of session lifecycle for the CLI bridge."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import time
import uuid, weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional, Sequence
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import AnchoredStorage, ArtifactStore, Session as ProductSession
from breadboard.product.runtime.ports import JsonlEventSink
from fastapi import HTTPException, UploadFile, status
from .events import EventType, SessionEvent
from .models import (
    ATPReplBatchRequest, ATPReplBatchResponse, ATPReplError, ATPReplMetrics, ATPReplRequest, ATPReplResponse,
    ATPReplSorry, AttachmentHandle, AttachmentUploadResponse, ModelCatalogEntry, ModelCatalogResponse,
    SkillCatalogResponse, CTreeSnapshotResponse, SessionCommandRequest, SessionCommandResponse,
    SessionCreateRequest, SessionCreateResponse, SessionFileContent, SessionFileInfo, SessionInputRequest,
    SessionInputResponse, SessionStatus,
)
from .atp_diagnostics import build_atp_harness_diagnostic
from .registry import SessionRecord, SessionRegistry
from .session_runner import MAX_ATTACHMENT_BYTES, SessionRunner
from .tail_index import _TAIL_LINE_INDEX_CACHE
from ...compilation.v2_loader import load_agent_config
from ...compilation.effective_operation_policy import policy_pack_for_config_authority
from .runtime_emission import _sanitize_persisted_runtime_config, compile_runtime_effective_config_graph, default_runtime_record_root, emit_session_start_records, primitive_emission_enabled
from ...provider import runtime_codex as runtime_codex_module
from ...provider_routing import provider_router
logger = logging.getLogger(__name__)
def _load_bridge_chaos_metadata() -> dict[str, float] | None:
    latency, jitter = (max(0, int(os.environ.get(name, "0")))
                       for name in ("BREADBOARD_CLI_LATENCY_MS", "BREADBOARD_CLI_JITTER_MS"))
    try: drop = float(os.environ.get("BREADBOARD_CLI_DROP_RATE", "0"))
    except ValueError: drop = 0.0
    drop = max(0.0, min(1.0, drop))
    if latency == jitter == drop == 0: return None
    return {"latencyMs": float(latency), "jitterMs": float(jitter), "dropRate": drop}
def _env_flag(name: str) -> bool:
    return (os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"})
_START_PENDING, _START_COMMITTED, _START_OWNER = ".start.pending", ".start.committed", ".start.owner"
def _event_root() -> Path:
    return Path(os.environ.get("BREADBOARD_SESSION_EVENT_ROOT", Path.home() / ".breadboard" / "session_events")).resolve()
def _sync_tree(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream: os.fsync(stream.fileno())
    for path in sorted((root, *(item for item in root.rglob("*") if item.is_dir())), key=lambda item: len(item.parts), reverse=True): AnchoredStorage.sync_directory(path)
def _open_workspace_breadboard(workspace_dir: Path) -> tuple[Path, Path, int | None, list[int]]:
    workspace_root = workspace_dir.resolve(); logical = workspace_root / ".breadboard"
    if os.name == "nt":
        handles: list[int] = []
        try:
            handles.append(AnchoredStorage.windows_handle(workspace_root, directory=True, create=False)); handles.append(AnchoredStorage.windows_handle(logical, directory=True))
            return logical, workspace_root, None, handles
        except OSError as exc:
            for handle in reversed(handles): AnchoredStorage.close_windows_handle(handle)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid workspace metadata path") from exc
    root_fd, metadata_fd = os.open(workspace_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)), None
    try:
        try: os.mkdir(".breadboard", dir_fd=root_fd)
        except FileExistsError: pass
        metadata_fd = os.open(".breadboard", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd); os.fsync(root_fd)
        return logical, workspace_root, metadata_fd, []
    except OSError as exc:
        if metadata_fd is not None: os.close(metadata_fd)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid workspace metadata path") from exc
    finally: os.close(root_fd)
def _start_active(path: Path) -> bool:
    try:
        pid = int((path / _START_OWNER).read_text(encoding="utf-8").strip())
        os.kill(pid, 0); return True
    except PermissionError: return True
    except (OSError, ValueError): return False
def _create_owned_stage(path: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(16).hex()}.start-owner"); path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.mkdir(); (temporary / _START_OWNER).write_text(str(os.getpid()), encoding="utf-8"); _sync_tree(temporary)
        temporary.replace(path); AnchoredStorage.sync_directory(path.parent)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
def _cleanup_incomplete_starts() -> None:
    record_root, event_root = default_runtime_record_root(), _event_root()
    for root in (record_root, event_root):
        for staged in root.glob(".*.start-owner") if root.is_dir() else ():
            if not _start_active(staged): shutil.rmtree(staged, ignore_errors=True)
    for staged in record_root.glob(".*.records.starting") if record_root.is_dir() else ():
        session_id = staged.name[1:-len(".records.starting")]
        if _start_active(staged): continue
        if not (record_root / session_id / _START_COMMITTED).is_file(): shutil.rmtree(event_root / session_id, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)
    for bundle in record_root.iterdir() if record_root.is_dir() else ():
        if bundle.is_dir() and (bundle / _START_PENDING).exists() and not (bundle / _START_COMMITTED).exists() and not _start_active(bundle): shutil.rmtree(bundle, ignore_errors=True); shutil.rmtree(event_root / bundle.name, ignore_errors=True)
    for staged in event_root.glob(".*.events.starting") if event_root.is_dir() else ():
        if _start_active(staged): continue
        session_id = staged.name[1:-len(".events.starting")]; authority, target = record_root / session_id / _START_COMMITTED, event_root / session_id
        if authority.is_file():
            target.mkdir(parents=True, exist_ok=True)
            for path in staged.iterdir(): (target / path.name).exists() or path.replace(target / path.name)
            (target / _START_OWNER).unlink(missing_ok=True); shutil.rmtree(staged, ignore_errors=True); _sync_tree(target)
        else: shutil.rmtree(staged, ignore_errors=True)
    for root in (record_root, event_root): AnchoredStorage.sync_directory(root) if root.is_dir() else None
class SessionService:
    """Facade that coordinates the registry, runners, and FastAPI endpoints."""
    def __init__(self, registry: SessionRegistry | None = None) -> None:
        self.registry = registry or SessionRegistry()
        self._bridge_chaos = _load_bridge_chaos_metadata()
        self._atp_repl_enabled = _env_flag("ATP_REPL_ENABLE") or _env_flag("ATP_REPL_ROUTE")
        self._atp_repl_service: Any | None = None
        self._atp_service_initialized = False
        self._atp_runtime_capabilities: dict[str, Any] = {}
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        _cleanup_incomplete_starts()
    def _session_lock(self, session_id: str) -> asyncio.Lock: return self._session_locks.setdefault(session_id, asyncio.Lock())
    @staticmethod
    def _runtime_lock(session_id: str, runtime_config: dict[str, Any], source_ref: str) -> EffectiveHarnessLock:
        return EffectiveHarnessLock._from_record(compile_runtime_effective_config_graph(session_id, runtime_config, source_ref))
    def _publication_boundary(self, _name: str) -> None: pass
    def _publish_start_bundle(
        self, session_id: str, staged_record_dir: Path, staging_record_root: Path, runtime_record_dir: Path, staged_event_dir: Path, event_dir: Path, publish_records: bool,
    ) -> None:
        bundle = staged_record_dir if publish_records else staged_event_dir
        bundle.mkdir(parents=True, exist_ok=True); (bundle / _START_OWNER).write_text(str(os.getpid()), encoding="utf-8")
        if publish_records:
            bundle.mkdir(parents=True, exist_ok=True); (bundle / _START_PENDING).write_text(session_id + "\n", encoding="utf-8")
            _sync_tree(bundle); _sync_tree(staged_event_dir); self._publication_boundary("records")
            if runtime_record_dir == event_dir:
                for path in staged_event_dir.iterdir(): path.replace(bundle / path.name)
                shutil.rmtree(staged_event_dir); _sync_tree(bundle)
            self._publication_boundary("events")
        else: _sync_tree(bundle); self._publication_boundary("records"); self._publication_boundary("events")
        temporary, marker = bundle / f"{_START_COMMITTED}.tmp", bundle / _START_COMMITTED
        with temporary.open("xb") as stream:
            stream.write((session_id + "\n").encode()); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, marker); AnchoredStorage.sync_directory(bundle); self._publication_boundary("commit")
        target = runtime_record_dir if publish_records else event_dir; bundle.replace(target); AnchoredStorage.sync_directory(target.parent); self._publication_boundary("authority")
        if publish_records: shutil.rmtree(staging_record_root, ignore_errors=True)
        if publish_records and runtime_record_dir != event_dir: staged_event_dir.replace(event_dir); AnchoredStorage.sync_directory(event_dir.parent)
        for owner in {target / _START_OWNER, event_dir / _START_OWNER}: owner.unlink(missing_ok=True)
        for root in {target, event_dir}: AnchoredStorage.sync_directory(root) if root.is_dir() else None
    async def create_session(self, request: SessionCreateRequest) -> SessionCreateResponse:
        session_id, metadata = str(uuid.uuid4()), dict(request.metadata or {}); metadata.setdefault("config_path", request.config_path)
        if self._bridge_chaos: metadata.setdefault("bridgeChaos", self._bridge_chaos)
        record = SessionRecord(session_id=session_id, status=SessionStatus.STARTING, metadata=metadata); runner = SessionRunner(session=record, registry=self.registry, request=request)
        runtime_config = runner.prepare_runtime_config(); persisted_runtime_config = _sanitize_persisted_runtime_config(runtime_config); runtime_graph = compile_runtime_effective_config_graph(session_id, persisted_runtime_config, request.config_path); runtime_lock = EffectiveHarnessLock._from_record(runtime_graph)
        emit_primitives = primitive_emission_enabled(); runtime_record_dir, event_dir = default_runtime_record_root() / session_id, _event_root() / session_id
        staging_record_root = runtime_record_dir.parent / f".{session_id}.records.starting"
        staged_record_dir, staged_event_dir = staging_record_root / session_id, event_dir.with_name(f".{session_id}.events.starting")
        if event_dir.exists() or emit_primitives and runtime_record_dir.exists(): raise RuntimeError(f"session bundle already exists: {session_id}")
        active_stages, created_stages = {staged_event_dir, *({staging_record_root} if emit_primitives else set())}, []
        try:
            for path in active_stages:
                _create_owned_stage(path); created_stages.append(path)
        except BaseException:
            for path in created_stages: shutil.rmtree(path, ignore_errors=True); AnchoredStorage.sync_directory(path.parent) if path.parent.is_dir() else None
            raise
        if emit_primitives: metadata.setdefault("runtime_record_dir", str(runtime_record_dir))
        record.runner, record.product_artifacts, published = runner, {}, False
        try:
            if emit_primitives:
                staged_paths = emit_session_start_records(
                    session_id=session_id, request=request, output_root=staging_record_root,
                    effective_runtime_config=runtime_config,
                )
                metadata.setdefault("runtime_records", {name: str(runtime_record_dir / Path(path).relative_to(staged_record_dir)) for name, path in staged_paths.items()})
            event_sink = JsonlEventSink(staged_event_dir / "session_events.jsonl")
            product_session = ProductSession.start(runtime_lock, request.task if request.task.strip() else "interactive session awaiting input",
                                                   session_id=session_id, sink=event_sink)
            record.product_session = product_session; metadata["session_contract"] = product_session.read_model.as_dict()
            async with self.registry._lock:
                self._publish_start_bundle(session_id, staged_record_dir, staging_record_root, runtime_record_dir, staged_event_dir, event_dir, emit_primitives)
                event_sink.path = event_dir / "session_events.jsonl"; self.registry._records[session_id] = record
            published = True; await self._ensure_dispatcher(record)
            await self._maybe_prewarm_request_runtime(request, metadata, runtime_config)
            await runner.start()
        except BaseException:
            published = published or ((runtime_record_dir if emit_primitives else event_dir) / _START_COMMITTED).is_file()
            if published and "event_sink" in locals(): event_sink.path = (event_dir if (event_dir / "session_events.jsonl").is_file() else staged_event_dir) / "session_events.jsonl"
            if published: (staged_event_dir / _START_OWNER).unlink(missing_ok=True)
            try: runner.transition_product_session("fail", "session_setup_failed", "session setup failed")
            except Exception: logger.exception("Failed to terminalize session %s after setup failure", session_id)
            try: await runner.stop()
            except Exception: logger.exception("Failed to stop session %s after setup failure", session_id)
            if record.dispatcher_task and not record.dispatcher_task.done():
                await record.event_queue.put(None); await asyncio.gather(record.dispatcher_task, return_exceptions=True)
            if not published:
                self.registry._records.pop(session_id, None)
                for path in (runtime_record_dir, staging_record_root, event_dir, staged_event_dir): shutil.rmtree(path, ignore_errors=True)
                for root in (runtime_record_dir.parent, event_dir.parent):
                    if root.exists(): AnchoredStorage.sync_directory(root)
            else: await self.registry.update_status(session_id, SessionStatus.FAILED)
            raise
        logger.info("Session %s created", session_id)
        return SessionCreateResponse(session_id=session_id, status=record.status, created_at=record.created_at, logging_dir=record.logging_dir)
    async def _maybe_prewarm_request_runtime(self, request: SessionCreateRequest, metadata: Dict[str, Any], runtime_config: dict[str, Any]) -> None:
        if not self._should_prewarm_request_runtime(metadata): return
        try: await asyncio.to_thread(self._prewarm_request_runtime_sync, request, metadata, runtime_config)
        except Exception as exc: logger.debug("Codex prewarm skipped: %s", exc)
    def _should_prewarm_request_runtime(self, metadata: Dict[str, Any]) -> bool:
        return bool(metadata.get("non_interactive_cli_session") or str(metadata.get("cli_session_kind") or "").strip().lower() in {"oneshot", "interactive", "repl"})
    def _prewarm_request_runtime_sync(self, request: SessionCreateRequest, metadata: Dict[str, Any], config: dict[str, Any]) -> None:
        providers = config.get("providers", {}) if isinstance(config, dict) else {}
        selected_model = (
            metadata.get("model")
            or (request.overrides or {}).get("providers.default_model")
            or providers.get("default_model")
            or (config.get("model") if isinstance(config, dict) else None)
        )
        if not selected_model:
            return
        model_ref = str(selected_model).strip()
        if not model_ref:
            return
        descriptor, routed_model = provider_router.get_runtime_descriptor(model_ref)
        if descriptor.runtime_id != "codex_app_server":
            return
        workspace = str(request.workspace or os.getcwd()).strip() or os.getcwd()
        runtime_codex_module.prewarm_codex_app_server(model=routed_model, cwd=workspace)
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
        if getattr(record, "_dispatcher_complete", False) or task and not task.done(): return
        record.dispatcher_task = asyncio.get_running_loop().create_task(self._dispatch_events(record))
    async def _register_subscriber(
        self, record: SessionRecord, queue: "asyncio.Queue[Optional[SessionEvent]]", *,
        replay: bool = False, limit: Optional[int] = None, from_id: Optional[str] = None,
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
                        if not validated:
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
                        events = []
                    else:
                        events = events[start_index:]
                if isinstance(limit, int) and limit > 0:
                    events = events[-limit:]
                for event in events: queue.put_nowait(event)
            else:
                # Snapshot-on-reconnect: if the client connects without replay/from_id,
                # push the most recent todo snapshot into its queue so the TUI can
                # converge even when history is missing (resume window exceeded, etc).
                envelope = record.metadata.get("todo_last_update") if isinstance(record.metadata, dict) else None
                if not isinstance(envelope, dict):
                    runner = getattr(record, "runner", None)
                    workspace_dir = runner.get_workspace_dir() if runner else None
                    if workspace_dir:
                        try:
                            from agentic_coder_prototype.todo import TodoStore
                            from agentic_coder_prototype.todo.projection import project_store_snapshot_to_tui_envelope
                            store = TodoStore(str(workspace_dir), load_existing=True)
                            envelope = project_store_snapshot_to_tui_envelope(store.snapshot(), scope_key="main", scope_label="main")
                        except Exception:
                            envelope = None
                if isinstance(envelope, dict):
                    queue.put_nowait(
                        SessionEvent(
                            EventType.TOOL_RESULT,
                            record.session_id,
                            {"call_id": f"todo:snapshot:connect:{uuid.uuid4().hex[:8]}", "todo": envelope},
                        )
                    )
            if getattr(record, "_dispatcher_complete", False): queue.put_nowait(None)
            else: record.subscribers.add(queue)
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
                record.event_log.append(event)
                if record.subscribers:
                    for subscriber in list(record.subscribers):
                        try:
                            subscriber.put_nowait(event)
                        except asyncio.QueueFull:
                            # Drop on overflow; subscribers are best-effort observers.
                            continue
        async with record.dispatch_lock:
            setattr(record, "_dispatcher_complete", True); subscribers = list(record.subscribers)
            for subscriber in subscribers:
                try:
                    subscriber.put_nowait(None)
                except asyncio.QueueFull: subscriber.get_nowait(); subscriber.put_nowait(None)
    async def list_sessions(self):
        return await self.registry.list()
    async def list_session_records(
        self,
        session_id: str,
        *,
        schema_version: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        record = await self.ensure_session(session_id)
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        runtime_dir = Path(str(metadata["runtime_record_dir"])) if metadata.get("runtime_record_dir") else default_runtime_record_root() / session_id
        rows: list[dict[str, Any]] = []
        committed = not (runtime_dir / _START_PENDING).exists() or (runtime_dir / _START_COMMITTED).exists()
        if committed:
            for path in sorted((runtime_dir / "records").glob("*.jsonl")):
                try: lines = path.read_text(encoding="utf-8").splitlines()
                except OSError: continue
                for line_no, line in enumerate(lines, start=1):
                    if not line.strip(): continue
                    try: payload = json.loads(line)
                    except json.JSONDecodeError: continue
                    row_record = payload.get("record") if isinstance(payload, dict) and isinstance(payload.get("record"), dict) else payload
                    row_schema = row_record.get("schema_version") if isinstance(row_record, dict) else None
                    if row_schema is None and isinstance(payload, dict): row_schema = payload.get("schema_version")
                    if not schema_version or row_schema == schema_version:
                        rows.append({"schema_version": row_schema, "path": str(path), "line": line_no, "record": row_record})
        safe_offset, safe_limit = max(0, int(offset)), max(1, min(int(limit), 1000))
        return {"session_id": session_id, "records": rows[safe_offset:safe_offset + safe_limit],
                "offset": safe_offset, "limit": safe_limit, "total": len(rows)}
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
        )
    async def get_limits_status(self, session_id: str) -> dict[str, Any] | None:
        from .events import EventType
        record = await self.ensure_session(session_id)
        try:
            for event in reversed(list(record.event_log)):
                event_type = getattr(event, "type", None)
                if event_type == EventType.LIMITS_UPDATE or getattr(event_type, "value", None) == EventType.LIMITS_UPDATE.value:
                    payload = getattr(event, "payload", None)
                    if isinstance(payload, dict):
                        return dict(payload)
                    return None
        except Exception:
            return None
        return None
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
        async with self._session_lock(session_id): await self._stop_session_locked(session_id)
    async def _stop_session_locked(self, session_id: str) -> None:
        record = await self.ensure_session(session_id); runner: Optional[SessionRunner] = getattr(record, "runner", None)
        try:
            if runner: await runner.stop()
        finally:
            try:
                product_session: ProductSession | None = getattr(record, "product_session", None)
                terminal_status = {"canceled": SessionStatus.STOPPED, "completed": SessionStatus.COMPLETED, "failed": SessionStatus.FAILED}.get(getattr(getattr(product_session, "read_model", None), "status", None))
                if terminal_status is not None: await self.registry.update_status(session_id, terminal_status)
            finally:
                dispatcher = getattr(record, "dispatcher_task", None)
                if dispatcher and not dispatcher.done(): await record.event_queue.put(None); await dispatcher
    async def delete_session(self, session_id: str) -> None:
        async with self._session_lock(session_id):
            await self._stop_session_locked(session_id)
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
        def durable_reconfigure(runtime_config: dict[str, Any]) -> None:
            runner.transition_product_session(
                "reconfigure", self._runtime_lock(session_id, runtime_config, runner.request.config_path),
                payload.command)
        try:
            detail = await runner.handle_command(
                payload.command,
                payload.payload,
                durable_reconfigure=durable_reconfigure if payload.command in {"set_model", "set_mode", "set_skills"} else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except RuntimeError as exc:
            product_session: ProductSession | None = getattr(record, "product_session", None)
            if product_session and product_session.read_model.status == "failed":
                await self.registry.update_status(session_id, SessionStatus.FAILED)
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return SessionCommandResponse(detail=detail)
    async def upload_attachments(self, session_id: str, files: Sequence[UploadFile], metadata: Optional[dict[str, Any]] = None) -> AttachmentUploadResponse:
        async with self._session_lock(session_id):
            return await self._upload_attachments_locked(session_id, files, metadata)
    async def _upload_attachments_locked(
        self, session_id: str, files: Sequence[UploadFile], metadata: Optional[dict[str, Any]] = None,
    ) -> AttachmentUploadResponse:
        if not files: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no files provided")
        record = await self.ensure_session(session_id); runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        workspace_dir = runner.get_workspace_dir()
        if not workspace_dir: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workspace not ready")
        staged_uploads = []; staged_bytes = 0
        for index, upload in enumerate(files, start=1):
            data = bytearray()
            try:
                while True:
                    chunk = await upload.read(MAX_ATTACHMENT_BYTES - staged_bytes - len(data) + 1)
                    if not chunk: break
                    data.extend(chunk)
                    if staged_bytes + len(data) > MAX_ATTACHMENT_BYTES: raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"attachments exceed {MAX_ATTACHMENT_BYTES}-byte handoff limit")
            except HTTPException: raise
            except Exception as exc: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"failed to read upload: {exc}") from exc
            if data: staged_uploads.append((index, upload, bytes(data))); staged_bytes += len(data)
        if not staged_uploads: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no attachment data found")
        attachment_entries: list[dict[str, Any]] = []; handles: list[AttachmentHandle] = []; created_dirs: list[str] = []; created_refs = set()
        anchor, workspace_root, descriptor, windows_handles = _open_workspace_breadboard(workspace_dir); artifact_fd = attachment_fd = None
        artifact_root, attachment_root = anchor / "artifacts", anchor / "attachments"; artifact_refs = dict(getattr(record, "product_artifacts", {}))
        manifest_path: Path | None = None; manifest_fd = None; manifest_name = None; transaction = None; registered_before = dict(getattr(runner, "_attachment_store", {}))
        try:
            if descriptor is not None:
                artifact_fd = AnchoredStorage.open_directory(descriptor, "artifacts")
                try: attachment_fd = AnchoredStorage.open_directory(descriptor, "attachments")
                except BaseException: os.close(artifact_fd); artifact_fd = None; raise
                os.fsync(descriptor)
            artifact_store = ArtifactStore(artifact_root, descriptor=artifact_fd)
            candidate_transaction = artifact_store.transaction(); candidate_transaction.__enter__(); transaction = candidate_transaction
            if attachment_fd is None: attachment_root.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                windows_handles.append(AnchoredStorage.windows_handle(artifact_root, directory=True)); windows_handles.append(AnchoredStorage.windows_handle(attachment_root, directory=True))
            try:
                for index, upload, data in staged_uploads:
                    attachment_id = f"att-{uuid.uuid4().hex[:10]}"; filename = self._sanitize_filename(upload.filename or f"attachment-{index}.bin"); created_dirs.append(attachment_id)
                    if attachment_fd is not None: target_fd = AnchoredStorage.open_directory(attachment_fd, attachment_id)
                    else: target_fd = None; (attachment_root / attachment_id).mkdir(parents=True, exist_ok=True)
                    try:
                        artifact_ref = artifact_store.put(data, media_type=upload.content_type or "application/octet-stream", created=created_refs)
                        if target_fd is not None: artifact_store.materialize_at(artifact_ref, target_fd, filename)
                        else: artifact_store.materialize(artifact_ref, attachment_root / attachment_id / filename)
                        artifact_refs[attachment_id] = artifact_ref
                    finally:
                        if target_fd is not None: os.close(target_fd)
                    logical_target = workspace_root / ".breadboard" / "attachments" / attachment_id / filename
                    handles.append(AttachmentHandle(id=attachment_id, filename=filename, mime=upload.content_type, size_bytes=len(data)))
                    attachment_entries.append({"id": attachment_id, "filename": filename, "absolute_path": str(logical_target), "relative_path": str(logical_target.relative_to(workspace_root)), "metadata": metadata or {}})
                manifest = artifact_store.manifest(session_id, artifact_refs); manifest_ref = artifact_store.put_json(manifest, created=created_refs)
                manifest_name = f"{session_id}.{manifest_ref.digest.removeprefix('sha256:')}.json"
                if artifact_fd is not None: manifest_fd = AnchoredStorage.open_directory(artifact_fd, "manifests"); artifact_store.materialize_at(manifest_ref, manifest_fd, manifest_name); os.fsync(artifact_fd)
                else: manifest_path = artifact_root / "manifests" / manifest_name; artifact_store.materialize(manifest_ref, manifest_path)
                if descriptor is not None and (workspace_root / ".breadboard").resolve() != AnchoredStorage.descriptor_path(descriptor).resolve(): raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="workspace metadata path changed")
                runner.register_attachments(attachment_entries)
            except BaseException:
                if attachment_fd is not None:
                    for name in created_dirs:
                        try: target_fd = AnchoredStorage.open_directory(attachment_fd, name, create=False)
                        except FileNotFoundError: continue
                        try:
                            for child in os.listdir(target_fd): os.unlink(child, dir_fd=target_fd)
                        finally: os.close(target_fd)
                        os.rmdir(name, dir_fd=attachment_fd)
                    if manifest_fd is not None and manifest_name is not None:
                        try: os.unlink(manifest_name, dir_fd=manifest_fd)
                        except FileNotFoundError: pass
                        os.fsync(manifest_fd); os.fsync(artifact_fd)
                    os.fsync(attachment_fd)
                else:
                    for name in created_dirs:
                        target = attachment_root / name; target_lock = AnchoredStorage.windows_handle(target, directory=True, create=False) if os.name == "nt" else None
                        try:
                            if target_lock is None: shutil.rmtree(target, ignore_errors=True)
                            else:
                                for child in target.iterdir(): child.unlink()
                        finally: AnchoredStorage.close_windows_handle(target_lock)
                        if target_lock is not None: target.rmdir()
                    if manifest_path is not None:
                        manifest_lock = AnchoredStorage.windows_handle(manifest_path.parent, directory=True, create=False) if os.name == "nt" else None
                        try: manifest_path.unlink(missing_ok=True)
                        finally: AnchoredStorage.close_windows_handle(manifest_lock)
                    for parent in {attachment_root, manifest_path.parent if manifest_path is not None else artifact_root}: AnchoredStorage.sync_directory(parent) if parent.is_dir() else None
                for artifact_ref in created_refs: artifact_store.discard(artifact_ref)
                if hasattr(runner, "_attachment_store"): runner._attachment_store = registered_before
                raise
        finally:
            if transaction is not None: transaction.__exit__(None, None, None)
            for open_descriptor in (manifest_fd, artifact_fd, attachment_fd, descriptor):
                if open_descriptor is not None: os.close(open_descriptor)
            for handle in reversed(windows_handles): AnchoredStorage.close_windows_handle(handle)
        record.product_artifacts = artifact_refs; record.metadata["artifact_manifest"], record.metadata["artifact_manifest_ref"] = manifest, manifest_ref.as_dict()
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
        head_lines: int | None = None,
        tail_lines: int | None = None,
        max_bytes: int | None = None,
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
            resolved_head_lines = 200 if head_lines is None else head_lines
            resolved_tail_lines = 80 if tail_lines is None else tail_lines
            resolved_max_bytes = 80_000 if max_bytes is None else max_bytes
            snippet, returned_bytes = self._read_snippet(
                target,
                head_lines=resolved_head_lines,
                tail_lines=resolved_tail_lines,
                max_bytes=resolved_max_bytes,
            )
            return SessionFileContent(
                path=target.relative_to(workspace_dir).as_posix(),
                content=snippet,
                truncated=True if returned_bytes < total_bytes else False,
                total_bytes=total_bytes,
            )
        # Optional: bounded reads for "cat" to keep focus/raw mode performant on large artifacts.
        if mode == "cat":
            effective_tail_lines = None if tail_lines is None else max(0, int(tail_lines))
            effective_max_bytes = None if max_bytes is None else max(1, int(max_bytes))
            if effective_tail_lines is not None and effective_max_bytes is None:
                # Defensive fallback: avoid unbounded reads if caller asked for tail lines but omitted a byte cap.
                effective_max_bytes = 80_000
            if effective_tail_lines is not None and effective_tail_lines > 0 and effective_max_bytes is not None:
                content, meta = _TAIL_LINE_INDEX_CACHE.read_tail_text(
                    target, tail_lines=effective_tail_lines, max_bytes=effective_max_bytes
                )
                start_offset = int(meta.get("start_offset", 0))
                return SessionFileContent(
                    path=target.relative_to(workspace_dir).as_posix(),
                    content=content,
                    truncated=True if start_offset > 0 else False,
                    total_bytes=total_bytes,
                )
            if effective_max_bytes is not None and total_bytes > effective_max_bytes:
                try:
                    with target.open("rb") as handle:
                        handle.seek(max(0, total_bytes - effective_max_bytes))
                        raw = handle.read(effective_max_bytes)
                except Exception as exc:  # pragma: no cover - defensive
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
                text = raw.decode("utf-8", errors="replace")
                return SessionFileContent(
                    path=target.relative_to(workspace_dir).as_posix(),
                    content=text,
                    truncated=True,
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
        policy = policy_pack_for_config_authority(
            config,
            session_id="model_catalog",
            config_path=config_path,
            logger=logger,
        )
        if policy.model_allowlist is not None or policy.model_denylist:
            entries = [entry for entry in entries if policy.is_model_allowed(entry.id)]
            if default_model and not policy.is_model_allowed(str(default_model)):
                default_model = entries[0].id if entries else None
        return ModelCatalogResponse(
            models=entries,
            default_model=str(default_model) if default_model else None,
            config_path=str(config_path),
        )
    def atp_feature_status(self, *, enabled: bool | None = None) -> dict[str, Any]:
        return {
            "enabled": bool(self._atp_repl_enabled if enabled is None else enabled),
            "service_initialized": bool(self._atp_service_initialized),
            "runtime_capabilities": dict(self._atp_runtime_capabilities or {}),
        }
    async def _ensure_atp_repl_service(self):
        if not bool(self._atp_repl_enabled):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "atp_repl_disabled",
                    "message": "ATP REPL is disabled",
                },
            )
        if self._atp_repl_service is not None:
            return self._atp_repl_service
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "atp_repl_unavailable",
                "message": "ATP REPL backend not initialized",
            },
        )
    @staticmethod
    def _build_atp_backend_request(payload: ATPReplRequest):
        metadata = dict(payload.metadata or {})
        if payload.tenant_id:
            metadata["tenant_id"] = payload.tenant_id
        return SimpleNamespace(
            commands=list(payload.commands),
            state_ref=payload.state_ref,
            timeout_s=payload.timeout_s,
            memory_mb=payload.memory_mb,
            max_heartbeats=payload.max_heartbeats,
            want_state=bool(payload.want_state),
            metadata=metadata,
        )
    @staticmethod
    async def _maybe_await(value):
        if asyncio.iscoroutine(value):
            return await value
        return value
    @staticmethod
    def _coerce_metrics(metrics: Any) -> list[ATPReplMetrics]:
        rows: list[ATPReplMetrics] = []
        for item in list(metrics or []):
            rows.append(
                ATPReplMetrics(
                    repl_ms=(None if getattr(item, "repl_ms", None) is None else float(getattr(item, "repl_ms"))),
                    restore_ms=(
                        None if getattr(item, "restore_ms", None) is None else float(getattr(item, "restore_ms"))
                    ),
                )
            )
        return rows
    @staticmethod
    def _coerce_errors(errors: Any) -> list[ATPReplError]:
        rows: list[ATPReplError] = []
        for item in list(errors or []):
            rows.append(
                ATPReplError(
                    severity=getattr(item, "severity", None),
                    message=str(getattr(item, "message", "")),
                    pos_line=getattr(item, "pos_line", None),
                    pos_col=getattr(item, "pos_col", None),
                    signature=getattr(item, "signature", None),
                )
            )
        return rows
    @staticmethod
    def _coerce_sorries(sorries: Any) -> list[ATPReplSorry]:
        rows: list[ATPReplSorry] = []
        for item in list(sorries or []):
            rows.append(
                ATPReplSorry(
                    pos_line=getattr(item, "pos_line", None),
                    pos_col=getattr(item, "pos_col", None),
                    goal=getattr(item, "goal", None),
                )
            )
        return rows
    def _map_atp_result(self, result: Any, metrics: Any) -> ATPReplResponse:
        error_code = getattr(result, "error_code", None)
        error_detail = getattr(result, "error_detail", None)
        return ATPReplResponse(
            request_id=getattr(result, "request_id", None),
            success=bool(getattr(result, "success", False)),
            messages=list(getattr(result, "messages", []) or []),
            errors=self._coerce_errors(getattr(result, "errors", None)),
            sorries=self._coerce_sorries(getattr(result, "sorries", None)),
            metrics=self._coerce_metrics(metrics),
            new_state_ref=getattr(result, "new_state_ref", None),
            error_code=error_code,
            error_detail=error_detail,
            harness_diagnostic=build_atp_harness_diagnostic(error_code, error_detail),
        )
    @staticmethod
    def _append_metrics_rows(
        path: str,
        *,
        result: Any,
        response: ATPReplResponse,
        batch_size: int,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rows = response.metrics or [ATPReplMetrics(repl_ms=None, restore_ms=None)]
        with target.open("a", encoding="utf-8") as handle:
            for metric in rows:
                payload = {
                    "ts": now,
                    "request_id": response.request_id,
                    "success": bool(response.success),
                    "repl_ms": metric.repl_ms,
                    "restore_ms": metric.restore_ms,
                    "batch_size": int(batch_size),
                    "error_code": response.error_code,
                    "header_cache_hit": bool(getattr(result, "header_cache_hit", False)),
                    "header_cache_miss": bool(getattr(result, "header_cache_miss", False)),
                }
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
    async def atp_repl(self, payload: ATPReplRequest) -> ATPReplResponse:
        service = await self._ensure_atp_repl_service()
        backend_request = self._build_atp_backend_request(payload)
        result, metrics = await self._maybe_await(service.submit_request_with_metrics(backend_request))
        response = self._map_atp_result(result, metrics)
        metrics_path = os.environ.get("ATP_REPL_METRICS_PATH", "").strip()
        if metrics_path:
            self._append_metrics_rows(metrics_path, result=result, response=response, batch_size=1)
        return response
    async def atp_repl_batch(self, payload: ATPReplBatchRequest) -> ATPReplBatchResponse:
        service = await self._ensure_atp_repl_service()
        backend_requests = [self._build_atp_backend_request(item) for item in payload.requests]
        results, metrics_rows = await self._maybe_await(service.submit_batch_requests(backend_requests))
        result_list = list(results or [])
        metric_list = list(metrics_rows or [])
        if len(result_list) != len(backend_requests) or len(metric_list) != len(backend_requests):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "protocol_batch_mismatch",
                    "message": "ATP REPL batch size mismatch",
                    "detail": {
                        "requests": len(backend_requests),
                        "results": len(result_list),
                        "metrics": len(metric_list),
                    },
                },
            )
        response_rows: list[ATPReplResponse] = []
        metrics_path = os.environ.get("ATP_REPL_METRICS_PATH", "").strip()
        for result, row_metrics in zip(result_list, metric_list):
            response = self._map_atp_result(result, row_metrics)
            response_rows.append(response)
            if metrics_path:
                self._append_metrics_rows(
                    metrics_path,
                    result=result,
                    response=response,
                    batch_size=len(backend_requests),
                )
        return ATPReplBatchResponse(results=response_rows)
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
    def _read_snippet(target: Path, *, head_lines: int, tail_lines: int, max_bytes: int) -> tuple[str, int]:
        max_bytes = max(1, int(max_bytes))
        head_lines = max(0, int(head_lines))
        tail_lines = max(0, int(tail_lines))
        stat = target.stat()
        size = stat.st_size
        # Tail-only snippet: used by focus modal when it explicitly requests head_lines=0.
        if head_lines == 0:
            if tail_lines == 0:
                return "", 0
            tail_text, meta = _TAIL_LINE_INDEX_CACHE.read_tail_text(
                target, tail_lines=tail_lines, max_bytes=max_bytes
            )
            returned_bytes = int(meta.get("returned_bytes", 0))
            return tail_text, returned_bytes
        if size <= max_bytes:
            raw = target.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            return text.replace("\r\n", "\n").replace("\r", "\n"), len(raw)
        # Classic head+tail snippet behavior (used by @read + large file mentions).
        if tail_lines == 0:
            head_bytes = max_bytes
            tail_bytes = 0
        else:
            head_bytes = max(1, max_bytes // 2)
            tail_bytes = max(1, max_bytes - head_bytes)
        with target.open("rb") as handle:
            head_raw = handle.read(head_bytes) if head_bytes > 0 else b""
            tail_raw = b""
            if tail_bytes > 0:
                if size > tail_bytes:
                    handle.seek(max(0, size - tail_bytes))
                tail_raw = handle.read(tail_bytes)
        head_text = head_raw.decode("utf-8", errors="replace")
        tail_text = tail_raw.decode("utf-8", errors="replace")
        head_list = head_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:head_lines]
        tail_list = tail_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[-tail_lines:] if tail_lines else []
        parts: list[str] = []
        if head_list:
            parts.extend(head_list)
        parts.extend(["", "… (truncated) …", ""])
        if tail_list:
            parts.extend(tail_list)
        return "\n".join(parts), len(head_raw) + len(tail_raw)
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        candidate = filename.strip() or "attachment.bin"
        candidate = candidate.replace("\\", "/")
        return os.path.basename(candidate)
