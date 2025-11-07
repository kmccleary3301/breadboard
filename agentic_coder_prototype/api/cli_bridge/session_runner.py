"""Session execution helpers for the CLI bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Callable, Dict, Optional

from agentic_coder_prototype.agent import AgenticCoder, create_agent

from .events import EventType, SessionEvent
from .models import SessionCreateRequest, SessionStatus
from .registry import SessionRecord, SessionRegistry

logger = logging.getLogger(__name__)


AgentFactory = Callable[[str, Optional[str], Optional[Dict[str, Any]]], AgenticCoder]


class SessionRunner:
    """Coordinates agent execution, user inputs, and command handling for a session."""

    def __init__(
        self,
        *,
        session: SessionRecord,
        registry: SessionRegistry,
        request: SessionCreateRequest,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.request = request
        self.agent_factory = agent_factory or self._default_factory

        self._task: Optional[asyncio.Task[None]] = None
        self._agent: Optional[AgenticCoder] = None
        self._stop_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._input_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self._published_events = 0
        self._closed = False

        # Live overrides updated via commands
        initial_metadata = dict(request.metadata or {})
        self.session.metadata = initial_metadata
        self._model_override: Optional[str] = initial_metadata.get("model")
        self._mode: Optional[str] = initial_metadata.get("mode")

    def _default_factory(
        self,
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> AgenticCoder:
        return create_agent(config_path, workspace_dir=workspace_dir, overrides=overrides)

    async def start(self) -> None:
        if self._task:
            raise RuntimeError("runner already started")
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(self._run(), name=f"kyle-session-{self.session.session_id}")

    async def stop(self) -> None:
        if self._closed:
            return
        self._stop_event.set()
        await self._input_queue.put(None)
        if self._task and not self._task.done():
            await self._task

    async def enqueue_input(self, content: str) -> None:
        if self._closed:
            raise RuntimeError("session is closed")
        if not content or not content.strip():
            raise ValueError("input content must not be empty")
        await self._input_queue.put(content)

    async def handle_command(self, command: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._closed:
            raise RuntimeError("session is closed")

        payload = payload or {}
        match command:
            case "set_model":
                model_value = payload.get("model")
                if not isinstance(model_value, str) or not model_value.strip():
                    raise ValueError("set_model requires non-empty 'model'")
                self._model_override = model_value.strip()
                self.session.metadata["model"] = self._model_override
                self._apply_model_override()
                return {"status": "ok", "model": self._model_override}
            case "set_mode":
                mode_value = payload.get("mode")
                if not isinstance(mode_value, str) or not mode_value.strip():
                    raise ValueError("set_mode requires non-empty 'mode'")
                self._mode = mode_value.strip()
                self.session.metadata["mode"] = self._mode
                return {"status": "ok", "mode": self._mode}
            case "run_tests":
                raise NotImplementedError("run_tests not yet implemented")
            case "apply_diff":
                raise NotImplementedError("apply_diff not yet implemented")
            case _:
                raise ValueError(f"Unsupported command: {command}")

    async def _run(self) -> None:
        await self.registry.update_status(self.session.session_id, SessionStatus.RUNNING)
        try:
            self._agent = self.agent_factory(
                self.request.config_path,
                self.request.workspace,
                self.request.overrides,
            )

            initial_task = (self.request.task or "").strip()
            if initial_task:
                self._input_queue.put_nowait(initial_task)

            while not self._stop_event.is_set():
                try:
                    next_input = await self._input_queue.get()
                except asyncio.CancelledError:  # pragma: no cover - defensive
                    break
                if next_input is None:
                    break

                result = await asyncio.to_thread(self._execute_task, next_input)
                await self.registry.update_metadata(
                    self.session.session_id,
                    completion_summary=result.get("completion_summary"),
                    reward_summary=result.get("reward_metrics"),
                    logging_dir=result.get("logging_dir"),
                    metadata=self.session.metadata,
                )
                self._input_queue.task_done()

            final_status = SessionStatus.STOPPED if self._stop_event.is_set() else SessionStatus.COMPLETED
            await self.registry.update_status(self.session.session_id, final_status)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Session %s failed", self.session.session_id)
            await self.registry.update_status(self.session.session_id, SessionStatus.FAILED)
            self.publish_event(EventType.ERROR, {"message": str(exc)})
        finally:
            self._closed = True
            self._enqueue_termination()

    def _enqueue_termination(self) -> None:
        queue = self.session.event_queue
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning("Event queue full while terminating session %s", self.session.session_id)

    def _apply_model_override(self) -> None:
        if not self._agent or not self._model_override:
            return
        try:
            providers = self._agent.config.setdefault("providers", {})  # type: ignore[attr-defined]
            providers["default_model"] = self._model_override
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to apply model override: %s", exc)

    def _execute_task(self, task_text: str) -> Dict[str, Any]:
        if not self._agent:
            raise RuntimeError("agent missing")

        emitted_flags = {"assistant": False}
        self._published_events = 0
        is_local_agent = bool(getattr(self._agent, "_local_mode", False))
        event_queue = None
        queue_stop = None
        queue_thread = None

        def handle_runtime_event(event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None) -> None:
            translated = self._translate_runtime_event(event_type, payload, turn)
            if not translated:
                return
            evt_type, evt_payload, evt_turn = translated
            if evt_type is EventType.ASSISTANT_MESSAGE:
                emitted_flags["assistant"] = True
            self.publish_event(evt_type, evt_payload, turn=evt_turn)

        remote_stream_enabled = bool(os.environ.get("KYLECODE_ENABLE_REMOTE_STREAM", ""))
        if isinstance(self.request.metadata, dict) and "enable_remote_stream" in self.request.metadata:
            remote_stream_enabled = bool(self.request.metadata.get("enable_remote_stream"))

        logger.info(
            "session(%s) task=%s stream=%s local=%s remote_toggle=%s",
            self.session.session_id,
            task_text[:32].replace("\n", " ") if task_text else "<empty>",
            bool(self.request.stream),
            is_local_agent,
            remote_stream_enabled,
        )

        if self._model_override:
            self._apply_model_override()

        if not is_local_agent and self.request.stream and remote_stream_enabled:
            try:
                from ray.util.queue import Queue
            except ImportError:  # pragma: no cover
                Queue = None  # type: ignore[misc]
            if Queue is not None:
                event_queue = Queue()
                queue_stop, queue_thread = self._start_queue_pump(event_queue, handle_runtime_event)
                logger.info("session(%s) remote streaming queue initialized", self.session.session_id)

        try:
            result = self._agent.run_task(  # type: ignore[call-arg]
                task_text,
                max_iterations=self.request.max_steps,
                stream=self.request.stream,
                event_emitter=handle_runtime_event if is_local_agent else None,
                event_queue=event_queue,
            )
        finally:
            if queue_stop:
                queue_stop.set()
                if event_queue is not None:
                    try:
                        event_queue.put((None, None, None))
                    except Exception:  # pragma: no cover
                        pass
            if queue_thread:
                queue_thread.join(timeout=2)
            if event_queue is not None:
                self._drain_event_queue(event_queue, handle_runtime_event)

        completion = result.get("completion_summary") or {}
        reward = result.get("reward_metrics_payload") or {}
        messages = result.get("messages")
        if not emitted_flags["assistant"] and isinstance(messages, list):
            for entry in reversed(messages):
                if isinstance(entry, dict) and entry.get("role") == "assistant":
                    text = entry.get("content", "")
                    self.publish_event(
                        EventType.ASSISTANT_MESSAGE,
                        {"text": text, "message": entry, "source": "fallback"},
                    )
                    break
        self.publish_event(EventType.COMPLETION, {"summary": completion, "mode": self._mode})
        if reward:
            self.publish_event(EventType.REWARD_UPDATE, {"summary": reward})
        logging_dir = result.get("logging_dir") or result.get("run_dir")
        logger.info(
            "session(%s) task complete events=%s logging_dir=%s",
            self.session.session_id,
            self._published_events,
            logging_dir,
        )
        return {
            "completion_summary": completion,
            "reward_metrics": reward or None,
            "logging_dir": logging_dir,
        }

    def publish_event(self, event_type: EventType, payload: Dict[str, Any], *, turn: Optional[int] = None) -> None:
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
        )
        loop = self._loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and loop and running_loop is loop:
            self._enqueue_event(event)
            return

        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._enqueue_event, event)
            return

        self._enqueue_event(event)

    def _enqueue_event(self, event: SessionEvent) -> None:
        try:
            self.session.event_queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning("Event queue full for session %s, dropping event", self.session.session_id)

    def _start_queue_pump(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
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
                handle_event(event_type, payload, turn=turn)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return stop_signal, thread

    def _drain_event_queue(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
    ) -> None:
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
        logger.info("session(%s) published %s events", self.session.session_id, self._published_events)

    def _translate_runtime_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        turn: Optional[int],
    ) -> Optional[tuple[EventType, Dict[str, Any], Optional[int]]]:
        mapping = {
            "turn_start": EventType.TURN_START,
            "assistant_message": EventType.ASSISTANT_MESSAGE,
            "user_message": EventType.USER_MESSAGE,
            "tool_call": EventType.TOOL_CALL,
            "tool_result": EventType.TOOL_RESULT,
            "reward_update": EventType.REWARD_UPDATE,
            "completion": EventType.COMPLETION,
            "log_link": EventType.LOG_LINK,
            "error": EventType.ERROR,
        }
        evt = mapping.get(event_type)
        if not evt:
            return None

        normalized_payload: Dict[str, Any] = dict(payload or {})
        if evt is EventType.ASSISTANT_MESSAGE:
            message = normalized_payload.get("message")
            text = ""
            if isinstance(message, dict):
                text = str(message.get("content", ""))
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.USER_MESSAGE:
            message = normalized_payload.get("message")
            text = ""
            if isinstance(message, dict):
                text = str(message.get("content", ""))
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.TOOL_CALL:
            call = normalized_payload.get("call")
            normalized_payload = {"call": call}
        elif evt is EventType.TOOL_RESULT and "message" in normalized_payload:
            message = normalized_payload.get("message")
            normalized_payload = {
                "message": message,
                "content": (message or {}).get("content") if isinstance(message, dict) else None,
            }
        return evt, normalized_payload, turn
