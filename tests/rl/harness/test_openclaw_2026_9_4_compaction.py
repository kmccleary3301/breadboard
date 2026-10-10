from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import uuid

import pytest

_WORKER = Path(__file__).parents[3] / "breadboard/rl/harness/openclaw_tool_worker.mjs"
_LOADER = _WORKER.with_name("openclaw_classifier_loader.mjs")
_DEFAULT_DIST = Path(
    os.environ.get(
        "OPENCLAW_DIST",
        "/Users/kylemccleary/.cache/bb-compaction-e4/pkgs/openclaw@2026.9.4/node_modules/openclaw/dist",
    )
)


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class _WorkerClient:
    def __init__(
        self, dist_path: Path, workspace: Path | None = None,
        initialize: bool = True, context_window: int = 131_072,
    ):
        self._proc = subprocess.Popen(
            ["node", "--import", str(_LOADER), str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "OPENCLAW_DIST": str(dist_path)},
        )
        self._workspace = workspace or (Path("/tmp") / f"openclaw_test_worker_{uuid.uuid4().hex[:8]}")
        self._workspace.mkdir(parents=True, exist_ok=True)
        if initialize:
            target_dir = Path(__file__).resolve().parents[3] / "config" / "e4_targets" / "openclaw" / "2026.9.4"
            native_config = json.loads((target_dir / "native-config.json").read_text(encoding="utf-8"))
            bootstrap_root = target_dir / "bootstrap"
            assets = []
            for name in ("AGENTS.md", "SOUL.md"):
                p = bootstrap_root / name
                assets.append({"name": name, "content": p.read_text(encoding="utf-8"), "sha256": _sha256_file(p)})
            # Production-shaped model_config from policy_provider.py
            model_config = {
                "id": "openclaw-tool-client",
                "name": "openclaw-tool-client",
                "api": "openai-completions",
                "provider": "openai",
                "baseUrl": "http://127.0.0.1",
                "input": ["text"],
                "contextWindow": context_window,
                "maxTokens": 2_048,
            }
            self.initialized = self.request({
                "phase": "initialize",
                "workspace": str(self._workspace),
                "scopeKey": f"openclaw:e4:{os.getpid()}",
                "bootstrap_assets": assets,
                "advertisement": native_config["advertisement"],
                "model_config": model_config,
                "runtime_inputs": {
                    "cwd": str(self._workspace),
                    "home": str(Path.home()),
                    "current_date": "2026-10-09",
                    "message_timestamp_ms": "1728432000000",
                    "package_dir": str(Path(__file__).resolve().parents[3]),
                    "session_id": "test-session",
                },
            })
            self.tool_schemas = self.initialized["tool_schemas"]
            self.model_config = model_config

    def request(self, payload: dict) -> dict:
        req_id = str(uuid.uuid4())
        phase = payload.get("phase") or payload.get("operation")
        sub_payload = {k: v for k, v in payload.items() if k not in ("phase", "operation")}
        body = json.dumps({
            "schema_version": "bb.native-worker.rpc.v1",
            "request_id": req_id,
            "operation": phase,
            "payload": sub_payload,
        }, ensure_ascii=False).encode("utf-8")
        self._proc.stdin.write(struct.pack(">I", len(body)) + body)
        self._proc.stdin.flush()
        prefix = self._proc.stdout.read(4)
        if len(prefix) != 4:
            err = self._proc.stderr.read() if self._proc.stderr else b""
            raise RuntimeError(f"worker crashed: {err.decode('utf-8', errors='replace')}")
        length = struct.unpack(">I", prefix)[0]
        resp_bytes = self._proc.stdout.read(length)
        resp = json.loads(resp_bytes.decode("utf-8"))
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp.get("result", {})

    def close(self):
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            self._proc.kill()


@pytest.fixture
def worker(tmp_path: Path):
    client = _WorkerClient(_DEFAULT_DIST, workspace=tmp_path / "workspace")
    try:
        yield client
    finally:
        client.close()


def _make_conversation_history(turns: int = 4) -> list[dict]:
    messages = []
    for i in range(turns):
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": f"User instruction turn {i}: please inspect file_{i}.py with detailed description"}],
        })
        messages.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Inspecting file_{i}.py now."},
                {"type": "toolCall", "id": f"call_{i}", "name": "read", "arguments": {"path": f"file_{i}.py"}},
            ],
        })
        messages.append({
            "role": "toolResult",
            "toolCallId": f"call_{i}",
            "toolName": "read",
            "isError": False,  # Required by stock session-entry-codec-TjyNj_PU.mjs:27-32.
            "content": [{"type": "text", "text": f"def function_{i}():\n    return {i}\n"}],
        })
    return messages


def test_openclaw_compaction_worker_uninitialized(tmp_path: Path):
    client = _WorkerClient(_DEFAULT_DIST, workspace=tmp_path / "uninit_workspace", initialize=False)
    try:
        with pytest.raises(RuntimeError, match="worker is not initialized with model_config"):
            client.request({
                "phase": "prepare_compaction",
                "reason": "overflow",
                "checkpoint": "overflow",
                "messages": _make_conversation_history(2),
                "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
            })
    finally:
        client.close()


def test_openclaw_worker_prepare_and_finalize_overflow_compaction(worker):
    messages = _make_conversation_history(5)
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })

    assert prepared["kind"] == "compaction_prepared"
    assert prepared["schema_version"] == "bb.openclaw-native.v1"
    summary_req = prepared["summary_request"]
    assert len(summary_req["messages"]) == 2
    assert summary_req["messages"][0]["role"] == "system"
    assert "You are a context summarization assistant." in summary_req["messages"][0]["content"]
    assert summary_req["messages"][1]["role"] == "user"
    user_text = summary_req["messages"][1]["content"][0]["text"]
    assert "<conversation>" in user_text
    assert "## Goal" in user_text

    prep = prepared["preparation"]
    assert "tokensBefore" in prep
    assert prep["firstKeptIndex"] > 0

    turn_prefix_summary = "Prefix work summary" if prep.get("isSplitTurn") else None

    # Finalize compaction
    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": "## Goal\nInspect files\n\n## Decisions\nInspected files 0 to 2",
        "turn_prefix_summary": turn_prefix_summary,
        "preparation": prep,
    })

    assert finalized["kind"] == "compaction_finalized"
    assert finalized["first_kept_index"] == prep["firstKeptIndex"]
    assert finalized["compaction_message"]["role"] == "compactionSummary"
    assert "## Decisions" in finalized["compaction_message"]["summary"]
    assert finalized["compaction_message"]["timestamp"] == int(worker.initialized["bootstrap"]["message_timestamp_ms"]) + 1

    # Verify project_request converts the stock compactionSummary message
    retained = [
        {"role": "system", "content": "You are an agent"},
        finalized["compaction_message"],
        *messages[finalized["first_kept_index"]:],
    ]
    projected = worker.request({"phase": "project_request", "messages": retained})
    wire_user_content = projected["messages"][1]["content"][0]["text"]
    assert "The conversation history before this point was compacted" in wire_user_content
    assert "<summary>" in wire_user_content
    assert "## Decisions" in wire_user_content


