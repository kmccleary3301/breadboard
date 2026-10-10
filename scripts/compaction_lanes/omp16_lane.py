"""OMP 16.2.13 SDK versus the lowered r3 production conductor.

Run with OMP16213_CODING_AGENT_NODE_MODULES exported and
BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. python -m scripts.compaction_lanes.omp16_lane
--out /tmp/omp16-lane. Only the context window is overridden, identically on
both sides. Stock snapcompact falls back to context-full for the target's
text-only model. Captures retain original bytes; comparison removes only the
target's two admitted summary fields, after validating their values.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from scripts.compaction_lanes.mock_provider import MockProvider, MockScriptEngine

ROOT = Path(__file__).resolve().parents[2]
TARGET_ID = "oh-my-pi-r3@16.2.13"
TARGET_DIR = ROOT / "config/e4_targets/oh_my_pi/16.2.13-r3"
WINDOW = 65536
MODEL = "Qwen/Qwen3.5-35B-A3B"
PROMPT = "Inspect the numbered records with read, then report completion."
SUMMARY = "## Goal\nInspect records.\n\n## Progress\nRead numbered records.\n\n## Next Steps\nContinue inspection."
SCENARIOS = ("before_request", "agent_end", "overflow")

# The installed SDK owns the entire stock provider/tool/maintenance loop.
STOCK_DRIVER = r'''
const root = process.env.OMP16213_CODING_AGENT_NODE_MODULES;
const cfg = JSON.parse(await Bun.file(process.argv[2]).text());
const { createAgentSession } = await import(`${root}/@oh-my-pi/pi-coding-agent/src/sdk.ts`);
const { Settings } = await import(`${root}/@oh-my-pi/pi-coding-agent/src/config/settings.ts`);
const { ModelRegistry } = await import(`${root}/@oh-my-pi/pi-coding-agent/src/config/model-registry.ts`);
const { SessionManager } = await import(`${root}/@oh-my-pi/pi-coding-agent/src/session/session-manager.ts`);
const { AuthStorage } = await import(`${root}/@oh-my-pi/pi-ai/src/auth-storage.ts`);
const settings = await Settings.init({cwd: cfg.workspace, agentDir: cfg.scratch, inMemory: true, configFiles: [], overrides: {
  "retry.enabled": cfg.native.agent.retry_enabled,
  "images.blockImages": !cfg.native.model.images_enabled,
  "compaction.enabled": cfg.native.agent.compaction_enabled,
}});
const authStorage = await AuthStorage.create(":memory:");
const registry = new ModelRegistry(authStorage, `${cfg.scratch}/models.json`, {settings});
registry.registerProvider(cfg.native.model_registry.provider_id, {
 baseUrl: cfg.base_url + "/v1", api: cfg.native.model_registry.api, apiKey: "omp-tool-worker-key",
 models: [{id: cfg.model, name: cfg.model, ...cfg.native.model_registry, contextWindow: cfg.window}],
});
const model = registry.find(cfg.native.model_registry.provider_id, cfg.model);
const {session} = await createAgentSession({cwd: cfg.workspace, agentDir: cfg.scratch,
 settings, authStorage, modelRegistry: registry, model, sessionManager: SessionManager.inMemory(cfg.workspace),
 thinkingLevel: cfg.native.agent.thinking_level, toolNames: cfg.native.tools.cli_declared,
 autoApprove: false, skills: [], disableExtensionDiscovery: true, additionalExtensionPaths: [], enableMCP: false,
});
const events = [];
session.subscribe(event => { if (event.type.startsWith("auto_compaction")) events.push(event); });
try {
 await session.prompt(cfg.prompt);
 await session.waitForIdle();
 await Bun.write(cfg.result, JSON.stringify({events, settings: settings.getGroup("compaction"), messages: session.messages}));
} finally { await session.dispose(); }
'''


def target_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    import yaml
    return (json.loads((TARGET_DIR / "native-config.json").read_text()),
            yaml.safe_load((TARGET_DIR / "harness.yaml").read_text()))


def resolve_stock() -> Path:
    root = Path(os.environ["OMP16213_CODING_AGENT_NODE_MODULES"])
    package = root / "@oh-my-pi/pi-coding-agent"
    metadata = json.loads((package / "package.json").read_text())
    if metadata["version"] != "16.2.13":
        raise ValueError(f"Expected OMP 16.2.13, got {metadata['version']}")
    return root


def scenario_script(scenario: str) -> list[dict[str, Any]]:
    low = {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}
    high = {"input_tokens": 60000, "output_tokens": 50, "total_tokens": 60050}
    def reads(turn: int, usage: dict[str, int]) -> dict[str, Any]:
        return {"type": "response", "text": "Inspecting records.", "tool_calls": [
            {"name": "read", "call_id": f"read-{turn}-{i}", "arguments": json.dumps({"path": f"records-{turn}-{i}.txt:1-650:raw", "i": "Reading numbered records"})}
            for i in range(1)], "usage": usage}
    # Summary exchanges are supplied separately by RecordingEngine, so a
    # split-turn fan-out cannot consume a main-turn response.
    done = {"type": "text", "text": "Inspection complete.", "usage": low}
    rounds = [reads(0, low), reads(1, low)]
    if scenario == "before_request":
        return [*rounds, reads(2, high), reads(3, low), reads(4, low), reads(5, high), done]
    if scenario == "agent_end":
        return [*rounds, {**done, "usage": high}, done]
    if scenario == "overflow":
        return [*rounds, {"type": "error", "error": "context_length_exceeded", "message": f"This model's maximum context length is {WINDOW} tokens.", "status_code": 400}, done]
    raise ValueError(scenario)


class RecordingEngine(MockScriptEngine):
    """Compose the shared recorder with independent main/summary response queues."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        import threading
        super().__init__(*args, **kwargs)
        self._request = threading.local()

    def record_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        record = super().record_request(*args, **kwargs)
        self._request.summary = is_summary(record["json"])
        return record

    def next_response(self) -> dict[str, Any]:
        if self._request.summary:
            return {"type": "text", "text": SUMMARY,
                    "usage": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}}
        return super().next_response()


