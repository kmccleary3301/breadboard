"""Source-derived OpenClaw 2026.9.4 native tool and lifecycle helpers.

This module intentionally owns only the tool boundary.  The model loop, provider
transport, history commit, recovery admission, and terminal arbitration stay in
BreadBoard's conductor.  The constants below are grounded in the pinned source
archive (commit 3a9d69db306cd7f081e06254cb89c4bcc14a7107).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

OPENCLAW_SOURCE_COMMIT = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
OPENCLAW_VERSION = "2026.9.4"
TOOL_ORDER = ("ls", "read", "edit", "write", "exec", "process")
PROMPT_TOOL_ORDER = ("read", "write", "edit", "ls", "exec", "process")
BOOTSTRAP_ORDER = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "BOOTSTRAP.md", "MEMORY.md")
BOOTSTRAP_MAX_CHARS = 20_000
BOOTSTRAP_TOTAL_MAX_CHARS = 60_000
USER_BOOTSTRAP_MAX_CHARS = 4_000
MIN_BOOTSTRAP_FILE_BUDGET_CHARS = 64
EXEC_YIELD_MS = 10_000
EXEC_DEFAULT_TIMEOUT_SECONDS = 30
EXEC_MAX_TIMEOUT_SECONDS = 30
PROCESS_MAX_POLL_MS = 30_000
MAX_LIVE_PROCESSES = 4

# Source citations are kept machine-readable so target manifests and tests can
# report exactly which source path grounds a behavior.
SOURCE_CITATIONS: Mapping[str, str] = {
    "tools": "src/agents/core-coding-tools.ts:createCoreCodingTools",
    "title": "src/agents/schema/typebox.ts:executionTitleSchema",
    "edit_preparation": "src/agents/sessions/tools/edit.ts:prepareEditArguments",
    "bootstrap_order": "src/agents/workspace.ts:WORKSPACE_BOOTSTRAP_FILENAMES",
    "bootstrap_budgets": "src/agents/embedded-agent-helpers/bootstrap.ts:buildBootstrapContextFiles",
    "exec_yield": "src/agents/bash-tools.exec-run.ts:createExecTool",
    "process_poll": "src/agents/bash-tools.process.ts:resolvePollWaitMs",
    "process_ack": "src/agents/bash-tools.process.ts:finishedPollResult",
    "cleanup": "src/commands/agent-exec.ts:agentExecCommand",
    "stream_finalization": "packages/agent-core/src/agent-stream-response.ts:finalizeAssistantMessage",
    "recovery": "src/agents/sessions/agent-session-execution.ts:prepareRetry",
}

_XML_ARG_VALUE_SUFFIX = re.compile(r"</arg_value>>+$")


def _normalize_path(value: str) -> str:
    """Apply the source's harmless XML suffix repair without changing payloads."""
    return _XML_ARG_VALUE_SUFFIX.sub("", value) if "</arg_value>" in value else value


def execution_title_schema() -> dict[str, Any]:
    """Return the optional source execution title schema (maxLength 120)."""
    return {
        "type": "string",
        "maxLength": 120,
        "description": "Every call: short purpose; never claim success. No secrets.",
    }


