"""OpenClaw 2026.9.4 semantics owned by BreadBoard's model-loop boundary.

The implementation consumes a finalized native Chat response.  It never dispatches
from a partial JSON prefix, never retries/falls back/compacts, and returns the
history mutations and tool batch to the conductor for authorization.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableSequence, Sequence

from breadboard.rl.harness.openclaw_native_tools import (
    BOOTSTRAP_ORDER,
    EXEC_DEFAULT_TIMEOUT_SECONDS,
    MAX_LIVE_PROCESSES,
    OPENCLAW_SOURCE_COMMIT,
    OPENCLAW_VERSION,
    PreparedToolCall,
    RecoveryDecision,
    prepare_tool_calls,
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


@dataclass(frozen=True)
class NativeStreamFragment:
    kind: str
    index: int = 0
    text: str = ""
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None

    @classmethod
    def from_value(cls, value: Any) -> "NativeStreamFragment":
        if isinstance(value, NativeStreamFragment):
            return value
        if not isinstance(value, Mapping):
            raise OpenClawSemanticsError("stream fragment must be an object")
        return cls(
            kind=str(value.get("kind", value.get("type", ""))),
            index=int(value.get("index", value.get("contentIndex", 0)) or 0),
            text=str(value.get("text", value.get("delta", value.get("content", ""))) or ""),
            call_id=(str(value["call_id"]) if value.get("call_id") is not None else (str(value["callId"]) if value.get("callId") is not None else None)),
            name=(str(value["name"]) if value.get("name") is not None else None),
            arguments=(str(value["arguments"]) if value.get("arguments") is not None else None),
        )


@dataclass(frozen=True)
class FinalizedToolCall:
    tool_call_id: str
    name: str
    arguments: Mapping[str, Any]
    raw_arguments: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.tool_call_id,
            "name": self.name,
            "arguments": dict(self.arguments),
            "raw_arguments": self.raw_arguments,
        }


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


def _call_fields(call: Any, index: int) -> tuple[str, str, Any]:
    if not isinstance(call, Mapping):
        raise OpenClawSemanticsError(f"tool call {index} must be an object")
    function = call.get("function") if isinstance(call.get("function"), Mapping) else call
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise OpenClawSemanticsError(f"tool call {index} has no name")
    raw = function.get("arguments", call.get("arguments", {}))
    call_id = call.get("id", call.get("tool_call_id", f"call_{index}"))
    return str(call_id), name, raw


def finalize_native_chat_response(response: Any, *, allow_silent_tool_promotion: bool = False) -> OpenClawParseResult:
    """Finalize all fragments and validated tool calls before any dispatch."""
    view = NativeProviderResponseView.from_value(response)
    fragments = tuple(NativeStreamFragment.from_value(item) for item in view.stream_fragments)
    text_parts: list[str] = []
    assembled: dict[str, dict[str, Any]] = {}
    for fragment in fragments:
        if fragment.kind in {"text_delta", "text", "text_end"}:
            text_parts.append(fragment.text)
        elif fragment.kind in {"toolcall_start", "tool_call_start"}:
            key = fragment.call_id or f"call_{fragment.index}"
            assembled.setdefault(key, {"id": key, "name": fragment.name or "", "arguments": ""})
        elif fragment.kind in {"toolcall_delta", "tool_call_delta"}:
            key = fragment.call_id or f"call_{fragment.index}"
            slot = assembled.setdefault(key, {"id": key, "name": fragment.name or "", "arguments": ""})
            if fragment.name:
                slot["name"] = fragment.name
            slot["arguments"] = f"{slot.get('arguments', '')}{fragment.arguments if fragment.arguments is not None else fragment.text}"
        elif fragment.kind in {"toolcall_end", "tool_call_end"}:
            key = fragment.call_id or f"call_{fragment.index}"
            slot = assembled.setdefault(key, {"id": key, "name": fragment.name or "", "arguments": ""})
            if fragment.name:
                slot["name"] = fragment.name
            if fragment.arguments is not None:
                slot["arguments"] = fragment.arguments

    raw_calls: list[Any] = list(view.tool_calls)
    if not raw_calls and assembled:
        raw_calls = list(assembled.values())
    if text_parts and not view.content:
        content = "".join(text_parts)
    else:
        content = view.content
    finish_reason = str(view.finish_reason) if view.finish_reason is not None else None
    errors: list[str] = []
    calls: list[FinalizedToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        try:
            call_id, name, raw_arguments = _call_fields(raw_call, index)
            args = _decode_tool_arguments(raw_arguments)
            # prepare_tool_calls is the source preparation/legacy-edit seam.
            prepared = prepare_tool_calls(({"id": call_id, "name": name, "arguments": args},))[0]
            calls.append(FinalizedToolCall(call_id, name, dict(prepared.arguments), raw_arguments))
        except (KeyError, TypeError, ValueError, OpenClawSemanticsError) as exc:
            errors.append(str(exc))

    executable = bool(calls) and not errors and finish_reason == "tool_calls"
    # OpenClaw's explicit terminal stop can promote a fully confirmed silent
    # tool call; an interrupted stream cannot borrow this behavior.
    if calls and not errors and finish_reason == "stop" and allow_silent_tool_promotion:
        executable = True
    if errors:
        executable = False
    if finish_reason in {"error", "aborted"}:
        executable = False

    assistant = {
        "role": "assistant",
        "content": content,
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
        raw_fragments=tuple({"kind": f.kind, "index": f.index, "text": f.text, "call_id": f.call_id, "name": f.name, "arguments": f.arguments} for f in fragments),
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
        *,
        task: str = "",
        max_requests: int = MAX_REQUESTS,
        episode_deadline_seconds: float = EPISODE_WALL_SECONDS,
        max_tool_admissions: int = TOOL_ADMISSIONS,
        started_at: float | None = None,
    ) -> None:
        self.task = task
        self.max_requests = max_requests
        self.episode_deadline_seconds = episode_deadline_seconds
        self.max_tool_admissions = max_tool_admissions
        self.started_at = time.monotonic() if started_at is None else started_at
        self.request_count = 0
        self.tool_admissions = 0
        self.history: list[dict[str, Any]] = []
        self.raw_responses: list[Mapping[str, Any]] = []
        self.terminal_kind: str | None = None
        self.native_stop_reason: str | None = None
        self.stop_reason: str | None = None
        self.recovery: RecoveryDecision | None = None

    def begin_request(self) -> int:
        if self.request_count >= self.max_requests:
            self.terminal_kind = "request_budget"
            raise RequestLimitExceeded(f"OpenClaw request cap exceeded ({self.max_requests})")
        if time.monotonic() - self.started_at >= self.episode_deadline_seconds:
            self.terminal_kind = "episode_timeout"
            raise RequestLimitExceeded("OpenClaw episode deadline elapsed")
        self.request_count += 1
        return self.request_count

    def consume_native_response(self, response: Any) -> OpenClawParseResult:
        """Generic streaming seam: finalized response in, tool batch/history out."""
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
                raise ToolAdmissionExceeded(f"OpenClaw tool admission cap exceeded ({self.max_tool_admissions})")
            self.tool_admissions += len(result.tool_batch.tool_calls)
        return result

    def commit_tool_results(self, results: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Commit model-visible results in source call order, after effects settle."""
        committed = tuple(dict(result) for result in results)
        self.history.extend(committed)
        return committed

    def finish(self, kind: str = "stop") -> dict[str, Any]:
        self.terminal_kind = self.terminal_kind or kind
        return {
            "kind": self.terminal_kind,
            "native_stop_reason": self.native_stop_reason,
            "request_count": self.request_count,
            "tool_admissions": self.tool_admissions,
            "history": [dict(message) for message in self.history],
        }

    def prepare_request_history(self) -> list[dict[str, Any]]:
        return [{key: value for key, value in message.items() if key not in {"extra", "raw_fragments"}} for message in self.history]


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
