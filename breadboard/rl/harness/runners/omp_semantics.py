"""Source-backed semantic primitives for the Oh My Pi 18.1.17 lane.

The production worker remains the pinned Bun/Rust implementation.  This module
owns the deterministic controller decisions that can be checked without that
worker: hashline identity/provenance, barriers, stop handling, retry policy,
and the Bash timing projection.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import inspect
import re
import json
from difflib import SequenceMatcher
from typing import Any, TypeVar

from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall
from ..omp_native_tools import deny_declared_read_route, deny_excluded_capabilities

# xxHash32 constants from xxhash_rust::xxh32::xxh32 (seed 0 in the supplier).
_P1 = 0x9E3779B1
_P2 = 0x85EBCA77
_P3 = 0xC2B2AE3D
_P4 = 0x27D4EB2F
_P5 = 0x165667B1
ALLOWED_TOOLS = ("read", "bash", "edit", "write")
_MASK = 0xFFFFFFFF

EMPTY_STOP_REMINDER_TEMPLATE = (
    "<system-injection>\n"
    "Stopped without actionable output; task incomplete. Continue with a user-visible final answer or the next required tool call.\n"
    "Attempt #{{retryCount}}/{{maxRetries}}\n"
    "</system-injection>"
)

def _rotl32(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & _MASK


def xxh32(data: bytes, seed: int = 0) -> int:
    """Return xxHash32, matching Bun.hash.xxHash32 and xxhash-rust."""
    length = len(data)
    index = 0
    if length >= 16:
        v1 = (seed + _P1 + _P2) & _MASK
        v2 = (seed + _P2) & _MASK
        v3 = seed & _MASK
        v4 = (seed - _P1) & _MASK

        def round32(acc: int, lane: int) -> int:
            return (_rotl32((acc + lane * _P2) & _MASK, 13) * _P1) & _MASK

        limit = length - 16
        while index <= limit:
            v1 = round32(v1, int.from_bytes(data[index : index + 4], "little"))
            v2 = round32(v2, int.from_bytes(data[index + 4 : index + 8], "little"))
            v3 = round32(v3, int.from_bytes(data[index + 8 : index + 12], "little"))
            v4 = round32(v4, int.from_bytes(data[index + 12 : index + 16], "little"))
            index += 16
        value = (_rotl32(v1, 1) + _rotl32(v2, 7) + _rotl32(v3, 12) + _rotl32(v4, 18)) & _MASK
    else:
        value = (seed + _P5) & _MASK

    value = (value + length) & _MASK
    while index + 4 <= length:
        lane = int.from_bytes(data[index : index + 4], "little")
        value = (_rotl32((value + lane * _P3) & _MASK, 17) * _P4) & _MASK
        index += 4
    while index < length:
        value = (_rotl32((value + data[index] * _P5) & _MASK, 11) * _P1) & _MASK
        index += 1
    value ^= value >> 15
    value = (value * _P2) & _MASK
    value ^= value >> 13
    value = (value * _P3) & _MASK
    value ^= value >> 16
    return value & _MASK


_HASH_TRAILING = re.compile(r"[ \t\r]+(?=\n|$)")


def normalize_hashline_text(text: str) -> str:
    """Normalize only trailing spaces, tabs, and CR before hashing."""
    return _HASH_TRAILING.sub("", text)


def hashline_tag(text: str) -> str:
    """Compute the supplier's four-uppercase-hex xxh32 low-16 tag."""
    return f"{xxh32(normalize_hashline_text(text).encode(), 0) & 0xFFFF:04X}"


# Names used by source/proof material and callers that mirror the TS API.
compute_file_hash = hashline_tag
file_hash = hashline_tag


def normalize_file_text(text: str) -> str:
    """Match native EditStore text identity (BOM-free LF text)."""
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class Snapshot:
    path: str
    text: str
    hash: str
    seen_lines: set[int] | None = None
    recorded_at: int = 0


