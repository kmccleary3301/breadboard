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
from difflib import SequenceMatcher
from typing import Any, TypeVar

# xxHash32 constants from xxhash_rust::xxh32::xxh32 (seed 0 in the supplier).
_P1 = 0x9E3779B1
_P2 = 0x85EBCA77
_P3 = 0xC2B2AE3D
_P4 = 0x27D4EB2F
_P5 = 0x165667B1
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
    expected_tag: str,
    anchors: Iterable[int],
    *,
    reveal_cap: int = 40,
    reveal_columns: int = 512,
) -> None:
    """Apply native seen-line behavior; absent/empty provenance is a bypass."""
    snapshot = store.by_content(path, expected_tag) or store.by_hash(path, expected_tag)
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
        store.record_seen_lines(path, expected_tag, revealed)
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


@dataclass(frozen=True)
class NativeProviderResponse:
    """Shared provider seam with PiComplete; transport stays outside runners."""

    binding_digest: str
    request_digest: str
    response_id: str | None
    content: str
    finish_reason: str
    stream_fragments: tuple[Mapping[str, Any], ...] = ()

__all__ = [
    "EMPTY_STOP_REMINDER_TEMPLATE",
    "EditStore",
    "NativeProviderResponse",
    "NoRetryPolicy",
    "RetryDecision",
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
