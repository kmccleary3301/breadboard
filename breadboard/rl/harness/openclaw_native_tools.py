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
import struct
import subprocess
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

OPENCLAW_SOURCE_COMMIT = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
OPENCLAW_VERSION = "2026.9.4"
MAX_AGGREGATE_OUTPUT_CHARS = 200_000
MAX_PENDING_OUTPUT_CHARS = 30_000
TOOL_ORDER = ("edit", "exec", "ls", "process", "read", "write")
PROMPT_TOOL_ORDER = TOOL_ORDER
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
    """Synchronous phase bridge to the pinned Node OpenClaw source worker."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        node: str | None = None,
        dist: str | None = None,
    ) -> None:
        self.workspace = str(Path(workspace).resolve())
        self.node = node or os.environ.get("OPENCLAW_NODE", "node")
        fallback_dist = "/tmp/openclaw-npm-20260923/node_modules/openclaw/dist"
        default_dist = (
            "/opt/openclaw/dist"
            if Path("/opt/openclaw/dist").is_dir()
            else (fallback_dist if Path(fallback_dist).is_dir() else "/opt/openclaw/dist")
        )
        self.dist = dist or os.environ.get("OPENCLAW_DIST", default_dist)
        if not Path(self.dist).is_dir():
            raise FileNotFoundError(f"pinned OpenClaw dist is unavailable: {self.dist}")
        worker = Path(__file__).with_name("openclaw_tool_worker.mjs")
        env = {
            key: value
            for key, value in os.environ.items()
            if not _is_provider_auth_env(key)
        }
        env["OPENCLAW_DIST"] = self.dist
        self._process = subprocess.Popen(
            [self.node, "--import", str(worker.with_name("openclaw_classifier_loader.mjs")), str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=env,
            cwd=self.workspace,
            bufsize=0,
        )
        self._request_id = 0
        self._pending: dict[str, str | None] = {}
        self._bootstrap: tuple[Mapping[str, Any], ...] = ()
        bootstrap_root = (
            Path(__file__).resolve().parents[3]
            / "config"
            / "e4_targets"
            / "openclaw"
            / OPENCLAW_VERSION
            / "bootstrap"
        )
        assets: list[dict[str, str]] = []
        for name in ("AGENTS.md", "SOUL.md"):
            content = (bootstrap_root / name).read_text(encoding="utf-8")
            assets.append({"name": name, "content": content, "sha256": sha256_file(bootstrap_root / name)})
        native_config_path = (
            Path(__file__).resolve().parents[3]
            / "config"
            / "e4_targets"
            / "openclaw"
            / OPENCLAW_VERSION
            / "native-config.json"
        )
        native_config = json.loads(native_config_path.read_text(encoding="utf-8"))
        advertisement = native_config["advertisement"]
        model_config = {
            "id": "openclaw-tool-client",
            "name": "openclaw-tool-client",
            "api": "openai-completions",
            "provider": "openai",
            "baseUrl": "http://127.0.0.1",
            "input": ["text"],
            "contextWindow": 32_768,
            "maxTokens": 2_048,
            "compat": {
                "supportsStore": True,
                "supportsDeveloperRole": True,
                "supportsUsageInStreaming": True,
                "supportsStrictMode": False,
            },
        }
        initialized = self._request(
            {
                "phase": "initialize",
                "workspace": self.workspace,
                "scopeKey": f"openclaw:e4:{os.getpid()}",
                "bootstrap_assets": assets,
                "advertisement": advertisement,
                "model_config": model_config,
                "runtime_inputs": {
                    "cwd": self.workspace,
                    "home": str(Path.home()),
                    "current_date": datetime.now(timezone.utc).date().isoformat(),
                    "package_dir": self.dist,
                    "session_id": uuid.uuid4().hex,
                },
                "system_prompt": "",
            }
        )
        self._bootstrap = tuple(
            item
            for item in initialized.get("bootstrap", ())
            if isinstance(item, Mapping)
        )

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("OpenClaw Node worker pipes are unavailable")
        phase = payload.get("phase")
        if not isinstance(phase, str) or not phase:
            raise ValueError("OpenClaw native phase is required")
        self._request_id += 1
        command = {
            "schema_version": "bb.native-worker.rpc.v1",
            "request_id": self._request_id,
            "operation": phase,
            "payload": {key: value for key, value in payload.items() if key != "phase"},
        }
        encoded = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._process.stdin.write(struct.pack(">I", len(encoded)) + encoded)
        self._process.stdin.flush()
        prefix = self._process.stdout.read(4)
        if len(prefix) != 4:
            details = self._process.stderr.read() if self._process.stderr is not None else b""
            raise RuntimeError(
                f"OpenClaw Node worker exited: {bytes(details)[-400:].decode(errors='replace')}"
            )
        length = struct.unpack(">I", prefix)[0]
        if length > 16 * 1024 * 1024:
            raise RuntimeError("OpenClaw native worker frame exceeds the limit")
        body = self._process.stdout.read(length)
        if len(body) != length:
            raise RuntimeError("OpenClaw native worker returned a short frame")
        response = json.loads(body.decode("utf-8"))
        if response.get("request_id") != self._request_id:
            raise ValueError("OpenClaw native worker request ID mismatch")
        if "error" in response:
            error = response["error"]
            raise ValueError(str(error.get("message", error)) if isinstance(error, Mapping) else str(error))
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise ValueError("OpenClaw Node worker result is not an object")
        return dict(result)

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        call_id = str(uuid.uuid4())
        prepared = self._request(
            {
                "phase": "prepare_tools",
                "calls": [{"id": call_id, "name": name, "arguments": dict(arguments or {})}],
            }
        )
        calls = prepared.get("calls")
        if not isinstance(calls, Sequence) or not calls:
            raise ValueError("OpenClaw source preparation returned no call")
        results = self._request({"phase": "execute_batch"}).get("results")
        if not isinstance(results, Sequence) or not results:
            raise ValueError("OpenClaw source execution returned no result")
        result = next(
            (item for item in results if isinstance(item, Mapping) and item.get("id") == call_id),
            results[0],
        )
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
        normalized["isError"] = bool(result.get("isError"))
        normalized.setdefault("status", "completed")
        if name in {"read", "write", "edit", "ls"}:
            normalized.setdefault("content", text)
        elif text:
            normalized.setdefault("output", text)
        delivery_id = result.get("delivery_id")
        if isinstance(delivery_id, str):
            session_id = details.get("sessionId")
            self._pending[delivery_id] = str(session_id) if session_id is not None else None
            normalized["delivery_id"] = delivery_id
            normalized["pendingAcknowledgement"] = True
        return normalized

    def acknowledge(self, delivery_id: str, history_digest: str = "") -> None:
        key = str(delivery_id)
        if key not in self._pending:
            matches = [pending_id for pending_id, session_id in self._pending.items() if session_id == key]
            if not matches:
                raise KeyError(f"unknown pending OpenClaw delivery {key}")
            for pending_id in matches:
                self.acknowledge(pending_id, history_digest)
            return
        self._request(
            {
                "phase": "ack",
                "delivery_id": key,
                "history_digest": history_digest,
            }
        )
        del self._pending[key]

    def bootstrap(self) -> tuple[Mapping[str, Any], ...]:
        return self._bootstrap

    def close(self) -> None:
        if self._process.poll() is None:
            result = self._request({"phase": "close"})
            cleanup = result.get("cleanup")
            if not isinstance(cleanup, Mapping) or cleanup.get("all_dead") is not True:
                raise RuntimeError("OpenClaw worker close did not prove all processes dead")
            self._process.stdin.close() if self._process.stdin is not None else None
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
        # Arguments cross the source boundary unchanged.  OpenClaw's pinned
        # Node factory owns preparation, validation, and effect admission.
        return self._worker.execute(name, arguments)

    def acknowledge_poll(self, delivery_id: str, history_digest: str = "") -> None:
        self._worker.acknowledge(delivery_id, history_digest)

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
        "command": ["node", "--import", "./breadboard/rl/harness/openclaw_classifier_loader.mjs", "breadboard/rl/harness/openclaw_tool_worker.mjs"],
        "protocol": "bb.openclaw-native.v1",
        "transport": "bb.native-worker.rpc.v1",
        "phases": [
            "initialize",
            "project_request",
            "prepare_tools",
            "execute_batch",
            "ack",
            "close",
        ],
        "runtime": "node >=24.16.0 <25 || >=26.1.0",
        "source_modules": [
            "core-coding-tools-DoP9tAh3.mjs",
            "bash-process-registry-DHrULGkz.mjs",
            "bootstrap-DYYMCrXY.mjs",
            "workspace-YW5Pl2cf.mjs",
        ],
        "source_commit": OPENCLAW_SOURCE_COMMIT,
        "worker_owns": [
            "verified pinned source tool construction",
            "raw argument preparation and tool effects",
            "process/PTY scope and independently observed cleanup",
            "unique pending poll deliveries",
            "bootstrap asset materialization",
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
