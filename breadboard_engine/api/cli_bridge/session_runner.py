"""Session execution helpers for the CLI bridge."""

from __future__ import annotations
import asyncio
import json
import logging
import threading
import os
import time
import uuid, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, List, Tuple
from breadboard_engine.compilation.v2_loader import load_agent_config
from breadboard.product.runtime.artifacts import _validate_artifact_name
from breadboard_engine.auth.enforcer import apply_dotted_overrides
from breadboard_engine.compilation.effective_operation_policy import policy_pack_for_config_authority
from breadboard_engine.model_roles import (
    ModelRoleProblem,
    ModelRoleResolutionError,
    resolve_role_name,
    select_role_target,
)
from breadboard_engine.checkpointing.checkpoint_manager import CheckpointManager
from breadboard_engine.security import WorkspaceFilesystem, redaction
from breadboard_engine.skills.registry import (
    load_skills,
    build_skill_catalog,
    normalize_skill_selection,
    apply_skill_selection,
)
from breadboard_engine.provider.contracts import (
    strip_provider_exchange_completion_sentinels,
    strip_public_completion_sentinel_lines,
    strip_public_completion_sentinel_tree,
)
from breadboard_engine.plugins.loader import discover_plugin_manifests, plugin_snapshot
from breadboard_engine.guardrail import GuardrailCoordinator
from breadboard_engine.permissions import (
    build_permission_overrides,
    load_permission_rules,
    upsert_permission_rule,
)
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.todo import TodoStore
from breadboard_engine.todo.projection import project_store_snapshot_to_tui_envelope
from breadboard_engine.state.session_state import (
    AUDIT_ONLY_RUNTIME_EVENT_TYPES,
    CANONICAL_KERNEL_EVENT_TYPES,
    PROJECTION_ONLY_RUNTIME_EVENT_TYPES,
)
from .events import EventType, SessionEvent
from .event_normalization import normalize_task_event_payload
from .models import SessionCreateRequest, SessionStatus
from .registry import SessionRecord, SessionRegistry, TurnRecord, submission_body_digest, identity_digest

logger = logging.getLogger(__name__)
AgentFactory = Callable[[str, Optional[str], Optional[Dict[str, Any]]], Any]
MAX_ATTACHMENT_BYTES = 16 * 1024
MAX_PAIRED_PRODUCT_TOOL_COMPLETIONS = 128
_PERMISSION_ALIASES = {alias: decision for decision, aliases in {
    "once": "once allow approve approved ok okay yes y allow-once allow_once", "always": "always allow-always allow_always",
    "reject": "reject deny denied no n deny-once deny_once deny-always deny_always deny-stop deny_stop",
}.items() for alias in aliases.split()}


def _permission_response_tokens(value: Any) -> list[str]:
    if isinstance(value, dict): return [token for nested in value.values() for token in _permission_response_tokens(nested)]
    return [value.strip().lower()] if isinstance(value, str) and value.strip() else []


def _permission_item_ids(request: Any) -> list[str]: return [str(item["item_id"]) for item in request.get("items", []) if isinstance(item, dict) and item.get("item_id")] if isinstance(request, dict) else []


def _permission_default_response(config: Any) -> str: return PermissionBroker((config.get("permissions") or {}) if isinstance(config, dict) else {})._default_response


def _canonical_permission_resolution(response: Any, responses: Any, requested_ids: Sequence[str] = (), missing_response: str = "reject") -> str:
    explicit = responses.get("items") if isinstance(responses, dict) else None; wrapped = isinstance(explicit, dict)
    if not wrapped and isinstance(responses, dict) and requested_ids and any(item_id in responses for item_id in requested_ids): explicit = responses
    if isinstance(responses, dict) and "default" in responses: tokens = _permission_response_tokens(responses["default"])
    elif isinstance(explicit, dict):
        fallback = (responses.get("fallback") or responses.get("default_response") or missing_response) if wrapped else missing_response
        tokens = [_permission_response_tokens(explicit.get(item_id, fallback)) for item_id in requested_ids] if requested_ids else [_permission_response_tokens(value) for value in explicit.values()]; tokens = [token for group in tokens for token in group]
    else: tokens = _permission_response_tokens(responses if isinstance(responses, dict) else response)
    values = [PermissionBroker._coerce_response(_PERMISSION_ALIASES.get(token, token)) for token in tokens]
    if not values: raise ValueError("permission response contains no valid decisions")
    return "reject" if "reject" in values else "always" if all(value == "always" for value in values) else "once"


def _control_kind(item: Any) -> str: return "stop" if item is None else item.strip().lower() if isinstance(item, str) else str(item.get("kind") or item.get("type") or ("stop" if item.get("stop") else "")).strip().lower() if isinstance(item, dict) else ""


class _PauseAwareControlQueue:
    def __init__(self, queue: Any) -> None: self._queue = queue
    def __getattr__(self, name: str) -> Any:
        queue = object.__getattribute__(self, "_queue")
        return getattr(queue, name)
    def get_nowait(self) -> Any:
        item = self._queue.get_nowait()
        while _control_kind(item) == "pause": item = self._queue.get()
        return item


def _canonical_permission_responses(responses: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _canonical_permission_responses(value) if isinstance(value, dict)
            else _canonical_permission_resolution(value, None) for key, value in responses.items()}


KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES = {
    "assistant_message",
    "user_message",
    "tool_call",
    "tool.result",
    "tool_result",
    "todo_event",
    "permission_request",
    "permission_response",
    "ctree_node",
    "ctree_snapshot",
    "task_event",
}
BRIDGE_STREAM_ONLY_RUNTIME_EVENT_TYPES = {
    "stream.gap",
    "assistant.message.start",
    "assistant.message.delta",
    "assistant.message.end",
    "assistant.reasoning.delta",
    "assistant.thought_summary.delta",
    "assistant.tool_call.start",
    "assistant.tool_call.delta",
    "assistant.tool_call.end",
    "tool.exec.start",
    "tool.exec.stdout.delta",
    "tool.exec.stderr.delta",
    "tool.exec.end",
    "assistant_delta",
}
BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES = {
    "conversation.compaction.start",
    "conversation.compaction.end",
    "checkpoint_list",
    "checkpoint_restored",
    "skills_catalog",
    "skills_selection",
    "warning",
    "reward_update",
    "limits_update",
    "completion",
    "log_link",
    "error",
    "run_finished",
}
SESSION_SCOPED_RUNTIME_EVENT_TYPES = {
    "stream.gap",
    "todo_event",
    "checkpoint_list",
    "checkpoint_restored",
    "skills_catalog",
    "skills_selection",
    "ctree_node",
    "ctree_snapshot",
}

_PUBLIC_RUNTIME_ERROR_CODES = frozenset(
    {
        "runtime_failure",
        "worker_crash",
        "runtime_protocol_error",
        "runtime_observation_failed",
        "turn_execution_failed",
        "permission_delivery_failed",
        "runtime_cancelled",
    }
)
_REPLAY_EVENT_PAYLOAD_FIELDS = {
    EventType.ASSISTANT_MESSAGE_START: frozenset(
        {"message_id", "item_id", "index"}
    ),
    EventType.ASSISTANT_MESSAGE_DELTA: frozenset(
        {"message_id", "item_id", "index", "delta", "text", "content"}
    ),
    EventType.ASSISTANT_MESSAGE_END: frozenset(
        {"message_id", "item_id", "index", "text", "content", "finish_reason"}
    ),
    EventType.ASSISTANT_REASONING_DELTA: frozenset(
        {"message_id", "item_id", "index", "delta", "text", "provider_field"}
    ),
    EventType.ASSISTANT_THOUGHT_SUMMARY_DELTA: frozenset(
        {"message_id", "item_id", "index", "delta", "text", "provider_field"}
    ),
    EventType.ASSISTANT_TOOL_CALL_START: frozenset(
        {"message_id", "item_id", "index", "call_id", "name", "tool"}
    ),
    EventType.ASSISTANT_TOOL_CALL_DELTA: frozenset(
        {
            "message_id",
            "item_id",
            "index",
            "call_id",
            "delta",
            "arguments_delta",
        }
    ),
    EventType.ASSISTANT_TOOL_CALL_END: frozenset(
        {
            "message_id",
            "item_id",
            "index",
            "call_id",
            "name",
            "arguments",
            "arguments_json",
        }
    ),
    EventType.TOOL_EXEC_START: frozenset(
        {"call_id", "exec_id", "tool", "tool_name", "command"}
    ),
    EventType.TOOL_EXEC_STDOUT_DELTA: frozenset(
        {"call_id", "exec_id", "delta"}
    ),
    EventType.TOOL_EXEC_STDERR_DELTA: frozenset(
        {"call_id", "exec_id", "delta"}
    ),
    EventType.TOOL_EXEC_END: frozenset(
        {"call_id", "exec_id", "exit_code"}
    ),
    EventType.ASSISTANT_MESSAGE: frozenset({"text", "message", "source"}),
    EventType.ASSISTANT_DELTA: frozenset({"text", "message_id"}),
    EventType.TOOL_CALL: frozenset(
        {
            "action",
            "call",
            "call_id",
            "diff_preview",
            "progress",
            "todo",
            "tool",
        }
    ),
    EventType.TOOL_RESULT: frozenset(
        {
            "call_id",
            "error",
            "message",
            "metadata",
            "result",
            "status",
            "success",
            "todo",
            "tool",
        }
    ),
    EventType.TOOL_RESULT_DOT: frozenset(
        {
            "call_id",
            "error",
            "message",
            "metadata",
            "result",
            "status",
            "success",
            "todo",
            "tool",
        }
    ),
    EventType.WARNING: frozenset({"code", "message"}),
    EventType.REWARD_UPDATE: frozenset({"summary"}),
    EventType.COMPLETION: frozenset({"summary", "mode", "usage"}),
    EventType.LOG_LINK: frozenset({"url"}),
    EventType.RUN_FINISHED: frozenset(
        {
            "completed",
            "eventCount",
            "logging_dir",
            "reason",
            "steps",
            "usage",
            "bridge_timing",
        }
    ),
}


