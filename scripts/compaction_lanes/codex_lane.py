"""Live Codex 0.139.0 versus the dated BreadBoard engine profile.

Run from the repository root::

    BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. /Users/kylemccleary/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.codex_lane --out /tmp/codex-e4-lane

Requests are compared as canonical JSON bytes; recordings retain the raw bodies.
No values, array order, string whitespace or member presence are normalized.
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
from pathlib import Path
import subprocess
import sys
from typing import Any

from scripts.compaction_lanes.mock_provider import MockProvider, MockRequestHandler, MockThreadingServer

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


def scenario_script(scenario: str, limit: int) -> list[dict[str, Any]]:
    low = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    high = {"input_tokens": limit, "output_tokens": 100, "total_tokens": limit + 100}
    def text(value: str, usage: dict[str, int]) -> dict[str, Any]:
        return {"type": "text", "text": value, "usage": usage}
    if scenario == "pre_turn_and_second_compaction":
        return [text("Turn 1 answer.", high), text("Summary of turn 1.", low),
                text("Turn 2 answer.", high), text("Summary of turn 1 and turn 2.", low), text("Turn 3 answer.", low)]
    if scenario == "mid_turn_compaction":
        return [{"type": "tool_call", "name": "shell_command", "call_id": "call_mid_1",
                 "arguments": json.dumps({"command": "printf test", "workdir": "."}), "usage": high},
                text("Summary of mid-turn operations.", low), text("Finished mid-turn test.", low)]
    if scenario == "overflow_terminal":
        return [{"type": "error", "error": "context_length_exceeded", "message": "Your input exceeds the context window of this model.", "status_code": 400}]
    raise ValueError(scenario)


def prompts(scenario: str) -> list[str]:
    return ["Turn 1 prompt", "Turn 2 prompt", "Turn 3 prompt"] if scenario == SCENARIOS[0] else ["Execute mid-turn test prompt" if scenario == SCENARIOS[1] else "Overflow terminal test prompt"]


class EngineResponsesHandler(MockRequestHandler):
    """Serve complete normative Responses events and unary summary responses."""
    def _handle_openai_responses(self, item: dict[str, Any], request_json: Any) -> None:
        if item["type"] in {"error", "raw"}:
            return super()._handle_openai_responses(item, request_json)
        output = []
        if item.get("text"):
            output.append({"type": "message", "id": "msg_" + self.server.engine.next_id(), "status": "completed",
                           "role": "assistant", "content": [{"type": "output_text", "text": item["text"], "annotations": []}]})
        calls = item.get("tool_calls", [])
        if item["type"] == "tool_call":
            calls = [item]
        for call in calls:
            output.append({"type": "function_call", "id": call["call_id"], "status": "completed",
                           "call_id": call["call_id"], "name": call["name"], "arguments": call["arguments"]})
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
                initial["arguments"] = ""
            initial["status"] = "in_progress"
            emit("response.output_item.added", output_index=index, item=initial)
            if value["type"] == "message":
                emit("response.content_part.added", item_id=value["id"], output_index=index, content_index=0,
                     part={"type": "output_text", "text": "", "annotations": []})
                emit("response.output_text.delta", item_id=value["id"], output_index=index, content_index=0, delta=item["text"])
                emit("response.output_text.done", item_id=value["id"], output_index=index, content_index=0, text=item["text"])
                emit("response.content_part.done", item_id=value["id"], output_index=index, content_index=0, part=value["content"][0])
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
    home = scratch / "codex-home"
    home.mkdir(parents=True)
    (home / "config.toml").write_text(f'model = {json.dumps(config["model"])}\nmodel_provider = "lane"\nmodel_context_window = {config["window"]}\nmodel_auto_compact_token_limit = {config["limit"]}\n[model_providers.lane]\nname = "lane"\nbase_url = {json.dumps(base_url + "/v1")}\nwire_api = "responses"\nrequires_openai_auth = false\nenv_key = "MOCK_API_KEY"\n')
    env = dict(os.environ, CODEX_HOME=str(home), MOCK_API_KEY="lane-key")
    runs = []
    for index, prompt in enumerate(prompts(scenario)):
        args = [str(BINARY), "exec", *(["resume", "--last"] if index else []),
                "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json", prompt]
        result = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True, timeout=90)
        (scratch / f"turn-{index}.stdout").write_text(result.stdout)
        (scratch / f"turn-{index}.stderr").write_text(result.stderr)
        runs.append({"returncode": result.returncode, "argv": args})
        if result.returncode:
            break
    return {"runs": runs}


def run_bb(scenario: str, workspace: Path, scratch: Path, base_url: str, config: dict[str, Any]) -> dict[str, Any]:
    # Public SDK endpoint override. The actual provider runtime, controller,
    # tools, completion logic and resume reader are left untouched.
    os.environ.update(OPENAI_BASE_URL=base_url + "/v1", OPENAI_API_KEY="lane-key", PRESERVE_SEEDED_WORKSPACE="1")
    from breadboard_engine.agent import AgenticCoder
    agent = AgenticCoder(str(PROFILE), str(workspace), overrides={"compaction.contextWindow": config["window"], "logging.root_dir": str(scratch / "logs")}, force_local_mode=True)
    agent.initialize()
    results = []
    snapshot = None
    for index, prompt in enumerate(prompts(scenario)):
        if snapshot is not None:
            agent.agent.config["resume"] = {"snapshot_path": str(snapshot)}
        # The production entrypoint clears its output file before reading resume.
        # Use a new output path and preserve the previous snapshot as its input.
        snapshot = workspace / f".breadboard/checkpoints/lane-turn-{index}.json"
        result = agent.agent.run_agentic_loop("", prompt, config["model"], max_steps=3 if scenario == SCENARIOS[1] else 1,
                                             stream_responses=True, output_json_path=str(snapshot),
                                             tool_prompt_mode=agent._resolve_tool_prompt_mode(),
                                             context={"session_id": "codex-lane", "input_id": f"input-{index}", "turn_id": f"turn-{index}"})
        results.append({"completion_summary": result["completion_summary"], "run_dir": result.get("run_dir")})
        if result["completion_summary"].get("error") or result["completion_summary"].get("exit_kind") == "provider_error":
            break
    return {"runs": results, "snapshot": str(snapshot)}


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


def compare_requests(stock: list[dict[str, Any]], bb: list[dict[str, Any]]) -> list[dict[str, Any]]:
    differences = []
    for index in range(max(len(stock), len(bb))):
        if index >= len(stock) or index >= len(bb):
            differences.append({"request": index, "kind": "missing_request", "stock_present": index < len(stock), "bb_present": index < len(bb)})
            continue
        a, b = stock[index], bb[index]
        raw_a, raw_b = bytes.fromhex(a["raw_body_bytes"]), bytes.fromhex(b["raw_body_bytes"])
        canonical_a = json.dumps(a["json"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        canonical_b = json.dumps(b["json"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if a["path"] != b["path"] or canonical_a != canonical_b:
            differences.append({"request": index, "kind": "request_bytes", "stock_path": a["path"], "bb_path": b["path"],
                                "stock_raw_sha256": hashlib.sha256(raw_a).hexdigest(), "bb_raw_sha256": hashlib.sha256(raw_b).hexdigest(),
                                "stock_canonical_sha256": hashlib.sha256(canonical_a).hexdigest(), "bb_canonical_sha256": hashlib.sha256(canonical_b).hexdigest(),
                                "first_byte_difference": next((i for i, (x, y) in enumerate(zip(canonical_a, canonical_b)) if x != y), min(len(canonical_a), len(canonical_b))),
                                "fields": json_differences(a["json"], b["json"])})
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


def run_lane(out: Path, window: int = 16000) -> dict[str, Any]:
    config = settings(window)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"profile": str(PROFILE.relative_to(ROOT)), "settings": config, "deviations_used": [],
                              "comparison_basis": "canonical JSON: sort_keys=True, separators=(',', ':'), ensure_ascii=False; raw requests retained",
                              "files_changed": ["scripts/compaction_lanes/codex_lane.py", "tests/compaction_lanes/test_codex_lane.py",
                                                "breadboard_engine/compaction/presets/codex@0.139.0.yaml", "breadboard_engine/provider/runtimes/openai/responses.py",
                                                "breadboard_engine/provider/invoker.py", "breadboard_engine/compaction/overflow.py",
                                                "docs/guides/CONTEXT_COMPACTION.md"],
                              "tests_run": [{"command": f"BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. {sys.executable} -m scripts.compaction_lanes.codex_lane --out {out} --window {window}",
                                             "evidence": str(out / "lane_report.json")}],
                              "execution_limits": {"source": "lane admission, not production defaults", "stock_process_timeout_seconds": 90,
                                                   "side_process_timeout_seconds": 180, "bb_max_steps": {"pre_turn_and_second_compaction": 1, "mid_turn_compaction": 3, "overflow_terminal": 1}},
                              "overrides": [{"setting": "context_window", "stock": window, "bb": window, "production": config["production_window"]}],
                              "lane_results": [], "defects_found_and_fixed": [], "unresolved": [],
                              "rerun_command": f"BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. {sys.executable} -m scripts.compaction_lanes.codex_lane --out {out} --window {window}"}
    for scenario in SCENARIOS:
        folder = out / scenario
        workspace = folder / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        sides, records = {}, {}
        for side in ("stock", "bb"):
            scratch = folder / side
            scratch.mkdir()
            with EngineMockProvider(script=deepcopy(scenario_script(scenario, config["limit"])), record_path=scratch / "requests.jsonl") as provider:
                command = [sys.executable, "-m", "scripts.compaction_lanes.codex_lane", "--side", side, "--scenario", scenario,
                           "--workspace", str(workspace), "--out", str(scratch), "--base-url", provider.base_url, "--window", str(window)]
                result = subprocess.run(command, cwd=ROOT, env=dict(os.environ, BB_WORKSPACE_ROOT=str(ROOT)), text=True, capture_output=True, timeout=180)
                (scratch / "stdout.txt").write_text(result.stdout)
                (scratch / "stderr.txt").write_text(result.stderr)
                records[side] = list(provider.engine.recorded_requests)
                sides[side] = {"returncode": result.returncode, "remaining_script": len(provider.engine.script), "result": json.loads((scratch / "result.json").read_text()) if (scratch / "result.json").exists() else None}
        differences = compare_requests(records["stock"], records["bb"])
        counts = {side: compaction_count(records[side]) for side in sides}
        expected = {SCENARIOS[0]: 2, SCENARIOS[1]: 1, SCENARIOS[2]: 0}[scenario]
        lane = {"scenario": scenario, "stock_requests": len(records["stock"]), "bb_requests": len(records["bb"]),
                "stock_compactions": counts["stock"], "bb_compactions": counts["bb"], "diffs": differences, "sides": sides,
                "outcomes_valid": outcomes_valid(scenario, sides),
                "passed": not differences and counts["stock"] == expected and counts["bb"] == expected and outcomes_valid(scenario, sides)}
        report["lane_results"].append(lane)
        if not lane["passed"]:
            report["unresolved"].append({"scenario": scenario, "evidence": str(folder), "request_differences": len(differences), "stock_compactions": counts["stock"], "bb_compactions": counts["bb"]})
        (folder / "lane_report.json").write_text(json.dumps(lane, indent=2))
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
    args = parser.parse_args()
    if args.side:
        config = settings(args.window)
        result = (run_stock if args.side == "stock" else run_bb)(args.scenario, args.workspace, args.out, args.base_url, config)
        (args.out / "result.json").write_text(json.dumps(result, indent=2))
        return 0
    report = run_lane(args.out.resolve(), args.window)
    print(json.dumps({"passed": report["passed"], "report": str(args.out / "lane_report.json"), "scenarios": [{key: lane[key] for key in ("scenario", "stock_requests", "bb_requests", "stock_compactions", "bb_compactions", "passed")} for lane in report["lane_results"]]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
