"""Source-derived Pi 0.73.1 semantics for the BreadBoard native seam.

The stream consumer reads exactly these fields from ``NativeProviderResponse``:
``content``, ``finish_reason``, ``tool_calls`` (ordered values with ``id``,
``name``, and raw ``arguments``), and ``stream_fragments`` (ordered values with
``kind``, ``index``, ``text``, optional ``call_id`` and ``name``).  Binding and
request digests are retained in the request record but never interpreted here.
The consumer reconstructs tool arguments from ordered argument fragments and
hands each call to the pinned Node worker for validation and execution.

The generic host seam is deliberately small: a caller supplies one
``NativeProviderResponse`` for each admitted HTTP response, or a ``stream_fn``
that returns one.  ``PiSemanticsState.request()`` counts every streamFn seam
attempt, while the ninth attempt at the public eight-request cap is handled
locally and does not call the supplied stream function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

from breadboard_engine.provider.native_response import NativeProviderResponse


TOOL_NAMES = ("read", "bash", "edit", "write")
DEFAULT_REQUEST_CAP = 8
DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
COMPLETE_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class PiSemanticsError(RuntimeError):
    """Base class for source-semantics failures exposed as tool observations."""


class PiRequestCapExceeded(PiSemanticsError):
    """Raised only by callers that ask to submit a ninth provider request."""


@dataclass(frozen=True, slots=True)
class PiToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PiToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)
    terminate: bool = False

    def as_message(self) -> dict[str, Any]:
        native_content = self.details.get("native_content")
        content = native_content if isinstance(native_content, list) and self.details.get("image_delivery") else [{"type": "text", "text": self.content}]
        return {
            "role": "toolResult",
            "toolCallId": self.call_id,
            "toolName": self.name,
            "content": content,
            "isError": self.is_error,
        }


@dataclass(frozen=True, slots=True)
class PiResponseResult:
    assistant: dict[str, Any]
    calls: tuple[PiToolCall, ...]
    results: tuple[PiToolResult, ...]
    stop_reason: str
    quiescent: bool


@dataclass(frozen=True, slots=True)
class PiRequestRecord:
    """One streamFn seam attempt and whether it resulted in HTTP."""

    attempt: int
    sent: bool
    request_digest: str | None






def repair_json(value: str) -> str:
    """Repair control chars and invalid backslash escapes as Pi does."""
    escapes = set('"\\/bfnrtu')
    out: list[str] = []
    in_string = False
    index = 0
    while index < len(value):
        char = value[index]
        if not in_string:
            out.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            out.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            nxt = value[index + 1] if index + 1 < len(value) else None
            if nxt == "u" and re.match(r"^[0-9a-fA-F]{4}$", value[index + 2 : index + 6]):
                out.append(value[index : index + 6])
                index += 6
                continue
            if nxt in escapes:
                out.extend(("\\", nxt))
                index += 2
                continue
            out.extend(("\\", "\\"))
            index += 1
            continue
        if ord(char) <= 0x1F:
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(char, f"\\u{ord(char):04x}"))
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _close_partial_json(value: str) -> str:
    """Close the common object/array/string prefixes accepted by partial-json."""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack and stack[-1] == char:
            stack.pop()
    repaired = repair_json(value)
    if in_string:
        repaired += '"'
    repaired += "".join(reversed(stack))
    return repaired


def parse_streaming_json(value: str | None) -> dict[str, Any]:
    if not value or not value.strip():
        return {}
    candidates = (value, repair_json(value), _close_partial_json(value), _close_partial_json(repair_json(value)))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}




def execute_pi_tool(
    name: str,
    arguments: Mapping[str, Any] | Any,
    cwd: str | os.PathLike[str],
    *,
    image_delivery: bool = False,
    call_id: str = "",
) -> PiToolResult:
    """Execute one Pi tool through the pinned Node worker."""
    try:
        from breadboard.rl.harness.pi_native_tools import execute_native_tool

        result = execute_native_tool(name, arguments, cwd=cwd, image_delivery=image_delivery, call_id=call_id)
        return PiToolResult(
            call_id,
            name,
            str(result.get("text", "")),
            bool(result.get("isError", False)),
            result.get("details", {}),
            bool(result.get("terminate", False)),
        )
    except Exception as exc:
        return PiToolResult(call_id, name, str(exc), True)


def _fragment_tool_calls(response: NativeProviderResponse) -> tuple[PiToolCall, ...]:
    """Project decoder-finalized tool calls without reassembling fragments.

    ``NativeStreamFragment.index`` is a global fragment ordinal.  The native
    transport already joins argument deltas by provider tool index and fills
    delayed IDs before constructing ``response.tool_calls``; rejoining here
    would misassign same-name calls and reorder the model's batch.
    """
    result: list[PiToolCall] = []
    for call in response.tool_calls:
        result.append(PiToolCall(call.id, call.name, parse_streaming_json(call.arguments)))
    return tuple(result)


def _content_from_response(response: NativeProviderResponse) -> str:
    if response.content is not None:
        return response.content
    return "".join(fragment.text for fragment in response.stream_fragments if fragment.kind == "content")


def _native_stop_reason(finish_reason: str) -> str:
    return {
        "tool_calls": "toolUse",
        "stop": "stop",
        "length": "length",
        "content_filter": "error",
        "error": "error",
        "aborted": "aborted",
    }.get(finish_reason, finish_reason)


class PiSemanticsState:
    """Pi 0.73.1 agent state with a bounded streamFn admission seam."""

    def __init__(
        self,
        *,
        task: str = "",
        cwd: str | os.PathLike[str] = ".",
        system_prompt: str = "",
        request_cap: int = DEFAULT_REQUEST_CAP,
        image_delivery: bool = False,
        case_id: str | None = None,
    ) -> None:
        if request_cap <= 0:
            raise ValueError("request_cap must be positive")
        self.task = task
        self.cwd = Path(cwd).resolve()
        self.system_prompt = system_prompt
        self.request_cap = request_cap
        self.image_delivery = image_delivery
        self.case_id = case_id
        self.request_count = 0
        self.stream_fn_issued = 0
        self.request_records: list[PiRequestRecord] = []
        self.messages: list[dict[str, Any]] = []
        self.effects: dict[str, dict[str, Any]] = {}
        self.exit_status: str | None = None
        self.native_stop_reason: str | None = None
        self.messages.append({"role": "user", "content": [{"type": "text", "text": task}]})

    @property
    def is_exited(self) -> bool:
        return self.exit_status is not None

    def _cap_response(self) -> PiResponseResult:
        assistant = {"role": "assistant", "content": [], "stopReason": "error", "text": ""}
        self.messages.append(assistant)
        self.exit_status = "RequestLimitExceeded"
        self.native_stop_reason = "error"
        return PiResponseResult(assistant, (), (), "error", True)

    def begin_query(self) -> PiResponseResult | None:
        """Admit one provider query before transport performs network I/O.

        A ``None`` result reserves the query slot.  Once the cap is reached,
        the ninth attempt is recorded as unsent and returns Pi's terminal
        request-limit response without invoking transport.
        """
        if self.stream_fn_issued > self.request_count:
            raise PiSemanticsError("a provider query is already pending")
        self.stream_fn_issued += 1
        if self.request_count >= self.request_cap:
            self.request_records.append(PiRequestRecord(self.stream_fn_issued, False, None))
            return self._cap_response()
        return None

    def request(self, stream_fn: Callable[..., NativeProviderResponse], request: Any = None) -> NativeProviderResponse | PiResponseResult:
        """Issue one streamFn attempt; cap checks happen before calling it."""
        terminal = self.begin_query()
        if terminal is not None:
            return terminal
        response = stream_fn(request) if request is not None else stream_fn()
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("stream_fn must return NativeProviderResponse")
        return response

    def prepare_response(self, response: NativeProviderResponse) -> PiResponseResult:
        """Commit an admitted assistant response without executing its tools."""
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("response must be NativeProviderResponse")
        if self.stream_fn_issued <= self.request_count:
            raise PiSemanticsError("response has no admitted provider query")
        if self.request_count >= self.request_cap:
            return self._cap_response()
        self.request_count += 1
        self.request_records.append(PiRequestRecord(self.stream_fn_issued, True, response.request_digest))
        stop_reason = _native_stop_reason(response.finish_reason)
        calls = () if response.finish_reason in {"error", "aborted"} else _fragment_tool_calls(response)
        content = _content_from_response(response)
        blocks: list[dict[str, Any]] = []
        if content:
            blocks.append({"type": "text", "text": content})
        for call in calls:
            blocks.append({"type": "toolCall", "id": call.id, "name": call.name, "arguments": call.arguments})
        assistant = {"role": "assistant", "content": blocks, "stopReason": stop_reason}
        self.messages.append(assistant)
        self.native_stop_reason = stop_reason
        if not calls:
            self.exit_status = "Submitted" if stop_reason == "stop" else stop_reason
        return PiResponseResult(assistant, calls, (), stop_reason, not calls)

    def _result_from_port(self, call: PiToolCall, raw: PiToolResult | Mapping[str, Any]) -> PiToolResult:
        if isinstance(raw, PiToolResult):
            details = dict(raw.details)
            details.setdefault("image_delivery", self.image_delivery)
            return PiToolResult(call.id, call.name, raw.content, raw.is_error, details, raw.terminate)
        content = raw.get("content", raw.get("text", ""))
        if isinstance(content, list):
            text = "".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            )
            native_content: Any = content
        else:
            text = str(content)
            native_content = [{"type": "text", "text": text}]
        details = dict(raw.get("details", {})) if isinstance(raw.get("details"), Mapping) else {}
        details.setdefault("image_delivery", self.image_delivery)
        return PiToolResult(
            call.id,
            call.name,
            text,
            bool(raw.get("isError", raw.get("is_error", False))),
            details,
            bool(raw.get("terminate", False)),
        )

    def commit_tool_results(
        self,
        calls: Iterable[PiToolCall],
        results: Iterable[PiToolResult | Mapping[str, Any]],
    ) -> tuple[PiToolResult, ...]:
        """Join sandbox results to calls in source order and commit history."""
        ordered_calls = tuple(calls)
        ordered_results = tuple(results)
        if len(ordered_calls) != len(ordered_results):
            raise PiSemanticsError("tool result count does not match tool call count")
        committed = tuple(self._result_from_port(call, raw) for call, raw in zip(ordered_calls, ordered_results))
        for result in committed:
            self.messages.append(result.as_message())
        return committed

    def consume_response(self, response: NativeProviderResponse) -> PiResponseResult:
        """Offline wrapper: prepare a response, execute native tools, then commit."""
        if self.stream_fn_issued <= self.request_count:
            terminal = self.begin_query()
            if terminal is not None:
                return terminal
        prepared = self.prepare_response(response)
        if not prepared.calls:
            return prepared
        from breadboard.rl.harness.pi_native_tools import dispatch_native_tools

        raw_results = dispatch_native_tools(
            [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in prepared.calls
            ],
            cwd=self.cwd,
            image_delivery=self.image_delivery,
        )
        results = self.commit_tool_results(prepared.calls, raw_results)
        return PiResponseResult(
            prepared.assistant,
            prepared.calls,
            results,
            prepared.stop_reason,
            prepared.quiescent,
        )

    def run_episode(
        self,
        responses: Iterable[NativeProviderResponse] | None = None,
        *,
        stream_fn: Callable[..., NativeProviderResponse] | None = None,
        requests: Iterable[Any] | None = None,
    ) -> dict[str, Any]:
        """Replay responses or call a stream function until Pi reaches quiescence."""
        if responses is not None and stream_fn is not None:
            raise ValueError("provide responses or stream_fn, not both")
        results: list[PiResponseResult] = []
        if stream_fn is not None:
            iterator = iter(requests) if requests is not None else iter(())
            while not self.is_exited:
                request = next(iterator, None)
                outcome = self.request(stream_fn, request)
                if isinstance(outcome, PiResponseResult):
                    results.append(outcome)
                    break
                result = self.consume_response(outcome)
                results.append(result)
                if result.quiescent:
                    break
        else:
            iterator = iter(responses or ())
            while not self.is_exited:
                try:
                    response = next(iterator)
                except StopIteration:
                    if self.request_count >= self.request_cap and results and not results[-1].quiescent:
                        terminal = self.begin_query()
                        if terminal is not None:
                            results.append(terminal)
                    break
                result = self.consume_response(response)
                results.append(result)
                if result.quiescent:
                    break
        requests = [
            {
                "attempt": record.attempt,
                "sent": record.sent,
                "request_digest": record.request_digest,
            }
            for record in self.request_records
        ]
        return self.to_trace(
            requests=requests,
            runtime_inputs={},
            effects=self.effects,
        )

    def to_trace(
        self,
        *,
        requests: Iterable[Mapping[str, Any]],
        runtime_inputs: Mapping[str, Any],
        effects: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_values = [dict(request) for request in requests]
        return {
            "schema_version": "bb.e4.pi-replay-trace.v1",
            "role": "replay",
            "profile": "pi",
            "version": "0.73.1",
            "case_id": self.case_id,
            "request_count": self.request_count,
            "stream_fn_issued": self.stream_fn_issued,
            "messages": self.messages,
            "effects": dict(effects),
            "termination": {
                "kind": self.exit_status or "running",
                "native_stop_reason": self.native_stop_reason,
            },
            "requests": request_values,
            "runtime_inputs": dict(runtime_inputs),
        }


def run_episode(
    responses: Iterable[NativeProviderResponse],
    *,
    task: str = "",
    cwd: str | os.PathLike[str] = ".",
    system_prompt: str = "",
    case_id: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for deterministic offline replay."""
    state = PiSemanticsState(task=task, cwd=cwd, system_prompt=system_prompt, case_id=case_id)
    return state.run_episode(responses)


__all__ = [
    "COMPLETE_MARKER",
    "DEFAULT_REQUEST_CAP",
    "PiRequestCapExceeded",
    "PiRequestRecord",
    "PiResponseResult",
    "PiSemanticsError",
    "PiSemanticsState",
    "PiToolCall",
    "PiToolResult",
    "execute_pi_tool",
    "parse_streaming_json",
    "repair_json",
    "run_episode",
]
