"""Stock oracle byte-equality tests for Pi 0.73.1 and 0.57.1 context compaction.

Compares the worker with real stock AgentSession and SessionManager:
(a) every summary wire request (messages and max_tokens) between worker and stock;
(b) post-compaction context: stock buildSessionContext -> convertToLlm -> convertMessages
    vs the worker's rebuilt messages through projectRequest.

Cases tested for both versions:
- overflow
- threshold
- split turn (history + turn prefix)
- previous-summary update (second compaction)
- tool calls and results straddling the cut
- agent_end over threshold (triggers summary request)
- agent_end under threshold (declines compaction: not_triggered)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
import subprocess
from typing import Any, Mapping

import pytest

_NODE_MODULES_0_73_1 = Path(
    os.environ.get(
        "PI_CODING_AGENT_NODE_MODULES",
        "/Users/kylemccleary/projects/breadboard-compaction-ref-20261009/node_modules",
    )
)
_NODE_MODULES_0_57_1 = Path(
    os.environ.get(
        "PI057_CODING_AGENT_NODE_MODULES",
        "/Users/kylemccleary/.cache/bb-compaction-e4/pkgs/@mariozechner__pi-coding-agent@0.57.1/node_modules",
    )
)
_WORKER_0_73_1 = (
    Path(__file__).resolve().parents[3]
    / "breadboard"
    / "rl"
    / "harness"
    / "pi_tools_0_73_1.mjs"
)
_WORKER_0_57_1 = (
    Path(__file__).resolve().parents[3]
    / "breadboard"
    / "rl"
    / "harness"
    / "pi_tools_0_57_1.mjs"
)


class FramedWorkerClient:
    """Async client communicating with the framed Node.js worker over stdin/stdout."""

    def __init__(self, worker_script: Path, node_modules: Path, workspace: Path) -> None:
        self.worker_script = worker_script
        self.node_modules = node_modules
        self.workspace = workspace
        self.scratch = workspace / ".scratch"
        self.scratch.mkdir(parents=True, exist_ok=True)
        (self.scratch / "home").mkdir(parents=True, exist_ok=True)
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    async def start(self, target_version: str) -> dict[str, Any]:
        env = dict(os.environ)
        env["PI_NATIVE_WORKER_FRAMED"] = "1"
        env["PI_CODING_AGENT_NODE_MODULES"] = str(self.node_modules)
        node_bin = subprocess.check_output(["which", "node"], text=True).strip()
        env["PATH"] = f"{Path(node_bin).parent}:/usr/bin:/bin"

        self._process = await asyncio.create_subprocess_exec(
            "node",
            str(self.worker_script),
            env=env,
            cwd=self.workspace,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        model_config = {
            "id": "test-model",
            "name": "test-model",
            "api": "openai-completions",
            "provider": "openai",
            "baseUrl": "http://127.0.0.1:8000/v1",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 131072,
            "maxTokens": 2048,
            "compat": {
                "supportsStore": False,
                "supportsDeveloperRole": False,
                "supportsReasoningEffort": False,
                "supportsUsageInStreaming": True,
                "maxTokensField": "max_tokens",
                "supportsStrictMode": True,
            },
        }
        package_dir = str(self.node_modules / "@mariozechner" / "pi-coding-agent")
        if target_version == "0.73.1":
            config_path = (
                Path(__file__).resolve().parents[3]
                / "config/e4_targets/pi/0.73.1/native-config.json"
            )
            native_config = json.loads(config_path.read_text(encoding="utf-8"))
            advertisement = native_config["advertisement"]
            runtime_inputs = {
                "cwd": str(self.workspace),
                "home": str(self.scratch / "home"),
                "current_date": datetime.now(timezone.utc).date().isoformat(),
                "package_dir": package_dir,
            }
        else:
            advertisement = {}
            runtime_inputs = {
                "cwd": str(self.workspace),
                "home": str(self.scratch / "home"),
                "package_dir": package_dir,
            }
        init_payload = {
            "task": "Test stock oracle compaction",
            "model_config": model_config,
            "advertisement": advertisement,
            "runtime_inputs": runtime_inputs,
            "workspace": str(self.workspace),
            "scratch": str(self.scratch),
            "package_dir": package_dir,
        }
        return await self.invoke("initialize", init_payload)
    async def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._process and self._process.stdin and self._process.stdout
        self._request_id += 1
        cmd = {
            "schema_version": "bb.native-worker.rpc.v1",
            "request_id": self._request_id,
            "operation": operation,
            "payload": payload,
        }
        body = json.dumps(cmd, separators=(",", ":")).encode("utf-8")
        self._process.stdin.write(struct.pack(">I", len(body)) + body)
        await self._process.stdin.drain()
        header = await asyncio.wait_for(self._process.stdout.readexactly(4), 15.0)
        size = struct.unpack(">I", header)[0]
        resp = json.loads(
            (await asyncio.wait_for(self._process.stdout.readexactly(size), 15.0)).decode("utf-8")
        )
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]

    async def close(self) -> None:
        if self._process is not None:
            if self._process.returncode is None:
                self._process.kill()
            await self._process.wait()
            self._process = None



# This oracle never imports the BreadBoard worker. Its entry tree is built by
# the actual SessionManager, and its trigger is the actual AgentSession method.
_STOCK_ORACLE = r"""
import fs from 'node:fs';
import crypto from 'node:crypto';
import { syncBuiltinESMExports } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let idCounter = 0;
crypto.randomUUID = () => `${(idCounter++).toString(16).padStart(8, '0')}-0000-0000-0000-000000000000`;
syncBuiltinESMExports();
const NativeDate = Date;
let clock = 1000;
globalThis.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [clock])); }
  static now() { return clock; }
};
const imp = (pkg, sub) => import(pathToFileURL(resolve(input.node_modules, pkg, sub)).href);
const { SessionManager } = await imp('@mariozechner/pi-coding-agent', 'dist/core/session-manager.js');
const { AgentSession } = await imp('@mariozechner/pi-coding-agent', 'dist/core/agent-session.js');
const { prepareCompaction, compact } = await imp('@mariozechner/pi-coding-agent', 'dist/core/compaction/index.js');
const { convertToLlm } = await imp('@mariozechner/pi-coding-agent', 'dist/core/messages.js');
const { convertMessages, streamSimpleOpenAICompletions } = await imp('@mariozechner/pi-ai', 'dist/providers/openai-completions.js');
const { registerApiProvider, unregisterApiProviders } = await imp('@mariozechner/pi-ai', 'dist/api-registry.js');
const { AssistantMessageEventStream } = await imp('@mariozechner/pi-ai', 'dist/utils/event-stream.js');
const { Agent } = await imp('@mariozechner/pi-agent-core', 'dist/index.js');
const sm = SessionManager.inMemory();
idCounter = 0;
const steps = [];
let overflowRecoveryAttempted = false;
for (const step of input.steps) {
  for (const message of step.messages) {
    ++clock;
    let stamped = { ...message, timestamp: clock };
    // Verbatim lifecycle reset: agent-session.js 0.73.1:313-317,274;
    // 0.57.1:223-227,186. The check itself runs the real AgentSession method.
    if (message.role === 'user' || (message.role === 'assistant' && message.stopReason !== 'error')) {
      overflowRecoveryAttempted = false;
    }
    if (message.role === 'assistant') {
      // The fixtures script provider deltas, not invented AssistantMessages.
      // Both versions initialize zero usage even with no usage chunk.
      let toolIndex = 0;
      const chunks = message.content.map((block) => ({
        choices: [{ index: 0, delta: block.type === 'text' ? { content: block.text } : {
          tool_calls: [{ index: toolIndex++, id: block.id, type: 'function',
            function: { name: block.name, arguments: JSON.stringify(block.arguments) } }],
        } }],
      }));
      chunks.push({ choices: [{ index: 0, delta: {},
        finish_reason: message.stopReason === 'toolUse' ? 'tool_calls' : message.stopReason }] });
      if (step.usage) chunks.push({ choices: [], usage: step.usage });
      globalThis.fetch = async () => new Response(
        chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join('') + 'data: [DONE]\n\n',
        { headers: { 'content-type': 'text/event-stream' } });
      const stream = streamSimpleOpenAICompletions(input.model, { messages: [] }, { apiKey: 'EMPTY' });
      for await (const event of stream) {}
      stamped = await stream.result();
      if (stamped.stopReason === 'error') throw new Error(stamped.errorMessage);
    }
    sm.appendMessage(stamped);
  }
  let errorMessage;
  if (step.error) {
    ++clock;
    globalThis.fetch = async () => new Response(JSON.stringify(step.error.body), {
      status: step.error.status, headers: { 'content-type': 'application/json' },
    });
    const stream = streamSimpleOpenAICompletions(input.model,
      { systemPrompt: input.system_prompt, messages: convertToLlm(sm.buildSessionContext().messages) },
      { apiKey: 'EMPTY', maxRetries: 0 });
    for await (const event of stream) {}
    errorMessage = await stream.result();
    sm.appendMessage(errorMessage);
  }
  const context = sm.buildSessionContext().messages;
  let triggered = step.checkpoint !== 'agent_end' && !step.error;
  let compactionReason = triggered ? 'overflow' : undefined;
  let retry = triggered;
  let unavailableReason = 'not_triggered';
  if (!triggered) {
    const host = {
      model: { ...input.model, contextWindow: step.context_window },
      settingsManager: { getCompactionSettings: () => step.settings },
      sessionManager: sm, agent: new Agent({ initialState: { messages: context, model: input.model } }),
      _overflowRecoveryAttempted: overflowRecoveryAttempted,
      _emit: (event) => { if (event.willRetry === false) unavailableReason = 'overflow_retry_exhausted'; },
      _runAutoCompaction: async (reason, willRetry) => {
        triggered = true; compactionReason = reason; retry = willRetry;
      },
    };
    await AgentSession.prototype._checkCompaction.call(host, context.findLast((m) => m.role === 'assistant'));
    overflowRecoveryAttempted = host._overflowRecoveryAttempted;
  } else { overflowRecoveryAttempted = true; }
  if (!triggered) { steps.push({ triggered: false, errorMessage, unavailableReason }); continue; }
  const branchBefore = structuredClone(sm.getBranch());
  const prep = prepareCompaction(sm.getBranch(), step.settings);
  if (!prep) { steps.push({ triggered: false, errorMessage }); continue; }
  const split = prep.isSplitTurn && prep.turnPrefixMessages.length > 0;
  const kinds = split ? [...(prep.messagesToSummarize.length ? ['history'] : []), 'prefix'] : ['history'];
  const requests = {};
  let callIndex = 0;
  registerApiProvider({ api: 'oracle', streamSimple: (model, ctx, options) => {
    const kind = kinds[callIndex++];
    requests[kind] = { messages: convertMessages(input.model, ctx, input.model.compat), max_tokens: options.maxTokens };
    const stream = new AssistantMessageEventStream();
    stream.push({ type: 'done', message: { role: 'assistant', content: [{ type: 'text', text: step.responses[kind] }], stopReason: 'stop' } });
    return stream;
  } }, 'oracle');
  ++clock; // worker prepare calls compact through its capture seam
  ++clock; // worker finalize replays the same stock provider calls
  const result = input.version === '0.73.1'
    ? await compact(prep, { ...input.model, api: 'oracle' }, undefined, undefined, undefined, undefined, undefined)
    : await compact(prep, { ...input.model, api: 'oracle' }, undefined, undefined, undefined);
  unregisterApiProviders('oracle');
  ++clock;
  sm.appendCompaction(result.summary, result.firstKeptEntryId, result.tokensBefore, result.details, false);
  let messages = sm.buildSessionContext().messages;
  if (compactionReason === 'overflow' && messages.at(-1)?.stopReason === 'error') messages = messages.slice(0, -1);
  if (retry) {
    retry = false;
    const agent = new Agent({
      initialState: { model: input.model, messages: structuredClone(messages) }, convertToLlm,
      streamFn: (model, context, options) => {
        retry = true;
        return streamSimpleOpenAICompletions(model, context, {
          ...options, apiKey: 'EMPTY',
          onPayload: () => { throw new Error('scripted provider replay boundary'); },
        });
      },
    });
    // Actual stock continuation, including its empty-queue assistant refusal.
    await agent.continue().catch(() => {});
  }
  steps.push({ triggered: true, reason: compactionReason, retry, errorMessage, requests, result, messages, branchBefore,
    preparation: { ...prep, fileOps: { read: [...prep.fileOps.read], written: [...prep.fileOps.written], edited: [...prep.fileOps.edited] } },
    postWire: convertMessages(input.model, { systemPrompt: input.system_prompt, messages: convertToLlm(messages) }, input.model.compat) });
}
console.log(JSON.stringify(steps));
"""


def _assistant(text: str, timestamp: int, calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message = {
        "role": "assistant", "content": [{"type": "text", "text": text}, *(calls or [])],
        "api": "openai-completions", "provider": "openai", "model": "test-model",
        "stopReason": "toolUse" if calls else "stop", "timestamp": timestamp,
    }
    return message


def _call(call_id: str) -> dict[str, Any]:
    return {"type": "toolCall", "id": call_id, "name": "read", "arguments": {"path": f"{call_id}.txt"}}


def _result(call_id: str, text: str, timestamp: int) -> dict[str, Any]:
    return {"role": "toolResult", "toolCallId": call_id, "toolName": "read",
            "content": [{"type": "text", "text": text}], "isError": False, "timestamp": timestamp}


def _step(messages: list[dict[str, Any]], *, checkpoint: str = "overflow",
          keep: int = 150, error: bool = False) -> dict[str, Any]:
    return {
        "messages": messages, "checkpoint": checkpoint, "context_window": 10000,
        "settings": {"enabled": True, "reserveTokens": 2000, "keepRecentTokens": keep},
        "responses": {"history": "## Goal\nStock history summary", "prefix": "## Original Request\nStock turn prefix"},
        **({"error": {"status": 400, "body": {"error": {"message": "maximum context length exceeded", "type": "invalid_request_error", "code": "context_length_exceeded"}}}} if error else {}),
    }


def _scenario(name: str) -> list[dict[str, Any]]:
    prior = [
        {"role": "user", "content": "Earlier task " + "prior " * 500, "timestamp": 1000},
        _assistant("Earlier answer " + "answer " * 500, 1001),
    ]
    turn = [
        {"role": "user", "content": "Read and analyze " + "instruction " * 100, "timestamp": 1002},
        _assistant("First read", 1003, [_call("c1")]),
        _result("c1", "file data " * 300, 1004),
        _assistant("Second read", 1005, [_call("c2")]),
        _result("c2", "small result", 1006),
    ]
    if name == "huge_tool_overflow":
        return [_step(prior + turn[:2] + [_result("c1", "huge result " * 4000, 1004)], error=True)]
    if name == "overflow_retry_threshold":
        overflow = _scenario("huge_tool_overflow")[0]
        retry = _scenario("agent_end_over_threshold")[0]
        # BB's Pi057 state caches the original query timestamp across retry.
        # The source provider constructs this *new* message after compaction.
        retry["messages"] = [retry["messages"][-1]]
        retry["messages"][0]["timestamp"] = 1000
        return [overflow, retry]
    if name == "split_turn":
        return [_step(prior + turn)]
    if name == "tool_calls_straddling_cut":
        return [_step(prior + turn, keep=40)]
    if name == "previous_summary_update":
        first = _step(prior + [{"role": "user", "content": "Continue", "timestamp": 1002}])
        second = _step([_assistant("New progress " + "work " * 500, 1010),
                        {"role": "user", "content": "Next phase", "timestamp": 1011}])
        return [first, second]
    over = name == "agent_end_over_threshold"
    if name == "overflow_error_retry_exhausted":
        return [_scenario("huge_tool_overflow")[0], _step([], error=True)]
    step = _step([prior[0], _assistant("Final answer " + "findings " * 500, 1001)],
                 checkpoint="agent_end")
    # Both sides run their own pinned pi-ai parser over this provider usage.
    step["usage"] = {"prompt_tokens": 7500 if over else 2000,
                     "completion_tokens": 1000 if over else 500,
                     "total_tokens": 8500 if over else 2500}
    if name == "agent_end_no_usage":
        step["usage"] = None
    if name == "agent_end_silent_overflow":
        step["usage"] = {"prompt_tokens": 11000, "completion_tokens": 1000, "total_tokens": 12000}
    if name == "agent_end_length":
        step["messages"][-1]["stopReason"] = "length"
        step["usage"] = {"prompt_tokens": 10000, "completion_tokens": 0, "total_tokens": 10000}
    return [step]


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


@pytest.mark.parametrize("version,worker_script,node_modules", [
    ("0.73.1", _WORKER_0_73_1, _NODE_MODULES_0_73_1),
    ("0.57.1", _WORKER_0_57_1, _NODE_MODULES_0_57_1),
])
@pytest.mark.parametrize("scenario", ["huge_tool_overflow", "split_turn", "previous_summary_update",
                                    "tool_calls_straddling_cut", "agent_end_over_threshold",
                                    "agent_end_under_threshold", "overflow_retry_threshold",
                                    "agent_end_no_usage", "agent_end_silent_overflow",
                                    "overflow_error_retry_exhausted", "agent_end_length"])
@pytest.mark.asyncio
async def test_stock_oracle(tmp_path: Path, version: str, worker_script: Path,
                            node_modules: Path, scenario: str) -> None:
    if not node_modules.is_dir():
        pytest.skip("pinned Pi stock package unavailable")
    client = FramedWorkerClient(worker_script, node_modules, tmp_path)
    try:
        initialized = await client.start(version)
        model = initialized["bootstrap"]["model_config"]
        steps = _scenario(scenario)
        stock = subprocess.run(
            ["node", "--input-type=module", "-e", _STOCK_ORACLE],
            input=json.dumps({"node_modules": str(node_modules), "version": version, "model": model,
                              "system_prompt": initialized["system_prompt"], "steps": steps}),
            text=True, capture_output=True,
        )
        assert stock.returncode == 0, stock.stderr
        expected_steps = json.loads(stock.stdout)
        messages: list[dict[str, Any]] = []
        for step, expected in zip(steps, expected_steps, strict=True):
            messages.extend(step["messages"])
            if "error" in step:
                failure = await client.invoke("project_provider_failure", {
                    "http_status": step["error"]["status"],
                    "response_body_text": json.dumps(step["error"]["body"]), "messages": messages,
                })
                assert _bytes(failure["message"]) == _bytes(expected["errorMessage"])
                messages.append(failure["message"])
            payload = {"messages": messages, "checkpoint": step["checkpoint"],
                       "reason": "threshold" if step["checkpoint"] == "agent_end" else "overflow",
                       "context_window": step["context_window"], "settings": step["settings"]}
            if step["checkpoint"] == "agent_end":
                payload["usage"] = step["usage"]
            prepared = await client.invoke("prepare_compaction", payload)
            if not expected["triggered"]:
                assert prepared["kind"] == "compaction_unavailable"
                assert prepared["reason"] == expected["unavailableReason"]
                continue
            assert prepared["kind"] == "compaction_prepared"
            assert _bytes(prepared["summary_request"]) == _bytes(expected["requests"].get("history"))
            assert _bytes(prepared["turn_prefix_request"]) == _bytes(expected["requests"].get("prefix"))
            for key in ("firstKeptEntryId", "tokensBefore", "isSplitTurn", "messagesToSummarize", "turnPrefixMessages", "fileOps"):
                assert _bytes(prepared["preparation"][key]) == _bytes(expected["preparation"][key])
            if scenario == "huge_tool_overflow":
                assert prepared["preparation"]["firstKeptEntryId"] == expected["branchBefore"][-1]["id"]
                assert expected["branchBefore"][-1]["message"]["stopReason"] == "error"
            if scenario == "split_turn":
                assert prepared["turn_prefix_request"] is not None
            finalized = await client.invoke("finalize_compaction", {
                "summary": step["responses"]["history"], "turn_prefix_summary": step["responses"]["prefix"],
                "preparation": prepared["preparation"],
            })
            assert finalized["reason"] == expected["reason"]
            assert finalized["retry"] is expected["retry"]
            assert _bytes(finalized["messages"]) == _bytes(expected["messages"])
            projected = await client.invoke("project_request", {"messages": finalized["messages"]})
            assert _bytes(projected["messages"]) == _bytes(expected["postWire"])
            messages = finalized["messages"]
    finally:
        await client.close()
