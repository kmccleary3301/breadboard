"""OpenClaw 2026.9.4 semantics owned by BreadBoard's model-loop boundary.

The implementation consumes a finalized native Chat response. It never dispatches
from a partial JSON prefix, never retries/falls back/compacts, and returns the
history mutations and tool batch to the conductor for authorization.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from breadboard.rl.harness.openclaw_native_tools import (
    BOOTSTRAP_ORDER,
    MAX_LIVE_PROCESSES,
    OPENCLAW_SOURCE_COMMIT,
    OPENCLAW_VERSION,
    RecoveryDecision,
    stop_before_recovery,
)

OPENCLAW_CONSUMER_ID = "breadboard.openclaw.native-chat.v1"
MAX_REQUESTS = 8
EPISODE_WALL_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 45
OUTPUT_TOKENS = 2048
TOOL_ADMISSIONS = 32

SOURCE_CITATIONS = {
    "response_finalization": "packages/agent-core/src/agent-stream-response.ts:finalizeAssistantMessage",
    "terminal_finish": "src/agents/sessions/agent-session-base.ts:direct Chat terminal finalization",
    "tool_prepare": "src/agents/agent-tools.execution-preparer.ts:prepareToolCallArguments",
    "recovery": "src/agents/sessions/agent-session-execution.ts:prepareRetry",
    "composition": "src/agents/core-coding-tools.ts:createCoreCodingTools",
}


class OpenClawSemanticsError(Exception):
    """A native format/admission error that must not execute a tool."""


class RequestLimitExceeded(OpenClawSemanticsError):
    pass


class ToolAdmissionExceeded(OpenClawSemanticsError):
    pass
REQUIRED_TOOL_FIELDS = {
    "read": ("path",),
    "write": ("path", "content"),
    "edit": ("path", "edits"),
    "exec": ("command",),
    "process": ("action",),
}
PROCESS_ACTIONS = {"list", "poll", "log", "write", "send-keys", "submit", "paste", "kill", "clear", "remove"}


def _validate_prepared_tool(name: str, arguments: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_TOOL_FIELDS.get(name, ()) if field not in arguments]
    if missing:
        raise OpenClawSemanticsError(f"missing required {name} parameters: {', '.join(missing)}")
    if name == "edit" and (not isinstance(arguments.get("edits"), list) or not arguments["edits"]):
        raise OpenClawSemanticsError("edit edits must be a non-empty array")
    if name == "process" and arguments.get("action") not in PROCESS_ACTIONS:
        raise OpenClawSemanticsError("invalid process action")


@dataclass(frozen=True)
class NativeStreamFragment:
    """One lossless native stream fragment."""

    kind: str
    index: int = 0
    text: str = ""
    call_id: str | None = None
    name: str | None = None
    tool_index: int | None = None
    fragment_id: str | None = None
    fragment_type: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "NativeStreamFragment":
        if isinstance(value, NativeStreamFragment):
            return value
        if not isinstance(value, Mapping):
            value = {
                key: getattr(value, key, None)
                for key in ("kind", "index", "text", "call_id", "name", "tool_index")
            }
        if not isinstance(value, Mapping):
            raise OpenClawSemanticsError("stream fragment must be an object")
        raw_kind = str(value.get("kind", value.get("type", "")))
        kind = (
            "tool_arguments"
            if raw_kind in {
                "toolcall_start",
                "tool_call_start",
                "toolcall_delta",
                "tool_call_delta",
                "toolcall_end",
                "tool_call_end",
            }
            else raw_kind
        )
        raw_id = value.get("id")
        call_id = value.get("call_id", value.get("callId"))
        if call_id is None and raw_id is not None and kind == "tool_arguments":
            call_id = raw_id
        raw_text = value.get("text", value.get("delta", value.get("content", "")))
        if raw_text is None and value.get("arguments") is not None:
            raw_text = value["arguments"]
        return cls(
            kind=kind,
            index=int(value.get("index", value.get("contentIndex", 0)) or 0),
            text=str(raw_text or ""),
            call_id=str(call_id) if call_id is not None else None,
            name=str(value["name"]) if value.get("name") is not None else None,
            tool_index=(
                int(value["tool_index"]) if value.get("tool_index") is not None else None
            ),
            fragment_id=str(raw_id) if raw_id is not None else None,
            fragment_type=str(value["type"]) if value.get("type") is not None else None,
        )


@dataclass(frozen=True)
class FinalizedToolCall:
    tool_call_id: str
    name: str
    arguments: Mapping[str, Any]
    raw_arguments: Any = None
    call_type: str | None = None
    index: int | None = None

    @property
    def id(self) -> str:
        return self.tool_call_id
    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.tool_call_id,
            "name": self.name,
            "arguments": dict(self.arguments),
            "raw_arguments": self.raw_arguments,
        }
        if self.call_type is not None:
            result["type"] = self.call_type
        if self.index is not None:
            result["index"] = self.index
        return result


@dataclass(frozen=True)
class OpenClawToolBatch:
    calls: tuple[FinalizedToolCall, ...]
    dispatchable: bool
    errors: tuple[str, ...] = ()

    @property
    def tool_calls(self) -> tuple[FinalizedToolCall, ...]:
        return self.calls if self.dispatchable else ()


@dataclass(frozen=True)
class OpenClawParseResult:
    assistant_message: Mapping[str, Any]
    tool_batch: OpenClawToolBatch
    history_mutations: tuple[Mapping[str, Any], ...]
    finish_reason: str | None
    native_stop_reason: str | None
    usage: Mapping[str, Any] | None = None
    raw_fragments: tuple[Mapping[str, Any], ...] = ()
    recovery: RecoveryDecision | None = None
    @property
    def assistant(self) -> Mapping[str, Any]:
        return self.assistant_message

    @property
    def calls(self) -> tuple[FinalizedToolCall, ...]:
        return self.tool_batch.tool_calls


@dataclass(frozen=True)
class NativeProviderResponseView:
    """Duck-typed PR129 response view used by the generic stream seam."""

    content: str = ""
    finish_reason: str | None = None
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    stream_fragments: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, Any] | None = None
    response_id: str | None = None
    native_stop_reason: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "NativeProviderResponseView":
        if isinstance(value, cls):
            return value
        def get(key: str, default: Any = None) -> Any:
            if isinstance(value, Mapping):
                return value.get(key, default)
            return getattr(value, key, default)
        calls = get("tool_calls", ()) or ()
        fragments = get("stream_fragments", ()) or ()
        return cls(
            content=str(get("content", "") or ""),
            finish_reason=get("finish_reason"),
            tool_calls=tuple(calls),
            stream_fragments=tuple(fragments),
            usage=get("usage"),
            response_id=get("response_id"),
            native_stop_reason=get("native_stop_reason", get("stop_reason")),
        )


def _decode_tool_arguments(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        raise OpenClawSemanticsError("tool arguments must be a JSON object")
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise OpenClawSemanticsError(f"invalid tool arguments: {exc}") from exc
    if not isinstance(decoded, Mapping):
        raise OpenClawSemanticsError("tool arguments must decode to an object")
    return dict(decoded)


def _call_fields(call: Any, index: int) -> tuple[str, str, Any, str | None, int | None]:
    if not isinstance(call, Mapping):
        call = {
            key: getattr(call, key, None)
            for key in ("id", "name", "arguments", "type", "index")
        }
    if not isinstance(call, Mapping):
        raise OpenClawSemanticsError(f"tool call {index} must be an object")
    function = call.get("function") if isinstance(call.get("function"), Mapping) else call
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise OpenClawSemanticsError(f"tool call {index} has no name")
    raw = function.get("arguments", call.get("arguments", {}))
    call_id = call.get("id", call.get("tool_call_id", f"call_{index}"))
    call_type = call.get("type")
    call_index = call.get("index")
    return str(call_id), name, raw, str(call_type) if call_type is not None else None, call_index


def finalize_native_chat_response(
    response: Any, *, allow_silent_tool_promotion: bool = False
) -> OpenClawParseResult:
    """Finalize lossless fragments before the worker receives raw arguments."""
    view = NativeProviderResponseView.from_value(response)
    fragments = tuple(NativeStreamFragment.from_value(item) for item in view.stream_fragments)
    text_parts: list[str] = []
    assembled: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for fragment in fragments:
        if fragment.kind in {"content", "text_delta", "text", "text_end"}:
            text_parts.append(fragment.text)
            continue
        if fragment.kind != "tool_arguments":
            continue
        key = (
            f"tool_index:{fragment.tool_index}"
            if fragment.tool_index is not None
            else (f"call_id:{fragment.call_id}" if fragment.call_id else f"index:{fragment.index}")
        )
        slot = assembled.setdefault(
            key,
            {
                "id": fragment.call_id or fragment.fragment_id or f"call_{fragment.index}",
                "type": fragment.fragment_type,
                "index": fragment.tool_index if fragment.tool_index is not None else fragment.index,
                "name": fragment.name or "",
                "arguments": "",
            },
        )
        if key not in order:
            order.append(key)
        if fragment.call_id:
            slot["id"] = fragment.call_id
        if fragment.fragment_id:
            slot["id"] = fragment.fragment_id
        if fragment.fragment_type:
            slot["type"] = fragment.fragment_type
        if fragment.name:
            slot["name"] = fragment.name
        slot["arguments"] += fragment.text

    raw_calls: list[Any] = list(view.tool_calls)
    if not raw_calls and order:
        raw_calls = [assembled[key] for key in order]
    content = "".join(text_parts) if text_parts and not view.content else view.content
    finish_reason = str(view.finish_reason) if view.finish_reason is not None else None
    errors: list[str] = []
    calls: list[FinalizedToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        try:
            call_id, name, raw_arguments, call_type, call_index = _call_fields(raw_call, index)
            args = _decode_tool_arguments(raw_arguments)
            _validate_prepared_tool(name, args)
            calls.append(
                FinalizedToolCall(
                    call_id,
                    name,
                    dict(args),
                    raw_arguments,
                    call_type,
                    int(call_index) if call_index is not None else None,
                )
            )
        except (KeyError, TypeError, ValueError, OpenClawSemanticsError) as exc:
            errors.append(str(exc))
    if calls and finish_reason not in {"tool_calls", "stop"}:
        errors.append("tool calls require a terminal finish_reason")
    executable = bool(calls) and not errors and finish_reason == "tool_calls"
    if calls and not errors and finish_reason == "stop" and allow_silent_tool_promotion:
        executable = True
    if errors or finish_reason in {"error", "aborted"}:
        executable = False
    assistant_content: Any = content
    if calls:
        blocks: list[dict[str, Any]] = []
        if content:
            blocks.append({"type": "text", "text": content})
        blocks.extend(
            {
                "type": "toolCall",
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments),
            }
            for call in calls
        )
        assistant_content = blocks
    assistant = {
        "role": "assistant",
        "content": assistant_content,
        "tool_calls": [call.to_dict() for call in calls],
        "finish_reason": finish_reason,
        "stop_reason": view.native_stop_reason or finish_reason,
        **({"response_id": view.response_id} if view.response_id else {}),
        **({"usage": dict(view.usage)} if isinstance(view.usage, Mapping) else {}),
    }
    batch = OpenClawToolBatch(tuple(calls), executable, tuple(errors))
    mutations: list[Mapping[str, Any]] = [assistant]
    recovery = None
    if finish_reason in {"error", "aborted"} or errors:
        trigger = "provider-error" if finish_reason in {"error", "aborted"} else "malformed-tool-call"
        recovery = stop_before_recovery(trigger, mutations, retry=False, fallback=False, compaction=False)
    return OpenClawParseResult(
        assistant_message=assistant,
        tool_batch=batch,
        history_mutations=tuple(mutations),
        finish_reason=finish_reason,
        native_stop_reason=view.native_stop_reason or finish_reason,
        usage=dict(view.usage) if isinstance(view.usage, Mapping) else None,
        raw_fragments=tuple(
            {
                "kind": f.kind,
                "index": f.index,
                "text": f.text,
                "call_id": f.call_id,
                "name": f.name,
                "tool_index": f.tool_index,
                **({"id": f.fragment_id} if f.fragment_id else {}),
                **({"type": f.fragment_type} if f.fragment_type else {}),
            }
            for f in fragments
        ),
        recovery=recovery,
    )


class OpenClawSemanticsState:
    """State machine for one disposable, serial OpenClaw episode."""

    consumer_id = OPENCLAW_CONSUMER_ID
    source_commit = OPENCLAW_SOURCE_COMMIT
    source_version = OPENCLAW_VERSION
    tool_order = ("ls", "read", "edit", "write", "exec", "process")
    bootstrap_order = BOOTSTRAP_ORDER

    def __init__(
        self,
        task: str = "",
        system_prompt: str = "",
        bootstrap: Mapping[str, Any] | None = None,
        *,
        max_requests: int = MAX_REQUESTS,
        episode_deadline_seconds: float = EPISODE_WALL_SECONDS,
        max_tool_admissions: int = TOOL_ADMISSIONS,
        started_at: float | None = None,
    ) -> None:
        self.task = task
        self.system_prompt = system_prompt
        self.bootstrap = dict(bootstrap or {})
        self.max_requests = max_requests
        self.episode_deadline_seconds = episode_deadline_seconds
        self.max_tool_admissions = max_tool_admissions
        self.started_at = time.monotonic() if started_at is None else started_at
        self.request_count = 0
        self.refused_attempts = 0
        self.tool_admissions = 0
        self.history: list[dict[str, Any]] = []
        if system_prompt:
            self.history.append({"role": "system", "content": system_prompt})
        if task:
            self.history.append({"role": "user", "content": task})
        self.raw_responses: list[Mapping[str, Any]] = []
        self.terminal_kind: str | None = None
        self.native_stop_reason: str | None = None
        self.stop_reason: str | None = None
        self.recovery: RecoveryDecision | None = None
        self._stream_fn_issued = False
        self._terminal_message: dict[str, Any] | None = None

    @property
    def messages(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.history)

    @property
    def is_exited(self) -> bool:
        return self.terminal_kind is not None

    @property
    def exit_status(self) -> str | None:
        return self.terminal_kind

    @property
    def stream_fn_issued(self) -> bool:
        return self._stream_fn_issued

    def begin_query(self) -> Mapping[str, Any] | None:
        """Admit one provider stream, or return the local cap terminal."""
        if self.request_count >= self.max_requests:
            self.refused_attempts += 1
            self.terminal_kind = "request_budget"
            self.native_stop_reason = "429"
            self.stop_reason = "error"
            self._terminal_message = {
                "role": "assistant",
                "content": "bbe4 capture request cap",
                "isError": True,
                "status": 429,
                "error": "bbe4 capture request cap",
                "finish_reason": "error",
                "stop_reason": "429",
            }
            if self._terminal_message not in self.history:
                self.history.append(dict(self._terminal_message))
            return dict(self._terminal_message)
        if time.monotonic() - self.started_at >= self.episode_deadline_seconds:
            self.terminal_kind = "episode_timeout"
            self.native_stop_reason = "timeout"
            self.stop_reason = "aborted"
            self._terminal_message = {
                "role": "assistant",
                "content": "OpenClaw episode deadline elapsed",
                "isError": True,
                "finish_reason": "aborted",
                "stop_reason": "timeout",
            }
            self.history.append(dict(self._terminal_message))
            return dict(self._terminal_message)
        self.request_count += 1
        self._stream_fn_issued = True
        return None

    def begin_request(self) -> int:
        """Compatibility helper for callers predating the native seam."""
        terminal = self.begin_query()
        if terminal is not None:
            raise RequestLimitExceeded(str(terminal.get("error", terminal.get("content"))))
        return self.request_count

    def consume_native_response(self, response: Any) -> OpenClawParseResult:
        """Finalize one native response; no tool effect occurs here."""
        result = finalize_native_chat_response(response)
        self.raw_responses.append(_as_mapping(response))
        self.history.extend(dict(message) for message in result.history_mutations)
        self.native_stop_reason = result.native_stop_reason
        self.stop_reason = result.finish_reason
        if result.recovery:
            self.recovery = result.recovery
            self.terminal_kind = "recovery_stop"
        if result.tool_batch.dispatchable:
            if self.tool_admissions + len(result.tool_batch.tool_calls) > self.max_tool_admissions:
                self.terminal_kind = "tool_budget"
                self.recovery = stop_before_recovery("tool-admission-budget", result.history_mutations)
                raise ToolAdmissionExceeded(
                    f"OpenClaw tool admission cap exceeded ({self.max_tool_admissions})"
                )
            self.tool_admissions += len(result.tool_batch.tool_calls)
        elif result.finish_reason in {"stop", "error", "aborted"} and not result.recovery:
            self.terminal_kind = "stop" if result.finish_reason == "stop" else "provider_error"
        return result

    def prepare_response(self, response: Any) -> OpenClawParseResult:
        return self.consume_native_response(response)

    def commit_tool_results(
        self,
        calls: Sequence[FinalizedToolCall] | Sequence[Mapping[str, Any]],
        results: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Commit settled effects in source call order after history append."""
        if results is None:
            committed = tuple(dict(result) for result in calls)
        else:
            by_id = {
                str(result.get("tool_call_id", result.get("id", ""))): result
                for result in results
                if isinstance(result, Mapping)
            }
            committed_items: list[Mapping[str, Any]] = []
            for call in calls:
                call_id = (
                    call.tool_call_id
                    if isinstance(call, FinalizedToolCall)
                    else str(call.get("id", call.get("tool_call_id", "")))
                )
                raw = dict(by_id.get(call_id, {}))
                if raw.get("role") != "tool":
                    content = raw.get("content", raw.get("text", ""))
                    raw = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": content if isinstance(content, (str, list)) else str(content),
                    }
                committed_items.append(raw)
            committed = tuple(committed_items)
        self.history.extend(committed)
        return committed

    def finish(self, kind: str = "stop") -> dict[str, Any]:
        self.terminal_kind = self.terminal_kind or kind
        return self.to_trace()

    def to_trace(self) -> dict[str, Any]:
        return {
            "kind": self.terminal_kind,
            "native_stop_reason": self.native_stop_reason,
            "request_count": self.request_count,
            "refused_attempts": self.refused_attempts,
            "tool_admissions": self.tool_admissions,
            "stream_fn_issued": self.stream_fn_issued,
            "history": [dict(message) for message in self.history],
        }

    def prepare_request_history(self) -> list[dict[str, Any]]:
        return [
            {
                key: value
                for key, value in message.items()
                if key not in {"extra", "raw_fragments"}
            }
            for message in self.history
        ]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    fields = {}
    for key in ("content", "finish_reason", "tool_calls", "stream_fragments", "usage", "response_id", "native_stop_reason"):
        if hasattr(value, key):
            fields[key] = getattr(value, key)
    return fields


# The parent conductor can use this shape without importing a profile-specific
# provider implementation.  PiComplete and OMP should register the same seam.
def consume_native_provider_response(state: OpenClawSemanticsState, response: Any) -> tuple[OpenClawToolBatch, tuple[Mapping[str, Any], ...]]:
    result = state.consume_native_response(response)
    return result.tool_batch, result.history_mutations