def recording_provider(scenario: str, record_path: Path) -> MockProvider:
    provider = MockProvider()
    provider.engine = RecordingEngine(scenario_script(scenario), record_path)
    return provider


def seed_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for turn in range(6):
        (workspace / f"records-{turn}-0.txt").write_text("".join(
            f"record {turn} item {j}: value {j} with additional numbered detail for inspection\n"
            for j in range(650)))


def run_stock(workspace: Path, scratch: Path, url: str, out: Path) -> dict[str, Any]:
    native, _ = target_documents()
    root = resolve_stock()
    scratch.mkdir(parents=True, exist_ok=True)
    home = scratch / "home"
    home.mkdir(exist_ok=True)
    driver = out / "stock-driver.ts"
    driver.write_text(STOCK_DRIVER)
    config = out / "stock-config.json"
    result = out / "stock-result.json"
    config.write_text(json.dumps({"workspace": str(workspace), "scratch": str(scratch), "native": native,
        "base_url": url, "window": WINDOW, "model": MODEL, "prompt": PROMPT, "result": str(result)}))
    env = dict(os.environ, HOME=str(home), PI_CODING_AGENT_DIR=str(scratch), OMP_OFFLINE="1", NODE_PATH=str(root))
    for key in ("TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TERM", "COLORTERM", "TERMINAL_EMULATOR", "WT_SESSION"):
        env.pop(key, None)
    proc = subprocess.run([shutil.which("bun") or "bun", str(driver), str(config)], cwd=workspace, env=env,
                          capture_output=True, text=True, timeout=120)
    (out / "stock-stdout.txt").write_text(proc.stdout)
    (out / "stock-stderr.txt").write_text(proc.stderr)
    if proc.returncode:
        raise RuntimeError(f"Stock OMP failed ({proc.returncode}): {proc.stderr}")
    return json.loads(result.read_text())