def _validate_replay_event_payload(
    event_type: EventType, payload: Any
) -> Dict[str, Any]:
    allowed = _REPLAY_EVENT_PAYLOAD_FIELDS.get(event_type)
    if allowed is None or not isinstance(payload, dict):
        raise RuntimeProtocolError("runtime_protocol_error")
    unknown = set(payload) - allowed
    if unknown:
        raise RuntimeProtocolError("runtime_protocol_error")
    normalized = dict(payload)

    def require_string(field: str) -> None:
        if not isinstance(normalized.get(field), str):
            raise RuntimeProtocolError("runtime_protocol_error")

    if event_type in {
        EventType.ASSISTANT_MESSAGE,
        EventType.ASSISTANT_DELTA,
        EventType.WARNING,
    }:
        require_string("text" if event_type is not EventType.WARNING else "message")
    elif event_type in {
        EventType.ASSISTANT_MESSAGE_DELTA,
        EventType.ASSISTANT_REASONING_DELTA,
        EventType.ASSISTANT_THOUGHT_SUMMARY_DELTA,
    }:
        if not any(
            isinstance(normalized.get(field), str)
            for field in ("delta", "text", "content")
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
    elif event_type in {
        EventType.ASSISTANT_TOOL_CALL_DELTA,
        EventType.TOOL_EXEC_STDOUT_DELTA,
        EventType.TOOL_EXEC_STDERR_DELTA,
    }:
        if not any(
            isinstance(normalized.get(field), str)
            for field in ("delta", "arguments_delta")
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
    elif event_type is EventType.TOOL_CALL:
        if (
            not isinstance(normalized.get("call"), dict)
            or not isinstance(normalized.get("call_id"), (str, type(None)))
            or not isinstance(normalized.get("tool"), (str, type(None)))
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
    elif event_type in {EventType.TOOL_RESULT, EventType.TOOL_RESULT_DOT}:
        if "status" not in normalized or not isinstance(
            normalized.get("error"), bool
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
    elif event_type in {EventType.REWARD_UPDATE, EventType.COMPLETION}:
        field = "summary"
        if not isinstance(normalized.get(field), dict):
            raise RuntimeProtocolError("runtime_protocol_error")
    elif event_type is EventType.LOG_LINK:
        require_string("url")
    elif event_type is EventType.RUN_FINISHED:
        count = normalized.get("eventCount")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuntimeProtocolError("runtime_protocol_error")
    for field in ("message_id", "item_id", "call_id", "exec_id", "name"):
        if field in normalized and not isinstance(normalized[field], str):
            raise RuntimeProtocolError("runtime_protocol_error")
    index = normalized.get("index")
    if index is not None and (
        not isinstance(index, int) or isinstance(index, bool) or index < 0
    ):
        raise RuntimeProtocolError("runtime_protocol_error")
    return normalized


class RuntimeProtocolError(RuntimeError):
    """Safe protocol failure raised for an unsupported normative runtime event."""

    def __init__(self, code: str = "runtime_protocol_error") -> None:
        self.code = (
            code if code in _PUBLIC_RUNTIME_ERROR_CODES else "runtime_protocol_error"
        )
        super().__init__(self.code)


def _safe_runtime_error_code(value: Any, *, default: str = "runtime_failure") -> str:
    candidate = str(value or "").strip()
    if candidate in _PUBLIC_RUNTIME_ERROR_CODES:
        return candidate
    if candidate == "unknown_runtime_event":
        return "runtime_protocol_error"
    return default if default in _PUBLIC_RUNTIME_ERROR_CODES else "runtime_failure"


def _strip_completion_sentinels(value: Any) -> Any:
    if isinstance(value, str):
        return strip_public_completion_sentinel_lines(value)
    if isinstance(value, list):
        return [_strip_completion_sentinels(item) for item in value]
    if isinstance(value, dict):
        normalized = dict(value)
        for key in ("text", "content", "delta", "message", "summary", "value"):
            if key in normalized:
                normalized[key] = _strip_completion_sentinels(normalized[key])
        return normalized
    return value


def _assistant_visible_text(value: Any) -> str:
    normalized = _strip_completion_sentinels(value)
    if isinstance(normalized, str):
        return normalized
    if not isinstance(normalized, list):
        return ""
    parts: list[str] = []
    for block in normalized:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {
            "text",
            "input_text",
            "output_text",
            "summary_text",
        }:
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _runtime_event_is_session_scoped(event_type: str) -> bool:
    return str(event_type or "") in SESSION_SCOPED_RUNTIME_EVENT_TYPES


RuntimeEventContract = Dict[str, Optional[str]]
TranslatedRuntimeEvent = Tuple[EventType, Dict[str, Any], Optional[int], RuntimeEventContract]


def _default_runtime_event_contract(event_type: str) -> RuntimeEventContract:
    event_name = str(event_type or "")
    for registry, classification in (
        (CANONICAL_KERNEL_EVENT_TYPES, "canonical"),
        (PROJECTION_ONLY_RUNTIME_EVENT_TYPES, "projection_only"),
        (AUDIT_ONLY_RUNTIME_EVENT_TYPES, "audit_only"),
    ):
        metadata = registry.get(event_name)
        if metadata is not None:
            return {
                "classification": classification,
                "family": metadata["family"],
                "actor": metadata["actor"],
                "visibility": metadata["visibility"],
            }
    if event_name in {"assistant_message", "assistant_delta", "assistant.message.start", "assistant.message.delta", "assistant.message.end"}:
        return {"classification": "bridge_stream", "family": "message.assistant", "actor": "engine", "visibility": "transcript"}
    if event_name == "user_message":
        return {"classification": "kernel", "family": "message.user", "actor": "human", "visibility": "transcript"}
    if event_name in {"assistant.reasoning.delta", "assistant.thought_summary.delta"}:
        return {"classification": "bridge_stream", "family": "reasoning.delta", "actor": "engine", "visibility": "diagnostic"}
    if event_name.startswith("assistant.tool_call."):
        return {"classification": "bridge_stream", "family": "tool.call.delta", "actor": "engine", "visibility": "tool"}
    if event_name.startswith("tool.") or event_name in {"tool_call", "tool_result", "tool.result", "todo_event"}:
        return {"classification": "kernel", "family": "tool.event", "actor": "tool", "visibility": "tool"}
    if event_name in {"ctree_node", "turn_start", "lifecycle_event", "guardrail_event"}:
        return {"classification": "kernel", "family": f"audit.{event_name}", "actor": "service", "visibility": "audit"}
    if event_name in BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES or event_name in {"permission_request", "permission_response", "task_event", "ctree_snapshot"}:
        return {"classification": "bridge_host", "family": f"host.{event_name}", "actor": "service", "visibility": "host"}
    return {"classification": "legacy_unclassified", "family": "legacy.unclassified", "actor": "engine", "visibility": "audit"}


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
        self._agent: Optional[Any] = None
        self._stop_event = asyncio.Event()
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._permission_decision_lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._start_authority = asyncio.Event()
        self._input_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._product_session_lock = threading.RLock()
        self._product_tool_completions: Dict[str, int] = {}
        self._published_events = 0
        self._session_failure_published = False
        self._workspace_path: Optional[Path] = None
        self._closed = False
        self._attachment_store: Dict[str, Dict[str, Any]] = {}
        self._active_attachment_capabilities: Dict[str, Dict[str, Any]] = {}
        self._active_input_media: List[Dict[str, str]] = []
        self._permission_queue: Any = None
        self._consumed_permission_responses: Dict[tuple[str, str, str], int] = {}
        self._skills_catalog_cache: Optional[Dict[str, Any]] = None
        self._ctree_snapshot_cache: Optional[Dict[str, Any]] = None
        self._ctree_last_node: Optional[Dict[str, Any]] = None
        self._base_config_cache: Optional[Dict[str, Any]] = None
        self._prepared_runtime_config: Optional[Dict[str, Any]] = None
        self._todo_enabled: bool = False
        self._active_bridge_timing_context: Optional[Dict[str, float]] = None
        self._accepted_task_texts: List[str] = []
        initial_metadata = self.session.metadata
        initial_metadata.update(dict(request.metadata or {}))
        self.session.metadata = initial_metadata
        task_context = dict(initial_metadata.get("task_context") or {})
        task_context.setdefault("session_id", self.session.session_id)
        if not isinstance(task_context.get("input_id"), str) or not task_context[
            "input_id"
        ]:
            task_context["input_id"] = f"input-{uuid.uuid4()}"
        if not isinstance(task_context.get("turn_id"), str) or not task_context[
            "turn_id"
        ]:
            task_context["turn_id"] = f"turn-{uuid.uuid4()}"
        initial_metadata["task_context"] = task_context
        self._model_role_lock: Any = initial_metadata.get("model_role_lock")
        self._active_model_role: str | None = (
            str(initial_metadata.get("active_model_role") or "").strip() or None
        )
        self._model_override: Optional[str] = initial_metadata.get("model")
        self._mode: Optional[str] = initial_metadata.get("mode")
        self._profile_timing_enabled: bool = bool(
            os.environ.get("BREADBOARD_PROFILE_TIMING", "").strip().lower()
            in {"1", "true", "yes", "on"}
            or initial_metadata.get("profile_timing")
        )

    def _default_factory(
        self,
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> Any:
        from breadboard_engine.agent import create_agent
        metadata = self.session.metadata if isinstance(self.session.metadata, dict) else {}
        force_local_mode = bool(metadata.get("cli_force_local_mode", True))
        return create_agent(
            config_path,
            workspace_dir=workspace_dir,
            overrides=overrides,
            force_local_mode=force_local_mode,
        )

    def schedule_start(self) -> None:
        if self._task:
            raise RuntimeError("runner already started")
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(self._run_after_start_authority(), name=f"kyle-session-{self.session.session_id}")

    async def prepare_start(self, *, admission_serialized: bool = False) -> None:
        """Retain an initial turn before execution becomes runnable."""
        if self._task:
            raise RuntimeError("runner already started")
        self._loop = asyncio.get_running_loop()
        initial_task = (self.request.task or "").strip()
        if not initial_task:
            return
        client_message_id = f"session-create:{self.session.session_id}"
        input_id = f"input-{uuid.uuid4()}"
        turn_id = f"turn-{uuid.uuid4()}"
        attachments: tuple[str, ...] = ()
        turn = TurnRecord(
            input_id=input_id,
            turn_id=turn_id,
            client_message_id=client_message_id,
            content=initial_task,
            attachments=attachments,
            original_disposition="started",
            state="active",
            body_digest=submission_body_digest(initial_task, attachments),
        )
        self.session.turns_by_id[turn_id] = turn
        self.session.submissions_by_key[client_message_id] = turn
        self.session.submissions_by_key_digest[identity_digest(client_message_id)] = turn
        self.session.active_turn_id = turn_id

    def authorize_start(self) -> None:
        if not self._task:
            raise RuntimeError("runner is not scheduled")
        self._start_authority.set()

    async def _run_after_start_authority(self) -> None:
        await self._start_authority.wait()
        await self._run()

    async def start(self) -> None:
        if (self.request.task or "").strip() and self.session.active_turn_id is None:
            await self.prepare_start()
        self.schedule_start()
        self.authorize_start()

    def _signal_control(self, kind: str) -> bool:
        queue = getattr(self, "_control_queue", None)
        put = (
            getattr(queue, "put_nowait", None) or getattr(queue, "put", None)
            if queue is not None
            else None
        )
        if not callable(put):
            return False
        put({"kind": kind})
        return True

    def _install_control_queue(self, queue: Any) -> None:
        with self._product_session_lock:
            self._control_queue = queue
            if queue is None:
                return
            if self._stop_event.is_set():
                queue.put({"kind": "stop"})
            elif not self._resume_event.is_set():
                queue.put({"kind": "pause"})

    def _request_stop(self, reason: str) -> bool:
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            stopping = (
                not product_session
                or product_session.read_model.status
                not in {"completed", "failed", "canceled"}
            )
            try:
                if stopping:
                    self.transition_product_session("cancel", reason)
            finally:
                self._stop_event.set()
                self._resume_event.set()
                self._input_queue.put_nowait(None)
                try:
                    delivered = self._signal_control("stop")
                except Exception:
                    delivered = False
                request_stop = (
                    getattr(
                        getattr(self._agent, "agent", None),
                        "request_stop",
                        None,
                    )
                    if not delivered
                    else None
                )
                remote = getattr(request_stop, "remote", None)
                try:
                    if callable(remote):
                        remote()
                    elif callable(request_stop):
                        request_stop()
                except Exception:
                    pass
            return stopping

    async def stop(self, reason: str = "operator request") -> None:
        if self._closed:
            return
        cancelled_before_start = self._task is None or not self._start_authority.is_set()
        self._request_stop(reason)
        if self._task and not self._start_authority.is_set():
            self._task.cancel()
        if self._task and not self._task.done():
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if cancelled_before_start:
            product_state = getattr(
                getattr(
                    getattr(self.session, "product_session", None),
                    "read_model",
                    None,
                ),
                "status",
                None,
            )
            outcome = {
                "completed": "completed",
                "failed": "failed",
                "canceled": "cancelled",
            }.get(product_state, "cancelled")
            await self._terminalize_admitted_turns(
                outcome=outcome,
                reason=(
                    "stop_requested"
                    if outcome == "cancelled"
                    else "runtime_failure"
                    if outcome == "failed"
                    else "completed"
                ),
                error_code=(
                    "runtime_failure" if outcome == "failed" else None
                ),
            )
            final_status = {
                "completed": SessionStatus.COMPLETED,
                "failed": SessionStatus.FAILED,
                "cancelled": SessionStatus.STOPPED,
            }[outcome]
            await self.registry.update_status(
                self.session.session_id, final_status
            )
            self._closed = True
            await self._enqueue_termination()


    async def enqueue_input(
        self,
        content: str,
        attachments: Optional[list[str]] = None,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> str:
        if self._closed:
            raise RuntimeError("session is closed")
        if not content or not content.strip():
            raise ValueError("input content must not be empty")
        if not isinstance(input_id, str) or not input_id.strip():
            raise ValueError("input_id is required at runner admission")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("turn_id is required at runner admission")
        admitted_turn = self.session.turns_by_id.get(turn_id)
        if admitted_turn is None:
            raise RuntimeError("turn_id was not admitted by the session registry")
        if admitted_turn.input_id != input_id:
            raise RuntimeError("input_id does not match the admitted turn")
        if admitted_turn.terminal_outcome is not None:
            raise RuntimeError("turn is already terminal")
        admitted_active = (
            admitted_turn.state == "active"
            and self.session.active_turn_id == admitted_turn.turn_id
        )
        admitted_queued = (
            admitted_turn.state == "queued"
            and admitted_turn.turn_id in self.session.queued_turn_ids
        )
        if not (admitted_active or admitted_queued):
            raise RuntimeError("turn is not active or queued for execution")


        attachment_ids = list(dict.fromkeys(item.strip() for item in (attachments or []) if isinstance(item, str) and item.strip()))
        if admitted_turn.content != content:
            raise RuntimeError("input content does not match the admitted turn")
        if admitted_turn.attachments != tuple(attachment_ids):
            raise RuntimeError("attachments do not match the admitted turn")
        with self._product_session_lock:
            artifacts = getattr(self.session, "product_artifacts", {})
            unknown = [item for item in attachment_ids if not isinstance(artifacts, dict) or item not in artifacts]
            if unknown: raise ValueError(f"unknown attachment IDs: {', '.join(unknown)}")
            total_bytes = sum(int(getattr(artifacts[item], "size_bytes", MAX_ATTACHMENT_BYTES + 1)) for item in attachment_ids)
            if total_bytes > MAX_ATTACHMENT_BYTES: raise ValueError(f"selected attachments exceed {MAX_ATTACHMENT_BYTES}-byte handoff limit")
            content = self._sanitize_interactive_input_content(content)
            payload = {
                "content": content,
                "attachments": attachment_ids,
                "input_id": input_id,
                "turn_id": turn_id,
            }
            product_session = getattr(self.session, "product_session", None)
            if product_session is not None:
                product_session.input(content, [artifacts[item] for item in attachment_ids])
                self.session.metadata["session_contract"] = product_session.read_model.as_dict()
            self._input_queue.put_nowait(payload)
        return content

    def transition_product_session(self, transition: str, *args: Any) -> None:
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            if product_session is None:
                return
            if transition in {"complete", "fail", "cancel"} and product_session.read_model.status in {
                "completed", "failed", "canceled"}:
                return
            getattr(product_session, transition)(*args)
            self.session.metadata["session_contract"] = product_session.read_model.as_dict()

    # Provider-supplied names are not public identities until they resolve into
    # the active, configured tool surface.
    def _observation_tool_name(self, payload: Dict[str, Any]) -> Optional[str]:
        raw = payload.get("tool")
        if not isinstance(raw, str) or not raw or len(raw) > 128 or not raw.isascii():
            return None
        if not raw[0].isalnum() or any(not (character.isalnum() or character in "_.-") for character in raw):
            return None
        canonical = raw
        executor = getattr(self._agent, "agent_executor", None)
        canonicalize = getattr(executor, "canonical_tool_name", None)
        if callable(canonicalize):
            try:
                candidate = canonicalize(raw)
                if isinstance(candidate, str) and candidate:
                    canonical = candidate
            except Exception:
                return None
        if len(canonical) > 128 or not canonical.isascii() or not canonical[0].isalnum() or any(not (character.isalnum() or character in "_.-") for character in canonical):
            return None
        active_names = getattr(self._agent, "_active_tool_names", ())
        allowed = {
            name
            for name in active_names
            if isinstance(name, str)
        } if isinstance(active_names, Sequence) and not isinstance(active_names, (str, bytes)) else set()
        config = self.current_runtime_config()
        modes = config.get("modes")
        for mode in modes if isinstance(modes, list) else ():
            if not isinstance(mode, dict):
                continue
            enabled_names = mode.get("tools_enabled")
            if isinstance(enabled_names, Sequence) and not isinstance(enabled_names, (str, bytes)):
                allowed.update(name for name in enabled_names if isinstance(name, str))
        configured_tools = config.get("tools")
        if isinstance(configured_tools, dict):
            allowed.update(
                name
                for name, enabled in configured_tools.items()
                if isinstance(name, str) and bool(enabled)
            )
        return canonical if canonical in allowed else None

    def _tool_completion_fingerprint(
        self,
        tool: str,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        result = payload.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        try:
            material = json.dumps(
                {"error": bool(payload.get("error")), "result": result, "tool": tool},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return None
        return identity_digest(material)

    def _record_product_observation(
        self,
        family: Optional[str],
        payload: Dict[str, Any],
        *,
        message_projection: bool = False,
    ) -> None:
        if family not in {"message.assistant", "tool.called", "tool.completed"}:
            return
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            if product_session is None or product_session.read_model.status != "running":
                return
            if family == "message.assistant":
                text = payload.get("text")
                product_session.assistant_message(text if isinstance(text, str) else "")
            elif family == "tool.called":
                tool = self._observation_tool_name(payload)
                if tool is None:
                    return
                product_session.tool_called(tool)
            else:
                tool = self._observation_tool_name(payload)
                if tool is None:
                    return
                # Some runtimes emit a canonical completion and then its model-message
                # projection. Pair transient digests; persist neither digest nor result.
                fingerprint = self._tool_completion_fingerprint(tool, payload)
                duplicate_count = (
                    self._product_tool_completions.get(fingerprint, 0)
                    if fingerprint is not None
                    else 0
                )
                if message_projection and duplicate_count:
                    if duplicate_count == 1:
                        del self._product_tool_completions[fingerprint]
                    else:
                        self._product_tool_completions[fingerprint] = duplicate_count - 1
                    return
                product_session.tool_completed(tool, bool(payload.get("error")))
                if not message_projection and fingerprint is not None:
                    if (
                        fingerprint not in self._product_tool_completions
                        and len(self._product_tool_completions)
                        >= MAX_PAIRED_PRODUCT_TOOL_COMPLETIONS
                    ):
                        self._product_tool_completions.pop(
                            next(iter(self._product_tool_completions))
                        )
                    self._product_tool_completions[fingerprint] = duplicate_count + 1
            self.session.metadata["session_contract"] = product_session.read_model.as_dict()

    def _sanitize_interactive_input_content(self, content: str) -> str:
        """Remove an exact prior-prompt prefix accidentally repeated by the TUI.

        Independent prompt POSTs must remain separate turns; only the nonempty suffix
        of an exact prior accepted prompt is new input.
        """
        raw = str(content or "")
        if not raw:
            return raw
        for prior in sorted(self._accepted_task_texts, key=len, reverse=True):
            if not prior or not raw.startswith(prior) or len(raw) <= len(prior):
                continue
            suffix = raw[len(prior) :]
            if not suffix.strip():
                continue
            logger.warning(
                "session(%s) stripped stale prompt prefix from interactive input old_len=%s new_len=%s",
                self.session.session_id,
                len(prior),
                len(suffix),
            )
            meta = (
                self.session.metadata
                if isinstance(self.session.metadata, dict)
                else {}
            )
            repairs = (
                list(meta.get("input_boundary_repairs") or [])
                if isinstance(meta.get("input_boundary_repairs"), list)
                else []
            )
            repairs.append(
                {
                    "prior_len": len(prior),
                    "raw_len": len(raw),
                    "suffix_len": len(suffix),
                }
            )
            meta["input_boundary_repairs"] = repairs[-10:]
            self.session.metadata = meta
            self._persist_metadata_snapshot_threadsafe()
            return suffix.lstrip()
        return raw

    def _fail_control_transition(self, code: str, detail: str) -> None:
        try:
            self.transition_product_session("fail", code, detail)
        finally:
            self._request_stop(detail)

    async def handle_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        durable_reconfigure: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        if self._closed:
            raise RuntimeError("session is closed")
        if command == "set_model" and self._model_role_lock is not None:
            raise ModelRoleResolutionError(
                ModelRoleProblem(
                    "lock_immutable",
                    "model overrides are rejected after session.start",
                    "$.role_overrides",
                    {"lock_hash": self.session.metadata.get("model_role_lock_hash")},
                )
            )
        payload = payload or {}
        match command:
            case "list_checkpoints":
                manager = self._checkpoint_manager
                if manager is None:
                    workspace_dir = self.get_workspace_dir()
                    if not workspace_dir:
                        raise RuntimeError("workspace not ready")
                    manager = CheckpointManager(workspace_dir)
                    self._checkpoint_manager = manager
                checkpoints = [cp.as_payload() for cp in manager.list_checkpoints()]
                await self.publish_event_async(
                    EventType.CHECKPOINT_LIST, {"checkpoints": checkpoints}
                )
                return {"status": "ok", "count": len(checkpoints)}
            case "restore_checkpoint":
                checkpoint_id = (
                    payload.get("checkpoint_id")
                    or payload.get("checkpointId")
                    or payload.get("id")
                )
                if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
                    raise ValueError(
                        "restore_checkpoint requires non-empty 'checkpoint_id'"
                    )
                mode = str(payload.get("mode") or "code").strip().lower()
                if mode not in {"code", "conversation", "both"}:
                    raise ValueError(
                        "restore_checkpoint 'mode' must be one of: code, conversation, both"
                    )
                workspace_dir = self.get_workspace_dir()
                manager = self._checkpoint_manager
                if manager is None:
                    workspace_dir = self.get_workspace_dir()
                    if not workspace_dir:
                        raise RuntimeError("workspace not ready")
                    manager = CheckpointManager(workspace_dir)
                    self._checkpoint_manager = manager
                prune = True
                if mode in {"code", "both"}:
                    manager.restore_checkpoint(checkpoint_id.strip(), prune=prune)
                if mode in {"conversation", "both"}:
                    try:
                        snapshot = manager.load_snapshot(checkpoint_id.strip())
                    except Exception:
                        snapshot = None
                    if snapshot:
                        if workspace_dir:
                            active_logical = (
                                ".breadboard/checkpoints/active_snapshot.json"
                            )
                            try:
                                with WorkspaceFilesystem.open_anchored_root(
                                    workspace_dir,
                                    create=False,
                                ) as filesystem:
                                    filesystem.create_directory(
                                        ".breadboard/checkpoints"
                                    )
                                    filesystem.write_text(
                                        active_logical,
                                        json.dumps(
                                            snapshot, indent=2, ensure_ascii=False
                                        ),
                                        encoding="utf-8",
                                    )
                                    active_path = Path(
                                        filesystem.display_path(active_logical)
                                    )
                                self.session.metadata["conversation_snapshot"] = {
                                    "checkpoint_id": checkpoint_id.strip(),
                                    "path": str(active_path),
                                }
                                self._persist_metadata_snapshot_threadsafe()
                            except Exception:
                                pass
                await self.publish_event_async(
                    EventType.CHECKPOINT_RESTORED,
                    {
                        "checkpoint_id": checkpoint_id.strip(),
                        "mode": mode,
                        "prune": prune,
                    },
                )
                checkpoints = [cp.as_payload() for cp in manager.list_checkpoints()]
                await self.publish_event_async(
                    EventType.CHECKPOINT_LIST, {"checkpoints": checkpoints}
                )
                return {
                    "status": "ok",
                    "checkpoint_id": checkpoint_id.strip(),
                    "mode": mode,
                    "prune": prune,
                }
            case "permission_decision":
                async with self._permission_decision_lock:
                    request_id = next(
                        (
                            payload.get(key)
                            for key in (
                                "request_id",
                                "requestId",
                                "permission_id",
                                "permissionId",
                                "id",
                            )
                            if payload.get(key)
                        ),
                        None,
                    )
                    decision = payload.get("decision") or payload.get("response")
                    if not isinstance(request_id, str) or not request_id.strip():
                        raise ValueError(
                            "permission_decision requires non-empty 'request_id'"
                        )
                    if not isinstance(decision, str) or not decision.strip():
                        raise ValueError(
                            "permission_decision requires non-empty 'decision'"
                        )
                    request_id, normalized = (
                        request_id.strip(),
                        decision.strip().lower(),
                    )
                    pending = self.session.metadata.get("pending_permissions")
                    active = (
                        pending[0]
                        if isinstance(pending, list)
                        and pending
                        and isinstance(pending[0], dict)
                        else {}
                    )
                    if (
                        str(active.get("request_id") or active.get("id") or "")
                        != request_id
                    ):
                        raise ValueError("permission request is not active")
                    response_value = _canonical_permission_resolution(normalized, None)
                    rule, scope, note = (
                        payload.get("rule"),
                        payload.get("scope"),
                        payload.get("note"),
                    )
                    category = (
                        self._infer_permission_category(request_id) if rule else None
                    )
                    workspace_dir = self.get_workspace_dir() if rule else None
                    if rule:
                        metadata = dict(self.session.metadata or {})
                        rules = list(metadata.get("permission_rules") or [])
                        rules.append(
                            {
                                "request_id": request_id,
                                "decision": normalized,
                                "rule": rule,
                                "scope": scope,
                                "note": note,
                            }
                        )
                        metadata["permission_rules"] = rules
                        persist_rule = (
                            (
                                response_value == "always"
                                or normalized in {"deny-always", "deny_always"}
                            )
                            and category
                            and workspace_dir
                        )
                        try:
                            persisted = not persist_rule or upsert_permission_rule(
                                workspace_dir,
                                category=category,
                                pattern=str(rule).strip(),
                                decision=(
                                    "deny" if normalized.startswith("deny") else "allow"
                                ),
                                scope=str(scope or "project"),
                            )
                        except Exception as exc:
                            self.transition_product_session(
                                "fail",
                                "permission_commit_failed",
                                "failed to commit permission decision",
                            )
                            raise RuntimeError(
                                "failed to commit permission decision"
                            ) from exc
                        if not persisted:
                            self.transition_product_session(
                                "fail",
                                "permission_commit_failed",
                                "failed to commit permission decision",
                            )
                            raise RuntimeError("failed to persist permission rule")
                        try:
                            await self.registry.update_metadata(
                                self.session.session_id, metadata=metadata
                            )
                        except Exception:
                            if not persist_rule:
                                self.transition_product_session(
                                    "fail",
                                    "permission_commit_failed",
                                    "failed to commit permission decision",
                                )
                                raise
                            self.session.metadata = metadata
                            logger.warning(
                                "Permission metadata projection failed after durable rule commit",
                                exc_info=True,
                            )
                    detail = await self.handle_command(
                        "respond_permission",
                        {"request_id": request_id, "response": response_value},
                    )
                    if normalized in {"deny-stop", "deny_stop"} or bool(
                        payload.get("stop")
                    ):
                        await self.handle_command("stop", {})
                    return {
                        "status": "ok",
                        "request_id": request_id,
                        "decision": response_value,
                        "delivered": detail,
                    }
            case "set_skills":
                selection_payload = dict(payload or {})
                if (
                    "selected" in selection_payload
                    and "allowlist" not in selection_payload
                ):
                    selection_payload["allowlist"] = selection_payload.get("selected")
                with self._product_session_lock:
                    config = (
                        dict(getattr(self._agent, "config", {}) or {})
                        if self._agent
                        else {}
                    )
                    selection = normalize_skill_selection(config, selection_payload)
                    overrides = {
                        "skills.allowlist": selection.get("allowlist") or [],
                        "skills.blocklist": selection.get("blocklist") or [],
                    }
                    previous = self.current_runtime_config()
                    had_skills = "skills" in config
                    previous_skills = (
                        json.loads(json.dumps(config.get("skills")))
                        if had_skills
                        else None
                    )
                    rollback = {
                        "skills.allowlist": (previous_skills or {}).get("allowlist")
                        or [],
                        "skills.blocklist": (previous_skills or {}).get("blocklist")
                        or [],
                    }
                    try:
                        if (
                            self._agent
                            and self._agent.apply_runtime_overrides(overrides) is False
                        ):
                            raise RuntimeError("failed to apply skills configuration")
                        prepared = apply_dotted_overrides(previous, overrides)
                        if durable_reconfigure is not None:
                            durable_reconfigure(prepared)
                    except Exception as error:
                        rolled_back = self._rollback_runtime_overrides(
                            rollback, ("skills", had_skills, previous_skills)
                        )
                        if not isinstance(error, OSError) or not rolled_back:
                            self.transition_product_session(
                                "fail",
                                "runtime_reconfigure_failed",
                                "failed to apply skills configuration",
                            )
                        raise
                    self.session.metadata["skills_selection"] = selection
                    self._prepared_runtime_config = prepared
                    self._persist_metadata_snapshot_threadsafe()
                    self._skills_catalog_cache = None
                    catalog_payload = self.get_skill_catalog()
                await self.publish_event_async(
                    EventType.SKILLS_SELECTION, {"selection": selection}
                )
                await self.publish_event_async(
                    EventType.SKILLS_CATALOG, catalog_payload
                )
                return {
                    "status": "ok",
                    "selection": selection,
                    "catalog": catalog_payload.get("catalog"),
                }
            case "pause":
                with self._product_session_lock:
                    transitioned = False
                    was_resumed = self._resume_event.is_set()
                    try:
                        self._resume_event.clear()
                        self.transition_product_session(
                            "pause", str(payload.get("reason") or "operator request")
                        )
                        transitioned = True
                        self._signal_control("pause")
                    except Exception:
                        self._resume_event.set() if was_resumed else None
                        (
                            self._fail_control_transition(
                                "pause_control_failed",
                                "failed to deliver pause control",
                            )
                            if transitioned
                            else None
                        )
                        raise
                return {"status": "ok", "paused": True}
            case "resume":
                with self._product_session_lock:
                    self.transition_product_session("resume")
                    try:
                        pending = self.session.metadata.get("pending_permissions")
                        head = (
                            pending[0]
                            if isinstance(pending, list)
                            and pending
                            and isinstance(pending[0], dict)
                            else None
                        )
                        request = (
                            head.get("request")
                            if head and isinstance(head.get("request"), dict)
                            else {}
                        )
                        (
                            self._update_pending_permissions(
                                "permission_request",
                                request,
                                source=str(head.get("source") or "session"),
                                task_session_id=head.get("task_session_id"),
                                subagent_type=head.get("subagent_type"),
                            )
                            if head
                            else None
                        )
                        self._signal_control("resume")
                        self._resume_event.set()
                    except Exception:
                        self._fail_control_transition(
                            "resume_control_failed", "failed to deliver resume control"
                        )
                        raise
                return {"status": "ok", "paused": False}
            case "stop":
                stopping = self._request_stop("stop command")
                return {"status": "ok", "stopping": stopping}
            case "set_role" | "set_model_role":
                if self._model_role_lock is None:
                    raise ModelRoleResolutionError(
                        ModelRoleProblem(
                            "known_role_unbound",
                            "no model-role lock is active",
                            "$.roles",
                        )
                    )
                if (
                    getattr(self.session, "product_session", None) is not None
                    and durable_reconfigure is None
                ):
                    raise RuntimeError(
                        "model-role transitions require durable reconfiguration"
                    )
                async with self.session.admission_lock:
                    if self.session.active_turn_id is not None:
                        raise ModelRoleResolutionError(
                            ModelRoleProblem(
                                "model_role_transition_active_turn",
                                "model roles may change only between turns",
                                "$.role",
                            )
                        )
                    requested = str(
                        payload.get("role") or payload.get("model_role") or ""
                    ).strip()
                    role = resolve_role_name(
                        self._model_role_lock, requested or None
                    )
                    target = self._locked_target(role)
                    route = self._target_route(target)
                    with self._product_session_lock:
                        previous_config = self.current_runtime_config()
                        previous_role = self._active_model_role
                        previous_model = self._model_override
                        previous_metadata = dict(self.session.metadata)
                        prepared = dict(previous_config)
                        prepared["active_model_role"] = role
                        prepared["model_role_lock"] = dict(
                            self._model_role_lock
                        )
                        prepared["providers"] = dict(
                            prepared.get("providers") or {}
                        )
                        prepared["providers"]["default_model"] = route
                        self._active_model_role = role
                        self._model_override = route
                        try:
                            if not self._apply_model_override():
                                raise RuntimeError(
                                    "failed to apply locked model role"
                                )
                            self.session.metadata.update(
                                {"active_model_role": role, "model": route}
                            )
                            self._prepared_runtime_config = prepared
                            if durable_reconfigure is not None:
                                durable_reconfigure(prepared)
                        except Exception:
                            self._active_model_role = previous_role
                            self._model_override = previous_model
                            self.session.metadata.clear()
                            self.session.metadata.update(previous_metadata)
                            self._prepared_runtime_config = previous_config
                            self._apply_model_override()
                            raise
                    await self.registry.update_metadata(
                        self.session.session_id,
                        metadata=dict(self.session.metadata or {}),
                    )
                    return {
                        "status": "ok",
                        "role": role,
                        "model": route,
                        "target": dict(target),
                    }
            case "set_model":
                if self._model_role_lock is not None:
                    raise ModelRoleResolutionError(
                        ModelRoleProblem(
                            "model_role_lock_immutable",
                            "direct model mutation is forbidden while a model-role lock is active",
                            "$.model",
                        )
                    )
                model_value = payload.get("model")
                if not isinstance(model_value, str) or not model_value.strip():
                    raise ValueError("set_model requires non-empty 'model'")
                model_value = model_value.strip()
                cfg = self.current_runtime_config()
                policy = policy_pack_for_config_authority(
                    cfg,
                    session_id=self.session.session_id,
                    config_path=self.request.config_path,
                    logger=logger,
                )
                if (
                    policy.model_allowlist is not None or policy.model_denylist
                ) and not policy.is_model_allowed(model_value):
                    raise ValueError(f"set_model denied by policy: {model_value}")
                with self._product_session_lock:
                    previous, previous_model = (
                        self.current_runtime_config(),
                        self._model_override,
                    )
                    agent_config = (
                        getattr(self._agent, "config", {}) if self._agent else {}
                    )
                    agent_providers = (
                        agent_config.get("providers")
                        if isinstance(agent_config, dict)
                        and isinstance(agent_config.get("providers"), dict)
                        else {}
                    )
                    rollback_model = (
                        "default_model" in agent_providers,
                        agent_providers.get("default_model"),
                    )
                    prepared = apply_dotted_overrides(
                        previous, {"providers.default_model": model_value}
                    )
                    try:
                        self._model_override = model_value
                        if not self._apply_model_override():
                            raise RuntimeError("failed to apply model configuration")
                        if durable_reconfigure is not None:
                            durable_reconfigure(prepared)
                    except Exception as error:
                        self._model_override = previous_model
                        rolled_back = True
                        try:
                            providers = (
                                self._agent.config.setdefault("providers", {})
                                if self._agent
                                else {}
                            )
                            if rollback_model[0]:
                                providers["default_model"] = rollback_model[1]
                            else:
                                providers.pop("default_model", None)
                        except Exception:
                            rolled_back = False
                            logger.exception("Failed to roll back model configuration")
                        if not isinstance(error, OSError) or not rolled_back:
                            self.transition_product_session(
                                "fail",
                                "runtime_reconfigure_failed",
                                "failed to apply model configuration",
                            )
                        raise
                    self.session.metadata["model"] = model_value
                    self._prepared_runtime_config = prepared
                return {"status": "ok", "model": model_value}
            case "set_mode":
                mode_value = payload.get("mode")
                if not isinstance(mode_value, str) or not mode_value.strip():
                    raise ValueError("set_mode requires non-empty 'mode'")
                mode_value = mode_value.strip()
                with self._product_session_lock:
                    overrides = {"mode": mode_value}
                    previous, previous_mode = self.current_runtime_config(), self._mode
                    agent_config = (
                        getattr(self._agent, "config", {}) if self._agent else {}
                    )
                    mode_restore = (
                        "mode",
                        "mode" in agent_config,
                        agent_config.get("mode"),
                    )
                    prepared = apply_dotted_overrides(previous, overrides)
                    try:
                        if (
                            self._agent
                            and self._agent.apply_runtime_overrides(overrides) is False
                        ):
                            raise RuntimeError("failed to apply mode configuration")
                        if durable_reconfigure is not None:
                            durable_reconfigure(prepared)
                    except Exception as error:
                        rolled_back = self._rollback_runtime_overrides(
                            {"mode": previous.get("mode")}, mode_restore
                        )
                        self._mode = previous_mode
                        if not isinstance(error, OSError) or not rolled_back:
                            self.transition_product_session(
                                "fail",
                                "runtime_reconfigure_failed",
                                "failed to apply mode configuration",
                            )
                        raise
                    self._mode = mode_value
                    self.session.metadata["mode"] = mode_value
                    self._prepared_runtime_config = prepared
                return {"status": "ok", "mode": mode_value}
            case "session_child_next" | "session_child_previous" | "session_parent":
                child_session_id = payload.get("child_session_id") or payload.get(
                    "childSessionId"
                )
                parent_session_id = payload.get("parent_session_id") or payload.get(
                    "parentSessionId"
                )
                target_session_id = payload.get("target_session_id") or payload.get(
                    "targetSessionId"
                )

                def _norm(value: Any) -> Optional[str]:
                    if not isinstance(value, str):
                        return None
                    trimmed = value.strip()
                    return trimmed or None

                child_session_id = _norm(child_session_id)
                parent_session_id = _norm(parent_session_id)
                target_session_id = _norm(target_session_id)
                if command == "session_parent":
                    resolved_target = (
                        target_session_id or parent_session_id or child_session_id
                    )
                else:
                    resolved_target = (
                        target_session_id or child_session_id or parent_session_id
                    )
                if not resolved_target:
                    return {
                        "status": "ok",
                        "command": command,
                        "switched": False,
                        "reason": "target_missing",
                    }
                target_record = await self.registry.get(resolved_target)
                if target_record is None:
                    return {
                        "status": "ok",
                        "command": command,
                        "switched": False,
                        "reason": "target_not_found",
                        "target_session_id": resolved_target,
                    }
                return {
                    "status": "ok",
                    "command": command,
                    "switched": True,
                    "target_session_id": resolved_target,
                    "active_session_id": resolved_target,
                    "child_session_id": child_session_id,
                    "parent_session_id": parent_session_id,
                }
            case "run_tests":
                if self._debug_permissions_enabled():
                    event_payload = await self._emit_debug_permission_request(payload)
                    return {
                        "status": "ok",
                        "debug": True,
                        "request_id": event_payload.get("request_id"),
                    }
                raise NotImplementedError("run_tests not yet implemented")
            case "apply_diff":
                raise NotImplementedError("apply_diff not yet implemented")
            case "respond_permission" | "permission_response":
                request_id = (
                    payload.get("request_id")
                    or payload.get("requestId")
                    or payload.get("permission_id")
                    or payload.get("permissionId")
                    or payload.get("id")
                )
                response = (
                    payload.get("response")
                    or payload.get("decision")
                    or payload.get("default")
                )
                responses = payload.get("responses")
                items = payload.get("items")
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError(
                        "respond_permission requires non-empty 'request_id'/'permission_id'/'id'"
                    )
                if isinstance(items, dict) and not isinstance(responses, dict):
                    responses = {"items": dict(items)}
                canonical_responses = (
                    _canonical_permission_responses(responses)
                    if isinstance(responses, dict)
                    else None
                )
                normalized_request_id = request_id.strip()
                pending = self.session.metadata.get("pending_permissions")
                pending_request = (
                    next(
                        (
                            entry.get("request")
                            for entry in pending
                            if isinstance(entry, dict)
                            and entry.get("request_id") == normalized_request_id
                        ),
                        {},
                    )
                    if isinstance(pending, list)
                    else {}
                )
                requested_ids = _permission_item_ids(pending_request)
                resolution = _canonical_permission_resolution(
                    response,
                    canonical_responses,
                    requested_ids,
                    _permission_default_response(self.current_runtime_config()),
                )
                queue = getattr(self, "_permission_queue", None)
                if queue is None:
                    if self._debug_permissions_enabled():
                        response_payload: Dict[str, Any] = {
                            "request_id": normalized_request_id
                        }
                        if canonical_responses is not None:
                            response_payload["responses"] = canonical_responses
                        else:
                            response_payload["response"] = resolution
                            response_payload["decision"] = resolution
                        with self._product_session_lock:
                            self._update_pending_permissions(
                                "permission_response",
                                response_payload,
                                source="session",
                            )
                        await self.publish_event_async(
                            EventType.PERMISSION_RESPONSE, response_payload
                        )
                        return {
                            "status": "ok",
                            "request_id": normalized_request_id,
                            "decision": resolution,
                            "delivered": response_payload,
                            "debug": True,
                        }
                    self._discard_undeliverable_permission(normalized_request_id)
                    raise ValueError("no permission request is active")
                if canonical_responses is not None:
                    item: Dict[str, Any] = {
                        "request_id": normalized_request_id,
                        "responses": canonical_responses,
                    }
                else:
                    if not isinstance(response, str) or not response.strip():
                        raise ValueError(
                            "respond_permission requires non-empty 'response' when 'responses' is not provided"
                        )
                    item = {
                        "permission_id": normalized_request_id,
                        "response": resolution,
                    }
                with self._product_session_lock:
                    self.transition_product_session(
                        "resolve_approval", normalized_request_id, resolution
                    )
                    try:
                        put_nowait = getattr(queue, "put_nowait", None)
                        if callable(put_nowait):
                            put_nowait(item)
                        else:
                            queue.put(item)
                    except Exception as exc:
                        self.transition_product_session(
                            "fail",
                            "permission_delivery_failed",
                            "failed to deliver permission response",
                        )
                        raise RuntimeError(
                            f"failed to deliver permission response: {exc}"
                        ) from exc
                    self._update_pending_permissions(
                        "permission_response", item, source="session", consume_fifo=True
                    )
                return {
                    "status": "ok",
                    "request_id": normalized_request_id,
                    "decision": resolution,
                    "delivered": item,
                }
            case _:
                raise ValueError(f"Unsupported command: {command}")
    @staticmethod
    def _target_route(target: Mapping[str, Any]) -> str:
        route = str(target.get("route_id") or "").strip()
        if route:
            return route
        provider = str(target.get("provider_id") or "").strip()
        model = str(target.get("model_id") or "").strip()
        if not provider or not model:
            raise ValueError("locked model target is missing provider/model identity")
        return f"{provider}/{model}"

    def _locked_target(self, role: str | None = None) -> dict[str, Any]:
        lock = self._model_role_lock
        if not isinstance(lock, Mapping):
            raise ModelRoleResolutionError(ModelRoleProblem("known_role_unbound", "no model-role lock is active", "$.roles"))
        chosen = str(role or self._active_model_role or (lock.get("defaults") or {}).get("role") or "").strip()
        if not chosen:
            raise ModelRoleResolutionError(ModelRoleProblem("known_role_unbound", "no active model role is bound", "$.defaults.role"))
        return select_role_target(lock, chosen)

    def install_model_role_lock(self, lock: Mapping[str, Any]) -> Dict[str, Any]:
        self._model_role_lock = lock.as_dict() if hasattr(lock, "as_dict") else dict(lock)
        role = str((self._model_role_lock.get("defaults") or {}).get("role") or "").strip()
        route = self._target_route(self._locked_target(role))
        self._active_model_role, self._model_override = role, route
        self.session.metadata.update({
            "model_role_lock": dict(self._model_role_lock),
            "model_role_lock_hash": str(self._model_role_lock.get("lock_hash") or ""),
            "active_model_role": role,
            "model": route,
        })
        prepared = self.prepare_runtime_config()
        prepared["model_role_lock"] = dict(self._model_role_lock)
        prepared["active_model_role"] = role
        prepared["providers"] = dict(prepared.get("providers") or {})
        prepared["providers"]["default_model"] = route
        self._prepared_runtime_config = prepared
        return dict(prepared)

    def _load_base_config(self) -> Dict[str, Any]:
        if isinstance(self._base_config_cache, dict): return dict(self._base_config_cache)
        cfg = load_agent_config(self.request.config_path)
        if not isinstance(cfg, dict): raise TypeError("agent config loader must return a mapping")
        self._base_config_cache = dict(cfg)
        return dict(self._base_config_cache)

    def prepare_runtime_config(self) -> Dict[str, Any]:
        """Freeze the exact base configuration and overrides passed to the agent."""
        if self._prepared_runtime_config is not None:
            return dict(self._prepared_runtime_config)
        overrides = dict(self.request.overrides or {})
        if isinstance(self._model_override, str) and self._model_override.strip():
            overrides["providers.default_model"] = self._model_override.strip()
        if isinstance(self._mode, str) and self._mode.strip():
            overrides["mode"] = self._mode.strip()
        permission_mode = (self.request.permission_mode or self.session.metadata.get("permission_mode") or "").strip().lower()
        if permission_mode in {"prompt", "ask", "interactive"}:
            overrides.setdefault("permissions.options.mode", "prompt")
            overrides.setdefault("permissions.options.default_response", "reject")
            overrides.setdefault("permissions.edit.default", "ask")
            overrides.setdefault("permissions.shell.default", "ask")
            overrides.setdefault("permissions.webfetch.default", "ask")
            overrides.setdefault("permissions.read.default", "ask")
            self.request.permission_mode = permission_mode
            self.session.metadata["permission_mode"] = permission_mode
        base_cfg = self._load_base_config()
        workspace_guess_path = self._resolve_workspace_guess(base_cfg)
        if workspace_guess_path:
            self._workspace_path = workspace_guess_path
            workspace = str(workspace_guess_path)
            self.request.workspace = workspace
            overrides["workspace.root"] = workspace
            try:
                rules = load_permission_rules(workspace_guess_path)
            except Exception:
                rules = []
            for key, value in build_permission_overrides(base_cfg, rules).items() if rules else ():
                existing = overrides.get(key)
                if key in overrides and isinstance(existing, list) and isinstance(value, list):
                    value = existing + [item for item in value if item not in existing]
                overrides[key] = value
        self.request.overrides = overrides
        self._prepared_runtime_config = apply_dotted_overrides(base_cfg, overrides)
        return dict(self._prepared_runtime_config)

    def current_runtime_config(self) -> Dict[str, Any]:
        return dict(config) if isinstance((config := getattr(self._agent, "config", None)), dict) else self.prepare_runtime_config()

    def _resolve_workspace_guess(self, base_cfg: Dict[str, Any]) -> Optional[Path]:
        candidate: Any = self.request.workspace
        if not candidate and isinstance(base_cfg, dict):
            workspace = base_cfg.get("workspace")
            candidate = (workspace.get("root") or workspace.get("path")) if isinstance(workspace, dict) else None
        candidate = candidate or f"tmp/agent_ws_{os.path.basename(self.request.config_path).split('.')[0]}"
        try:
            path = Path(str(candidate)).expanduser()
            if not path.is_absolute():
                root = Path(__file__).resolve().parents[3]
                path = root / path if path.parts[:1] == ("tmp",) else root / "tmp" / path
            return path.resolve()
        except Exception:
            return None

    def _parse_replay_path(self, task_text: str) -> Optional[Path]:
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

    async def _maybe_publish_todo_snapshot(self, workspace_dir: Optional[Path], *, call_id: str) -> None:
        if not self._todo_enabled or not workspace_dir:
            return
        envelope = self._load_todo_envelope_from_disk(workspace_dir)
        if envelope is None:
            return
        self.session.metadata["todo_last_update"] = envelope
        self._persist_metadata_snapshot_threadsafe()
        await self.publish_event_async(
            EventType.TOOL_RESULT,
            {"call_id": call_id, "todo": envelope},
        )

    async def _ensure_agent_initialized(self) -> None:
        if self._agent is not None:
            return
        overrides = dict(self.request.overrides or {})
        frozen = self.current_runtime_config()
        if redaction.contains_provider_auth_runtime(frozen) or redaction.contains_provider_auth_runtime(overrides):
            logger.warning(
                "Ignoring inline provider credentials; attach credentials through the provider broker."
            )
        frozen = redaction.strip_provider_auth_runtime(frozen)
        overrides = redaction.strip_provider_auth_runtime(overrides)
        descriptor, snapshot = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream: json.dump(frozen, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
            self._agent = self.agent_factory(snapshot, self.request.workspace, overrides or None)
            if hasattr(self._agent, "config_path"): self._agent.config_path = self.request.config_path
            await asyncio.to_thread(self._agent.initialize)
        finally: Path(snapshot).unlink(missing_ok=True)
        workspace_dir = Path(self._agent.workspace_dir).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_path = workspace_dir
        if self._model_override:
            self._apply_model_override()
        if self._todo_enabled:
            meta = self.session.metadata if isinstance(self.session.metadata, dict) else {}
            if not isinstance(meta.get("todo_last_update"), dict):
                await self._maybe_publish_todo_snapshot(workspace_dir, call_id="todo:snapshot:init")
        try:
            if self._checkpoint_manager is None:
                self._checkpoint_manager = CheckpointManager(workspace_dir)
                self._checkpoint_manager.create_checkpoint("Session start")
        except Exception:
            self._checkpoint_manager = None

    def _require_execution_correlation(
        self, input_id: Optional[str], turn_id: Optional[str]
    ) -> Dict[str, str]:
        if not isinstance(input_id, str) or not input_id.strip():
            raise RuntimeProtocolError("runtime_protocol_error")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise RuntimeProtocolError("runtime_protocol_error")
        turn = self.session.turns_by_id.get(turn_id)
        if (
            turn is None
            or turn.input_id != input_id
            or turn.terminal_outcome is not None
            or self.session.active_turn_id != turn_id
        ):
            raise RuntimeProtocolError("runtime_protocol_error")
        return {"input_id": input_id, "turn_id": turn_id}

    async def _execute_replay_task(
        self,
        task_text: str,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        replay_path = self._parse_replay_path(task_text)
        if replay_path is None:
            raise ValueError("replay task missing path (expected replay:<path>)")
        correlation = self._require_execution_correlation(input_id, turn_id)
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
                        raise RuntimeProtocolError(
                            "runtime_protocol_error"
                        ) from None
                    payload_raw = (
                        entry[payload_fields[0]] if payload_fields else {}
                    )
                    payload = _validate_replay_event_payload(
                        event_type, payload_raw
                    )
                    delay_ms = entry[delay_fields[0]] if delay_fields else 0
                    if (
                        not isinstance(delay_ms, int)
                        or isinstance(delay_ms, bool)
                        or delay_ms < 0
                    ):
                        raise RuntimeProtocolError("runtime_protocol_error")
                    turn = entry.get("turn")
                    if turn is not None and (
                        not isinstance(turn, int)
                        or isinstance(turn, bool)
                        or turn < 0
                    ):
                        raise RuntimeProtocolError("runtime_protocol_error")
                    if seen_run_finished:
                        raise RuntimeProtocolError("runtime_protocol_error")
                    if (
                        seen_completion
                        and event_type is not EventType.RUN_FINISHED
                    ):
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
                    translated = self._translate_runtime_event(
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
            self.session.metadata
            if isinstance(self.session.metadata, dict)
            else {}
        )
        meta = dict(meta)
        meta["replay_fixture"] = {"path": str(replay_path)}
        self.session.metadata = meta
        await self.registry.update_metadata(
            self.session.session_id, metadata=meta
        )

        terminal_events: list[TranslatedRuntimeEvent] = []
        published_events = 0
        for (
            event_type,
            raw_payload,
            turn,
            event_contract,
        ), delay_ms in zip(prepared_events, prepared_delays):
            if self._stop_event.is_set():
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
                        payload[field] = _strip_completion_sentinels(
                            payload[field]
                        )
            if event_type in {EventType.TOOL_RESULT, EventType.TOOL_RESULT_DOT}:
                todo_update = payload.get("todo")
                if isinstance(todo_update, dict):
                    self.session.metadata["todo_last_update"] = dict(todo_update)
                    await self.registry.update_metadata(
                        self.session.session_id,
                        metadata=self.session.metadata,
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
                await self.publish_event_async(
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
                        "mode": self._mode,
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

    async def _run(self) -> None:
        session_started_at = time.monotonic()
        input_inflight = False
        await self.registry.update_status(
            self.session.session_id, SessionStatus.RUNNING
        )
        try:
            # Safety: never auto-wipe an existing workspace when running interactive sessions
            # via the CLI bridge. The engine historically treated workspaces as disposable
            # sandboxes; for a Claude Code-style experience we must preserve the user's
            # working directory unless explicitly overridden by the caller.
            os.environ.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
            initial_task = (self.request.task or "").strip()
            base_cfg = (
                {}
                if self._parse_replay_path(initial_task) is not None
                else self.prepare_runtime_config()
            )
            try:
                todo_cfg = GuardrailCoordinator(base_cfg).todo_config()
            except Exception:
                todo_cfg = {"enabled": False}
            self._todo_enabled = bool(todo_cfg.get("enabled"))
            await self._maybe_publish_todo_snapshot(
                self._workspace_path, call_id="todo:snapshot:init"
            )
            try:
                if self._workspace_path and self._checkpoint_manager is None:
                    self._checkpoint_manager = CheckpointManager(self._workspace_path)
                    self._checkpoint_manager.create_checkpoint("Session start")
            except Exception:
                self._checkpoint_manager = None
            try:
                catalog_payload = self.get_skill_catalog()
                await self.publish_event_async(
                    EventType.SKILLS_CATALOG, catalog_payload
                )
                selection = (
                    (catalog_payload.get("selection") or {})
                    if isinstance(catalog_payload, dict)
                    else {}
                )
                if selection:
                    await self.publish_event_async(
                        EventType.SKILLS_SELECTION, {"selection": selection}
                    )
            except Exception:
                pass
            if initial_task:
                self._accepted_task_texts.append(initial_task)
                initial_turn = self.session.turns_by_id.get(
                    self.session.active_turn_id or ""
                )
                if initial_turn is None:
                    raise RuntimeProtocolError("runtime_protocol_error")
                self._input_queue.put_nowait(
                    {
                        "content": initial_task,
                        "attachments": [],
                        "input_id": initial_turn.input_id,
                        "turn_id": initial_turn.turn_id,
                    }
                )

            while not self._stop_event.is_set():
                try:
                    next_input = await self._input_queue.get()
                    input_inflight = True
                except asyncio.CancelledError:  # pragma: no cover - defensive
                    break
                if next_input is None:
                    self._input_queue.task_done()
                    input_inflight = False
                    break
                await self._resume_event.wait()
                if self._stop_event.is_set():
                    self._input_queue.task_done()
                    input_inflight = False
                    break
                task_payload = dict(next_input)
                task_text = str(task_payload.get("content", ""))
                task_input_id = task_payload.get("input_id")
                task_turn_id = task_payload.get("turn_id")
                task_turn = (
                    self.session.turns_by_id.get(task_turn_id)
                    if isinstance(task_turn_id, str)
                    else None
                )
                if (
                    task_turn is not None
                    and self.session.active_turn_id != task_turn.turn_id
                ):
                    raise RuntimeError("turn queue correlation mismatch")
                task_received_at = time.monotonic()
                if self._parse_replay_path(task_text) is not None:
                    result = await self._execute_replay_task(
                        task_text,
                        input_id=(
                            task_input_id
                            if isinstance(task_input_id, str)
                            else None
                        ),
                        turn_id=(
                            task_turn_id
                            if isinstance(task_turn_id, str)
                            else None
                        ),
                    )
                    after_execute_task_at = time.monotonic()
                else:
                    attachment_ids = task_payload.get("attachments") or []
                    await self._ensure_agent_initialized()
                    if self._stop_event.is_set():
                        self._input_queue.task_done()
                        input_inflight = False
                        break
                    after_agent_init_at = time.monotonic()
                    accepted_text = task_text
                    attachment_text = self._format_attachment_helper(attachment_ids)
                    if attachment_text:
                        task_text = f"{task_text.rstrip()}\n\n{attachment_text}"
                    if accepted_text.strip():
                        self._accepted_task_texts.append(accepted_text)
                        self._accepted_task_texts = self._accepted_task_texts[-20:]
                    if self._profile_timing_enabled:
                        self._active_bridge_timing_context = {
                            "session_to_task_received_seconds": round(
                                task_received_at - session_started_at, 6
                            ),
                            "task_received_to_agent_initialized_seconds": round(
                                after_agent_init_at - task_received_at, 6
                            ),
                        }
                    result = await asyncio.to_thread(
                        self._execute_task,
                        task_text,
                        input_id=(
                            task_input_id
                            if isinstance(task_input_id, str)
                            else None
                        ),
                        turn_id=(
                            task_turn_id
                            if isinstance(task_turn_id, str)
                            else None
                        ),
                    )
                    after_execute_task_at = time.monotonic()
                    if self._profile_timing_enabled and isinstance(result, dict):
                        timing = result.setdefault("bridge_timing", {})
                        if isinstance(timing, dict):
                            timing.update(
                                {
                                    "session_to_task_received_seconds": round(
                                        task_received_at - session_started_at, 6
                                    ),
                                    "task_received_to_agent_initialized_seconds": round(
                                        after_agent_init_at - task_received_at, 6
                                    ),
                                    "execute_task_wall_seconds": round(
                                        after_execute_task_at - after_agent_init_at, 6
                                    ),
                                }
                            )
                    self._active_bridge_timing_context = None
                metadata = (
                    self.session.metadata
                    if isinstance(self.session.metadata, dict)
                    else {}
                )
                one_shot = bool(
                    metadata.get("non_interactive_cli_session")
                    or metadata.get("cli_session_kind") == "oneshot"
                )
                with self._product_session_lock:
                    product_session = getattr(self.session, "product_session", None)
                    if product_session is None:
                        durable_success = True
                    else:
                        product_status = product_session.read_model
                        if (
                            one_shot
                            and product_status.status == "running"
                            and not self._stop_event.is_set()
                        ):
                            self.transition_product_session("complete")
                        durable_success = (
                            product_session.read_model.status == "completed"
                            if one_shot
                            else product_status.status not in {"failed", "canceled"}
                        )
                if durable_success:
                    try:
                        await self.registry.update_metadata(
                            self.session.session_id,
                            completion_summary=result.get("completion_summary"),
                            reward_summary=result.get("reward_metrics"),
                            logging_dir=result.get("logging_dir"),
                            metadata=self.session.metadata,
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
                            self.session.session_id,
                        )
                        durable_success = False
                if task_turn is not None:
                    completion_summary = result.get("completion_summary") or {}
                    completion_reason = str(
                        completion_summary.get("reason")
                        or completion_summary.get("exit_kind")
                        or "turn_execution_failed"
                    )
                    if self._stop_event.is_set():
                        await self._finish_turn(
                            task_turn, "cancelled", reason=completion_reason
                        )
                    elif bool(completion_summary.get("completed")):
                        await self._finish_turn(
                            task_turn,
                            "completed",
                            completed_payload=result.get("_turn_completion_payload"),
                        )
                    else:
                        await self._finish_turn(
                            task_turn,
                            "failed",
                            reason=completion_reason,
                            error_code=completion_reason,
                        )
                if durable_success:
                    for (
                        event_type,
                        event_payload,
                        event_turn,
                        event_contract,
                    ) in result.pop("_terminal_events", ()):
                        await self.publish_event_async(
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
                if self._profile_timing_enabled and isinstance(result, dict):
                    timing = result.setdefault("bridge_timing", {})
                    if isinstance(timing, dict):
                        timing["post_execute_registry_update_seconds"] = round(
                            after_registry_update_at - after_execute_task_at, 6
                        )
                self._input_queue.task_done()
                input_inflight = False
                if one_shot or not durable_success:
                    break
            if self._stop_event.is_set():
                await self._terminalize_admitted_turns(
                    outcome="cancelled", reason="stop_requested"
                )

            product_session = getattr(self.session, "product_session", None)
            if product_session is None:
                metadata = (
                    self.session.metadata
                    if isinstance(self.session.metadata, dict)
                    else {}
                )
                legacy_one_shot = bool(
                    metadata.get("non_interactive_cli_session")
                    or metadata.get("cli_session_kind") == "oneshot"
                )
                final_status = (
                    SessionStatus.STOPPED
                    if self._stop_event.is_set() and not legacy_one_shot
                    else SessionStatus.COMPLETED
                )
            else:
                product_state = product_session.read_model.status
                if product_state == "running" and not self._stop_event.is_set():
                    self.transition_product_session("complete")
                elif product_state not in {"completed", "failed", "canceled"}:
                    self.transition_product_session("cancel", "runtime stopped")
                product_state = product_session.read_model.status
                final_status = {
                    "completed": SessionStatus.COMPLETED,
                    "failed": SessionStatus.FAILED,
                    "canceled": SessionStatus.STOPPED,
                }[product_state]
            await self.registry.update_status(self.session.session_id, final_status)
        except Exception as exc:  # noqa: BLE001
            error_code = _safe_runtime_error_code(
                getattr(exc, "code", None),
                default=(
                    "runtime_protocol_error"
                    if isinstance(exc, RuntimeProtocolError)
                    else "worker_crash"
                ),
            )
            if input_inflight:
                self._input_queue.task_done()
                input_inflight = False
            logger.error(
                "Session %s failed with code=%s", self.session.session_id, error_code
            )
            await self._terminalize_admitted_turns(
                outcome="failed", reason=error_code, error_code=error_code
            )
            self.transition_product_session("fail", error_code, "runtime failure")
            product_state = getattr(
                getattr(
                    getattr(self.session, "product_session", None), "read_model", None
                ),
                "status",
                "failed",
            )
            await self.registry.update_status(
                self.session.session_id,
                {
                    "completed": SessionStatus.COMPLETED,
                    "failed": SessionStatus.FAILED,
                    "canceled": SessionStatus.STOPPED,
                }.get(product_state, SessionStatus.FAILED),
            )
            await self._publish_session_failure(error_code)
        finally:
            self._closed = True
            await self._enqueue_termination()

    def _load_todo_envelope_from_disk(self, workspace_dir: Path) -> Optional[Dict[str, Any]]:
        try:
            store = TodoStore(str(workspace_dir), load_existing=True)
            snapshot = store.snapshot()
            return project_store_snapshot_to_tui_envelope(snapshot, scope_key="main", scope_label="main")
        except Exception:
            return None

    async def _terminalize_admitted_turns(
        self,
        *,
        outcome: str,
        reason: str,
        error_code: Optional[str] = None,
    ) -> None:
        """Persist one terminal event for every admitted nonterminal turn."""
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError("unsupported bulk terminal outcome")
        async with self.session.admission_lock:
            ordered_ids: list[str] = []
            if self.session.active_turn_id:
                ordered_ids.append(self.session.active_turn_id)
            ordered_ids.extend(
                turn_id
                for turn_id in self.session.queued_turn_ids
                if turn_id not in ordered_ids
            )
            ordered_ids.extend(
                turn_id
                for turn_id, turn in self.session.turns_by_id.items()
                if turn.terminal_outcome is None and turn_id not in ordered_ids
            )
        for turn_id in ordered_ids:
            turn = self.session.turns_by_id.get(turn_id)
            if turn is None or turn.terminal_outcome is not None:
                continue
            await self._finish_turn(
                turn,
                outcome,
                reason=reason,
                error_code=error_code,
                advance_queue=False,
            )
        while True:
            try:
                self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._input_queue.task_done()
        async with self.session.admission_lock:
            self.session.active_turn_id = None
            self.session.queued_turn_ids.clear()
            self.session.turn_admission = self.session.turn_admission.__class__.IDLE

    async def _publish_session_failure(self, error_code: str) -> None:
        if self._session_failure_published:
            return
        self._session_failure_published = True
        await self.publish_event_async(
            EventType.ERROR,
            {"code": _safe_runtime_error_code(error_code)},
            classification="bridge_host",
            family="session.failure",
            actor="service",
            visibility="host",
        )

    async def _enqueue_termination(self) -> None:
        queue = self.session.event_queue
        try:
            await queue.put(None)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning("Event queue full while terminating session %s", self.session.session_id)

    def _rollback_runtime_overrides(self, overrides: Dict[str, Any], restore: Optional[tuple[str, bool, Any]] = None) -> bool:
        try: rolled_back = not self._agent or self._agent.apply_runtime_overrides(overrides) is not False
        except Exception: logger.exception("Failed to roll back runtime configuration"); return False
        if self._agent and restore:
            if restore[1]: self._agent.config[restore[0]] = restore[2]
            else: self._agent.config.pop(restore[0], None)
        return rolled_back

    def _apply_model_override(self) -> bool:
        if not self._agent or not self._model_override:
            return True
        try:
            overrides: Dict[str, Any] = {
                "providers.default_model": self._model_override,
                "active_model_role": self._active_model_role,
            }
            if self._model_role_lock is not None:
                overrides["model_role_lock"] = dict(self._model_role_lock)
            return self._agent.apply_runtime_overrides(overrides) is not False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to apply model override: %s", exc)
            return False

    def _persist_metadata_snapshot_threadsafe(self) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return

        async def persist_latest_metadata() -> None:
            await self.registry.update_metadata(
                self.session.session_id,
                metadata=dict(self.session.metadata or {}),
            )

        try:
            asyncio.run_coroutine_threadsafe(
                persist_latest_metadata(),
                loop,
            )
        except Exception:
            pass

    def _debug_permissions_enabled(self) -> bool:
        try:
            meta = self.session.metadata or {}
            if isinstance(meta, dict) and meta.get("debug_permissions"):
                return True
        except Exception:
            pass
        return bool(os.environ.get("BREADBOARD_DEBUG_PERMISSIONS"))

    async def _emit_debug_permission_request(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = dict(payload or {})
        request_id = data.get("request_id") or f"debug-perm-{uuid.uuid4().hex[:8]}"
        suite = data.get("suite") if isinstance(data.get("suite"), str) else None
        summary = f"Tool requests permission to run bash{f' ({suite})' if suite else ''}."
        event_payload = {
            "request_id": str(request_id),
            "tool": "bash",
            "kind": "run",
            "rewindable": True,
            "summary": summary,
            "default_scope": "project",
            "metadata": {
                "function": "run_shell",
                "command": "pwd",
                "kind": "run",
            },
        }
        self._update_pending_permissions("permission_request", event_payload, source="session")
        await self.publish_event_async(EventType.PERMISSION_REQUEST, event_payload)
        return event_payload

    def _pending_permission_key(self, entry: Dict[str, Any]) -> tuple[str, str, str]:
        source = str(entry.get("source") or "session")
        task_id = str(entry.get("task_session_id") or "")
        req_id = str(entry.get("request_id") or entry.get("id") or "")
        return source, task_id, req_id

    def _infer_permission_category(self, request_id: str) -> Optional[str]:
        pending = self.session.metadata.get("pending_permissions")
        if not isinstance(pending, list):
            return None
        for entry in pending:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("request_id") or "") != request_id:
                continue
            request = entry.get("request") or {}
            if isinstance(request, dict):
                category = request.get("category")
                if isinstance(category, str) and category.strip():
                    return category.strip().lower()
                items = request.get("items")
                if isinstance(items, list) and items:
                    first = items[0] if isinstance(items[0], dict) else {}
                    cat = first.get("category") if isinstance(first, dict) else None
                    if isinstance(cat, str) and cat.strip():
                        return cat.strip().lower()
            return None
        return None

    def _update_pending_permissions(
        self, kind: str, info: Dict[str, Any], *, source: str = "session",
        task_session_id: Optional[str] = None, subagent_type: Optional[str] = None,
        consume_fifo: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        req_id = next((info.get(key) for key in ("request_id", "requestId", "permission_id", "permissionId", "id")
                       if info.get(key)), None)
        if not isinstance(req_id, str) or not req_id.strip(): return None
        request_id = req_id.strip()
        with self._product_session_lock:
            pending = self.session.metadata.get("pending_permissions")
            pending = pending if isinstance(pending, list) else []
            entry: Dict[str, Any] = {"source": str(source or "session"), "request_id": request_id}
            entry.update({key: value for key, value in (("task_session_id", task_session_id), ("subagent_type", subagent_type)) if value})
            entry_key = self._pending_permission_key(entry); activate = None; ready = None
            project_before_activation = kind == "permission_response"
            if kind == "permission_request":
                entry["request"] = dict(info or {})
                match = next((i for i, item in enumerate(pending) if self._pending_permission_key(item) == entry_key), None)
                normalized = list(pending); project_before_activation = match is not None
                if match is None:
                    normalized.append(entry); activate = entry if not pending else None
                else:
                    normalized[match] = entry; activate = entry if match == 0 else None
            elif kind == "permission_response":
                if not consume_fifo:
                    suppressed = self._consumed_permission_responses.get(entry_key, 0)
                    if suppressed:
                        if suppressed == 1: self._consumed_permission_responses.pop(entry_key)
                        else: self._consumed_permission_responses[entry_key] = suppressed - 1
                        return None
                match = next((i for i, item in enumerate(pending) if
                              (str(item.get("request_id") or item.get("id") or "") == request_id if consume_fifo else self._pending_permission_key(item) == entry_key)), None)
                normalized = list(pending)
                if match is not None and not consume_fifo and match:
                    normalized[match] = {**normalized[match], "deferred_response": dict(info)}; ready = []
                elif match is not None:
                    product_session = getattr(self.session, "product_session", None)
                    if not consume_fifo:
                        if product_session:
                            request = normalized[match].get("request"); responses = info.get("responses") or info.get("items"); self.transition_product_session("resolve_approval", request_id, _canonical_permission_resolution(info.get("response") or info.get("decision"), responses, _permission_item_ids(request), _permission_default_response(self.current_runtime_config())))
                        ready = [dict(info)]
                    if consume_fifo:
                        consumed_key = self._pending_permission_key(normalized[match]); self._consumed_permission_responses[consumed_key] = self._consumed_permission_responses.get(consumed_key, 0) + 1
                    normalized.pop(match)
                    while ready is not None and normalized and isinstance(normalized[0].get("deferred_response"), dict):
                        self.session.metadata["pending_permissions"] = normalized; deferred = dict(normalized[0]["deferred_response"]); deferred_id = str(normalized[0].get("request_id") or "")
                        if product_session:
                            request = normalized[0].get("request") if isinstance(normalized[0].get("request"), dict) else {}; operation = str(request.get("operation") or request.get("tool") or request.get("category") or "runtime permission")
                            self.transition_product_session("request_approval", deferred_id, operation)
                            self.transition_product_session("resolve_approval", deferred_id, _canonical_permission_resolution(deferred.get("response") or deferred.get("decision"), deferred.get("responses") or deferred.get("items"), _permission_item_ids(request), _permission_default_response(self.current_runtime_config())))
                        normalized.pop(0); ready.append(deferred)
                    activate = normalized[0] if match == 0 and normalized else None
            else: return None
            product_session = getattr(self.session, "product_session", None)
            if project_before_activation:
                if normalized: self.session.metadata["pending_permissions"] = normalized
                else: self.session.metadata.pop("pending_permissions", None)
            if product_session and activate and product_session.read_model.status == "running":
                request = activate.get("request") if isinstance(activate.get("request"), dict) else {}
                operation = str(request.get("operation") or request.get("tool") or request.get("category") or "runtime permission")
                product_session.request_approval(str(activate.get("request_id") or activate.get("id") or ""), operation)
                self.session.metadata["session_contract"] = product_session.read_model.as_dict()
            if not project_before_activation:
                if normalized: self.session.metadata["pending_permissions"] = normalized
                else: self.session.metadata.pop("pending_permissions", None)
            self._persist_metadata_snapshot_threadsafe()
            return ready

    def _discard_undeliverable_permission(self, request_id: str) -> None:
        with self._product_session_lock:
            pending = self.session.metadata.get("pending_permissions")
            if not isinstance(pending, list): return
            def _usable(entry: Any) -> bool:
                return isinstance(entry, dict) and bool(str(entry.get("request_id") or ""))
            match = next((index for index, entry in enumerate(pending)
                          if _usable(entry) and str(entry.get("request_id")) == request_id), None)
            if match is None: return
            remaining = [entry for index, entry in enumerate(pending) if index != match and _usable(entry)]
            first_valid = next((index for index, entry in enumerate(pending) if _usable(entry)), None)
            is_head = first_valid is not None and match == first_valid
            product_session = getattr(self.session, "product_session", None)
            if is_head and product_session is not None and product_session.read_model.status == "awaiting_approval":
                self.transition_product_session("resolve_approval", request_id, "reject")
                head = remaining[0] if remaining else None
                if head is not None:
                    request = head.get("request") if isinstance(head.get("request"), dict) else {}
                    operation = str(request.get("operation") or request.get("tool") or request.get("category") or "runtime permission")
                    try:
                        self.transition_product_session("request_approval", str(head.get("request_id") or head.get("id") or ""), operation)
                    except Exception:
                        logger.warning("Failed to re-expose pending approval after discarding undeliverable request", exc_info=True)
                self.session.metadata["session_contract"] = product_session.read_model.as_dict()
            if remaining: self.session.metadata["pending_permissions"] = remaining
            else: self.session.metadata.pop("pending_permissions", None)
        self._persist_metadata_snapshot_threadsafe()

    def _rehydrate_pending_permissions(
        self, event_type: str, payload: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        if event_type in {"permission_request", "permission_response"}:
            info = {**dict(payload or {}), **({"_runtime_event": (event_type, dict(payload or {}))} if event_type == "permission_response" else {})}
            return self._update_pending_permissions(event_type, info, source="session")
        if event_type != "task_event": return None
        kind = str((payload or {}).get("kind") or "")
        if kind not in {"permission_request", "permission_response"}: return None
        child_payload = (payload or {}).get("payload") or {}
        child = dict(child_payload) if isinstance(child_payload, dict) else {"payload": child_payload}
        if kind == "permission_response": child["_runtime_event"] = (event_type, dict(payload or {}))
        return self._update_pending_permissions(kind, child,
            source="task", task_session_id=str((payload or {}).get("sessionId") or ""),
            subagent_type=str((payload or {}).get("subagent_type") or ""),
        )

    def _execute_task(
        self,
        task_text: str,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._agent:
            raise RuntimeError("agent missing")
        execute_started_at = time.monotonic()
        emitted_flags: Dict[Any, bool] = {
            "assistant": False,
            EventType.COMPLETION: False,
            EventType.RUN_FINISHED: False,
        }
        self._published_events = 0
        self._product_tool_completions.clear()
        correlation = self._require_execution_correlation(input_id, turn_id)
        terminal_events: list[TranslatedRuntimeEvent] = []
        runtime_event_lock = threading.Lock()
        is_local_agent = bool(getattr(self._agent, "_local_mode", False))
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
                self.publish_event(
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
                        self._ctree_last_node = dict(node)
                    snapshot = (payload or {}).get("snapshot")
                    if isinstance(snapshot, dict):
                        self._ctree_snapshot_cache = dict(snapshot)
                except Exception:
                    pass
            elif event_type == "ctree_snapshot":
                try:
                    if isinstance(payload, dict):
                        self._ctree_snapshot_cache = dict(payload)
                except Exception:
                    pass
            ready_responses = self._rehydrate_pending_permissions(
                event_type, dict(payload or {})
            )
            permission_response_event = (
                event_type == "permission_response"
                or event_type == "task_event"
                and payload.get("kind") == "permission_response"
            )
            if permission_response_event and ready_responses == []:
                return
            translated = self._translate_runtime_event(event_type, payload, turn)
            if not translated:
                return
            evt_type, evt_payload, evt_turn, evt_contract = translated
            try:
                self._record_product_observation(
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
            self.publish_event(
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
            isinstance(self.request.metadata, dict)
            and "enable_remote_stream" in self.request.metadata
        ):
            remote_stream_enabled = bool(
                self.request.metadata.get("enable_remote_stream")
            )
        permission_mode = (
            (
                self.request.permission_mode
                or self.session.metadata.get("permission_mode")
                or ""
            )
            .strip()
            .lower()
        )
        interactive_permissions = permission_mode in {"prompt", "ask", "interactive"}
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
        if interactive_permissions:
            try:
                perms = getattr(self._agent, "config", {}).setdefault("permissions", {})  # type: ignore[attr-defined]
                if not isinstance(perms, dict):
                    perms = {}
                    self._agent.config["permissions"] = perms  # type: ignore[attr-defined]
                opts = perms.get("options")
                if not isinstance(opts, dict):
                    opts = {}
                opts["mode"] = "prompt"
                opts.setdefault("default_response", "reject")
                perms["options"] = opts
            except Exception:
                pass
        if not is_local_agent and (
            interactive_permissions or (self.request.stream and remote_stream_enabled)
        ):
            try:
                from ray.util.queue import Queue
            except ImportError:  # pragma: no cover
                Queue = None  # type: ignore[misc]
            if Queue is not None:
                event_queue = Queue()
                queue_stop, queue_thread = self._start_queue_pump(
                    event_queue,
                    handle_runtime_event,
                    errors=queue_errors,
                )
                logger.info(
                    "session(%s) remote event queue initialized",
                    self.session.session_id,
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
            self._permission_queue = permission_queue
        else:
            self._permission_queue = None
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
        self._install_control_queue(control_queue)
        start_time = time.time()
        run_task_started_at = time.monotonic()
        run_task_error: BaseException | None = None
        try:
            task_context = {}
            try:
                if isinstance(self.session.metadata, dict):
                    task_context = dict(self.session.metadata.get("task_context") or {})
                    if (
                        "task_type" in self.session.metadata
                        and "task_type" not in task_context
                    ):
                        task_context["task_type"] = self.session.metadata.get(
                            "task_type"
                        )
            except Exception:
                task_context = {}
            # The registry-owned ID is authoritative; request metadata cannot retarget
            # credential affinity to another product session.
            task_context["session_id"] = self.session.session_id
            task_context["input_id"] = correlation["input_id"]
            task_context["turn_id"] = correlation["turn_id"]
            task_context["attachment_capabilities"] = dict(
                self._active_attachment_capabilities
            )
            task_context["input_media"] = [
                dict(block) for block in self._active_input_media
            ]
            kernel_emitter_run_dir = None
            kernel_emitter_mode = None
            try:
                meta = (
                    self.session.metadata
                    if isinstance(self.session.metadata, dict)
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
            result = self._agent.run_task(  # type: ignore[call-arg]
                task_text,
                max_iterations=self.request.max_steps,
                stream=self.request.stream,
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
            self._permission_queue = None
            self._install_control_queue(None)
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
                    self._drain_event_queue(event_queue, handle_runtime_event)
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
            "session_id": self.session.session_id,
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
                exchange = strip_provider_exchange_completion_sentinels(
                    raw_exchange
                )
                if (
                    exchange["correlation"] != expected_provider_correlation
                    or exchange["exchange_id"] in exchange_ids
                ):
                    raise RuntimeProtocolError("runtime_protocol_error")
                exchange_ids.add(exchange["exchange_id"])
                provider_exchanges.append(exchange)
            raw_provider_exchange = result.get("provider_exchange")
            provider_exchange = (
                strip_provider_exchange_completion_sentinels(
                    raw_provider_exchange
                )
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
                    content = _strip_completion_sentinels(
                        entry.get("content", "")
                    )
                    text = _assistant_visible_text(content)
                    self.publish_event(
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
                self.publish_event(
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
        usage_payload = self._extract_usage_metrics(
            result, logging_dir, elapsed_ms=elapsed_ms
        )
        completion_payload: Dict[str, Any] = {"summary": completion, "mode": self._mode}
        if self._profile_timing_enabled:
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
                "published_event_count_before_completion": self._published_events,
                "provider_runtime_timing": provider_timing,
                **dict(self._active_bridge_timing_context or {}),
            }
        if usage_payload:
            completion_payload["usage"] = usage_payload
        claim_terminal(EventType.COMPLETION, completion_payload, None, {})
        after_completion_publish_at = time.monotonic()
        if reward:
            self.publish_event(
                EventType.REWARD_UPDATE, {"summary": reward}, **correlation
            )
        if logging_dir:
            self.publish_event(
                EventType.LOG_LINK, {"url": f"file://{logging_dir}"}, **correlation
            )
        logger.info(
            "session(%s) task complete events=%s logging_dir=%s",
            self.session.session_id,
            self._published_events,
            logging_dir,
        )
        finish_payload = {
            "eventCount": self._published_events + 1,
            "steps": completion.get("steps_taken") or result.get("steps_taken"),
            "completed": bool(completion.get("completed")),
            "reason": completion.get("reason") or completion.get("exit_kind"),
            "logging_dir": logging_dir,
        }
        if usage_payload:
            finish_payload["usage"] = usage_payload
        if self._profile_timing_enabled:
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
        if self._profile_timing_enabled:
            result_payload["bridge_timing"] = dict(
                completion_payload.get("bridge_timing") or {}
            )
        return result_payload

    async def _finish_turn(
        self,
        turn: TurnRecord,
        outcome: str,
        *,
        reason: Optional[str] = None,
        error_code: Optional[str] = None,
        completed_payload: Optional[Dict[str, Any]] = None,
        advance_queue: bool = True,
    ) -> bool:
        async with self.session.admission_lock:
            if turn.terminal_outcome is not None:
                return False
            previous_state = turn.state
            turn.terminal_outcome = outcome
            turn.state = outcome
        if outcome == "completed":
            event_type, payload = EventType.TURN_COMPLETED, dict(
                completed_payload or {}
            )
        elif outcome == "cancelled":
            event_type, payload = EventType.TURN_CANCELLED, {
                "reason": reason or "user_requested"
            }
        else:
            event_type, payload = EventType.TURN_FAILED, {
                "error": {
                    "code": _safe_runtime_error_code(
                        error_code, default="turn_execution_failed"
                    )
                }
            }
        terminal_event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            input_id=turn.input_id,
            turn_id=turn.turn_id,
        )
        dispatcher = getattr(self.session, "dispatcher_task", None)
        try:
            if dispatcher is not None and not dispatcher.done():
                await self._enqueue_event_async(terminal_event)
                await self.session.event_queue.join()
            else:
                async with self.session.dispatch_lock:
                    previous_event_seq = self.session.event_seq
                    previous_event_seq_value = terminal_event.seq
                    self.session.event_seq += 1
                    terminal_event.seq = self.session.event_seq
                    try:
                        await self.registry.persist(
                            self.session, terminal_event=terminal_event
                        )
                    except Exception:
                        self.session.event_seq = previous_event_seq
                        terminal_event.seq = previous_event_seq_value
                        raise
                    self.session.event_log.append(terminal_event)
        except Exception:
            async with self.session.admission_lock:
                if not turn.terminal_resolution_committed:
                    turn.terminal_outcome = None
                    turn.state = previous_state
            raise
        if not turn.terminal_resolution_committed:
            async with self.session.admission_lock:
                turn.terminal_outcome = None
                turn.state = previous_state
            raise RuntimeError("turn_terminal_persistence_failed")
        if not advance_queue:
            return True
        async with self.session.admission_lock:
            if self.session.active_turn_id == turn.turn_id:
                self.session.active_turn_id = None
            while self.session.queued_turn_ids:
                next_turn_id = self.session.queued_turn_ids.popleft()
                next_turn = self.session.turns_by_id.get(next_turn_id)
                if next_turn is None or next_turn.terminal_outcome is not None:
                    continue
                next_turn.state = "active"
                self.session.active_turn_id = next_turn.turn_id
                break
            self.session.turn_admission = (
                self.session.turn_admission.__class__.ACTIVE
                if self.session.active_turn_id is not None
                else self.session.turn_admission.__class__.IDLE
            )
        return True

    async def publish_event_async(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        classification: Optional[str] = None,
        family: Optional[str] = None,
        actor: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> None:
        self._touch_last_activity()
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
            input_id=input_id,
            turn_id=turn_id,
            classification=classification,
            family=family,
            actor=actor,
            visibility=visibility,
        )
        await self._enqueue_event_async(event)

    def publish_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        classification: Optional[str] = None,
        family: Optional[str] = None,
        actor: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> None:
        self._touch_last_activity()
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
            input_id=input_id,
            turn_id=turn_id,
            classification=classification,
            family=family,
            actor=actor,
            visibility=visibility,
        )
        loop = self._loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if loop and loop.is_running():
            if running_loop and running_loop is loop:
                loop.create_task(self._enqueue_event_async(event))
                return
            future = asyncio.run_coroutine_threadsafe(self._enqueue_event_async(event), loop)
            future.result()
            return
        try:
            self.session.event_queue.put_nowait(event)
            self._published_events += 1
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning("Event queue full for session %s, dropping event", self.session.session_id)

    async def _enqueue_event_async(self, event: SessionEvent) -> None:
        await self.session.event_queue.put(event)
        self._published_events += 1

    def _touch_last_activity(self) -> None:
        try:
            self.session.last_activity_at = datetime.now(timezone.utc)
        except Exception:
            pass

    def _start_queue_pump(
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

    def get_workspace_dir(self) -> Optional[Path]:
        if self._workspace_path:
            self._workspace_path.mkdir(parents=True, exist_ok=True)
            return self._workspace_path
        candidate = getattr(self._agent, "workspace_dir", None) or self.request.workspace
        if candidate:
            path = Path(candidate).resolve()
            path.mkdir(parents=True, exist_ok=True)
            self._workspace_path = path
            return path
        return None

    def register_attachments(self, entries: Sequence[Dict[str, Any]]) -> None:
        for entry in entries:
            attachment_id = entry.get("id")
            if not attachment_id:
                continue
            self._attachment_store[str(attachment_id)] = dict(entry)

    def _format_attachment_helper(self, attachment_ids: Sequence[str]) -> str:
        helper_lines: list[str] = []
        self._active_attachment_capabilities = {}
        self._active_input_media = []
        for index, key in enumerate(
            dict.fromkeys(str(value) for value in attachment_ids), start=1
        ):
            info = self._attachment_store.get(key)
            if not info:
                continue
            artifact_ref = getattr(self.session, "product_artifacts", {}).get(key)
            if artifact_ref is None:
                raise RuntimeError(f"attachment artifact missing: {key}")
            filename = str(info.get("filename") or key)
            _validate_artifact_name(key)
            uri = f"attachment://{artifact_ref.digest}"
            self._active_attachment_capabilities[uri] = artifact_ref.as_dict()
            if str(artifact_ref.media_type).startswith("image/"):
                self._active_input_media.append(
                    {
                        "type": "media",
                        "kind": "image",
                        "uri": uri,
                        "mime": str(artifact_ref.media_type),
                    }
                )
            helper_lines.append(
                "[Attachment "
                f"{index}: name={json.dumps(filename, ensure_ascii=True)}; "
                f"uri={uri}; size_bytes={artifact_ref.size_bytes}; "
                "read with read_file after normal authorization]"
            )
        return "\n".join(helper_lines)

    def _load_run_summary(self, logging_dir: Optional[str]) -> Optional[Dict[str, Any]]:
        if not logging_dir:
            return None
        try:
            run_path = Path(logging_dir) / "meta" / "run_summary.json"
            if not run_path.exists():
                return None
            return json.loads(run_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _normalize_usage_payload(self, usage: Dict[str, Any], *, latency_ms: Optional[int] = None) -> Dict[str, Any]:
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
        prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = _to_int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = _to_int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        cache_read = _to_int(usage.get("cache_read_tokens") or usage.get("cache_read") or 0)
        cache_write = _to_int(usage.get("cache_write_tokens") or usage.get("cache_write") or 0)
        cost_usd = _to_float(usage.get("cost_usd") or usage.get("cost") or usage.get("total_cost"))
        latency_ms_val = _to_int(usage.get("latency_ms") or 0)
        if not latency_ms_val:
            latency_s = _to_float(usage.get("latency_s") or usage.get("latency_seconds"))
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

    def _usage_from_run_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
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
                totals["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                totals["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                total_tokens = usage.get("total_tokens")
                if total_tokens is None:
                    total_tokens = (usage.get("prompt_tokens") or usage.get("input_tokens") or 0) + (
                        usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    )
                totals["total_tokens"] += int(total_tokens or 0)
                totals["cache_read_tokens"] += int(usage.get("cache_read_tokens") or usage.get("cache_read") or 0)
                totals["cache_write_tokens"] += int(usage.get("cache_write_tokens") or usage.get("cache_write") or 0)
                cost_value = usage.get("cost_usd") or usage.get("cost")
                if isinstance(cost_value, (int, float)):
                    cost_total += float(cost_value)
            latency_value = entry.get("latency_seconds") or entry.get("latency_s")
            if isinstance(latency_value, (int, float)):
                latency_total += float(latency_value)
        if not saw_usage:
            return {}
        totals["total_tokens"] = totals["total_tokens"] or (totals["prompt_tokens"] + totals["completion_tokens"])
        normalized = self._normalize_usage_payload(totals)
        if latency_total:
            normalized["latency_ms"] = int(latency_total * 1000)
        if cost_total:
            normalized["cost_usd"] = cost_total
        return normalized

    def _extract_usage_metrics(
        self,
        result: Dict[str, Any],
        logging_dir: Optional[str],
        *,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        for key in ("usage", "usage_summary", "usage_metrics"):
            usage = result.get(key)
            if isinstance(usage, dict):
                normalized = self._normalize_usage_payload(usage, latency_ms=elapsed_ms)
                if normalized:
                    return normalized
        summary = self._load_run_summary(logging_dir)
        if summary:
            normalized = self._usage_from_run_summary(summary)
            if normalized:
                if elapsed_ms and not normalized.get("latency_ms"):
                    normalized["latency_ms"] = int(elapsed_ms)
                return normalized
        if elapsed_ms:
            return {"latency_ms": int(elapsed_ms)}
        return {}

    def _normalize_tool_call_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        call = payload.get("call") or payload.get("tool_call") or payload.get("tool")
        if not isinstance(call, dict):
            return payload
        call_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
        function = call.get("function") if isinstance(call.get("function"), dict) else None
        tool_name = call.get("name") or (function or {}).get("name")
        arguments = call.get("arguments")
        if arguments is None and isinstance(function, dict):
            arguments = function.get("arguments")
        action = None
        if isinstance(arguments, dict):
            action = arguments.get("action") or arguments.get("command") or arguments.get("operation")
        diff_preview = call.get("diff_preview") if isinstance(call, dict) else None
        progress = call.get("progress") if isinstance(call, dict) else None
        normalized = dict(payload)
        normalized.update(
            {
                "call": call,
                "call_id": call_id,
                "tool": tool_name,
                "action": action,
            }
        )
        if diff_preview is not None and "diff_preview" not in normalized:
            normalized["diff_preview"] = diff_preview
        if progress is not None and "progress" not in normalized:
            normalized["progress"] = progress
        return normalized

    def _normalize_tool_result_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload)
        message = normalized.get("message")
        if isinstance(message, dict):
            call_id = (
                normalized.get("call_id")
                or message.get("tool_call_id")
                or message.get("tool_call_id")
                or message.get("call_id")
            )
            content = message.get("content")
            normalized.setdefault("call_id", call_id)
            normalized.setdefault("result", content)
            normalized.setdefault("status", message.get("status") or ("error" if message.get("error") else "ok"))
            normalized.setdefault("error", bool(message.get("error")))
            if not normalized.get("tool"):
                tool = message.get("name") or message.get("tool")
                if isinstance(tool, str) and tool:
                    normalized["tool"] = tool
        if "result" not in normalized and "content" in normalized:
            normalized["result"] = normalized.get("content")
        artifact_ref = self._extract_artifact_ref(normalized)
        if artifact_ref is not None:
            normalized["artifact_ref"] = artifact_ref
        return normalized

    def _extract_artifact_ref(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidate = payload.get("artifact_ref")
        if isinstance(candidate, dict):
            normalized = self._normalize_artifact_ref(candidate)
            if normalized:
                return normalized
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            normalized = self._normalize_artifact_ref(artifact)
            if normalized:
                return normalized
        display = payload.get("display")
        if isinstance(display, dict):
            detail_artifact = display.get("detail_artifact")
            if isinstance(detail_artifact, dict):
                normalized = self._normalize_artifact_ref(detail_artifact)
                if normalized:
                    return normalized
        return None

    def _normalize_artifact_ref(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = payload.get("path")
        sha256 = payload.get("sha256")
        schema_version = payload.get("schema_version") or "artifact_ref_v1"
        if not isinstance(path, str) or not path.strip():
            return None
        if not isinstance(sha256, str) or not sha256.strip():
            return None
        size_bytes = payload.get("size_bytes")
        size_int = int(size_bytes) if isinstance(size_bytes, (int, float)) else None
        if size_int is None or size_int < 0:
            return None
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            kind = "tool_result"
        mime = payload.get("mime")
        if not isinstance(mime, str) or not mime.strip():
            mime = "text/plain"
        storage = payload.get("storage")
        if not isinstance(storage, str) or not storage.strip():
            storage = "workspace_file"
        normalized: Dict[str, Any] = {
            "schema_version": str(schema_version),
            "id": str(payload.get("id") or f"artifact:{sha256[:16]}"),
            "kind": str(kind),
            "mime": str(mime),
            "size_bytes": int(size_int),
            "sha256": str(sha256),
            "storage": str(storage),
            "path": str(path).strip(),
        }
        preview = payload.get("preview")
        if isinstance(preview, dict):
            normalized["preview"] = preview
        return normalized

    def _normalize_permission_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload or {})
        request_id = normalized.get("request_id") or normalized.get("id")
        items = normalized.get("items")
        first_item = items[0] if isinstance(items, list) and items else {}
        category = normalized.get("category") or first_item.get("category")
        pattern = normalized.get("pattern") or first_item.get("pattern")
        metadata = normalized.get("metadata") or first_item.get("metadata") or {}
        tool = metadata.get("function") or category
        summary = metadata.get("summary") or metadata.get("command") or metadata.get("path") or pattern or category
        kind = metadata.get("kind") or (str(category).title() if category else "Permission")
        normalized.setdefault("request_id", request_id)
        normalized.setdefault("tool", tool)
        normalized.setdefault("kind", kind)
        normalized.setdefault("summary", summary)
        if "diff" in metadata and "diff" not in normalized:
            normalized["diff"] = metadata.get("diff")
        if "rule_suggestion" in metadata and "rule_suggestion" not in normalized:
            normalized["rule_suggestion"] = metadata.get("rule_suggestion")
        if "approval_pattern" in metadata and "rule_suggestion" not in normalized:
            normalized["rule_suggestion"] = metadata.get("approval_pattern")
        if "default_scope" not in normalized:
            normalized["default_scope"] = metadata.get("default_scope") or "project"
        if "rewindable" not in normalized:
            normalized["rewindable"] = bool(metadata.get("rewindable")) if isinstance(metadata, dict) else False
        return normalized

    def _normalize_permission_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload or {})
        request_id = normalized.get("request_id") or normalized.get("id")
        decision = normalized.get("decision") or normalized.get("response")
        responses = normalized.get("responses")
        if decision is None and isinstance(responses, dict):
            if "default" in responses:
                decision = responses.get("default")
            elif "items" in responses and isinstance(responses.get("items"), dict):
                items = responses.get("items") or {}
                if items:
                    unique = {str(v) for v in items.values() if v is not None}
                    if len(unique) == 1:
                        decision = next(iter(unique))
        normalized.setdefault("request_id", request_id)
        if decision is not None:
            normalized.setdefault("decision", decision)
        return normalized

    def _normalize_task_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return normalize_task_event_payload(
            payload,
            parent_session_id=getattr(self.session, "session_id", None),
        )

    def _translate_runtime_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        turn: Optional[int],
    ) -> Optional[TranslatedRuntimeEvent]:
        mapping = {
            "turn_start": EventType.TURN_START,
            "stream.gap": EventType.STREAM_GAP,
            "conversation.compaction.start": EventType.CONVERSATION_COMPACTION_START,
            "conversation.compaction.end": EventType.CONVERSATION_COMPACTION_END,
            "assistant.message.start": EventType.ASSISTANT_MESSAGE_START,
            "assistant.message.delta": EventType.ASSISTANT_MESSAGE_DELTA,
            "assistant.message.end": EventType.ASSISTANT_MESSAGE_END,
            "assistant.reasoning.delta": EventType.ASSISTANT_REASONING_DELTA,
            "assistant.thought_summary.delta": EventType.ASSISTANT_THOUGHT_SUMMARY_DELTA,
            "assistant.tool_call.start": EventType.ASSISTANT_TOOL_CALL_START,
            "assistant.tool_call.delta": EventType.ASSISTANT_TOOL_CALL_DELTA,
            "assistant.tool_call.end": EventType.ASSISTANT_TOOL_CALL_END,
            "tool.exec.start": EventType.TOOL_EXEC_START,
            "tool.exec.stdout.delta": EventType.TOOL_EXEC_STDOUT_DELTA,
            "tool.exec.stderr.delta": EventType.TOOL_EXEC_STDERR_DELTA,
            "tool.exec.end": EventType.TOOL_EXEC_END,
            "assistant_message": EventType.ASSISTANT_MESSAGE,
            "assistant_delta": EventType.ASSISTANT_DELTA,
            "user_message": EventType.USER_MESSAGE,
            "tool_call": EventType.TOOL_CALL,
            "tool.result": EventType.TOOL_RESULT_DOT,
            "tool_result": EventType.TOOL_RESULT,
            "todo_event": EventType.TOOL_RESULT,
            "permission_request": EventType.PERMISSION_REQUEST,
            "permission_response": EventType.PERMISSION_RESPONSE,
            "checkpoint_list": EventType.CHECKPOINT_LIST,
            "checkpoint_restored": EventType.CHECKPOINT_RESTORED,
            "skills_catalog": EventType.SKILLS_CATALOG,
            "skills_selection": EventType.SKILLS_SELECTION,
            "ctree_node": EventType.CTREE_NODE,
            "ctree_snapshot": EventType.CTREE_SNAPSHOT,
            "task_event": EventType.TASK_EVENT,
            "warning": EventType.WARNING,
            "reward_update": EventType.REWARD_UPDATE,
            "limits_update": EventType.LIMITS_UPDATE,
            "completion": EventType.COMPLETION,
            "log_link": EventType.LOG_LINK,
            "error": EventType.ERROR,
            "run_finished": EventType.RUN_FINISHED,
        }
        evt = mapping.get(event_type)
        if not evt:
            raise RuntimeProtocolError("runtime_protocol_error")

        normalized_payload: Dict[str, Any] = dict(payload or {})
        event_contract = _default_runtime_event_contract(event_type)
        if event_type == "todo_event":
            try:
                todo_update = normalized_payload.get("todo")
                if isinstance(todo_update, dict):
                    self.session.metadata["todo_last_update"] = dict(todo_update)
                    self._persist_metadata_snapshot_threadsafe()
            except Exception:
                pass
        if evt is EventType.TURN_START:
            normalized_payload = {}
        elif evt is EventType.ASSISTANT_MESSAGE:
            message = _strip_completion_sentinels(
                normalized_payload.get("message")
            )
            candidate_text = normalized_payload.get("text")
            if not isinstance(candidate_text, str) and isinstance(message, dict):
                candidate_text = message.get("content")
            text = _assistant_visible_text(candidate_text)
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.ASSISTANT_DELTA:
            candidate_text = normalized_payload.get(
                "text", normalized_payload.get("delta")
            )
            text = _assistant_visible_text(candidate_text)
            message_id = (
                normalized_payload.get("message_id")
                or normalized_payload.get("messageId")
                or normalized_payload.get("id")
            )
            normalized_payload = {"text": text, "message_id": message_id}
        elif evt in {
            EventType.ASSISTANT_MESSAGE_DELTA,
            EventType.ASSISTANT_MESSAGE_END,
        }:
            normalized_payload = dict(normalized_payload)
            for field in ("text", "delta", "content", "message"):
                if field in normalized_payload:
                    normalized_payload[field] = _strip_completion_sentinels(
                        normalized_payload[field]
                    )
        elif evt is EventType.USER_MESSAGE:
            message = normalized_payload.get("message")
            text = normalized_payload.get("text")
            if not isinstance(text, str):
                text = ""
            if not text and isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(
                        str(block.get("text") or "")
                        for block in content
                        if isinstance(block, dict)
                        and block.get("type") == "text"
                    )
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.TOOL_CALL:
            normalized_payload = self._normalize_tool_call_payload(normalized_payload)
        elif evt in {EventType.TOOL_RESULT, EventType.TOOL_RESULT_DOT}:
            normalized_payload = self._normalize_tool_result_payload(normalized_payload)
        elif evt is EventType.PERMISSION_REQUEST:
            normalized_payload = self._normalize_permission_request(normalized_payload)
        elif evt is EventType.PERMISSION_RESPONSE:
            normalized_payload = self._normalize_permission_response(normalized_payload)
        elif evt is EventType.ERROR:
            nested_error = normalized_payload.get("error")
            nested_code = (
                nested_error.get("code") if isinstance(nested_error, dict) else None
            )
            normalized_payload = {
                "code": _safe_runtime_error_code(
                    nested_code or normalized_payload.get("code")
                )
            }
        elif evt is EventType.TASK_EVENT:
            normalized_payload = self._normalize_task_event(normalized_payload)
        if _runtime_event_is_session_scoped(event_type):
            turn = None
        return evt, normalized_payload, turn, event_contract

    def _resolve_skill_catalog(self) -> Dict[str, Any]:
        config = self.current_runtime_config()
        workspace = self.get_workspace_dir()
        if not workspace:
            ws_cfg = (config.get("workspace") or {}) if isinstance(config, dict) else {}
            workspace = Path(str(ws_cfg.get("root") or self.request.workspace or ".")).resolve()
        plugin_manifests = discover_plugin_manifests(config, str(workspace))
        plugin_skill_paths: List[Path] = []
        for manifest in plugin_manifests:
            for rel in getattr(manifest, "skills_paths", []) or []:
                try:
                    base = getattr(manifest, "root", None) or str(workspace)
                    plugin_skill_paths.append(Path(str(base)) / rel)
                except Exception:
                    continue
        prompt_skills, graph_skills = load_skills(
            config,
            str(workspace),
            plugin_skill_paths=plugin_skill_paths,
        )
        selection = normalize_skill_selection(config, self.session.metadata.get("skills_selection"))
        _, _, enabled_map = apply_skill_selection(prompt_skills, graph_skills, selection)
        catalog = build_skill_catalog(prompt_skills, graph_skills, selection=selection, enabled_map=enabled_map)
        snapshot = None
        try:
            snapshot = plugin_snapshot(plugin_manifests)
        except Exception:
            snapshot = None
        return {
            "catalog": catalog,
            "selection": selection,
            "sources": {
                "config_path": self.session.metadata.get(
                    "config_path", self.request.config_path
                ),
                "workspace": str(workspace),
                "plugin_count": len(plugin_manifests),
                "plugin_snapshot": snapshot,
                "skill_paths": [str(p) for p in plugin_skill_paths],
            },
        }

    def get_skill_catalog(self) -> Dict[str, Any]:
        try:
            self._skills_catalog_cache = self._resolve_skill_catalog()
        except Exception:
            if self._skills_catalog_cache is None:
                self._skills_catalog_cache = {"catalog": {"skills": []}, "selection": {}, "sources": {}}
        return dict(self._skills_catalog_cache or {})

    def get_ctree_snapshot(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(self._ctree_snapshot_cache, dict):
            payload.update(self._ctree_snapshot_cache)
        if self._ctree_last_node:
            payload.setdefault("last_node", dict(self._ctree_last_node))
        return payload
