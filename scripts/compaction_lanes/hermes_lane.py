"""Pinned Hermes stock/worker compaction lane.

Run with ``python -m scripts.compaction_lanes.hermes_lane --output lane.json``.
The stock oracle imports AIAgent and runs run_conversation, never a copied loop.
Only declared summary-body deviations are removed by the comparator. Each side
runs in a fresh interpreter with the same workspace, fixtures and scripted HTTP
provider. The conductor uses its production checkpointed loop and lowered r2
profile; the lane policy forwards the source's exact HTTP body to the provider.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any

from scripts.compaction_lanes.mock_provider import MockProvider

ROOT = Path(__file__).resolve().parents[2]
TARGET_ID = "hermes-agent-r2@2026.9.11"
TARGET_DIR = ROOT / "config/e4_targets/hermes_agent/2026.9.11-r2"
SOURCE = Path.home() / ".cache/bb-compaction-e4/hermes-agent-939e45c91d751fadd94dcd1b873ac3cb44846213"
SUMMARY = "The inspected files contain numbered records. Continue the requested inspection."
TERMINAL = "Context length exceeded: max compression attempts (3) reached."
PROMPT = "Read the numbered records with the supplied tools, then finish the inspection."


def target_profile() -> Any:
    from breadboard_engine.e4_targets import read_e4_target
    from breadboard.product.harness.targets import lower_e4_target
    package = read_e4_target(TARGET_ID, read_resource=lambda path: (ROOT / "config/e4_targets" / path).read_bytes())
    return lower_e4_target(package, {}).runtime_profile


def scenario_script(scenario: str) -> list[dict[str, Any]]:
    # Three real tool rounds leave a middle outside the protected head and tail.
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    rounds = []
    for batch in range(3 if scenario == "threshold" else 4):
        calls = [{"call_id": f"read-{i}", "name": "read_file", "arguments": json.dumps({"path": f"records-{i}.txt"})} for i in range(batch * 24, (batch + 1) * 24)]
        rounds.append({"type": "response", "id": f"inspect-{batch}", "text": "Inspecting records.", "tool_calls": calls, "usage": usage})
    if scenario == "threshold":
        rounds[-1]["usage"] = {"input_tokens": 120000, "output_tokens": 10, "total_tokens": 120010}
    summary = {"type": "text", "id": "summary", "text": SUMMARY, "usage": usage}
    if scenario == "threshold":
        return [*rounds, summary, {"type": "text", "id": "done", "text": "Inspection complete.", "usage": usage}]
    # On the third pass the history is already irreducible. A newly reported
    # provider limit still earns a retry, exactly as turn_overflow.py:400-406
    # specifies, so the fourth error reaches stock's three-attempt backstop.
    errors = [
        {"type": "error", "id": f"overflow-{i}", "error": "context_length_exceeded",
         "message": f"This model's maximum context length is {limit} tokens.",
         "status_code": 400}
        for i, limit in enumerate((131072, 120000, 110000, 100000))
    ]
    if scenario == "retry_turns":
        transient = {"type": "error", "id": "aux-blip", "error": "server_error", "message": "upstream unavailable", "status_code": 500}
        final = {"type": "text", "id": "done", "text": "Inspection complete.", "usage": usage}
        return [*rounds[:3], errors[0], transient, transient, summary,
                rounds[3], errors[0], transient, transient, summary, final]
    return [*rounds, errors[0], summary, errors[1], summary, errors[2], errors[3]]


def seed_workspace(workspace: Path, scenario: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for i in range(72 if scenario == "threshold" else 96):
        # Distinct real tool results give the compressor a middle to summarize.
        text = "".join(f"record {i} item {j}: value {i * 10000 + j}\n" for j in range(100))
        (workspace / f"records-{i}.txt").write_text(text)


def _pin_replay_identity() -> None:
    from datetime import datetime
    import uuid
    import time
    # Stock session identity: agent/agent_init.py:1122-1125; turn identity:
    # agent/turn_context.py:470-474; message clock: message_metadata.py:5,27;
    # prompt date: agent/system_prompt.py:437-438.
    class ReplayDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return cls.fromtimestamp(1791504000, tz)
    import agent.agent_init as init
    init.datetime = ReplayDateTime
    counter = iter(range(1, 1000000))
    uuid.uuid4 = lambda: uuid.UUID(int=next(counter))
    time.time = lambda: 1791504000.125
    import agent.message_metadata as metadata
    import hermes_time
    metadata.wall_time = time.time
    hermes_time.now = lambda: ReplayDateTime.now(hermes_time.get_timezone())


def _stock_environment(workspace: Path, scratch: Path, profile: Any) -> None:
    home = scratch / "hermes-home"
    home.mkdir(parents=True)
    for rel in ("memories/MEMORY.md", "memories/USER.md", "skills/fixture-code-style/SKILL.md", "skills/fixture-code-style/references/assertions.md"):
        source = TARGET_DIR / "fixtures" / rel.removeprefix("memories/").removeprefix("skills/")
        destination = home / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    config = {
        "model": {"streaming": False, "context_length": profile["model"]["context_length"]},
        "agent": {"api_max_retries": 1, "environment_probe": False, "bot_mode_protocol": False, "verify_on_stop": False, "task_completion_guidance": True, "parallel_tool_call_guidance": True, "stall_guards": True},
        "terminal": {"backend": "local", "cwd": str(workspace), "timeout": 30},
        "memory": {"memory_enabled": True, "user_profile_enabled": True, "memory_char_limit": 2200, "user_char_limit": 1375, "provider": ""},
        "compression": {"enabled": profile["compaction"], "micro_compact": False},
        "approvals": {"mode": "manual", "single_query_mode": "deny", "unattended_mode": "deny"},
        "timeouts": {"api": {"call": 45}, "tools": {"call": 35, "concurrent_batch": 35}},
        "lsp": {"enabled": False},
    }
    (home / "config.yaml").write_text(json.dumps(config))
    os.environ.update(HOME=str(home), HERMES_HOME=str(home), HERMES_SKIP_MCP="1", HERMES_SINGLE_QUERY_SESSION="1", TERMINAL_ENV="local", TERMINAL_CWD=str(workspace), TERMINAL_TIMEOUT="30", HERMES_API_TIMEOUT="45", HERMES_TOOL_TIMEOUT="35", HERMES_CONCURRENT_TOOL_TIMEOUT_S="35", TZ="UTC", HERMES_TIMEZONE="UTC")
    import time
    time.tzset()
    os.chdir(workspace)


def _run_stock(workspace: Path, scratch: Path, base_url: str) -> dict[str, Any]:
    profile = target_profile()
    _stock_environment(workspace, scratch, profile)
    sys.path.insert(0, str(SOURCE))
    from run_agent import AIAgent
    import toolsets
    _pin_replay_identity()
    toolsets.create_custom_toolset("bb-hermes-native", "Bounded Hermes seven-tool profile", tools=list(profile["agent"]["tool_names"]), includes=[])
    agent = AIAgent(base_url=base_url, api_key="bb-hermes-native", provider="custom", api_mode="chat_completions", model=profile["model"]["model_name"], max_iterations=profile["agent"]["max_iterations"], enabled_toolsets=["bb-hermes-native"], disabled_toolsets=[], save_trajectories=False, verbose_logging=False, quiet_mode=True, max_tokens=profile["agent"]["max_output_tokens"], reasoning_config=None, request_overrides={}, platform="api_server", skip_context_files=False, load_soul_identity=False, skip_memory=False, skip_background_review=True, session_db=None, pass_session_id=False, checkpoints_enabled=False, fallback_model=None, credential_pool=None, capabilities={"streaming": False})
    # Both sides apply the target's already approved bounded read/terminal schema
    # overlay. Tool execution and the conversation loop remain stock.
    for tool in agent.tools:
        name = tool["function"]["name"]
        if name in profile["schema_overlay"]:
            tool.clear()
            tool.update(json.loads(profile["schema_overlay"][name]["approved_schema_json"]))
    # Stock re-discovers schemas after compaction. Its supported request override
    # keeps the target's bounded advertisement across that refresh, without
    # changing stock execution (agent/transports/chat_completions.py:440-441).
    agent.request_overrides["tools"] = json.loads(json.dumps(agent.tools))
    try:
        result = agent.run_conversation(PROMPT)
        return {"history": result["messages"], "terminal": result["final_response"], "completed": result["completed"]}
    finally:
        agent.close()


async def _run_bb(workspace: Path, scratch: Path, base_url: str) -> dict[str, Any]:
    from dataclasses import replace
    import httpx
    from breadboard.rl.harness.hermes_worker import HermesActor
    from breadboard.rl.harness.runners import conductor as c
    from breadboard.rl.harness.runners.base import RunnerDependencyError, thaw_json
    from breadboard.rl.harness.native_stream_profiles import HERMES_RESPONSE_CONSUMER_ID
    profile = target_profile()
    sys.path.insert(0, str(SOURCE))
    _pin_replay_identity()
    commands: queue.Queue[Any] = queue.Queue()
    responses: queue.Queue[Any] = queue.Queue()
    class Channel:
        def respond(self, value: Any) -> None:
            responses.put(value)
        def receive(self) -> Any:
            return commands.get(timeout=120)
    actor = HermesActor(Channel())
    def actor_loop() -> None:
        try:
            while True:
                command = commands.get(timeout=120)
                if command is None:
                    actor.close()
                    return
                responses.put(actor.dispatch(command["operation"], command["payload"]))
        except BaseException as exc:
            responses.put(exc)
    thread = threading.Thread(target=actor_loop, daemon=True)
    thread.start()
    class Port:
        tool_bindings = ()
        declared_workspace = str(workspace)
        initialized_payload: Any = None
        phases: list[Any] = []
        async def invoke_native_phase(self, operation: str, payload: Any, *, timeout_ms: int) -> Any:
            body = thaw_json(payload)
            if operation == "initialize":
                self.initialized_payload = body.copy()
                body.update(workspace=str(workspace), scratch=str(scratch))
            commands.put({"operation": operation, "payload": body})
            response = await asyncio.to_thread(responses.get, True, timeout_ms / 1000)
            if isinstance(response, BaseException):
                raise response
            self.phases.append(response)
            return response
        async def begin_native_workspace_effects(self) -> None:
            pass
        async def measure_workspace_effects(self) -> dict[str, Any]:
            return {}
        async def close_native_runtime(self) -> dict[str, Any]:
            commands.put(None)
            await asyncio.to_thread(thread.join, 30)
            if thread.is_alive():
                raise RuntimeError("Hermes actor did not close")
            if not responses.empty():
                pending = responses.get_nowait()
                if isinstance(pending, BaseException):
                    raise pending
            return {"kind": "closed", "cleanup": {"all_dead": True}}
    port = Port()
    class Binding:
        source_model_config = {"model_name": profile["model"]["model_name"], "model_canonical_name": None, "max_input_tokens": profile["model"]["context_length"], "base_url": base_url}
        def bind_native_tools(self, tools: Any) -> None:
            self.tools = tools
    class Session(c._ConductorSession):
        def __init__(self) -> None:
            self.requests: list[Any] = []
        def _context(self) -> dict[str, Any]:
            return {}
        async def _emit(self, event: Any) -> None:
            self._events.append(replace(event, sequence=len(self._events)))
        async def _checkpoint(self, *args: Any, **kwargs: Any) -> None:
            pass
        async def _commit_termination(self, termination: Any) -> None:
            await self._emit(c.RunnerTerminationEvent(
                0, self._open_request.episode_id, self._open_request.effective_plan_digest,
                len(self._turns), termination,
            ))
        async def _raise_error(self, error: BaseException, **kwargs: Any) -> None:
            raise error
        async def _invoke_native_policy(self, request: Any, *, model: Any, turn: int, verify_staged_body: bool = False, compaction_summary: bool = False) -> Any:
            self.requests.append({"purpose": "compaction_summary" if compaction_summary else "main", "body_b64": request["body_b64"]})
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(request["url"], content=base64.b64decode(request["body_b64"]), headers={"content-type": "application/json", "authorization": "Bearer bb-hermes-native"})
            return {"status_code": response.status_code, "headers": [["content-type", "application/json"]], "body_b64": base64.b64encode(response.content).decode()}
    session = Session()
    stream_profile = c.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    limits = SimpleNamespace(max_turns=stream_profile.max_turns, action_timeout_ms=stream_profile.action_timeout_ms, transcript_bytes=profile["bounds"]["transcript_bytes"])
    session._open_request = SimpleNamespace(episode_id="hermes-lane", effective_plan_digest="sha256:" + "a" * 64, effective_plan=SimpleNamespace(effective_capabilities=SimpleNamespace(limits=limits)))
    session._projection = SimpleNamespace(source_consumer_id=HERMES_RESPONSE_CONSUMER_ID, source_profile=profile, models=(SimpleNamespace(params={}, policy_slot_id="slot"),), modes=(SimpleNamespace(tool_ids=stream_profile.tool_order),))
    session._binding, session._tools = Binding(), port
    session._turns, session._events = [], []
    session._native_cleanup_outcome = c.NativeCleanupOutcome(False, None, None, None)
    terminal = ""
    try:
        result = await session._loop_native_stream(c.ConductorRunRequest({"prompt": PROMPT}), stream_profile)
        history = thaw_json(result.response["messages"])
        completed = True
    except RunnerDependencyError as exc:
        terminal = str(exc)
        history = actor._messages
        completed = False
    return {"history": history, "terminal": terminal, "completed": completed, "requests": session.requests, "initialize": port.initialized_payload, "summaries": [{key: phase[key] for key in ("stock_body_sha256", "compaction_summary_id", "compaction_summary_retry")} for phase in port.phases if phase.get("purpose") == "compaction_summary"]}


def run_side(side: str, scenario: str, workspace: Path, scratch: Path, base_url: str) -> dict[str, Any]:
    scratch.mkdir(parents=True)
    result_path = scratch / "result.json"
    command = [sys.executable, "-m", "scripts.compaction_lanes.hermes_lane", "--child", side, "--scenario", scenario, "--workspace", str(workspace), "--scratch", str(scratch), "--base-url", base_url, "--output", str(result_path)]
    process = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=150)
    if process.returncode:
        raise RuntimeError(f"{side} Hermes failed:\n{process.stdout}\n{process.stderr}")
    return json.loads(result_path.read_bytes())


def summary_deviations_valid(stock: dict[str, Any], bb: dict[str, Any], fields: list[str], main: dict[str, Any]) -> bool:
    """Admit only absent stock fields and the episode's exact tools/cap."""
    return all(
        field not in stock and field in bb and field in main
        and json.dumps(bb[field], sort_keys=True) == json.dumps(main[field], sort_keys=True)
        for field in fields
    )


