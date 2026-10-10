"""Record pinned OpenClaw agent exec versus the compiled r2 production conductor.

Run with OPENCLAW_DIST set and ``python -m scripts.compaction_lanes.openclaw_lane
--out /tmp/openclaw-lane``. No target assets or stock files are modified. The
16000-token window is a symmetric fixture override. Target limits are retained
except the profile-sealed 35-second phase timeout, cited in the report. Stock
identity and prepared-message timestamp seams share BB's declared replay inputs;
stock formatters, execution and control clocks remain unchanged. A fixed-length
deterministic scratch path keeps prompt budgets independent of report location.
Bodies are compared as canonical JSON; raw HTTP bytes remain in the recordings.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import threading
import re
from typing import Any

from scripts.compaction_lanes.mock_provider import MockProvider, MockScriptEngine, MockRequestHandler, MockThreadingServer

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "config/e4_targets/openclaw/2026.9.4-r2"
TARGET_ID = "openclaw-r2@2026.9.4"
SCENARIOS = ("threshold", "overflow_bound", "split_turn", "length_stop")
PROMPT = "Read the numbered records, then report completion."
WINDOW = 16000
SESSION_ID = "00000002-0000-4000-8000-000000000002"
# One millisecond before a formatter minute boundary, deliberately exercising
# exact timestamp bytes without changing any stock deadline/control clock.
EPISODE_TIMESTAMP_MS = 1728432059999


def configuration() -> tuple[dict[str, Any], dict[str, Any]]:
    import yaml
    return json.loads((TARGET / "native-config.json").read_text()), yaml.safe_load((TARGET / "harness.yaml").read_text())


def script(scenario: str) -> list[dict[str, Any]]:
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    low = {"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010}
    rounds = [{"type": "tool_call", "name": "read", "call_id": f"read{i}",
               "arguments": json.dumps({"path": f"records-{i}.txt"}), "usage": low} for i in range(3)]
    # Summary replies are selected by the real request, not guessed positions.
    final = {"type": "text", "text": "Inspection complete.", "usage": {"input_tokens": 12500, "output_tokens": 10, "total_tokens": 12510}}
    error = {"type": "error", "error": "context_length_exceeded", "message": "context length exceeded", "status_code": 400}
    if scenario == "threshold":
        return [*rounds, final]
    if scenario == "overflow_bound":
        # Two first reads share one model turn, preserving room under the sealed
        # eight-main-request cap for fresh observations between recovery attempts.
        batch = {"type": "text", "text": "", "usage": low,
                 "tool_calls": [{key: call[key] for key in ("call_id", "name", "arguments")} for call in rounds[:2]]}
        fresh = [{"type": "tool_call", "name": "read", "call_id": f"read{i}",
                  "arguments": json.dumps({"path": f"records-{i}.txt"}), "usage": low} for i in (3, 4)]
        return [batch, rounds[2], error, fresh[0], error, fresh[1], error, error]
    if scenario == "length_stop":
        error = {"type": "text", "text": "", "finish_reason": "length", "usage": {"input_tokens": WINDOW, "output_tokens": 0, "total_tokens": WINDOW}}
    return [*rounds, error, {"type": "tool_call", "name": "read", "call_id": "read3", "arguments": json.dumps({"path": "records-3.txt"}), "usage": low}, final]


class ScenarioEngine(MockScriptEngine):
    def next_response(self) -> dict[str, Any]:
        if is_summary(self.recorded_requests[-1]["json"]):
            return {"type": "text", "text": "Records inspected. Continue the pending inspection.",
                    "usage": {"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010}}
        return super().next_response()


class LaneHandler(MockRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/v1/embeddings":
            return super().do_POST()
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        body = json.loads(raw)
        self.server.engine.record_request("POST", self.path, dict(self.headers), raw, body)
        inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
        # Fixed provider fixture, not a hidden replay filter. Auxiliary requests
        # remain in the byte comparison and produce undeclared differences.
        response = {"object": "list", "model": body["model"], "data": [
            {"object": "embedding", "index": i, "embedding": [1.0, 0.0, 0.0, 0.0]}
            for i in range(len(inputs))], "usage": {"prompt_tokens": 1, "total_tokens": 1}}
        self._send_raw_response({"headers": {"content-type": "application/json"}, "body": json.dumps(response)})

    def _handle_chat_completions(self, item, request_json):
        if item.get("finish_reason") != "length":
            return super()._handle_chat_completions(item, request_json)
        from tests.rl.harness.test_openclaw_native_stream_conductor import _sse_tool_response
        usage = item["usage"]
        payload = _sse_tool_response(1, [], content=item["text"], finish_reason="length",
            usage={"prompt_tokens": usage["input_tokens"], "completion_tokens": usage["output_tokens"], "total_tokens": usage["total_tokens"]})
        self._send_raw_response({"headers": {"content-type": "text/event-stream"}, "body": payload})


class LaneProvider(MockProvider):
    def start(self) -> str:
        self.server = MockThreadingServer((self.host, self.port), LaneHandler, self.engine)
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url


def provider_for(scenario: str, record: Path) -> MockProvider:
    provider = LaneProvider()
    provider.engine = ScenarioEngine(script(scenario), record)
    return provider


def canonical(body: Any) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def seed(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("AGENTS.md", "SOUL.md"):
        (workspace / name).write_bytes((TARGET / "bootstrap" / name).read_bytes())
    for i in range(5):
        (workspace / f"records-{i}.txt").write_text("".join(f"record {i} item {j}: value {i * 10000 + j}\n" for j in range(800)))


STOCK = r'''
import { pathToFileURL } from "node:url";
const input = JSON.parse(process.argv[1]);
// Identity seam only: stock agent-exec-BAuhpelg.mjs:311 allocates the id;
// BB receives the same opaque identity through declared runtime_inputs.
const crypto = await import("node:crypto");
let nextIdentity = 0;
// Session-accessor-BxcCxteu.mjs:2789-2792 also requires unique first8 digits.
crypto.default.randomUUID = () => {
  const id = (++nextIdentity).toString(16);
  return id.padStart(8, "0") + "-0000-4000-8000-" + id.padStart(12, "0");
};
(await import("node:module")).syncBuiltinESMExports();
const { agentExecCommand } = await import(pathToFileURL(process.env.OPENCLAW_DIST + "/agent-exec-BAuhpelg.mjs"));
const config = {
  agents: { defaults: { workspace: input.workspace, model: {primary: "openai/model-a"},
    contextTokens: input.window, compaction: { mode: "default" },
    systemAgent: {agentId: "main"}, userTimezone: "UTC" }, entries: {main: {}} },
  tools: {allow: input.native.tools.ordered, deny: ["apply_patch"], fs: {workspaceOnly: false}, exec: {host: "gateway", mode: "full", ask: "off", timeoutSec: input.native.tools.exec_timeout_seconds}},
  models: {providers: {openai: {baseUrl: input.base + "/v1", api: "openai-completions", apiKey: "fixture-key", models: [{id: "model-a", name: "model-a", input: ["text"], contextWindow: input.window, maxTokens: input.native.agent_exec.output_tokens}]}}},
};
const clockTimestamps = [];
const runAgentWithDeclaredClock = async (opts, runtime) => {
  const [{ agentCommand }, { n: AgentSession }, { n: createUserTurnTranscriptRecorder },
    { n: beforeMessageWrite }, configIo] = await Promise.all([
    import(pathToFileURL(process.env.OPENCLAW_DIST + "/agent-LlQj-moa.mjs")),
    import(pathToFileURL(process.env.OPENCLAW_DIST + "/resource-loader-Bu_pVD2t.mjs")),
    import(pathToFileURL(process.env.OPENCLAW_DIST + "/user-turn-transcript-DhYgvhAP.mjs")),
    import(pathToFileURL(process.env.OPENCLAW_DIST + "/hook-helpers-CoFqzDGy.mjs")),
    import(pathToFileURL(process.env.OPENCLAW_DIST + "/io-CfH9dYtp.mjs")),
  ]);
  let replayTimestamp = input.messageTimestamp;
  const stockEmit = AgentSession.prototype.emit;
  AgentSession.prototype.emit = function(event) {
    // Same committed replay offsets as BB: assistant/tool commits and completed
    // checkpoints. HTTP failures have no BB assistant commit; the stock SDK's
    // error placeholder is not a committed reply. Clock observation changes no
    // stock control clock: resource-loader:8452-8476,9922-9931,10172-10182.
    if (event.type === "message_end" && (event.message.role === "toolResult"
        || event.message.role === "assistant" && event.message.stopReason !== "error")
      || event.type === "compaction_end" && event.outcome.status === "completed") replayTimestamp += 1;
    return stockEmit.call(this, event);
  };
  const stockCreateUserMessage = AgentSession.prototype.createUserMessage;
  AgentSession.prototype.createUserMessage = function(text, images, preparedMessage) {
    // The source's prepared-message seam supplies only the timestamp:
    // resource-loader-Bu_pVD2t.mjs:9133-9144; user-turn-transcript.message:156-168.
    const message = stockCreateUserMessage.call(this, text, images, {...preparedMessage, timestamp: replayTimestamp});
    clockTimestamps.push(message.timestamp);
    return message;
  };
  // The initial currentUserTimestamp originates in the real source recorder:
  // agent-command-CHGsr0QY.mjs:2799-2818; builtin-openclaw:7225,7312,6143.
  const recorder = createUserTurnTranscriptRecorder({
    input: {text: opts.message, timestamp: input.messageTimestamp},
    target: {sessionId: opts.sessionId, sessionKey: opts.sessionKey, agentId: opts.agentId,
      cwd: opts.cwd, config: configIo.getRuntimeConfigSnapshot()},
    beforeMessageWrite, errorContext: "agent command user turn transcript",
  });
  return agentCommand({...opts, userTurnTranscriptRecorder: recorder}, runtime);
};
const result = await agentExecCommand(input.prompt, {cwd: input.workspace, isolated: true, authEnvOnly: true, json: true, model: "openai/model-a", thinking: "off"},
  {log() {}, error(...args) {console.error(...args)}, exit(code) {throw new Error("stock exit " + code)}},
  {baseConfig: config, agentId: "main", timeoutMs: input.native.agent_exec.episode_deadline_seconds * 1000, maxToolCalls: input.native.agent_exec.tool_admissions, modelFallbacksOverride: [], runAgent: runAgentWithDeclaredClock});
// Declared headless entry performs shared cleanup and drained one-shot exit:
// register.agent-turn-gi9D9FTy.mjs:63-68; cli/run-main.js:1156.
const { defaultRuntime } = await import(pathToFileURL(process.env.OPENCLAW_DIST + "/runtime-CBk2UDHH.mjs"));
const { a: closeCliResources, n: requestExitAfterOneShotOutput, r: runCliWithExitFinalization } =
  await import(pathToFileURL(process.env.OPENCLAW_DIST + "/one-shot-exit-Caliv-hT.mjs"));
await runCliWithExitFinalization({
  runtime: defaultRuntime,
  run: async () => {
    requestExitAfterOneShotOutput(defaultRuntime, result.exitCode);
    await closeCliResources();
    console.log(JSON.stringify({...result, lane_clock_timestamps: clockTimestamps}));
  },
  onError(error) { throw error; },
});
'''


def run_stock(workspace: Path, scratch: Path, base: str) -> dict[str, Any]:
    native, _ = configuration()
    dist = Path(os.environ["OPENCLAW_DIST"])
    metadata = json.loads((dist.parent / "package.json").read_text())
    if metadata["version"] != "2026.9.4":
        raise ValueError("OpenClaw stock version mismatch")
    home = scratch / "home"
    home.mkdir(parents=True)
    args = {"workspace": str(workspace), "base": base, "window": WINDOW, "sessionId": SESSION_ID, "native": native, "prompt": PROMPT, "messageTimestamp": EPISODE_TIMESTAMP_MS}
    try:
        proc = subprocess.run(["node", "--input-type=module", "-e", STOCK, json.dumps(args)], cwd=workspace,
            env={"PATH": os.environ["PATH"], "OPENCLAW_DIST": str(dist), "HOME": str(home), "OPENCLAW_STATE_DIR": str(scratch / "state"), "TZ": "UTC"},
            capture_output=True, text=True, timeout=native["agent_exec"]["episode_deadline_seconds"] + 30)
    except subprocess.TimeoutExpired as exc:
        (scratch / "stdout.txt").write_bytes(exc.stdout or b"")
        (scratch / "stderr.txt").write_bytes(exc.stderr or b"")
        raise
    (scratch / "stdout.txt").write_text(proc.stdout)
    (scratch / "stderr.txt").write_text(proc.stderr)
    result = json.loads(proc.stdout.splitlines()[-1])
    if proc.returncode != result["exitCode"]:
        raise RuntimeError(f"Stock process status differs from native envelope ({proc.returncode}): {proc.stderr}")
    return result


async def run_bb(workspace: Path, scratch: Path, base: str) -> dict[str, Any]:
    from tests.rl.harness import test_openclaw_native_stream_conductor as h
    native, harness = configuration()
    controls = harness["policy"]["execution"]["bounded_controls"]
    profile = h.OpenAICompletionsProviderProfile(model="model-a", scoped_credential="fixture-key", base_url=base + "/v1", context_window=WINDOW,
        max_output_tokens=native["agent_exec"]["output_tokens"], caller_headers={},
        request_policy={"mode": "streaming", "include_usage": True, "strict_tools": None, "enable_thinking": None, **h._SUPPLIER_WIRE_POLICY["request_policy"]},
        capabilities=dict(h._SUPPLIER_WIRE_POLICY["capabilities"]))
    projection, semantics, manifest = h._compile_target(scratch, profile_digest=h.profile_identity_digest(profile), target_id=TARGET_ID, context_window=WINDOW)
    projection = replace(projection, chat_tools=h.thaw_json(projection.chat_tools))
    observation = h._observation(provider_id="openai", model_id="model-a", route_id="route-a", credential_handle_id="credential-a", protocol_abi="responses-v1",
        capabilities=h._policy_capabilities(request_features=sorted(["json_mode", "seed", "stream_options", "streaming", *h._SUPPLIER_WIRE_POLICY["request_features"]])))
    tools = tuple(h._tool_grant(name) for name in native["tools"]["ordered"])
    plan = h._plan(observation=observation, semantics=semantics, tools=tools, policy_slot_ids=("model:model-a",),
        limit_updates={"max_turns": controls["max_model_calls"], "action_timeout_ms": 35000,
                       "observation_bytes": controls["raw_evidence_bytes_per_outcome"], "transcript_bytes": controls["raw_evidence_bytes_per_outcome"]}, implementation_digest=h.CONDUCTOR_IMPLEMENTATION_DIGEST)
    payload = plan.model_dump(mode="python")
    identity = plan.base_compiled.model_dump(mode="python")
    identity.update(manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(), compiler_input_digest=manifest.inputs.compiler_input_digest)
    payload["base_compiled"] = h.c.CompiledArtifactIdentity.model_validate(identity)
    plan = h.c.EffectiveExecutionPlan.model_validate(payload)
    phases = []
    class Worker(h._NativeWorkerPort):
        def native_runtime_inputs(self, *, input_names, package_subpath):
            values = dict(super().native_runtime_inputs(input_names=input_names, package_subpath=package_subpath))
            # The pinned stock module consumes one setup UUID before allocating
            # agent exec's identity. Only this opaque fixture input is replayed.
            values["session_id"] = SESSION_ID
            values["message_timestamp_ms"] = str(EPISODE_TIMESTAMP_MS)
            return values
        async def invoke_native_phase(self, operation, payload, **kwargs):
            result = await super().invoke_native_phase(operation, payload, **kwargs)
            if operation in {"prepare_compaction", "finalize_compaction"}:
                phases.append({"operation": operation, "reason": payload.get("reason"), "result": result})
            return result
    worker = Worker(workspace, tuple(h.RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools))
    client = h.EpisodeOpenAICompletionsPolicyClient(episode_id="openclaw-lane", effective_plan_digest=plan.canonical_digest(), observation=observation,
        profile=profile, target_projection=projection, timeout_seconds=harness["policy"]["provider"]["request_timeout_seconds"])
    request = h.RunnerOpenRequest(episode_id="openclaw-lane", effective_plan=plan)
    session = await h.ConductorAdapter(h.CONDUCTOR_RUNTIME_ABI, containment_authenticator=h.CONDUCTOR_TEST_AUTHENTICATOR,
        admitted_lease_ledger=h.CONDUCTOR_TEST_LEDGER).open(request, policy=h.PolicyRuntimeBinding(request, client), workspace=worker, cancellation=h._Cancellation(), events=h._Events())
    try:
        result = await session.run(h.ConductorRunRequest(task_input={"prompt": PROMPT}, context={}))
        return {"termination": result.termination.value, "response": h.thaw_json(result.response), "phases": phases}
    finally:
        await session.close()
        await worker.close()
        await client.close()


def is_summary(body: dict[str, Any]) -> bool:
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    from breadboard.rl.harness.runners.openclaw_semantics import OPENCLAW_CONSUMER_ID
    messages = body.get("messages", [])
    return bool(messages) and messages[0]["content"] == NATIVE_STREAM_PROFILES[OPENCLAW_CONSUMER_ID].compaction_summary_system_prompt


# Source transport-utils-zrYjICLZ.mjs:32-33,57-60: UTF-16 length plus
# non-Latin weighting, including astral CJK's surrogate-pair adjustment.
_NON_LATIN = re.compile(r"[\u2e80-\u9fff\ua000-\ua4ff\uac00-\ud7af\uf900-\ufaff\uff01-\uff60\uffe0-\uffe6\U00020000-\U0002fa1f]")


def source_string_chars(text: str) -> int:
    count = len(_NON_LATIN.findall(text))
    units = len(text.encode("utf-16-le", errors="surrogatepass")) // 2
    if count:
        units -= sum(0x20000 <= ord(char) <= 0x2FBFF for char in text)
    return units + count * 3


def source_input_tokens(body: dict[str, Any]) -> int:
    """Stock proxy estimator; openai-completions-stream-Da2vvl-S.mjs:873-927.

    Recorded payloads are parsed JSON, so source JSON.stringify error fallbacks
    cannot occur here. This estimates without rewriting the compared payload.
    """
    def content_chars(value):
        if isinstance(value, str):
            return source_string_chars(value)
        if not isinstance(value, list):
            return 0
        return sum(
            8000 if block.get("type") in {"image_url", "input_image"}
            else source_string_chars(block["text"]) if isinstance(block.get("text"), str)
            else source_string_chars(json.dumps(block, ensure_ascii=False, separators=(",", ":")))
            for block in value if isinstance(block, dict)
        )

    chars = 0
    for message in body.get("messages", []):
        for field in ("content", "reasoning_details", "reasoning_content", "reasoning", "reasoning_text"):
            chars += content_chars(message.get(field))
        if "tool_calls" in message:
            chars += source_string_chars(json.dumps(message["tool_calls"], ensure_ascii=False, separators=(",", ":")))
    if body.get("tools"):
        chars += source_string_chars(json.dumps(body["tools"], ensure_ascii=False, separators=(",", ":")))
    if "response_format" in body:
        chars += source_string_chars(json.dumps(body["response_format"], ensure_ascii=False, separators=(",", ":")))
    return (chars * 5 + 15) // 16


def stock_summary_cap(body: dict[str, Any]) -> int:
    native, _ = configuration()
    # agent-settings-DcI_VuTd.mjs:19-23 and agent-compaction-constants:2-6.
    reserve = min(20000, WINDOW // 4)
    ratio = .5 if "This is the PREFIX of a turn" in str(body["messages"][-1]["content"]) else .8
    requested = min(int(reserve * ratio), native["agent_exec"]["output_tokens"])
    # Explicit proxy endpoint, stock transport:1068-1078.
    return min(requested, max(1, WINDOW - source_input_tokens(body) - 1))


def compare(stock: list[dict[str, Any]], bb: list[dict[str, Any]]) -> dict[str, Any]:
    native, harness = configuration()
    allowed = {item["id"] for item in harness["policy"]["deviations"]}
    tools = bb[0]["json"]["tools"]
    diffs = []
    used = set()
    for i in range(max(len(stock), len(bb))):
        if i >= min(len(stock), len(bb)):
            diffs.append({"request": i, "reason": "request count differs"})
            continue
        left, right = stock[i], bb[i]
        if left["raw_body_bytes"] == right["raw_body_bytes"] and left["path"] == right["path"]:
            continue
        a, b = deepcopy(left["json"]), deepcopy(right["json"])
        if not is_summary(a) and "tools" in a and "tools" in b:
            overlay = native["advertisement"]["tools"]["exec"]
            stock_exec = next(tool["function"] for tool in a["tools"] if tool["function"]["name"] == "exec")
            bb_exec = next(tool["function"] for tool in b["tools"] if tool["function"]["name"] == "exec")
            if "sha256:" + hashlib.sha256(stock_exec["description"].encode()).hexdigest() != overlay["native_sha256"]:
                raise ValueError("Stock exec description differs from declared overlay source")
            if bb_exec["description"] != overlay["description"]:
                raise ValueError("BB exec description differs from declared overlay")
            stock_exec["description"] = overlay["description"]
        if is_summary(a) and is_summary(b):
            if "compaction_summary_episode_tools" in allowed:
                if "tools" in a or b.get("tools") != tools:
                    raise ValueError("Summary tools violate recorded deviation")
                b.pop("tools")
                used.add("compaction_summary_episode_tools")
            if "compaction_summary_max_tokens" in allowed:
                # Reserve/model cap, followed by stock's input-dependent proxy
                # clamp. BB retains the sealed episode cap (harness.yaml:81-86).
                budget = stock_summary_cap(a)
                if a.get("max_completion_tokens") != budget or b.get("max_completion_tokens") != native["agent_exec"]["output_tokens"]:
                    raise ValueError("Summary cap violates recorded deviation")
                a.pop("max_completion_tokens")
                b.pop("max_completion_tokens")
                used.add("compaction_summary_max_tokens")
        if canonical(a) != canonical(b) or left["path"] != right["path"]:
            diffs.append({"request": i, "stock": a, "breadboard": b})
    return {"stock_requests": len(stock), "bb_requests": len(bb), "diffs": diffs, "deviations_used": sorted(used),
            "stock_summary_requests": sum(is_summary(r["json"]) for r in stock), "bb_summary_requests": sum(is_summary(r["json"]) for r in bb)}



def run_lane(out: Path, scenarios: tuple[str, ...] = SCENARIOS) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for scenario in scenarios:
        folder = out / scenario
        folder.mkdir()
        scratch_key = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:12]
        workspace = Path("/tmp/bb-oc-lane") / scratch_key / "workspace"
        seed(workspace)
        with provider_for(scenario, folder / "stock_requests.jsonl") as provider:
            shared_scratch = workspace.parent / (workspace.name + "-scratch")
            try:
                stock_result = run_stock(workspace, shared_scratch, provider.base_url)
            except subprocess.TimeoutExpired as exc:
                stock_result = {"execution_error": "stock subprocess did not settle", "timeout_seconds": exc.timeout}
            for name in ("stdout.txt", "stderr.txt"):
                (folder / ("stock_" + name)).write_bytes((shared_scratch / name).read_bytes())
            shutil.rmtree(shared_scratch)
            stock_requests = deepcopy(provider.engine.recorded_requests)
        # Same absolute workspace and pristine fixture contents on both sides.
        seed(workspace)
        with provider_for(scenario, folder / "bb_requests.jsonl") as provider:
            bb_result = asyncio.run(run_bb(workspace, folder / "bb", provider.base_url))
            bb_requests = deepcopy(provider.engine.recorded_requests)
        result = compare(stock_requests, bb_requests)
        result.update(stock_result=stock_result, bb_result=bb_result)
        result["workspace_path"] = str(workspace)
        bb_clock_timestamps = [EPISODE_TIMESTAMP_MS]
        preparation = None
        for phase in bb_result["phases"]:
            if phase["result"]["kind"] == "compaction_prepared":
                preparation = phase["result"]["preparation"]
            elif phase["result"]["kind"] == "compaction_finalized" and phase["result"]["retry"]:
                # The real worker uses exactly prep.timestamp for its transient
                # user message (openclaw_tool_worker.mjs:1301), not a lane clock.
                bb_clock_timestamps.append(int(datetime.fromisoformat(preparation["timestamp"]).timestamp() * 1000))
        result["clock_timestamps"] = {"stock": stock_result.get("lane_clock_timestamps", []),
                                      "bb": bb_clock_timestamps}
        # Diagnostic only. The full-request comparison above never drops auxiliary
        # requests, and therefore remains red for missing stock memory indexing.
        result["chat_payload_comparison"] = compare(
            [request for request in stock_requests if request["path"] == "/v1/chat/completions"],
            [request for request in bb_requests if request["path"] == "/v1/chat/completions"],
        )
        result["summary_budgets"] = [
            {"stock_wire_cap": a["json"]["max_completion_tokens"],
             "stock_expected_cap": stock_summary_cap(a["json"]),
             "stock_input_tokens": source_input_tokens(a["json"]),
             "bb_wire_cap": b["json"]["max_completion_tokens"],
             "bb_input_tokens_including_episode_tools": source_input_tokens(b["json"])}
            for a, b in zip(
                [r for r in stock_requests if is_summary(r["json"])],
                [r for r in bb_requests if is_summary(r["json"])],
            )
        ]
        result["auxiliary_requests"] = {
            side: [{"index": index, "path": request["path"]} for index, request in enumerate(requests)
                   if request["path"] != "/v1/chat/completions"]
            for side, requests in (("stock", stock_requests), ("bb", bb_requests))
        }
        stock_logs = (folder / "stock_stdout.txt").read_text() + (folder / "stock_stderr.txt").read_text()
        # The session-owned path and embedded caller-owned recovery log separately:
        # resource-loader-Bu_pVD2t.mjs and embedded-agent-CE9KzQvy.mjs:3157-3159.
        result["stock_compactions"] = stock_logs.count("auto-compaction complete:") + stock_logs.count("auto-compaction succeeded for ")
        result["stock_overflow_attempts"] = [int(value) for value in re.findall(r"context overflow (?:detected|persisted after in-attempt compaction) \(attempt (\d+)/3\)", stock_logs)]
        result["stock_overflow_exhausted"] = "exhausted provider overflow recovery" in stock_logs
        overflow_checks = [p for p in bb_result["phases"] if p["operation"] == "prepare_compaction"
                           and (p["reason"] == "overflow"
                                or p["result"].get("preparation", {}).get("sourceReason") == "overflow")]
        bound_checks = sum(p["result"]["kind"] == "compaction_unavailable"
                           and p["result"].get("reason", "").startswith("Context overflow recovery failed after 3 compact-and-retry attempts.")
                           for p in overflow_checks)
        result["bb_overflow_checks"] = len(overflow_checks)
        result["bb_overflow_attempts"] = len(overflow_checks) - bound_checks
        result["bb_overflow_bound_reached"] = bound_checks > 0
        result["bb_compactions"] = sum(p["operation"] == "finalize_compaction" and p["result"]["kind"] == "compaction_finalized" for p in bb_result["phases"])
        result["stock_turn_prefix_requests"] = sum(is_summary(r["json"]) and "This is the PREFIX of a turn" in str(r["json"]["messages"][-1]["content"]) for r in stock_requests)
        result["bb_turn_prefix_requests"] = sum(is_summary(r["json"]) and "This is the PREFIX of a turn" in str(r["json"]["messages"][-1]["content"]) for r in bb_requests)
        results[scenario] = result
        (folder / "lane_report.json").write_text(json.dumps(result, indent=2) + "\n")
    report = {"target": TARGET_ID, "overrides": {"model.context_window": {"target": configuration()[0]["model"]["context_window"], "both_sides": WINDOW}}, "lane_results": results,
              "rerun_command": "PYTHONPATH=. BB_WORKSPACE_ROOT=$PWD python -m scripts.compaction_lanes.openclaw_lane --out /tmp/openclaw-lane"}
    report["comparison_basis"] = "canonical JSON, preserving string bytes, values, array order and present/absent fields; raw HTTP bodies retained"
    report["limit_sources"] = {"action_timeout_ms": "native_stream_profiles.py OpenClaw profile and test_openclaw_native_stream_conductor.py:549 both use 35000; target40s is an outer admission ceiling"}
    report["declared_overlay"] = "native-config.json:advertisement.tools.exec, SHA-256 source/value guards"
    report["artifact_root"] = str(out)
    report["scratch_seam"] = "Shared stock/BB workspace /tmp/bb-oc-lane/<12 hex SHA256(report scenario path)>/workspace; fixed path length and unique deterministic key. Report location no longer changes prompt size."
    report["stock_summary_cap_formula"] = "min(floor(ratio * min(20000, WINDOW/4)), model.maxTokens, max(1, WINDOW - ceil(adjustedChars * 1.25/4) - 1)); ratio .8 history/.5 prefix. compaction:634; agent-settings:19-23; agent-compaction-constants:2-6; openai-completions-stream:873-927,1068-1078."
    report["ordinary_proxy_clamp_gap"] = {
        "status": "unresolved",
        "classification": "pre-existing E4 fixed-episode-cap difference",
        "observed_long_path_pairs": {"threshold": [2037, 2048], "overflow_bound": [2015, 2048],
                                    "split_turn": [2032, 2048], "length_stop": [2028, 2048]},
        "artifact": "artifact://18305",
        "basis": "These are ordinary requests, not summaries. No deviation normalization is applied. Stock proxy clamp applies to every request; BB retains decision 2's fixed episode cap.",
        "baseline": "308b9027 breadboard_engine/provider/runtimes/openai/chat.py:572-587 delegates to profile.chat_request; provider/profiles.py:671 assigns max_output_tokens without input-dependent clamping.",
    }
    report["configuration_conflicts"] = ["target.json:57-60 still lists model.compaction=false; production harness.yaml:29 and native-config.json:21 enable it"]
    report["identity_seams"] = ["agent-exec-BAuhpelg.mjs:311 allocates UUID2 after stock module setup consumes UUID1; BB runtime_inputs.session_id is UUID2", "UUID sequence varies first8 and final12 digits, preserving session-accessor-BxcCxteu.mjs:2789-2792 uniqueness"]
    report["environment_normalizations"] = []
    report["clock_seams"] = {"message_timestamp_ms": EPISODE_TIMESTAMP_MS,
                            "stock": "real recorder input.timestamp (user-turn-transcript.message-C84t0g-c.mjs:111; agent-command:2799) and SDK preparedMessage.timestamp (resource-loader:9133-9144); event-count replay offsets only",
                            "formatter": "builtin-openclaw-B-H-7lKk.mjs:6143,7312,6352-6362",
                            "breadboard": "declared runtime_inputs.message_timestamp_ms; worker:531,538,637,1301; semantics _source_message_timestamp",
                            "replay_offsets": "One per committed assistant/tool and completed checkpoint; HTTP error placeholders do not commit before recovery in BB (conductor.py:2799-2822). Actual stock and BB initial/continuation timestamps are reported and asserted equal.",
                            "control_clocks": "Stock Date.now, timers and deadlines are unchanged; no regex normalization"}
    report["files_changed"] = ["scripts/compaction_lanes/openclaw_lane.py", "tests/compaction_lanes/test_openclaw_lane.py", "breadboard/rl/harness/openclaw_tool_worker.mjs", "tests/rl/harness/test_openclaw_native_stream_conductor.py", "tests/rl/harness/test_openclaw_2026_9_4_compaction.py"]
    report["defects_found_and_fixed"] = [
        {"classification": "pre-existing E4 parity gap", "defect": "read/ls caps and skill-description budget omitted model window", "fix": "openclaw_tool_worker.mjs:makeTools,materializeSourcePrompt", "stock": "core-coding-tools-DoP9tAh3.mjs:1007,1024-1025; workspace-skill-prompt-D3wdQJbf.mjs:105-106"},
        {"classification": "pre-existing E4 parity gap", "defect": "headless initialization created templates and retained BOOTSTRAP.md", "fix": "openclaw_tool_worker.mjs:bootstrapContext,initialize_session", "stock": "agent-exec-BAuhpelg.mjs:106; builtin-openclaw-B-H-7lKk.mjs:1850-1861,1893"},
        {"classification": "pre-existing E4 parity gap", "defect": "session runtime facts used explicit scope rather than declared agent-exec scope", "fix": "openclaw_tool_worker.mjs:initialize_session", "stock": "agent-exec-BAuhpelg.mjs:388"},
        {"classification": "compaction-path defect introduced by branch", "defect": "overflow retry omitted transient internal continuation", "fix": "openclaw_tool_worker.mjs:projectSourceRequest,finalize_compaction; verified source data and normalizeMessagesForLlmBoundary formatter", "stock": "embedded-agent-CE9KzQvy.mjs:5898,5961-5967,6063-6068"},
        {"classification": "compaction-path defect introduced by branch", "defect": "SDK assistant-progress resets weakened the embedded caller's three-attempt recovery bound", "fix": "openclaw_tool_worker.mjs:admitSourceRecoveryEvents", "stock": "embedded-agent-CE9KzQvy.mjs:3093-3095,3189-3198"},
    ]
    report["deviations_used"] = sorted({deviation for result in results.values() for deviation in result["deviations_used"]})
    report["unresolved"] = [{"scenario": name, "classification": "pre-existing E4 memory-indexing gap" if result["auxiliary_requests"]["stock"] and not result["auxiliary_requests"]["bb"] and not result["chat_payload_comparison"]["diffs"] else "request parity failure",
                            "diff_count": len(result["diffs"]), "auxiliary_requests": result["auxiliary_requests"],
                            "chat_diff_count": len(result["chat_payload_comparison"]["diffs"]),
                            "stock_execution_error": result["stock_result"].get("execution_error")}
                           for name, result in results.items() if result["diffs"] or result["stock_result"].get("execution_error")]
    report["baseline_memory_evidence"] = "Base308b9027 openclaw_tool_worker.mjs:197-204,378 only builds coding tools and renders memory instructions; no memory-index path. The r2 sealed config does not disable default stock embeddings. Target unchanged; omission is not an admitted deviation."
    (out / "lane_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scenario", choices=SCENARIOS)
    args = parser.parse_args()
    report = run_lane(args.out.resolve(), (args.scenario,) if args.scenario else SCENARIOS)
    print(json.dumps({k: {field: value for field, value in v.items() if field not in {"stock_result", "bb_result", "diffs"}} | {"diff_count": len(v["diffs"])} for k, v in report["lane_results"].items()}, indent=2))
    if report["unresolved"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
