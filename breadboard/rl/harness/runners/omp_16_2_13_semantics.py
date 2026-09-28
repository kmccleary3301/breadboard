"""Source-derived Oh My Pi 16.2.13 semantics for the BreadBoard native seam.

Messages mirror the pinned AgentMessage shapes of @oh-my-pi/pi-ai and
@oh-my-pi/pi-agent-core 16.2.13:

* assistant: pi-ai ``src/providers/openai-completions.ts`` builds
  ``{role, content, api, provider, model, usage, stopReason, timestamp}``;
  blocks are appended in stream order.
* usage: ``src/providers/openai-completions.ts:1025-1031`` with calculateCost.
* stop reason: ``mapStopReason`` (``openai-completions.ts:2142-2172``) and
  agent-loop ``recoverTransientErrorToolTurn`` (``agent-loop.ts:1526-1556``).
* length continuation: ``agent-loop.ts:1002-1020`` generates placeholder
  aborted tool results (``createAbortedToolResult``) and continues the loop.
* broken stream recovery: ``agent-loop.ts:1530-1555`` recovers transient stream
  read errors when tool calls were received, promoting stopReason to "toolUse".
* user and toolResult: pi-agent-core ``agent-loop.ts:995-998`` and
  ``agent-loop.ts:2088-2122``.
* compaction: ``agent-session.ts:9508,11244-11400`` threshold compaction events.

BreadBoard's Conductor owns the loop. The ninth provider query past the cap
(REQUEST_CAP = 8) is refused before any HTTP (BB bounded control
``bounded_request_cap``; upstream has no native CLI cap).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import time
from types import MappingProxyType
from typing import Any

from breadboard.rl.harness.runners.base import RunnerProtocolError
from breadboard_engine.provider.native_response import NativeProviderResponse


PROFILE_NAME = "oh_my_pi"
PROFILE_VERSION = "16.2.13"
TRACE_SCHEMA_VERSION = "bb.e4.omp-replay-trace.v1"
CONSUMER_ID = "breadboard.oh-my-pi.v16.2.13"
PHASE_SCHEMA_VERSION = "bb.omp-native.v16.2.13"
TARGET_ID = "oh-my-pi-r2@16.2.13"
LOCAL_ADAPTER_ID = "oh-my-pi.local.v16.2.13"
TOOL_NAMES = ("read", "bash", "edit", "write", "generate_image")
REQUEST_CAP = 8

# Pinned mapStopReason (openai-completions.ts:2142-2172); null -> "stop".
_STOP_REASONS = MappingProxyType({
    "stop": "stop",
    "end": "stop",
    "length": "length",
    "function_call": "toolUse",
    "tool_calls": "toolUse",
    "content_filter": "error",
    "network_error": "error",
    "error": "error",
})

_RESULT_KEYS = frozenset({"id", "completion_index", "content", "isError", "details", "terminate"})
_REQUIRED_RESULT_KEYS = frozenset({"id", "completion_index", "content", "isError"})


class Omp16213SemanticsError(RuntimeError):
    """A replay-state invariant was violated."""


class Omp16213UnmodeledFinishReason(RunnerProtocolError):
    """The provider finish_reason lies outside the pinned stop mapping."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__(
            f"provider finish_reason is outside the pinned stop mapping: {reason!r}",
            code="native_unmodeled_finish_reason",
        )


@dataclass(frozen=True, slots=True)
class Omp16213ToolCall:
    id: str
    name: str
    arguments: Any
    partial_args: str | None = None
    stream_index: int | None = None
@dataclass(frozen=True, slots=True)
class Omp16213ResponseResult:
    assistant: dict[str, Any]
    calls: tuple[Omp16213ToolCall, ...]
    stop_reason: str
    quiescent: bool
    dispatch_calls: tuple[Omp16213ToolCall, ...] = ()
    synthetic_results: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class Omp16213RequestRecord:
    attempt: int
    sent: bool
    request_digest: str | None


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _js_number(value: float) -> int | float:
    # JSON.stringify renders integral doubles without a fraction.
    return int(value) if float(value).is_integer() else value


