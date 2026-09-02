"""Runtime event validation, normalization, and bridge projection."""

from __future__ import annotations

import json

from typing import Any, Callable, Dict, Optional, Tuple

from breadboard_engine.provider.contracts import strip_public_completion_sentinel_lines
from .registry import identity_digest
from breadboard_engine.state.session_state import (
    AUDIT_ONLY_RUNTIME_EVENT_TYPES,
    CANONICAL_KERNEL_EVENT_TYPES,
    PROJECTION_ONLY_RUNTIME_EVENT_TYPES,
)

from .event_normalization import normalize_task_event_payload
from .events import EventType

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
MAX_PAIRED_PRODUCT_TOOL_COMPLETIONS = 128

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
    EventType.ASSISTANT_MESSAGE_START: frozenset({"message_id", "item_id", "index"}),
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
    EventType.TOOL_EXEC_STDOUT_DELTA: frozenset({"call_id", "exec_id", "delta"}),
    EventType.TOOL_EXEC_STDERR_DELTA: frozenset({"call_id", "exec_id", "delta"}),
    EventType.TOOL_EXEC_END: frozenset({"call_id", "exec_id", "exit_code"}),
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
        if "status" not in normalized or not isinstance(normalized.get("error"), bool):
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
TranslatedRuntimeEvent = Tuple[
    EventType, Dict[str, Any], Optional[int], RuntimeEventContract
]


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
    if event_name in {
        "assistant_delta",
        "assistant.message.start",
        "assistant.message.delta",
        "assistant.message.end",
    }:
        return {
            "classification": "bridge_stream",
            "family": "message.assistant.stream",
            "actor": "engine",
            "visibility": "transcript",
        }
    if event_name == "user_message":
        return {
            "classification": "kernel",
            "family": "message.user",
            "actor": "human",
            "visibility": "transcript",
        }
    if event_name in {"assistant.reasoning.delta", "assistant.thought_summary.delta"}:
        return {
            "classification": "bridge_stream",
            "family": "reasoning.delta",
            "actor": "engine",
            "visibility": "diagnostic",
        }
    if event_name.startswith("assistant.tool_call."):
        return {
            "classification": "bridge_stream",
            "family": "tool.call.delta",
            "actor": "engine",
            "visibility": "tool",
        }
    if event_name.startswith("tool.") or event_name in {
        "tool_call",
        "tool_result",
        "tool.result",
        "todo_event",
    }:
        return {
            "classification": "kernel",
            "family": "tool.event",
            "actor": "tool",
            "visibility": "tool",
        }
    if event_name in {"ctree_node", "turn_start", "lifecycle_event", "guardrail_event"}:
        return {
            "classification": "kernel",
            "family": f"audit.{event_name}",
            "actor": "service",
            "visibility": "audit",
        }
    if event_name in BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES or event_name in {
        "permission_request",
        "permission_response",
        "task_event",
        "ctree_snapshot",
    }:
        return {
            "classification": "bridge_host",
            "family": f"host.{event_name}",
            "actor": "service",
            "visibility": "host",
        }
    return {
        "classification": "legacy_unclassified",
        "family": "legacy.unclassified",
        "actor": "engine",
        "visibility": "audit",
    }


class RuntimeEventProjector:
    """Owns runtime event contract validation and bridge-facing projection."""

    def __init__(
        self,
        session: Any,
        persist_metadata: Callable[[], None],
        *,
        observation_tool_name: Callable[[Dict[str, Any]], Optional[str]],
        product_session_lock: Any,
        product_tool_completions: Dict[str, int],
    ) -> None:
        self.session = session
        self._persist_metadata = persist_metadata
        self._observation_tool_name = observation_tool_name
        self._product_session_lock = product_session_lock
        self._product_tool_completions = product_tool_completions

    def _persist_metadata_snapshot_threadsafe(self) -> None:
        self._persist_metadata()

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
            if (
                product_session is None
                or product_session.read_model.status != "running"
            ):
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
                        self._product_tool_completions[fingerprint] = (
                            duplicate_count - 1
                        )
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

    def _normalize_tool_call_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        call = payload.get("call") or payload.get("tool_call") or payload.get("tool")
        if not isinstance(call, dict):
            return payload
        call_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
        function = (
            call.get("function") if isinstance(call.get("function"), dict) else None
        )
        tool_name = call.get("name") or (function or {}).get("name")
        arguments = call.get("arguments")
        if arguments is None and isinstance(function, dict):
            arguments = function.get("arguments")
        action = None
        if isinstance(arguments, dict):
            action = (
                arguments.get("action")
                or arguments.get("command")
                or arguments.get("operation")
            )
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
            normalized.setdefault(
                "status",
                message.get("status") or ("error" if message.get("error") else "ok"),
            )
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

    def _extract_artifact_ref(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
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

    def _normalize_artifact_ref(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
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
        summary = (
            metadata.get("summary")
            or metadata.get("command")
            or metadata.get("path")
            or pattern
            or category
        )
        kind = metadata.get("kind") or (
            str(category).title() if category else "Permission"
        )
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
            normalized["rewindable"] = (
                bool(metadata.get("rewindable"))
                if isinstance(metadata, dict)
                else False
            )
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

    def translate(
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
            message = _strip_completion_sentinels(normalized_payload.get("message"))
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
                        if isinstance(block, dict) and block.get("type") == "text"
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