def _object(properties: Mapping[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": True,
    }


TOOL_SCHEMAS: Mapping[str, dict[str, Any]] = {
    "ls": _object(
        {
            "title": execution_title_schema(),
            "path": {"type": "string", "description": "Directory path."},
        }
    ),
    "read": _object(
        {
            "title": execution_title_schema(),
            "path": {"type": "string", "description": "File path."},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1},
            "cursor": {"type": "integer", "minimum": 0},
            "optional": {"type": "boolean"},
        },
        ("path",),
    ),
    "edit": _object(
        {
            "title": execution_title_schema(),
            "path": {"type": "string", "description": "File path."},
            "edits": {
                "type": "array",
                "items": _object(
                    {
                        "oldText": {"type": "string"},
                        "newText": {"type": "string"},
                    },
                    ("oldText", "newText"),
                ),
            },
            "oldText": {"type": "string"},
            "newText": {"type": "string"},
        },
        ("path", "edits"),
    ),
    "write": _object(
        {
            "title": execution_title_schema(),
            "path": {"type": "string", "description": "File path."},
            "content": {"type": "string"},
        },
        ("path", "content"),
    ),
    "exec": _object(
        {
            "title": execution_title_schema(),
            "command": {"type": "string", "description": "Shell command."},
            "workdir": {"type": "string"},
            "env": {"type": "object"},
            "yieldMs": {"type": "number", "minimum": 0},
            "background": {"type": "boolean"},
            "timeoutSeconds": {"type": "number", "minimum": 0, "maximum": 30},
            "pty": {"type": "boolean"},
            "host": {"type": "string", "enum": ["auto", "gateway"]},
        },
        ("command",),
    ),
    "process": _object(
        {
            "title": execution_title_schema(),
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "poll",
                    "log",
                    "write",
                    "send-keys",
                    "submit",
                    "paste",
                    "kill",
                    "clear",
                    "remove",
                ],
            },
            "sessionId": {"type": "string"},
            "data": {"type": "string"},
            "keys": {"type": "array", "items": {"type": "string"}},
            "hex": {"type": "array", "items": {"type": "string"}},
            "literal": {"type": "string"},
            "text": {"type": "string"},
            "bracketed": {"type": "boolean"},
            "eof": {"type": "boolean"},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 0},
            "timeout": {"type": "number", "minimum": 0, "maximum": 30_000},
        },
        ("action",),
    ),
}


def _copy_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def prepare_edit_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Prepare edit arguments exactly at the native edit boundary.

    ``edits`` may be a JSON string, and legacy ``oldText``/``newText`` are
    folded into the array.  Unknown metadata is intentionally discarded from
    the executable argument object while callers retain ``raw_arguments``.
    """
    args = _copy_mapping(arguments)
    edits = args.get("edits")
    if isinstance(edits, str):
        try:
            parsed = json.loads(edits)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            edits = parsed
    if isinstance(args.get("oldText"), str) and isinstance(args.get("newText"), str):
        edits = list(edits) if isinstance(edits, list) else []
        edits.append({"oldText": args["oldText"], "newText": args["newText"]})
    if isinstance(edits, list):
        normalized: list[Any] = []
        for item in edits:
            if isinstance(item, Mapping) and not isinstance(item, list):
                normalized.append({"oldText": item.get("oldText"), "newText": item.get("newText")})
            else:
                normalized.append(item)
        edits = normalized
    return {"path": args.get("path"), "edits": edits}


def prepare_tool_call(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply source argument preparation before schema validation/effects."""
    if name not in TOOL_SCHEMAS:
        raise ValueError(f"Unknown OpenClaw tool {name!r}")
    args = _copy_mapping(arguments)
    # Optional titles are provider-facing metadata, not tool implementation args.
    args.pop("title", None)
    if name == "edit":
        args = prepare_edit_arguments(args)
    if "path" in args and isinstance(args["path"], str):
        args["path"] = _normalize_path(args["path"])
    if name == "exec":
        if "timeout" in args:
            raise ValueError('exec parameter "timeout" is unsupported; use "timeoutSeconds" instead')
        if "yieldMs" in args and args["yieldMs"] is not None:
            args["yieldMs"] = max(10, min(120_000, int(float(args["yieldMs"]))))
        if "timeoutSeconds" in args and args["timeoutSeconds"] is not None:
            timeout = float(args["timeoutSeconds"])
            if timeout < 0 or timeout > EXEC_MAX_TIMEOUT_SECONDS:
                raise ValueError("timeoutSeconds must be 0 or between 0 and 30 seconds")
            args["timeoutSeconds"] = int(timeout) if timeout.is_integer() else timeout
    if name == "process" and "timeout" in args and args["timeout"] is not None:
        args["timeout"] = max(0, min(PROCESS_MAX_POLL_MS, int(float(args["timeout"]))))
    return args


@dataclass(frozen=True)
class PreparedToolCall:
    name: str
    tool_call_id: str
    raw_arguments: Mapping[str, Any]
    arguments: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool_call_id": self.tool_call_id,
            "raw_arguments": dict(self.raw_arguments),
            "arguments": dict(self.arguments),
        }


