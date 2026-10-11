"""Run pinned OpenCode and its pinned plugin against the production BB engine.

The stock side is the unmodified 1.2.17 binary. The plugin must be built from
5137df72d8fab3fec609c82f91387db8e3b13825, using its frozen bun.lock. The BB side
loads the dated profile through AgenticCoder, including its real provider and
compaction controller. Only endpoint credentials, isolation paths and identical
model limits are supplied by this lane. No source-slice oracle drives either side.

Raw HTTP bodies remain in requests.jsonl. Comparison uses canonical JSON member
order and source-proven admitted deviations, never string-content normalization.
A report with unadmitted differences is evidence of failure, not parity.

Run: BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. <python> -m scripts.compaction_lanes.opencode_lane
  --harness opencode --scenario auto_prune --out /tmp/opencode-auto
The plugin installation defaults to the dated profile's digest-pinned artifact;
OPENCODE_PLUGIN_ROOT may select that same artifact explicitly. Run all scenarios
with --scenario all; the plugin additionally exercises delegated summaries.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import json
import os
import base64
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import re
import shutil
import socket
import urllib.request
import urllib.parse
import time
import traceback
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import yaml

from scripts.compaction_lanes.mock_provider import MockProvider, MockRequestHandler, MockThreadingServer, MockScriptEngine

class SessionScriptEngine(MockScriptEngine):
    """Keep the background request off the root's scripted response queue."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = threading.local()
        self.child_script = None

    def set_child_script(self, root_session_id: str, script: list[dict[str, Any]]):
        """Use the same auxiliary FIFO for direct stock and native forwarding.

        Descriptors are ordinary responses_sse results, or delegated_pending
        to hold a real generation until delegated_release is set.
        """
        with self._lock:
            self.delegated_root = root_session_id
            self.child_script = list(script)
        self.delegated_arrived = threading.Event()
        self.delegated_release = threading.Event()
        self.delegated_finished = threading.Event()

    def record_request(self, *, method, path, headers, raw_body, parsed_json):
        self.request.body = parsed_json
        root = getattr(self, "delegated_root", None)
        if root is not None and parsed_json.get("prompt_cache_key") == root and any(
            item.get("type") == "function_call_output" and item.get("call_id") == "call_delegated"
            for item in parsed_json.get("input", [])
        ):
            # Both generations are real stock requests. Admit the first root
            # continuation only after the child's request has been recorded.
            if not self.delegated_arrived.wait(10):
                raise TimeoutError("The real delegated model request did not arrive")
        return super().record_request(method=method, path=path, headers=headers,
                                      raw_body=raw_body, parsed_json=parsed_json)

    def next_response(self):
        root = getattr(self, "delegated_root", None)
        if root is not None and self.request.body.get("prompt_cache_key") != root:
            if self.child_script is None:
                return {"type": "delegated_pending"}
            with self._lock:
                if not self.child_script:
                    raise RuntimeError("Mock auxiliary script exhausted")
                return self.child_script.pop(0)
        return super().next_response()


class ResponsesHandler(MockRequestHandler):
    """Serve the same scripted completion in streaming and unary transports."""

    def do_GET(self):
        if self.path not in {"/v1/lane/script-progress", "/v1/lane/release-child"}:
            return super().do_GET()
        engine = self.server.engine
        if self.path == "/v1/lane/release-child":
            engine.delegated_release.set()
        with engine._lock:
            body = json.dumps({"remaining": len(engine.script)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_openai_responses(self, item: dict[str, Any], request_json: Any) -> None:
        engine = self.server.engine
        root = getattr(engine, "delegated_root", None)
        if root is not None and request_json.get("prompt_cache_key") != root:
            engine.delegated_arrived.set()
        if item["type"] == "delegated_pending":
            # Real background generation remains active while the root compacts.
            # Both native processes cancel it when this scripted episode closes.
            if not engine.delegated_release.wait(360):
                raise TimeoutError("The delegated generation was not cleaned up")
            self.close_connection = True
            engine.delegated_finished.set()
            return
        if item["type"] == "delegated_complete":
            if not engine.delegated_release.wait(360):
                raise TimeoutError("The delegated completion was not released")
            try:
                self._handle_openai_responses(item["response"], request_json)
            finally:
                engine.delegated_finished.set()
            return
        if item["type"] == "raw" and request_json.get("stream") is not True:
            event = json.loads(item["chunks"][-1].split("data: ", 1)[1])
            self._send_raw_response({"type": "raw", "headers": {"content-type": "application/json"}, "body": json.dumps(event["response"])})
            return
        super()._handle_openai_responses(item, request_json)


class ResponsesProvider(MockProvider):
    def __init__(self, script=None, record_path=None, host="127.0.0.1", port=0):
        super().__init__(script=script, record_path=None, host=host, port=port)
        self.engine = SessionScriptEngine(script=script, record_path=record_path)

    def start(self) -> str:
        self.server = MockThreadingServer((self.host, self.port), ResponsesHandler, engine=self.engine)
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url


class MCPRelay:
    """Forward real MCP bytes; serialize native create().listTools readiness.

    Stock index.ts:192-213 inserts clients after create(), whose final operation
    is listTools (:503-529). No tool schemas/results or LLM arrays are rewritten.
    """
    UPSTREAMS = {
        "websearch": "https://mcp.exa.ai/mcp?tools=web_search_exa",
        "context7": "https://mcp.context7.com/mcp",
        "grep_app": "https://mcp.grep.app",
    }
    def __init__(self):
        self.condition = threading.Condition()
        self.completed: set[str] = set()
        self.first_lists: set[str] = set()
        self.failures: list[str] = []

    def reset(self) -> None:
        with self.condition:
            self.completed.clear()
            self.first_lists.clear()
            self.condition.notify_all()

    def __enter__(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_args):
                pass
            def forward(self):
                import httpx
                if self.path == "/reset":
                    owner.reset()
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                name = self.path.removeprefix("/mcp/")
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                method = json.loads(body).get("method") if body else None
                ordered = False
                if method == "tools/list":
                    with owner.condition:
                        ordered = name not in owner.first_lists
                        owner.first_lists.add(name)
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in {"host", "content-length", "connection"}}
                try:
                    with httpx.Client(timeout=60) as client:
                        request = client.build_request(self.command, owner.UPSTREAMS[name],
                                                       headers=headers, content=body)
                        response = client.send(request, stream=True)
                        if ordered:
                            previous = list(owner.UPSTREAMS)[:list(owner.UPSTREAMS).index(name)]
                            with owner.condition:
                                if not owner.condition.wait_for(lambda: all(key in owner.completed for key in previous), 30):
                                    raise TimeoutError(f"MCP readiness stalled before {name}")
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in {"transfer-encoding", "connection"}:
                                self.send_header(key, value)
                        self.send_header("Connection", "close")
                        self.end_headers()
                        for chunk in response.iter_raw():
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        response.close()
                        if ordered:
                            # Scheduling only, not persisted/model data. Let the
                            # source event-loop settle before releasing the next.
                            time.sleep(1)
                            with owner.condition:
                                owner.completed.add(name)
                                owner.condition.notify_all()
                except Exception as exc:
                    owner.failures.append(f"{name}: {exc}")
                    self.close_connection = True
            do_POST = forward
            do_GET = forward
            do_DELETE = forward
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def mcp_configuration(relay_url: str | None) -> dict[str, Any]:
    return {"mcp": {name: {"type": "remote", "url": relay_url + "/mcp/" + name,
                           "enabled": True, "oauth": False}
                    for name in MCPRelay.UPSTREAMS}} if relay_url is not None else {}


def reset_mcp_relay(relay_url: str | None) -> None:
    if relay_url is not None:
        with urllib.request.urlopen(urllib.request.Request(relay_url + "/reset", data=b"", method="POST"), timeout=10):
            pass


ROOT = Path(__file__).resolve().parents[2]
STOCK_VERSION = "1.2.17"
PLUGIN_COMMIT = "5137df72d8fab3fec609c82f91387db8e3b13825"
PROFILES = {
    "opencode": "agent_configs/opencode_1-2-17_e4_10-9-2026.yaml",
    "oh-my-opencode": "agent_configs/oh_my_opencode_3-10-0_e4_10-9-2026.yaml",
}
SCENARIOS = ("auto_prune", "overflow", "final_threshold")
PLUGIN_SCENARIOS = ("delegate_prune", "plugin_error", "delegate_idle")
# Input and window are shrunk together; output stays the production 32000.
TEST_WINDOW = 64000
LOW_USAGE = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
SOURCE_API_USERNAME = "opencode"
SOURCE_API_PASSWORD = "isolated-compaction-lane"
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
    "src/features/background-agent/manager.ts:1255-1287,1359-1367 (completed child autonomously prompts its idle parent)",
]


