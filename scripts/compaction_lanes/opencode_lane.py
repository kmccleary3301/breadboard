"""Run pinned OpenCode and its pinned plugin against the production BB engine.

The stock side is the unmodified 1.2.17 binary. The plugin must be built from
5137df72d8fab3fec609c82f91387db8e3b13825, using its frozen bun.lock. The BB side
loads the dated profile through AgenticCoder, including its real provider and
compaction controller. Only endpoint credentials, isolation paths and identical
model limits are supplied by this lane. No source-slice oracle drives either side.

Raw HTTP bodies remain in requests.jsonl. Comparison uses canonical JSON member
order, not string-content normalization. No deviations are admitted by these
engine profiles. A report with differences is evidence of failure, not parity.

Run: BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. <python> -m scripts.compaction_lanes.opencode_lane
  --harness opencode --scenario auto_prune --out /tmp/opencode-auto
Set OPENCODE_PLUGIN_ROOT to the exact pinned plugin source and built dist/index.js
for --harness oh-my-opencode. Run all scenarios with --scenario all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import base64
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

import yaml

from scripts.compaction_lanes.mock_provider import MockProvider, MockRequestHandler, MockThreadingServer

class ResponsesHandler(MockRequestHandler):
    """Serve the same scripted completion in streaming and unary transports."""

    def _handle_openai_responses(self, item: dict[str, Any], request_json: Any) -> None:
        if item["type"] == "raw" and request_json.get("stream") is not True:
            event = json.loads(item["chunks"][-1].split("data: ", 1)[1])
            self._send_raw_response({"type": "raw", "headers": {"content-type": "application/json"}, "body": json.dumps(event["response"])})
            return
        super()._handle_openai_responses(item, request_json)


class ResponsesProvider(MockProvider):
    def start(self) -> str:
        self.server = MockThreadingServer((self.host, self.port), ResponsesHandler, engine=self.engine)
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url


ROOT = Path(__file__).resolve().parents[2]
STOCK_VERSION = "1.2.17"
PLUGIN_COMMIT = "5137df72d8fab3fec609c82f91387db8e3b13825"
PROFILES = {
    "opencode": "agent_configs/opencode_1-2-17_e4_10-9-2026.yaml",
    "oh-my-opencode": "agent_configs/oh_my_opencode_3-10-0_e4_10-9-2026.yaml",
}
SCENARIOS = ("auto_prune", "overflow", "final_threshold")
# Input and window are shrunk together; output stays the production 32000.
TEST_WINDOW = 64000
LOW_USAGE = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
PARITY_REFERENCE = {
    "report": "docs_tmp/RL_TEAM_HANDOFF_20261007/engine_results/PARITY_V2_REPORT.md:1-19",
    "evidence": "docs_tmp/RL_TEAM_HANDOFF_20261007/engine_results/PARITY_V2_EVIDENCE.json:/request",
    "matching_entry": None,
    "reason": "The referenced v2 campaign compares Pi 0.80.2 with BB Pi 0.57.1, not OpenCode. It cannot admit an OpenCode deviation.",
}
PLUGIN_EVIDENCE = [
    "src/index.ts:82-93 (native compaction context and todo capture)",
    "src/hooks/compaction-context-injector/hook.ts:7-71 (summary contributor)",
    "src/plugin/event.ts:137-159 (preemptive-compaction event is not dispatched)",
    "src/plugin/hooks/create-session-hooks.ts:81-85 (preemptive hook requires experimental setting)",
    "src/plugin/event.ts:149 (Anthropic overflow recovery is dispatched)",
    "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts:26-196 (largest-first recovery)",
]


def production_settings(harness: str) -> dict[str, Any]:
    path = ROOT / PROFILES[harness]
    document = yaml.safe_load(path.read_bytes())
    model_id = document["providers"]["default_model"]
    model = next(m for m in document["providers"]["models"] if m["id"] == model_id)
    return {"profile": str(path), "profile_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model_id": model_id, "model": model_id.split("/", 1)[1],
            "max_output_tokens": model["max_output_tokens"], "params": model["params"],
            "preset": document["compaction"]["preset"],
            "tools": document["tools"]["registry"]["include"],
            "overrides": {"contextWindow": {"production": document["compaction"]["contextWindow"], "lane": TEST_WINDOW},
                          "max_input_tokens": {"production": model["max_input_tokens"], "lane": TEST_WINDOW},
                          "lsp": {"stock": False, "bb_enhanced_tools_lsp_integration_enabled": False, "reason": "equal local tool isolation; production LSP wrapper recursion retained as unresolved"}}}


def responses_sse(descriptor: dict[str, Any], ordinal: int) -> dict[str, Any]:
    """Compose full Responses SSE events consumed by both real provider SDKs.

    The shared mock's abbreviated output_item.done-only stream is not sufficient
    for AI SDK 5: text/tool starts and deltas must precede completion. Its raw
    response capability keeps the recorder and error handling unchanged.
    """
    if descriptor["type"] == "error":
        return descriptor
    rid = f"resp_lane_{ordinal}"
    events: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    events.append({"type": "response.created", "response": {"id": rid, "model": "gpt-5.1-codex-mini", "status": "in_progress", "created_at": 1700000000, "output": []}})
    text = descriptor.get("text")
    if text is not None:
        mid = f"msg_lane_{ordinal}"
        index = len(output)
        item = {"type": "message", "id": mid, "role": "assistant", "status": "in_progress", "content": []}
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        part = {"type": "output_text", "text": "", "annotations": []}
        events.append({"type": "response.content_part.added", "item_id": mid, "output_index": index, "content_index": 0, "part": part})
        events.append({"type": "response.output_text.delta", "item_id": mid, "output_index": index, "content_index": 0, "delta": text})
        events.append({"type": "response.output_text.done", "item_id": mid, "output_index": index, "content_index": 0, "text": text})
        part = {**part, "text": text}
        events.append({"type": "response.content_part.done", "item_id": mid, "output_index": index, "content_index": 0, "part": part})
        item = {**item, "status": "completed", "content": [part]}
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
        output.append(item)
    for call in descriptor.get("tool_calls", []):
        index = len(output)
        item = {"type": "function_call", "id": f"fc_lane_{ordinal}_{index}", "call_id": call["call_id"], "name": call["name"], "arguments": "", "status": "in_progress"}
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        events.append({"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": index, "delta": call["arguments"]})
        events.append({"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": index, "arguments": call["arguments"]})
        item = {**item, "arguments": call["arguments"], "status": "completed"}
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
        output.append(item)
    usage = {**descriptor["usage"], "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    events.append({"type": "response.completed", "response": {"id": rid, "model": "gpt-5.1-codex-mini", "status": "completed", "created_at": 1700000000, "output": output, "usage": usage}})
    return {"type": "raw", "status_code": 200, "headers": {"content-type": "text/event-stream"},
            "chunks": [f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events]}


def text_response(text: str, usage: dict[str, int] = LOW_USAGE) -> dict[str, Any]:
    return {"type": "text", "text": text, "usage": dict(usage)}


def tool_response(prefix: str, count: int, chars: int, usage: dict[str, int] = LOW_USAGE) -> dict[str, Any]:
    calls = [{"name": "bash", "call_id": f"call_{prefix}_{i}",
              "arguments": json.dumps({"command": f"printf '%0{chars}d' 0", "description": f"Read fixture {prefix} {i}"}, separators=(",", ":"))} for i in range(count)]
    return {"type": "response", "tool_calls": calls, "usage": dict(usage)}


def scenario_turns(scenario: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    # OpenCode's native input reserve is min(20000, capped output).
    threshold = TEST_WINDOW - min(20000, settings["max_output_tokens"])
    high = {"input_tokens": threshold + 1, "output_tokens": 50, "total_tokens": threshold + 51}
    if scenario == "auto_prune":
        # 12 * 24000 chars exceed both native 40k protected and 20k minimum
        # savings budgets. Three completed user turns precede the compactions.
        return [
            {"prompt": "Inspect the numbered records.", "responses": [tool_response("seed", 12, 24000), text_response("First inspection complete.")]},
            {"prompt": "Record the first inspection.", "responses": [text_response("Second inspection complete.")]},
            {"prompt": "Record the second inspection.", "responses": [text_response("Third inspection complete.")]},
            {"prompt": "Continue the inspection and finish.", "responses": [tool_response("high1", 1, 8, high), text_response("Summary one: first inspection complete."), tool_response("high2", 1, 8, high), text_response("Summary two: second inspection complete."), text_response("Inspection complete.")]},
        ]
    if scenario == "overflow":
        error = {"type": "error", "status_code": 400, "error": "context_length_exceeded", "message": f"prompt is too long: {TEST_WINDOW + 10000} tokens > {TEST_WINDOW} maximum"}
        return [
            {"prompt": "Inspect the numbered records.", "responses": [tool_response("overflow_seed", 12, 24000), text_response("First inspection complete.")]},
            {"prompt": "Finish the inspection.", "responses": [error, text_response("Overflow summary: inspection in progress."), text_response("Inspection complete.")]},
        ]
    if scenario == "final_threshold":
        return [{"prompt": "Say the analysis is complete.", "responses": [text_response("Inspection complete above threshold.", high), text_response("Final summary: inspection complete."), text_response("Inspection complete.")]}]
    raise ValueError(scenario)


def resolve_stock() -> Path:
    cli = Path(os.environ.get("OPENCODE_TEST_BINARY", str(Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-darwin-arm64@1.2.17/package/bin/opencode")))
    result = subprocess.run([str(cli), "--version"], input="", capture_output=True, text=True, timeout=20)
    if result.returncode or result.stdout.strip() != STOCK_VERSION:
        raise ValueError(f"Expected OpenCode {STOCK_VERSION}: {result.stdout!r} {result.stderr!r}")
    return cli


def plugin_root() -> Path:
    source = Path(os.environ["OPENCODE_PLUGIN_ROOT"]).resolve()
    metadata = json.loads((source / "package.json").read_bytes())
    if metadata["name"] != "oh-my-opencode" or metadata["version"] != "3.10.0":
        raise ValueError("Plugin package must be oh-my-opencode@3.10.0")
    # Source snapshot identity must be explicit, never inferred from the npm version.
    if source.name != f"oh-my-openagent-{PLUGIN_COMMIT}":
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, timeout=10)
        if result.returncode or result.stdout.strip() != PLUGIN_COMMIT:
            raise ValueError(f"Plugin source must be pinned to {PLUGIN_COMMIT}")
    if not (source / "dist/index.js").is_file():
        raise FileNotFoundError(source / "dist/index.js")
    return source


def run_stock(harness: str, scenario: str, workspace: Path, scratch: Path, base_url: str) -> dict[str, Any]:
    settings = production_settings(harness)
    tools = {"*": False, **{name: True for name in settings["tools"]}}
    agent = {"tools": tools, **settings["params"]}
    config: dict[str, Any] = {"model": settings["model_id"], "provider": {"openai": {"options": {"baseURL": base_url, "apiKey": "lane-mock"}, "models": {settings["model"]: {"limit": {"context": TEST_WINDOW, "input": TEST_WINDOW, "output": settings["max_output_tokens"]}}}}}, "agent": {"build": agent}, "permission": "allow", "compaction": {"auto": True, "prune": True}}
    config["lsp"] = False
    if harness == "oh-my-opencode":
        config["plugin"] = [(plugin_root() / "dist/index.js").as_uri()]
    env = {**os.environ, "HOME": str(scratch / "home"), "OPENCODE_TEST_HOME": str(scratch / "home"),
           "XDG_CONFIG_HOME": str(scratch / "config"), "XDG_DATA_HOME": str(scratch / "data"), "XDG_CACHE_HOME": str(scratch / "cache"), "XDG_STATE_HOME": str(scratch / "state"),
           "OPENCODE_CONFIG_CONTENT": json.dumps(config), "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}
    cli = resolve_stock()
    session_id = None
    turns = []
    for turn in scenario_turns(scenario, settings):
        # CLI quotes a positional string containing spaces and appends a newline
        # on non-TTY stdin. Supply the exact prompt as stdin instead.
        command = [str(cli), "run", "--format", "json", "--print-logs", "--title", "Compaction lane", "--agent", "build"]
        if session_id:
            command += ["--session", session_id]
        result = subprocess.run(command, cwd=workspace, env=env, input=turn["prompt"], capture_output=True, text=True, timeout=120)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        if session_id is None:
            session_id = next((event["sessionID"] for event in events if "sessionID" in event), None)
        turns.append({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "events": events})
        if result.returncode or not session_id:
            break
    return {"turns": turns, "session_id": session_id, "stock_config": config}


def run_bb(harness: str, scenario: str, workspace: Path, base_url: str) -> dict[str, Any]:
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider.routing import provider_router
    settings = production_settings(harness)
    # Recording-provider transport binding only. Compaction/provider runtimes
    # and their serializers are not replaced.
    route = provider_router.providers["openai"]
    route.base_url = base_url
    route.auth_owner = "none"
    route.credential_required = False
    agent = AgenticCoder(settings["profile"], str(workspace), overrides={"compaction.contextWindow": TEST_WINDOW, "providers.models[0].max_input_tokens": TEST_WINDOW, "enhanced_tools.lsp_integration.enabled": False}, force_local_mode=True)
    retained: list[dict[str, Any]] = []
    facts: list[str] = []
    events: list[dict[str, Any]] = []
    turns = []
    for index, turn in enumerate(scenario_turns(scenario, settings)):
        def receive(kind: str, payload: dict[str, Any], turn: int | None = None) -> None:
            events.append({"type": kind, "payload": payload, "turn": turn})
        context = {"session_id": "opencode-lane", "input_id": f"input-{index}", "turn_id": f"turn-{index}", "retained_effective_messages": retained, "retained_raw_fact_ids": facts, "_product_compaction_owner": True}
        result = agent.run_task("\n" + turn["prompt"], max_iterations=40, stream=True, event_emitter=receive, context=context)
        snapshots = [event["payload"] for event in events if event["type"] == "conversation.compaction.end"]
        if snapshots:
            snapshot = snapshots[-1]
            retained = json.loads(base64.b64decode(snapshot["effective_context"]))
            facts = snapshot["raw_fact_ids"]
        else:
            raise RuntimeError("Production engine did not emit the retained product context")
        turns.append(result)
    return {"turns": turns, "events": events, "retained_effective_messages": retained}


def canonical(body: Any) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def is_summary(row: dict[str, Any]) -> bool:
    body = row["json"]
    return isinstance(body, dict) and "You are a helpful AI assistant tasked with summarizing conversations." in json.dumps(body.get("input", []))


def content_text(content: Any) -> str:
    """Read text for diagnostic classification; never rewrite compared bodies."""
    if isinstance(content, str):
        return content
    return "".join(part["text"] for part in content or [] if isinstance(part, dict) and isinstance(part.get("text"), str))


def compaction_input_findings(stock: dict[str, Any], bb: dict[str, Any], *, summary: bool, baseline_users: tuple[tuple[str, str], ...] = (), baseline_outputs: set[tuple[bytes, bytes]] | None = None) -> list[str]:
    left, right = stock.get("input", []), bb.get("input", [])
    findings = []
    if summary:
        systems = lambda items: [(item["role"], content_text(item.get("content"))) for item in items if item.get("role") in {"system", "developer"}]
        if systems(left) != systems(right):
            findings.append("summary system text differs")
        # The ordinary BB ledger interleaves calls and outputs, unlike stock's
        # grouped calls. Keep that array-order baseline in the strict diff;
        # still guard every retained call/output value and per-kind order.
        for kind in ("function_call", "function_call_output"):
            parts = lambda items: [item for item in items if item.get("type") == kind]
            stock_parts, bb_parts = parts(left), parts(right)
            if len(stock_parts) != len(bb_parts) or any(
                canonical(a) != canonical(b) and not (kind == "function_call_output" and baseline_outputs is not None and (canonical(a), canonical(b)) in baseline_outputs)
                for a, b in zip(stock_parts, bb_parts)
            ):
                findings.append(f"summary {kind} history differs")
        users = lambda items: [content_text(item.get("content")) for item in items if item.get("role") == "user"]
        stock_users, bb_users = users(left), users(right)
        if len(stock_users) != len(bb_users) or any(a != b and (a, b) not in baseline_users for a, b in zip(stock_users, bb_users)):
            findings.append("summary user history or prompt differs")
        # model_output.py:160-161 already mutates zero-tool answers before
        # compaction. Diagnose that inherited baseline rendering without ever
        # changing its strict body diff, digest, or compared bytes.
        assistants = lambda items: [content_text(item.get("content")) for item in items if item.get("role") == "assistant"]
        stock_assistants, bb_assistants = assistants(left), assistants(right)
        if len(stock_assistants) != len(bb_assistants) or any(
            candidate != text and candidate != text.rstrip() + "\n\n>>>>>> END RESPONSE"
            for text, candidate in zip(stock_assistants, bb_assistants)
        ):
            findings.append("summary assistant history differs in count, content, or order")
        masked = lambda items: sum(item.get("type") == "function_call_output" and item.get("output") == "[Old tool result content cleared]" for item in items)
        if masked(left) != masked(right):
            findings.append("summary pruned tool-output count differs")
    else:
        marker = "What did we do so far?"
        def bridge(items):
            for index, item in enumerate(items):
                if item.get("role") == "user" and content_text(item.get("content")) == marker:
                    return [(part.get("role"), content_text(part.get("content"))) for part in items[index:index + 3]]
            return None
        expected = bridge(left)
        if expected is not None and bridge(right) != expected:
            findings.append("post-compaction summary bridge differs")
    return findings


def compare_records(stock: list[dict[str, Any]], bb: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diffs = []
    baseline_users = ()
    if stock and bb and not is_summary(stock[0]) and not is_summary(bb[0]):
        texts = lambda row: [content_text(item.get("content")) for item in row["json"].get("input", []) if item.get("role") == "user"]
        # Exact inherited differences already present before compaction, e.g.
        # the stock plugin's keyword-detector analysis prefix. Classify them;
        # retain the complete differing bodies and bytes without normalization.
        baseline_users = tuple((a, b) for a, b in zip(texts(stock[0]), texts(bb[0])) if a != b)
    baseline_outputs = set()
    for left, right in zip(stock, bb):
        if is_summary(left) or is_summary(right):
            break
        outputs = lambda row: {item["call_id"]: item for item in row["json"].get("input", []) if item.get("type") == "function_call_output"}
        a, b = outputs(left), outputs(right)
        # The first tool-bearing request precedes user-turn pruning in these
        # scenarios. Capture only that exact inherited output difference
        # (e.g. the plugin's category-skill-reminder), never later pruning.
        if a or b:
            baseline_outputs.update((canonical(a[key]), canonical(b[key])) for key in a.keys() & b.keys() if canonical(a[key]) != canonical(b[key]))
            break
    if len(stock) != len(bb):
        diffs.append({"kind": "request_count", "classification": "compaction-path", "stock": len(stock), "bb": len(bb)})
    for index, (left, right) in enumerate(zip(stock, bb)):
        if canonical(left["json"]) == canonical(right["json"]) and left["path"] == right["path"]:
            continue
        summary = is_summary(left) or is_summary(right)
        findings = compaction_input_findings(left["json"], right["json"], summary=summary, baseline_users=baseline_users, baseline_outputs=baseline_outputs)
        keys = sorted(set(left["json"]) | set(right["json"]))
        fields = []
        for key in keys:
            if key not in left["json"] or key not in right["json"] or canonical(left["json"][key]) != canonical(right["json"][key]):
                classification = "compaction-path" if summary and key in {"tools", "tool_choice", "max_output_tokens", "stream"} else "pre-existing baseline"
                if key == "input" and findings:
                    classification = "compaction-path"
                fields.append({"field": key, "classification": classification, "stock_present": key in left["json"], "bb_present": key in right["json"], "baseline_reference": PARITY_REFERENCE if classification == "pre-existing baseline" else None})
        diffs.append({"kind": "request_body", "index": index, "stock_summary": is_summary(left), "bb_summary": is_summary(right), "fields": fields, "compaction_input_findings": findings, "inherited_user_baseline_pairs": baseline_users if summary else (),
                      "stock_raw_sha256": hashlib.sha256(bytes.fromhex(left["raw_body_bytes"])).hexdigest(), "bb_raw_sha256": hashlib.sha256(bytes.fromhex(right["raw_body_bytes"])).hexdigest()})
    return diffs


def run_scenario(harness: str, scenario: str, out: Path) -> dict[str, Any]:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    workspace = out / "workspace"
    workspace.mkdir(exist_ok=True)
    settings = production_settings(harness)
    descriptors = [response for turn in scenario_turns(scenario, settings) for response in turn["responses"]]
    script = [responses_sse(response, i) for i, response in enumerate(descriptors)]
    records: dict[str, list[dict[str, Any]]] = {}
    sides: dict[str, Any] = {}
    # Each side gets a fresh stock/engine session and the identical scripted
    # provider. Same literal workspace path, no path-based comparator rewrites.
    with ResponsesProvider(script=script) as provider:
        for side in ("stock", "bb"):
            provider.engine.set_script(script)
            provider.engine.recorded_requests.clear()
            result_path = out / f"{side}_result.json"
            if result_path.exists():
                raise FileExistsError(f"Use a fresh lane output directory: {result_path}")
            command = [sys.executable, "-m", "scripts.compaction_lanes.opencode_lane", "--child", side, "--harness", harness, "--scenario", scenario, "--out", str(out), "--base-url", provider.base_url + "/v1"]
            process = subprocess.run(command, cwd=ROOT, env={**os.environ, "BB_WORKSPACE_ROOT": str(ROOT), "PYTHONPATH": str(ROOT), "RAY_SCE_LOCAL_MODE": "1", "RAY_SCE_SKIP_LSP": "1"}, input="", capture_output=True, text=True, timeout=360)
            (out / f"{side}.stdout").write_text(process.stdout)
            (out / f"{side}.stderr").write_text(process.stderr)
            sides[side] = json.loads(result_path.read_bytes()) if result_path.is_file() else {"error": process.stderr, "returncode": process.returncode}
            sides[side]["unconsumed_responses"] = len(provider.engine.script)
            records[side] = list(provider.engine.recorded_requests)
            (out / f"{side}_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records[side]))
    diffs = compare_records(records["stock"], records["bb"])
    unresolved = [{"side": side, "error": value["error"]} for side, value in sides.items() if "error" in value]
    unresolved.append({"classification": "daily-driving outside compaction", "defect": "Local LSPEnhancedSandbox wraps LocalActorProxy remotely; serialization recursion prevents native shell execution.", "evidence": "breadboard_engine/conductor/components.py:579-585; /tmp/opencode-e4-lanes-r4/auto_prune/bb_result.json", "equal_override": "stock lsp:false; BB enhanced_tools.lsp_integration.enabled:false", "approved_by": "Main"})
    for side, result in sides.items():
        if result["unconsumed_responses"]:
            unresolved.append({"side": side, "unconsumed_responses": result["unconsumed_responses"]})
    counts = {side: {"requests": len(rows), "compactions": sum(is_summary(row) for row in rows), "requests_with_pruned_output": sum("[Old tool result content cleared]" in row["raw_body"] for row in rows)} for side, rows in records.items()}
    expected = 2 if scenario == "auto_prune" else 1
    scenario_findings = []
    if counts["stock"]["requests"] != counts["bb"]["requests"]:
        scenario_findings.append({"defect": "request counts differ", "stock": counts["stock"]["requests"], "bb": counts["bb"]["requests"]})
    for side, count in counts.items():
        if count["compactions"] != expected:
            scenario_findings.append({"side": side, "defect": "summary count", "expected": expected, "actual": count["compactions"]})
        if scenario == "auto_prune" and not count["requests_with_pruned_output"]:
            scenario_findings.append({"side": side, "defect": "pruning not visible in following requests"})
    scenario_findings.extend({"index": diff["index"], "defect": finding} for diff in diffs if diff["kind"] == "request_body" for finding in diff["compaction_input_findings"])
    scenario_findings.extend({"index": diff["index"], "defect": f"summary request field differs: {field['field']}"} for diff in diffs if diff["kind"] == "request_body" for field in diff["fields"] if field["classification"] == "compaction-path" and field["field"] != "input")
    if scenario == "auto_prune":
        checkpoints = {side: [index for index, row in enumerate(rows) if any(item.get("type") == "function_call_output" and item.get("output") == "[Old tool result content cleared]" for item in row["json"].get("input", []))] for side, rows in records.items()}
        if checkpoints["stock"] != checkpoints["bb"]:
            scenario_findings.append({"defect": "pruning request checkpoints differ", **checkpoints})
    report = {"harness": harness, "scenario": scenario, "settings": settings, "stock_version": STOCK_VERSION, "plugin_commit": PLUGIN_COMMIT if harness == "oh-my-opencode" else None,
              "plugin_source_evidence": PLUGIN_EVIDENCE if harness == "oh-my-opencode" else [], "comparison_basis": "Canonical JSON member order; raw request bytes retained; all values and arrays strict", "environment_regexes": [], "deviations_used": [],
              "counts": counts, "diffs": diffs, "unresolved": unresolved, "compaction_findings": scenario_findings, "compaction_checks_passed": not scenario_findings and not any(result["unconsumed_responses"] or "error" in result for result in sides.values()), "passed": not diffs and not unresolved,
              "artifacts": {side: {"requests": str(out / f"{side}_requests.jsonl"), "result": str(out / f"{side}_result.json")} for side in sides}}
    (out / "lane_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=tuple(PROFILES), required=True)
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--child", choices=("stock", "bb"))
    parser.add_argument("--base-url")
    args = parser.parse_args()
    if args.child:
        if args.child == "stock":
            result = run_stock(args.harness, args.scenario, args.out / "workspace", args.out / "stock_runtime", args.base_url)
        else:
            result = run_bb(args.harness, args.scenario, args.out / "workspace", args.base_url)
        (args.out / f"{args.child}_result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        return
    scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
    results = [run_scenario(args.harness, scenario, args.out / scenario) for scenario in scenarios]
    print(json.dumps([{key: report[key] for key in ("harness", "scenario", "counts", "passed", "unresolved")} | {"diff_count": len(report["diffs"])} for report in results], indent=2))
    raise SystemExit(0 if all(report["passed"] for report in results) else 1)


if __name__ == "__main__":
    main()
