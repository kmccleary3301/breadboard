"""Run pinned Claude Code and the dated BreadBoard engine profile over Messages.

Run from the worktree root with the supplied reference virtualenv::

    BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. ~/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.claude_lane --out /tmp/claude-e4-lane

The stock CLI is unmodified. Its supported CLAUDE_AUTOCOMPACT_PCT_OVERRIDE is
applied to both sides, leaving the production window and output cap intact.
Requests are compared as canonical JSON per the integrator's decision; object
member order alone is ignored and raw bytes remain in the captures. All values,
array order, string whitespace and present/absent fields stay strict. This
profile declares no admitted request deviations.
Reports and raw captures are retained even when either runner fails.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

from scripts.compaction_lanes.mock_provider import (
    MockProvider, MockRequestHandler, MockScriptEngine, MockThreadingServer,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "agent_configs/claude_code_2-1-63_e4_10-9-2026.yaml"
PRESET = "claude_code@2.1.63"
STOCK_PACKAGE = Path.home() / ".cache/bb-compaction-e4/pkgs/@anthropic-ai__claude-code@2.1.63/node_modules/@anthropic-ai/claude-code"
SESSION_ID = "00000000-0000-4000-8000-000000000063"
PROMPT = "Inspect records.txt with Read repeatedly as instructed, then finish the inspection."
SUMMARY_PREFIX = "Your task is to create a detailed summary of the conversation so far,"


def production_settings() -> dict[str, Any]:
    import yaml
    document = yaml.safe_load(PROFILE.read_text())
    model = document["providers"]["default_model"]
    entry = next(item for item in document["providers"]["models"] if item["id"] == model)
    if document["compaction"]["preset"] != PRESET or entry["adapter"] != "anthropic":
        raise ValueError("Unexpected Claude production route")
    output = entry["params"]["max_output_tokens"]
    if output != document["provider_tools"]["anthropic"]["max_output_tokens"]:
        raise ValueError("Claude production output caps disagree")
    return {"profile": str(PROFILE), "preset": PRESET, "model": model,
            "wire_model": model.removeprefix("anthropic/"),
            "context_window": document["compaction"]["contextWindow"],
            "max_output_tokens": output,
            "provider_settings": document["provider_tools"]["anthropic"]}


def is_summary(body: dict[str, Any]) -> bool:
    # The exact first sentence comes from the pinned stock compact prompt and
    # the production preset, not from model names, budgets, or absent tools.
    messages = body.get("messages") or []
    if not messages or messages[-1].get("role") != "user":
        return False
    content = messages[-1].get("content", "")
    if isinstance(content, str):
        return content.startswith(SUMMARY_PREFIX)
    return isinstance(content, list) and any(
        part.get("type") == "text" and part.get("text", "").startswith(SUMMARY_PREFIX)
        for part in content if isinstance(part, dict)
    )


def signed_tool_response(item: dict[str, Any], model: str, *, thinking: str, signature: str,
                         redacted_data: str | None = None) -> dict[str, Any]:
    """Script native Messages SSE, including signed thinking before tool_use."""
    events = [{"type": "message_start", "message": {
        "id": item["id"], "type": "message", "role": "assistant", "content": [], "model": model,
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": item["usage"]["input_tokens"], "output_tokens": 1},
    }},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": thinking}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": signature}},
        {"type": "content_block_stop", "index": 0}]
    index = 1
    if redacted_data is not None:
        events.extend([
            {"type": "content_block_start", "index": index, "content_block": {"type": "redacted_thinking", "data": redacted_data}},
            {"type": "content_block_stop", "index": index},
        ])
        index += 1
    events.extend([
        {"type": "content_block_start", "index": index,
         "content_block": {"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": {}}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": item["arguments"]}},
        {"type": "content_block_stop", "index": index},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
         "usage": {"output_tokens": item["usage"]["output_tokens"]}},
        {"type": "message_stop"},
    ])
    return {"type": "raw", "status_code": 200, "headers": {"Content-Type": "text/event-stream"},
            "chunks": [f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events]}


def scenario_responses(scenario: str, workspace: Path, settings: dict[str, Any], pct: float) -> dict[str, list[dict[str, Any]]]:
    window = settings["context_window"]
    threshold = min(int((window - min(settings["max_output_tokens"], 20000)) * pct / 100),
                    window - min(settings["max_output_tokens"], 20000) - 13000)
    high = {"input_tokens": threshold + 4000, "output_tokens": 10}
    low = {"input_tokens": 100, "output_tokens": 10}
    tool = lambda i: signed_tool_response(
        {"id": f"msg_read_{i}", "name": "Read", "call_id": f"toolu_read_{i}",
         "arguments": json.dumps({"file_path": str(workspace / "records.txt")}), "usage": high},
        settings["wire_model"], thinking=f"Inspect records in pass {i}.", signature=f"claude-lane-signature-{i}",
    )
    summaries = [{"type": "text", "id": f"msg_summary_{i}",
                  "text": f"<summary>Inspected records.txt in pass {i}. Continue the inspection.</summary>",
                  "usage": low} for i in (1, 2)]
    if scenario == "long_session":
        return {"main": [tool(1), tool(2), {"type": "text", "id": "msg_final", "text": "Inspection complete.", "usage": low}],
                "summary": summaries}
    if scenario == "overflow":
        return {"main": [{"type": "error", "error": "context_length_exceeded", "status_code": 400,
                          "message": f"prompt is too long: {window + 1000} tokens > {window} maximum"}],
                "summary": []}
    raise ValueError(f"Unknown scenario: {scenario}")


class ClaudeScriptEngine(MockScriptEngine):
    """Keep separate scripted main/summary queues, and record every request."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]], record_path: Path):
        super().__init__(record_path=record_path)
        self.responses = deepcopy(responses)
        self.local = threading.local()
        self.dispatches: list[dict[str, Any]] = []

    def record_request(self, **kwargs: Any) -> dict[str, Any]:
        record = super().record_request(**kwargs)
        self.local.record = record
        return record

    def next_response(self) -> dict[str, Any]:
        record = self.local.record
        kind = "summary" if is_summary(record["json"] or {}) else "main"
        with self._lock:
            queue = self.responses[kind]
            self.dispatches.append({"kind": kind, "request_sha256": hashlib.sha256(bytes.fromhex(record["raw_body_bytes"])).hexdigest(),
                                    "exhausted": not queue})
            if not queue:
                raise RuntimeError(f"Claude {kind} script exhausted")
            return queue.pop(0)


