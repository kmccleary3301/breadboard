"""High level orchestration of session lifecycle for the CLI bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Sequence

from fastapi import HTTPException, UploadFile, status

from .events import SessionEvent
from .models import (
    AttachmentHandle,
    AttachmentUploadResponse,
    ModelCatalogEntry,
    ModelCatalogResponse,
    SkillCatalogResponse,
    CTreeSnapshotResponse,
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
from .session_runner import SessionRunner
from ...compilation.v2_loader import load_agent_config
from ...policy_pack import PolicyPack

logger = logging.getLogger(__name__)


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
        self.registry = registry or SessionRegistry()
        self._bridge_chaos = _load_bridge_chaos_metadata()

    async def create_session(self, request: SessionCreateRequest) -> SessionCreateResponse:
        session_id = str(uuid.uuid4())
        metadata = dict(request.metadata or {})
        if self._bridge_chaos:
            metadata.setdefault("bridgeChaos", self._bridge_chaos)
        metadata.setdefault("config_path", request.config_path)
        record = SessionRecord(session_id=session_id, status=SessionStatus.STARTING, metadata=metadata)
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
                record.event_log.append(event)
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
        return await self.registry.list()

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
