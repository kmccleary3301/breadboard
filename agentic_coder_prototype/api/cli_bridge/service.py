"""High level orchestration of session lifecycle for the CLI bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, Optional, Sequence

from fastapi import HTTPException, UploadFile, status

from .events import (
    OVERFLOW_RECOVERY_ACTION,
    REPLAY_RETENTION_MAX_AGE_MS,
    REPLAY_RETENTION_MAX_EVENTS,
    SNAPSHOT_RECOVERY_ACTION,
    EventType,
    SessionEvent,
    replay_retention_facts,
)
from .models import (
    BeginControlDrainRequest,
    ClientLeaseRequest,
    ClientRegisterRequest,
    ClientRegistrationResponse,
    DrainControlRequest,
    DrainControlResponse,
    GracefulControlResultRequest,
    HardSignalDecisionRequest,
    OwnerAcquireRequest,
    OwnerLeaseRequest,
    OwnerLeaseResponse,
    ATPReplBatchRequest,
    ATPReplBatchResponse,
    ATPReplError,
    ATPReplMetrics,
    ATPReplRequest,
    ATPReplResponse,
    ATPReplSorry,
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
    SessionTurnCancelRequest,
    SessionTurnCancelResponse,
)
from .engine_identity_config import (
    P30_SESSION_SCHEMA_SHA256,
    get_engine_process_identity,
    get_launch_bootstrap_verifier,
    p30_session_schema_sha256,
)
from .atp_diagnostics import build_atp_harness_diagnostic
from .registry import (
    SessionRecord,
    SessionRecordDeletedError,
    SessionRegistry,
    SubscriberState,
    cancellation_body_digest,
    identity_digest,
    submission_body_digest,
)
from .session_runner import SessionRunner, TurnContractConflict
from .tail_index import _TAIL_LINE_INDEX_CACHE
from ...compilation.v2_loader import load_agent_config
from ...compilation.effective_operation_policy import policy_pack_for_config_authority
from .runtime_emission import default_runtime_record_root, emit_session_start_records, primitive_emission_enabled
from ...provider import runtime_codex as runtime_codex_module
from ...provider_routing import provider_router

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


def _env_flag(name: str) -> bool:
    return (os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"})


@dataclass(frozen=True)
class SessionContractReadiness:
    ready: bool
    reason: str


@dataclass(frozen=True)
class PreparedEventStream:
    record: SessionRecord
    queue: "asyncio.Queue[Optional[SessionEvent]]"
    subscriber: SubscriberState


class SessionService:
    """Facade that coordinates the registry, runners, and FastAPI endpoints."""

    def __init__(
        self,
        registry: SessionRegistry | None = None,
        *,
        state_root: str | Path | None = None,
        subscriber_queue_maxsize: int | None = None,
    ) -> None:
        configured_state_root = (
            state_root
            or os.environ.get("BREADBOARD_SESSION_STATE_ROOT")
            or (default_runtime_record_root() / "session_state")
        )
        self.registry = registry or SessionRegistry(
            configured_state_root,
            process_identity=get_engine_process_identity(),
            bootstrap_verifier=get_launch_bootstrap_verifier(),
        )
        self._subscriber_queue_maxsize = max(
            1,
            subscriber_queue_maxsize
            if subscriber_queue_maxsize is not None
            else REPLAY_RETENTION_MAX_EVENTS + 2,
        )
        self._bridge_chaos = _load_bridge_chaos_metadata()
        self._atp_repl_enabled = _env_flag("ATP_REPL_ENABLE") or _env_flag("ATP_REPL_ROUTE")
        self._atp_repl_service: Any | None = None
        self._atp_service_initialized = False
        self._atp_runtime_capabilities: dict[str, Any] = {}

    def p30_session_contract_readiness(
        self,
        contract_descriptor: dict[str, Any],
    ) -> SessionContractReadiness:
        http_contract = contract_descriptor.get("http")
        if not isinstance(http_contract, dict) or http_contract.get("missing_routes"):
            return SessionContractReadiness(
                ready=False,
                reason="session_contract_missing",
            )
        if p30_session_schema_sha256(contract_descriptor) != P30_SESSION_SCHEMA_SHA256:
            return SessionContractReadiness(
                ready=False,
                reason="session_contract_mismatch",
            )
        return SessionContractReadiness(ready=True, reason="ready")

    async def acquire_owner(
        self,
        request: OwnerAcquireRequest,
        *,
        owner_credential: bytearray,
        bootstrap_credential: bytearray | None = None,
    ) -> OwnerLeaseResponse:
        return await self.registry.acquire_owner(
            request,
            owner_credential=owner_credential,
            bootstrap_credential=bootstrap_credential,
        )

    async def renew_owner(
        self,
        request: OwnerLeaseRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        return await self.registry.renew_owner(
            request,
            owner_credential=owner_credential,
        )

    async def release_owner(
        self,
        request: OwnerLeaseRequest,
        *,
        owner_credential: bytearray,
    ) -> OwnerLeaseResponse:
        return await self.registry.release_owner(
            request,
            owner_credential=owner_credential,
        )

    async def register_client(
        self,
        request: ClientRegisterRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        return await self.registry.register_client(
            request,
            registration_credential=registration_credential,
        )

    async def renew_client(
        self,
        request: ClientLeaseRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        return await self.registry.renew_client(
            request,
            registration_credential=registration_credential,
        )

    async def detach_client(
        self,
        request: ClientLeaseRequest,
        *,
        registration_credential: bytearray,
    ) -> ClientRegistrationResponse:
        return await self.registry.detach_client(
            request,
            registration_credential=registration_credential,
        )

    async def begin_control_drain(
        self,
        request: BeginControlDrainRequest,
        *,
        owner_credential: bytearray,
        registration_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.begin_control_drain(
            request,
            owner_credential=owner_credential,
            registration_credential=registration_credential,
        )

    async def record_graceful_control(
        self,
        request: GracefulControlResultRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.record_graceful_control(
            request,
            owner_credential=owner_credential,
        )

    async def record_hard_signal_decision(
        self,
        request: HardSignalDecisionRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.record_hard_signal_decision(
            request,
            owner_credential=owner_credential,
        )

    async def rollback_control_drain(
        self,
        request: DrainControlRequest,
        *,
        owner_credential: bytearray,
    ) -> DrainControlResponse:
        return await self.registry.rollback_control_drain(
            request,
            owner_credential=owner_credential,
        )

    async def create_session(self, request: SessionCreateRequest) -> SessionCreateResponse:
        session_id = str(uuid.uuid4())
        metadata = dict(request.metadata or {})
        if self._bridge_chaos:
            metadata.setdefault("bridgeChaos", self._bridge_chaos)
        metadata.setdefault("config_path", request.config_path)
        record = SessionRecord(
            session_id=session_id,
            status=SessionStatus.STARTING,
            metadata=metadata,
        )
        runner = SessionRunner(session=record, registry=self.registry, request=request)
        record.runner = runner
        metadata = record.metadata
        await self.registry.admit_session(record, runner)

        if primitive_emission_enabled():
            try:
                emitted_paths = emit_session_start_records(
                    session_id=session_id,
                    request=request,
                )
            except Exception:
                logger.warning("Session start evidence emission failed")
            else:
                metadata.setdefault("runtime_records", emitted_paths)
                metadata.setdefault(
                    "runtime_record_dir",
                    str(default_runtime_record_root() / session_id),
                )
        await self._maybe_prewarm_request_runtime(request, metadata)
        await self._ensure_dispatcher(record)
        runner.start_execution()
        logger.info("Session %s created", session_id)
        return SessionCreateResponse(
            session_id=session_id,
            status=record.status,
            created_at=record.created_at,
            logging_dir=record.logging_dir,
        )

    async def _maybe_prewarm_request_runtime(
        self,
        request: SessionCreateRequest,
        metadata: Dict[str, Any],
    ) -> None:
        if not self._should_prewarm_request_runtime(metadata):
            return
        try:
            await asyncio.to_thread(self._prewarm_request_runtime_sync, request, metadata)
        except Exception:
            logger.debug("Codex prewarm skipped")

    def _should_prewarm_request_runtime(self, metadata: Dict[str, Any]) -> bool:
        session_kind = str(metadata.get("cli_session_kind") or "").strip().lower()
        return bool(
            metadata.get("non_interactive_cli_session")
            or session_kind == "oneshot"
            or session_kind == "interactive"
            or session_kind == "repl"
        )

    def _prewarm_request_runtime_sync(self, request: SessionCreateRequest, metadata: Dict[str, Any]) -> None:
        config = load_agent_config(request.config_path)
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

    async def prepare_event_stream(
        self,
        session_id: str,
        *,
        replay: bool = False,
        limit: Optional[int] = None,
        from_id: Optional[str] = None,
        include_open_ack: bool = False,
    ) -> PreparedEventStream:
        """Validate and register a stream before response headers are committed."""
        record = await self.ensure_session(session_id)
        await self._ensure_dispatcher(record)
        queue: asyncio.Queue[Optional[SessionEvent]] = asyncio.Queue(
            maxsize=self._subscriber_queue_maxsize
        )
        subscriber = await self._register_subscriber(
            record,
            queue,
            replay=replay,
            limit=limit,
            from_id=from_id,
            include_open_ack=include_open_ack,
        )
        return PreparedEventStream(record=record, queue=queue, subscriber=subscriber)

    async def event_stream(
        self,
        session_id: str,
        *,
        replay: bool = False,
        limit: Optional[int] = None,
        from_id: Optional[str] = None,
        include_open_ack: bool = False,
    ) -> AsyncIterator[SessionEvent]:
        prepared = await self.prepare_event_stream(
            session_id,
            replay=replay,
            limit=limit,
            from_id=from_id,
            include_open_ack=include_open_ack,
        )
        async for event in self.prepared_event_stream(prepared):
            yield event

    async def prepared_event_stream(
        self,
        prepared: PreparedEventStream,
    ) -> AsyncIterator[SessionEvent]:
        try:
            while True:
                event = await prepared.queue.get()
                if event is None:
                    break
                if event.stable_cursor:
                    prepared.subscriber.last_delivered_sequence = event.seq
                    prepared.subscriber.last_delivered_event_id = event.event_id
                yield event
                if event.type is EventType.STREAM_GAP:
                    break
        finally:
            await self._unregister_subscriber(prepared.record, prepared.queue)

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
        include_open_ack: bool = False,
    ) -> SubscriberState:
        replay_enabled = replay or bool(from_id)
        state = SubscriberState(queue=queue)
        async with record.dispatch_lock:
            self._ensure_event_sequence(record)
            self._prune_replay(record)
            if replay and not from_id and record.replay_history_partial:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=self._resume_gap_detail(record, None),
                )
            events = list(record.event_log)
            if from_id:
                start_index = self._resolve_start_index(events, from_id)
                if start_index is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=self._resume_gap_detail(record, from_id),
                    )
                cursor_event = events[start_index - 1]
                state.last_delivered_sequence = cursor_event.seq
                state.last_delivered_event_id = cursor_event.event_id
                events = events[start_index:]
            elif replay_enabled and isinstance(limit, int) and limit > 0:
                events = events[-limit:]
            elif not replay_enabled:
                events = []

            record.subscribers[queue] = state
            if include_open_ack:
                self._enqueue_subscription(
                    record,
                    state,
                    SessionEvent(
                        EventType.STREAM_OPEN,
                        record.session_id,
                        replay_retention_facts(
                            record.event_log,
                            head_sequence=record.event_seq,
                            retained_history_partial=record.replay_history_partial,
                        ),
                        stable_cursor=False,
                    ),
                )
            for event in events:
                self._enqueue_subscription(record, state, event)

            if not replay_enabled and not state.gapped:
                envelope = (
                    record.metadata.get("todo_last_update")
                    if isinstance(record.metadata, dict)
                    else None
                )
                if not isinstance(envelope, dict):
                    runner = getattr(record, "runner", None)
                    workspace_dir = runner.get_workspace_dir() if runner else None
                    if workspace_dir:
                        try:
                            from agentic_coder_prototype.todo import TodoStore
                            from agentic_coder_prototype.todo.projection import (
                                project_store_snapshot_to_tui_envelope,
                            )

                            store = TodoStore(str(workspace_dir), load_existing=True)
                            envelope = project_store_snapshot_to_tui_envelope(
                                store.snapshot(),
                                scope_key="main",
                                scope_label="main",
                            )
                        except Exception:
                            envelope = None
                if isinstance(envelope, dict):
                    self._enqueue_subscription(
                        record,
                        state,
                        SessionEvent(
                            EventType.TOOL_RESULT,
                            record.session_id,
                            {
                                "call_id": f"todo:snapshot:connect:{uuid.uuid4().hex[:8]}",
                                "todo": envelope,
                            },
                            stable_cursor=False,
                        ),
                    )
        return state

    async def _unregister_subscriber(
        self,
        record: SessionRecord,
        queue: "asyncio.Queue[Optional[SessionEvent]]",
    ) -> None:
        async with record.dispatch_lock:
            record.subscribers.pop(queue, None)

    async def _dispatch_events(self, record: SessionRecord) -> None:
        """Persist stable events before any subscriber can observe them."""
        while True:
            event = await record.event_queue.get()
            if event is None:
                break
            persistence_failed = False
            async with self.registry.event_persistence_authority():
                async with record.dispatch_lock:
                    if event.stable_cursor:
                        previous_sequence = record.event_seq
                        original_event_sequence = event.seq
                        previous_event_log = tuple(record.event_log)
                        previous_history_partial = record.replay_history_partial
                        record.event_seq += 1
                        if event.seq is None:
                            event.seq = record.event_seq
                        else:
                            record.event_seq = max(record.event_seq, int(event.seq))
                        record.event_log.append(event)
                        self._prune_replay(record)
                        try:
                            await self.registry.persist(
                                record,
                                terminal_event=event
                                if event.type
                                in {
                                    EventType.TURN_COMPLETED,
                                    EventType.TURN_FAILED,
                                    EventType.TURN_CANCELLED,
                                }
                                else None,
                            )
                        except Exception:
                            logger.error(
                                "Durable event persistence failed for session %s",
                                record.session_id,
                            )
                            record.event_log.clear()
                            record.event_log.extend(previous_event_log)
                            record.event_seq = previous_sequence
                            event.seq = original_event_sequence
                            record.replay_history_partial = previous_history_partial
                            persistence_failed = True
                            for subscriber in list(record.subscribers.values()):
                                self._mark_subscription_gap(
                                    record,
                                    subscriber,
                                    code="durable_persist_failed",
                                    recovery_action=SNAPSHOT_RECOVERY_ACTION,
                                )
                    if not persistence_failed:
                        for subscriber in list(record.subscribers.values()):
                            self._enqueue_subscription(record, subscriber, event)
            if persistence_failed:
                break
        async with record.dispatch_lock:
            for subscriber in list(record.subscribers.values()):
                try:
                    subscriber.queue.put_nowait(None)
                except asyncio.QueueFull:
                    self._mark_subscription_gap(record, subscriber)

    def _enqueue_subscription(
        self,
        record: SessionRecord,
        subscriber: SubscriberState,
        event: SessionEvent,
    ) -> None:
        if subscriber.gapped:
            return
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            self._mark_subscription_gap(record, subscriber)

    def _mark_subscription_gap(
        self,
        record: SessionRecord,
        subscriber: SubscriberState,
        *,
        code: str = "subscriber_overflow",
        recovery_action: str = OVERFLOW_RECOVERY_ACTION,
    ) -> None:
        if subscriber.gapped:
            return
        subscriber.gapped = True
        record.subscribers.pop(subscriber.queue, None)
        while True:
            try:
                subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        replay = replay_retention_facts(
            record.event_log,
            head_sequence=record.event_seq,
            retained_history_partial=record.replay_history_partial,
        )
        subscriber.queue.put_nowait(
            SessionEvent(
                EventType.STREAM_GAP,
                record.session_id,
                {
                    "code": code,
                    "last_safely_delivered_cursor": {
                        "sequence": subscriber.last_delivered_sequence,
                        "event_id": subscriber.last_delivered_event_id,
                    },
                    "recovery": {
                        "action": recovery_action,
                        "snapshot_action": SNAPSHOT_RECOVERY_ACTION,
                    },
                    **replay,
                },
                stable_cursor=False,
            )
        )

    async def list_sessions(self):
        summaries = []
        for record in await self.registry.records():
            async with record.dispatch_lock:
                self._ensure_event_sequence(record)
                self._prune_replay(record)
                summaries.append(record.to_summary())
        return summaries

    async def session_snapshot(self, session_id: str):
        record = await self.ensure_session(session_id)
        async with record.dispatch_lock:
            self._ensure_event_sequence(record)
            self._prune_replay(record)
            return record.to_summary()

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
        runtime_dir_raw = metadata.get("runtime_record_dir")
        runtime_dir = Path(str(runtime_dir_raw)) if runtime_dir_raw else default_runtime_record_root() / session_id
        records_dir = runtime_dir / "records"
        rows: list[dict[str, Any]] = []
        if records_dir.is_dir():
            for path in sorted(records_dir.glob("*.jsonl")):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line_no, line in enumerate(lines, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    row_record = payload.get("record") if isinstance(payload, dict) and isinstance(payload.get("record"), dict) else payload
                    row_schema = None
                    if isinstance(row_record, dict):
                        row_schema = row_record.get("schema_version")
                    if row_schema is None and isinstance(payload, dict):
                        row_schema = payload.get("schema_version")
                    if schema_version and row_schema != schema_version:
                        continue
                    rows.append(
                        {
                            "schema_version": row_schema,
                            "path": str(path),
                            "line": line_no,
                            "record": row_record,
                        }
                    )
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 1000))
        return {
            "session_id": session_id,
            "records": rows[safe_offset : safe_offset + safe_limit],
            "offset": safe_offset,
            "limit": safe_limit,
            "total": len(rows),
        }

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
        # Best-effort: scan the in-memory event log for the most recent limits_update.
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


    def _ensure_event_sequence(self, record: SessionRecord) -> None:
        seq = record.event_seq
        for event in record.event_log:
            if event.seq is None:
                seq += 1
                event.seq = seq
            else:
                seq = max(seq, int(event.seq))
        record.event_seq = seq

    def _prune_replay(self, record: SessionRecord) -> None:
        cutoff = int(time.time() * 1000) - REPLAY_RETENTION_MAX_AGE_MS
        while record.event_log and (
            len(record.event_log) > REPLAY_RETENTION_MAX_EVENTS
            or int(record.event_log[0].created_at) < cutoff
        ):
            record.event_log.popleft()
            record.replay_history_partial = True

    def _resume_gap_detail(self, record: SessionRecord, from_id: Optional[str]) -> dict[str, Any]:
        replay = replay_retention_facts(
            record.event_log,
            head_sequence=record.event_seq,
            retained_history_partial=record.replay_history_partial,
        )
        last_retained_sequence = record.event_log[-1].seq if record.event_log else None
        return {
            "message": "resume cursor is outside the retained replay window",
            "code": "resume_window_exceeded",
            "last_event_id": from_id,
            "first_retained_sequence": replay["earliestRetainedSequence"],
            "last_retained_sequence": last_retained_sequence,
            "recovery": {
                "action": SNAPSHOT_RECOVERY_ACTION,
                "snapshot": {"method": "GET", "resource": "session_snapshot"},
                "reconnect": {"method": "GET", "resource": "session_event_stream", "cursor": None},
            },
            **replay,
        }

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
        record = await self.registry.get(session_id)
        if record is not None:
            async with record.lifecycle_lock:
                record.deleting = True
                runner: Optional[SessionRunner] = getattr(record, "runner", None)
                if runner:
                    await runner.stop()
                dispatcher = record.dispatcher_task
                if dispatcher is not None and not dispatcher.done():
                    await record.event_queue.put(None)
                    await dispatcher
                await self.registry.update_status(session_id, SessionStatus.STOPPED)
                await self.registry.delete(session_id)

    async def send_input(self, session_id: str, payload: SessionInputRequest) -> SessionInputResponse:
        record = await self.ensure_session(session_id)
        async with record.lifecycle_lock:
            if record.deleting:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
            return await self._send_input_for_record(record, payload)

    async def _send_input_for_record(
        self,
        record: SessionRecord,
        payload: SessionInputRequest,
    ) -> SessionInputResponse:
        attachments = tuple(
            item.strip()
            for item in (payload.attachments or [])
            if isinstance(item, str) and item.strip()
        )
        key_digest = identity_digest(payload.client_message_id)
        existing = record.submissions_by_key.get(payload.client_message_id)
        if existing is None:
            existing = record.submissions_by_key_digest.get(key_digest)
        if existing is not None:
            expected_body_digest = existing.body_digest or submission_body_digest(
                existing.content,
                existing.attachments,
            )
            if expected_body_digest != submission_body_digest(payload.content, attachments):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "input_idempotency_conflict",
                        "message": "client_message_id was already used with a different input body",
                        "turn_id": existing.turn_id,
                    },
                )
            return SessionInputResponse(
                client_message_id=payload.client_message_id,
                input_id=existing.input_id,
                turn_id=existing.turn_id,
                disposition="deduplicated",
                original_disposition=existing.original_disposition,
            )

        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        try:
            response = await runner.submit_input(
                payload.content,
                attachments=list(attachments),
                client_message_id=payload.client_message_id,
            )
            turn = record.turns_by_id.get(response.turn_id)
            if turn is not None:
                turn.body_digest = submission_body_digest(payload.content, attachments)
            await self.registry.persist(record)
            return response
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except TurnContractConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": exc.code, "message": str(exc), "turn_id": exc.turn_id},
            ) from exc
        except SessionRecordDeletedError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    async def cancel_turn(
        self,
        session_id: str,
        turn_id: str,
        payload: SessionTurnCancelRequest,
    ) -> SessionTurnCancelResponse:
        record = await self.ensure_session(session_id)
        async with record.lifecycle_lock:
            if record.deleting:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
            return await self._cancel_turn_for_record(record, turn_id, payload)

    async def _cancel_turn_for_record(
        self,
        record: SessionRecord,
        turn_id: str,
        payload: SessionTurnCancelRequest,
    ) -> SessionTurnCancelResponse:
        key_digest = identity_digest(payload.cancellation_request_key)
        existing = record.cancellations_by_key.get(payload.cancellation_request_key)
        if existing is None:
            existing = record.cancellations_by_key_digest.get(key_digest)
        if existing is not None:
            expected_body_digest = existing.body_digest or cancellation_body_digest(
                existing.turn_id,
                existing.reason,
            )
            if expected_body_digest != cancellation_body_digest(turn_id, payload.reason):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "cancellation_idempotency_conflict",
                        "message": (
                            "cancellation_request_key was already used with a different target or body"
                        ),
                        "turn_id": existing.turn_id,
                    },
                )
            return SessionTurnCancelResponse(
                cancellation_request_id=existing.cancellation_request_id,
                cancellation_request_key=payload.cancellation_request_key,
                input_id=existing.input_id,
                turn_id=existing.turn_id,
                disposition="deduplicated",
                original_disposition=existing.original_disposition,
            )

        turn = record.turns_by_id.get(turn_id)
        runner: Optional[SessionRunner] = getattr(record, "runner", None)
        if not runner:
            if turn is not None and turn.terminal_outcome is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "turn_already_terminal",
                        "message": "target turn already has a terminal outcome",
                        "turn_id": turn_id,
                    },
                )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not active")
        try:
            response = await runner.cancel_turn(
                turn_id,
                cancellation_request_key=payload.cancellation_request_key,
                reason=payload.reason,
            )
            await self.registry.persist(record)
            return response
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except TurnContractConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": exc.code, "message": str(exc), "turn_id": exc.turn_id},
            ) from exc
        except SessionRecordDeletedError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

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
