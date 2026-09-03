"""Task execution, replay delivery, and turn finalization for CLI sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from breadboard_engine.provider.contracts import (
    strip_provider_exchange_completion_sentinels,
    strip_public_completion_sentinel_tree,
)
from breadboard_engine.permissions import PermissionAuthority

from .events import EventType, SessionEvent
from .registry import TurnRecord
from .runtime_event_projector import (
    RuntimeEventContract,
    RuntimeProtocolError,
    TranslatedRuntimeEvent,
    _assistant_visible_text,
    _runtime_event_is_session_scoped,
    _safe_runtime_error_code,
    _strip_completion_sentinels,
    _validate_replay_event_payload,
)
from .session_control import _PauseAwareControlQueue

logger = logging.getLogger(__name__)


class TaskExecutionHost(Protocol):
    """Explicit host port retained by task execution."""

    session: Any
    request: Any
    registry: Any
    _agent: Any
    _stop_event: asyncio.Event
    _profile_timing_enabled: bool
    _mode: Optional[str]
    _todo_enabled: bool
    _model_override: Optional[str]
    _product_tool_completions: Dict[str, int]
    _published_events: int
    _ctree_last_node: Optional[Dict[str, Any]]
    _ctree_snapshot_cache: Optional[Dict[str, Any]]
    _permission_queue: Any
    _active_attachment_capabilities: Dict[str, Dict[str, Any]]
    permission_authority: PermissionAuthority
    _active_input_media: List[Dict[str, str]]
    _active_bridge_timing_context: Optional[Dict[str, float]]

    def _persist_metadata_snapshot_threadsafe(self) -> None: ...
    async def publish_event_async(
        self, event_type: EventType, payload: Dict[str, Any], **kwargs: Any
    ) -> None: ...
    def publish_event(
        self, event_type: EventType, payload: Dict[str, Any], **kwargs: Any
    ) -> None: ...
    def _translate_runtime_event(
        self, event_type: str, payload: Dict[str, Any], turn: Optional[int]
    ) -> Optional[TranslatedRuntimeEvent]: ...
    def _record_product_observation(
        self,
        family: Optional[str],
        payload: Dict[str, Any],
        *,
        message_projection: bool = False,
        trajectory_id: str | None = None,
    ) -> None: ...
    def _apply_model_override(self) -> bool: ...
    def _install_control_queue(self, queue: Any) -> None: ...
    async def _enqueue_event_async(self, event: SessionEvent) -> None: ...


PermissionProjection = Callable[[str, Dict[str, Any]], Optional[List[Dict[str, Any]]]]


class TaskExecutionOwner:
    """Owns replay/task execution and durable turn terminalization."""

    def __init__(
        self,
        runner: TaskExecutionHost,
        *,
        permission_projection: Optional[PermissionProjection] = None,
    ) -> None:
        self._runner = runner
        self._permission_projection = permission_projection

    def _project_pending_permissions(
        self, event_type: str, payload: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        projection = self._permission_projection
        if projection is None:
            return None
        return projection(event_type, payload)

    def parse_replay_path(self, task_text: str) -> Optional[Path]:
        text = (task_text or "").strip()
        if not text:
            return None
        while text.startswith("<system-reminder>"):
            closing = text.find("</system-reminder>")
            if closing < 0:
                return None
            text = text[closing + len("</system-reminder>") :].lstrip()
        path_text: Optional[str] = None
        if text.startswith("replay:"):
            path_text = text[len("replay:") :].splitlines()[0].strip()
        elif text.startswith("@replay") or text.startswith("/replay"):
            command = text.splitlines()[0]
            parts = command.split(maxsplit=1)
            if len(parts) == 2:
                path_text = parts[1].strip()
        if not path_text:
            return None
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        return path

    async def maybe_publish_todo_snapshot(
        self, workspace_dir: Optional[Path], *, call_id: str
    ) -> None:
        runner = self._runner
        if not runner._todo_enabled or not workspace_dir:
            return
        envelope = self.load_todo_envelope_from_disk(workspace_dir)
        if envelope is None:
            return
        runner.session.metadata["todo_last_update"] = envelope
        runner._persist_metadata_snapshot_threadsafe()
        await runner.publish_event_async(
            EventType.TOOL_RESULT,
            {"call_id": call_id, "todo": envelope},
        )

    def require_execution_correlation(
        self, input_id: Optional[str], turn_id: Optional[str]
    ) -> Dict[str, str]:
        runner = self._runner
        if not isinstance(input_id, str) or not input_id.strip():
            raise RuntimeProtocolError("runtime_protocol_error")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise RuntimeProtocolError("runtime_protocol_error")
        turn = runner.session.turns_by_id.get(turn_id)
        if (
            turn is None
            or turn.input_id != input_id
            or turn.terminal_outcome is not None
            or runner.session.active_turn_id != turn_id
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
        return {"input_id": input_id, "turn_id": turn_id}

    async def execute_replay_task(
        self,
        task_text: str,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        runner = self._runner
        replay_path = self.parse_replay_path(task_text)
        if replay_path is None:
            raise ValueError("replay task missing path (expected replay:<path>)")
        correlation = self.require_execution_correlation(input_id, turn_id)
        if not replay_path.exists():
            raise FileNotFoundError(f"replay fixture not found: {replay_path}")

        prepared_events: list[TranslatedRuntimeEvent] = []
        prepared_delays: list[int] = []
        completion_summary: Dict[str, Any] = {
            "completed": True,
            "reason": "replay",
        }
        seen_completion = False
        seen_run_finished = False
        allowed_entry_fields = {
            "type",
            "event_type",
            "eventType",
            "payload",
            "data",
            "delay_ms",
            "delayMs",
            "turn",
        }
        try:
            with replay_path.open("r", encoding="utf-8") as stream:
                for raw_line in stream:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    entry = json.loads(line)
                    if not isinstance(entry, dict) or set(entry) - allowed_entry_fields:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    type_fields = [
                        key
                        for key in ("type", "event_type", "eventType")
                        if key in entry
                    ]
                    payload_fields = [
                        key for key in ("payload", "data") if key in entry
                    ]
                    delay_fields = [
                        key for key in ("delay_ms", "delayMs") if key in entry
                    ]
                    if len(type_fields) != 1 or len(payload_fields) > 1:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    if len(delay_fields) > 1:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    type_raw = entry[type_fields[0]]
                    if not isinstance(type_raw, str) or not type_raw.strip():
                        raise RuntimeProtocolError("runtime_protocol_error")
                    try:
                        event_type = EventType(type_raw.strip())
                    except ValueError:
                        raise RuntimeProtocolError("runtime_protocol_error") from None
                    payload_raw = entry[payload_fields[0]] if payload_fields else {}
                    payload = _validate_replay_event_payload(event_type, payload_raw)
                    delay_ms = entry[delay_fields[0]] if delay_fields else 0
                    if (
                        not isinstance(delay_ms, int)
                        or isinstance(delay_ms, bool)
                        or delay_ms < 0
                    ):
                        raise RuntimeProtocolError("runtime_protocol_error")
                    turn = entry.get("turn")
                    if turn is not None and (
                        not isinstance(turn, int) or isinstance(turn, bool) or turn < 0
                    ):
                        raise RuntimeProtocolError("runtime_protocol_error")
                    if seen_run_finished:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    if seen_completion and event_type is not EventType.RUN_FINISHED:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    if event_type is EventType.COMPLETION:
                        if seen_completion:
                            raise RuntimeProtocolError("runtime_protocol_error")
                        seen_completion = True
                        summary = payload["summary"]
                        completed = summary.get("completed")
                        if completed is not None and completed is not True:
                            raise RuntimeProtocolError("runtime_protocol_error")
                        completion_summary = {
                            **summary,
                            "completed": True,
                            "reason": str(summary.get("reason") or "replay"),
                        }
                    elif event_type is EventType.RUN_FINISHED:
                        if seen_run_finished or not seen_completion:
                            raise RuntimeProtocolError("runtime_protocol_error")
                        if payload.get("completed") not in {None, True}:
                            raise RuntimeProtocolError("runtime_protocol_error")
                        seen_run_finished = True
                    translated = runner._translate_runtime_event(
                        event_type.value, payload, turn
                    )
                    if translated is None or translated[0] is not event_type:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    prepared_events.append(translated)
                    prepared_delays.append(delay_ms)
        except RuntimeProtocolError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeProtocolError("runtime_protocol_error") from None

        meta = (
            runner.session.metadata if isinstance(runner.session.metadata, dict) else {}
        )
        meta = dict(meta)
        meta["replay_fixture"] = {"path": str(replay_path)}
        runner.session.metadata = meta
        await runner.registry.update_metadata(runner.session.session_id, metadata=meta)

        terminal_events: list[TranslatedRuntimeEvent] = []
        published_events = 0
        for (
            event_type,
            raw_payload,
            turn,
            event_contract,
        ), delay_ms in zip(prepared_events, prepared_delays):
            if runner._stop_event.is_set():
                break
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000.0)
            payload = dict(raw_payload)
            if event_type in {
                EventType.ASSISTANT_MESSAGE,
                EventType.ASSISTANT_DELTA,
                EventType.ASSISTANT_MESSAGE_DELTA,
                EventType.ASSISTANT_MESSAGE_END,
                EventType.ASSISTANT_REASONING_DELTA,
                EventType.ASSISTANT_THOUGHT_SUMMARY_DELTA,
            }:
                for field in ("text", "delta", "content", "message"):
                    if field in payload:
                        payload[field] = _strip_completion_sentinels(payload[field])
            if event_type in {EventType.TOOL_RESULT, EventType.TOOL_RESULT_DOT}:
                todo_update = payload.get("todo")
                if isinstance(todo_update, dict):
                    runner.session.metadata["todo_last_update"] = dict(todo_update)
                    await runner.registry.update_metadata(
                        runner.session.session_id,
                        metadata=runner.session.metadata,
                    )
            if event_type in {EventType.COMPLETION, EventType.RUN_FINISHED}:
                payload = strip_public_completion_sentinel_tree(payload)
                terminal_events.append(
                    (
                        event_type,
                        payload,
                        turn,
                        {**event_contract, **correlation},
                    )
                )
            else:
                await runner.publish_event_async(
                    event_type,
                    payload,
                    turn=turn,
                    input_id=correlation["input_id"],
                    turn_id=correlation["turn_id"],
                    classification=event_contract.get("classification"),
                    family=event_contract.get("family"),
                    actor=event_contract.get("actor"),
                    visibility=event_contract.get("visibility"),
                )
            published_events += 1

        if not seen_completion:
            terminal_events.append(
                (
                    EventType.COMPLETION,
                    {
                        "summary": completion_summary,
                        "mode": runner._mode,
                    },
                    None,
                    dict(correlation),
                )
            )
            published_events += 1
        if not seen_run_finished:
            terminal_events.append(
                (
                    EventType.RUN_FINISHED,
                    {
                        "eventCount": published_events,
                        "completed": True,
                        "reason": "replay",
                        "logging_dir": None,
                    },
                    None,
                    dict(correlation),
                )
            )
        return {
            "completion_summary": completion_summary,
            "reward_metrics": None,
            "logging_dir": None,
            "_terminal_events": terminal_events,
        }

    def load_todo_envelope_from_disk(
        self, workspace_dir: Path
    ) -> Optional[Dict[str, Any]]:
        try:
            store = TodoStore(str(workspace_dir), load_existing=True)
            snapshot = store.snapshot()
            return project_store_snapshot_to_tui_envelope(
                snapshot, scope_key="main", scope_label="main"
            )
        except Exception:
            return None

    def execute_task(
        self,
        task_text: str,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        runner = self._runner
        if not runner._agent:
            raise RuntimeError("agent missing")
        execute_started_at = time.monotonic()
        emitted_flags: Dict[Any, bool] = {
            "assistant": False,
            EventType.COMPLETION: False,
            EventType.RUN_FINISHED: False,
        }
        runner._published_events = 0
        runner._product_tool_completions.clear()
        correlation = self.require_execution_correlation(input_id, turn_id)
        terminal_events: list[TranslatedRuntimeEvent] = []
        runtime_event_lock = threading.Lock()
        is_local_agent = bool(getattr(runner._agent, "_local_mode", False))
        # Runtime completion events remain provisional until the owning provider
        # exchange validates and the turn terminal is durably committed.
        defer_terminal_events = True
        event_queue = permission_queue = control_queue = queue_stop = queue_thread = (
            None
        )
        # A remote pump cannot raise into this owner thread; carry failures back
        # across the join boundary before processing any successful completion.
        queue_errors: list[BaseException] = []
        observation_errors: list[BaseException] = []

        def claim_terminal(
            evt_type: EventType,
            evt_payload: Dict[str, Any],
            evt_turn: Optional[int],
            evt_contract: RuntimeEventContract,
        ) -> None:
            with runtime_event_lock:
                if emitted_flags[evt_type]:
                    return
                emitted_flags[evt_type] = True
                event_contract = {**evt_contract, **correlation}
                if defer_terminal_events or evt_type is EventType.RUN_FINISHED:
                    terminal_events.append(
                        (evt_type, evt_payload, evt_turn, event_contract)
                    )
                    return
                runner.publish_event(
                    evt_type,
                    evt_payload,
                    turn=evt_turn,
                    input_id=event_contract["input_id"],
                    turn_id=event_contract["turn_id"],
                    classification=event_contract.get("classification"),
                    family=event_contract.get("family"),
                    actor=event_contract.get("actor"),
                    visibility=event_contract.get("visibility"),
                )

        def handle_runtime_event(
            event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None
        ) -> None:
            if event_type == "ctree_node":
                try:
                    node = (payload or {}).get("node")
                    if isinstance(node, dict):
                        runner._ctree_last_node = dict(node)
                    snapshot = (payload or {}).get("snapshot")
                    if isinstance(snapshot, dict):
                        runner._ctree_snapshot_cache = dict(snapshot)
                except Exception:
                    pass
            elif event_type == "ctree_snapshot":
                try:
                    if isinstance(payload, dict):
                        runner._ctree_snapshot_cache = dict(payload)
                except Exception:
                    pass
            ready_responses = self._project_pending_permissions(
                event_type, dict(payload or {})
            )
            permission_response_event = (
                event_type == "permission_response"
                or event_type == "task_event"
                and payload.get("kind") == "permission_response"
            )
            if permission_response_event and ready_responses == []:
                return
            translated = runner._translate_runtime_event(event_type, payload, turn)
            if not translated:
                return
            evt_type, evt_payload, evt_turn, evt_contract = translated
            try:
                runner._record_product_observation(
                    evt_contract.get("family"),
                    evt_payload,
                    message_projection=(
                        evt_contract.get("family") == "tool.completed"
                        and isinstance(payload.get("message"), dict)
                        and not any(
                            key in payload
                            for key in (
                                "call_id",
                                "error",
                                "result",
                                "status",
                                "success",
                                "tool",
                            )
                        )
                    ),
                    trajectory_id=str(correlation["turn_id"]),
                )
            except BaseException as error:
                with runtime_event_lock:
                    if not observation_errors:
                        observation_errors.append(error)
                raise
            if evt_type in {EventType.COMPLETION, EventType.RUN_FINISHED}:
                claim_terminal(evt_type, evt_payload, evt_turn, evt_contract)
                return
            if evt_type in {
                EventType.ASSISTANT_MESSAGE,
                EventType.ASSISTANT_MESSAGE_START,
                EventType.ASSISTANT_MESSAGE_DELTA,
                EventType.ASSISTANT_MESSAGE_END,
                EventType.ASSISTANT_DELTA,
            }:
                emitted_flags["assistant"] = True
            event_correlation = (
                {} if _runtime_event_is_session_scoped(event_type) else correlation
            )
            runner.publish_event(
                evt_type,
                evt_payload,
                turn=evt_turn,
                **event_correlation,
                classification=evt_contract.get("classification"),
                family=evt_contract.get("family"),
                actor=evt_contract.get("actor"),
                visibility=evt_contract.get("visibility"),
            )
            if permission_response_event and ready_responses:
                for deferred in ready_responses[1:]:
                    deferred_type, deferred_payload = deferred.get(
                        "_runtime_event", (event_type, deferred)
                    )
                    handle_runtime_event(
                        str(deferred_type), dict(deferred_payload), turn=turn
                    )

        remote_stream_enabled = bool(
            os.environ.get("BREADBOARD_ENABLE_REMOTE_STREAM", "")
        )
        if (
            isinstance(runner.request.metadata, dict)
            and "enable_remote_stream" in runner.request.metadata
        ):
            remote_stream_enabled = bool(
                runner.request.metadata.get("enable_remote_stream")
            )
        permission_mode = (
            (
                runner.request.permission_mode
                or runner.session.metadata.get("permission_mode")
                or ""
            )
            .strip()
            .lower()
        )
        interactive_permissions = runner.permission_authority.is_interactive_mode(
            permission_mode
        )
        logger.info(
            "session(%s) task=%s stream=%s local=%s remote_toggle=%s",
            runner.session.session_id,
            task_text[:32].replace("\n", " ") if task_text else "<empty>",
            bool(runner.request.stream),
            is_local_agent,
            remote_stream_enabled,
        )
        if runner._model_override:
            runner._apply_model_override()
        if not is_local_agent and (
            interactive_permissions or (runner.request.stream and remote_stream_enabled)
        ):
            try:
                from ray.util.queue import Queue
            except ImportError:  # pragma: no cover
                Queue = None  # type: ignore[misc]
            if Queue is not None:
                event_queue = Queue()
                queue_stop, queue_thread = self.start_queue_pump(
                    event_queue,
                    handle_runtime_event,
                    errors=queue_errors,
                )
                logger.info(
                    "session(%s) remote event queue initialized",
                    runner.session.session_id,
                )
        if interactive_permissions:
            if is_local_agent:
                import queue as pyqueue

                permission_queue = pyqueue.Queue()
            else:
                try:
                    from ray.util.queue import Queue as RayQueue
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "Ray Queue required for remote permission prompts"
                    ) from exc
                permission_queue = RayQueue()
            runner._permission_queue = permission_queue
        else:
            runner._permission_queue = None
        if is_local_agent:
            import queue as pyqueue

            control_queue = _PauseAwareControlQueue(pyqueue.Queue())
        else:
            try:
                from ray.util.queue import Queue as RayQueue
            except ImportError:  # pragma: no cover
                control_queue = None
            else:
                control_queue = _PauseAwareControlQueue(RayQueue())
        runner._install_control_queue(control_queue)
        start_time = time.time()
        run_task_started_at = time.monotonic()
        run_task_error: BaseException | None = None
        try:
            task_context = {}
            try:
                if isinstance(runner.session.metadata, dict):
                    task_context = dict(
                        runner.session.metadata.get("task_context") or {}
                    )
                    if (
                        "task_type" in runner.session.metadata
                        and "task_type" not in task_context
                    ):
                        task_context["task_type"] = runner.session.metadata.get(
                            "task_type"
                        )
            except Exception:
                task_context = {}
            # The registry-owned ID is authoritative; request metadata cannot retarget
            # credential affinity to another product session.
            task_context["session_id"] = runner.session.session_id
            task_context["input_id"] = correlation["input_id"]
            task_context["turn_id"] = correlation["turn_id"]
            task_context["attachment_capabilities"] = dict(
                runner._active_attachment_capabilities
            )
            task_context["input_media"] = [
                dict(block) for block in runner._active_input_media
            ]
            kernel_emitter_run_dir = None
            kernel_emitter_mode = None
            try:
                meta = (
                    runner.session.metadata
                    if isinstance(runner.session.metadata, dict)
                    else {}
                )
                runtime_records = (
                    meta.get("runtime_records") if isinstance(meta, dict) else None
                )
                runtime_dir = (
                    meta.get("runtime_record_dir") if isinstance(meta, dict) else None
                )
                if runtime_dir:
                    kernel_emitter_run_dir = runtime_dir
                elif isinstance(runtime_records, dict):
                    config_plane_stream = runtime_records.get("config_plane_stream")
                    if config_plane_stream:
                        kernel_emitter_run_dir = str(
                            Path(config_plane_stream).resolve().parents[1]
                        )
                if kernel_emitter_run_dir:
                    from breadboard_engine.runtime.kernel_emitter import (
                        primitive_emission_mode,
                    )

                    kernel_emitter_mode = primitive_emission_mode("strict")
            except Exception:
                kernel_emitter_run_dir = None
                kernel_emitter_mode = None
            result = runner._agent.run_task(  # type: ignore[call-arg]
                task_text,
                max_iterations=runner.request.max_steps,
                stream=runner.request.stream,
                event_emitter=handle_runtime_event if is_local_agent else None,
                event_queue=event_queue,
                permission_queue=permission_queue,
                control_queue=control_queue,
                context=task_context if task_context else None,
                kernel_emitter_run_dir=(
                    str(kernel_emitter_run_dir) if kernel_emitter_run_dir else None
                ),
                kernel_emitter_mode=(
                    str(kernel_emitter_mode) if kernel_emitter_mode else None
                ),
            )
            run_task_completed_at = time.monotonic()
        except BaseException as error:
            run_task_error = error
            raise
        finally:
            runner._permission_queue = None
            runner._install_control_queue(None)
            if queue_stop:
                queue_stop.set()
                if event_queue is not None:
                    try:
                        event_queue.put((None, None, None))
                    except Exception:  # pragma: no cover
                        pass
            if queue_thread:
                queue_thread.join()
            if event_queue is not None:
                try:
                    self.drain_event_queue(event_queue, handle_runtime_event)
                except BaseException:
                    if run_task_error is None:
                        raise
                    logger.exception("Runtime event drain failed after task failure")
            if (observation_errors or queue_errors) and run_task_error is None:
                source_error = (
                    observation_errors[0] if observation_errors else queue_errors[0]
                )
                raise RuntimeError("runtime event persistence failed") from source_error
        elapsed_ms = int((time.time() - start_time) * 1000)
        after_queue_drain_at = time.monotonic()
        expected_provider_correlation = {
            "session_id": runner.session.session_id,
            "input_id": correlation["input_id"],
            "turn_id": correlation["turn_id"],
        }
        raw_provider_exchanges = result.get("provider_exchanges", [])
        if not isinstance(raw_provider_exchanges, list):
            raise RuntimeProtocolError("runtime_protocol_error")
        provider_exchanges: list[Dict[str, Any]] = []
        exchange_ids: set[str] = set()
        try:
            for raw_exchange in raw_provider_exchanges:
                exchange = strip_provider_exchange_completion_sentinels(raw_exchange)
                if (
                    exchange["correlation"] != expected_provider_correlation
                    or exchange["exchange_id"] in exchange_ids
                ):
                    raise RuntimeProtocolError("runtime_protocol_error")
                exchange_ids.add(exchange["exchange_id"])
                provider_exchanges.append(exchange)
            raw_provider_exchange = result.get("provider_exchange")
            provider_exchange = (
                strip_provider_exchange_completion_sentinels(raw_provider_exchange)
                if raw_provider_exchange is not None
                else None
            )
        except RuntimeProtocolError:
            raise
        except (TypeError, ValueError):
            raise RuntimeProtocolError("runtime_protocol_error") from None
        if provider_exchange is not None:
            if provider_exchange["correlation"] != expected_provider_correlation:
                raise RuntimeProtocolError("runtime_protocol_error")
            if provider_exchanges:
                if provider_exchange != provider_exchanges[-1]:
                    raise RuntimeProtocolError("runtime_protocol_error")
            else:
                provider_exchanges.append(provider_exchange)
        elif provider_exchanges:
            provider_exchange = provider_exchanges[-1]
        if (
            provider_exchange is not None
            and provider_exchange["terminal"]["kind"] != "done"
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
        if provider_exchange is not None:
            result["provider_exchange"] = provider_exchange
            result["provider_exchanges"] = provider_exchanges
        completion = strip_public_completion_sentinel_tree(
            result.get("completion_summary") or {}
        )
        if not isinstance(completion, dict):
            completion = {}
        reward = result.get("reward_metrics_payload") or {}
        messages = result.get("messages")
        fallback_assistant_emitted = False
        if not emitted_flags["assistant"] and isinstance(messages, list):
            for entry in reversed(messages):
                if isinstance(entry, dict) and entry.get("role") == "assistant":
                    content = _strip_completion_sentinels(entry.get("content", ""))
                    text = _assistant_visible_text(content)
                    runner.publish_event(
                        EventType.ASSISTANT_MESSAGE,
                        {
                            "text": text,
                            "message": {**entry, "content": content},
                            "source": "fallback",
                        },
                        **correlation,
                    )
                    fallback_assistant_emitted = True
                    break
        if not emitted_flags["assistant"] and not fallback_assistant_emitted:
            final_message = _strip_completion_sentinels(
                completion.get("final_message")
                if isinstance(completion, dict)
                else None
            )
            if isinstance(final_message, str) and final_message:
                runner.publish_event(
                    EventType.ASSISTANT_MESSAGE,
                    {
                        "text": final_message,
                        "message": {
                            "role": "assistant",
                            "content": final_message,
                            "source": "completion_summary",
                        },
                        "source": "completion_summary",
                    },
                    **correlation,
                    visibility="transcript",
                )
        after_fallback_emit_at = time.monotonic()
        logging_dir = result.get("logging_dir") or result.get("run_dir")
        usage_payload = self.extract_usage_metrics(
            result, logging_dir, elapsed_ms=elapsed_ms
        )
        completion_payload: Dict[str, Any] = {
            "summary": completion,
            "mode": runner._mode,
        }
        if runner._profile_timing_enabled:
            provider_timing = None
            candidate = result.get("provider_runtime_timing")
            if isinstance(candidate, dict):
                provider_timing = dict(candidate)
            elif isinstance(result.get("provider_finish_meta"), dict):
                nested = result["provider_finish_meta"].get("provider_runtime_timing")
                if isinstance(nested, dict):
                    provider_timing = dict(nested)
            completion_payload["bridge_timing"] = {
                "execute_task_total_seconds": round(
                    time.monotonic() - execute_started_at, 6
                ),
                "run_task_seconds": round(
                    run_task_completed_at - run_task_started_at, 6
                ),
                "post_run_task_queue_drain_seconds": round(
                    after_queue_drain_at - run_task_completed_at, 6
                ),
                "post_queue_drain_to_completion_payload_seconds": round(
                    after_fallback_emit_at - after_queue_drain_at, 6
                ),
                "published_event_count_before_completion": runner._published_events,
                "provider_runtime_timing": provider_timing,
                **dict(runner._active_bridge_timing_context or {}),
            }
        if usage_payload:
            completion_payload["usage"] = usage_payload
        claim_terminal(EventType.COMPLETION, completion_payload, None, {})
        after_completion_publish_at = time.monotonic()
        if reward:
            runner.publish_event(
                EventType.REWARD_UPDATE, {"summary": reward}, **correlation
            )
        if logging_dir:
            runner.publish_event(
                EventType.LOG_LINK, {"url": f"file://{logging_dir}"}, **correlation
            )
        logger.info(
            "session(%s) task complete events=%s logging_dir=%s",
            runner.session.session_id,
            runner._published_events,
            logging_dir,
        )
        finish_payload = {
            "eventCount": runner._published_events + 1,
            "steps": completion.get("steps_taken") or result.get("steps_taken"),
            "completed": bool(completion.get("completed")),
            "reason": completion.get("reason") or completion.get("exit_kind"),
            "logging_dir": logging_dir,
        }
        if usage_payload:
            finish_payload["usage"] = usage_payload
        if runner._profile_timing_enabled:
            finish_payload["bridge_timing"] = {
                "completion_event_publish_seconds": round(
                    after_completion_publish_at - after_fallback_emit_at, 6
                ),
                "post_completion_to_run_finished_seconds": round(
                    time.monotonic() - after_completion_publish_at, 6
                ),
            }
        claim_terminal(EventType.RUN_FINISHED, finish_payload, None, {})
        turn_completion_payload: Dict[str, Any] = {}
        if provider_exchange is not None:
            terminal = provider_exchange.get("terminal")
            exchange_id = provider_exchange.get("exchange_id")
            schema_version = provider_exchange.get("schema_version")
            if (
                isinstance(exchange_id, str)
                and exchange_id
                and schema_version == "bb.provider_exchange.v2"
            ):
                turn_completion_payload["exchange_ref"] = {
                    "exchange_id": exchange_id,
                    "schema_version": schema_version,
                }
            if isinstance(terminal, dict) and terminal.get("kind") == "done":
                finish_reason = terminal.get("finish_reason")
                output_emitted = terminal.get("output_emitted")
                if isinstance(finish_reason, str):
                    turn_completion_payload["finish_reason"] = finish_reason
                if isinstance(output_emitted, bool):
                    turn_completion_payload["output_emitted"] = output_emitted
                raw_provider_finish = terminal.get("raw_provider_finish")
                if isinstance(raw_provider_finish, str):
                    turn_completion_payload["raw_provider_finish"] = raw_provider_finish
                terminal_usage = terminal.get("usage")
                if isinstance(terminal_usage, dict):
                    turn_completion_payload["usage"] = dict(terminal_usage)
        result_payload = {
            "completion_summary": completion,
            "reward_metrics": reward or None,
            "logging_dir": logging_dir,
            "_terminal_events": terminal_events,
            "_turn_completion_payload": turn_completion_payload,
        }
        if runner._profile_timing_enabled:
            result_payload["bridge_timing"] = dict(
                completion_payload.get("bridge_timing") or {}
            )
        return result_payload

    async def finish_turn(
        self,
        turn: TurnRecord,
        outcome: str,
        *,
        reason: Optional[str] = None,
        error_code: Optional[str] = None,
        completed_payload: Optional[Dict[str, Any]] = None,
        advance_queue: bool = True,
    ) -> bool:
        runner = self._runner
        async with runner.session.admission_lock:
            if turn.terminal_outcome is not None:
                return False
            previous_state = turn.state
            turn.terminal_outcome = outcome
            turn.state = outcome
        if outcome == "completed":
            event_type, payload = (
                EventType.TURN_COMPLETED,
                dict(completed_payload or {}),
            )
        elif outcome == "cancelled":
            event_type, payload = (
                EventType.TURN_CANCELLED,
                {"reason": reason or "user_requested"},
            )
        else:
            event_type, payload = (
                EventType.TURN_FAILED,
                {
                    "error": {
                        "code": _safe_runtime_error_code(
                            error_code, default="turn_execution_failed"
                        )
                    }
                },
            )
        terminal_event = SessionEvent(
            type=event_type,
            session_id=runner.session.session_id,
            payload=payload,
            input_id=turn.input_id,
            turn_id=turn.turn_id,
        )
        dispatcher = getattr(runner.session, "dispatcher_task", None)
        try:
            if dispatcher is not None and not dispatcher.done():
                await runner._enqueue_event_async(terminal_event)
                await runner.session.event_queue.join()
            else:
                async with runner.session.dispatch_lock:
                    previous_event_seq = runner.session.event_seq
                    previous_event_seq_value = terminal_event.seq
                    runner.session.event_seq += 1
                    terminal_event.seq = runner.session.event_seq
                    try:
                        await runner.registry.persist(
                            runner.session, terminal_event=terminal_event
                        )
                    except Exception:
                        runner.session.event_seq = previous_event_seq
                        terminal_event.seq = previous_event_seq_value
                        raise
                    runner.session.event_log.append(terminal_event)
        except Exception:
            async with runner.session.admission_lock:
                if not turn.terminal_resolution_committed:
                    turn.terminal_outcome = None
                    turn.state = previous_state
            raise
        if not turn.terminal_resolution_committed:
            async with runner.session.admission_lock:
                turn.terminal_outcome = None
                turn.state = previous_state
            raise RuntimeError("turn_terminal_persistence_failed")
        if not advance_queue:
            return True
        async with runner.session.admission_lock:
            if runner.session.active_turn_id == turn.turn_id:
                runner.session.active_turn_id = None
            while runner.session.queued_turn_ids:
                next_turn_id = runner.session.queued_turn_ids.popleft()
                next_turn = runner.session.turns_by_id.get(next_turn_id)
                if next_turn is None or next_turn.terminal_outcome is not None:
                    continue
                next_turn.state = "active"
                runner.session.active_turn_id = next_turn.turn_id
                break
            runner.session.turn_admission = (
                runner.session.turn_admission.__class__.ACTIVE
                if runner.session.active_turn_id is not None
                else runner.session.turn_admission.__class__.IDLE
            )
        return True

    def start_queue_pump(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
        *,
        errors: Optional[List[BaseException]] = None,
    ) -> tuple[Any, Any]:
        import threading
        from queue import Empty

        stop_signal = threading.Event()

        def runner() -> None:
            while not stop_signal.is_set():
                try:
                    item = event_queue.get(timeout=0.1)
                except Empty:
                    continue
                if not item:
                    continue
                try:
                    event_type, payload, turn = item
                except ValueError:
                    continue
                if event_type is None:
                    break
                try:
                    handle_event(event_type, payload, turn=turn)
                except BaseException as error:
                    if errors is not None:
                        errors.append(error)
                    stop_signal.set()
                    return

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return stop_signal, thread

    def drain_event_queue(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
    ) -> None:
        runner = self._runner
        from queue import Empty

        while True:
            try:
                item = event_queue.get_nowait()
            except Empty:
                break
            if not item:
                continue
            try:
                event_type, payload, turn = item
            except ValueError:
                continue
            if event_type is None:
                continue
            handle_event(event_type, payload, turn=turn)
        logger.info(
            "session(%s) published %s events",
            runner.session.session_id,
            runner._published_events,
        )

    def load_run_summary(self, logging_dir: Optional[str]) -> Optional[Dict[str, Any]]:
        if not logging_dir:
            return None
        try:
            run_path = Path(logging_dir) / "meta" / "run_summary.json"
            if not run_path.exists():
                return None
            return json.loads(run_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def normalize_usage_payload(
        self, usage: Dict[str, Any], *, latency_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        if not isinstance(usage, dict):
            return {}

        def _to_int(value: Any) -> int:
            try:
                return int(value)
            except Exception:
                return 0

        def _to_float(value: Any) -> Optional[float]:
            try:
                return float(value)
            except Exception:
                return None

        prompt_tokens = _to_int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        completion_tokens = _to_int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        total_tokens = _to_int(
            usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        )
        cache_read = _to_int(
            usage.get("cache_read_tokens") or usage.get("cache_read") or 0
        )
        cache_write = _to_int(
            usage.get("cache_write_tokens") or usage.get("cache_write") or 0
        )
        cost_usd = _to_float(
            usage.get("cost_usd") or usage.get("cost") or usage.get("total_cost")
        )
        latency_ms_val = _to_int(usage.get("latency_ms") or 0)
        if not latency_ms_val:
            latency_s = _to_float(
                usage.get("latency_s") or usage.get("latency_seconds")
            )
            if latency_s is not None:
                latency_ms_val = int(latency_s * 1000)
        if not latency_ms_val and latency_ms is not None:
            latency_ms_val = int(latency_ms)
        normalized: Dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        if cache_read:
            normalized["cache_read_tokens"] = cache_read
        if cache_write:
            normalized["cache_write_tokens"] = cache_write
        if cost_usd is not None:
            normalized["cost_usd"] = cost_usd
        if latency_ms_val:
            normalized["latency_ms"] = latency_ms_val
        return normalized

    def usage_from_run_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        diagnostics = summary.get("turn_diagnostics")
        if not isinstance(diagnostics, list):
            return {}
        totals: Dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        latency_total = 0.0
        cost_total = 0.0
        saw_usage = False
        for entry in diagnostics:
            if not isinstance(entry, dict):
                continue
            usage = entry.get("usage")
            if isinstance(usage, dict):
                saw_usage = True
                totals["prompt_tokens"] += int(
                    usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                )
                totals["completion_tokens"] += int(
                    usage.get("completion_tokens") or usage.get("output_tokens") or 0
                )
                total_tokens = usage.get("total_tokens")
                if total_tokens is None:
                    total_tokens = (
                        usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                    ) + (
                        usage.get("completion_tokens")
                        or usage.get("output_tokens")
                        or 0
                    )
                totals["total_tokens"] += int(total_tokens or 0)
                totals["cache_read_tokens"] += int(
                    usage.get("cache_read_tokens") or usage.get("cache_read") or 0
                )
                totals["cache_write_tokens"] += int(
                    usage.get("cache_write_tokens") or usage.get("cache_write") or 0
                )
                cost_value = usage.get("cost_usd") or usage.get("cost")
                if isinstance(cost_value, (int, float)):
                    cost_total += float(cost_value)
            latency_value = entry.get("latency_seconds") or entry.get("latency_s")
            if isinstance(latency_value, (int, float)):
                latency_total += float(latency_value)
        if not saw_usage:
            return {}
        totals["total_tokens"] = totals["total_tokens"] or (
            totals["prompt_tokens"] + totals["completion_tokens"]
        )
        normalized = self.normalize_usage_payload(totals)
        if latency_total:
            normalized["latency_ms"] = int(latency_total * 1000)
        if cost_total:
            normalized["cost_usd"] = cost_total
        return normalized

    def extract_usage_metrics(
        self,
        result: Dict[str, Any],
        logging_dir: Optional[str],
        *,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        for key in ("usage", "usage_summary", "usage_metrics"):
            usage = result.get(key)
            if isinstance(usage, dict):
                normalized = self.normalize_usage_payload(usage, latency_ms=elapsed_ms)
                if normalized:
                    return normalized
        summary = self.load_run_summary(logging_dir)
        if summary:
            normalized = self.usage_from_run_summary(summary)
            if normalized:
                if elapsed_ms and not normalized.get("latency_ms"):
                    normalized["latency_ms"] = int(elapsed_ms)
                return normalized
        if elapsed_ms:
            return {"latency_ms": int(elapsed_ms)}
        return {}