async def run_bb(workspace: Path, scratch: Path, url: str) -> dict[str, Any]:
    from tests.rl.harness import test_omp_16_2_13_native_stream_conductor as h
    native, harness = target_documents()
    profile = replace(h._profile(url), model=MODEL, context_window=WINDOW,
                      max_output_tokens=native["model"]["max_tokens"])
    cas = h.FilesystemCAS(scratch / "cas")
    try:
        compiled = h.compile_e4_harness(h.load_e4_target(TARGET_ID), {}, {
            "version": 2, "profile": {"name": "omp16-compaction-lane"}, "workspace": {"root": "workspace"},
            "provider_tools": {"use_native": True, "api_variant": "chat_completions"},
            "providers": {"default_model": "model-a", "models": [{"id": "model-a", "adapter": "openai",
                "context_length": WINDOW, "route_handle_id": "route-a", "credential_handle_id": "credential-a", "params": {},
                "response_policy": {"schema_version": "bb.provider_native_response_policy.v1",
                    "consumer_id": h.OMP_16_2_13_RESPONSE_CONSUMER_ID, "provider_profile_digest": h.profile_identity_digest(profile),
                    "max_response_bytes": 1048576, "max_stream_fragments": 10000}}]}},
            cas=cas, options=h._options(), request_schema_version="bb.rl.headless-run-request.v2")
        manifest = compiled.manifest
        projection = h.E4TargetPolicyProjection.from_compiled(manifest)
        semantics = manifest.semantic.to_canonical_obj()
    finally:
        cas.close()
    observation = h._observation(provider_id="openai", model_id="model-a", capabilities=h._policy_capabilities(
        tool_calling=True, request_features=sorted(["chat_template_kwargs", "max_completion_tokens", "n", "preserve_thinking", "stream_options", "streaming"])))
    tools = tuple(h._tool_grant(name) for name in sorted(native["tools"]["ordered"]))
    controls = harness["policy"]["execution"]["bounded_controls"]
    plan = h._plan(observation=observation, semantics=semantics, tools=tools, policy_slot_ids=("model:model-a",),
        limit_updates={"max_turns": controls["max_model_calls"], "action_timeout_ms": controls["single_tool_wall_seconds"] * 1000,
                       "observation_bytes": controls["raw_evidence_bytes_per_outcome"], "transcript_bytes": 1048576},
        implementation_digest=h.CONDUCTOR_IMPLEMENTATION_DIGEST)
    base = plan.base_compiled.model_dump(mode="python")
    base.update(manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(), compiler_input_digest=manifest.inputs.compiler_input_digest)
    payload = plan.model_dump(mode="python")
    payload["base_compiled"] = h.c.CompiledArtifactIdentity.model_validate(base)
    plan = h.c.EffectiveExecutionPlan.model_validate(payload)
    class Port(h._Omp16213WorkerPort):
        async def invoke_native_phase(self, operation: str, payload: Any, **kwargs: Any) -> Any:
            result = await super().invoke_native_phase(operation, payload, **kwargs)
            if operation in ("prepare_compaction", "finalize_compaction"):
                self.phases.append({"operation": operation, "payload": h.thaw_json(payload), "result": result})
            return result
    port = Port(workspace, scratch, tuple(h.RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools))
    port.phases = []
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    stream_profile = NATIVE_STREAM_PROFILES[h.OMP_16_2_13_RESPONSE_CONSUMER_ID]
    client = h.EpisodeOpenAICompletionsPolicyClient(episode_id="episode-omp16-lane", effective_plan_digest=plan.canonical_digest(),
        observation=observation, profile=profile, target_projection=projection, timeout_seconds=stream_profile.provider_timeout_seconds)
    request = h.RunnerOpenRequest(episode_id="episode-omp16-lane", effective_plan=plan)
    session = await h.ConductorAdapter(h.CONDUCTOR_RUNTIME_ABI, containment_authenticator=h.CONDUCTOR_TEST_AUTHENTICATOR,
        admitted_lease_ledger=h.CONDUCTOR_TEST_LEDGER).open(request, policy=h.PolicyRuntimeBinding(request, client), workspace=port,
        cancellation=h._Cancellation(), events=h._Events())
    try:
        result = await session.run(h.ConductorRunRequest(task_input={"prompt": PROMPT}, context={}))
        return {"response": h.thaw_json(result.response), "phases": port.phases,
                "limits": plan.effective_capabilities.limits.model_dump(mode="json")}
    finally:
        await session.close()
        await port.close()
        await client.close()


def is_summary(body: dict[str, Any]) -> bool:
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    system = NATIVE_STREAM_PROFILES["breadboard.oh-my-pi.v16.2.13"].compaction_summary_system_prompt
    return bool(body.get("messages")) and body["messages"][0].get("content") == system


