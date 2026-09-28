"""Source-derived Pi 0.57.1 semantics for the BreadBoard native seam.

Messages mirror the pinned AgentMessage shapes of @mariozechner/pi-ai and
@mariozechner/pi-agent-core 0.57.1 (paths relative to each package's dist):

* assistant: pi-ai ``providers/openai-completions.js:31-47`` builds
  ``{role, content, api, provider, model, usage, stopReason, timestamp}``;
  blocks are appended in stream order (``:120-209``); ``errorMessage`` is set
  only by the catch at ``:239-249`` (projected by the worker, never here).
* usage: ``:92-115`` with ``calculateCost`` (``models.js:22-29``).
* stop reason: ``mapStopReason`` ``:616-634``. ``content_filter`` maps to
  "error" and then throws a pinned message; that and every other reason
  outside the modeled set is a typed unmodeled failure here.
* user and toolResult: pi-agent-core ``agent.js:231-236`` and
  ``agent-loop.js:250-258`` (``details`` present only when the tool result
  defines it). The loop ends on error/aborted (``agent-loop.js:88-93``) or when
  an assistant message has no tool calls (``:95-96``).

BreadBoard's Conductor owns the loop. The ninth provider attempt is refused
before any HTTP (BB bounded control ``bounded_request_cap``; upstream has no
cap). Tool arguments arrive already parsed by the pinned worker's
``parseStreamingJson`` phase.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import time
from types import MappingProxyType
from typing import Any

from breadboard.rl.harness.runners.base import RunnerProtocolError
from breadboard_engine.provider.native_response import NativeProviderResponse


PROFILE_NAME = "pi"
PROFILE_VERSION = "0.57.1"
TRACE_SCHEMA_VERSION = "bb.e4.pi-replay-trace.v1"
TOOL_NAMES = ("read", "bash", "edit", "write", "grep", "find", "ls")
REQUEST_CAP = 8
# pinned mapStopReason (openai-completions.js:616-634); null -> "stop".
_STOP_REASONS = MappingProxyType({
    "stop": "stop",
    "length": "length",
    "function_call": "toolUse",
    "tool_calls": "toolUse",
})
_RESULT_KEYS = frozenset({"id", "completion_index", "content", "isError", "details", "terminate"})
_REQUIRED_RESULT_KEYS = frozenset({"id", "completion_index", "content", "isError"})


class Pi0571SemanticsError(RuntimeError):
    """A replay-state invariant was violated."""


class Pi0571UnmodeledFinishReason(RunnerProtocolError):
    """The provider finish_reason lies outside the pinned stop mapping
    (named divergence ``unmodeled_finish_reason``)."""

    def __init__(self) -> None:
        super().__init__(
            "provider finish_reason is outside the pinned stop mapping",
            code="native_unmodeled_finish_reason",
        )


@dataclass(frozen=True, slots=True)
class Pi0571ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass(frozen=True, slots=True)
class Pi0571ResponseResult:
    assistant: dict[str, Any]
    calls: tuple[Pi0571ToolCall, ...]
    stop_reason: str
    quiescent: bool


@dataclass(frozen=True, slots=True)
class Pi0571RequestRecord:
    attempt: int
    sent: bool
    request_digest: str | None


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _js_number(value: float) -> int | float:
    # JSON.stringify renders integral doubles without a fraction.
    return int(value) if float(value).is_integer() else value


def _js_or_zero(container: Any, key: str) -> Any:
    # Mirrors ``container?.[key] || 0`` for the pinned usage members.
    if not isinstance(container, Mapping) or key not in container:
        return 0
    value = container[key]
    return value if value else 0


def pinned_usage(usage: Mapping[str, Any] | None, cost: Mapping[str, Any]) -> dict[str, Any]:
    """openai-completions.js:92-115 plus models.js:22-29 calculateCost."""
    if not usage:
        return {
            "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        }
    prompt_details = usage["prompt_tokens_details"] if "prompt_tokens_details" in usage else None
    completion_details = (
        usage["completion_tokens_details"] if "completion_tokens_details" in usage else None
    )
    cached = _js_or_zero(prompt_details, "cached_tokens")
    reasoning = _js_or_zero(completion_details, "reasoning_tokens")
    input_tokens = _js_or_zero(usage, "prompt_tokens") - cached
    output_tokens = _js_or_zero(usage, "completion_tokens") + reasoning
    counts = {"input": input_tokens, "output": output_tokens, "cacheRead": cached, "cacheWrite": 0}
    priced = {name: cost[name] / 1_000_000 * counts[name] for name in counts}
    total = priced["input"] + priced["output"] + priced["cacheRead"] + priced["cacheWrite"]
    return {
        **counts,
        "totalTokens": input_tokens + output_tokens + cached,
        "cost": {**{name: _js_number(value) for name, value in priced.items()}, "total": _js_number(total)},
    }


def pinned_stop_reason(finish_reason: str | None) -> str:
    """openai-completions.js:616-634 restricted to the modeled reasons."""
    if finish_reason is None:
        return "stop"
    if finish_reason not in _STOP_REASONS:
        raise Pi0571UnmodeledFinishReason()
    return _STOP_REASONS[finish_reason]


def _stream_blocks(
    response: NativeProviderResponse, calls: Sequence[Pi0571ToolCall],
) -> list[dict[str, Any]]:
    """Order text and toolCall blocks as the pinned stream loop appends them.

    Each toolCall block sits where its first argument fragment arrived; a
    call with no argument fragment has no observable position and fails.
    """
    if not response.stream_fragments:
        if calls:
            raise Pi0571SemanticsError("tool calls cannot be ordered without stream fragments")
        return [{"type": "text", "text": response.content}] if response.content else []
    tool_indices = {
        fragment.tool_index for fragment in response.stream_fragments
        if fragment.kind == "tool_arguments"
    }
    if None in tool_indices or len(tool_indices) != len(calls):
        raise Pi0571SemanticsError("tool calls cannot be ordered from the stream fragments")
    ordinal = {index: position for position, index in enumerate(sorted(tool_indices))}
    blocks: list[dict[str, Any]] = []
    placed: set[int] = set()
    current: dict[str, Any] | None = None
    for fragment in response.stream_fragments:
        if fragment.kind == "content":
            if not fragment.text:
                continue
            if current is None or current["type"] != "text":
                current = {"type": "text", "text": ""}
                blocks.append(current)
            current["text"] += fragment.text
            continue
        position = ordinal[fragment.tool_index]
        if position not in placed:
            call = calls[position]
            current = {"type": "toolCall", "id": call.id, "name": call.name, "arguments": call.arguments}
            blocks.append(current)
            placed.add(position)
    return blocks


class Pi0571SemanticsState:
    """Pi 0.57.1 agent state driven by the Conductor's native-stream loop."""

    def __init__(
        self,
        *,
        task: str,
        system_prompt: str,
        model_id: str,
        provider: str,
        api: str,
        cost: Mapping[str, Any],
        current_date_time: str,
        request_cap: int = REQUEST_CAP,
        case_id: str | None = None,
        clock: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        for name, value in (
            ("model_id", model_id), ("provider", provider), ("api", api),
            ("current_date_time", current_date_time),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{name} must be non-empty text")
        if not isinstance(cost, Mapping) or set(cost) != {"input", "output", "cacheRead", "cacheWrite"}:
            raise ValueError("cost must carry the pinned model cost members")
        if type(request_cap) is not int or request_cap <= 0:
            raise ValueError("request_cap must be positive")
        self.task = task
        self.system_prompt = system_prompt
        self.model_id = model_id
        self.provider = provider
        self.api = api
        self.cost = dict(cost)
        self.current_date_time = current_date_time
        self.request_cap = request_cap
        self.case_id = case_id
        self._clock = clock
        self.request_count = 0
        self.stream_fn_issued = 0
        self.request_records: list[Pi0571RequestRecord] = []
        self.exit_status: str | None = None
        self.native_stop_reason: str | None = None
        self._query_timestamp: int | None = None
        self.messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": task}], "timestamp": clock()},
        ]

    @property
    def is_exited(self) -> bool:
        return self.exit_status is not None

    def _assistant(self, content: list[dict[str, Any]], usage: dict[str, Any], stop: str) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "api": self.api,
            "provider": self.provider,
            "model": self.model_id,
            "usage": usage,
            "stopReason": stop,
            "timestamp": self._query_timestamp,
        }

    def begin_query(self) -> Pi0571ResponseResult | None:
        """Admit one provider query; the attempt past the cap is refused unsent."""
        if self.is_exited:
            raise Pi0571SemanticsError("episode already exited")
        if self.stream_fn_issued > self.request_count:
            raise Pi0571SemanticsError("a provider query is already pending")
        self.stream_fn_issued += 1
        self._query_timestamp = self._clock()
        if self.request_count < self.request_cap:
            return None
        self.request_records.append(Pi0571RequestRecord(self.stream_fn_issued, False, None))
        assistant = self._assistant([], pinned_usage(None, self.cost), "error")
        self.messages.append(assistant)
        self.exit_status = "RequestLimitExceeded"
        self.native_stop_reason = "error"
        return Pi0571ResponseResult(assistant, (), "error", True)

    def _admit_response(self) -> None:
        if self.is_exited:
            raise Pi0571SemanticsError("episode already exited")
        if self.stream_fn_issued <= self.request_count:
            raise Pi0571SemanticsError("response has no admitted provider query")

    def prepare_response(
        self, response: NativeProviderResponse, parsed_arguments: Sequence[Any],
    ) -> Pi0571ResponseResult:
        """Commit one sent response with worker-parsed tool arguments."""
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("response must be NativeProviderResponse")
        self._admit_response()
        if response.stream_termination is not None:
            raise Pi0571SemanticsError("Pi 0.57.1 does not admit truncated streams")
        stop = pinned_stop_reason(response.finish_reason)
        if not isinstance(parsed_arguments, Sequence) or len(parsed_arguments) != len(response.tool_calls):
            raise Pi0571SemanticsError("parsed argument count differs from the tool calls")
        calls = tuple(
            Pi0571ToolCall(call.id, call.name, arguments)
            for call, arguments in zip(response.tool_calls, parsed_arguments, strict=True)
        )
        blocks = _stream_blocks(response, calls)
        self.request_count += 1
        self.request_records.append(
            Pi0571RequestRecord(self.stream_fn_issued, True, response.request_digest)
        )
        assistant = self._assistant(blocks, pinned_usage(response.usage, self.cost), stop)
        self.messages.append(assistant)
        self.native_stop_reason = stop
        if not calls:
            self.exit_status = "Submitted" if stop == "stop" else stop
        return Pi0571ResponseResult(assistant, calls, stop, not calls)

    def commit_provider_failure(self, message: Mapping[str, Any]) -> None:
        """Commit the worker-projected pinned assistant error message."""
        self._admit_response()
        if (
            not isinstance(message, Mapping)
            or message.get("role") != "assistant"
            or message.get("stopReason") != "error"
            or type(message.get("errorMessage")) is not str
        ):
            raise Pi0571SemanticsError("provider failure message is not a pinned assistant error")
        self.request_count += 1
        self.request_records.append(Pi0571RequestRecord(self.stream_fn_issued, True, None))
        self.messages.append(dict(message))
        self.native_stop_reason = "error"
        self.exit_status = "error"

    def commit_tool_results(
        self, calls: Iterable[Pi0571ToolCall], results: Iterable[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Append toolResult messages in source order (agent-loop.js:250-258)."""
        ordered_calls = tuple(calls)
        ordered_results = tuple(results)
        if len(ordered_calls) != len(ordered_results):
            raise Pi0571SemanticsError("tool result count does not match tool call count")
        committed: list[dict[str, Any]] = []
        for call, raw in zip(ordered_calls, ordered_results, strict=True):
            if (
                not isinstance(raw, Mapping)
                or not _REQUIRED_RESULT_KEYS <= set(raw) <= _RESULT_KEYS
                or raw["id"] != call.id
                or not isinstance(raw["content"], list)
                or type(raw["isError"]) is not bool
            ):
                raise Pi0571SemanticsError("tool result is malformed")
            message: dict[str, Any] = {
                "role": "toolResult",
                "toolCallId": call.id,
                "toolName": call.name,
                "content": list(raw["content"]),
            }
            if "details" in raw:
                message["details"] = raw["details"]
            message["isError"] = raw["isError"]
            message["timestamp"] = self._clock()
            committed.append(message)
        self.messages.extend(committed)
        return tuple(committed)

    def to_trace(
        self,
        *,
        requests: Iterable[Mapping[str, Any]],
        runtime_inputs: Mapping[str, Any],
        effects: Mapping[str, Any],
    ) -> dict[str, Any]:
        if "current_date_time" in runtime_inputs:
            raise Pi0571SemanticsError("current_date_time is observed at initialize, not declared")
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "role": "replay",
            "profile": PROFILE_NAME,
            "version": PROFILE_VERSION,
            "case_id": self.case_id,
            "request_count": self.request_count,
            "stream_fn_issued": self.stream_fn_issued,
            "messages": self.messages,
            "effects": dict(effects),
            "termination": {
                "kind": self.exit_status or "running",
                "native_stop_reason": self.native_stop_reason,
            },
            "requests": [dict(request) for request in requests],
            "runtime_inputs": {**dict(runtime_inputs), "current_date_time": self.current_date_time},
        }


__all__ = [
    "PROFILE_VERSION",
    "REQUEST_CAP",
    "TOOL_NAMES",
    "Pi0571RequestRecord",
    "Pi0571ResponseResult",
    "Pi0571SemanticsError",
    "Pi0571SemanticsState",
    "Pi0571ToolCall",
    "Pi0571UnmodeledFinishReason",
    "pinned_stop_reason",
    "pinned_usage",
]