def production_settings(harness: str) -> dict[str, Any]:
    path = ROOT / PROFILES[harness]
    document = yaml.safe_load(path.read_bytes())
    model_id = document["providers"]["default_model"]
    model = next(m for m in document["providers"]["models"] if m["id"] == model_id)
    from breadboard_engine.compaction.presets import load_preset_document
    preset = load_preset_document(document["compaction"]["preset"])
    recovery_attempts = sum(trigger["max_attempts"] for trigger in preset["triggers"]
                            if trigger["kind"] == "pending_error")
    return {"profile": str(path), "profile_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "model_id": model_id, "model": model_id.split("/", 1)[1],
            "max_output_tokens": model["max_output_tokens"], "params": model["params"],
            "preset": document["compaction"]["preset"],
            "deferred_recovery_attempts": recovery_attempts,
            "tools": document["tools"]["registry"]["include"],
            "source_api_authentication": {
                "mode": "HTTP Basic authentication on both source serve processes",
                "username": SOURCE_API_USERNAME,
                "stock_password": "Fixed isolated fixture credential; not a provider credential",
                "bb_password": "Production sidecar's generated isolated credential",
                "configuration_fact_not_deviation": True,
                "source": [
                    "packages/opencode/src/cli/cmd/serve.ts:14-15",
                    "packages/opencode/src/cli/cmd/web.ts:36-37",
                    "src/shared/opencode-server-auth.ts:8-17",
                    "src/shared/opencode-http-api.ts:68-72",
                ],
                "unauthenticated_behavior": "Native aggressive PATCH returns false without a password; the plugin falls through to summarize",
            },
            "overrides": {"contextWindow": {"production": document["compaction"]["contextWindow"], "lane": TEST_WINDOW},
                          "max_input_tokens": {"production": model["max_input_tokens"], "lane": TEST_WINDOW},
                          "lsp": {"stock": False, "bb_stock_executor": False, "reason": "equal local tool isolation; stock owns LSP, not the legacy BB wrapper"},
                          "stock_environment": {"OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}}}


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
    # Valid formatted model arguments exercise stock's native JSON.stringify
    # replay; pre-minifying these would conceal an argument-codec difference.
    calls = [{"name": "bash", "call_id": f"call_{prefix}_{i}",
              # Stock executes this batch concurrently. Stagger completion at
              # the scenario source so both native executions put the plugin's
              # third-completed-tool reminder on the same actual call.
              "arguments": json.dumps({"command": f"sleep {i * 0.2:.1f}; printf '%0{chars}d' 0", "description": f"Read fixture {prefix} {i}"}, indent=2)} for i in range(count)]
    return {"type": "response", "tool_calls": calls, "usage": dict(usage)}


def scenario_turns(scenario: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    # OpenCode's native input reserve is min(20000, capped output).
    threshold = TEST_WINDOW - min(20000, settings["max_output_tokens"])
    high = {"input_tokens": threshold + 1, "output_tokens": 50, "total_tokens": threshold + 51}
    if scenario == "delegate_idle":
        call = {"name": "task", "call_id": "call_delegated",
                "arguments": json.dumps({"subagent_type": "explore", "load_skills": [],
                                         "description": "Delegated inspection",
                                         "prompt": "Inspect the delegated fixture and report completion.",
                                         "run_in_background": True}, separators=(",", ":"))}
        return [
            {"prompt": "Delegate the inspection and wait for its completion.",
             "responses": [{"type": "response", "tool_calls": [call], "usage": dict(LOW_USAGE)},
                           text_response("Parent waiting for delegated inspection.")],
             "autonomous_responses": [
                 tool_response("idle_high1", 1, 8, high),
                 text_response("Autonomous summary one: delegated inspection complete."),
                 tool_response("idle_high2", 1, 8, high),
                 text_response("Autonomous summary two: delegated inspection complete."),
                 text_response("Autonomous inspection complete.")]},
            {"prompt": "Confirm the delegated inspection is complete.",
             "responses": [text_response("The delegated inspection is complete.")]},
        ]
    if scenario == "delegate_prune":
        turns = scenario_turns("auto_prune", settings)
        call = {"name": "task", "call_id": "call_delegated",
                "arguments": json.dumps({"subagent_type": "explore", "load_skills": [],
                                         "description": "Delegated inspection",
                                         "prompt": "Inspect the delegated fixture while the parent compacts.",
                                         "run_in_background": True}, separators=(",", ":"))}
        turns[-1]["responses"][:0] = [
            {"type": "response", "tool_calls": [call], "usage": dict(LOW_USAGE)}]
        return turns
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
        responses = [error, text_response("Overflow summary: inspection in progress."), text_response("Inspection complete.")]
        if settings["deferred_recovery_attempts"]:
            # Successful summarize keeps pending state; the source preset
            # names the budget. Both real sides receive this identical script.
            for attempt in range(1, settings["deferred_recovery_attempts"] + 1):
                responses.extend([text_response(f"Deferred recovery summary {attempt}: inspection complete."),
                                  text_response(f"Inspection complete after deferred recovery {attempt}.")])
        turns = [
            {"prompt": "Inspect the numbered records.", "responses": [tool_response("overflow_seed", 12, 24000), text_response("First inspection complete.")]},
        ]
        if settings["deferred_recovery_attempts"]:
            # Native prune protects the two newest user turns. Complete actual
            # acknowledgements before delivering overflow, so its 300 ms hook
            # sees the same six eligible outputs regardless of model latency.
            turns.extend([
                {"prompt": "Record the first inspection.", "responses": [text_response("Second inspection complete.")]},
                {"prompt": "Record the second inspection.", "responses": [text_response("Third inspection complete.")],
                 "native_prune_call_ids": [f"call_overflow_seed_{i}" for i in range(6)]},
            ])
        return [*turns, {"prompt": "Finish the inspection.", "responses": responses}]
    if scenario == "plugin_error":
        if not settings["deferred_recovery_attempts"]:
            raise ValueError("The plugin-only error scenario requires its production pending_error trigger")
        responses = [{"type": "error", "status_code": 400, "error": "invalid_request_error",
                      "message": "too many tokens: 74000 tokens > 64000 maximum"}]
        for attempt in range(1, settings["deferred_recovery_attempts"] + 1):
            responses.extend([text_response(f"Plugin-only recovery summary {attempt}: inspection complete."),
                              text_response(f"Inspection complete after plugin-only recovery {attempt}.")])
        return [{"prompt": "Finish the inspection.", "responses": responses}]
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
    document = yaml.safe_load((ROOT / PROFILES["oh-my-opencode"]).read_bytes())
    source = Path(os.environ.get("OPENCODE_PLUGIN_ROOT", document["tools"]["stock_plugin_root"])).expanduser().resolve()
    metadata = json.loads((source / "package.json").read_bytes())
    if metadata["name"] != "oh-my-opencode" or metadata["version"] != "3.10.0":
        raise ValueError("Plugin package must be oh-my-opencode@3.10.0")
    from breadboard_engine.security.stock_install import stock_install_digest
    pin = next(entry for entry in document["tools"]["stock_read_only_roots"]
               if Path(entry["path"]).expanduser().resolve() == source)
    if stock_install_digest(source) != pin["digest"]:
        raise ValueError(f"Plugin installation does not match the profile's pinned {PLUGIN_COMMIT} artifact")
    if not (source / "dist/index.js").is_file():
        raise FileNotFoundError(source / "dist/index.js")
    return source


def stock_environment(config: dict[str, Any], scratch: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(scratch / "home"), "OPENCODE_TEST_HOME": str(scratch / "home"),
            **{"XDG_" + name.upper() + "_HOME": str(scratch / name)
               for name in ("config", "data", "cache", "state")},
            "OPENCODE_CONFIG_CONTENT": json.dumps(config),
            "OPENCODE_SERVER_USERNAME": SOURCE_API_USERNAME,
            "OPENCODE_SERVER_PASSWORD": SOURCE_API_PASSWORD,
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}


@contextmanager
def stock_server(workspace: Path, scratch: Path, config: dict[str, Any]):
    """Keep one unmodified source instance alive for the entire session."""
    ready = threading.Event()
    endpoints: list[str] = []
    logs: list[str] = []
    process = subprocess.Popen(
        [str(resolve_stock()), "serve", "--hostname", "127.0.0.1", "--port", "0"],
        cwd=workspace, env=stock_environment(config, scratch),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    def collect() -> None:
        for line in process.stdout:
            logs.append(line)
            match = re.search(r"opencode server listening on (http://127\.0\.0\.1:\d+)", line)
            if match:
                endpoints.append(match.group(1))
                ready.set()
        ready.set()
    threading.Thread(target=collect, daemon=True).start()
    try:
        if not ready.wait(30) or not endpoints:
            raise RuntimeError(f"Stock server failed: {''.join(logs)}")
        def api(method: str, path: str, body: dict[str, Any] | None = None):
            request = urllib.request.Request(
                endpoints[0] + path, data=json.dumps(body).encode() if body is not None else None,
                headers={"content-type": "application/json",
                         "Authorization": "Basic " + base64.b64encode(
                             f"{SOURCE_API_USERNAME}:{SOURCE_API_PASSWORD}".encode()).decode()},
                method=method)
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read())
        # The source SSE endpoint uses the same declared fixture credentials.
        # Expose its genuine listener address, never a manufactured event feed.
        api.url = endpoints[0]
        yield api
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()


def create_empty_stock_sessions(workspace: Path, scratch: Path, *, delegated: bool) -> list[dict[str, Any]]:
    """Create real IDs before either run; import.ts:134-169 preserves them."""
    with stock_server(workspace, scratch, {"permission": "allow", "lsp": False}) as api:
        root = {"info": api("POST", "/session", {"title": "Compaction lane"}), "messages": []}
        return [root, {"info": api("POST", "/session", {
            "parentID": root["info"]["id"], "title": "Delegated fixture"}), "messages": []}] if delegated else [root]


def native_session_evidence(api, session_id: str) -> dict[str, Any]:
    """Readonly genuine source API evidence; never used as model history."""
    children = api("GET", "/session/" + session_id + "/children")
    return {"session": api("GET", "/session/" + session_id),
            "messages": api("GET", "/session/" + session_id + "/message"),
            "children": [{"session": child,
                          "messages": api("GET", "/session/" + child["id"] + "/message")}
                         for child in children]}


class NativePruneFence:
    """Observe actual owned part-update events before the next real human input."""

    def __init__(self, api, url: str, workspace: Path, session_id: str,
                 call_ids: list[str], password: str):
        self.session_id = session_id
        self.call_ids = call_ids
        self.expected = {}
        self.observed = {}
        self.ready = threading.Event()
        self.complete = threading.Event()
        self.stop = threading.Event()
        self.failure = None
        messages = api("GET", f"/session/{session_id}/message")
        for message in messages:
            for part in message["parts"]:
                if part.get("type") == "tool" and part.get("callID") in call_ids:
                    if part["sessionID"] != session_id or part["callID"] in self.expected:
                        raise ValueError("Prune fence requires unique owned native tool parts")
                    if part["state"]["status"] != "completed" or part["state"]["time"].get("compacted") is not None:
                        raise ValueError("Prune fence must subscribe before native pruning")
                    self.expected[part["callID"]] = {
                        "id": part["id"], "messageID": message["info"]["id"],
                        "tool": part["tool"],
                        "output_sha256": hashlib.sha256(part["state"]["output"].encode()).hexdigest(),
                    }
        if set(self.expected) != set(call_ids):
            raise ValueError("Prune fence omitted a genuine native tool part")
        endpoint = urllib.parse.urlsplit(url)
        self.connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=180)
        self.headers = {
            "x-opencode-directory": str(workspace.resolve()),
            "Authorization": "Basic " + base64.b64encode(
                f"{SOURCE_API_USERNAME}:{password}".encode()).decode(),
        }
        self.thread = threading.Thread(target=self._observe, daemon=True)

    def _observe(self) -> None:
        try:
            self.connection.request("GET", "/event", headers=self.headers)
            with self.connection.getresponse() as response:
                if response.status != 200:
                    raise RuntimeError(f"Native event subscription failed: {response.status}")
                self.ready.set()
                for line in response:
                    if self.stop.is_set():
                        break
                    if not line.startswith(b"data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] != "message.part.updated":
                        continue
                    part = event["properties"]["part"]
                    call_id = part.get("callID")
                    if part.get("sessionID") != self.session_id or call_id not in self.expected:
                        continue
                    expected = self.expected[call_id]
                    if part["id"] != expected["id"] or part["messageID"] != expected["messageID"]:
                        raise ValueError("Prune event changed its native part identity")
                    if (part.get("type") != "tool" or part.get("tool") != expected["tool"]
                            or part["state"]["status"] != "completed"):
                        raise ValueError("Prune event changed its native completed tool")
                    compacted = part["state"]["time"].get("compacted")
                    if type(compacted) is not int:
                        continue
                    digest = hashlib.sha256(part["state"]["output"].encode()).hexdigest()
                    if digest != expected["output_sha256"]:
                        raise ValueError("Prune fence observed changed output, not native prune")
                    self.observed[call_id] = {**expected, "compacted": compacted}
                    if set(self.observed) == set(self.expected):
                        self.complete.set()
                        break
                if not self.complete.is_set() and not self.stop.is_set():
                    raise RuntimeError("Native event subscription ended before prune completed")
        except Exception as error:
            if not self.stop.is_set():
                self.failure = error
                self.complete.set()
        finally:
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        if not self.ready.wait(30):
            self.__exit__(None, None, None)
            raise TimeoutError("Native prune event subscription did not start")
        if self.failure is not None:
            self.__exit__(None, None, None)
            raise self.failure
        return self

    def wait(self) -> dict[str, Any]:
        if not self.complete.wait(180):
            raise TimeoutError("Native prune-complete events did not arrive")
        if self.failure is not None:
            raise self.failure
        return {"session_id": self.session_id, "event_type": "message.part.updated",
                "call_ids": self.call_ids, "native_parts": self.observed,
                "next_error_delivery": "Only after these genuine native prune events",
                "source": "packages/opencode/src/session/prompt.ts:716"}

    def __exit__(self, *_):
        self.stop.set()
        connection_socket = self.connection.sock
        if connection_socket is not None:
            try:
                connection_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.connection.close()
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("Native prune event observer did not stop")


def wait_stock_idle_checkpoint(api, session_id: str, base_url: str,
                               remaining_responses: int, answer: str) -> dict[str, Any]:
    """Fence scripted phases on genuine source idle and completed-answer evidence."""
    deadline = time.monotonic() + 180
    while True:
        with urllib.request.urlopen(base_url + "/lane/script-progress", timeout=10) as response:
            remaining = json.load(response)["remaining"]
        evidence = native_session_evidence(api, session_id)
        status = api("GET", "/session/status").get(session_id, {"type": "idle"})
        completed = any(
            message["info"]["role"] == "assistant"
            and message["info"].get("finish") == "stop"
            and message["info"].get("time", {}).get("completed")
            and not message["info"].get("summary")
            and not message["info"].get("error")
            and any(part.get("type") == "text" and part.get("text") == answer
                    for part in message["parts"])
            for message in evidence["messages"])
        checkpoint = {"native_evidence": evidence, "native_status": status,
                      "remaining_responses": remaining, "completed_answer": answer}
        if remaining == remaining_responses and status["type"] == "idle" and completed:
            return checkpoint
        if time.monotonic() >= deadline or remaining < remaining_responses:
            return {**checkpoint, "error": "Source idle/completed checkpoint did not match the scripted phase"}
        time.sleep(0.05)


def run_stock(harness: str, scenario: str, workspace: Path, scratch: Path, base_url: str,
              session_imports: list[dict[str, Any]] | None = None, mcp_relay: str | None = None) -> dict[str, Any]:
    settings = production_settings(harness)
    tools = {"*": False, **{name: True for name in settings["tools"]}}
    agent = {"tools": tools, **settings["params"]}
    config: dict[str, Any] = {"model": settings["model_id"], "provider": {"openai": {"options": {"baseURL": base_url, "apiKey": "lane-mock"}, "models": {settings["model"]: {"limit": {"context": TEST_WINDOW, "input": TEST_WINDOW, "output": settings["max_output_tokens"]}}}}}, "agent": {"build": agent}, "permission": "allow", "compaction": {"auto": True, "prune": True}}
    config["lsp"] = False
    if harness == "oh-my-opencode":
        config["plugin"] = [(plugin_root() / "dist/index.js").as_uri()]
        config.update(mcp_configuration(mcp_relay))
    env = stock_environment(config, scratch)
    cli = resolve_stock()
    session_id = None
    for index, fixture in enumerate(session_imports or ()):
        path = scratch / f"empty-session-{index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture))
        imported = subprocess.run([str(cli), "import", str(path)], cwd=workspace, env=env,
                                  capture_output=True, text=True, timeout=30)
        if imported.returncode or f"Imported session: {fixture['info']['id']}" not in imported.stdout:
            raise RuntimeError(f"Stock fixture import failed: {imported.stderr} {imported.stdout}")
        if not fixture["info"].get("parentID"):
            session_id = fixture["info"]["id"]
    if not session_id:
        raise ValueError("Stock requires its shared empty root session fixture")
    turns = []
    reset_mcp_relay(mcp_relay)
    with stock_server(workspace, scratch, config) as api:
        scripted_turns = scenario_turns(scenario, settings)
        idle_checkpoints = []
        prune_fences = []
        for index, turn in enumerate(scripted_turns):
            fence_context = (
                NativePruneFence(api, api.url, workspace, session_id,
                                 turn["native_prune_call_ids"], SOURCE_API_PASSWORD)
                if "native_prune_call_ids" in turn else nullcontext(None)
            )
            with fence_context as fence:
                try:
                    result = api("POST", "/session/" + session_id + "/message", {
                        "model": {"providerID": "openai", "modelID": settings["model"]},
                        "parts": [{"type": "text", "text": "\n" + turn["prompt"]}]})
                except TimeoutError:
                    failure = {"error": traceback.format_exc(), "failure_kind": "foreground_post_timeout",
                               "turns": turns, "session_id": session_id, "stock_config": config,
                               "idle_checkpoints": idle_checkpoints, "native_prune_fences": prune_fences}
                    # Read the same still-live source before its existing cleanup.
                    # Only native control-boundary ties can reject this oracle.
                    try:
                        failure["native_evidence"] = native_session_evidence(api, session_id)
                        failure["native_status"] = api("GET", "/session/status").get(session_id)
                    except Exception as observation_error:
                        failure["native_evidence_error"] = (
                            f"{type(observation_error).__name__}: {observation_error}")
                    return failure
                if fence is not None:
                    prune_fences.append(fence.wait())
            turns.append(result)
            error = result.get("info", {}).get("error")
            if error:
                # This scenario proves the source API error rather than
                # converting it into native overflow. Its plugin continues
                # asynchronously, and the normal completion barrier below
                # must still observe every scripted request and final answer.
                if not (scenario == "plugin_error" and error.get("name") == "APIError"
                        and error.get("data", {}).get("message") == "too many tokens: 74000 tokens > 64000 maximum"):
                    raise RuntimeError(f"Stock assistant failed: {error}")
            if "autonomous_responses" in turn:
                following = sum(len(next_turn["responses"]) + len(next_turn.get("autonomous_responses", []))
                                for next_turn in scripted_turns[index + 1:])
                checkpoint = wait_stock_idle_checkpoint(
                    api, session_id, base_url, following + len(turn["autonomous_responses"]),
                    turn["responses"][-1]["text"])
                idle_checkpoints.append(checkpoint)
                if "error" not in checkpoint:
                    with urllib.request.urlopen(base_url + "/lane/release-child", timeout=10) as response:
                        response.read()
                    checkpoint = wait_stock_idle_checkpoint(
                        api, session_id, base_url, following, turn["autonomous_responses"][-1]["text"])
                    idle_checkpoints.append(checkpoint)
                if "error" in checkpoint:
                    return {**checkpoint, "turns": turns, "session_id": session_id,
                            "stock_config": config, "idle_checkpoints": idle_checkpoints}
        # Plugin recovery forks summarize/prompt after the foreground POST
        # returns. Keep the real source process alive through the identical
        # scripted session, then observe its own root idle/completed boundary.
        deadline = time.monotonic() + 180
        while True:
            with urllib.request.urlopen(base_url + "/lane/script-progress", timeout=10) as response:
                remaining = json.load(response)["remaining"]
            evidence = native_session_evidence(api, session_id)
            assistants = [message["info"] for message in evidence["messages"]
                          if message["info"]["role"] == "assistant"]
            last = max(assistants, key=lambda info: info["time"]["created"]) if assistants else {}
            status = api("GET", "/session/status").get(session_id, {"type": "idle"})
            if remaining == 0 and status["type"] == "retry" and status.get("message") == "Mock provider script exhausted":
                return {"error": "Stock exhausted the identical scripted provider responses", "turns": turns, "session_id": session_id, "stock_config": config, "native_evidence": evidence, "native_status": status, "native_prune_fences": prune_fences}
            if remaining == 0 and status["type"] == "idle" and last.get("time", {}).get("completed"):
                if last.get("error") or last.get("summary"):
                    raise RuntimeError("Stock ended without a completed normal assistant answer")
                break
            if time.monotonic() >= deadline:
                return {"error": f"Stock scripted session did not finish: remaining={remaining}, status={status}, last={last}", "turns": turns, "session_id": session_id, "stock_config": config, "native_evidence": evidence, "native_status": status, "native_prune_fences": prune_fences}
            time.sleep(0.05)
    return {"turns": turns, "session_id": session_id, "stock_config": config,
            "native_evidence": evidence, "idle_checkpoints": idle_checkpoints,
            "native_prune_fences": prune_fences}


def materialize_episode_profile(settings: dict[str, Any], overrides: dict[str, Any], path: Path) -> dict[str, Any]:
    """Freeze the production-loaded profile with the episode's declared overrides."""
    from breadboard_engine.auth.enforcer import apply_dotted_overrides
    from breadboard_engine.compilation.v2_loader import load_agent_config
    config = apply_dotted_overrides(load_agent_config(settings["profile"]), overrides)
    payload = json.dumps(config, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
            "source_profile": settings["profile"], "source_profile_sha256": settings["profile_sha256"],
            "override_keys": list(overrides)}


async def run_bb_queued_session(settings: dict[str, Any], workspace: Path, base_url: str,
                                session_id: str, effective_profile: dict[str, Any],
                                scenario: str) -> dict[str, Any]:
    """Drive autonomous source inputs through the actual production session lifecycle."""
    from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionInputRequest
    from breadboard_engine.api.cli_bridge.service import SessionService
    service = SessionService(state_root=Path(tempfile.mkdtemp(prefix="bb-opencode-queue-bridge-")))
    checkpoints = []
    prune_fences = []
    failure = None
    record = None
    evidence = None
    async def completed_turns(expected: int) -> str | None:
        deadline = time.monotonic() + 180
        while True:
            turns = list(record.turns_by_id.values())
            if len(turns) > expected:
                return f"Production session accepted {len(turns)} turns, expected {expected}"
            if any(turn.terminal_outcome in {"failed", "cancelled"} for turn in turns):
                return "Production queued turn did not complete successfully"
            if len(turns) == expected and all(turn.terminal_outcome == "completed" for turn in turns):
                return None
            if time.monotonic() >= deadline:
                return f"Production session did not complete {expected} queued turns"
            await asyncio.sleep(0.05)
    def queued_result() -> dict[str, Any]:
        product = record.product_session
        return {"session_id": session_id, "driver": "SessionService/SessionRunner production input queue",
                "effective_profile": effective_profile,
                "turns": [{"input_id": turn.input_id, "turn_id": turn.turn_id,
                           "client_message_id": turn.client_message_id,
                           "content": turn.content, "state": turn.state,
                           "terminal_outcome": turn.terminal_outcome}
                          for turn in record.turns_by_id.values()],
                "events": [event.asdict() for event in record.event_log],
                "idle_checkpoints": checkpoints,
                "native_prune_fences": prune_fences,
                "retained_effective_messages": json.loads(product.effective_context)
                    if product is not None and product.effective_context is not None else None,
                **({"native_evidence": evidence} if evidence is not None else {}),
                **({"error": failure} if failure is not None else {})}
    try:
        await service.create_session(
            SessionCreateRequest(task="", config_path=effective_profile["path"],
                                 workspace=str(workspace), max_steps=40, stream=True),
            session_id=session_id)
        record = await service.registry.get(session_id)
        turns = scenario_turns(scenario, settings)
        await service.send_input(session_id, SessionInputRequest(
            content="\n" + turns[0]["prompt"],
            client_message_id="delegate-idle-initial" if scenario == "delegate_idle" else "overflow-initial"))
        failure = await completed_turns(1)
        if failure is None:
            native_sessions = list(record.runner._agent.agent._opencode_tool_sessions.values())
            if len(native_sessions) != 1 or native_sessions[0].session_id != session_id:
                failure = "Production queued runner lost its genuine source session identity"
            else:
                native = native_sessions[0]
                if scenario == "overflow":
                    for index, turn in enumerate(turns[1:], 1):
                        fence_context = (
                            NativePruneFence(native.api, native.url, workspace, session_id,
                                             turn["native_prune_call_ids"], native.password)
                            if "native_prune_call_ids" in turn else nullcontext(None)
                        )
                        with fence_context as fence:
                            await service.send_input(session_id, SessionInputRequest(
                                content="\n" + turn["prompt"],
                                client_message_id=f"overflow-human-{index}"))
                            if index < len(turns) - 1:
                                failure = await completed_turns(index + 1)
                            else:
                                # The final human owns the overflow. Any genuine
                                # native recovery users enter the production queue.
                                checkpoint = await asyncio.to_thread(
                                    wait_stock_idle_checkpoint, native.api, session_id, base_url,
                                    0, turn["responses"][-1]["text"])
                                checkpoints.append(checkpoint)
                                failure = checkpoint.get("error")
                            if failure is None and fence is not None:
                                prune_fences.append(await asyncio.to_thread(fence.wait))
                        if failure is not None:
                            break
                    deadline = time.monotonic() + 180
                    while failure is None and any(
                        turn.terminal_outcome is None for turn in record.turns_by_id.values()
                    ):
                        if time.monotonic() >= deadline:
                            failure = "Production recovery queue did not settle at native completion"
                            break
                        await asyncio.sleep(0.05)
                    if failure is None and any(
                        turn.terminal_outcome != "completed" for turn in record.turns_by_id.values()
                    ):
                        failure = "Production recovery queued turn did not complete successfully"
                    evidence = native_session_evidence(native.api, session_id)
                    return queued_result()
                following = len(turns[1]["responses"])
                checkpoint = await asyncio.to_thread(
                    wait_stock_idle_checkpoint, native.api, session_id, base_url,
                    following + len(turns[0]["autonomous_responses"]), turns[0]["responses"][-1]["text"])
                checkpoints.append(checkpoint)
                failure = checkpoint.get("error")
                if failure is None:
                    with urllib.request.urlopen(base_url + "/lane/release-child", timeout=10) as response:
                        response.read()
                    # No lane-origin input invokes this turn. The source notification
                    # enters SessionService's trusted production stock-origin path.
                    checkpoint = await asyncio.to_thread(
                        wait_stock_idle_checkpoint, native.api, session_id, base_url,
                        following, turns[0]["autonomous_responses"][-1]["text"])
                    checkpoints.append(checkpoint)
                    failure = checkpoint.get("error") or await completed_turns(2)
                if failure is None:
                    await service.send_input(session_id, SessionInputRequest(
                        content="\n" + turns[1]["prompt"], client_message_id="delegate-idle-followup"))
                    failure = await completed_turns(3)
                if failure is None:
                    checkpoint = await asyncio.to_thread(
                        wait_stock_idle_checkpoint, native.api, session_id, base_url,
                        0, turns[1]["responses"][-1]["text"])
                    checkpoints.append(checkpoint)
                    failure = checkpoint.get("error")
                evidence = native_session_evidence(native.api, session_id)
        return queued_result()
    finally:
        active_error = sys.exc_info()[1]
        try:
            observation = {
                "session_id": session_id,
                "events": [event.asdict() for event in record.event_log] if record is not None else [],
                "turns": [{"input_id": turn.input_id, "turn_id": turn.turn_id,
                           "client_message_id": turn.client_message_id,
                           "content": turn.content, "state": turn.state,
                           "terminal_outcome": turn.terminal_outcome}
                          for turn in record.turns_by_id.values()] if record is not None else [],
                "idle_checkpoints": checkpoints,
                "native_prune_fences": prune_fences,
            }
            (Path(effective_profile["path"]).parent / "bb_queued_observation.json").write_text(
                json.dumps(observation, indent=2) + "\n")
        except Exception as observation_error:
            if active_error is None:
                raise
            active_error.add_note(
                f"Queued observation failed: {type(observation_error).__name__}: {observation_error}")
        finally:
            await service.shutdown_runtime_owners()


def run_bb(harness: str, scenario: str, workspace: Path, base_url: str, stock_session_id: str,
           episode_profile_path: Path, session_imports: list[dict[str, Any]] | None = None,
           mcp_relay: str | None = None) -> dict[str, Any]:
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider.routing import provider_router
    settings = production_settings(harness)
    # Recording-provider transport binding only. Compaction/provider runtimes
    # and their serializers are not replaced.
    route = provider_router.providers["openai"]
    route.base_url = base_url
    route.auth_owner = "none"
    route.credential_required = False
    if not stock_session_id:
        raise ValueError("The stock run must supply its real sessionID before BB starts")
    reset_mcp_relay(mcp_relay)
    overrides = {
        "compaction.contextWindow": TEST_WINDOW,
        "providers.models[0].max_input_tokens": TEST_WINDOW,
        "enhanced_tools.lsp_integration.enabled": False,
        "tools.stock_config": {**mcp_configuration(mcp_relay), "permission": "allow", "lsp": False,
                               "provider": {"openai": {"models": {settings["model"]: {
                                   "limit": {"context": TEST_WINDOW, "input": TEST_WINDOW,
                                             "output": settings["max_output_tokens"]}}}}}},
        "tools.stock_environment": settings["overrides"]["stock_environment"],
        **({"tools.stock_session_imports": session_imports} if session_imports is not None else {}),
    }
    if scenario == "delegate_idle" or (
        scenario == "overflow" and harness == "oh-my-opencode"
    ):
        return asyncio.run(run_bb_queued_session(
            settings, workspace, base_url, stock_session_id,
            materialize_episode_profile(settings, overrides, episode_profile_path), scenario))
    agent = AgenticCoder(settings["profile"], str(workspace), overrides=overrides, force_local_mode=True)
    retained: list[dict[str, Any]] = []
    facts: list[str] = []
    events: list[dict[str, Any]] = []
    turns = []
    failure = None
    try:
        for index, turn in enumerate(scenario_turns(scenario, settings)):
            def receive(kind: str, payload: dict[str, Any], turn: int | None = None) -> None:
                events.append({"type": kind, "payload": payload, "turn": turn})
            # Stock emitted this ID before this engine session starts. Feed the
            # real session seam, never rewrite prompt_cache_key after recording.
            context = {"session_id": stock_session_id, "input_id": f"input-{index}", "turn_id": f"turn-{index}", "retained_effective_messages": retained, "retained_raw_fact_ids": facts, "_product_compaction_owner": True}
            result = agent.run_task("\n" + turn["prompt"], max_iterations=40, stream=True, event_emitter=receive, context=context)
            turns.append(result)
            if result.get("completion_reason") == "run_loop_exception":
                failure = f"Production turn {index} failed: {result.get('completion_summary')}"
                break
            snapshots = [event["payload"] for event in events if event["type"] == "conversation.compaction.end"]
            if snapshots:
                snapshot = snapshots[-1]
                retained = json.loads(base64.b64decode(snapshot["effective_context"]))
                facts = snapshot["raw_fact_ids"]
            else:
                failure = "Production engine did not emit the retained product context"
                break
        native_sessions = list(agent.agent._opencode_tool_sessions.values())
        if len(native_sessions) != 1 or native_sessions[0].session_id != stock_session_id:
            raise RuntimeError("The production runner lost its genuine source session identity")
        evidence = native_session_evidence(native_sessions[0].api, stock_session_id)
        return {"turns": turns, "events": events, "retained_effective_messages": retained,
                "session_id": stock_session_id, "native_evidence": evidence,
                **({"error": failure} if failure is not None else {})}
    finally:
        agent.close()


def canonical(body: Any) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

class CreatedTimeTieOrder:
    """Guard stock message-v2.ts:740's ORDER BY time_created DESC collision.

    Native records establish the tie, message identities and BB creation order.
    Only tied slots move. The entire resulting request must otherwise match.
    Tool assistants move as a contiguous calls-then-results slice, validated
    against genuine native call IDs, names, input arguments and output.
    """

    def __init__(self, stock: dict[str, Any], bb: dict[str, Any]):
        self.groups = {}
        for message in stock.get("messages", []):
            created = message["info"].get("time", {}).get("created")
            if type(created) is int:
                self.groups.setdefault(created, []).append(message)
        self.bb_messages = bb.get("messages", [])
        self.stock_session_id = stock.get("session", {}).get("id")
        self.bb_session_id = bb.get("session", {}).get("id")

    @staticmethod
    def wire(message):
        role = message["info"].get("role")
        if role not in {"user", "assistant"}:
            return None
        parts = message.get("parts", [])
        if any(part.get("type") not in {"text", "step-start", "step-finish"} for part in parts):
            return None
        texts = [part["text"] for part in parts
                 if part.get("type") == "text" and not part.get("ignored")]
        if not texts or any(not isinstance(text, str) for text in texts):
            return None
        return {"role": role, "content": [
            {"type": "input_text" if role == "user" else "output_text", "text": text}
            for text in texts]}

    @classmethod
    def wire_slice(cls, message, inputs):
        text = cls.wire(message)
        if text is not None:
            return [text]
        if message["info"].get("role") != "assistant":
            return None
        parts = message.get("parts", [])
        if any(part.get("type") not in {"tool", "step-start", "step-finish"} for part in parts):
            return None
        tools = [part for part in parts if part.get("type") == "tool"]
        if not tools or len({part.get("callID") for part in tools}) != len(tools):
            return None
        calls, results = [], []
        for part in tools:
            state = part.get("state", {})
            if (state.get("status") != "completed" or not isinstance(state.get("input"), dict)
                    or not isinstance(state.get("output"), str)
                    or not isinstance(part.get("callID"), str) or not isinstance(part.get("tool"), str)):
                return None
            matches = [item for item in inputs if item.get("type") == "function_call"
                       and item.get("call_id") == part["callID"]]
            outputs = [item for item in inputs if item.get("type") == "function_call_output"
                       and item.get("call_id") == part["callID"]]
            if len(matches) != 1 or len(outputs) != 1:
                return None
            call, output = matches[0], outputs[0]
            if (set(call) != {"type", "call_id", "name", "arguments"}
                    or set(output) != {"type", "call_id", "output"}
                    or call["name"] != part["tool"] or not isinstance(call["arguments"], str)
                    or output["output"] != state["output"]):
                return None
            try:
                arguments = json.loads(call["arguments"])
            except json.JSONDecodeError:
                return None
            if canonical(arguments) != canonical(state["input"]):
                return None
            calls.append(call)
            results.append(output)
        return [*calls, *results]

    @staticmethod
    def positions(inputs, wire):
        return [index for index in range(len(inputs) - len(wire) + 1)
                if canonical(inputs[index:index + len(wire)]) == canonical(wire)]

    def body(self, stock: dict[str, Any], bb: dict[str, Any], *,
             raw_stock: dict[str, Any] | None = None, raw_bb: dict[str, Any] | None = None):
        if not isinstance(stock.get("input"), list) or not isinstance(bb.get("input"), list):
            return stock, []
        raw_stock = stock if raw_stock is None else raw_stock
        raw_bb = bb if raw_bb is None else raw_bb
        if (not isinstance(self.stock_session_id, str) or not self.stock_session_id
                or not isinstance(self.bb_session_id, str) or not self.bb_session_id
                or raw_stock.get("prompt_cache_key") != self.stock_session_id
                or raw_bb.get("prompt_cache_key") != self.bb_session_id):
            return stock, []
        stock_inputs = raw_stock["input"]
        bb_inputs = raw_bb["input"]
        projected = {**stock, "input": list(stock["input"])}
        used = []
        for created, group in self.groups.items():
            if len(group) < 2:
                continue
            raw_wires = [self.wire_slice(message, stock_inputs) for message in group]
            if any(wire is None for wire in raw_wires):
                continue
            positions = [self.positions(stock_inputs, wire) for wire in raw_wires]
            if any(len(matches) != 1 for matches in positions):
                continue
            starts = [matches[0] for matches in positions]
            slots = sorted(slot for start, wire in zip(starts, raw_wires)
                           for slot in range(start, start + len(wire)))
            if slots != list(range(slots[0], slots[-1] + 1)):
                continue
            wires = [stock["input"][start:start + len(wire)]
                     for start, wire in zip(starts, raw_wires)]
            candidates = []
            for wire in wires:
                matches = []
                for message in self.bb_messages:
                    raw_wire = self.wire_slice(message, bb_inputs)
                    if raw_wire is None:
                        continue
                    positions = self.positions(bb_inputs, raw_wire)
                    if len(positions) == 1:
                        start = positions[0]
                        if canonical(bb["input"][start:start + len(raw_wire)]) == canonical(wire):
                            matches.append(message)
                candidates.append(matches)
            # A replay user can share text with its original. The uniquely
            # matched native assistant's parentID identifies its actual user.
            parents = {matches[0]["info"].get("parentID")
                       for matches in candidates if len(matches) == 1}
            candidates = [matches if len(matches) == 1 else [
                message for message in matches if message["info"]["id"] in parents]
                for matches in candidates]
            if any(len(matches) != 1 for matches in candidates):
                continue
            mapped = [matches[0] for matches in candidates]
            times = [message["info"].get("time", {}).get("created") for message in mapped]
            if any(type(time) is not int for time in times) or len(set(times)) != len(times):
                continue
            ordered = sorted(zip(times, wires), key=lambda pair: pair[0])
            replacement = [item for _, wire in ordered for item in wire]
            start, end = slots[0], slots[-1] + 1
            if canonical(bb["input"][start:end]) != canonical(replacement):
                continue
            if canonical(stock["input"][start:end]) == canonical(replacement):
                continue
            projected["input"][start:end] = replacement
            used.append({"stock_created": created, "stock_message_ids": [
                message["info"]["id"] for message in group],
                "bb_message_ids": [message["info"]["id"] for message in mapped],
                "bb_created": times, "request_slots": slots})
        if used and canonical(projected) == canonical(bb):
            return projected, used
        return stock, []



class RuntimeGeneratedIDs:
    """Guard only source runtime IDs, proven by each side's genuine native API.

    Sources: background-task.ts:80-91; manager.ts:156; id/id.ts:49-74;
    compaction-context-injector/hook.ts:63-67; task-history.ts:61-73.
    Completed-list notification: manager.ts:1255-1287; internal-initiator-marker.ts:1-10.
    """
    SESSION = r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}"
    BACKGROUND = r"bg_[0-9a-f]{8}"
    OUTPUT = re.compile(
        rf"Background task launched\.\n\nBackground Task ID: (?P<bg>{BACKGROUND})"
        rf"\nDescription: [^\n]+\nAgent: [^\n]+\nStatus: (?:pending|running)\n\n"
        rf'System notifies on completion\. Use `background_output` with task_id="(?P<bg_ref>{BACKGROUND})" to check\.'
        rf"\n\n<task_metadata>\nsession_id: (?P<session>{SESSION})"
        rf"\ntask_id: (?P<session_ref>{SESSION})\nbackground_task_id: (?P<bg_meta>{BACKGROUND})\n</task_metadata>"
        rf'(?:\n\nto continue: task\(session_id="(?P<session_hint>{SESSION})", prompt="\.\.\."\))?'
    )
    HISTORY = re.compile(
        rf"(- \*\*[^*\n]+\*\*(?:\[[^\]\n]+\])?\((?:pending|running|completed|error|cancelled)\)"
        rf": [^\n]+ \| session: `)({SESSION})(`)"
    )
    COMPLETED_NOTIFICATION = re.compile(
        r"<system-reminder>\n\[ALL BACKGROUND TASKS COMPLETE\]\n\n\*\*Completed:\*\*\n"
        rf"(?P<tasks>(?:- `{BACKGROUND}`: [^\n]+\n)+)"
        r'\nUse `background_output\(task_id="<id>"\)` to retrieve each result\.\n'
        r"</system-reminder>\n<!-- OMO_INTERNAL_INITIATOR -->"
    )
    COMPLETED_ROW = re.compile(rf"- `(?P<bg>{BACKGROUND})`: (?P<description>[^\n]+)\n")
    TOKENS = re.compile(rf"\b(?:{SESSION}|{BACKGROUND})\b")

    def __init__(self, stock: dict[str, Any], bb: dict[str, Any], imported_ids: list[str]):
        self.root = stock["session"]["id"]
        if bb["session"]["id"] != self.root or self.root not in imported_ids:
            raise ValueError("The deterministic imported root session IDs differ")
        self.imported = set(imported_ids)
        tasks = {side: self._tasks(evidence) for side, evidence in (("stock", stock), ("bb", bb))}
        if not tasks["stock"] or tasks["stock"].keys() != tasks["bb"].keys():
            raise ValueError("Native APIs do not report the same background call identities")
        self.tasks = tasks
        self.maps = {"stock": {}, "bb": {}}
        for index, call_id in enumerate(tasks["stock"]):
            for kind, slot in (("bg", 0), ("session", 1)):
                token = f"<runtime-{kind}-{index}>"
                for side in self.maps:
                    value = tasks[side][call_id][slot]
                    if value in self.maps[side] or value in self.imported:
                        raise ValueError("Runtime identity mapping is not a bijection")
                    self.maps[side][value] = token
        self.counts = {"stock": {}, "bb": {}}
        self.completed_notifications = {
            side: self._completed_notifications(evidence, tasks[side])
            for side, evidence in (("stock", stock), ("bb", bb))}

    def _tasks(self, evidence):
        children = {child["session"]["id"]: child["session"] for child in evidence["children"]}
        tasks = {}
        for message in evidence["messages"]:
            for part in message["parts"]:
                state = part.get("state", {})
                if part.get("type") != "tool" or part.get("tool") != "task" or state.get("input", {}).get("run_in_background") is not True:
                    continue
                matched = self.OUTPUT.fullmatch(state.get("output", ""))
                if matched is None:
                    raise ValueError("Genuine background tool output does not match its source format")
                bg, child = matched["bg"], matched["session"]
                if matched["bg_ref"] != bg or matched["bg_meta"] != bg or matched["session_ref"] != child:
                    raise ValueError("Genuine background tool output IDs are inconsistent")
                if matched["session_hint"] is not None and matched["session_hint"] != child:
                    raise ValueError("Genuine background continuation hint ID is inconsistent")
                info = children.get(child)
                if info is None or info.get("parentID") != self.root or state.get("metadata", {}).get("sessionId") != child:
                    raise ValueError("Runtime child ID has no matching genuine native API provenance")
                call_id = part["callID"]
                if call_id in tasks:
                    raise ValueError("Duplicate native background call identity")
                tasks[call_id] = (bg, child, state.get("time", {}).get("compacted") is not None)
        return tasks

    def _completed_notifications(self, evidence, tasks):
        children = {child["session"]["id"]: child for child in evidence["children"]}
        calls = {part["callID"]: part for message in evidence["messages"]
                 for part in message["parts"]
                 if part.get("type") == "tool" and part.get("tool") == "task"}
        by_background = {value[0]: (call_id, value[1]) for call_id, value in tasks.items()}
        notifications = {}
        for message in evidence["messages"]:
            if message["info"]["role"] != "user":
                continue
            for part in message["parts"]:
                text = part.get("text", "")
                matched = self.COMPLETED_NOTIFICATION.fullmatch(text) if part.get("type") == "text" else None
                if matched is None:
                    continue
                if (message["info"].get("sessionID") != self.root
                        or part.get("sessionID") != self.root
                        or part.get("messageID") != message["info"]["id"]):
                    raise ValueError("Completed notification has no matching native user provenance")
                proof = []
                for row in self.COMPLETED_ROW.finditer(matched["tasks"]):
                    task = by_background.get(row["bg"])
                    if task is None:
                        raise ValueError("Completed notification has an unproven background ID")
                    call_id, child_id = task
                    if calls[call_id]["state"]["input"].get("description") != row["description"]:
                        raise ValueError("Completed notification description does not match its native task call")
                    completions = [
                        child_message["info"]["id"] for child_message in children[child_id]["messages"]
                        if child_message["info"].get("sessionID") == child_id
                        and child_message["info"]["role"] == "assistant"
                        and child_message["info"].get("finish") == "stop"
                        and child_message["info"].get("time", {}).get("completed")
                        and not child_message["info"].get("error")
                        and not child_message["info"].get("summary")]
                    if not completions:
                        raise ValueError("Completed notification has no genuine child completion evidence")
                    proof.append({"background_id": row["bg"], "call_id": call_id,
                                  "child_id": child_id, "child_completion_ids": completions})
                if len({item["background_id"] for item in proof}) != len(proof):
                    raise ValueError("Completed notification repeats a native background ID")
                notifications[text] = {"native_user_id": message["info"]["id"], "tasks": proof}
        return notifications

    def _token(self, side, value, position):
        token = self.maps[side].get(value)
        if token is None:
            raise ValueError(f"Unmapped runtime ID at {position}: {value}")
        counts = self.counts[side]
        counts[position] = counts.get(position, 0) + 1
        return token

    def body(self, body: dict[str, Any], side: str) -> dict[str, Any]:
        # Comparison copy only: the retained raw request is never altered.
        if re.search(r"<runtime-(?:bg|session)-[0-9]+>", json.dumps(body)):
            raise ValueError("Raw request contains a reserved comparison marker")
        projected = json.loads(json.dumps(body))
        # Stock injects this notification only into the parent session (manager.ts:1359-1367);
        # each side's root is its genuine native root, checked equal in __init__.
        root_owned = body.get("prompt_cache_key") == self.root
        cache = projected.get("prompt_cache_key")
        if cache is not None and cache not in self.imported:
            if re.fullmatch(self.SESSION, cache) is None:
                raise ValueError("Delegated request has an invalid native session ID")
            projected["prompt_cache_key"] = self._token(side, cache, "delegated.prompt_cache_key")
        summary = is_summary({"json": projected})
        for index, item in enumerate(projected.get("input", [])):
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
                matched = self.OUTPUT.fullmatch(item["output"])
                proven = self.tasks[side].get(item.get("call_id"))
                if proven is not None and matched is None:
                    if item["output"] == "[Old tool result content cleared]" and proven[2]:
                        continue
                    raise ValueError("Native background call has malformed wire output")
                if matched is not None:
                    if proven is None or proven[:2] != (matched["bg"], matched["session"]):
                        raise ValueError("Background wire call has no matching native task provenance")
                    if matched["bg_ref"] != matched["bg"] or matched["bg_meta"] != matched["bg"] or matched["session_ref"] != matched["session"]:
                        raise ValueError("Background wire IDs do not match their source provenance")
                    if matched["session_hint"] is not None and matched["session_hint"] != matched["session"]:
                        raise ValueError("Background continuation hint ID does not match source provenance")
                    text = item["output"]
                    names = [name for name in ("bg", "bg_ref", "session", "session_ref", "bg_meta", "session_hint")
                             if matched[name] is not None]
                    for name in sorted(names, key=lambda name: matched.start(name), reverse=True):
                        text = text[:matched.start(name)] + self._token(side, matched[name], "background_output." + name) + text[matched.end(name):]
                    item["output"] = text
            if root_owned and item.get("role") == "user":
                content = item.get("content")
                blocks = [{"type": "input_text", "text": content}] if isinstance(content, str) else content
                if isinstance(blocks, list) and len(blocks) == 1 and blocks[0].get("type") == "input_text":
                    text = blocks[0].get("text", "")
                    matched = self.COMPLETED_NOTIFICATION.fullmatch(text)
                    if matched is not None:
                        if text not in self.completed_notifications[side]:
                            raise ValueError("Completed notification wire text has no native user provenance")
                        rows = list(self.COMPLETED_ROW.finditer(matched["tasks"]))
                        offset = matched.start("tasks")
                        for row in reversed(rows):
                            start, end = row.span("bg")
                            text = (text[:offset + start]
                                    + self._token(side, row["bg"], "notification.completed_list")
                                    + text[offset + end:])
                        blocks[0]["text"] = text
                        if isinstance(content, str):
                            item["content"] = text
            if summary and index == len(projected["input"]) - 1 and item.get("role") == "user":
                content = item.get("content")
                blocks = [{"text": content}] if isinstance(content, str) else content
                for block in blocks:
                    text = block.get("text", "")
                    marker = "\n### Active/Recent Delegated Sessions\n"
                    if marker not in text:
                        continue
                    head, history = text.split(marker, 1)
                    lines = []
                    for line in history.splitlines(keepends=True):
                        if not line.strip():
                            lines.append(line)
                            continue
                        matched = self.HISTORY.fullmatch(line.removesuffix("\n"))
                        if matched is None:
                            raise ValueError("Delegated history does not match its exact source format")
                        lines.append(matched[1] + self._token(side, matched[2], "summary.active_recent") + matched[3]
                                     + ("\n" if line.endswith("\n") else ""))
                    block["text"] = head + marker + "".join(lines)
                if isinstance(content, str):
                    item["content"] = blocks[0]["text"]
        for token in self.TOKENS.findall(json.dumps(projected, ensure_ascii=False)):
            if token not in self.imported:
                raise ValueError(f"Runtime ID outside an approved position or without native provenance: {token}")
        return projected

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


def stock_control_flow_ties(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Select the oracle from native records, never request bytes or counts.

    A tie involving a native summary or compaction part can alter boundary
    reconstruction/control flow (message-v2.ts:740). Ordinary text replay
    ties use only the separately guarded request-order deviation.
    """
    ties = []
    sessions = [(evidence.get("session", {}).get("id"), evidence.get("messages", []))]
    sessions.extend((child["session"]["id"], child.get("messages", []))
                    for child in evidence.get("children", []))
    for session_id, messages in sessions:
        groups = {}
        for message in messages:
            created = message["info"].get("time", {}).get("created")
            if type(created) is int:
                groups.setdefault(created, []).append(message)
        for created, group in groups.items():
            if len(group) > 1 and any(
                message["info"].get("role") == "assistant" and message["info"].get("summary") is True
                or any(part.get("type") == "compaction" for part in message.get("parts", []))
                for message in group
            ):
                ties.append({"session_id": session_id, "time_created": created,
                             "message_ids": [message["info"]["id"] for message in group]})
    return ties


def auxiliary_script(scenario: str) -> list[dict[str, Any]]:
    if scenario == "delegate_prune":
        return [{"type": "delegated_pending"}]
    if scenario == "delegate_idle":
        return [{"type": "delegated_complete", "response": responses_sse(
            text_response("Delegated fixture inspection complete."), -1)}]
    return []


def archive_bb_session(workspace: Path, out: Path, session_id: str) -> str | None:
    """Retain a closed, lane-owned product/native store before a fresh attempt."""
    from breadboard.product.runtime.artifacts import is_portable_basename
    from breadboard.product.runtime.session_store import session_directory
    if not is_portable_basename(session_id):
        raise ValueError("BB oracle session ID is not a portable store key")
    source = session_directory(workspace) / session_id
    if not source.exists():
        return None
    if source.is_symlink():
        raise ValueError("BB oracle session store cannot be a symlink")
    destination = out / "bb_session_store"
    if destination.exists():
        raise FileExistsError(f"BB oracle archive already exists: {destination}")
    shutil.move(str(source), str(destination))
    return str(destination)


def capture_side(side, harness, scenario, workspace, out, provider, script, sessions,
                 relay, stock_session_id=None):
    out.mkdir(parents=True, exist_ok=True)
    (out / "source_seed_sessions.json").write_text(json.dumps(sessions, indent=2) + "\n")
    provider.engine.set_script(script)
    provider.engine.recorded_requests.clear()
    if scenario in {"delegate_prune", "delegate_idle"}:
        provider.engine.set_child_script(sessions[0]["info"]["id"], auxiliary_script(scenario))
    else:
        provider.engine.delegated_root = None
    result_path = out / f"{side}_result.json"
    if result_path.exists():
        raise FileExistsError(f"Use a fresh lane output directory: {result_path}")
    command = [sys.executable, "-m", "scripts.compaction_lanes.opencode_lane",
               "--child", side, "--harness", harness, "--scenario", scenario,
               "--out", str(out), "--workspace", str(workspace),
               "--base-url", provider.base_url + "/v1"]
    if relay is not None:
        command += ["--mcp-relay", relay.url]
    if side == "bb":
        command += ["--stock-session-id", str(stock_session_id or "")]
    process = subprocess.run(command, cwd=ROOT, env={**os.environ, "BB_WORKSPACE_ROOT": str(ROOT),
                             "PYTHONPATH": str(ROOT), "RAY_SCE_LOCAL_MODE": "1", "RAY_SCE_SKIP_LSP": "1"},
                             input="", capture_output=True, text=True, timeout=360)
    if scenario in {"delegate_prune", "delegate_idle"}:
        provider.engine.delegated_release.set()
        if provider.engine.delegated_arrived.is_set() and not provider.engine.delegated_finished.wait(10):
            raise RuntimeError("The lane failed to release its real background request")
    (out / f"{side}.stdout").write_text(process.stdout)
    (out / f"{side}.stderr").write_text(process.stderr)
    result = json.loads(result_path.read_bytes()) if result_path.is_file() else {
        "error": process.stderr, "returncode": process.returncode}
    observation_path = out / "bb_queued_observation.json"
    if side == "bb" and observation_path.is_file():
        result["queued_observation_artifact"] = str(observation_path)
    result["unconsumed_responses"] = len(provider.engine.script)
    result["unconsumed_auxiliary_responses"] = len(provider.engine.child_script or [])
    rows = list(provider.engine.recorded_requests)
    (out / f"{side}_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    if side == "bb":
        result["archived_session_store"] = archive_bb_session(workspace, out, stock_session_id)
        result_path.write_text(json.dumps(result, indent=2) + "\n")
    return result, rows


def run_scenario(harness: str, scenario: str, out: Path) -> dict[str, Any]:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # A workspace outside the engine repo has no inherited project/git files.
    # Both real processes receive this identical source path and git state.
    workspace = Path(tempfile.mkdtemp(prefix="bb-opencode-lane-"))
    settings = production_settings(harness)
    seed_path = out / "source_seed_sessions.json"
    if scenario in PLUGIN_SCENARIOS and harness != "oh-my-opencode":
        raise ValueError("The plugin contributor and recovery scenarios require oh-my-opencode")
    sessions = create_empty_stock_sessions(workspace, out / "fixture_runtime", delegated=False)
    seed_path.write_text(json.dumps(sessions, indent=2) + "\n")
    descriptors = [response for turn in scenario_turns(scenario, settings)
                   for phase in ("responses", "autonomous_responses")
                   for response in turn.get(phase, [])]
    script = [responses_sse(response, i) for i, response in enumerate(descriptors)]
    records: dict[str, list[dict[str, Any]]] = {}
    sides: dict[str, Any] = {}
    # Each side gets a fresh stock/engine session and the identical scripted
    # provider. Same literal workspace path, no path-based comparator rewrites.
    with (MCPRelay() if harness == "oh-my-opencode" else nullcontext(None)) as relay, ResponsesProvider(script=script) as provider:
        attempts = []
        selected = None
        bb_runs = 0
        bb_attempts = []
        bb_selected = None
        for attempt in range(1, 6):
            attempt_out = out / "stock_attempts" / f"attempt-{attempt}"
            result, rows = capture_side("stock", harness, scenario, workspace, attempt_out,
                                        provider, script, sessions, relay)
            if not result.get("native_evidence", {}).get("messages"):
                result.setdefault("error", "Stock oracle omitted genuine native message evidence")
            ties = stock_control_flow_ties(result.get("native_evidence", {}))
            attempts.append({"attempt": attempt, "control_flow_ties": ties,
                             "requests": len(rows),
                             "compactions": sum(message["info"].get("summary") is True
                                                and message["info"].get("finish") == "stop"
                                                and not message["info"].get("error")
                                                for message in result.get("native_evidence", {}).get("messages", [])),
                             "error": result.get("error"),
                             "artifacts": {"result": str(attempt_out / "stock_result.json"),
                                           "requests": str(attempt_out / "stock_requests.jsonl")}})
            if not result.get("native_evidence", {}).get("messages"):
                break
            if ties:
                continue
            selected = attempt
            sides["stock"], records["stock"] = result, rows
            break
        if selected is None:
            sides["stock"], records["stock"] = result, rows
            if len(attempts) == 5 and all(item["control_flow_ties"] for item in attempts):
                sides["stock"]["error"] = "All five stock oracle attempts have native control-flow ties"
        (out / "stock_result.json").write_text(json.dumps(sides["stock"], indent=2) + "\n")
        (out / "stock_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records["stock"]))
        if selected is not None and "error" not in sides["stock"]:
            for attempt in range(1, 6):
                attempt_out = out / "bb_attempts" / f"attempt-{attempt}"
                bb_runs += 1
                result, rows = capture_side(
                    "bb", harness, scenario, workspace, attempt_out, provider, script, sessions,
                    relay, sides["stock"]["session_id"])
                if not result.get("native_evidence", {}).get("messages"):
                    result.setdefault("error", "BB sidecar oracle omitted genuine native message evidence")
                ties = stock_control_flow_ties(result.get("native_evidence", {}))
                bb_attempts.append({
                    "attempt": attempt, "control_flow_ties": ties,
                    "requests": len(rows),
                    "compactions": sum(message["info"].get("summary") is True
                                       and message["info"].get("finish") == "stop"
                                       and not message["info"].get("error")
                                       for message in result.get("native_evidence", {}).get("messages", [])),
                    "error": result.get("error"),
                    "artifacts": {"result": str(attempt_out / "bb_result.json"),
                                  "requests": str(attempt_out / "bb_requests.jsonl"),
                                  "session_store": result.get("archived_session_store")},
                })
                sides["bb"], records["bb"] = result, rows
                if not result.get("native_evidence", {}).get("messages"):
                    break
                if ties:
                    continue
                bb_selected = attempt
                break
            if len(bb_attempts) == 5 and all(item["control_flow_ties"] for item in bb_attempts):
                sides["bb"]["error"] = "All five BB sidecar oracle attempts have native control-flow ties"
            observation = sides["bb"].get("queued_observation_artifact")
            if observation:
                shutil.copyfile(observation, out / "bb_queued_observation.json")
        else:
            sides["bb"], records["bb"] = {
                "error": "No successful tie-free stock oracle; BB not run",
                "unconsumed_responses": 0, "unconsumed_auxiliary_responses": 0}, []
        (out / "bb_result.json").write_text(json.dumps(sides["bb"], indent=2) + "\n")
        (out / "bb_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records["bb"]))
        relay_failures = list(relay.failures) if relay is not None else []
    deviations = []
    guard_errors = []
    compared = records
    if scenario in {"delegate_prune", "delegate_idle"} and all("native_evidence" in sides[side] for side in ("stock", "bb")):
        try:
            guard = RuntimeGeneratedIDs(sides["stock"]["native_evidence"], sides["bb"]["native_evidence"],
                                        [item["info"]["id"] for item in sessions])
            compared = {side: [{**row, "json": guard.body(row["json"], side)} for row in rows]
                        for side, rows in records.items()}
            if guard.counts["stock"] != guard.counts["bb"]:
                raise ValueError("Runtime ID position substitution counts differ")
            deviations.append({"name": "opencode_runtime_generated_ids", "source": RuntimeGeneratedIDs.__doc__,
                               "mapping": guard.maps, "counts_per_position": guard.counts,
                               "completed_notification_provenance": guard.completed_notifications,
                               "provenance": {side: str(out / f"{side}_result.json") + "#native_evidence"
                                              for side in sides}})
        except ValueError as exc:
            compared = records
            guard_errors.append({"deviation_guard": str(exc)})
    tie_usage = []
    if all("native_evidence" in sides[side] for side in ("stock", "bb")):
        tie_guard = CreatedTimeTieOrder(sides["stock"]["native_evidence"], sides["bb"]["native_evidence"])
        tied_rows = []
        for index, stock_row in enumerate(compared["stock"]):
            if index < len(compared["bb"]) and stock_row["path"] == compared["bb"][index]["path"]:
                body, groups = tie_guard.body(
                    stock_row["json"], compared["bb"][index]["json"],
                    raw_stock=records["stock"][index]["json"], raw_bb=records["bb"][index]["json"])
            else:
                body, groups = stock_row["json"], []
            tied_rows.append({**stock_row, "json": body})
            if groups:
                tie_usage.append({"request": index, "tie_groups": len(groups), "groups": groups})
        if tie_usage:
            compared = {**compared, "stock": tied_rows}
            deviations.append({"name": "opencode_created_time_tie_order",
                               "source": CreatedTimeTieOrder.__doc__, "counts_per_request": tie_usage,
                               "provenance": {side: str(out / f"{side}_result.json") + "#native_evidence.messages"
                                              for side in sides}})
    diffs = compare_records(compared["stock"], compared["bb"])
    unresolved = [{"side": side, "error": value["error"]} for side, value in sides.items() if "error" in value]
    unresolved.extend({"mcp_relay": error} for error in relay_failures)
    unresolved.extend(guard_errors)
    for side, result in sides.items():
        if result["unconsumed_responses"]:
            unresolved.append({"side": side, "unconsumed_responses": result["unconsumed_responses"]})
        if result["unconsumed_auxiliary_responses"]:
            unresolved.append({"side": side, "unconsumed_auxiliary_responses": result["unconsumed_auxiliary_responses"]})
    counts = {side: {
        "requests": len(rows),
        "compactions": sum(message["info"].get("summary") is True
                           and message["info"].get("finish") == "stop"
                           and not message["info"].get("error")
                           for message in sides[side].get("native_evidence", {}).get("messages", [])),
        "summary_requests": sum(is_summary(row) for row in rows),
        "requests_with_pruned_output": sum("[Old tool result content cleared]" in row["raw_body"] for row in rows),
    } for side, rows in records.items()}
    expected = 2 if scenario in {"auto_prune", "delegate_prune", "delegate_idle"} else 1
    if scenario == "overflow":
        expected += settings["deferred_recovery_attempts"]
    if scenario == "plugin_error":
        expected = settings["deferred_recovery_attempts"]
    scenario_findings = []
    if counts["stock"]["requests"] != counts["bb"]["requests"]:
        scenario_findings.append({"defect": "request counts differ", "stock": counts["stock"]["requests"], "bb": counts["bb"]["requests"]})
    for side, count in counts.items():
        if count["compactions"] != expected:
            scenario_findings.append({"side": side, "defect": "summary count", "expected": expected, "actual": count["compactions"]})
        if scenario in {"auto_prune", "delegate_prune"} and not count["requests_with_pruned_output"]:
            scenario_findings.append({"side": side, "defect": "pruning not visible in following requests"})
    prune_fences = {side: result.get("native_prune_fences", [])
                    for side, result in sides.items()}
    partial_overflow = scenario == "overflow" and settings["deferred_recovery_attempts"] > 0
    if partial_overflow:
        expected_calls = scenario_turns(scenario, settings)[2]["native_prune_call_ids"]
        for side, proofs in prune_fences.items():
            if (len(proofs) != 1
                    or proofs[0]["session_id"] != sides[side].get("session_id")
                    or proofs[0]["event_type"] != "message.part.updated"
                    or proofs[0]["call_ids"] != expected_calls
                    or set(proofs[0]["native_parts"]) != set(expected_calls)):
                scenario_findings.append({
                    "side": side, "defect": "Owned previous-turn native prune fence missing",
                    "native_prune_fences": proofs})
    scenario_findings.extend({"index": diff["index"], "defect": finding} for diff in diffs if diff["kind"] == "request_body" for finding in diff["compaction_input_findings"])
    scenario_findings.extend({"index": diff["index"], "defect": f"summary request field differs: {field['field']}"} for diff in diffs if diff["kind"] == "request_body" for field in diff["fields"] if field["classification"] == "compaction-path" and field["field"] != "input")
    if scenario in {"auto_prune", "delegate_prune"}:
        checkpoints = {side: [index for index, row in enumerate(rows) if any(item.get("type") == "function_call_output" and item.get("output") == "[Old tool result content cleared]" for item in row["json"].get("input", []))] for side, rows in records.items()}
        if checkpoints["stock"] != checkpoints["bb"]:
            scenario_findings.append({"defect": "pruning request checkpoints differ", **checkpoints})
    if scenario == "delegate_prune":
        for side, rows in records.items():
            summaries = [row for row in rows if is_summary(row)]
            children = sides[side].get("native_evidence", {}).get("children", [])
            child_ids = [child["session"]["id"] for child in children]
            if not summaries or not any(
                any(session_id in row["raw_body"] for session_id in child_ids)
                and "Active/Recent Delegated Sessions" in row["raw_body"] for row in summaries):
                scenario_findings.append({"side": side, "defect": "delegated session contributor absent from summary"})
    if scenario == "delegate_idle":
        followup = "\n" + scenario_turns(scenario, settings)[-1]["prompt"]
        for side in sides:
            messages = sides[side].get("native_evidence", {}).get("messages", [])
            notifications = [message for message in messages
                             if message["info"]["role"] == "user" and any(
                                 part.get("type") == "text"
                                 and RuntimeGeneratedIDs.COMPLETED_NOTIFICATION.fullmatch(part.get("text", ""))
                                 for part in message["parts"])]
            followups = [message for message in messages
                         if message["info"]["role"] == "user" and any(
                             part.get("type") == "text" and part.get("text") == followup
                             for part in message["parts"])]
            summaries = [message for message in messages
                         if message["info"].get("summary") is True
                         and message["info"].get("finish") == "stop"
                         and not message["info"].get("error")]
            if len(notifications) != 1 or len(followups) != 1 or len(summaries) != 2:
                scenario_findings.append({"side": side, "defect": "autonomous notification/followup/summary boundaries missing"})
            else:
                start = notifications[0]["info"]["time"]["created"]
                end = followups[0]["info"]["time"]["created"]
                if not all(start < summary["info"]["time"]["created"] < end for summary in summaries):
                    scenario_findings.append({"side": side, "defect": "compaction did not occur inside the autonomous source turn"})
                if side == "bb":
                    queued = sides[side].get("turns", [])
                    native_text = next(part["text"] for part in notifications[0]["parts"]
                                       if part.get("type") == "text")
                    if (len(queued) != 3 or any(turn["terminal_outcome"] != "completed" for turn in queued)
                            or queued[1]["content"] != native_text or queued[-1]["content"] != followup):
                        scenario_findings.append({"side": side, "defect": "production queue did not preserve the autonomous input and real followup"})
    if scenario == "plugin_error":
        for side in sides:
            errors = [message["info"].get("error", {}).get("name")
                      for message in sides[side].get("native_evidence", {}).get("messages", [])
                      if message["info"].get("error")]
            if errors != ["APIError"]:
                scenario_findings.append({"side": side, "defect": "plugin-only error is not the genuine native APIError",
                                          "native_errors": errors})
        reasons = [event["payload"]["payload"].get("reason")
                   for event in sides["bb"].get("events", [])
                   if event["type"] == "lifecycle_event"
                   and event["payload"].get("type") == "compaction_record_appended"
                   and event["payload"]["payload"].get("method") == "summary"]
        if reasons != ["threshold"] * expected:
            scenario_findings.append({"side": "bb", "defect": "plugin recovery entered native overflow or missed its checkpoint",
                                      "summary_reasons": reasons})
    report = {"harness": harness, "scenario": scenario, "settings": settings, "stock_version": STOCK_VERSION, "plugin_commit": PLUGIN_COMMIT if harness == "oh-my-opencode" else None,
              "plugin_source_evidence": PLUGIN_EVIDENCE if harness == "oh-my-opencode" else [], "comparison_basis": "Canonical JSON member order plus explicitly guarded deviations; raw bytes retained; every other value and array strict", "environment_regexes": [], "deviations_used": deviations,
              "workspace": str(workspace),
              "stock_oracle_selection": {"rule": stock_control_flow_ties.__doc__, "max_attempts": 5,
                                         "selected_attempt": selected, "attempts": attempts,
                                         "bb_runs": bb_runs, "comparison_max_bb_runs": 5},
              "bb_sidecar_oracle_selection": {
                  "rule": stock_control_flow_ties.__doc__, "max_attempts": 5,
                  "selected_attempt": bb_selected, "attempts": bb_attempts,
                  "native_source": "BB sidecar's own unmodified stock records; message-v2.ts:740 time-tie defect",
                  "rejection_signals": ["Native tie containing a summary assistant or compaction part"],
                  "bytes_or_counts_can_reject": False,
              },
              "native_prune_fences": prune_fences,
              "overflow_native_timing": {
                  "configuration_fact_not_deviation": True,
                  "race_evidence": str(ROOT / "artifacts/compaction_lanes/native-stock-defects/recovery_timer_prune_race.json"),
                  "real_human_ack_turns_before_overflow": 2,
                  "error_delivery": "After the prior turn's six owned native message.part.updated prune events on both sides",
                  "expected_native_branch": "Six original outputs PATCHed, insufficient; summarize fallbacks",
                  "same_original_tool_outputs_and_error": True,
                  "removed_response_descriptors": 0,
                  "sleeps_or_fake_parts_or_terminals": False,
                  "production_timer_or_prune_changes": False,
              } if partial_overflow else None,
              "stock_defects": [{"name": "time_created_tie_changes_native_compaction_control_flow",
                                "source": "packages/opencode/src/session/message-v2.ts:740",
                                "observed_attempt": str(ROOT / "artifacts/compaction_lanes/omo-plugin-error-proof-r1/plugin_error"),
                                "native_database": "stock_runtime/data/opencode/opencode.db#message",
                                "native_timestamps": [1791679068733, 1791679068737],
                                "native_completed_summaries": 3,
                                "plugin_summarize_posts": 2,
                                "response": "Reject native control-flow ties before comparison; BB never clones the race",
                                "bb_deviation": False},
                               *([{"name": "authenticated_handled_truncation_prompt_async_missing_parts",
                                   "source": "src/hooks/anthropic-context-window-limit-recovery/aggressive-truncation-strategy.ts:61-77; packages/opencode/src/session/prompt.ts:111",
                                   "evidence": str(ROOT / "artifacts/compaction_lanes/native-stock-defects/prompt_async_missing_parts.json"),
                                   "response": "Preserve native missing-parts failure, not a fabricated continuation; partial-overflow scenario uses the genuine summarize fallback",
                                   "bb_deviation": False}]
                                 if harness == "oh-my-opencode" else [])],
              "mock_script": {"identical_for_both_sides": True,
                              "sha256": hashlib.sha256(canonical(script)).hexdigest(),
                              "response_descriptors": len(descriptors),
                              "auxiliary_descriptors": auxiliary_script(scenario),
                              "deferred_recovery_attempts": settings["deferred_recovery_attempts"] if scenario in {"overflow", "plugin_error"} else 0,
                              "budget_source": f"breadboard_engine/compaction/presets/{settings['preset']}.yaml#triggers"},
              "shared_session_id": {"value": sides["stock"].get("session_id"), "source": "Stock /session created the empty fixture before either compared run; both stock CLI import.ts:134-169 and BB native executor import that same fixture", "delivery": "BB context.session_id before its first production request; no body rewriting", "fixture": str(seed_path)},
              "mcp_relay": {"upstreams": MCPRelay.UPSTREAMS, "readiness_order": list(MCPRelay.UPSTREAMS), "native_source": "mcp/index.ts:192-213,503-529", "wire_transformations": [], "independent_stock_proof": str(ROOT / "artifacts/compaction_lanes/omo-mcp-stock-order-proof/order_report.json")} if harness == "oh-my-opencode" else None,
              "out_of_scope_findings": [{"defect": "Legacy local LSP wrapper serialization recursion, integrator-reported; selected stock execution does not use this wrapper", "source": "breadboard_engine/conductor/components.py:579-585"}],
              "counts": counts, "diffs": diffs, "unresolved": unresolved, "compaction_findings": scenario_findings, "compaction_checks_passed": not scenario_findings and not any(result["unconsumed_responses"] or result["unconsumed_auxiliary_responses"] or "error" in result for result in sides.values()), "passed": not diffs and not unresolved and not scenario_findings,
              "admitted_created_time_tie_groups_per_request": [
                  {"request": index, "tie_groups": next(
                      (usage["tie_groups"] for usage in tie_usage if usage["request"] == index), 0)}
                  for index in range(max(len(records["stock"]), len(records["bb"])))],
              "raw_diffs_before_guarded_deviations": compare_records(records["stock"], records["bb"]) if deviations else diffs,
              "artifacts": {side: {"requests": str(out / f"{side}_requests.jsonl"), "result": str(out / f"{side}_result.json")} for side in sides}}
    (out / "lane_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=tuple(PROFILES), required=True)
    parser.add_argument("--scenario", choices=(*SCENARIOS, *PLUGIN_SCENARIOS, "all"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--child", choices=("stock", "bb"))
    parser.add_argument("--base-url")
    parser.add_argument("--stock-session-id")
    parser.add_argument("--mcp-relay")
    args = parser.parse_args()
    sessions = json.loads((args.out / "source_seed_sessions.json").read_bytes()) if args.child else None
    if args.child:
        if args.workspace is None:
            parser.error("--child requires the shared --workspace")
        if args.child == "stock":
            result = run_stock(args.harness, args.scenario, args.workspace, args.out / "stock_runtime", args.base_url, sessions, args.mcp_relay)
        else:
            result = run_bb(args.harness, args.scenario, args.workspace, args.base_url,
                            args.stock_session_id, args.out / "effective_episode_profile.json",
                            sessions, args.mcp_relay)
        (args.out / f"{args.child}_result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        raise SystemExit(1 if "error" in result else 0)
    available = (*SCENARIOS, *PLUGIN_SCENARIOS) if args.harness == "oh-my-opencode" else SCENARIOS
    scenarios = available if args.scenario == "all" else (args.scenario,)
    results = [run_scenario(args.harness, scenario, args.out / scenario) for scenario in scenarios]
    print(json.dumps([{key: report[key] for key in ("harness", "scenario", "counts", "passed", "unresolved")} | {"diff_count": len(report["diffs"])} for report in results], indent=2))
    raise SystemExit(0 if all(report["passed"] for report in results) else 1)


if __name__ == "__main__":
    main()
