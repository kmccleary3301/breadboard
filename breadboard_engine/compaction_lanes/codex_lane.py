"""Live Codex 0.139.0 versus the dated BreadBoard engine profile.

Run from the repository root::

    BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. /Users/kylemccleary/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.codex_lane --out /tmp/codex-e4-lane

Requests compare raw HTTP bodies; every raw mismatch is retained in reports.
Only approved terminal chunk IDs and wall times are guarded on tool-result
headers. Values, array order, body whitespace and member presence stay strict.
The production conductor writes and reads its own session snapshot between user
turns. The mock subclass serves complete streaming and unary Responses, without
changing compaction or request generation.
A failing report is evidence, not an acceptance claim. Exit status is nonzero if
any request differs, a scenario fails, or stock does not compact as required.
Context overflow bypasses transport retries; only the preset's controller may
recover it by compacting. Stock Codex's terminal overflow therefore sends once.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid

from breadboard_engine.compaction_lanes.mock_provider import MockProvider, MockRequestHandler, MockThreadingServer
from .request_bytes import raw_request_differences, raw_mismatch, guarded_changes
from .request_bytes import project_guarded_values

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "agent_configs/codex_0-139-0_gpt55_e4_10-9-2026.yaml"
PACKAGE = Path.home() / ".cache/bb-compaction-e4/pkgs/@openai__codex@0.139.0/node_modules/@openai/codex"
BINARY = PACKAGE.parent.parent / ".bin/codex"
PROMPT = (ROOT / "breadboard_engine/compaction/presets/prompts/codex@0.139.0/prompt.md").read_text()
SCENARIOS = ("pre_turn_and_second_compaction", "mid_turn_compaction", "overflow_terminal")


def settings(window: int) -> dict[str, Any]:
    from breadboard_engine.compilation.v2_loader import load_agent_config
    from breadboard_engine.compaction import load_compaction_config
    from breadboard_engine.compaction.presets import load_preset_document
    config = load_agent_config(str(PROFILE))
    preset = load_preset_document(load_compaction_config(config["compaction"]).preset)
    limit = preset["triggers"][0]["limit"]
    if limit["kind"] != "window_fraction" or limit["config_limit"] is not None:
        raise ValueError("Lane expects Codex's native window-fraction trigger")
    return {"model": config["providers"]["default_model"], "production_window": config["compaction"]["contextWindow"],
            "window": window, "limit": window * limit["percent"] // 100,
            "params": config["providers"]["models"][0]["params"], "preset": config["compaction"]["preset"]}


def scenario_script(scenario: str, limit: int, workspace: Path | None = None) -> list[dict[str, Any]]:
    low = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    high = {"input_tokens": limit, "output_tokens": 100, "total_tokens": limit + 100}
    def text(value: str, usage: dict[str, int]) -> dict[str, Any]:
        return {"type": "text", "text": value, "usage": usage}
    if scenario == "pre_turn_and_second_compaction":
        return [text("Turn 1 answer.", high), text("Summary of turn 1.", low),
                text("Turn 2 answer.", high), text("Summary of turn 1 and turn 2.", low), text("Turn 3 answer.", low)]
    if scenario == "mid_turn_compaction":
        arguments = {"cmd": "printf 'ready\\n'; IFS= read -r line; printf 'received:%s\\n' \"$line\"",
                     "tty": True, "yield_time_ms": 1000}
        if workspace is not None:
            arguments["workdir"] = str(workspace)
        return [{"type": "tool_call", "name": "exec_command", "call_id": "call_exec_1",
                 "arguments": json.dumps(arguments), "usage": low},
                {"type": "tool_call", "name": "write_stdin", "call_id": "call_stdin_1",
                 "arguments": json.dumps({"session_id": 0, "chars": "lane-input\n", "yield_time_ms": 1000}),
                 "session_id_from_output": True, "usage": high},
                text("Summary of mid-turn operations.", low), text("Finished mid-turn test.", low)]
    if scenario == "overflow_terminal":
        return [{"type": "error", "error": "context_length_exceeded", "message": "Your input exceeds the context window of this model.", "status_code": 400}]
    raise ValueError(scenario)


def prompts(scenario: str) -> list[str]:
    return ["Turn 1 prompt", "Turn 2 prompt", "Turn 3 prompt"] if scenario == SCENARIOS[0] else ["Execute mid-turn test prompt" if scenario == SCENARIOS[1] else "Overflow terminal test prompt"]


def exec_outputs(body: Any) -> list[dict[str, Any]]:
    items = body.get("input") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    calls = {item["call_id"] for item in items if isinstance(item, dict)
             and item.get("type") == "function_call" and item.get("name") in {"exec_command", "write_stdin"}}
    return [item for item in items if isinstance(item, dict) and item.get("type") == "function_call_output"
            and item.get("call_id") in calls and isinstance(item.get("output"), str)]


def observed_process_ids(records: list[dict[str, Any]]) -> list[int]:
    """Read successful stock allocations, not repetitions of the same history."""
    seen_calls, result = set(), []
    for record in records:
        for output in exec_outputs(record["json"]):
            header = output["output"].partition("\nOutput:\n")[0]
            match = re.search(r"^Process running with session ID ([0-9]+)$", header, re.MULTILINE)
            if match is not None and output["call_id"] not in seen_calls:
                process_id = int(match[1])
                if not 1000 <= process_id < 100000:
                    raise ValueError("Stock process allocation is outside process_manager.rs:347 range")
                seen_calls.add(output["call_id"])
                result.append(process_id)
    return result


class StockProcessIds:
    """A finite lane-only generator; an extra allocation never wraps."""
    def __init__(self, values: list[int]) -> None:
        self.values = tuple(values)
        self.position = 0

    def __call__(self) -> int:
        if self.position == len(self.values):
            raise ValueError("Process allocation mismatch: stock ID source exhausted")
        value = self.values[self.position]
        self.position += 1
        return value


def validate_process_allocations(expected: list[int], state: dict[str, int]) -> None:
    if state["allocation_count"] != len(expected) or state["draw_count"] != len(expected):
        raise ValueError(f"Process allocation mismatch: expected {len(expected)}, observed {state}")


def guarded_exec_headers(body: Any, *, side: str, request: int) -> tuple[Any, dict[str, int]]:
    """Guard stock-generated metadata only (context.rs:409-434), preserving body."""
    result = deepcopy(body)
    counts = {"codex_exec_chunk_id": 0, "codex_exec_wall_time": 0}
    for item in exec_outputs(result):
        value = item["output"]
        if not value.startswith(("Chunk ID", "Wall time")):
            continue
        lines = value.split("\n")
        index = 0
        if value.startswith("Chunk ID"):
            if re.fullmatch(r"Chunk ID: [0-9a-f]{6}", lines[0]) is None:
                raise ValueError(f"{side} request {request}: malformed codex_exec_chunk_id")
            lines[0] = "Chunk ID: <codex_exec_chunk_id>"
            counts["codex_exec_chunk_id"] += 1
            index = 1
        if index >= len(lines) or re.fullmatch(r"Wall time: (?:0|[1-9][0-9]*)\.[0-9]{4} seconds", lines[index]) is None:
            raise ValueError(f"{side} request {request}: malformed codex_exec_wall_time")
        lines[index] = "Wall time: <codex_exec_wall_time> seconds"
        counts["codex_exec_wall_time"] += 1
        item["output"] = "\n".join(lines)
    return result, counts

class EngineResponsesHandler(MockRequestHandler):
    """Serve complete normative Responses events and unary summary responses."""
    def _handle_openai_responses(self, item: dict[str, Any], request_json: Any) -> None:
        if item["type"] in {"error", "raw"}:
            return super()._handle_openai_responses(item, request_json)
        output = []
        if item.get("text"):
            output.append({"type": "message", "id": "msg_" + self.server.engine.next_id(), "status": "completed",
                           "role": "assistant", "content": [{"type": "output_text", "text": item["text"], "annotations": []}]})
            if item.get("phase") is not None:
                output[-1]["phase"] = item["phase"]
        calls = item.get("tool_calls", [])
        if item["type"] in {"tool_call", "custom_tool_call"}:
            calls = [item]
        for call in calls:
            if call.get("type") == "custom_tool_call":
                output.append({"type": "custom_tool_call", "id": call["call_id"], "status": "completed",
                               "call_id": call["call_id"], "name": call["name"], "input": call["input"]})
                continue
            arguments = call["arguments"]
            if call.get("session_id_from_output") is True:
                process_ids = observed_process_ids([{"json": request_json}])
                if not process_ids:
                    raise ValueError("Scripted live stdin requires a visible running native process")
                values = json.loads(arguments)
                values["session_id"] = process_ids[-1]
                arguments = json.dumps(values)
            output.append({"type": "function_call", "id": call["call_id"], "status": "completed",
                           "call_id": call["call_id"], "name": call["name"], "arguments": arguments})
            if call.get("namespace") is not None:
                output[-1]["namespace"] = call["namespace"]
        body = {"id": "resp_" + self.server.engine.next_id(), "object": "response", "created_at": 1700000000,
                "status": "completed", "model": request_json["model"], "output": output, "usage": item["usage"]}
        self.send_response(200)
        streaming = request_json.get("stream") is True
        self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
        self.end_headers()
        if not streaming:
            self.wfile.write(json.dumps(body).encode())
            return
        sequence = 0
        def emit(kind: str, **fields: Any) -> None:
            nonlocal sequence
            event = {"type": kind, "sequence_number": sequence, **fields}
            sequence += 1
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(event)}\n\n".encode())
        emit("response.created", response={**body, "status": "in_progress", "output": [], "usage": None})
        for index, value in enumerate(output):
            initial = deepcopy(value)
            if value["type"] == "message":
                initial["content"] = []
            else:
                initial["input" if value["type"] == "custom_tool_call" else "arguments"] = ""
            initial["status"] = "in_progress"
            emit("response.output_item.added", output_index=index, item=initial)
            if value["type"] == "message":
                emit("response.content_part.added", item_id=value["id"], output_index=index, content_index=0,
                     part={"type": "output_text", "text": "", "annotations": []})
                emit("response.output_text.delta", item_id=value["id"], output_index=index, content_index=0, delta=item["text"])
                emit("response.output_text.done", item_id=value["id"], output_index=index, content_index=0, text=item["text"])
                emit("response.content_part.done", item_id=value["id"], output_index=index, content_index=0, part=value["content"][0])
            elif value["type"] == "custom_tool_call":
                emit("response.custom_tool_call_input.delta", item_id=value["id"], output_index=index, delta=value["input"])
                emit("response.custom_tool_call_input.done", item_id=value["id"], output_index=index, input=value["input"])
            else:
                emit("response.function_call_arguments.delta", item_id=value["id"], output_index=index, delta=value["arguments"])
                emit("response.function_call_arguments.done", item_id=value["id"], output_index=index, arguments=value["arguments"])
            emit("response.output_item.done", output_index=index, item=value)
        emit("response.completed", response=body)


class EngineMockProvider(MockProvider):
    def start(self) -> str:
        self.server = MockThreadingServer((self.host, self.port), EngineResponsesHandler, self.engine)
        self.port = self.server.server_port
        import threading
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url


def run_stock(scenario: str, workspace: Path, scratch: Path, base_url: str, config: dict[str, Any]) -> dict[str, Any]:
    metadata = json.loads((PACKAGE / "package.json").read_text())
    if metadata["version"] != "0.139.0":
        raise ValueError("Stock package version differs from preset")
    home = Path((config.get("admission") or {}).get("codex_home", scratch / "codex-home"))
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'model = {json.dumps(config["model"])}\nmodel_provider = "lane"\nmodel_context_window = {config["window"]}\nmodel_auto_compact_token_limit = {config["limit"]}\n[model_providers.lane]\nname = "lane"\nbase_url = {json.dumps(base_url + "/v1")}\nwire_api = "responses"\nrequires_openai_auth = false\nhttp_headers = {{ Authorization = "Bearer lane-key" }}\n')
    env = dict(os.environ, CODEX_HOME=str(home))
    runs = []
    thread_id = None
    for index, prompt in enumerate(prompts(scenario)):
        args = [str(BINARY), "exec", *(["resume", "--last"] if index else []),
                "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json", prompt]
        result = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True, timeout=90)
        (scratch / f"turn-{index}.stdout").write_text(result.stdout)
        (scratch / f"turn-{index}.stderr").write_text(result.stderr)
        for line in result.stdout.splitlines():
            event = json.loads(line)
            if event.get("type") == "thread.started":
                value = event["thread_id"]
                if uuid.UUID(value).version != 7 or (thread_id is not None and value != thread_id):
                    raise ValueError("Stock root must be a stable UUIDv7 across resumed turns")
                thread_id = value
        runs.append({"returncode": result.returncode, "argv": args})
        if result.returncode:
            break
    return {"runs": runs, "thread_id": thread_id}


def run_bb(scenario: str, workspace: Path, scratch: Path, base_url: str, config: dict[str, Any]) -> dict[str, Any]:
    # Public SDK endpoint override. The actual provider runtime, controller,
    # tools, completion logic and resume reader are left untouched.
    os.environ.update(OPENAI_BASE_URL=base_url + "/v1", PRESERVE_SEEDED_WORKSPACE="1")
    admission = config["admission"]
    os.environ["CODEX_HOME"] = admission["codex_home"]
    from breadboard_engine.provider_broker import get_provider_broker
    get_provider_broker().set_runtime_api_key(
        "openai", "lane-key", session_id=admission["session_id"], base_url=base_url + "/v1")
    from breadboard_engine.agent import AgenticCoder
    results = []
    snapshot = None
    for index, prompt in enumerate(prompts(scenario)):
        # Match stock exec/resume: each turn starts a new conductor/client lifetime.
        overrides = {"compaction.contextWindow": config["window"], "logging.root_dir": str(scratch / f"logs-{index}")}
        if snapshot is not None:
            overrides["resume.snapshot_path"] = str(snapshot)
        agent = AgenticCoder(str(PROFILE), str(workspace), overrides=overrides, force_local_mode=True)
        agent.initialize()
        conductor = agent.agent
        from breadboard_engine.execution.codex_exec import codex_runtime
        codex_runtime(conductor).set_process_id_generator(
            StockProcessIds(admission["process_ids"]), thread_id=admission["session_id"])
        # The production entrypoint clears its output file before reading resume.
        # Use a new output path and preserve the previous snapshot as its input.
        snapshot = workspace / f".breadboard/checkpoints/lane-turn-{index}.json"
        result = agent.agent.run_agentic_loop("", prompt, config["model"], max_steps=3 if scenario == SCENARIOS[1] else 1,
                                             stream_responses=True, output_json_path=str(snapshot),
                                             tool_prompt_mode=agent._resolve_tool_prompt_mode(),
                                             context={"session_id": admission["session_id"], "native_environment": admission["native_environment"],
                                                      "input_id": f"input-{index}", "turn_id": f"turn-{index}"})
        results.append({"completion_summary": result["completion_summary"], "run_dir": result.get("run_dir")})
        if result["completion_summary"].get("error") or result["completion_summary"].get("exit_kind") == "provider_error":
            break
    process_state = codex_runtime(conductor).process_id_generator_state()
    # Every native allocation must be observable in this lane's successful PTY session.
    validate_process_allocations(admission["process_ids"], process_state)
    return {"runs": results, "snapshot": str(snapshot), "process_allocations": process_state}


def json_differences(stock: Any, bb: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(stock) is not type(bb):
        return [{"path": path, "stock": stock, "bb": bb}]
    if isinstance(stock, dict):
        result = []
        for key in sorted(stock.keys() | bb.keys()):
            if key not in stock or key not in bb:
                result.append({"path": path + "." + key, "stock_present": key in stock, "bb_present": key in bb,
                               "stock": stock.get(key), "bb": bb.get(key)})
            else:
                result.extend(json_differences(stock[key], bb[key], path + "." + key))
        return result
    if isinstance(stock, list):
        result = []
        if len(stock) != len(bb):
            result.append({"path": path + ".length", "stock": len(stock), "bb": len(bb)})
        for index, (a, b) in enumerate(zip(stock, bb)):
            result.extend(json_differences(a, b, f"{path}[{index}]"))
        return result
    return [] if stock == bb else [{"path": path, "stock": stock, "bb": bb}]


def compare_requests(stock: list[dict[str, Any]], bb: list[dict[str, Any]],
                     deviation_counts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    differences = []
    for index in range(max(len(stock), len(bb))):
        if index >= len(stock) or index >= len(bb):
            differences.append({"request": index, "kind": "missing_request", "stock_present": index < len(stock), "bb_present": index < len(bb)})
            continue
        a, b = stock[index], bb[index]
        raw_a, raw_b = bytes.fromhex(a["raw_body_bytes"]), bytes.fromhex(b["raw_body_bytes"])
        try:
            normalized_a, counts_a = guarded_exec_headers(a["json"], side="stock", request=index)
            normalized_b, counts_b = guarded_exec_headers(b["json"], side="bb", request=index)
        except ValueError as error:
            differences.append({"request": index, "kind": "deviation_guard", "message": str(error),
                                "raw_difference": raw_mismatch(raw_a, raw_b, index)})
            continue
        if deviation_counts is not None:
            deviation_counts.append({"request": index, "stock": counts_a, "bb": counts_b,
                                     "raw_difference": raw_mismatch(raw_a, raw_b, index),
                                     "names": [name for name in counts_a if counts_a[name] or counts_b[name]],
                                     "authority": "codex@0.139.0 core/src/exec.rs tool result headers",
                                     "changes": {"stock": guarded_changes(a["json"], normalized_a),
                                                 "bb": guarded_changes(b["json"], normalized_b)}})
        canonical_a = project_guarded_values(raw_a, normalized_a)
        canonical_b = project_guarded_values(raw_b, normalized_b)
        if a["path"] != b["path"] or a.get("method") != b.get("method") or canonical_a != canonical_b:
            differences.append({"request": index, "kind": "request_bytes", "stock_path": a["path"], "bb_path": b["path"],
                                "stock_raw_sha256": hashlib.sha256(raw_a).hexdigest(), "bb_raw_sha256": hashlib.sha256(raw_b).hexdigest(),
                                "stock_canonical_sha256": hashlib.sha256(canonical_a).hexdigest(), "bb_canonical_sha256": hashlib.sha256(canonical_b).hexdigest(),
                                "first_byte_difference": next((i for i, (x, y) in enumerate(zip(canonical_a, canonical_b)) if x != y), min(len(canonical_a), len(canonical_b))),
                                "fields": json_differences(normalized_a, normalized_b)})
    return differences


def compaction_count(records: list[dict[str, Any]]) -> int:
    def contains_prompt(value: Any) -> bool:
        if isinstance(value, str):
            return PROMPT.strip() in value
        if isinstance(value, dict):
            return any(contains_prompt(part) for part in value.values())
        if isinstance(value, list):
            return any(contains_prompt(part) for part in value)
        return False
    return sum(contains_prompt(record["json"]) for record in records)


def outcomes_valid(scenario: str, sides: dict[str, Any]) -> bool:
    if any(side["returncode"] != 0 or side["remaining_script"] != 0 or not side["result"] for side in sides.values()):
        return False
    stock = sides["stock"]["result"]["runs"]
    bb = sides["bb"]["result"]["runs"]
    if scenario == "overflow_terminal":
        return (len(stock) == len(bb) == 1 and stock[0]["returncode"] == 1
                and bb[0]["completion_summary"].get("completed") is False
                and bool(bb[0]["completion_summary"].get("error")))
    return (len(stock) == len(bb) == len(prompts(scenario))
            and all(run["returncode"] == 0 for run in stock)
            and all(run["completion_summary"].get("completed") is True for run in bb))


def run_lane(out: Path, window: int = 16000, *, stock_recordings: Path | None = None) -> dict[str, Any]:
    config = settings(window)
    out.mkdir(parents=True, exist_ok=True)
    if stock_recordings is not None:
        stock_recordings.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"profile": str(PROFILE.relative_to(ROOT)), "settings": config, "deviations_used": [],
                              "comparison_basis": "Raw HTTP request bytes after named source-guarded edits; representation differences fail by default",
                              "files_changed": ["scripts/compaction_lanes/codex_lane.py", "tests/compaction_lanes/test_codex_lane.py",
                                                "breadboard_engine/compaction/presets/codex@0.139.0.yaml", "breadboard_engine/provider/runtimes/openai/responses.py",
                                                "breadboard_engine/provider/invoker.py", "breadboard_engine/compaction/overflow.py",
                                                "docs/guides/CONTEXT_COMPACTION.md"],
                              "source_commit": "a7dff904308535e965aee87680c1fc5ef1d19eec",
                              "tests_run": [{"command": f"BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. {sys.executable} -m scripts.compaction_lanes.codex_lane --out {out} --window {window}",
                                             "evidence": str(out / "lane_report.json")}],
                              "execution_limits": {"source": "lane admission, not production defaults", "stock_process_timeout_seconds": 90,
                                                   "side_process_timeout_seconds": 180, "bb_max_steps": {"pre_turn_and_second_compaction": 1, "mid_turn_compaction": 3, "overflow_terminal": 1}},
                              "overrides": [{"setting": "context_window", "stock": window, "bb": window, "production": config["production_window"]}],
                              "lane_results": [], "defects_found_and_fixed": [
                                  {"defect": "Startup and GPT-5.5 envelope differed from stock",
                                   "fix": "Source-derived model instructions/catalog and repo-only admitted context",
                                   "citation": "codex-rs/models-manager/models.json:gpt-5.5; core/src/session/mod.rs:2752-2977"},
                                  {"defect": "Summary requests inherited episode tools and envelope members",
                                   "fix": "Sealed reduce.request settings and summary-only explicit empty tools",
                                   "citation": "tests/compaction/oracles/codex@0.139.0/recordings-gpt55/"},
                                  {"defect": "Compaction did not reinstall contextual fragments in stock phase order",
                                   "fix": "Paired stock marker classification and explicit pre-turn/mid-turn placement",
                                   "citation": "context-fragments/src/fragment.rs:114-127; core/src/compact.rs:297-307,465-506"},
                                  {"defect": "Resume lost compaction boundaries and window generation",
                                   "fix": "Opt-in checkpoint ledger restoration; production derives generation from restored boundaries",
                                   "citation": "core/src/session/session.rs:520-529,1049-1052"},
                                  {"defect": "Function arguments were re-serialized in wire history",
                                   "fix": "Opt-in closed source-native function replay",
                                   "citation": "codex-rs/protocol/src/models.rs:793-806"},
                                  {"defect": "Provider overflow triggered an unchanged transport retry",
                                   "fix": "Overflow bypasses fallback; 429/rate_limited remains excluded",
                                   "citation": "core/src/compact.rs:177-212"}
                              ], "unresolved": [
                                  {"issue": "General engine resume drops compaction_state unless explicitly enabled",
                                   "scope": "Fixed opt-in on the dated Codex profile; other profiles preserve prior behavior",
                                   "evidence": "breadboard_engine/agent_llm_openai.py resume snapshot import",
                                   "follow_up": "Integrator-owned default-on cutover after cross-profile checking"}
                              ],
                              "rerun_command": f"BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. {sys.executable} -m scripts.compaction_lanes.codex_lane --out {out} --window {window}"}
    for scenario in SCENARIOS:
        folder = out / scenario
        workspace = folder / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        from breadboard_engine.compilation.context_builders.codex import admit_environment
        home = folder / "codex-home"
        home.mkdir()
        installation_id = "b637ab9d-566a-45d7-a71e-8726b668d5ca"
        (home / "installation_id").write_text(installation_id)
        admission = {"codex_home": str(home), "installation_id": installation_id,
                     "native_environment": admit_environment(str(workspace))}
        admission_path = folder / "admission.json"
        admission_path.write_text(json.dumps(admission, indent=2))
        sides, records = {}, {}
        for side in ("stock", "bb"):
            scratch = folder / side
            scratch.mkdir()
            recording = stock_recordings / f"{scenario}.jsonl" if side == "stock" and stock_recordings is not None else scratch / "requests.jsonl"
            with EngineMockProvider(script=deepcopy(scenario_script(scenario, config["limit"], workspace)), record_path=recording) as provider:
                command = [sys.executable, "-m", "scripts.compaction_lanes.codex_lane", "--side", side, "--scenario", scenario,
                           "--workspace", str(workspace), "--out", str(scratch), "--base-url", provider.base_url,
                           "--window", str(window), "--admission", str(admission_path)]
                from breadboard_engine.security.child_environment import is_provider_credential_env_key
                environment = {key: value for key, value in os.environ.items()
                               if not is_provider_credential_env_key(key)}
                environment["BB_WORKSPACE_ROOT"] = str(ROOT)
                result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=180)
                (scratch / "stdout.txt").write_text(result.stdout)
                (scratch / "stderr.txt").write_text(result.stderr)
                records[side] = list(provider.engine.recorded_requests)
                sides[side] = {"returncode": result.returncode, "remaining_script": len(provider.engine.script), "result": json.loads((scratch / "result.json").read_text()) if (scratch / "result.json").exists() else None}
                if side == "stock" and sides[side]["result"] is not None:
                    admission["session_id"] = sides[side]["result"]["thread_id"]
                    admission["process_ids"] = observed_process_ids(records[side])
                    admission_path.write_text(json.dumps(admission, indent=2))
        deviation_counts = []
        differences = compare_requests(records["stock"], records["bb"], deviation_counts)
        counts = {side: compaction_count(records[side]) for side in sides}
        expected = {SCENARIOS[0]: 2, SCENARIOS[1]: 1, SCENARIOS[2]: 0}[scenario]
        lane = {"scenario": scenario, "stock_requests": len(records["stock"]), "bb_requests": len(records["bb"]),
                "stock_compactions": counts["stock"], "bb_compactions": counts["bb"], "diffs": differences, "sides": sides,
                "outcomes_valid": outcomes_valid(scenario, sides),
                "admission": admission,
                "deviation_counts": deviation_counts,
                "raw_differences": raw_request_differences(records["stock"], records["bb"]),
                "passed": not differences and counts["stock"] == expected and counts["bb"] == expected and outcomes_valid(scenario, sides)}
        report["lane_results"].append(lane)
        if not lane["passed"]:
            report["unresolved"].append({"scenario": scenario, "evidence": str(folder), "request_differences": len(differences), "stock_compactions": counts["stock"], "bb_compactions": counts["bb"]})
        (folder / "lane_report.json").write_text(json.dumps(lane, indent=2))
    for name, citation, guard in (
        ("codex_exec_chunk_id", "codex-rs/core/src/unified_exec/mod.rs:173-178", "six lowercase hexadecimal digits"),
        ("codex_exec_wall_time", "codex-rs/core/src/tools/context.rs:409-434", "nonnegative seconds with exactly four decimal places"),
    ):
        if any(counts[side].get(name, 0) for lane in report["lane_results"]
               for counts in lane["deviation_counts"] for side in ("stock", "bb")):
            report["deviations_used"].append({
                "name": name, "scope": "exec_command/write_stdin function_call_output metadata headers only",
                "guard": guard, "citation": citation, "source_commit": "a7dff904308535e965aee87680c1fc5ef1d19eec",
            })
    report["passed"] = all(lane["passed"] for lane in report["lane_results"])
    (out / "lane_report.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window", type=int, default=16000)
    parser.add_argument("--side", choices=("stock", "bb"))
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--admission", type=Path)
    parser.add_argument("--stock-recordings", type=Path)
    args = parser.parse_args()
    if args.side:
        config = settings(args.window)
        if args.admission is not None:
            config["admission"] = json.loads(args.admission.read_text())
        result = (run_stock if args.side == "stock" else run_bb)(args.scenario, args.workspace, args.out, args.base_url, config)
        (args.out / "result.json").write_text(json.dumps(result, indent=2))
        return 0
    report = run_lane(args.out.resolve(), args.window, stock_recordings=args.stock_recordings)
    print(json.dumps({"passed": report["passed"], "report": str(args.out / "lane_report.json"), "scenarios": [{key: lane[key] for key in ("scenario", "stock_requests", "bb_requests", "stock_compactions", "bb_compactions", "passed")} for lane in report["lane_results"]]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