def _js_or_zero(container: Any, key: str) -> Any:
    # Mirrors container?.[key] || 0 for the pinned usage members.
    if not isinstance(container, Mapping) or key not in container:
        return 0
    value = container[key]
    return value if value else 0


def pinned_usage(usage: Mapping[str, Any] | None, cost: Mapping[str, Any]) -> dict[str, Any]:
    """openai-completions.ts:1025-1031 plus models.ts calculateCost.

    Empty/unreported usage matches openai-shared.ts:2302-2320
    createInitialResponsesAssistantMessage and openai-completions.ts:565.
    """
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
        "cost": {**{name: _js_number(val) for name, val in priced.items()}, "total": _js_number(total)},
    }


def pinned_stop_reason(finish_reason: str | None) -> str:
    """openai-completions.ts:2142-2172 restricted to the modeled reasons."""
    if finish_reason is None:
        return "stop"
    if finish_reason not in _STOP_REASONS:
        raise Omp16213UnmodeledFinishReason(finish_reason)
    return _STOP_REASONS[finish_reason]


def _stream_blocks(
    response: NativeProviderResponse, calls: Sequence[Omp16213ToolCall],
) -> list[dict[str, Any]]:
    """Order text and toolCall blocks as the pinned stream loop appends them."""
    if not response.stream_fragments:
        blocks: list[dict[str, Any]] = []
        if response.content:
            blocks.append({"type": "text", "text": response.content})
        for call in calls:
            blocks.append({"type": "toolCall", "id": call.id, "name": call.name, "arguments": call.arguments})
        return blocks

    tool_indices = {
        fragment.tool_index for fragment in response.stream_fragments
        if fragment.kind == "tool_arguments"
    }
    def _tool_call_block(call: Omp16213ToolCall) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "toolCall",
            "id": call.id,
            "name": call.name,
            "arguments": call.arguments,
        }
        if call.partial_args is not None:
            block["partialArgs"] = call.partial_args
        if call.stream_index is not None:
            block["streamIndex"] = call.stream_index
        return block

    if None in tool_indices or len(tool_indices) != len(calls):
        # Fallback to direct sequential ordering if fragment tool indices are ambiguous
        blocks = []
        if response.content:
            blocks.append({"type": "text", "text": response.content})
        for call in calls:
            blocks.append(_tool_call_block(call))
        return blocks

    ordinal = {index: position for position, index in enumerate(sorted(tool_indices))}
    blocks = []
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
            blocks.append(_tool_call_block(calls[position]))
            placed.add(position)
    return blocks


