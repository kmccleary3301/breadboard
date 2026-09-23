"""Source-derived Pi 0.73.1 semantics for the BreadBoard native seam.

The stream consumer reads exactly these fields from ``NativeProviderResponse``:
``content``, ``finish_reason``, ``tool_calls`` (ordered values with ``id``,
``name``, and raw ``arguments``), and ``stream_fragments`` (ordered values with
``kind``, ``index``, ``text``, optional ``call_id`` and ``name``).  Binding and
request digests are retained in the request record but never interpreted here.
The consumer reconstructs tool arguments from the ordered argument fragments,
then applies Pi's partial-json repair, TypeBox-like primitive coercion, edit
argument preparation, and ordered parallel tool execution.

The generic host seam is deliberately small: a caller supplies one
``NativeProviderResponse`` for each admitted HTTP response, or a ``stream_fn``
that returns one.  ``PiSemanticsState.request()`` counts every streamFn seam
attempt, while the ninth attempt at the public eight-request cap is handled
locally and does not call the supplied stream function.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

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
        return {
            "role": "toolResult",
            "toolCallId": self.call_id,
            "toolName": self.name,
            "content": [{"type": "text", "text": self.content}],
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


_MUTATION_LOCKS: dict[str, threading.Lock] = {}
_MUTATION_LOCKS_GUARD = threading.Lock()


@contextmanager
def _mutation_queue(path: Path):
    key = str(path.resolve())
    with _MUTATION_LOCKS_GUARD:
        lock = _MUTATION_LOCKS.setdefault(key, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _MUTATION_LOCKS_GUARD:
            if not lock.locked() and _MUTATION_LOCKS.get(key) is lock:
                _MUTATION_LOCKS.pop(key, None)


def _js_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _coerce_string(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return _js_string(value)
    return value


def _coerce_number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value
    return value


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


def prepare_edit_arguments(value: Any) -> Any:
    """Apply Pi's legacy edit argument and JSON-string edits compatibility."""
    if not isinstance(value, Mapping):
        return value
    args = dict(value)
    if isinstance(args.get("edits"), str):
        try:
            parsed = json.loads(args["edits"])
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            args["edits"] = parsed
    if isinstance(args.get("oldText"), str) and isinstance(args.get("newText"), str):
        edits = list(args.get("edits")) if isinstance(args.get("edits"), list) else []
        edits.append({"oldText": args["oldText"], "newText": args["newText"]})
        args.pop("oldText", None)
        args.pop("newText", None)
        args["edits"] = edits
    return args


def _coerce_tool_arguments(name: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, Mapping):
        raise ValueError("Tool arguments must be an object")
    result = dict(args)
    if name in {"read", "edit", "write"}:
        if "path" in result:
            result["path"] = _coerce_string(result["path"])
    if name == "read":
        for key in ("offset", "limit"):
            if key in result:
                result[key] = _coerce_number(result[key])
    elif name == "write" and "content" in result:
        result["content"] = _coerce_string(result["content"])
    elif name == "bash":
        if "command" in result:
            result["command"] = _coerce_string(result["command"])
        if "timeout" in result:
            result["timeout"] = _coerce_number(result["timeout"])
    elif name == "edit":
        result = prepare_edit_arguments(result)
    return result


def _require_string(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key}: expected string")
    return value


