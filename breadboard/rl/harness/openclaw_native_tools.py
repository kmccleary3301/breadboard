"""Source-derived OpenClaw 2026.9.4 native tool and lifecycle helpers.

This module intentionally owns only the tool boundary.  The model loop, provider
transport, history commit, recovery admission, and terminal arbitration stay in
BreadBoard's conductor.  The constants below are grounded in the pinned source
archive (commit 3a9d69db306cd7f081e06254cb89c4bcc14a7107).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

OPENCLAW_SOURCE_COMMIT = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
OPENCLAW_VERSION = "2026.9.4"
MAX_AGGREGATE_OUTPUT_CHARS = 200_000
MAX_PENDING_OUTPUT_CHARS = 30_000
TOOL_ORDER = ("ls", "read", "edit", "write", "exec", "process")
PROMPT_TOOL_ORDER = ("read", "write", "edit", "ls", "exec", "process")
BOOTSTRAP_ORDER = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "BOOTSTRAP.md", "MEMORY.md")
BOOTSTRAP_MAX_CHARS = 20_000
BOOTSTRAP_TOTAL_MAX_CHARS = 60_000
_PROVIDER_AUTH_ENV_MARKERS = (
    "API_KEY",
    "API_TOKEN",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "GATEWAY_TOKEN",
    "TOKEN",
    "SECRET",
    "PASSWORD",
)


def _is_provider_auth_env(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _PROVIDER_AUTH_ENV_MARKERS)
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
    """Mirror pinned embedded-agent bootstrap trimming and policy digest."""
    trimmed = content.rstrip()
    if len(trimmed) <= budget:
        return trimmed
    budget = max(1, int(budget))
    if name.lower() == "agents.md":
        candidates: list[tuple[str, bool]] = []
        previous: str | None = None
        candidate_re = re.compile(
            r"\b(?:AGENTS\.md|scoped|required|must|never|do not|before subtree|read scoped|"
            r"owner|security|secret|credential|test|validation|command|commit|push|github|pr)\b"
            r"|(?:🔴|禁止|嚴禁|不得|絕不|絕對不|切勿|必須|務必|一律|紅線)",
            re.I,
        )
        for source_line in trimmed.splitlines():
            line = re.sub(r"\s+", " ", source_line.strip())
            if not line:
                previous = None
                continue
            if re.match(r"^\s*(?:`{3,}|~{3,})", line):
                previous = None
                continue
            candidate = bool(
                re.match(r"^(?:#{1,6}|\s*[-*+]|\s*\d+[.)])\s+\S", line)
                or candidate_re.search(line)
            )
            if candidate:
                text = line if previous is None else f"{previous}\n{line}"
                candidates.append((text[:240], bool(re.search(
                    r"\b(?:AGENTS\.md|scoped|required|must|never|do not|before subtree|"
                    r"read scoped|security|secret|credential)\b|(?:🔴|禁止|嚴禁|不得|絕不|絕對不|切勿)",
                    line,
                    re.I,
                ))))
                previous = None
            else:
                previous = line

        def digest_for(limit: int) -> tuple[str, int]:
            selected: list[str] = []
            used = 0
            for high in (True, False):
                for text, priority in candidates:
                    if priority != high or text in selected:
                        continue
                    extra = len(text) + (1 if selected else 0)
                    if used + extra <= limit:
                        selected.append(text)
                        used += extra
            return "\n".join(selected), max(0, len(candidates) - len(selected))

        head = int(budget * 0.45)
        tail = int(budget * 0.15)
        digest_budget = int(budget * 0.35)

        def render() -> str:
            digest, omitted = digest_for(digest_budget)
            parts = [
                trimmed[:head],
                "[...truncated, read AGENTS.md for full content...]",
                "[Policy digest from AGENTS.md]" if digest else "",
                digest,
                f"[...{omitted} more policy lines omitted...]" if omitted else "",
                f"…(truncated AGENTS.md: kept {head}+policy {len(digest)}+{tail} chars of {len(trimmed)})…",
                trimmed[-tail:] if tail else "",
            ]
            return "\n".join(part for part in parts if part)

        rendered = render()
        while len(rendered) > budget and (tail > 0 or head > 1 or digest_budget > 0):
            overflow = len(rendered) - budget
            if tail:
                tail = max(0, tail - overflow)
            elif head > 1:
                head = max(1, head - overflow)
            else:
                digest_budget = max(0, digest_budget - overflow)
            rendered = render()
        return rendered[:budget]

    marker_template = lambda h, t: (
        f"\n[...truncated, read {name} for full content...]\n"
        f"…(truncated {name}: kept {h}+{t} chars of {len(trimmed)})…\n"
    )
    full_marker = marker_template(0, 0)
    compact = len(full_marker) + 1 + 16 > budget
    marker_template = (
        (lambda h, t: f"[…truncated {h}+{t}/{len(trimmed)}]")
        if compact
        else marker_template
    )
    head = tail = 0
    marker = marker_template(head, tail)
    for _ in range(3):
        separator = (1 if head else 0) + (1 if tail else 0) if "\n" in marker else 0
        content_budget = max(0, budget - len(marker) - separator)
        next_head = int(content_budget * 0.75)
        next_tail = int(content_budget * 0.25)
        next_marker = marker_template(next_head, next_tail)
        if (next_head, next_tail, len(next_marker)) == (head, tail, len(marker)):
            break
        head, tail, marker = next_head, next_tail, next_marker
    while head + tail + len(marker) + ((1 if head else 0) + (1 if tail else 0) if "\n" in marker else 0) > budget and (tail or head):
        overflow = head + tail + len(marker) - budget
        if tail:
            tail = max(0, tail - overflow)
        else:
            head = max(0, head - overflow)
        marker = marker_template(head, tail)
    if not head and not tail and len(trimmed) and len(marker_template(1, 0)) + 1 <= budget:
        head = 1
        marker = marker_template(1, 0)
    output = "\n".join(part for part in (trimmed[:head], marker, trimmed[-tail:] if tail else "") if part)
    return output[:budget]


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


class _NodeWorkerScope:
    def __init__(self, worker: "_OpenClawWorkerClient") -> None:
        self._worker = worker

    def cleanup(self) -> None:
        self._worker.close()
    def __enter__(self) -> "_NodeWorkerScope":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.cleanup()


class _OpenClawWorkerClient:
    """Synchronous JSONL bridge to the pinned Node source-tool worker."""

    def __init__(self, workspace: str | Path, *, node: str | None = None, dist: str | None = None) -> None:
        self.workspace = str(Path(workspace).resolve())
        self.node = node or os.environ.get("OPENCLAW_NODE", "node")
        self.dist = dist or os.environ.get("OPENCLAW_DIST", "/opt/openclaw/dist")
        if not Path(self.dist).is_dir():
            candidates = sorted(Path("/tmp").glob("openclaw-npm-*/node_modules/openclaw/dist"))
            if candidates:
                self.dist = str(candidates[-1])
        worker = Path(__file__).with_name("openclaw_tool_worker.mjs")
        env = os.environ.copy()
        env["OPENCLAW_DIST"] = self.dist
        self._process = subprocess.Popen(
            [self.node, str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=self.workspace,
            bufsize=1,
        )
        self._pending: dict[str, str] = {}
        self._request({"op": "init", "workspace": self.workspace})

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("OpenClaw Node worker pipes are unavailable")
        self._process.stdin.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            details = self._process.stderr.read() if self._process.stderr is not None else ""
            raise RuntimeError(f"OpenClaw Node worker exited: {details[-400:]}")
        response = json.loads(line)
        if not response.get("ok"):
            raise ValueError(str(response.get("error", "OpenClaw Node worker request failed")))
        return response

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self._request({
            "op": "tool",
            "tool_call_id": str(uuid.uuid4()),
            "name": name,
            "arguments": dict(arguments or {}),
        })
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise ValueError("OpenClaw source tool returned a non-object result")
        details = result.get("details") if isinstance(result.get("details"), Mapping) else {}
        content = result.get("content")
        text = ""
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            text = "\n".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, Mapping) and part.get("type") == "text"
            )
        normalized = dict(details)
        normalized.setdefault("status", "completed")
        if name in {"read", "write", "edit", "ls"}:
            normalized.setdefault("content", text)
        elif text:
            normalized.setdefault("output", text)
        ack = response.get("acknowledgement")
        if isinstance(ack, Mapping) and isinstance(ack.get("token"), str):
            normalized["acknowledgement"] = dict(ack)
            session_id = details.get("sessionId")
            if name == "process" and details.get("status") in {"running", "completed"} and session_id:
                self._pending[str(session_id)] = str(ack["token"])
                normalized["pendingAcknowledgement"] = True
        return normalized

    def acknowledge(self, session_id: str) -> None:
        token = self._pending.pop(str(session_id), None)
        if token:
            self._request({"op": "ack", "token": token})

    def bootstrap(self) -> tuple[Mapping[str, Any], ...]:
        response = self._request({"op": "bootstrap"})
        context = response.get("context")
        return tuple(item for item in context if isinstance(item, Mapping)) if isinstance(context, Sequence) else ()

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()



class OpenClawNativeTools:
    """Contained facade over the pinned Node OpenClaw source-tool worker."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        worker: _OpenClawWorkerClient | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._worker = worker or _OpenClawWorkerClient(self.workspace)
        self.scope = _NodeWorkerScope(self._worker)

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._worker.execute(name, prepare_tool_call(name, arguments))

    def acknowledge_poll(self, session_id: str) -> None:
        self._worker.acknowledge(session_id)

    def bootstrap_context(self) -> tuple[Mapping[str, Any], ...]:
        return self._worker.bootstrap()


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
    """Tool-only Node worker command; no model loop, provider, or supplier process."""
    return {
        "command": ["node", "breadboard/rl/harness/openclaw_tool_worker.mjs"],
        "protocol": "bb.openclaw.tool-worker.jsonl.v1",
        "runtime": "node >=24.16.0 <25 || >=26.1.0",
        "source_modules": [
            "core-coding-tools-DoP9tAh3.mjs",
            "bash-tools-Cb_Bzn6B.mjs",
            "resource-loader-Bu_pVD2t.mjs",
            "bootstrap-DYYMCrXY.mjs",
            "workspace-YW5Pl2cf.mjs",
        ],
        "source_commit": OPENCLAW_SOURCE_COMMIT,
        "worker_owns": [
            "pinned source tool construction",
            "tool effects",
            "process scope",
            "pending acknowledgements",
            "bootstrap loader",
        ],
        "breadboard_owns": [
            "model dispatch",
            "history commit",
            "recovery",
            "terminal arbitration",
        ],
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest}"
