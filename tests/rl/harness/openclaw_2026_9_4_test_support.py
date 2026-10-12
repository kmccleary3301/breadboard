from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import uuid
import pytest

from tests.rl.harness.native_dependencies import require_openclaw, resolve_openclaw

_WORKER = Path(__file__).parents[3] / "breadboard/rl/harness/openclaw_tool_worker.mjs"


_LOADER = _WORKER.with_name("openclaw_classifier_loader.mjs")


_DEFAULT_DIST = resolve_openclaw()


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


class _WorkerClient:
    def __init__(
        self, dist_path: Path, workspace: Path | None = None,
        initialize: bool = True, context_window: int = 131_072,
    ):
        require_openclaw()
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
    client = _WorkerClient(require_openclaw(), workspace=tmp_path / "workspace")
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
    require_openclaw()
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
  capturedRequests.push({
    messages: wire.messages,
    max_tokens: wire.max_completion_tokens ?? wire.max_tokens,
    request_body: wire,
    request_body_json: JSON.stringify(wire),
  });
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

    async def exchange(request, native_request_body_json=None):
        exchanges.append(thaw_json(request))
        return {"native_response": {"content": next(scripted)}}, {"request": thaw_json(request)}

    result = asyncio.run(compact_history(
        messages, reason=reason, checkpoint="overflow" if reason == "overflow" else "agent_end",
        usage=usage, context_window=worker.model_config["contextWindow"], settings=settings,
        model_id=worker.model_config["id"], tools=worker.tool_schemas,
        phase=phase, exchange=exchange, trace_requests=[],
    ))
    return result, phases, exchanges


@pytest.fixture
def stock_runtime(monkeypatch: pytest.MonkeyPatch):
    dist = require_openclaw()
    monkeypatch.setenv("OPENCLAW_DIST", str(dist))
    return dist