class Omp16213SemanticsState:
    """Oh My Pi 16.2.13 agent state driven by the Conductor's native-stream loop."""

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
        length_aborted_message: str,
        runtime_inputs: Mapping[str, Any],
        request_cap: int = REQUEST_CAP,
        case_id: str | None = None,
        clock: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        for name, value in (
            ("model_id", model_id), ("provider", provider), ("api", api),
            ("current_date_time", current_date_time),
            ("length_aborted_message", length_aborted_message),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{name} must be non-empty text")
        if not isinstance(cost, Mapping) or set(cost) != {"input", "output", "cacheRead", "cacheWrite"}:
            raise ValueError("cost must carry the pinned model cost members")
        if not isinstance(runtime_inputs, Mapping) or not {"cwd", "home", "current_date", "package_dir"} <= set(runtime_inputs):
            raise ValueError("runtime_inputs must carry cwd, home, current_date, package_dir")
        if type(request_cap) is not int or request_cap <= 0:
            raise ValueError("request_cap must be positive")
        self.task = task
        self.system_prompt = system_prompt
        self.model_id = model_id
        self.provider = provider
        self.api = api
        self.cost = dict(cost)
        self.current_date_time = current_date_time
        self.length_aborted_message = length_aborted_message
        self.runtime_inputs = dict(runtime_inputs)
        self.request_cap = request_cap
        self.case_id = case_id
        self._clock = clock
        self.request_count = 0
        self.stream_fn_issued = 0
        self.tool_admissions = 0
        self.request_records: list[Omp16213RequestRecord] = []
        self.exit_status: str | None = None
        self.native_stop_reason: str | None = None
        self._query_timestamp: int | None = None
        self.messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": task}], "attribution": "user", "timestamp": clock()},
        ]

    @property
    def is_exited(self) -> bool:
        return self.exit_status is not None

    def _assistant(
        self,
        content: list[dict[str, Any]],
        usage: dict[str, Any],
        stop: str,
        *,
        response_id: str | None = None,
        duration: float | None = None,
        ttft: float | None = None,
    ) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "api": self.api,
            "provider": self.provider,
            "model": self.model_id,
            "usage": usage,
            "stopReason": stop,
            "timestamp": self._query_timestamp if self._query_timestamp is not None else self._clock(),
        }
        if response_id is not None:
            msg["responseId"] = response_id
        if duration is not None:
            msg["duration"] = duration
        if ttft is not None:
            msg["ttft"] = ttft
        return msg

    def begin_query(self) -> Omp16213ResponseResult | None:
        """Admit one provider query; the attempt past the cap is refused unsent."""
        if self.is_exited:
            raise Omp16213SemanticsError("episode already exited")
        if self.stream_fn_issued > self.request_count:
            raise Omp16213SemanticsError("a provider query is already pending")
        self.stream_fn_issued += 1
        self._query_timestamp = self._clock()
        if self.request_count < self.request_cap:
            return None
        # Refuse request >= request_cap before any HTTP (divergence bounded_request_cap).
        self.request_records.append(Omp16213RequestRecord(self.stream_fn_issued, False, None))
        assistant = self._assistant([], pinned_usage(None, self.cost), "error")
        self.messages.append(assistant)
        self.exit_status = "RequestLimitExceeded"
        self.native_stop_reason = "error"
        return Omp16213ResponseResult(assistant, (), "error", True)

    def _admit_response(self) -> None:
        if self.is_exited:
            raise Omp16213SemanticsError("episode already exited")
        if self.stream_fn_issued <= self.request_count:
            raise Omp16213SemanticsError("response has no admitted provider query")

    def prepare_response(
        self, response: NativeProviderResponse, parsed_arguments: Sequence[Any] | None = None,
    ) -> Omp16213ResponseResult:
        """Commit one sent response with worker-parsed tool arguments."""
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("response must be NativeProviderResponse")
        self._admit_response()

        raw_calls = response.tool_calls
        is_severed = response.stream_termination is not None
        if parsed_arguments is not None:
            if not isinstance(parsed_arguments, Sequence) or len(parsed_arguments) != len(raw_calls):
                raise Omp16213SemanticsError("parsed argument count differs from tool calls")
            calls = tuple(
                Omp16213ToolCall(
                    call.id,
                    call.name,
                    args,
                    partial_args=None if is_severed else call.arguments,
                    stream_index=None if is_severed else i,
                )
                for i, (call, args) in enumerate(zip(raw_calls, parsed_arguments, strict=True))
            )
        else:
            calls = tuple(
                Omp16213ToolCall(
                    call.id,
                    call.name,
                    call.arguments,
                    partial_args=None if is_severed else call.arguments,
                    stream_index=None if is_severed else i,
                )
                for i, call in enumerate(raw_calls)
            )

        stop = pinned_stop_reason(response.finish_reason)

        # Severed stream handling (o10, openai-completions.ts:239-250, agent-loop.ts:1530-1555):
        # If the stream was truncated mid-arguments but tool calls were received,
        # recoverTransientErrorToolTurn promotes stopReason from error/stop to "toolUse".
        if response.stream_termination is not None and calls:
            stop = "toolUse"
        elif stop == "stop" and calls:
            # openai-completions.ts:1247-1248: promote natural-completion finish to "toolUse"
            stop = "toolUse"

        blocks = _stream_blocks(response, calls)
        self.request_count += 1
        self.request_records.append(
            Omp16213RequestRecord(self.stream_fn_issued, True, response.request_digest)
        )
        assistant = self._assistant(
            blocks, pinned_usage(response.usage, self.cost), stop, response_id=response.response_id,
        )
        self.messages.append(assistant)
        self.native_stop_reason = stop

        # Length cutoff handling (o11, agent-loop.ts:1002-1020):
        # When finish_reason is "length" and tool calls are present, the tool calls
        # are truncated and unsafe to execute. createAbortedToolResult creates synthetic
        # tool results and the agent loop continues (hasMoreToolCalls = True).
        if stop == "length" and calls:
            synthetic_results: list[dict[str, Any]] = []
            for position, call in enumerate(calls):
                synthetic_results.append({
                    "id": call.id,
                    "completion_index": position,
                    "content": [{"type": "text", "text": self.length_aborted_message}],
                    "details": {},
                    "isError": True,
                })
            return Omp16213ResponseResult(
                assistant=assistant,
                calls=calls,
                stop_reason=stop,
                quiescent=False,
                dispatch_calls=(),
                synthetic_results=tuple(synthetic_results),
            )

        if calls:
            # Normal toolUse dispatch
            return Omp16213ResponseResult(
                assistant=assistant,
                calls=calls,
                stop_reason=stop,
                quiescent=False,
                dispatch_calls=calls,
                synthetic_results=(),
            )

        # No tool calls: final conversational stop or terminal error
        self.exit_status = "Submitted" if stop == "stop" else stop
        return Omp16213ResponseResult(
            assistant=assistant,
            calls=(),
            stop_reason=stop,
            quiescent=True,
            dispatch_calls=(),
            synthetic_results=(),
        )

    def commit_provider_failure(self, message: Mapping[str, Any] | str) -> None:
        """Commit the worker-projected pinned assistant error message (o5)."""
        self._admit_response()
        self.request_count += 1
        self.request_records.append(Omp16213RequestRecord(self.stream_fn_issued, True, None))
        if isinstance(message, Mapping):
            if (
                message.get("role") != "assistant"
                or message.get("stopReason") != "error"
                or type(message.get("errorMessage")) is not str
            ):
                raise Omp16213SemanticsError("provider failure message is not a pinned assistant error")
            committed = dict(message)
        else:
            committed = self._assistant([], pinned_usage(None, self.cost), "error")
            committed["errorMessage"] = str(message)

        self.messages.append(committed)
        self.native_stop_reason = "error"
        self.exit_status = "error"

    def commit_tool_results(
        self, calls: Iterable[Omp16213ToolCall], results: Iterable[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Append toolResult messages in source order (agent-loop.ts:995-998, 2088-2122)."""
        ordered_calls = tuple(calls)
        ordered_results = tuple(results)
        if len(ordered_calls) != len(ordered_results):
            raise Omp16213SemanticsError("tool result count does not match tool call count")
        committed: list[dict[str, Any]] = []
        for call, raw in zip(ordered_calls, ordered_results, strict=True):
            if (
                not isinstance(raw, Mapping)
                or not _REQUIRED_RESULT_KEYS <= set(raw) <= _RESULT_KEYS
                or raw["id"] != call.id
                or not isinstance(raw["content"], list)
                or type(raw["isError"]) is not bool
            ):
                raise Omp16213SemanticsError("tool result is malformed")
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
        self.tool_admissions += len(committed)
        return tuple(committed)

    def to_trace(
        self,
        *,
        requests: Iterable[Mapping[str, Any]],
        runtime_inputs: Mapping[str, Any],
        effects: Mapping[str, Any],
        **_extra: Any,
    ) -> dict[str, Any]:
        """Produce the 12-key trace dictionary agreed with OmpComparator."""
        effective_inputs = dict(runtime_inputs) if runtime_inputs else dict(self.runtime_inputs)
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
            "runtime_inputs": effective_inputs,
        }


__all__ = [
    "CONSUMER_ID",
    "LOCAL_ADAPTER_ID",
    "Omp16213RequestRecord",
    "Omp16213ResponseResult",
    "Omp16213SemanticsError",
    "Omp16213SemanticsState",
    "Omp16213ToolCall",
    "Omp16213UnmodeledFinishReason",
    "PHASE_SCHEMA_VERSION",
    "PROFILE_NAME",
    "PROFILE_VERSION",
    "REQUEST_CAP",
    "TARGET_ID",
    "TOOL_NAMES",
    "TRACE_SCHEMA_VERSION",
    "pinned_stop_reason",
    "pinned_usage",
]