def prepare_tool_calls(tool_calls: Sequence[Mapping[str, Any]]) -> tuple[PreparedToolCall, ...]:
    """Prepare a whole batch without executing any call."""
    prepared: list[PreparedToolCall] = []
    for index, call in enumerate(tool_calls):
        name = call.get("name") or call.get("function", {}).get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"tool call {index} has no tool name")
        raw = call.get("arguments", call.get("function", {}).get("arguments", {}))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"invalid {name} arguments: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ValueError(f"invalid {name} arguments: expected object")
        prepared.append(
            PreparedToolCall(
                name=name,
                tool_call_id=str(call.get("id", f"call_{index}")),
                raw_arguments=dict(raw),
                arguments=prepare_tool_call(name, raw),
            )
        )
    return tuple(prepared)


@dataclass(frozen=True)
class BootstrapFile:
    name: str
    path: str
    content: str | None
    missing: bool
    error: str | None = None

    @property
    def context(self) -> str:
        if self.missing:
            return f"[MISSING] Expected at: {self.path}"
        if self.error:
            return f"[UNREADABLE: {self.error}]"
        return self.content or ""


def materialize_baseline_bootstrap(workspace: str | Path) -> tuple[Path, Path]:
    """Write the two selected UTF-8, one-LF baseline fixture inputs."""
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    agents = root / "AGENTS.md"
    soul = root / "SOUL.md"
    agents.write_text(
        "Use python -m unittest -q for tests. Keep changes focused. Do not read secrets.\n",
        encoding="utf-8",
        newline="",
    )
    soul.write_text("Be direct, helpful, and concise.\n", encoding="utf-8", newline="")
    return agents, soul


def load_bootstrap_files(workspace: str | Path) -> tuple[BootstrapFile, ...]:
    """Load files in OpenClaw's canonical order with native omission markers."""
    root = Path(workspace).resolve()
    result: list[BootstrapFile] = []
    for name in BOOTSTRAP_ORDER:
        path = root / name
        # Source omits absent USER/MEMORY entries; other canonical entries carry
        # a missing record that becomes a model-visible marker.
        if not path.exists() and name in {"USER.md", "MEMORY.md"}:
            continue
        if not path.exists():
            result.append(BootstrapFile(name, str(path), None, True))
            continue
        try:
            result.append(BootstrapFile(name, str(path), path.read_text(encoding="utf-8"), False))
        except OSError as exc:
            result.append(BootstrapFile(name, str(path), None, False, str(exc)))
    return tuple(result)


def _truncate_context(content: str, name: str, budget: int) -> str:
    trimmed = content.rstrip()
    if len(trimmed) <= budget:
        return trimmed
    if budget <= 1:
        return trimmed[:budget]
    marker = f"\n[...truncated, read {name} for full content...]\n"
    if len(marker) >= budget:
        return trimmed[:budget]
    body_budget = budget - len(marker)
    head = max(1, int(body_budget * 0.75))
    tail = max(0, body_budget - head)
    return f"{trimmed[:head]}{marker}{trimmed[-tail:] if tail else ''}"[:budget]


def build_bootstrap_context(
    files: Sequence[BootstrapFile],
    *,
    per_file_budget: int = BOOTSTRAP_MAX_CHARS,
    total_budget: int = BOOTSTRAP_TOTAL_MAX_CHARS,
) -> tuple[dict[str, str], ...]:
    """Build bounded path/content entries, preserving order and markers."""
    remaining = max(1, int(total_budget))
    result: list[dict[str, str]] = []
    for file in files:
        if remaining <= 0:
            break
        if file.missing or file.error:
            text = file.context[:remaining]
        else:
            budget = min(int(per_file_budget), remaining)
            if file.name == "USER.md":
                budget = min(USER_BOOTSTRAP_MAX_CHARS, budget)
            if remaining < MIN_BOOTSTRAP_FILE_BUDGET_CHARS and file.name not in {"USER.md"}:
                break
            text = _truncate_context(file.content or "", file.name, budget)
        if not text:
            continue
        result.append({"path": file.path, "content": text})
        remaining -= len(text)
    return tuple(result)