class SeenAnchorError(ValueError):
    """A hashline anchor was not among the lines actually shown to the model."""

    def __init__(self, path: str, unseen: Sequence[int], revealed: Mapping[int, str], truncated: bool):
        self.path = path
        self.unseen = tuple(unseen)
        self.revealed = dict(revealed)
        self.truncated = truncated
        suffix = " (reveal truncated)" if truncated else ""
        super().__init__(f"Unseen hashline anchors in {path}: {', '.join(map(str, unseen))}{suffix}")


class EditStore:
    """Bounded episode snapshot store matching native retention semantics."""

    def __init__(self, *, max_paths: int = 256, max_versions: int = 4, max_total_units: int = 64 * 1024 * 1024):
        self.max_paths = max_paths
        self.max_versions = max_versions
        self.max_total_units = max_total_units
        self._histories: OrderedDict[str, list[Snapshot]] = OrderedDict()
        self._clock = 0
        self._named_registers: dict[str, list[str]] = {}
        self._noop_counts: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _key(path: str) -> str:
        return str(path)

    @staticmethod
    def _utf16_units(text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    def _touch(self, path: str) -> None:
        if path in self._histories:
            self._histories.move_to_end(path)

    def _evict(self) -> None:
        while len(self._histories) > self.max_paths:
            self._histories.popitem(last=False)
        total = sum(self._utf16_units(s.text) for history in self._histories.values() for s in history)
        while total > self.max_total_units and self._histories:
            _path, history = self._histories.popitem(last=False)
            total -= sum(self._utf16_units(s.text) for s in history)
    def record(self, path: str, text: str, seen_lines: Iterable[int] | None = None) -> str:
        path = self._key(path)
        normalized = normalize_file_text(text)
        tag = hashline_tag(normalized)
        self._clock += 1
        history = self._histories.setdefault(path, [])
        existing = next((item for item in history if item.hash == tag and item.text == normalized), None)
        if existing is not None:
            existing.recorded_at = self._clock
            if seen_lines is not None:
                existing.seen_lines = set(existing.seen_lines or ()) | {int(line) for line in seen_lines}
            history.remove(existing)
            history.insert(0, existing)
        elif self.max_versions > 0:
            snapshot = Snapshot(path, normalized, tag, None, self._clock)
            if seen_lines is not None:
                snapshot.seen_lines = {int(line) for line in seen_lines}
            history.insert(0, snapshot)
            del history[self.max_versions :]
        self._touch(path)
        self._evict()
        return tag

    def record_seen_lines(self, path: str, tag: str, lines: Iterable[int]) -> None:
        snapshot = self.by_hash(path, tag)
        if snapshot is None:
            return
        snapshot.seen_lines = set(snapshot.seen_lines or ()) | {int(line) for line in lines}

    def head(self, path: str) -> Snapshot | None:
        path = self._key(path)
        self._touch(path)
        history = self._histories.get(path)
        return history[0] if history else None

    def by_hash(self, path: str, tag: str) -> Snapshot | None:
        path = self._key(path)
        self._touch(path)
        return next((item for item in self._histories.get(path, ()) if item.hash.upper() == tag.upper()), None)

    def by_content(self, path: str, text: str) -> Snapshot | None:
        normalized = normalize_file_text(text)
        path = self._key(path)
        self._touch(path)
        return next((item for item in self._histories.get(path, ()) if item.text == normalized), None)

    def find_by_hash(self, tag: str) -> list[Snapshot]:
        return [item for history in self._histories.values() for item in history if item.hash.upper() == tag.upper()]

    findByHash = find_by_hash

    def relocate(self, source: str, destination: str) -> None:
        source, destination = self._key(source), self._key(destination)
        history = self._histories.pop(source, None)
        if not history:
            return
        moved = [Snapshot(destination, item.text, item.hash, set(item.seen_lines) if item.seen_lines else None, item.recorded_at) for item in history]
        prior = self._histories.get(destination, [])
        seen: set[str] = set()
        self._histories[destination] = [item for item in (*moved, *prior) if not (item.hash in seen or seen.add(item.hash))][: self.max_versions]
        self._touch(destination)

    def invalidate(self, path: str) -> None:
        self._histories.pop(self._key(path), None)

    def clear(self) -> None:
        self._histories.clear()
        self._named_registers.clear()
        self._noop_counts.clear()

    def record_noop(self, path: str, payload_hash: int) -> tuple[int, bool]:
        previous_hash, count = self._noop_counts.get(path, (None, 0))
        count = count + 1 if previous_hash == payload_hash else 1
        self._noop_counts[path] = (payload_hash, count)
        return count, count >= 3

    @property
    def named_registers(self) -> dict[str, list[str]]:
        return self._named_registers


def seen_anchor_lines(body: str) -> list[int]:
    prefix = re.compile(r"^[ *]?(\d+)(?:-(\d+))?:")
    output: list[int] = []
    for row in body.split("\n"):
        match = prefix.match(row)
        if not match:
            continue
        output.append(int(match.group(1)))
        if match.group(2):
            output.append(int(match.group(2)))
def enforce_seen_lines(
    store: EditStore,
    path: str,
    expected_content: str,
    anchors: Iterable[int],
    *,
    reveal_cap: int = 40,
    reveal_columns: int = 512,
) -> None:
    """Apply native seen-line provenance by retained content, never by tag."""
    snapshot = store.by_content(path, expected_content)
    if snapshot is None or snapshot.seen_lines is None or not snapshot.seen_lines:
        return
    requested = list(dict.fromkeys(int(line) for line in anchors))
    unseen = [line for line in requested if line not in snapshot.seen_lines]
    if not unseen:
        return
    revealed: dict[int, str] = {}
    for line in unseen[:reveal_cap]:
        rows = snapshot.text.split("\n")
        if 1 <= line <= len(rows):
            value = rows[line - 1]
            revealed[line] = value[:reveal_columns] + ("…" if len(value) > reveal_columns else "")
    truncated = len(unseen) > len(revealed) or any(len(snapshot.text.split("\n")[line - 1]) > reveal_columns for line in unseen[: len(revealed)] if 1 <= line <= len(snapshot.text.split("\n")))
    if not truncated:
        snapshot.seen_lines = set(snapshot.seen_lines or ()) | set(revealed)
    raise SeenAnchorError(path, unseen, revealed, truncated)


def uniform_line_displacement(previous: str, current: str) -> int | None:
    """Return a single line displacement when all common rows move uniformly."""
    old, new = previous.split("\n"), current.split("\n")
    matcher = SequenceMatcher(a=old, b=new, autojunk=False)
    offsets: list[int] = []
    for block in matcher.get_matching_blocks():
        if block.size:
            offsets.extend([block.b - block.a] * block.size)
    if not offsets or len(set(offsets)) != 1:
        return None
    return offsets[0]


def recover_uniform_shift(previous: str, current: str, edits: Sequence[Mapping[str, Any]]) -> str | None:
    """Replay line edits after a stale tag only when row displacement is uniform."""
    offset = uniform_line_displacement(previous, current)
    if offset is None:
        return None
    rows = current.split("\n")
    for edit in edits:
        start = int(edit.get("start", edit.get("line", 0))) - 1 + offset
        end = int(edit.get("end", edit.get("line", 0))) + offset
        if start < 0 or end < start or end > len(rows):
            return None
        replacement = str(edit.get("replacement", edit.get("text", ""))).split("\n")
        rows[start:end] = replacement
    return "\n".join(rows)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any = field(default_factory=dict)
    index: int = 0


LENGTH_SKIP_MESSAGE = (
    "Tool call was not executed because the assistant hit its output token limit "
    "(stop_reason: length) before the arguments could complete; the recorded arguments "
    "are truncated and unsafe to run. Do NOT retry by re-emitting the same large payload "
    "— split the work into several smaller tool calls (e.g. for `write`/`edit`, write the "
    "first chunk then append the rest with subsequent `edit` insert ops, or break the "
    "file into multiple `write` targets)"
)


@dataclass(frozen=True)
class ToolResult:
    id: str
    name: str
    output: str = ""
    error: str | None = None
    skipped: bool = False
    invocation: bool = False
    completion_index: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def skipped_result(cls, call: ToolCall, reason: str = "length") -> "ToolResult":
        output = LENGTH_SKIP_MESSAGE if reason == "length" else f"Tool call was not executed because the assistant {reason}"
        return cls(call.id, call.name, output=output, skipped=True, invocation=False, details={"reason": reason})


_SHARED_TOOLS = frozenset({"read", "bash"})
_EXCLUSIVE_TOOLS = frozenset({"edit", "write"})


def tool_concurrency(name: str) -> str:
    if name in _EXCLUSIVE_TOOLS:
        return "exclusive"
    if name in _SHARED_TOOLS:
        return "shared"
    return "exclusive"


T = TypeVar("T")


async def _maybe_await(value: T | Awaitable[T]) -> T:
    return await value if inspect.isawaitable(value) else value


async def schedule_tool_calls(
    calls: Sequence[ToolCall],
    executor: Callable[[ToolCall], T | Awaitable[T]],
    *,
    concurrency: Callable[[ToolCall], str] | None = None,
    prepared_errors: Mapping[str, str] | None = None,
) -> list[T | ToolResult]:
    """Run a batch with native shared/exclusive barriers.

    The returned sequence is completion order, not request order.  Each call is
    still assigned its source slot; preparation failures become results in that
    slot and do not reject the all-settled join.
    """
    last_exclusive: asyncio.Future[Any] | asyncio.Task[Any] = asyncio.ensure_future(asyncio.sleep(0))
    shared: list[asyncio.Future[Any] | asyncio.Task[Any]] = []
    tasks: list[asyncio.Task[Any]] = []

    async def run(call: ToolCall, wait_for: Sequence[Awaitable[Any]]) -> Any:
        await asyncio.gather(*wait_for, return_exceptions=True)
        if prepared_errors and call.id in prepared_errors:
            return ToolResult(call.id, call.name, error=prepared_errors[call.id], details={"phase": "prepare"})
        try:
            return await _maybe_await(executor(call))
        except Exception as exc:  # allSettled: preserve neighboring calls
            return ToolResult(call.id, call.name, error=str(exc), details={"phase": "execute"})

    for call in calls:
        try:
            mode = (concurrency or (lambda item: tool_concurrency(item.name)))(call)
        except Exception:
            mode = "exclusive"
        if mode not in {"shared", "exclusive"}:
            mode = "exclusive"
        waits: list[Awaitable[Any]] = [last_exclusive] if mode == "shared" else [last_exclusive, *shared]
        task = asyncio.create_task(run(call, waits))
        tasks.append(task)
        if mode == "exclusive":
            last_exclusive = task
            shared = []
        else:
            shared.append(task)
    ordered: list[T | ToolResult] = []
    completion_index = 0
    for completed in asyncio.as_completed(tasks):
        value = await completed
        if isinstance(value, ToolResult):
            value = ToolResult(value.id, value.name, value.output, value.error, value.skipped, value.invocation, completion_index, value.details)
        ordered.append(value)
        completion_index += 1
    return ordered


async def run_tool_batch(
    calls: Sequence[ToolCall],
    finish_reason: str,
    executor: Callable[[ToolCall], T | Awaitable[T]],
    *,
    prepared_errors: Mapping[str, str] | None = None,
) -> list[T | ToolResult]:
    """Source stop handling: length calls are synthetic skips, never executed."""
    if finish_reason == "length":
        return [ToolResult.skipped_result(call) for call in calls]
    if finish_reason not in {"stop", "tool_calls", "toolUse"}:
        # Provider stream errors terminate the turn before dispatch. In
        # particular, incomplete toolcall_end frames are never prefix-executed.
        return []
    return await schedule_tool_calls(calls, executor, prepared_errors=prepared_errors)


@dataclass(frozen=True)
class RetryDecision:
    attempt: int
    retry: bool
    reason: str


class NoRetryPolicy:
    """Pinned OMP no-retry overlay; corrective turns are not transport retries."""

    transport_attempts = 1
    empty_stop_retries = 0
    provider_error_retries = 0
    strict_schema_retries = 0
    def __init__(self) -> None:
        self.attempts = 0
        self.decisions: list[RetryDecision] = []

    def begin_attempt(self) -> int:
        self.attempts += 1
        return self.attempts

    def should_retry(self, reason: str, attempt: int | None = None) -> bool:
        current = self.attempts if attempt is None else attempt
        decision = RetryDecision(current, False, reason)
        self.decisions.append(decision)
        return False

    def allow_retry(self, reason: str, attempt: int | None = None) -> bool:
        return self.should_retry(reason, attempt)


@dataclass
class TurnRecovery:
    """Visible empty-stop recovery, bounded to three corrective continuations."""

    max_corrective_continuations: int = 3
    corrective_continuations: int = 0
    active_history: list[Mapping[str, Any]] = field(default_factory=list)
    reminders: list[str] = field(default_factory=list)

    def recover_empty_turn(self, assistant: Mapping[str, Any]) -> bool:
        content = assistant.get("content")
        tool_calls = assistant.get("tool_calls") or assistant.get("toolCalls") or []
        stop = assistant.get("finish_reason", assistant.get("stop_reason", "stop"))
        empty = stop in {"stop", "toolUse", "tool_use"} and not tool_calls and not str(content or "").strip()
        if not empty:
            self.corrective_continuations = 0
            self.active_history.append(dict(assistant))
            return False
        if self.active_history and self.active_history[-1].get("role") == "assistant" and not self.active_history[-1].get("content"):
            self.active_history.pop()
        if self.corrective_continuations >= self.max_corrective_continuations:
            return False
        self.corrective_continuations += 1
        reminder = (
            EMPTY_STOP_REMINDER_TEMPLATE
            .replace("{{retryCount}}", str(self.corrective_continuations))
            .replace("{{maxRetries}}", str(self.max_corrective_continuations))
        )
        self.reminders.append(reminder)
        self.active_history.append({"role": "developer", "content": reminder, "synthetic": True})
        return True

    def accept_turn(self, assistant: Mapping[str, Any]) -> None:
        self.corrective_continuations = 0
        self.active_history.append(dict(assistant))

    @property
    def exhausted(self) -> bool:
        return self.corrective_continuations >= self.max_corrective_continuations


def format_wall_time_notice(wall_time_ms: float) -> str:
    """Exact source-owned Bash text slot."""
    return f"Wall time: {wall_time_ms / 1000:.2f} seconds"


def append_wall_time_notice(output: str, wall_time_ms: float) -> str:
    notice = format_wall_time_notice(wall_time_ms)
    return f"{output or '(no output)'}\n\n{notice}"


def _thaw_native_wire(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_native_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_native_wire(item) for item in value]
    return value


PHASE_SCHEMA_VERSION = "bb.omp-native.v1"
CONSUMER_ID = "breadboard.oh-my-pi.v18.1.17"
OMP_REQUEST_CAP = 8


class OMPPhaseError(RuntimeError):
    """A profile phase was requested out of order or with invalid state."""


@dataclass(frozen=True, slots=True)
class OMPPreparedCall:
    id: str
    name: str
    arguments: Any
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OMPResponseResult:
    assistant: dict[str, Any]
    calls: tuple[ToolCall, ...]
    stop_reason: str
    quiescent: bool
    dispatch_calls: tuple[ToolCall, ...] | None = None
    synthetic_results: tuple[Mapping[str, Any], ...] = ()


class OMPSemanticsState:
    """State protocol consumed by the shared native-stream Conductor loop."""

    def __init__(
        self,
        *,
        task: str = "",
        system_prompt: str = "",
        tool_schemas: Sequence[Mapping[str, Any]] = (),
        request_cap: int = OMP_REQUEST_CAP,
        worker: Any = None,
        case_id: str | None = None,
        capability_denials: Mapping[str, Mapping[str, Any]] | None = None,
        cwd: str | None = None,
    ) -> None:
        if request_cap <= 0:
            raise ValueError("request_cap must be positive")
        self.task = task
        self.system_prompt = system_prompt
        self.tool_schemas = tuple(dict(schema) for schema in tool_schemas)
        self.request_cap = request_cap
        self.worker = worker
        self.cwd = cwd
        self.case_id = case_id
        self.capability_denials = {
            str(key): dict(value)
            for key, value in (capability_denials or {}).items()
            if isinstance(value, Mapping)
        }
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        self.request_count = 0
        self.stream_fn_issued = 0
        self.exit_status: str | None = None
        self.native_stop_reason: str | None = None
        self.native_responses: list[dict[str, Any]] = []
        self.recovery = TurnRecovery()
        self._pending_finish_reason: str | None = None
        self._closed = False
        self.effects: dict[str, Any] = {}

    @property
    def is_exited(self) -> bool:
        return self.exit_status is not None

    def begin_query(self) -> dict[str, Any] | None:
        if self._closed:
            raise OMPPhaseError("query after close")
        if self.stream_fn_issued > self.request_count:
            raise OMPPhaseError("provider query is already pending")
        self.stream_fn_issued += 1
        if self.request_count >= self.request_cap:
            self.exit_status = "RequestLimitExceeded"
            self.native_stop_reason = "error"
            refusal = {"role": "assistant", "content": "", "stopReason": "error", "isError": True}
            self.messages.append(refusal)
            return refusal
        return None

    def project_request(self) -> dict[str, Any]:
        messages = [{"role": "system", "content": self.system_prompt}, *self.messages]
        return {"kind": "request", "messages": messages, "tools": [dict(schema) for schema in self.tool_schemas]}

    def _capability_denial(self, call: ToolCall) -> str | None:
        arguments = call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                return None
        if not isinstance(arguments, Mapping):
            return None
        try:
            deny_excluded_capabilities(
                arguments,
                denial_policy=self.capability_denials,
            )
            deny_declared_read_route(
                call.name,
                arguments,
                denial_policy=self.capability_denials,
                cwd=self.cwd,
            )
        except PermissionError as exc:
            return str(exc)
        return None

    def prepare_response(self, response: NativeProviderResponse) -> OMPResponseResult:
        if not isinstance(response, NativeProviderResponse):
            raise TypeError("response must be NativeProviderResponse")
        if self.stream_fn_issued <= self.request_count:
            raise OMPPhaseError("response has no admitted provider query")
        self.request_count += 1
        if response.raw_response is None:
            self.native_responses.append({"finish_reason": response.finish_reason})
        else:
            if not isinstance(response.raw_response, Mapping):
                raise OMPPhaseError("raw native response must be an object")
            raw_reasons: list[str] = []
            choices = response.raw_response.get("choices")
            if isinstance(choices, (list, tuple)):
                raw_reasons.extend(
                    choice["finish_reason"]
                    for choice in choices
                    if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str)
                )
            if isinstance(response.raw_response.get("finish_reason"), str):
                raw_reasons.append(response.raw_response["finish_reason"])
            if not raw_reasons or any(reason != response.finish_reason for reason in raw_reasons):
                raise OMPPhaseError("raw native response finish_reason disagrees with decoded response")
            self.native_responses.append(_thaw_native_wire(response.raw_response))
        finish_reason = response.finish_reason
        self.native_stop_reason = finish_reason
        calls: tuple[ToolCall, ...] = ()
        if finish_reason not in {"error", "aborted"}:
            calls = tuple(
                ToolCall(call.id, call.name, call.arguments, index)
                for index, call in enumerate(response.tool_calls)
            )
        blocks: list[dict[str, Any]] = []
        if response.content:
            blocks.append({"type": "text", "text": response.content})
        for call in calls:
            blocks.append({
                "type": "toolCall",
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            })
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": blocks,
            "stopReason": finish_reason,
        }
        self.messages.append(assistant)
        self._pending_finish_reason = finish_reason
        if not calls:
            empty_stop = finish_reason == "stop" and not response.content
            if empty_stop and self.recovery.recover_empty_turn({"content": "", "finish_reason": "stop"}):
                self.messages.pop()
                self.messages.append({
                    "role": "developer",
                    "content": self.recovery.reminders[-1],
                    "synthetic": True,
                })
                self.exit_status = None
                return OMPResponseResult(assistant, (), finish_reason, False, dispatch_calls=(), synthetic_results=())
            self.recovery.accept_turn(assistant)
            self.exit_status = "Submitted" if finish_reason == "stop" else finish_reason
            return OMPResponseResult(
                assistant,
                (),
                finish_reason,
                True,
                dispatch_calls=(),
                synthetic_results=(),
            )
        self.recovery.accept_turn(assistant)
        if finish_reason == "length":
            synthetic_results = tuple(
                {
                    "id": call.id,
                    "name": call.name,
                    "content": LENGTH_SKIP_MESSAGE,
                    "details": {"reason": "length"},
                    "isError": False,
                    "terminate": False,
                    "completion_index": index,
                }
                for index, call in enumerate(calls)
            )
            return OMPResponseResult(
                assistant,
                calls,
                finish_reason,
                False,
                dispatch_calls=(),
                synthetic_results=synthetic_results,
            )
        dispatch_calls: list[ToolCall] = []
        synthetic_results: list[Mapping[str, Any]] = []
        for index, call in enumerate(calls):
            denial = self._capability_denial(call)
            if denial is None:
                dispatch_calls.append(call)
                continue
            synthetic_results.append({
                "id": call.id,
                "name": call.name,
                "content": denial,
                "details": {"phase": "prepare", "reason": "capability_denied"},
                "isError": True,
                "terminate": False,
                "completion_index": index,
            })
        return OMPResponseResult(
            assistant,
            calls,
            finish_reason,
            False,
            dispatch_calls=tuple(dispatch_calls),
            synthetic_results=tuple(synthetic_results),
        )

    def prepare_tools(self, calls: Sequence[ToolCall]) -> dict[str, Any]:
        prepared: list[OMPPreparedCall] = []
        for call in calls:
            arguments = call.arguments
            error: str | None = None
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    error = "Invalid tool arguments: expected a JSON object"
            if not isinstance(arguments, Mapping):
                error = error or "Invalid tool arguments: expected a JSON object"
                arguments = {}
            if error is None:
                try:
                    deny_excluded_capabilities(
                        arguments,
                        denial_policy=self.capability_denials,
                    )
                    deny_declared_read_route(
                        call.name,
                        arguments,
                        denial_policy=self.capability_denials,
                        cwd=self.cwd,
                    )
                except PermissionError as exc:
                    error = str(exc)
            if call.name not in ALLOWED_TOOLS:
                error = f"OMP tool is not admitted: {call.name}"
            prepared.append(OMPPreparedCall(call.id, call.name, dict(arguments), error))
        self._pending_calls = tuple(prepared)
        return {
            "kind": "prepared",
            "calls": [{"id": item.id, "name": item.name, "arguments": item.arguments, **({"error": item.error} if item.error else {})} for item in prepared],
            "history_calls": [{"id": item.id, "name": item.name, "arguments": item.arguments} for item in prepared],
        }

    def execute_batch(self) -> dict[str, Any]:
        if self._pending_finish_reason == "length":
            results: list[Any] = [ToolResult.skipped_result(ToolCall(item.id, item.name, item.arguments)) for item in self._pending_calls]
        else:
            valid = [item for item in self._pending_calls if item.error is None]
            if not valid:
                raw_results = []
            elif self.worker is None:
                raw_results = [ToolResult(item.id, item.name, error="native worker unavailable") for item in valid]
            else:
                raw_results = self.worker.execute_batch([{"id": item.id, "name": item.name, "arguments": item.arguments} for item in valid])
            by_id = {
                item.id if isinstance(item, ToolResult) else str(item.get("id", "")): item
                for item in raw_results
            }
            results = [
                ToolResult(
                    item.id,
                    item.name,
                    output=item.error or "",
                    error=item.error,
                    details={"phase": "prepare"},
                )
                if item.error
                else by_id.get(item.id, ToolResult(item.id, item.name, error="native result missing"))
                for item in self._pending_calls
            ]
        projected = []
        for index, result in enumerate(results):
            if isinstance(result, ToolResult):
                projected.append({
                    "id": result.id,
                    "completion_index": result.completion_index if result.completion_index is not None else index,
                    "content": result.output,
                    "details": dict(result.details),
                    "isError": result.error is not None,
                    "terminate": False,
                })
            else:
                projected.append({"id": result.get("id", ""), "completion_index": result.get("completion_index", index), **dict(result)})

        return {"kind": "tool_results", "results": projected}
    def commit_tool_results(
        self,
        calls: Sequence[ToolCall],
        results: Sequence[Mapping[str, Any] | ToolResult],
    ) -> None:
        if len(calls) != len(results):
            raise OMPPhaseError("tool result count does not match call count")
        indexed = list(enumerate(results))
        indexed.sort(
            key=lambda item: (
                item[1].get("completion_index", item[0])
                if isinstance(item[1], Mapping)
                else (
                    item[1].completion_index
                    if item[1].completion_index is not None
                    else item[0]
                )
            )
        )
        for source_index, result in indexed:
            call = calls[source_index]
            if isinstance(result, ToolResult):
                content: Any = [{"type": "text", "text": result.output}]
                is_error = result.error is not None
                tool_id, tool_name = result.id, result.name
            else:
                raw = dict(result)
                native_content = raw.get("content", "")
                content = (
                    native_content
                    if isinstance(native_content, list)
                    else [{"type": "text", "text": str(native_content)}]
                )
                is_error = bool(raw.get("isError", raw.get("is_error", False)))
                tool_id, tool_name = call.id, call.name
            self.messages.append({
                "role": "toolResult",
                "toolCallId": tool_id,
                "toolName": tool_name,
                "content": content,
                "isError": is_error,
            })
        self._pending_calls = ()
        self._pending_finish_reason = None

    def to_trace(
        self,
        *,
        requests: Sequence[Mapping[str, Any]],
        runtime_inputs: Mapping[str, str],
        effects: Mapping[str, Any],
    ) -> dict[str, Any]:
        termination = {
            "kind": self.exit_status or "running",
            "native_stop_reason": self.native_stop_reason,
        }
        request_bodies = [dict(body) for body in requests]
        return {
            "schema_version": "bb.e4.omp-replay-trace.v1",
            "profile": "omp",
            "consumer_id": CONSUMER_ID,
            "case_id": self.case_id,
            "request_count": len(request_bodies),
            "requests": request_bodies,
            "runtime_inputs": dict(runtime_inputs),
            "effects": dict(effects),
            "native_responses": [dict(response) for response in self.native_responses],
            "exit": termination,
        }
__all__ = [
    "ALLOWED_TOOLS",
    "CONSUMER_ID",
    "EMPTY_STOP_REMINDER_TEMPLATE",
    "OMPSemanticsState",
    "OMPPhaseError",
    "OMPPreparedCall",
    "OMPResponseResult",
    "PHASE_SCHEMA_VERSION",
    "EditStore",
    "NativeProviderResponse",
    "NoRetryPolicy",
    "SeenAnchorError",
    "Snapshot",
    "ToolCall",
    "ToolResult",
    "TurnRecovery",
    "append_wall_time_notice",
    "compute_file_hash",
    "enforce_seen_lines",
    "file_hash",
    "format_wall_time_notice",
    "hashline_tag",
    "normalize_file_text",
    "normalize_hashline_text",
    "recover_uniform_shift",
    "run_tool_batch",
    "schedule_tool_calls",
    "seen_anchor_lines",
    "tool_concurrency",
    "uniform_line_displacement",
    "xxh32",
]