class ClaudeRequestHandler(MockRequestHandler):
    """Add nonstreaming Messages for the actual BB summary runtime."""

    def _handle_anthropic_messages(self, item: dict[str, Any], request_json: Any) -> None:
        if item["type"] in ("error", "raw") or request_json.get("stream"):
            return super()._handle_anthropic_messages(item, request_json)
        if item["type"] != "text":
            raise ValueError("The nonstreaming Claude fixture must be a text message")
        body = {"id": item["id"], "type": "message", "role": "assistant",
                "model": request_json["model"], "content": [{"type": "text", "text": item["text"]}],
                "stop_reason": "end_turn", "stop_sequence": None, "usage": item["usage"]}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ClaudeMockProvider(MockProvider):
    def __init__(self, responses: dict[str, list[dict[str, Any]]], record_path: Path):
        super().__init__()
        self.engine = ClaudeScriptEngine(responses, record_path)

    def start(self) -> str:
        self.server = MockThreadingServer((self.host, self.port), ClaudeRequestHandler, self.engine)
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url


def compare_requests(stock: list[dict[str, Any]], bb: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diffs = []
    for index in range(max(len(stock), len(bb))):
        if index >= len(stock) or index >= len(bb):
            diffs.append({"request_index": index, "kind": "missing_request", "missing_side": "stock" if index >= len(stock) else "bb"})
            continue
        left, right = stock[index], bb[index]
        raw_a, raw_b = bytes.fromhex(left["raw_body_bytes"]), bytes.fromhex(right["raw_body_bytes"])
        body_a, body_b = json.loads(raw_a), json.loads(raw_b)
        a = json.dumps(body_a, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        b = json.dumps(body_b, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if a != b or left["path"] != right["path"] or left["method"] != right["method"]:
            first = next((i for i, pair in enumerate(zip(a, b)) if pair[0] != pair[1]), min(len(a), len(b)))
            keys = sorted(set(body_a) | set(body_b))
            diffs.append({"request_index": index, "kind": "request_bytes", "first_differing_byte": first,
                          "stock_size": len(a), "bb_size": len(b),
                          "stock_sha256": hashlib.sha256(a).hexdigest(), "bb_sha256": hashlib.sha256(b).hexdigest(),
                          "stock_raw_sha256": hashlib.sha256(raw_a).hexdigest(), "bb_raw_sha256": hashlib.sha256(raw_b).hexdigest(),
                          "stock_path": left["path"], "bb_path": right["path"],
                          "differing_fields": [key for key in keys if key not in body_a or key not in body_b or body_a[key] != body_b[key]]})
    return diffs


def _bb_worker(workspace: Path, scratch: Path, base_url: str, pct: float) -> None:
    from breadboard_engine.agent import AgenticCoder
    from breadboard_engine.provider_broker import get_provider_broker
    broker = get_provider_broker()
    broker.set_config_api_key("anthropic", "bb-claude-lane", base_url=base_url)
    events = []
    try:
        agent = AgenticCoder(str(PROFILE), str(workspace), {
            "compaction.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": pct,
        }, force_local_mode=True)
        result = agent.run_task(PROMPT, max_iterations=8, stream=True,
                                context={"session_id": SESSION_ID, "input_id": "claude-lane-input", "turn_id": "claude-lane-turn"},
                                event_emitter=lambda event, payload, turn=None: events.append({"event": event, "payload": payload, "turn": turn}))
        (scratch / "bb-result.json").write_text(json.dumps(result, default=str, indent=2) + "\n")
    finally:
        (scratch / "bb-events.json").write_text(json.dumps(events, default=str, indent=2) + "\n")
        broker.remove_config_api_key("anthropic")


def _invoke(command: list[str], env: dict[str, str], cwd: Path, output: Path, timeout: int) -> dict[str, Any]:
    try:
        result = subprocess.run(command, input="", cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Preserve the failed process's output, do not synthesize a success.
        def text(value: Any) -> str:
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        output.with_suffix(".stdout").write_text(text(exc.stdout))
        output.with_suffix(".stderr").write_text(text(exc.stderr))
        return {"command": command, "timeout_seconds": timeout, "timed_out": True, "returncode": None}
    output.with_suffix(".stdout").write_text(result.stdout)
    output.with_suffix(".stderr").write_text(result.stderr)
    return {"command": command, "timeout_seconds": timeout, "timed_out": False, "returncode": result.returncode}


def summary_request_count(requests: list[dict[str, Any]]) -> int:
    """Count wire attempts, independent of mock dispatch or response success."""
    return sum(is_summary(request.get("json") or {}) for request in requests)


def long_session_terminal_problems(stock_result: dict[str, Any], bb_result: dict[str, Any]) -> list[str]:
    problems = []
    if stock_result.get("is_error") is not False or stock_result.get("result") != "Inspection complete.":
        problems.append("Stock did not terminate with successful inspection completion")
    if bb_result.get("completed") is not True or bb_result.get("provider_error") or (bb_result.get("completion_summary") or {}).get("error"):
        problems.append("BB agent did not report successful task completion")
    return problems


def run_scenario(scenario: str, out: Path, pct: float = 10) -> dict[str, Any]:
    settings = production_settings()
    metadata = json.loads((STOCK_PACKAGE / "package.json").read_text())
    if metadata["version"] != "2.1.63":
        raise ValueError("Stock Claude version does not match the profile")
    if not 0 < pct <= 100:
        raise ValueError("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE must be in (0, 100]")
    out.mkdir(parents=True, exist_ok=True)
    workspace = out / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "records.txt").write_text("record one\nrecord two\n")
    responses = scenario_responses(scenario, workspace, settings, pct)
    captures = {}
    for side in ("stock", "bb"):
        scratch = out / side
        scratch.mkdir(exist_ok=True)
        env = {key: value for key, value in os.environ.items() if key not in {
            "CLAUDECODE", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_COMPACT", "DISABLE_AUTO_COMPACT", "DISABLE_MICROCOMPACT",
        }}
        env.update(HOME=str(scratch / "home"), CLAUDE_CONFIG_DIR=str(scratch / "home/.claude"),
                   PYTHONPATH=str(ROOT), BB_WORKSPACE_ROOT=str(ROOT), PRESERVE_SEEDED_WORKSPACE="1",
                   BREADBOARD_CREDENTIAL_STORE_PATH=str(scratch / "credentials.sqlite3"),
                   CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=str(pct), CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(settings["max_output_tokens"]),
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1", DISABLE_AUTOUPDATER="1")
        with ClaudeMockProvider(responses, scratch / "requests.jsonl") as mock:
            if side == "stock":
                env.update(ANTHROPIC_API_KEY="bb-claude-lane", ANTHROPIC_BASE_URL=mock.base_url)
                command = ["node", str(STOCK_PACKAGE / "cli.js"), "-p", PROMPT, "--model", settings["wire_model"],
                           "--output-format", "stream-json", "--verbose", "--setting-sources", "",
                           "--dangerously-skip-permissions", "--no-session-persistence", "--strict-mcp-config",
                           "--session-id", SESSION_ID, "--debug-file", str(scratch / "debug.log")]
            else:
                command = [sys.executable, "-m", "scripts.compaction_lanes.claude_lane", "--bb-worker",
                           "--workspace", str(workspace), "--scratch", str(scratch), "--base-url", mock.base_url, "--pct", str(pct)]
            invocation = _invoke(command, env, workspace, scratch / "run", 120)
            captures[side] = {"invocation": invocation, "requests": mock.engine.recorded_requests,
                              "dispatches": mock.engine.dispatches, "remaining": {kind: len(queue) for kind, queue in mock.engine.responses.items()}}
            (scratch / "dispatches.json").write_text(json.dumps(captures[side]["dispatches"], indent=2) + "\n")
    diffs = compare_requests(captures["stock"]["requests"], captures["bb"]["requests"])
    counts = {side: {"request_count": len(value["requests"]),
                     "compaction_request_count": summary_request_count(value["requests"]),
                     "invocation": value["invocation"], "script_remaining": value["remaining"]} for side, value in captures.items()}
    stock_events = [json.loads(line) for line in (out / "stock/run.stdout").read_text().splitlines() if line.startswith("{")]
    counts["stock"]["compaction_count"] = sum(event.get("subtype") == "compact_boundary" for event in stock_events)
    bb_events_path = out / "bb/bb-events.json"
    bb_events = json.loads(bb_events_path.read_text()) if bb_events_path.exists() else []
    counts["bb"]["compaction_count"] = sum(
        event["event"] == "lifecycle_event" and event["payload"].get("type") == "compaction_finished"
        and event["payload"].get("payload", {}).get("reason") == "threshold"
        and event["payload"].get("payload", {}).get("status") == "committed"
        for event in bb_events
    )
    counts["bb"]["failed_compactions"] = [
        event["payload"]["payload"] for event in bb_events
        if event["event"] == "lifecycle_event" and event["payload"].get("type") == "compaction_finished"
        and event["payload"].get("payload", {}).get("status") == "failed"
    ]
    stock_result = next((event for event in reversed(stock_events) if event.get("type") == "result"), {})
    bb_result_path = out / "bb/bb-result.json"
    bb_result = json.loads(bb_result_path.read_text()) if bb_result_path.exists() else {}
    counts["stock"]["terminal"] = {"text": stock_result.get("result"), "is_error": stock_result.get("is_error")}
    counts["bb"]["terminal"] = {"completion_summary": bb_result.get("completion_summary"),
                                "provider_error": bb_result.get("provider_error"), "completed": bb_result.get("completed")}
    problems = []
    for side, value in counts.items():
        expected_returncode = 1 if side == "stock" and scenario == "overflow" else 0
        if value["invocation"]["timed_out"] or value["invocation"]["returncode"] != expected_returncode:
            problems.append(f"{side} runner returned an unexpected exit status; see {out / side / 'run.stderr'}")
        if any(item["exhausted"] for item in captures[side]["dispatches"]):
            problems.append(f"{side} issued an unscripted request; see dispatches.json")
        if scenario == "long_session" and value["compaction_count"] < 2:
            problems.append(f"{side} completed fewer than two threshold compactions")
        if any(value["script_remaining"].values()):
            problems.append(f"{side} did not consume the full scenario")
    for failure in counts["bb"]["failed_compactions"]:
        problems.extend(f"BB compaction failed: {stage['detail']}" for stage in failure.get("stages", []) if stage.get("status") == "failed")
    if scenario == "long_session":
        problems.extend(long_session_terminal_problems(stock_result, bb_result))
    if scenario == "overflow":
        if stock_result.get("result") != "Prompt is too long" or stock_result.get("is_error") is not True:
            problems.append("Stock did not terminate with its native Prompt is too long result")
        if counts["bb"]["request_count"] != 1 or counts["bb"]["compaction_request_count"]:
            problems.append("BB did not attempt exactly one request followed by terminal overflow")
        terminal_error = (bb_result.get("completion_summary") or {}).get("error") or {}
        if terminal_error.get("type") != "provider" or bb_result.get("completed") is not False:
            problems.append("BB did not report a terminal provider error")
    if diffs:
        problems.append("Unadmitted request differences; this profile has no recorded request-byte deviations")
    report = {"schema": "bb.compaction_lane.v1", "harness": PRESET, "scenario": scenario, "settings": settings,
              "overrides": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": pct},
              "comparison_basis": "canonical JSON, sorted object keys, compact separators, ensure_ascii=False; no value normalization",
              "execution_admission": {"max_iterations": 8, "subprocess_timeout_seconds": 120,
                                      "stock_permissions": "bypassPermissions", "stock_settings_sources": [],
                                      "stock_nonessential_traffic": False},
              "stock": counts["stock"], "bb": counts["bb"], "diffs": diffs, "deviations_used": [],
              "unresolved": problems, "passed": not problems,
              "artifacts": {side: str(out / side) for side in captures}}
    (out / "lane_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scenario", choices=("long_session", "overflow"))
    parser.add_argument("--pct", type=float, default=10)
    parser.add_argument("--bb-worker", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--base-url")
    args = parser.parse_args()
    if args.bb_worker:
        _bb_worker(args.workspace, args.scratch, args.base_url, args.pct)
        return
    if args.out is None:
        parser.error("--out is required")
    out = args.out.resolve()
    scenarios = [args.scenario] if args.scenario else ["long_session", "overflow"]
    reports = [run_scenario(scenario, out / scenario, args.pct) for scenario in scenarios]
    report = {"files_changed": [
                  "scripts/compaction_lanes/claude_lane.py", "tests/compaction_lanes/test_claude_lane.py",
                  "breadboard_engine/provider/runtimes/anthropic.py",
                  "breadboard_engine/provider/adapters.py",
                  "breadboard_engine/conductor/model_output.py", "breadboard_engine/conductor/modes.py",
                  "breadboard_engine/conductor/prompt_planner.py", "breadboard_engine/provider/normalizer.py",
                  "breadboard_engine/provider/contract_messages.py",
                  "tests/providers/test_provider_runtime_anthropic.py", "tests/test_tool_prompt_planner.py",
                  "tests/test_e4_target_packages.py",
                  "agent_configs/claude_code_2-1-63_e4_10-9-2026.yaml",
                  "breadboard_engine/compaction/methods.py", "breadboard_engine/compaction/summary_model.py",
                  "breadboard_engine/compaction/controller.py", "breadboard_engine/compaction/primitives/chat_reducers.py",
                  "breadboard_engine/compaction/presets/claude_code@2.1.63.yaml",
                  "tests/compaction/test_codex_claude_runtime.py", "docs/guides/CONTEXT_COMPACTION.md",
              ],
              "tests_run": [{"command": f"BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. {sys.executable} -m scripts.compaction_lanes.claude_lane --out {out} --pct {args.pct}",
                             "tail": f"Lane {'passed' if all(result['passed'] for result in reports) else 'failed'}; see per-scenario counts, diffs and terminal evidence"}],
              "lane_results": reports, "deviations_used": [],
              "defects_found_and_fixed": [
                  {"defect": "SDK rejects legacy sampling kwargs", "fix": "Signature-aware documented extra_body transport",
                   "citations": ["anthropic/resources/messages/messages.py:142-146,972-999,1049-1053", "tests/compaction_lanes/test_claude_lane.py::test_real_anthropic_sdk_preserves_legacy_sampling_members"]},
                  {"defect": "SDK snapshot events rejected as unknown wire events", "fix": "Recognize only documented SDK snapshot model events",
                   "citations": ["anthropic/lib/streaming/_types.py:22-105"]},
                  {"defect": "Streaming delta usage loses input tokens and expands unset SDK cache fields",
                   "fix": "Merge start and delta usage; model_dump(exclude_unset=True)", "citations": ["breadboard_engine/provider/runtimes/anthropic.py::_call_streaming", "breadboard_engine/provider/runtimes/anthropic.py::_extract_usage"]},
                  {"defect": "Summary invokes client=None outside broker lease", "fix": "Use production conductor client lease with full route id",
                   "citations": ["breadboard_engine/compaction/controller.py::_context_for_pass", "breadboard_engine/compaction/summary_model.py::complete"]},
                  {"defect": "Claude summary options cannot express stock streaming/temp1/Read", "fix": "Approved optional reducer request settings",
                   "citations": ["stock package/cli.js:6110", "engine_requests/COMPACTION_PRESET_SCHEMA_NOTE_20261010.md"]},
                  {"defect": "Native user/tool_use_id result storage breaks canonical recording and summary conversion",
                   "fix": "Build the existing correlated tool transport at AnthropicAdapter.create_tool_result_message; runtime alone restores native Messages wire",
                   "citations": ["breadboard_engine/provider/adapters.py:316-329", "breadboard_engine/provider/runtimes/anthropic.py::_convert_messages",
                                 "tests/compaction_lanes/test_claude_lane.py::test_anthropic_tool_turn_summary_and_next_request_round_trip"]},
                  {"defect": "Signed and redacted thinking lost from assistant history and summary requests",
                   "fix": "Own reasoning on ProviderMessage, preserve it in canonical history, restore opaque native signatures in order, retain repeated thought blocks",
                   "citations": ["breadboard_engine/provider/runtimes/anthropic.py::_normalize_response",
                                 "breadboard_engine/provider/runtimes/anthropic.py::_convert_messages",
                                 "breadboard_engine/conductor/model_output.py::_assistant_history_message",
                                 "tests/compaction_lanes/test_claude_lane.py::test_repeated_thinking_text_keeps_each_distinct_signature_in_order"]},
                  {"defect": "Canonical tool continuations receive synthetic empty user turns",
                   "fix": "Suppress native tool continuation stubs; isolate directives only after role=tool, preserving base text-tool stubs and append-to-last behavior",
                   "citations": ["breadboard_engine/conductor/modes.py::get_model_response",
                                 "breadboard_engine/conductor/prompt_planner.py::ToolPromptPlanner.plan",
                                 "tests/compaction_lanes/test_claude_lane.py::test_production_tool_continuation_never_injects_empty_user_when_prompts_suppressed"]},
                  {"defect": "Lane undercounts failed summary attempts and does not require successful completion",
                   "fix": "Count all recorded summary requests and reject failed/missing long-session terminals",
                   "citations": ["scripts/compaction_lanes/claude_lane.py::summary_request_count",
                                 "scripts/compaction_lanes/claude_lane.py::long_session_terminal_problems"]},
              ],
              "unresolved": [item for result in reports for item in result["unresolved"]],
              "rerun_command": f"BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. {sys.executable} -m scripts.compaction_lanes.claude_lane --out {out} --pct {args.pct}"}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(out / "report.json"), "passed": all(result["passed"] for result in reports)}))
    raise SystemExit(0 if all(result["passed"] for result in reports) else 1)


if __name__ == "__main__":
    main()