def _resolve_path(cwd: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else cwd / path


def _node_missing(path: Path) -> str:
    return f"ENOENT: no such file or directory, access '{path}'"


def _truncate_head(text: str, *, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[str, bool]:
    lines = text.split("\n")
    selected: list[str] = []
    consumed = 0
    truncated = False
    for index, line in enumerate(lines):
        candidate = line if not selected else "\n" + line
        if len(selected) >= max_lines or consumed + len(candidate.encode("utf-8")) > max_bytes:
            truncated = True
            break
        selected.append(line)
        consumed += len(candidate.encode("utf-8"))
    output = "\n".join(selected)
    return output, truncated


def _truncate_tail(text: str, *, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[str, bool]:
    lines = text.split("\n")
    truncated = len(lines) > max_lines
    selected = lines[-max_lines:] if truncated else lines
    output = "\n".join(selected)
    while len(output.encode("utf-8")) > max_bytes and "\n" in output:
        truncated = True
        output = output.split("\n", 1)[1]
    if len(output.encode("utf-8")) > max_bytes:
        truncated = True
        raw = output.encode("utf-8")[-max_bytes:]
        while raw and raw[0] & 0xC0 == 0x80:
            raw = raw[1:]
        output = raw.decode("utf-8", "replace")
    return output, truncated


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _read_tool(cwd: Path, args: Mapping[str, Any], *, image_delivery: bool) -> tuple[str, Mapping[str, Any]]:
    path = _require_string(args, "path")
    absolute = _resolve_path(cwd, path)
    if not absolute.exists():
        raise FileNotFoundError(_node_missing(absolute))
    if absolute.is_dir():
        raise IsADirectoryError(f"EISDIR: illegal operation on a directory, read")
    if absolute.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        if image_delivery:
            return f"Read image file [{absolute.suffix[1:]}]", {"image_delivery": False}
        return "[Image omitted: image delivery disabled]", {"image_delivery": False}
    text = absolute.read_text(encoding="utf-8")
    offset = args.get("offset")
    limit = args.get("limit")
    start = max(0, int(offset) - 1) if isinstance(offset, (int, float)) else 0
    lines = text.split("\n")
    if start >= len(lines):
        raise ValueError(f"Offset {offset} is beyond end of file ({len(lines)} lines total)")
    selected = "\n".join(lines[start : start + int(limit)]) if isinstance(limit, (int, float)) else "\n".join(lines[start:])
    output, truncated = _truncate_head(selected)
    if truncated:
        shown = len(output.split("\n"))
        end_line = start + shown
        total = len(lines)
        output += f"\n\n[Showing lines {start + 1}-{end_line} of {total}. Use offset={end_line + 1} to continue.]"
    elif isinstance(limit, (int, float)) and start + int(limit) < len(lines):
        remaining = len(lines) - start - int(limit)
        output += f"\n\n[{remaining} more lines in file. Use offset={start + int(limit) + 1} to continue.]"
    return output, {}


def _write_tool(cwd: Path, args: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    path = _require_string(args, "path")
    content = _require_string(args, "content")
    absolute = _resolve_path(cwd, path)
    with _mutation_queue(absolute):
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(content, encoding="utf-8")
    return f"Successfully wrote {_utf16_length(content)} bytes to {path}", {}


def _edit_tool(cwd: Path, args: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    path = _require_string(args, "path")
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("Edit tool input is invalid. edits must contain at least one replacement.")
    absolute = _resolve_path(cwd, path)
    with _mutation_queue(absolute):
        if not absolute.exists():
            raise FileNotFoundError(f"Could not edit file: {path}. Error code: ENOENT.")
        raw = absolute.read_text(encoding="utf-8")
        ending = "\r\n" if "\r\n" in raw else "\n"
        content = raw.replace("\r\n", "\n").replace("\r", "\n")
        normalized: list[tuple[str, str, int, int]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, Mapping) or not isinstance(edit.get("oldText"), str) or not isinstance(edit.get("newText"), str):
                raise ValueError(f"edits[{index}] must contain string oldText and newText")
            old = edit["oldText"].replace("\r\n", "\n").replace("\r", "\n")
            new = edit["newText"].replace("\r\n", "\n").replace("\r", "\n")
            if not old:
                raise ValueError(f"edits[{index}].oldText must not be empty in {path}.")
            occurrences = content.count(old)
            if occurrences == 0:
                if len(edits) == 1:
                    raise ValueError(f"Could not find the exact text in {path}. The old text must match exactly including all whitespace and newlines.")
                raise ValueError(f"Could not find edits[{index}] in {path}. The oldText must match exactly including all whitespace and newlines.")
            if occurrences > 1:
                if len(edits) == 1:
                    raise ValueError(f"Found {occurrences} occurrences of the text in {path}. The text must be unique. Please provide more context to make it unique.")
                raise ValueError(f"Found {occurrences} occurrences of edits[{index}] in {path}. Each oldText must be unique. Please provide more context to make it unique.")
            at = content.index(old)
            normalized.append((old, new, at, at + len(old)))
        for left, right in zip(sorted(normalized, key=lambda item: item[2]), sorted(normalized, key=lambda item: item[2])[1:]):
            if right[2] < left[3]:
                raise ValueError(f"Edits overlap in {path}. Each edit must target a separate region.")
        for old, new, start, end in sorted(normalized, key=lambda item: item[2], reverse=True):
            content = content[:start] + new + content[end:]
        if content == raw.replace("\r\n", "\n").replace("\r", "\n"):
            raise ValueError(f"No changes made to {path}. The replacements produced identical content.")
        if ending == "\r\n":
            content = content.replace("\n", "\r\n")
        absolute.write_text(content, encoding="utf-8", newline="")
    return f"Successfully replaced {len(edits)} block(s) in {path}.", {}


def _bash_tool(cwd: Path, args: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    command = _require_string(args, "command")
    timeout = args.get("timeout")
    timeout_value = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else None
    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout_value)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            text = stdout.decode("utf-8", "replace") if stdout else ""
            raise PiSemanticsError(f"{text}\n\nCommand timed out after {timeout} seconds" if text else f"Command timed out after {timeout} seconds")
    except OSError as exc:
        raise PiSemanticsError(str(exc)) from exc
    text = stdout.decode("utf-8", "replace") if stdout else ""
    text = text.replace("\r", "")
    display, truncated = _truncate_tail(text)
    details: dict[str, Any] = {}
    if truncated:
        temp = tempfile.NamedTemporaryFile(prefix="pi-bash-", suffix=".log", delete=False)
        temp.write(text.encode("utf-8"))
        temp.close()
        details["fullOutputPath"] = temp.name
        lines = text.split("\n")
        end = len(lines)
        start = max(1, end - len(display.split("\n")) + 1)
        if len(lines) > DEFAULT_MAX_LINES:
            display += f"\n\n[Showing lines {start}-{end} of {len(lines)}. Full output: {temp.name}]"
        else:
            display += f"\n\n[Showing lines {start}-{end} ({DEFAULT_MAX_BYTES / 1024:.0f}KB limit). Full output: {temp.name}]"
    if not display:
        display = "(no output)"
    if proc.returncode != 0:
        raise PiSemanticsError(f"{display}\n\nCommand exited with code {proc.returncode}")
    return display, details


def execute_pi_tool(name: str, arguments: Mapping[str, Any] | Any, cwd: str | os.PathLike[str], *, image_delivery: bool = False) -> PiToolResult:
    """Execute one Pi built-in tool with source-compatible result text."""
    if name not in TOOL_NAMES:
        return PiToolResult("", name, f"Tool {name} not found", True)
    try:
        args = _coerce_tool_arguments(name, arguments)
        if name == "read":
            content, details = _read_tool(Path(cwd), args, image_delivery=image_delivery)
        elif name == "write":
            content, details = _write_tool(Path(cwd), args)
        elif name == "edit":
            content, details = _edit_tool(Path(cwd), args)
        else:
            content, details = _bash_tool(Path(cwd), args)
        return PiToolResult("", name, content, False, details)
    except Exception as exc:
        return PiToolResult("", name, str(exc), True)


def _fragment_tool_calls(response: NativeProviderResponse) -> tuple[PiToolCall, ...]:
    calls_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for call in response.tool_calls:
        calls_by_id[call.id] = {"id": call.id, "name": call.name, "arguments": call.arguments}
        order.append(call.id)
    fragment_args: dict[str, list[str]] = {}
    fragment_names: dict[str, str] = {}
    for fragment in response.stream_fragments:
        if fragment.kind != "tool_arguments":
            continue
        call_id = fragment.call_id or f"index:{fragment.index}"
        fragment_args.setdefault(call_id, []).append(fragment.text)
        if fragment.name:
            fragment_names[call_id] = fragment.name
    for call_id, pieces in fragment_args.items():
        if call_id in calls_by_id:
            calls_by_id[call_id]["arguments"] = "".join(pieces)
        else:
            calls_by_id[call_id] = {"id": call_id, "name": fragment_names.get(call_id, ""), "arguments": "".join(pieces)}
            order.append(call_id)
    calls: list[PiToolCall] = []
    for call_id in order:
        raw = calls_by_id[call_id]
        parsed = parse_streaming_json(raw["arguments"])
        calls.append(PiToolCall(raw["id"], raw["name"], _coerce_tool_arguments(raw["name"], parsed)))
    return tuple(calls)


def _content_from_response(response: NativeProviderResponse) -> str:
    if response.content is not None:
        return response.content
    return "".join(fragment.text for fragment in response.stream_fragments if fragment.kind == "content")


def _native_stop_reason(finish_reason: str) -> str:
    return {"tool_calls": "toolUse", "stop": "stop", "length": "length", "content_filter": "error"}.get(finish_reason, finish_reason)


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

    def request(self, stream_fn: Callable[..., NativeProviderResponse], request: Any = None) -> NativeProviderResponse | PiResponseResult:
        """Issue one streamFn attempt; cap checks happen before calling it."""
        self.stream_fn_issued += 1
        if self.request_count >= self.request_cap:
            self.request_records.append(PiRequestRecord(self.stream_fn_issued, False, None))
            return self._cap_response()
        response = stream_fn(request) if request is not None else stream_fn()
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("stream_fn must return NativeProviderResponse")
        self.request_count += 1
        self.request_records.append(PiRequestRecord(self.stream_fn_issued, True, response.request_digest))
        return response

    def consume_response(self, response: NativeProviderResponse) -> PiResponseResult:
        """Consume one admitted native response and dispatch its ordered tool batch."""
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("response must be NativeProviderResponse")
        if self.request_count >= self.request_cap:
            return self._cap_response()
        self.request_count += 1
        self.stream_fn_issued = max(self.stream_fn_issued, self.request_count)
        calls = _fragment_tool_calls(response)
        content = _content_from_response(response)
        blocks: list[dict[str, Any]] = []
        if content:
            blocks.append({"type": "text", "text": content})
        for call in calls:
            blocks.append({"type": "toolCall", "id": call.id, "name": call.name, "arguments": call.arguments})
        assistant = {"role": "assistant", "content": blocks, "stopReason": _native_stop_reason(response.finish_reason)}
        self.messages.append(assistant)
        if not calls:
            self.native_stop_reason = assistant["stopReason"]
            self.exit_status = "Submitted" if assistant["stopReason"] == "stop" else assistant["stopReason"]
            return PiResponseResult(assistant, (), (), assistant["stopReason"], True)
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as executor:
            pending = []
            for call in calls:
                pending.append(executor.submit(execute_pi_tool, call.name, call.arguments, self.cwd, image_delivery=self.image_delivery))
            raw_results = [future.result() for future in pending]
        results: list[PiToolResult] = []
        for call, raw in zip(calls, raw_results):
            result = PiToolResult(call.id, call.name, raw.content, raw.is_error, raw.details, raw.terminate)
            results.append(result)
            self.messages.append(result.as_message())
        quiescent = assistant["stopReason"] not in {"toolUse", "tool_calls"}
        self.native_stop_reason = assistant["stopReason"]
        return PiResponseResult(assistant, calls, tuple(results), assistant["stopReason"], quiescent)

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
                # request() reserves the admitted slot; consume_response owns
                # the actual response count and increments it once.
                self.request_count -= 1
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
                        self.stream_fn_issued += 1
                        results.append(self._cap_response())
                    break
                self.stream_fn_issued += 1
                if self.request_count >= self.request_cap:
                    results.append(self._cap_response())
                    break
                result = self.consume_response(response)
                results.append(result)
                if result.quiescent:
                    break
        self._capture_effects()
        return self.to_trace()

    def _capture_effects(self) -> None:
        # Effects are scoped to the workspace and represented by relative paths.
        for path in self.cwd.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            relative = path.relative_to(self.cwd).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.effects[relative] = {"exists": True, "bytes": path.stat().st_size, "sha256": f"sha256:{digest}"}

    def to_trace(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.e4.pi-replay-trace.v1",
            "role": "replay",
            "profile": "pi",
            "version": "0.73.1",
            "case_id": self.case_id,
            "request_count": self.request_count,
            "stream_fn_issued": self.stream_fn_issued,
            "messages": self.messages,
            "effects": self.effects,
            "termination": {"kind": self.exit_status or "running", "native_stop_reason": self.native_stop_reason},
            "requests": [
                {
                    "attempt": record.attempt,
                    "sent": record.sent,
                    "request_digest": record.request_digest,
                }
                for record in self.request_records
            ],
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
    "prepare_edit_arguments",
    "repair_json",
    "run_episode",
]