def normalize(raw: bytes, *, stock: bool, tools: Any, cap: int, settings: dict[str, Any], deviations: list[str]) -> bytes:
    body = json.loads(raw)
    if not is_summary(body):
        return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    # The established lane comparator uses canonical JSON bytes, retaining
    # every value except the explicitly admitted, value-guarded fields below.
    expected_tools = "compaction_summary_episode_tools" in deviations
    expected_cap = "compaction_summary_max_tokens" in deviations
    if expected_tools:
        if stock and "tools" in body:
            raise ValueError("Stock summary unexpectedly has tools")
        if not stock and json.dumps(body.get("tools"), sort_keys=True, separators=(",", ":"), ensure_ascii=False) != json.dumps(tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False):
            raise ValueError("BB summary tools differ from episode tools")
    if expected_cap:
        last = body["messages"][-1]["content"]
        text = last if isinstance(last, str) else "\n".join(x.get("text", "") for x in last)
        prompts = ROOT / "breadboard_engine/compaction/presets/prompts/omp@16.2.13"
        if text.endswith((prompts / "compaction-short-summary.md").read_text().strip()):
            budget = min(512, int(settings["reserveTokens"] * .2))
        elif text.endswith((prompts / "compaction-turn-prefix.md").read_text().strip()):
            budget = int(settings["reserveTokens"] * .5)
        else:
            budget = int(settings["reserveTokens"] * .8)
        # pi-ai clamps completeSimple's requested budget to model.maxTokens.
        budget = min(cap, budget)
        if type(body.get("max_completion_tokens")) is not int or body["max_completion_tokens"] != (budget if stock else cap):
            raise ValueError(f"Invalid summary budget: {body.get('max_completion_tokens')}, expected {budget if stock else cap}")
    normalized = deepcopy(body)
    if expected_tools:
        normalized.pop("tools", None)
    if expected_cap:
        normalized.pop("max_completion_tokens", None)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def run_lane(out: Path, scenario: str) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    workspace = out / "workspace"
    seed_workspace(workspace)
    with recording_provider(scenario, out / "stock-requests.jsonl") as provider:
        stock = run_stock(workspace, out / "stock-scratch", provider.base_url, out)
        stock_rows = list(provider.engine.recorded_requests)
        remaining_stock = len(provider.engine.script)
    with recording_provider(scenario, out / "bb-requests.jsonl") as provider:
        bb = asyncio.run(run_bb(workspace, out / "bb-scratch", provider.base_url))
        bb_rows = list(provider.engine.recorded_requests)
        remaining_bb = len(provider.engine.script)
    (out / "bb-result.json").write_text(json.dumps(bb, indent=2))
    native, harness = target_documents()
    deviations = [d["id"] for d in harness["policy"]["deviations"]]
    tools = bb_rows[0]["json"]["tools"]
    diffs = []
    raw_differences = []
    for index in range(max(len(stock_rows), len(bb_rows))):
        if index >= min(len(stock_rows), len(bb_rows)):
            diffs.append({"index": index, "reason": "missing_request"})
            continue
        a = bytes.fromhex(stock_rows[index]["raw_body_bytes"])
        b = bytes.fromhex(bb_rows[index]["raw_body_bytes"])
        if a != b:
            raw_differences.append(index)
        try:
            aa = normalize(a, stock=True, tools=tools, cap=native["model"]["max_tokens"], settings=stock["settings"], deviations=deviations)
            bb_bytes = normalize(b, stock=False, tools=tools, cap=native["model"]["max_tokens"], settings=stock["settings"], deviations=deviations)
            if aa != bb_bytes:
                first = next((i for i, pair in enumerate(zip(aa, bb_bytes)) if pair[0] != pair[1]), min(len(aa), len(bb_bytes)))
                diffs.append({"index": index, "first_byte": first, "stock": aa[max(0, first-60):first+160].decode(), "bb": bb_bytes[max(0, first-60):first+160].decode()})
        except ValueError as exc:
            diffs.append({"index": index, "guard_error": str(exc)})
    stock_count = sum(e["type"] == "auto_compaction_end" and e.get("result") is not None for e in stock["events"])
    bb_count = sum(p["operation"] == "finalize_compaction" and p["result"].get("kind") == "compaction_finalized" for p in bb["phases"])
    report = {"target": TARGET_ID, "scenario": scenario, "stock_requests": len(stock_rows), "bb_requests": len(bb_rows),
        "stock_compactions": stock_count, "bb_compactions": bb_count, "diffs": diffs, "raw_difference_indices": raw_differences,
        "deviations_used": deviations, "remaining_stock_responses": remaining_stock, "remaining_bb_responses": remaining_bb,
        "overrides": {"context_window": {"stock": WINDOW, "bb": WINDOW, "production": native["model"]["context_window"]}},
        "comparison": "canonical JSON bytes after guarded target deviations; raw member ordering retained in captures",
        "test_admissions": {"transcript_bytes": 1048576, "source": "lane admission; target does not declare a transcript-byte cap"},
        "stock_settings": stock["settings"], "bb_limits": bb["limits"]}
    (out / "lane-report.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS + ("all",), default="all")
    args = parser.parse_args()
    scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
    reports = [run_lane(args.out.resolve() / scenario, scenario) for scenario in scenarios]
    print(json.dumps(reports, indent=2))
    if any(r["diffs"] or r["stock_requests"] != r["bb_requests"] for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
