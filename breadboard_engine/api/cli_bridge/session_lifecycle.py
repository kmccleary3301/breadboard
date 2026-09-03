"""Session lifecycle orchestration for the CLI bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from breadboard_engine.checkpointing.checkpoint_manager import CheckpointManager
from breadboard_engine.guardrails import GuardrailCoordinator

from .events import EventType
from .models import SessionStatus
from .runtime_event_projector import RuntimeProtocolError, _safe_runtime_error_code
from .task_execution import TaskExecutionOwner

logger = logging.getLogger(__name__)


class SessionLifecycleHost(Protocol):
    """Explicit host port used by the session lifecycle owner."""

    session: Any
    registry: Any
    request: Any
    _stop_event: asyncio.Event
    _resume_event: asyncio.Event
    _input_queue: asyncio.Queue[Optional[Dict[str, Any]]]
    _product_session_lock: Any
    _profile_timing_enabled: bool
    _todo_enabled: bool
    _workspace_path: Optional[Path]
    _checkpoint_manager: Any
    _closed: bool
    _accepted_task_texts: List[str]
    _active_bridge_timing_context: Optional[Dict[str, float]]

    def prepare_runtime_config(self) -> Dict[str, Any]: ...
    def get_skill_catalog(self) -> Dict[str, Any]: ...
    async def publish_event_async(
        self, event_type: EventType, payload: Dict[str, Any], **kwargs: Any
    ) -> None: ...
    async def _ensure_agent_initialized(self) -> None: ...
    def _format_attachment_helper(self, attachment_ids: Any) -> str: ...
    def transition_product_session(self, transition: str, *args: Any) -> None: ...
    async def _publish_session_failure(self, error_code: str) -> None: ...
    async def _enqueue_termination(self) -> None: ...


@dataclass
class _LifecycleRunState:
    session_started_at: float
    input_inflight: bool = False
    terminal_status: Optional[SessionStatus] = None

class SessionLifecycleOwner:
    """Owns the ordered phases that run one CLI bridge session."""

    def __init__(
        self, host: SessionLifecycleHost, execution: TaskExecutionOwner
    ) -> None:
        self._host = host
        self._execution = execution

    async def run(self) -> None:
        """Run the session through running, setup, execution, and terminal phases."""
        host = self._host
        state = _LifecycleRunState(session_started_at=time.monotonic())
        try:
            await self._mark_running()
            await self._initialize()
            await self._process_inputs(state)
            await self._finalize(state)
        except Exception as exc:  # noqa: BLE001
            await self._fail(state, exc)
        finally:
            host._closed = True
            await host._enqueue_termination()

    async def _mark_running(self) -> None:
        host = self._host
        await host.registry.update_status(
            host.session.session_id, SessionStatus.RUNNING
        )

    async def _initialize(self) -> None:
        host = self._host
        execution = self._execution
        # Safety: never auto-wipe an existing workspace when running interactive sessions
        # via the CLI bridge. The engine historically treated workspaces as disposable
        # sandboxes; for a Claude Code-style experience we must preserve the user's
        # working directory unless explicitly overridden by the caller.
        os.environ.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
        initial_task = (host.request.task or "").strip()
        base_cfg = (
            {}
            if execution.parse_replay_path(initial_task) is not None
            else host.prepare_runtime_config()
        )
        try:
            catalog_payload = host.get_skill_catalog()
            await host.publish_event_async(EventType.SKILLS_CATALOG, catalog_payload)
            selection = (
                (catalog_payload.get("selection") or {})
                if isinstance(catalog_payload, dict)
                else {}
            )
            if selection:
                await host.publish_event_async(
                    EventType.SKILLS_SELECTION, {"selection": selection}
                )
        except Exception:
            pass
        try:
            todo_cfg = GuardrailCoordinator(base_cfg).todo_config()
        except Exception:
            todo_cfg = {"enabled": False}
        host._todo_enabled = bool(todo_cfg.get("enabled"))
        await execution.maybe_publish_todo_snapshot(
            host._workspace_path, call_id="todo:snapshot:init"
        )
        try:
            if host._workspace_path and host._checkpoint_manager is None:
                host._checkpoint_manager = CheckpointManager(host._workspace_path)
                host._checkpoint_manager.create_checkpoint("Session start")
        except Exception:
            host._checkpoint_manager = None
        if initial_task:
            host._accepted_task_texts.append(initial_task)
            initial_turn = host.session.turns_by_id.get(
                host.session.active_turn_id or ""
            )
            if initial_turn is None:
                raise RuntimeProtocolError("runtime_protocol_error")
            host._input_queue.put_nowait(
                {
                    "content": initial_task,
                    "attachments": [],
                    "input_id": initial_turn.input_id,
                    "turn_id": initial_turn.turn_id,
                }
            )

    async def _process_inputs(self, state: _LifecycleRunState) -> None:
        host = self._host
        execution = self._execution
        while not host._stop_event.is_set():
            try:
                next_input = await host._input_queue.get()
                state.input_inflight = True
            except asyncio.CancelledError:  # pragma: no cover - defensive
                break
            if next_input is None:
                host._input_queue.task_done()
                state.input_inflight = False
                break
            await host._resume_event.wait()
            if host._stop_event.is_set():
                host._input_queue.task_done()
                state.input_inflight = False
                break
            task_payload = dict(next_input)
            task_text = str(task_payload.get("content", ""))
            task_input_id = task_payload.get("input_id")
            task_turn_id = task_payload.get("turn_id")
            task_turn = (
                host.session.turns_by_id.get(task_turn_id)
                if isinstance(task_turn_id, str)
                else None
            )
            if task_turn is not None and task_turn.terminal_outcome is not None:
                host._input_queue.task_done()
                state.input_inflight = False
                continue
            if (
                task_turn is not None
                and host.session.active_turn_id != task_turn.turn_id
            ):
                raise RuntimeError("turn queue correlation mismatch")
            task_received_at = time.monotonic()
            if execution.parse_replay_path(task_text) is not None:
                result = await execution.execute_replay_task(
                    task_text,
                    input_id=(
                        task_input_id if isinstance(task_input_id, str) else None
                    ),
                    turn_id=(task_turn_id if isinstance(task_turn_id, str) else None),
                )
                after_execute_task_at = time.monotonic()
            else:
                attachment_ids = task_payload.get("attachments") or []
                await host._ensure_agent_initialized()
                if host._stop_event.is_set():
                    host._input_queue.task_done()
                    state.input_inflight = False
                    break
                after_agent_init_at = time.monotonic()
                accepted_text = task_text
                attachment_text = host._format_attachment_helper(attachment_ids)
                if attachment_text:
                    task_text = f"{task_text.rstrip()}\n\n{attachment_text}"
                if accepted_text.strip():
                    host._accepted_task_texts.append(accepted_text)
                    host._accepted_task_texts = host._accepted_task_texts[-20:]
                if host._profile_timing_enabled:
                    host._active_bridge_timing_context = {
                        "session_to_task_received_seconds": round(
                            task_received_at - state.session_started_at, 6
                        ),
                        "task_received_to_agent_initialized_seconds": round(
                            after_agent_init_at - task_received_at, 6
                        ),
                    }
                result = await asyncio.to_thread(
                    execution.execute_task,
                    task_text,
                    input_id=(
                        task_input_id if isinstance(task_input_id, str) else None
                    ),
                    turn_id=(task_turn_id if isinstance(task_turn_id, str) else None),
                )
                after_execute_task_at = time.monotonic()
                if host._profile_timing_enabled and isinstance(result, dict):
                    timing = result.setdefault("bridge_timing", {})
                    if isinstance(timing, dict):
                        timing.update(
                            {
                                "session_to_task_received_seconds": round(
                                    task_received_at - state.session_started_at, 6
                                ),
                                "task_received_to_agent_initialized_seconds": round(
                                    after_agent_init_at - task_received_at, 6
                                ),
                                "execute_task_wall_seconds": round(
                                    after_execute_task_at - after_agent_init_at, 6
                                ),
                            }
                        )
                host._active_bridge_timing_context = None
            metadata = (
                host.session.metadata if isinstance(host.session.metadata, dict) else {}
            )
            one_shot = bool(
                metadata.get("non_interactive_cli_session")
                or metadata.get("cli_session_kind") == "oneshot"
            )
            completion_summary = result.get("completion_summary") or {}
            completion_reason = str(
                completion_summary.get("reason")
                or completion_summary.get("exit_kind")
                or "turn_execution_failed"
            )
            execution_completed = bool(completion_summary.get("completed"))
            failure_code = _safe_runtime_error_code(
                completion_reason,
                default="runtime_failure",
            )
            turn_was_cancelled = bool(
                task_turn is not None and task_turn.cancellation_requested
            )
            with host._product_session_lock:
                product_session = getattr(host.session, "product_session", None)
                if product_session is None:
                    durable_success = execution_completed or (
                        turn_was_cancelled and not one_shot
                    )
                else:
                    product_state = product_session.read_model.status
                    if turn_was_cancelled:
                        if one_shot and product_state == "running":
                            host.transition_product_session(
                                "cancel",
                                task_turn.cancellation_reason or "user_requested",
                            )
                    elif product_state == "running" and not host._stop_event.is_set():
                        if execution_completed:
                            if one_shot:
                                host.transition_product_session("complete")
                        else:
                            host.transition_product_session(
                                "fail",
                                failure_code,
                                completion_reason,
                            )
                    product_state = product_session.read_model.status
                    durable_success = (
                        product_state not in {"failed", "canceled"}
                        if not one_shot
                        else product_state == "completed"
                    )
            if durable_success:
                try:
                    await host.registry.update_metadata(
                        host.session.session_id,
                        completion_summary=result.get("completion_summary"),
                        reward_summary=result.get("reward_metrics"),
                        logging_dir=result.get("logging_dir"),
                        metadata=host.session.metadata,
                    )
                except Exception:
                    product_status = getattr(
                        getattr(product_session, "read_model", None),
                        "status",
                        None,
                    )
                    if product_status != "completed":
                        raise
                    logger.warning(
                        "Session %s metadata projection failed after durable completion",
                        host.session.session_id,
                    )
                    durable_success = False
            if task_turn is not None:
                if task_turn.cancellation_requested:
                    await execution.finish_turn(
                        task_turn,
                        "cancelled",
                        reason=task_turn.cancellation_reason or "user_requested",
                    )
                elif host._stop_event.is_set():
                    await execution.finish_turn(
                        task_turn, "cancelled", reason=completion_reason
                    )
                elif bool(completion_summary.get("completed")):
                    await execution.finish_turn(
                        task_turn,
                        "completed",
                        completed_payload=result.get("_turn_completion_payload"),
                    )
                else:
                    await execution.finish_turn(
                        task_turn,
                        "failed",
                        reason=completion_reason,
                        error_code=failure_code,
                    )
            if one_shot:
                if turn_was_cancelled:
                    await self.terminalize_admitted_turns(
                        outcome="cancelled",
                        reason=task_turn.cancellation_reason or "user_requested",
                    )
                elif host._stop_event.is_set():
                    await self.terminalize_admitted_turns(
                        outcome="cancelled", reason="stop_requested"
                    )
                elif execution_completed:
                    await self.terminalize_admitted_turns(
                        outcome="cancelled",
                        reason="superseded",
                    )
                else:
                    await self.terminalize_admitted_turns(
                        outcome="failed",
                        reason=completion_reason,
                        error_code=failure_code,
                    )
            if not one_shot and not durable_success:
                if host._stop_event.is_set():
                    await self.terminalize_admitted_turns(
                        outcome="cancelled",
                        reason="stop_requested",
                    )
                else:
                    await self.terminalize_admitted_turns(
                        outcome="failed",
                        reason=completion_reason,
                        error_code=failure_code,
                    )
            if durable_success and not turn_was_cancelled:
                for (
                    event_type,
                    event_payload,
                    event_turn,
                    event_contract,
                ) in result.pop("_terminal_events", ()):
                    await host.publish_event_async(
                        event_type,
                        event_payload,
                        turn=event_turn,
                        input_id=event_contract.get("input_id"),
                        turn_id=event_contract.get("turn_id"),
                        classification=event_contract.get("classification"),
                        family=event_contract.get("family"),
                        actor=event_contract.get("actor"),
                        visibility=event_contract.get("visibility"),
                    )
            after_registry_update_at = time.monotonic()
            if host._profile_timing_enabled and isinstance(result, dict):
                timing = result.setdefault("bridge_timing", {})
                if isinstance(timing, dict):
                    timing["post_execute_registry_update_seconds"] = round(
                        after_registry_update_at - after_execute_task_at, 6
                    )
            host._input_queue.task_done()
            state.input_inflight = False
            if one_shot or not durable_success:
                if host._stop_event.is_set() or turn_was_cancelled:
                    state.terminal_status = SessionStatus.STOPPED
                elif execution_completed:
                    state.terminal_status = SessionStatus.COMPLETED
                else:
                    state.terminal_status = SessionStatus.FAILED
                break

    async def _finalize(self, state: _LifecycleRunState) -> None:
        host = self._host
        if host._stop_event.is_set():
            await self.terminalize_admitted_turns(
                outcome="cancelled", reason="stop_requested"
            )

        product_session = getattr(host.session, "product_session", None)
        if product_session is None:
            metadata = (
                host.session.metadata if isinstance(host.session.metadata, dict) else {}
            )
            legacy_one_shot = bool(
                metadata.get("non_interactive_cli_session")
                or metadata.get("cli_session_kind") == "oneshot"
            )
            final_status = state.terminal_status or (
                SessionStatus.STOPPED
                if host._stop_event.is_set() and not legacy_one_shot
                else SessionStatus.COMPLETED
            )
        else:
            product_state = product_session.read_model.status
            if product_state == "running" and not host._stop_event.is_set():
                host.transition_product_session("complete")
            elif product_state not in {"completed", "failed", "canceled"}:
                host.transition_product_session("cancel", "runtime stopped")
            product_state = product_session.read_model.status
            final_status = {
                "completed": SessionStatus.COMPLETED,
                "failed": SessionStatus.FAILED,
                "canceled": SessionStatus.STOPPED,
            }[product_state]
        await host.registry.update_status(host.session.session_id, final_status)

    async def _fail(self, state: _LifecycleRunState, exc: BaseException) -> None:
        host = self._host
        error_code = _safe_runtime_error_code(
            getattr(exc, "code", None),
            default=(
                "runtime_protocol_error"
                if isinstance(exc, RuntimeProtocolError)
                else "worker_crash"
            ),
        )
        if state.input_inflight:
            host._input_queue.task_done()
            state.input_inflight = False
        logger.error(
            "Session %s failed with code=%s", host.session.session_id, error_code
        )
        try:
            await self.terminalize_admitted_turns(
                outcome="failed", reason=error_code, error_code=error_code
            )
        except Exception:
            logger.error(
                "Session %s could not persist terminal turn events",
                host.session.session_id,
            )
        product_session = getattr(host.session, "product_session", None)
        if product_session is None:
            product_state = "failed"
        else:
            product_state = product_session.read_model.status
            if product_state not in {"completed", "failed", "canceled"}:
                host.transition_product_session(
                    "fail",
                    error_code,
                    "runtime failure",
                )
                product_state = product_session.read_model.status
        final_status = {
            "completed": SessionStatus.COMPLETED,
            "failed": SessionStatus.FAILED,
            "canceled": SessionStatus.STOPPED,
        }.get(product_state, SessionStatus.FAILED)
        try:
            await host.registry.update_status(host.session.session_id, final_status)
        except Exception:
            host.session.status = final_status
            logger.error(
                "Session %s could not persist terminal session status",
                host.session.session_id,
            )
        try:
            await host._publish_session_failure(error_code)
        except Exception:
            logger.error(
                "Session %s could not publish its terminal failure event",
                host.session.session_id,
            )

    async def terminalize_admitted_turns(
        self,
        *,
        outcome: str,
        reason: str,
        error_code: Optional[str] = None,
    ) -> None:
        host = self._host
        execution = self._execution
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError("unsupported bulk terminal outcome")
        async with host.session.admission_lock:
            host._closed = True
            ordered_ids: list[str] = []
            if host.session.active_turn_id:
                ordered_ids.append(host.session.active_turn_id)
            ordered_ids.extend(
                turn_id
                for turn_id in host.session.queued_turn_ids
                if turn_id not in ordered_ids
            )
            ordered_ids.extend(
                turn_id
                for turn_id, turn in host.session.turns_by_id.items()
                if turn.terminal_outcome is None and turn_id not in ordered_ids
            )
        for turn_id in ordered_ids:
            turn = host.session.turns_by_id.get(turn_id)
            if turn is None or turn.terminal_outcome is not None:
                continue
            await execution.finish_turn(
                turn,
                outcome,
                reason=reason,
                error_code=error_code,
                advance_queue=False,
            )
        while True:
            try:
                host._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                host._input_queue.task_done()
        async with host.session.admission_lock:
            host.session.active_turn_id = None
            host.session.queued_turn_ids.clear()
            host.session.turn_admission = host.session.turn_admission.__class__.IDLE