def test_openclaw_worker_threshold_not_triggered(worker):
    messages = _make_conversation_history(2)
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "threshold",
        "checkpoint": "agent_end",
        "messages": messages,
        "usage": {
            "input": 5000,
            "output": 200,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 5200,
        },
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 20000},
    })

    assert prepared["kind"] == "compaction_unavailable"
    assert prepared["reason"] == "not_triggered"


def test_openclaw_worker_threshold_compaction_fires(worker):
    messages = _make_conversation_history(6)
    # 131072 window, reserve is floored at 20000 (capped at 32768).
    # Threshold is 131072 - 20000 = 111072 tokens.
    # Usage of 115000 exceeds threshold -> must fire!
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "threshold",
        "checkpoint": "agent_end",
        "messages": messages,
        "usage": {
            "input": 114000,
            "output": 1000,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 115000,
        },
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })

    assert prepared["kind"] == "compaction_prepared"
    assert prepared["preparation"]["tokensBefore"] == 115008


def test_openclaw_worker_split_turn_compaction(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Start task"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Working"}, {"type": "toolCall", "id": "c1", "name": "exec", "arguments": {"command": "ls"}}]},
        {"role": "toolResult", "toolCallId": "c1", "toolName": "exec", "isError": False, "content": [{"type": "text", "text": "log\n" * 500}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Continuing"}, {"type": "toolCall", "id": "c2", "name": "read", "arguments": {"path": "a.txt"}}]},
        {"role": "toolResult", "toolCallId": "c2", "toolName": "read", "isError": False, "content": [{"type": "text", "text": "content\n" * 500}]},
    ]
    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })

    assert prepared["kind"] == "compaction_prepared"
    prep = prepared["preparation"]
    if prep.get("isSplitTurn"):
        assert prepared.get("turn_prefix_request") is not None
        prefix_req = prepared["turn_prefix_request"]
        prefix_text = prefix_req["messages"][1]["content"][0]["text"]
        assert "This is the PREFIX of a turn that was too large to keep" in prefix_text

        finalized = worker.request({
            "phase": "finalize_compaction",
            "summary": "Prior turn summary",
            "turn_prefix_summary": "Prefix work done",
            "preparation": prep,
        })
        assert finalized["kind"] == "compaction_finalized"
        assert finalized["compaction_message"]["role"] == "compactionSummary"
        assert "Turn Context (split turn):" in finalized["compaction_message"]["summary"]
        assert "Prefix work done" in finalized["compaction_message"]["summary"]