def run_scenario(root: Path, scenario: str) -> dict[str, Any]:
    root = root.resolve()
    workspace = root / "workspace"
    seed_workspace(workspace, scenario)
    deviations = [entry["id"] for entry in __import__("yaml").safe_load((TARGET_DIR / "harness.yaml").read_bytes())["policy"]["deviations"]]
    deviation_fields = {"compaction_summary_episode_tools": "tools", "compaction_summary_max_tokens": "max_tokens"}
    fields = [deviation_fields[deviation] for deviation in deviations]
    records: dict[str, Any] = {}
    sides: dict[str, Any] = {}
    with MockProvider(script=scenario_script(scenario)) as provider:
        for side in ("stock", "bb"):
            provider.engine.set_script(scenario_script(scenario))
            provider.engine.recorded_requests.clear()
            # Use the same literal runtime paths, not path normalization in the
            # comparator: Hermes puts HOME and profile paths in its wire prompt.
            scratch = root / "runtime"
            sides[side] = run_side(side, scenario, workspace, scratch, provider.base_url + "/v1")
            import shutil
            shutil.rmtree(scratch)
            records[side] = [record for record in provider.engine.recorded_requests if record["path"] == "/v1/chat/completions"]
            if provider.engine.script:
                raise AssertionError(f"{side} did not consume the scenario: {len(provider.engine.script)} responses left")
    diffs = []
    compactions = []
    if len(records["stock"]) != len(records["bb"]):
        diffs.append({"kind": "request_count", "stock": len(records["stock"]), "bb": len(records["bb"])})
    summary_index = 0
    main_body = next(
        row["json"] for index, row in enumerate(records["bb"])
        if sides["bb"]["requests"][index]["purpose"] == "main"
    )
    for index, (stock, bb) in enumerate(zip(records["stock"], records["bb"])):
        purpose = sides["bb"]["requests"][index]["purpose"]
        if purpose == "compaction_summary":
            valid_deviations = summary_deviations_valid(stock["json"], bb["json"], fields, main_body)
            stock_body = {key: value for key, value in stock["json"].items() if key not in fields}
            bb_body = {key: value for key, value in bb["json"].items() if key not in fields}
            stock_sha = hashlib.sha256(bytes.fromhex(stock["raw_body_bytes"])).hexdigest()
            recorded_sha = sides["bb"]["summaries"][summary_index]["stock_body_sha256"]
            summary_index += 1
            compactions.append({"request_index": index, "stock_body_sha256": stock_sha, "recorded_stock_body_sha256": recorded_sha, "stock_omits_max_tokens": "max_tokens" not in stock["json"]})
            if stock_sha != recorded_sha:
                diffs.append({"kind": "stock_summary_hash", "index": index})
            equal = valid_deviations and stock_body == bb_body
        else:
            equal = stock["raw_body_bytes"] == bb["raw_body_bytes"]
        if not equal:
            diffs.append({"kind": "request_body", "index": index, "purpose": purpose, "stock": stock["json"], "bb": bb["json"]})
    return {"scenario": scenario, "target_id": TARGET_ID, "deviations": deviations, "requests": records, "compactions": compactions, "diffs": diffs, "stock": sides["stock"], "bb": sides["bb"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", choices=("stock", "bb"))
    parser.add_argument("--scenario", choices=("threshold", "overflow", "retry_turns"))
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--base-url")
    args = parser.parse_args()
    if args.child:
        result = _run_stock(args.workspace, args.scratch, args.base_url) if args.child == "stock" else asyncio.run(_run_bb(args.workspace, args.scratch, args.base_url))
    else:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="hermes-lane-") as temp:
            result = {scenario: run_scenario(Path(temp) / scenario, scenario) for scenario in ("threshold", "overflow", "retry_turns")}
    args.output.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