@dataclass
class ManagedProcess:
    session_id: str
    process: subprocess.Popen[bytes]
    command: str
    cwd: str
    started_at: float
    backgrounded: bool = True
    output: bytearray = field(default_factory=bytearray)
    pending_output: bytearray = field(default_factory=bytearray)
    ended_at: float | None = None
    exit_code: int | None = None
    removed: bool = False

    @property
    def exited(self) -> bool:
        return self.process.poll() is not None

    def refresh(self) -> None:
        if self.process.stdout is not None:
            # communicate() closes stdout on the foreground path; do not read
            # that closed handle again while recording process settlement.
            try:
                closed = self.process.stdout.closed
            except ValueError:
                closed = True
            if self.exited and not closed:
                data = self.process.stdout.read() or b""
                if data:
                    self.output.extend(data)
                    self.pending_output.extend(data)
        code = self.process.poll()
        if code is not None and self.ended_at is None:
            self.exit_code = code
            self.ended_at = time.time()


@dataclass(frozen=True)
class PendingDelivery:
    session_id: str
    text: str
    details: Mapping[str, Any]
    _ack: Callable[[], None] = field(repr=False, compare=False)

    def acknowledge(self) -> None:
        self._ack()


class ProcessScope:
    """Owns all processes for one disposable agent-exec episode."""

    def __init__(self, scope_key: str | None = None, max_live: int = MAX_LIVE_PROCESSES):
        self.scope_key = scope_key or f"agent-exec:{uuid.uuid4()}"
        self.max_live = max_live
        self._records: MutableMapping[str, ManagedProcess] = {}
        self._finished: MutableMapping[str, ManagedProcess] = {}

    def _live_count(self) -> int:
        return sum(not record.exited for record in self._records.values() if not record.removed)

    def register(self, record: ManagedProcess) -> None:
        if self._live_count() >= self.max_live:
            raise RuntimeError(f"live exec process limit exceeded ({self.max_live})")
        self._records[record.session_id] = record

    def get(self, session_id: str) -> ManagedProcess | None:
        record = self._records.get(session_id) or self._finished.get(session_id)
        if record is None or record.removed:
            return None
        return record

    def reap(self) -> None:
        for record in tuple(self._records.values()):
            record.refresh()
            if record.exited:
                self._finished[record.session_id] = record

    def cleanup(self) -> None:
        """Required-all process-tree cleanup; absence is independently checked."""
        self.reap()
        errors: list[str] = []
        for record in tuple(self._records.values()):
            if record.exited:
                continue
            try:
                os.killpg(record.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as exc:
                errors.append(f"{record.session_id}: {exc}")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.reap()
            if all(record.exited for record in self._records.values()):
                break
            time.sleep(0.01)
        for record in tuple(self._records.values()):
            if record.exited:
                continue
            try:
                os.killpg(record.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                errors.append(f"{record.session_id}: {exc}")
        self.reap()
        survivors = [record.session_id for record in self._records.values() if not record.exited]
        if survivors:
            errors.append(f"processes still live after required-all cleanup: {survivors}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def __enter__(self) -> "ProcessScope":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.cleanup()


class OpenClawNativeTools:
    """Contained native tool implementation used by replay and focused tests."""

    def __init__(self, workspace: str | Path, *, scope: ProcessScope | None = None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.scope = scope or ProcessScope()
        self._pending: dict[str, PendingDelivery] = {}

    def _path(self, value: str) -> Path:
        raw = _normalize_path(value)
        candidate = Path(raw[1:] if raw.startswith("@") else raw)
        resolved = (self.workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {value}") from exc
        return resolved

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = prepare_tool_call(name, arguments)
        if name == "ls":
            path = self._path(str(args.get("path") or "."))
            entries = sorted(path.iterdir(), key=lambda item: item.name)
            return {"status": "completed", "entries": [item.name + ("/" if item.is_dir() else "") for item in entries]}
        if name == "read":
            path = self._path(str(args["path"]))
            if path.is_dir():
                raise IsADirectoryError(f"Read requires a file: {path}")
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            offset = int(args.get("offset", 1) or 1)
            limit = args.get("limit")
            lines = text.splitlines(keepends=True)
            selected = lines[max(0, offset - 1) : (max(0, offset - 1) + int(limit) if limit is not None else None)]
            return {"status": "completed", "path": str(args["path"]), "content": "".join(selected)}
        if name == "write":
            path = self._path(str(args["path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(args.get("content", "")), encoding="utf-8", newline="")
            return {"status": "completed", "changed": True, "path": str(args["path"])}
        if name == "edit":
            return self._edit(args)
        if name == "exec":
            return self._exec(args)
        if name == "process":
            return self._process(args)
        raise ValueError(f"Unknown OpenClaw tool {name!r}")

    def _edit(self, args: Mapping[str, Any]) -> dict[str, Any]:
        path = self._path(str(args.get("path") or ""))
        edits = args.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError("Edit tool input is invalid. edits must contain at least one replacement.")
        original = path.read_text(encoding="utf-8")
        spans: list[tuple[int, int, str]] = []
        for edit in edits:
            if not isinstance(edit, Mapping) or not isinstance(edit.get("oldText"), str) or not isinstance(edit.get("newText"), str):
                raise ValueError("Edit replacement must contain oldText and newText strings")
            old = edit["oldText"]
            matches = [m.start() for m in re.finditer(re.escape(old), original)]
            if len(matches) != 1:
                raise ValueError(f"Could not find the exact text in {args.get('path')}: expected one match")
            start = matches[0]
            spans.append((start, start + len(old), edit["newText"]))
        spans.sort()
        if any(right > next_left for (_, right, _), (next_left, _, _) in zip(spans, spans[1:])):
            raise ValueError("Edit replacements must be non-overlapping")
        output = original
        for start, end, new in reversed(spans):
            output = output[:start] + new + output[end:]
        if output == original:
            return {"status": "completed", "changed": False, "path": str(args.get("path"))}
        path.write_text(output, encoding="utf-8", newline="")
        return {"status": "completed", "changed": True, "path": str(args.get("path"))}

    def _exec(self, args: Mapping[str, Any]) -> dict[str, Any]:
        command = str(args.get("command") or "")
        if not command:
            raise ValueError("Provide a command to start.")
        cwd = self._path(str(args.get("workdir") or "."))
        env = os.environ.copy()
        requested_env = args.get("env")
        if isinstance(requested_env, Mapping):
            env.update({str(key): str(value) for key, value in requested_env.items()})
        process = subprocess.Popen(
            ["/bin/sh", "-lc", command],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        session = ManagedProcess(str(uuid.uuid4()), process, command, str(cwd), time.time())
        self.scope.register(session)
        yield_ms = 0 if args.get("background") is True else int(args.get("yieldMs", EXEC_YIELD_MS))
        timeout = args.get("timeoutSeconds", EXEC_DEFAULT_TIMEOUT_SECONDS)
        timeout_seconds = None if timeout == 0 else min(float(timeout), EXEC_MAX_TIMEOUT_SECONDS)
        if args.get("background") is True:
            # Explicit background wins immediately; settlement is observed by
            # process poll and retained finished-session state.
            return {"status": "running", "sessionId": session.session_id, "pid": process.pid}
        try:
            if yield_ms > 0:
                try:
                    output, _ = process.communicate(timeout=yield_ms / 1000)
                    session.output.extend(output or b"")
                    session.pending_output.extend(output or b"")
                    session.refresh()
                    return {"status": "completed", "sessionId": session.session_id, "output": (output or b"").decode(errors="replace"), "exitCode": process.returncode}
                except subprocess.TimeoutExpired:
                    return {"status": "running", "sessionId": session.session_id, "pid": process.pid}
            if timeout_seconds is None:
                output, _ = process.communicate()
            else:
                output, _ = process.communicate(timeout=timeout_seconds)
            session.output.extend(output or b"")
            session.pending_output.extend(output or b"")
            session.refresh()
            return {"status": "completed", "sessionId": session.session_id, "output": (output or b"").decode(errors="replace"), "exitCode": process.returncode}
        except subprocess.TimeoutExpired:
            # Native timeout is distinct from outer watchdog; terminate the tree.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return {"status": "failed", "sessionId": session.session_id, "error": "Command timed out"}

    def _poll_delivery(self, record: ManagedProcess) -> PendingDelivery:
        record.refresh()
        payload = bytes(record.pending_output)
        text = payload.decode(errors="replace").strip() or "(no new output)"
        record.pending_output.clear()
        acknowledged = False

        def acknowledge() -> None:
            nonlocal acknowledged
            if acknowledged:
                return
            acknowledged = True
            self._pending.pop(record.session_id, None)

        delivery = PendingDelivery(record.session_id, text, {"status": "completed" if record.exited else "running", "sessionId": record.session_id}, acknowledge)
        self._pending[record.session_id] = delivery
        return delivery

    def acknowledge_poll(self, session_id: str) -> None:
        delivery = self._pending.get(session_id)
        if delivery:
            delivery.acknowledge()

    def _process(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self.scope.reap()
        action = args.get("action")
        if action == "list":
            records = [*self.scope._records.values(), *self.scope._finished.values()]
            records = [r for r in records if not r.removed]
            return {"status": "completed", "sessions": [{"sessionId": r.session_id, "status": "completed" if r.exited else "running", "runtimeMs": int(((r.ended_at or time.time()) - r.started_at) * 1000), "command": r.command, "cwd": r.cwd} for r in records]}
        session_id = str(args.get("sessionId") or "")
        record = self.scope.get(session_id)
        if record is None:
            return {"status": "failed", "error": f"No session found for {session_id}"}
        if action == "poll":
            wait_ms = max(0, min(PROCESS_MAX_POLL_MS, int(float(args.get("timeout", 0) or 0))))
            deadline = time.monotonic() + wait_ms / 1000
            while wait_ms and not record.exited and not record.pending_output and time.monotonic() < deadline:
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                record.refresh()
            delivery = self._poll_delivery(record)
            return {"status": delivery.details["status"], "sessionId": session_id, "output": delivery.text, "pendingAcknowledgement": True}
        if action == "log":
            record.refresh()
            return {"status": "completed" if record.exited else "running", "sessionId": session_id, "output": bytes(record.output).decode(errors="replace")}
        if action == "write":
            if record.process.stdin is None:
                return {"status": "failed", "error": "stdin is not writable"}
            record.process.stdin.write(str(args.get("data", "")).encode())
            record.process.stdin.flush()
            if args.get("eof"):
                record.process.stdin.close()
            return {"status": "running", "sessionId": session_id}
        if action in {"submit", "paste", "send-keys"}:
            if record.process.stdin is None:
                return {"status": "failed", "error": "stdin is not writable"}
            if action == "submit":
                data = b"\r"
            elif action == "paste":
                text = str(args.get("text", ""))
                data = ("\x1b[200~" + text + "\x1b[201~").encode() if args.get("bracketed", True) else text.encode()
            else:
                data = str(args.get("literal", "")).encode()
            record.process.stdin.write(data)
            record.process.stdin.flush()
            return {"status": "running", "sessionId": session_id}
        if action == "kill":
            try:
                os.killpg(record.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            return {"status": "completed", "sessionId": session_id, "message": f"Termination requested for session {session_id}."}
        if action in {"clear", "remove"}:
            record.removed = True
            if action == "remove" and not record.exited:
                try:
                    os.killpg(record.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            return {"status": "completed", "sessionId": session_id}
        return {"status": "failed", "error": f"Unknown process action {action}"}


@dataclass(frozen=True)
class RecoveryDecision:
    trigger: str
    committed_prefix: tuple[Mapping[str, Any], ...] = ()
    stop: bool = True
    retry: bool = False
    fallback: bool = False
    compaction: bool = False


def stop_before_recovery(
    trigger: str,
    committed_prefix: Sequence[Mapping[str, Any]] = (),
    *,
    retry: bool = False,
    fallback: bool = False,
    compaction: bool = False,
) -> RecoveryDecision:
    """Record the recovery decision before destructive retry/compaction changes."""
    return RecoveryDecision(trigger, tuple(committed_prefix), True, retry, fallback, compaction)


def native_worker_invocation() -> dict[str, Any]:
    """Exact pinned supplier invocation used only for oracle capture.

    BreadBoard does not invoke the supplier loop.  If an installed Node worker
    is selected, it must be this bounded one-shot command and must hand back
    prepared tool effects rather than owning model/history/recovery policy.
    """
    return {
        "node": "/opt/openclaw-runtime/bin/node",
        "argv": [
            "/opt/openclaw/dist/index.js",
            "agent",
            "--local",
            "--json",
            "--timeout",
            "45",
            "--isolated",
            "--auth-env-only",
        ],
        "node_range": ">=24.16.0 <25 || >=26.1.0",
        "source_commit": OPENCLAW_SOURCE_COMMIT,
        "worker_owns": ["tool preparation", "tool effects", "process scope", "pending acknowledgements"],
        "breadboard_owns": ["model dispatch", "history commit", "recovery", "terminal arbitration"],
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest}"