def test_openclaw_worker_parity_against_stock_native_work(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "step 0"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 0"}]},
        {"role": "user", "content": [{"type": "text", "text": "step 1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 3}
    direct = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=["Prior work", "Current turn"], tools=worker.tool_schemas,
    )
    worker_prep = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages, "settings": settings,
    })
    assert worker_prep["kind"] == "compaction_prepared"
    assert worker_prep["preparation"]["firstKeptIndex"] == direct["first_kept_index"]


def _run_stock_compaction_direct(
    messages: list[dict],
    *,
    reason: str,
    settings: dict,
    usage: dict | None = None,
    context_window: int = 131072,
    scripted_responses: list[str] | None = None,
    tools: list[dict] | None = None,
    model_config: dict | None = None,
    runtime_wire_message: dict | None = None,
    message_timestamp_ms: int = 1728432000000,
    source_entries: list[dict] | None = None,
    source_messages: list[dict] | None = None,
    overflow_recovery_attempts: int = 0,
) -> dict:
    node_script = """
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { normalizeMessagesForLlmBoundary } from "openclaw:pinned-attempt-prompt";
const dist = process.env.OPENCLAW_DIST;
const { n: AgentSession, rt: SettingsManager, b: ExtensionRunner } = await import(pathToFileURL(dist + "/resource-loader-Bu_pVD2t.mjs"));
const { t: SessionManager } = await import(pathToFileURL(dist + "/session-manager-DZHCo5g0.mjs"));
const { t: Agent } = await import(pathToFileURL(dist + "/agent-core-B_87jlHI.mjs"));
const { r: applyAgentCompactionSettingsFromConfig } = await import(pathToFileURL(dist + "/agent-settings-DcI_VuTd.mjs"));
const { t: resolveModel, n: completeModel } = await import(pathToFileURL(dist + "/model.inline-provider-BOrD-NlO.mjs"));
const { u: convertToLlm } = await import(pathToFileURL(dist + "/session-DVbOtm8K.mjs"));
const transport = await import(pathToFileURL(dist + "/openai-transport-stream-D950WgL3.mjs"));
const inputData = JSON.parse(process.argv[1]);
const system = inputData.messages[0]?.role === "system" ? inputData.messages[0] : null;
const messages = system ? inputData.messages.slice(1) : inputData.messages;
const timestamp = new Date(messages.reduce((value, msg) => typeof msg.timestamp === "number" ? Math.max(value, msg.timestamp) : value, inputData.message_timestamp_ms) + 1).toISOString();
// Clock seam for stock append/project replacement: session-manager-DZHCo5g0.mjs:1034-1036,
// resource-loader-Bu_pVD2t.mjs:10000. No stock compaction body is copied.
const SourceDate = Date;
globalThis.Date = class extends SourceDate {
  constructor(...args) { super(...(args.length ? args : [SourceDate.parse(timestamp)])); }
  static now() { return SourceDate.parse(timestamp); }
};
const entries = inputData.source_entries || messages.map((msg, index) => ({
  id: `entry_${index}`, parentId: index === 0 ? null : `entry_${index - 1}`,
  ...(msg.role === "compactionSummary" ? {
    type: "compaction", summary: msg.summary, tokensBefore: msg.tokensBefore,
    firstKeptEntryId: `entry_${index + 1}`,
    timestamp: typeof msg.timestamp === "number" ? new Date(msg.timestamp).toISOString() : msg.timestamp,
  } : { type: "message", message: msg, timestamp }),
}));
if (inputData.usage) {
  const last = messages.findLast((msg) => msg.role === "assistant");
  if (last && !Object.hasOwn(last, "usage") && !last.providerUsage) last.usage = inputData.usage;
}
const declared = inputData.model_config;
const providerConfig = {
  baseUrl: declared.baseUrl, api: declared.api,
  models: [{ id: declared.id, name: declared.name, input: declared.input,
    contextWindow: declared.contextWindow, maxTokens: declared.maxTokens }],
};
const model = completeModel(resolveModel({ [declared.provider]: providerConfig })[0], providerConfig);
const stockRequire = createRequire(pathToFileURL(dist + "/agent-core-B_87jlHI.mjs"));
const { parseOpenAICompletionsUsage, createEmptyTransportUsage } = await import(pathToFileURL(stockRequire.resolve("@openclaw/ai/transports")));
const { t: createAssistantOutput } = await import(new URL("./assistant-output-tLt4H-iQ.mjs", pathToFileURL(stockRequire.resolve("@openclaw/ai/transports"))));
for (const msg of messages) {
  if (msg.role === "assistant" && !Object.hasOwn(msg, "usage")) {
    msg.usage = msg.providerUsage ? parseOpenAICompletionsUsage(msg.providerUsage, model) : createEmptyTransportUsage();
  }
}
const settingsManager = SettingsManager.inMemory({ compaction: inputData.settings });
applyAgentCompactionSettingsFromConfig({ settingsManager, contextTokenBudget: model.contextWindow });
const manager = SessionManager.fromSelectedEntries([
  { type: "session", version: 4, id: "oracle", cwd: dist, timestamp }, ...entries,
], dist);
if (inputData.source_entries) {
  // The source's live Agent state can omit an overflow assistant saved to the
  // ledger (resource-loader:10107-10109,10183-10186). Admit only the new suffix.
  const priorCount = inputData.source_messages.filter((msg) => msg.role !== "system").length;
  for (const msg of messages.slice(priorCount)) manager.appendMessage(msg);
}
const initialContext = inputData.source_entries ? messages : manager.buildSessionContext().messages;
const initialBranch = manager.getBranch();
const capturedRequests = [];
let responseIndex = 0;
const streamFn = (m, context, options) => {
  const wire = transport.t(m, context, options);
  capturedRequests.push({ messages: wire.messages, max_tokens: wire.max_completion_tokens ?? wire.max_tokens });
  const summary = inputData.scripted_responses[responseIndex++];
  if (typeof summary !== "string") throw new Error("Oracle has no scripted provider response");
  const response = { stopReason: "stop", content: [{ type: "text", text: summary }] };
  return { async *[Symbol.asyncIterator]() {}, result: async () => response };
};
const episodeTools = inputData.tools.map((tool) => tool.function);
const agent = new Agent({ initialState: {
  model, systemPrompt: system ? system.content : "", messages: initialContext,
  tools: episodeTools,
}, streamFn });
const session = Object.create(AgentSession.prototype);
session.agent = agent;
session.sessionManager = manager;
session.settingsManager = settingsManager;
session.currentExtensionRunner = new ExtensionRunner([], undefined, dist, manager, undefined);
session.getCompactionRequestAuth = async () => ({});
const events = [];
session.emit = (event) => { events.push(event); };
// Stock constructor initialization (resource-loader:8285,8330).
session.overflowRecoveryAttempts = inputData.overflow_recovery_attempts;
session.contextOverflowRecoveryOwner = "session";
let trigger = initialContext.findLast((msg) => msg.role === "assistant");
if (inputData.reason === "overflow") {
  // Replay the scripted provider refusal through stock's assistant factory.
  trigger = { ...createAssistantOutput(model), stopReason: "error", errorMessage: "context length exceeded" };
  agent.state.messages = [...agent.state.messages, trigger];
}
const retry = await session.checkCompaction(trigger);
const completion = events.findLast((event) => event.type === "compaction_end");
if (!completion || completion.outcome.status !== "completed") {
  const output = JSON.stringify({
    status: completion ? completion.outcome.status : "not_triggered",
    outcome: completion?.outcome, retry, captured_requests: capturedRequests,
    messages: [...(system ? [system] : []), ...agent.state.messages],
  });
  await new Promise((resolve, reject) => process.stdout.write(output,
    (error) => error ? reject(error) : resolve()));
  process.exit(0);
}
const compacted = agent.state.messages;
const summary = compacted.find((msg) => msg.role === "compactionSummary");
const boundary = manager.getBranch().findLast((entry) => entry.type === "compaction");
let wireHistory = compacted;
if (inputData.runtime_wire_message) {
  const initialUser = compacted.find((msg) => msg.role === "user");
  if (initialUser && !Number.isFinite(initialUser.timestamp)) initialUser.timestamp = inputData.message_timestamp_ms;
  wireHistory = normalizeMessagesForLlmBoundary(compacted, { timezone: "UTC", includeTimestamp: true });
}
const wire = transport.t(model, {
  systemPrompt: agent.state.systemPrompt, messages: convertToLlm(wireHistory), tools: episodeTools,
});
if (inputData.runtime_wire_message) wire.messages.push(inputData.runtime_wire_message);
console.log(JSON.stringify({
  status: "completed", retry,
  first_kept_index: initialContext.indexOf(initialBranch.find((entry) => entry.id === boundary.firstKeptEntryId).message) + (system ? 1 : 0),
  summary: summary.summary, compaction_message: summary, captured_requests: capturedRequests,
  messages: [...(system ? [system] : []), ...compacted],
  wire,
  source_entries: manager.getBranch(),
  settings: settingsManager.getCompactionSettings(),
}));
"""
    proc = subprocess.run(
        ["node", "--import", str(_LOADER), "--input-type=module", "-e", node_script, json.dumps({
            "messages": messages,
            "reason": reason,
            "settings": settings,
            "usage": usage,
            "context_window": context_window,
            "scripted_responses": scripted_responses or [],
            "tools": tools or [],
            "runtime_wire_message": runtime_wire_message,
            "message_timestamp_ms": message_timestamp_ms,
            "source_entries": source_entries,
            "source_messages": source_messages,
            "overflow_recovery_attempts": overflow_recovery_attempts,
            "model_config": model_config or {
                "id": "openclaw-tool-client", "name": "openclaw-tool-client",
                "api": "openai-completions", "provider": "openai", "baseUrl": "http://127.0.0.1",
                "input": ["text"], "contextWindow": context_window, "maxTokens": 2048,
            },
        })],
        cwd=str(_DEFAULT_DIST),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "OPENCLAW_DIST": str(_DEFAULT_DIST)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_stock_equality_basic_cut(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Task turn 0: inspect files"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Inspected files."}]},
        {"role": "user", "content": [{"type": "text", "text": "Task turn 1: edit files"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Edited files."}]},
        {"role": "user", "content": [{"type": "text", "text": "Task turn 2: run tests"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    scripted = ["## Goal\nComplete tasks\n\n## Progress\n### Done\n- [x] Inspected and edited"]
    usage = {"input": 115000, "output": 1000, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 116000}

    stock = _run_stock_compaction_direct(
        messages, reason="threshold", settings=settings, usage=usage,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert len(stock["captured_requests"]) == 1

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "threshold",
        "checkpoint": "agent_end",
        "messages": messages,
        "usage": usage,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"
    worker_requests = [prepared["summary_request"]]
    if prepared.get("turn_prefix_request"):
        worker_requests.append(prepared["turn_prefix_request"])

    # Wire request sequence equality
    assert len(worker_requests) == len(stock["captured_requests"])
    for w_req, s_req in zip(worker_requests, stock["captured_requests"]):
        assert w_req["messages"] == s_req["messages"]
        assert w_req["max_tokens"] == s_req["max_tokens"]

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["first_kept_index"] == stock["first_kept_index"]
    assert finalized["summary"] == stock["summary"]
    assert finalized["compaction_message"] == stock["compaction_message"]
    assert "continuation" not in finalized


def test_stock_equality_split_turn(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Initial request"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Initial reply"}]},
        {"role": "user", "content": [{"type": "text", "text": "Split request"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Large turn running"},
            {"type": "toolCall", "id": "tc1", "name": "exec", "arguments": {"command": "cat big.log"}},
        ]},
        {"role": "toolResult", "toolCallId": "tc1", "toolName": "exec", "isError": False, "content": [{"type": "text", "text": "line\n" * 400}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Second large call"},
            {"type": "toolCall", "id": "tc2", "name": "read", "arguments": {"path": "big.txt"}},
        ]},
        {"role": "toolResult", "toolCallId": "tc2", "toolName": "read", "isError": False, "content": [{"type": "text", "text": "data\n" * 400}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 50}
    scripted = [
        "## Goal\nInitial tasks\n\n## Progress\n- [x] Initial",
        "## Original Request\nSplit request\n\n## Early Progress\n- Executed big.log",
    ]

    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert len(stock["captured_requests"]) == 2

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"
    assert prepared["summary_request"] is not None
    assert prepared["turn_prefix_request"] is not None
    worker_requests = [prepared["summary_request"], prepared["turn_prefix_request"]]

    assert len(worker_requests) == len(stock["captured_requests"])
    for w_req, s_req in zip(worker_requests, stock["captured_requests"]):
        assert w_req["messages"] == s_req["messages"]
        assert w_req["max_tokens"] == s_req["max_tokens"]

    # No exception is needed to suspend at stock's next provider await.
    followup = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert followup["kind"] == "compaction_followup_request"
    assert followup["request"] == stock["captured_requests"][1]

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "turn_prefix_summary": scripted[1],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["first_kept_index"] == stock["first_kept_index"]
    assert finalized["summary"] == stock["summary"]
    assert finalized["compaction_message"] == stock["compaction_message"]


def test_stock_equality_update_previous_summary(worker):
    messages = [
        {
            "role": "compactionSummary",
            "summary": "## Goal\nOriginal goal\n\n## Progress\n- [x] Prior work",
            "tokensBefore": 100,
            "timestamp": 0,
        },
        {"role": "user", "content": [{"type": "text", "text": "New turn: add feature X"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Feature X added"}]},
        {"role": "user", "content": [{"type": "text", "text": "New turn: add feature Y"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    scripted = ["## Goal\nOriginal goal plus X\n\n## Progress\n- [x] Prior work\n- [x] Added feature X"]

    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert len(stock["captured_requests"]) == 1
    stock_user_text = stock["captured_requests"][0]["messages"][1]["content"][0]["text"]
    assert "<previous-summary>" in stock_user_text

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"
    worker_user_text = prepared["summary_request"]["messages"][1]["content"][0]["text"]
    assert "<previous-summary>" in worker_user_text
    assert worker_user_text == stock_user_text

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["summary"] == stock["summary"]
    assert finalized["first_kept_index"] == stock["first_kept_index"]
    assert finalized["compaction_message"] == stock["compaction_message"]


def test_prior_compaction_missing_timestamp_is_not_replaced_with_epoch(worker):
    # session-DVbOtm8K.mjs:49-55,147 requires the persisted timestamp.
    with pytest.raises(RuntimeError, match="compaction summary timestamp"):
        worker.request({
            "phase": "prepare_compaction",
            "reason": "overflow",
            "checkpoint": "overflow",
            "messages": [
                {"role": "compactionSummary", "summary": "Prior work", "tokensBefore": 100},
                *_make_conversation_history(3),
            ],
            "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
        })


def test_prior_compaction_missing_tokens_before_is_preserved(worker):
    prepared = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": [
            {"role": "compactionSummary", "summary": "Prior work", "timestamp": 0},
            *_make_conversation_history(3),
        ],
        "settings": {"reserveTokens": 16384, "keepRecentTokens": 50},
    })
    assert "tokensBefore" not in prepared["preparation"]["entries"][0]


def test_stock_equality_overflow(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "task 0"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "result 0"}]},
        {"role": "user", "content": [{"type": "text", "text": "unresolved request 1"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    scripted = ["## Goal\nTask 0 complete"]

    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=scripted, tools=worker.tool_schemas,
    )
    assert stock["status"] == "completed"
    assert "## Latest unresolved user request" in stock["summary"]

    prepared = worker.request({
        "phase": "prepare_compaction",
        "reason": "overflow",
        "checkpoint": "overflow",
        "messages": messages,
        "settings": settings,
    })
    assert prepared["kind"] == "compaction_prepared"

    finalized = worker.request({
        "phase": "finalize_compaction",
        "summary": scripted[0],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["summary"] == stock["summary"]
    assert "## Latest unresolved user request" in finalized["summary"]
    assert finalized["compaction_message"] == stock["compaction_message"]


def test_stock_safeguard_mode_check():
    # Confirm RL config effective compaction mode is "default", not "safeguard"
    node_script = """
import { pathToFileURL } from "node:url";
const dist = process.env.OPENCLAW_DIST;
const { a: resolveEffectiveCompactionMode } = await import(pathToFileURL(dist + "/agent-settings-DcI_VuTd.mjs"));

// RL configuration has compaction: false and no provider / mode: safeguard
const modeEmpty = resolveEffectiveCompactionMode({});
const modeFalse = resolveEffectiveCompactionMode({ agents: { defaults: { compaction: false } } });
const modeSafeguard = resolveEffectiveCompactionMode({ agents: { defaults: { compaction: { mode: "safeguard" } } } });
console.log(JSON.stringify({ modeEmpty, modeFalse, modeSafeguard }));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=str(_DEFAULT_DIST),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "OPENCLAW_DIST": str(_DEFAULT_DIST)},
    )
    data = json.loads(proc.stdout.strip())
    assert data["modeEmpty"] == "default"
    assert data["modeFalse"] == "default"
    assert data["modeSafeguard"] == "safeguard"


def test_stock_session_context_and_worker_lifecycle_parity(worker):
    messages = [
        {"role": "system", "content": "You are an agent"},
        {"role": "user", "content": [{"type": "text", "text": "step 0"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 0"}]},
        {"role": "user", "content": [{"type": "text", "text": "step 1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 3}
    summary = "## Goal\nComplete steps\n\n## Progress\n- Step 0 completed"
    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings, scripted_responses=[summary, "Current turn"],
        tools=worker.tool_schemas,
    )
    prepared = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages, "settings": settings,
    })
    assert prepared["summary_request"] == stock["captured_requests"][0]
    finalized = worker.request({
        "phase": "finalize_compaction", "summary": summary, "turn_prefix_summary": "Current turn",
        "preparation": prepared["preparation"],
    })
    assert finalized["messages"] == stock["messages"]
    projected = worker.request({"phase": "project_request", "messages": finalized["messages"]})
    assert projected["messages"][:2] == stock["wire"]["messages"][:2]
    messages2 = [
        *finalized["messages"],
        {"role": "user", "content": [{"type": "text", "text": "step 2"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "reply 2"}]},
    ]
    settings2 = {"reserveTokens": 16384, "keepRecentTokens": 2}
    stock2 = _run_stock_compaction_direct(
        messages2, reason="overflow", settings=settings2, scripted_responses=["Updated work", "Current turn"],
        tools=worker.tool_schemas, source_entries=stock["source_entries"],
        source_messages=stock["messages"],
    )
    prepared2 = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages2, "settings": settings2,
    })
    assert prepared2["summary_request"] == stock2["captured_requests"][0]
    assert "<previous-summary>" in prepared2["summary_request"]["messages"][1]["content"][0]["text"]


def test_stock_request_budget_caps_summary_with_episode_tools(tmp_path):
    worker = _WorkerClient(_DEFAULT_DIST, tmp_path / "budget", context_window=32_768)
    try:
        messages = [{"role": "system", "content": "S" * 100_000}, *_make_conversation_history(4)]
        settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
        stock = _run_stock_compaction_direct(
            messages, reason="overflow", settings=settings,
            scripted_responses=["Completed prior work", "Current turn"],
            tools=worker.tool_schemas, model_config=worker.model_config,
        )
        prepared = worker.request({
            "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
            "messages": messages, "settings": settings,
        })
        requests = [prepared[key] for key in ("summary_request", "turn_prefix_request") if prepared.get(key)]
        assert requests == stock["captured_requests"]
        assert requests[0]["max_tokens"] < int(8192 * 0.8)
        finalized = worker.request({
            "phase": "finalize_compaction", "summary": "Completed prior work",
            "turn_prefix_summary": "Current turn", "preparation": prepared["preparation"],
        })
        assert finalized["messages"] == stock["messages"]
    finally:
        worker.close()


def test_summary_trace_preserves_proxy_clamp_near_production_window(worker):
    # Preparation-only synthetic branch: fit/retention bounds do not themselves
    # bound summarization input. R2 uses window131072, reserve20000, cap2048.
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "x" * 250400}]},
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": f"r{i}", "name": "read", "arguments": {"path": f"file{i}.txt"}}
            for i in range(20)
        ]},
        *[
            {"role": "toolResult", "toolCallId": f"r{i}", "toolName": "read",
             "content": [{"type": "text", "text": "漢" * 2000}], "isError": False}
            for i in range(20)
        ],
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": "last", "name": "read", "arguments": {"path": "last.txt"}}
        ]},
        {"role": "toolResult", "toolCallId": "last", "toolName": "read",
         "content": [{"type": "text", "text": "last result"}], "isError": False},
    ]
    settings = {"reserveTokens": 20000, "keepRecentTokens": 20000}
    summary = "Context retained"
    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings, scripted_responses=[summary],
        tools=worker.tool_schemas, model_config=worker.model_config,
    )
    prepared = worker.request({
        "phase": "prepare_compaction", "reason": "overflow", "checkpoint": "overflow",
        "messages": messages, "settings": settings,
    })
    assert prepared["summary_request"] is None
    assert prepared["turn_prefix_request"] == stock["captured_requests"][0]
    assert prepared["turn_prefix_request"]["max_tokens"] == 1985
    finalized = worker.request({
        "phase": "finalize_compaction", "summary": None, "turn_prefix_summary": summary,
        "preparation": prepared["preparation"],
    })
    assert finalized["messages"] == stock["messages"]


def test_invalid_summary_retries_once_with_real_stock_work(worker):
    messages = _make_conversation_history(3)
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    stock = _run_stock_compaction_direct(
        messages, reason="overflow", settings=settings,
        scripted_responses=["", "Recovered summary", "Turn prefix"],
        tools=worker.tool_schemas,
    )
    result, phases, exchanges = _compact_in_production_phases(
        worker, messages, reason="overflow", settings=settings,
        answers=["", "Turn prefix", "Recovered summary"],
    )
    finalized_payloads = [payload for operation, payload in phases if operation == "finalize_compaction"]
    assert finalized_payloads[0]["summary"] == ""
    assert finalized_payloads[0]["turn_prefix_summary"] == "Turn prefix"
    assert finalized_payloads[1]["followup_summaries"] == ["Recovered summary"]
    assert [request["messages"] for request in exchanges] == [
        stock["captured_requests"][0]["messages"],
        stock["captured_requests"][2]["messages"],  # prefetched prefix, not the history retry
        stock["captured_requests"][1]["messages"],
    ]
    assert result.messages == stock["messages"]


@pytest.mark.parametrize("overflow_mode", ["http_error", "length"])
def test_real_conductor_compacts_production_history_and_preserves_main_wire(tmp_path, overflow_mode):
    """Use unchanged openclaw/2026.9.4-r2 catalog bytes and native 131072 sizing.

    The shared fixture's providers.models[].context_length and provider profile's
    context_window are set to 131072 (rather than its unrelated 32768 default).
    No projection semantics, reserve or keepRecentTokens are overridden. The
    inherited chat_tools array thaw preserves JSON and asserts every other
    projection field unchanged.
    The length case uses stock's zero-output, 99%-window overflow branch
    (@openclaw/ai/dist/internal/runtime.mjs:195-197), without an HTTP failure.
    """
    import asyncio
    from tests.rl.harness.test_openclaw_native_stream_conductor import (
        RunnerTermination, _NativeWorkerPort, _run_episode,
    )
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    from breadboard.rl.harness.runners.openclaw_semantics import OPENCLAW_CONSUMER_ID
    summary_prompt = NATIVE_STREAM_PROFILES[OPENCLAW_CONSUMER_ID].compaction_summary_system_prompt
    import yaml
    catalog_path = Path(__file__).parents[3] / "config/e4_targets/openclaw/2026.9.4-r2/harness.yaml"
    catalog = yaml.safe_load(catalog_path.read_text())
    # Both evidence envelopes use the unchanged catalog's per-outcome byte cap,
    # rather than the shared fixture's smaller unrelated observation default.
    evidence_bytes = catalog["policy"]["execution"]["bounded_controls"]["raw_evidence_bytes_per_outcome"]

    ports = []

    class RecordingWorker(_NativeWorkerPort):
        def __init__(self, workspace, grants):
            super().__init__(workspace, grants)
            self.compactions = []
            self.compaction_outcomes = []
            ports.append(self)

        def native_runtime_inputs(self, *, input_names, package_subpath):
            runtime = dict(super().native_runtime_inputs(
                input_names=input_names, package_subpath=package_subpath,
            ))
            runtime.update(current_date="2026-10-09", message_timestamp_ms="1728432000000")
            return runtime

        async def invoke_native_phase(self, operation, payload, *, timeout_ms, package_subpath=None):
            result = await super().invoke_native_phase(
                operation, payload, timeout_ms=timeout_ms, package_subpath=package_subpath,
            )
            if operation in {"prepare_compaction", "finalize_compaction"}:
                self.compaction_outcomes.append((operation, result.get("kind"), result.get("reason")))
            if operation == "initialize":
                self.model_config = json.loads(json.dumps(payload["model_config"]))
                self.tool_schemas = result["tool_schemas"]
            elif operation == "prepare_compaction" and result["kind"] == "compaction_prepared":
                self.compactions.append({"input": json.loads(json.dumps(payload)), "prepared": result})
            elif operation == "finalize_compaction" and result["kind"] == "compaction_finalized":
                self.compactions[-1].update(
                    answers=json.loads(json.dumps(payload)), finalized=result,
                    next_wire=await super().invoke_native_phase(
                        "project_request", {"messages": result["messages"]}, timeout_ms=timeout_ms,
                    ),
                )
            return result

    main_requests = []
    summary_responses = []
    for number in range(1, 7):
        (tmp_path / f"observed{number}.txt").write_text(f"Native tool observation {number}\n" * 800)

    def responses(_ordinal, requests):
        request = requests[-1]
        if request["messages"][0]["content"] == summary_prompt:
            summary = "" if not summary_responses else f"Native summary {len(summary_responses)}"
            summary_responses.append(summary)
            return {"content": summary}
        main_requests.append(request)
        if len(main_requests) <= 5:
            number = len(main_requests)
            return {"calls": [(f"callread{number}", "read", {"path": f"observed{number}.txt"})],
                    "usage": {"prompt_tokens": number * 5_000, "completion_tokens": 10,
                              "total_tokens": number * 5_000 + 10}}
        if len(main_requests) == 6:
            if overflow_mode == "length":
                return {"content": "", "finish_reason": "length", "usage": {
                    "prompt_tokens": 131_072, "completion_tokens": 0, "total_tokens": 131_072,
                }}
            return {"http_error": 400, "body": {"error": {
                "code": "context_length_exceeded", "message": "context length exceeded",
            }}}
        if len(main_requests) == 7:
            return {"calls": [("callretry", "read", {"path": "observed6.txt"})],
                    "usage": {"prompt_tokens": 30_000, "completion_tokens": 10, "total_tokens": 30_010}}
        assert len(main_requests) == 8
        return {"content": "Done inspecting everything", "usage": {
            "prompt_tokens": 115_000, "completion_tokens": 20, "total_tokens": 115_020,
        }}

    result, _requests, _events, system_prompt, _operations = asyncio.run(_run_episode(
        tmp_path, responses, worker_factory=RecordingWorker,
        target_id="openclaw-r2@2026.9.4", context_window=131_072,
        evidence_limits={"observation_bytes": evidence_bytes, "transcript_bytes": evidence_bytes},
    ))
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    port = ports[0]
    assert len(main_requests) == 8, (len(main_requests), port.compaction_outcomes)
    assert [record["input"]["checkpoint"] for record in port.compactions] == [
        "agent_end" if overflow_mode == "length" else "overflow", "agent_end",
    ], port.compaction_outcomes
    assert [record["finalized"]["reason"] for record in port.compactions] == ["overflow", "threshold"]
    assert [record["finalized"]["retry"] for record in port.compactions] == [True, False]
    if overflow_mode == "length":
        cutoff = port.compactions[0]["input"]["messages"][-1]
        assert cutoff["stopReason"] == "length"
        assert (cutoff["api"], cutoff["provider"], cutoff["model"]) == (
            port.model_config["api"], port.model_config["provider"], port.model_config["id"],
        )
        assert cutoff not in port.compactions[0]["finalized"]["messages"]
    assert any(
        message["role"] == "toolResult" and message["content"]
        for message in port.compactions[0]["input"]["messages"]
    )
    terminal = port.compactions[1]["input"]["messages"][-1]
    assert terminal["content"] == [{"type": "text", "text": "Done inspecting everything"}]
    retained = [
        message for message in port.compactions[0]["finalized"]["messages"]
        if message["role"] == "assistant" and message.get("providerUsage")
    ]
    assert retained
    assert all(message["providerUsage"]["prompt_tokens"] > 0 for message in retained)
    assert all(message["usage"]["totalTokens"] == 0 for message in retained)
    source_entries = None
    source_messages = None
    for record in port.compactions:
        prepared, answers = record["prepared"], record["answers"]
        scripted = []
        if prepared["summary_request"] is not None:
            scripted.append(answers["summary"])
        if prepared["turn_prefix_request"] is not None:
            scripted.append(answers["turn_prefix_summary"])
        scripted.extend(answers.get("followup_summaries", []))
        stock = _run_stock_compaction_direct(
            record["input"]["messages"], reason=record["input"]["reason"],
            settings=record["input"].get("settings"), usage=record["input"].get("usage"),
            scripted_responses=scripted, tools=port.tool_schemas, model_config=port.model_config,
            # The initial wire's source-rendered runtime fragment is fixed episode context.
            runtime_wire_message=main_requests[0]["messages"][-1],
            message_timestamp_ms=1728432000000,
            source_entries=source_entries,
            source_messages=source_messages,
        )
        source_entries = stock["source_entries"]
        source_messages = stock["messages"]
        assert stock["settings"] == {"enabled": True, "reserveTokens": 20000, "keepRecentTokens": 20000}
        assert record["finalized"]["messages"] == stock["messages"]
        assert record["finalized"]["messages"][0] == {"role": "system", "content": system_prompt}
        assert record["next_wire"]["tools"] == stock["wire"]["tools"]
        if record["finalized"]["reason"] == "overflow":
            # AgentSession owns durable history, not the embedded caller's
            # transient continuation (embedded-agent-CE9KzQvy.mjs:5898,
            # 5961-5967,6063-6068). The real agent-exec lane pins its exact wire.
            next_messages = record["next_wire"]["messages"]
            assert next_messages[:-2] == stock["wire"]["messages"][:-1]
            assert next_messages[-1] == stock["wire"]["messages"][-1]
            assert next_messages[-2]["role"] == "user"
            assert main_requests[6]["messages"] == next_messages
        else:
            assert record["next_wire"]["messages"] == stock["wire"]["messages"]
            assert stock["retry"] is False
            assert "continuation" not in record["finalized"]


def _compact_in_production_phases(worker, messages, *, reason, settings, answers, usage=None):
    """Exercise the real conductor phase payloads, replacing only provider answers."""
    import asyncio
    from breadboard.rl.harness.runners.native_compaction import compact_history
    from breadboard.rl.harness.runners.base import thaw_json

    phases, exchanges = [], []
    scripted = iter(answers)

    async def phase(operation, payload):
        phases.append((operation, json.loads(json.dumps(payload))))
        return worker.request({"phase": operation, **payload})

    async def exchange(request):
        exchanges.append(thaw_json(request))
        return {"native_response": {"content": next(scripted)}}, {"request": thaw_json(request)}

    result = asyncio.run(compact_history(
        messages, reason=reason, checkpoint="overflow" if reason == "overflow" else "agent_end",
        usage=usage, context_window=worker.model_config["contextWindow"], settings=settings,
        model_id=worker.model_config["id"], tools=worker.tool_schemas,
        phase=phase, exchange=exchange, trace_requests=[],
    ))
    return result, phases, exchanges


def test_missing_provider_usage_does_not_trigger_native_threshold(worker):
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Large context " * 40_000}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done"}], "stopReason": "stop"},
    ]
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    stock = _run_stock_compaction_direct(
        messages, reason="threshold", settings=settings, scripted_responses=[],
        tools=worker.tool_schemas,
    )
    assert stock["status"] == "not_triggered"
    assert stock["captured_requests"] == []
    original = json.loads(json.dumps(messages))
    result, phases, exchanges = _compact_in_production_phases(
        worker, messages, reason="threshold", settings=settings, answers=[],
    )
    assert result is None
    assert [operation for operation, _payload in phases] == ["prepare_compaction"]
    assert exchanges == []
    assert messages == original


def test_failed_native_threshold_settles_with_original_history(worker):
    messages = _make_conversation_history(3)
    settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
    usage = {"input": 115000, "output": 20, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 115020}
    stock = _run_stock_compaction_direct(
        messages, reason="threshold", settings=settings, usage=usage,
        scripted_responses=["", ""], tools=worker.tool_schemas,
    )
    assert stock["status"] == "failed"
    assert stock["retry"] is False
    original = json.loads(json.dumps(messages))
    result, phases, exchanges = _compact_in_production_phases(
        worker, messages, reason="threshold", settings=settings, usage=usage,
        answers=["", "Unused prefix", ""],
    )
    assert result is None
    assert messages == original
    finalize_payloads = [payload for operation, payload in phases if operation == "finalize_compaction"]
    assert finalize_payloads[-1]["followup_summaries"] == [""]
    assert [request["messages"] for request in exchanges if request["messages"] == stock["captured_requests"][0]["messages"]] == [
        request["messages"] for request in stock["captured_requests"]
    ]


def test_prepare_suspends_before_fitting_a_tiny_summary_budget(tmp_path):
    """Explicit test model contextWindow=32768; stock sizes every request and summary."""
    worker = _WorkerClient(_DEFAULT_DIST, tmp_path / "tiny-budget", context_window=32_768)
    try:
        history = [
            {"role": "user", "content": [{"type": "text", "text": "Prior completed task"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "A" * 40_000}], "stopReason": "stop"},
            {"role": "user", "content": [{"type": "text", "text": "Next task"}]},
        ]
        settings = {"reserveTokens": 16384, "keepRecentTokens": 100}
        usage = {"input": 30000, "output": 20, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 30020}
        low, high = 0, worker.model_config["contextWindow"] * 4
        probes = []
        for _ in range(20):
            size = (low + high) // 2
            messages = [{"role": "system", "content": "S" * size}, *history]
            prepared = worker.request({
                "phase": "prepare_compaction", "reason": "threshold", "checkpoint": "agent_end",
                "messages": messages, "usage": usage, "settings": settings,
            })
            if prepared["kind"] != "compaction_prepared":
                probes.append((size, prepared.get("reason")))
                high = size - 1
                continue
            request_budget = prepared["preparation"]["requestBudget"]
            # Stay before stock's history-only crossover (resource-loader:8889-8892).
            if request_budget["fixedTokens"] + request_budget["pendingTokens"] >= request_budget["contextWindow"] - request_budget["reserveTokens"]:
                high = size - 1
                continue
            budget = prepared["preparation"]["summaryTokenBudget"]
            probes.append((size, budget))
            if 1 <= budget <= 2:
                break
            if budget > 2:
                low = size + 1
            else:
                high = size - 1
        else:
            pytest.fail(f"No stock-sized one/two-token summary boundary: {probes}")
        assert prepared["turn_prefix_request"] is None
        stock = _run_stock_compaction_direct(
            messages, reason="threshold", settings=settings, usage=usage,
            scripted_responses=["x"], tools=worker.tool_schemas, model_config=worker.model_config,
        )
        assert stock["status"] == "completed"
        result, _phases, _exchanges = _compact_in_production_phases(
            worker, messages, reason="threshold", settings=settings, usage=usage, answers=["x"],
        )
        assert result.messages == stock["messages"]
    finally:
        worker.close()


@pytest.mark.parametrize("reason,reported_usage", [("threshold", False), ("threshold", True), ("overflow", True)])
def test_native_threshold_gates_or_frames_planner_no_fit(tmp_path, reason, reported_usage):
    """Explicit contextWindow=32768; stock admits or declines the oversized prompt."""
    worker = _WorkerClient(_DEFAULT_DIST, tmp_path / "no-fit", context_window=32_768)
    try:
        sizing_script = """
import { pathToFileURL } from "node:url";
const dist = process.env.OPENCLAW_DIST;
const { f: budget, rt: SettingsManager } = await import(pathToFileURL(dist + "/resource-loader-Bu_pVD2t.mjs"));
const { r: applySettings } = await import(pathToFileURL(dist + "/agent-settings-DcI_VuTd.mjs"));
const data = JSON.parse(process.argv[1]);
const settings = SettingsManager.inMemory({ compaction: data.settings });
applySettings({ settingsManager: settings, contextTokenBudget: data.window });
let low = 0, high = data.window * 4, size = 0;
while (low <= high) {
  const mid = Math.floor((low + high) / 2);
  const request = budget({
    contextWindow: data.window, reserveTokens: settings.getCompactionSettings().reserveTokens,
    systemPrompt: "S".repeat(mid), tools: data.tools.map((tool) => tool.function),
  });
  if (request.fixedTokens + request.pendingTokens < request.contextWindow - request.reserveTokens) {
    size = mid; low = mid + 1;
  } else high = mid - 1;
}
console.log(JSON.stringify(size));
"""
        sizing = subprocess.run(
            ["node", "--input-type=module", "-e", sizing_script, json.dumps({
                "settings": {"reserveTokens": 16384, "keepRecentTokens": 5},
                "window": worker.model_config["contextWindow"], "tools": worker.tool_schemas,
            })], capture_output=True, text=True,
            env={**os.environ, "OPENCLAW_DIST": str(_DEFAULT_DIST)}, check=True,
        )
        # Actual stock createCompactionRequestBudget picks the last positive
        # foreground history budget, avoiding its history-only fallback (8889-8892).
        system_size = json.loads(sizing.stdout)
        messages = [
            {"role": "system", "content": "S" * system_size},
            {"role": "user", "content": [{"type": "text", "text": "Completed task"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Done " * 200}], "stopReason": "stop"},
        ]
        settings = {"reserveTokens": 16384, "keepRecentTokens": 5}
        usage = {"input": 30000, "output": 20, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 30020} if reported_usage else None
        stock = _run_stock_compaction_direct(
            messages, reason=reason, settings=settings, usage=usage, scripted_responses=[],
            tools=worker.tool_schemas, model_config=worker.model_config,
        )
        assert stock["status"] == ("failed" if reported_usage else "not_triggered")
        assert stock["captured_requests"] == []
        assert stock["retry"] is False
        if reported_usage:
            assert "No complete recent message fits" in stock["outcome"]["reason"]
        original = json.loads(json.dumps(messages))
        result, phases, exchanges = _compact_in_production_phases(
            worker, messages, reason=reason, settings=settings, usage=usage, answers=[],
        )
        assert result is None
        assert [operation for operation, _payload in phases] == ["prepare_compaction"]
        assert exchanges == []
        assert messages == original
        if reason == "overflow":
            # Repeat the admitted overflow through the same worker. The source
            # counts failed recovery passes too and declines the fourth before
            # making any provider request (resource-loader:10094-10109).
            for attempts in (1, 2, 3):
                stock = _run_stock_compaction_direct(
                    messages, reason=reason, settings=settings, usage=usage, scripted_responses=[],
                    tools=worker.tool_schemas, model_config=worker.model_config,
                    overflow_recovery_attempts=attempts,
                )
                actual = worker.request({"phase": "prepare_compaction", **phases[0][1]})
                assert actual["kind"] == "compaction_unavailable"
                assert actual["reason"] == stock["outcome"]["reason"]
                assert stock["retry"] is False
                assert stock["captured_requests"] == []
            assert "after 3 compact-and-retry attempts" in actual["reason"]
    finally:
        worker.close()
