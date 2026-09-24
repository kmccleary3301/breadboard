"""Source-derived Pi 0.73.1 semantics for the BreadBoard native seam.

The stream consumer reads exactly these fields from ``NativeProviderResponse``:
``content``, ``finish_reason``, ``tool_calls`` (ordered values with ``id``,
``name``, and raw ``arguments``), and ``stream_fragments`` (ordered values with
``kind``, ``index``, ``text``, optional ``call_id`` and ``name``).  Binding and
request digests are retained in the request record but never interpreted here.
The consumer reconstructs tool arguments from ordered argument fragments and
hands each call to the pinned Node worker for validation and execution.

BreadBoard's Conductor owns the loop.  It drives this state through
``begin_query`` (the streamFn seam; the ninth attempt at the public
eight-request cap is refused locally before any HTTP), ``prepare_response``,
``commit_tool_results``, and ``to_trace``.  Request bodies, runtime inputs,
and workspace effects are Conductor-owned facts passed into ``to_trace``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Iterable, Mapping

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






# Pinned partial-json options from node_modules/partial-json/dist/options.js:13-50
class Allow:
    STR = 0b000000001
    NUM = 0b000000010
    ARR = 0b000000100
    OBJ = 0b000001000
    NULL = 0b000010000
    BOOL = 0b000100000
    NAN = 0b001000000
    INFINITY = 0b010000000
    _INFINITY = 0b100000000
    INF = INFINITY | _INFINITY
    SPECIAL = NULL | BOOL | INF | NAN
    ATOM = STR | NUM | SPECIAL
    COLLECTION = ARR | OBJ
    ALL = ATOM | COLLECTION


class PartialJSON(ValueError):
    """Pinned PartialJSON error from node_modules/partial-json/dist/index.js:21-23."""


class MalformedJSON(ValueError):
    """Pinned MalformedJSON error from node_modules/partial-json/dist/index.js:24-26."""


_VALID_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}


def _is_control_character(char: str) -> bool:
    # node_modules/@mariozechner/pi-ai/dist/utils/json-parse.js:3-6
    cp = ord(char)
    return cp <= 0x1F


def _escape_control_character(char: str) -> str:
    # node_modules/@mariozechner/pi-ai/dist/utils/json-parse.js:7-22
    escapes = {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    if char in escapes:
        return escapes[char]
    return f"\\u{ord(char):04x}"


def repair_json(json_str: str) -> str:
    # node_modules/@mariozechner/pi-ai/dist/utils/json-parse.js:28-69
    repaired: list[str] = []
    in_string = False
    index = 0
    length = len(json_str)
    while index < length:
        char = json_str[index]
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == '"':
            repaired.append(char)
            in_string = False
            index += 1
            continue
        if char == "\\":
            if index + 1 >= length:
                repaired.append("\\\\")
                index += 1
                continue
            next_char = json_str[index + 1]
            if next_char == "u":
                unicode_digits = json_str[index + 2 : index + 6]
                if len(unicode_digits) == 4 and all(c in "0123456789abcdefABCDEF" for c in unicode_digits):
                    repaired.append(f"\\u{unicode_digits}")
                    index += 6
                    continue
            if next_char in _VALID_JSON_ESCAPES:
                repaired.append(f"\\{next_char}")
                index += 2
                continue
            repaired.append("\\\\")
            index += 1
            continue
        repaired.append(_escape_control_character(char) if _is_control_character(char) else char)
        index += 1
    return "".join(repaired)


def parse_json_with_repair(json_str: str) -> Any:
    # node_modules/@mariozechner/pi-ai/dist/utils/json-parse.js:71-82
    try:
        return json.loads(json_str)
    except Exception:
        repaired = repair_json(json_str)
        if repaired != json_str:
            return json.loads(repaired)
        raise


def partial_parse(json_string: str, allow: int = Allow.ALL) -> Any:
    # node_modules/partial-json/dist/index.js:35-44
    if not isinstance(json_string, str):
        raise TypeError(f"expecting str, got {type(json_string).__name__}")
    trimmed = json_string.strip()
    if not trimmed:
        raise ValueError(f"{json_string} is empty")
    return _parse_json(trimmed, allow)


def _parse_json(json_string: str, allow: int) -> Any:
    # node_modules/partial-json/dist/index.js:46-218
    length = len(json_string)
    index = 0

    def mark_partial_json(msg: str) -> None:
        # node_modules/partial-json/dist/index.js:49-51
        raise PartialJSON(f"{msg} at position {index}")

    def throw_malformed_error(msg: str) -> None:
        # node_modules/partial-json/dist/index.js:52-54
        raise MalformedJSON(f"{msg} at position {index}")

    def skip_blank() -> None:
        # node_modules/partial-json/dist/index.js:212-216
        nonlocal index
        while index < length and json_string[index] in " \n\r\t":
            index += 1

    def parse_any() -> Any:
        # node_modules/partial-json/dist/index.js:55-90
        nonlocal index
        skip_blank()
        if index >= length:
            mark_partial_json("Unexpected end of input")
        char = json_string[index]
        if char == '"':
            return parse_str()
        if char == "{":
            return parse_obj()
        if char == "[":
            return parse_arr()
        if json_string[index : index + 4] == "null" or (
            (Allow.NULL & allow) and length - index < 4 and "null".startswith(json_string[index:])
        ):
            index += 4
            return None
        if json_string[index : index + 4] == "true" or (
            (Allow.BOOL & allow) and length - index < 4 and "true".startswith(json_string[index:])
        ):
            index += 4
            return True
        if json_string[index : index + 5] == "false" or (
            (Allow.BOOL & allow) and length - index < 5 and "false".startswith(json_string[index:])
        ):
            index += 5
            return False
        if json_string[index : index + 8] == "Infinity" or (
            (Allow.INFINITY & allow) and length - index < 8 and "Infinity".startswith(json_string[index:])
        ):
            index += 8
            return float("inf")
        if json_string[index : index + 9] == "-Infinity" or (
            (Allow._INFINITY & allow) and 1 < length - index < 9 and "-Infinity".startswith(json_string[index:])
        ):
            index += 9
            return float("-inf")
        if json_string[index : index + 3] == "NaN" or (
            (Allow.NAN & allow) and length - index < 3 and "NaN".startswith(json_string[index:])
        ):
            index += 3
            return float("nan")
        return parse_num()

    def parse_str() -> str:
        # node_modules/partial-json/dist/index.js:91-117
        nonlocal index
        start = index
        escape = False
        index += 1  # skip initial quote
        while index < length and (json_string[index] != '"' or (escape and json_string[index - 1] == "\\")):
            escape = not escape if json_string[index] == "\\" else False
            index += 1
        if index < length and json_string[index] == '"':
            try:
                end = index + 1 - (1 if escape else 0)
                index += 1
                return json.loads(json_string[start:end])
            except Exception as e:
                throw_malformed_error(str(e))
        elif Allow.STR & allow:
            try:
                end = index - (1 if escape else 0)
                return json.loads(json_string[start:end] + '"')
            except Exception:
                last_backslash = json_string.rfind("\\", start, index)
                if last_backslash != -1:
                    return json.loads(json_string[start:last_backslash] + '"')
                raise
        mark_partial_json("Unterminated string literal")

    def parse_obj() -> dict[str, Any]:
        # node_modules/partial-json/dist/index.js:118-153
        nonlocal index
        index += 1  # skip initial brace
        skip_blank()
        obj: dict[str, Any] = {}
        try:
            while True:
                skip_blank()
                if index >= length:
                    if Allow.OBJ & allow:
                        return obj
                    mark_partial_json("Expected '}' at end of object")
                if json_string[index] == "}":
                    break
                key = parse_str()
                skip_blank()
                index += 1  # skip colon
                try:
                    value = parse_any()
                    obj[key] = value
                except Exception as e:
                    if Allow.OBJ & allow:
                        return obj
                    raise e
                skip_blank()
                if index < length and json_string[index] == ",":
                    index += 1  # skip comma
        except Exception:
            if Allow.OBJ & allow:
                return obj
            mark_partial_json("Expected '}' at end of object")
        index += 1  # skip final brace
        return obj

    def parse_arr() -> list[Any]:
        # node_modules/partial-json/dist/index.js:154-174
        nonlocal index
        index += 1  # skip initial bracket
        arr: list[Any] = []
        try:
            while True:
                skip_blank()
                if index >= length:
                    if Allow.ARR & allow:
                        return arr
                    mark_partial_json("Expected ']' at end of array")
                if json_string[index] == "]":
                    break
                arr.append(parse_any())
                skip_blank()
                if index < length and json_string[index] == ",":
                    index += 1  # skip comma
        except Exception:
            if Allow.ARR & allow:
                return arr
            mark_partial_json("Expected ']' at end of array")
        index += 1  # skip final bracket
        return arr

    def parse_num() -> Any:
        # node_modules/partial-json/dist/index.js:175-211
        nonlocal index
        if index == 0:
            if json_string == "-":
                throw_malformed_error("Not sure what '-' is")
            try:
                return json.loads(json_string)
            except Exception as e:
                if Allow.NUM & allow:
                    try:
                        return json.loads(json_string[: json_string.rfind("e")])
                    except Exception:
                        pass
                throw_malformed_error(str(e))
        start = index
        if index < length and json_string[index] == "-":
            index += 1
        while index < length and json_string[index] not in ",]}":
            index += 1
        if index == length and not (Allow.NUM & allow):
            mark_partial_json("Unterminated number literal")
        sub = json_string[start:index]
        try:
            return json.loads(sub)
        except Exception as e:
            if sub == "-":
                mark_partial_json("Not sure what '-' is")
            try:
                last_e = sub.rfind("e")
                if last_e != -1:
                    return json.loads(sub[:last_e])
            except Exception:
                pass
            throw_malformed_error(str(e))

    return parse_any()


def parse_streaming_json(partial_json: str | None) -> Any:
    # node_modules/@mariozechner/pi-ai/dist/utils/json-parse.js:90-112
    if not partial_json or not partial_json.strip():
        return {}
    try:
        return parse_json_with_repair(partial_json)
    except Exception:
        try:
            result = partial_parse(partial_json)
            return {} if result is None else result
        except Exception:
            try:
                result = partial_parse(repair_json(partial_json))
                return {} if result is None else result
            except Exception:
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
        system_prompt: str = "",
        request_cap: int = DEFAULT_REQUEST_CAP,
        image_delivery: bool = False,
        case_id: str | None = None,
        model_id: str,
        provider: str,
        api: str = "openai-completions",
    ) -> None:
        if request_cap <= 0:
            raise ValueError("request_cap must be positive")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id must be non-empty text")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be non-empty text")
        if not isinstance(api, str) or not api:
            raise ValueError("api must be non-empty text")
        self.task = task
        self.system_prompt = system_prompt
        self.request_cap = request_cap
        self.image_delivery = image_delivery
        self.case_id = case_id
        self.model_id = model_id
        self.provider = provider
        self.api = api
        self.request_count = 0
        self.stream_fn_issued = 0
        self.request_records: list[PiRequestRecord] = []
        self.messages: list[dict[str, Any]] = []
        self.exit_status: str | None = None
        self.native_stop_reason: str | None = None
        self.messages.append({"role": "user", "content": [{"type": "text", "text": task}]})

    @property
    def is_exited(self) -> bool:
        return self.exit_status is not None

    def _cap_response(self) -> PiResponseResult:
        assistant = {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "stopReason": "error",
            "api": self.api,
            "provider": self.provider,
            "model": self.model_id,
            "text": "",
        }
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
        assistant = {
            "role": "assistant",
            "content": blocks,
            "stopReason": stop_reason,
            "api": self.api,
            "provider": self.provider,
            "model": self.model_id,
        }
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
]
