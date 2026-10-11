"""Stock a7dff904 output contracts through BreadBoard's real child sessions."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.execution import codex_agents as native
from breadboard_engine.state.session_state import SessionState


@pytest.fixture
def owner(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    config = {
        "workspace": {"root": str(workspace), "mirror": {"enabled": False},
                      "sandbox": {"driver": "process"}},
        "logging": {"enabled": True, "root_dir": str(tmp_path / "logs")},
        "providers": {"default_model": "gpt-5.5", "models": [{
            "id": "gpt-5.5", "adapter": "openai",
            "tool_output_truncation": {"mode": "tokens", "limit": 10000},
            "supports_image_detail_original": True,
            "web_search_tool_type": "text_and_image",
        }]},
        "provider_tools": {"use_native": True, "suppress_prompts": True, "api_variant": "responses",
                           "openai": {"responses_stateful": False, "responses_message_type": True,
                                      "responses_tool_output_items": True,
                                      "responses_preserve_tool_call_items": True}},
        "tools": {"native_semantics": "codex_0_139", "registry": {
            "paths": ["implementations/tools/defs_codex_139"],
            "include": ["exec_command", "multi_agent_v1", "get_goal", "create_goal", "update_goal"],
        }},
        "loop": {"sequence": [{"mode": "build"}]},
        "modes": [{"name": "build", "prompt": "", "tools_enabled": ["*"], "tools_disabled": []}],
        "completion": {"provider_signals": True, "natural_finish": {
            "idle_turn_limit": 1, "no_tool_turns_threshold": 1}},
    }
    cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = cls(workspace=str(workspace), config=config, local_mode=True)
    conductor._active_session_state = SessionState(str(workspace), "image", config)
    conductor._active_session_state.set_provider_metadata("session_id", "11111111-1111-7111-8111-111111111111")
    conductor._current_route_id = "gpt-5.5"
    yield conductor
    registry = getattr(conductor, "_codex_agent_registry", None)
    if registry:
        for child in list(registry.children.values()):
            if not child.closed:
                call(conductor, "multi_agent_v1.close_agent", target=child.id)


def call(owner, name, **args):
    return native.execute_codex_agent_tool(owner, name, args)["__mvi_text_output"]


def value(owner, name, **args):
    return json.loads(call(owner, name, **args))


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("multi_agent_v1.spawn_agent", {}, "Provide one of: message or items"),
    ("multi_agent_v1.spawn_agent", {"message": " ", "items": []}, "Provide either message or items, but not both"),
    ("multi_agent_v1.spawn_agent", {"message": " "}, "Empty message can't be sent to an agent"),
    ("multi_agent_v1.spawn_agent", {"items": []}, "Items can't be empty"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "agent_type": "absent"}, "unknown agent_type 'absent'"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "agent_type": "awaiter"}, "unknown agent_type 'awaiter'"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "model": "", "agent_type": "absent"},
     "Unknown model `` for spawn_agent. Available models: gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.3-codex, gpt-5.2"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "reasoning_effort": "none"},
     "Reasoning effort `none` is not supported for model `gpt-5.5`. Supported reasoning efforts: low, medium, high, xhigh"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "service_tier": "flex"},
     "Service tier `flex` is not supported for model `gpt-5.5`. Supported service tiers: priority"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "model": "gpt-5.4-mini", "service_tier": "priority"},
     "Service tier `priority` is not supported for model `gpt-5.4-mini`. Supported service tiers: none"),
    ("multi_agent_v1.spawn_agent", {"message": "x", "fork_context": True, "model": "gpt-5.5"},
     "Full-history forked agents inherit the parent agent type, model, and reasoning effort; omit agent_type, model, and reasoning_effort, or spawn without a full-history fork."),
    ("multi_agent_v1.wait_agent", {}, "agent ids must be non-empty"),
    ("multi_agent_v1.wait_agent", {"targets": ["11111111-1111-7111-8111-111111111111"], "timeout_ms": 0},
     "timeout_ms must be greater than zero"),
    ("create_goal", {"objective": " "}, "goal objective must not be empty"),
    ("create_goal", {"objective": "x" * 4001}, "goal objective must be at most 4000 characters"),
    ("create_goal", {"objective": "x", "token_budget": 0}, "goal budgets must be positive when provided"),
    ("update_goal", {"status": "complete"}, "cannot update goal because this thread has no goal"),
    ("update_goal", {"status": "active"},
     "update_goal can only mark the existing goal complete or blocked; pause, resume, budget-limited, and usage-limited status changes are controlled by the user or system"),
])
def test_stock_errors(owner, name, args, expected):
    # multi_agents_common.rs:163-195,237-247; goal/tool.rs:175-197,213-220,255-259.
    out = native.execute_codex_agent_tool(owner, name, args)
    assert out == {"error": expected, "__mvi_text_output": expected}


@pytest.mark.parametrize("name,key", [
    ("send_input", "target"), ("close_agent", "target"), ("resume_agent", "id"),
])
def test_unknown_ids_match_stock(owner, name, key):
    id = "aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    args = {key: id, "message": "x"}
    assert call(owner, f"multi_agent_v1.{name}", **args) == f"agent with id {id} not found"
    assert value(owner, "multi_agent_v1.wait_agent", targets=[id]) == {
        "status": {id: "not_found"}, "timed_out": False}


def test_depth_limit_matches_stock(owner):
    owner._codex_agent_depth = 1
    assert call(owner, "multi_agent_v1.spawn_agent", message="x") == "Agent depth limit reached. Solve the task yourself."
    assert call(owner, "multi_agent_v1.resume_agent", id=str(uuid.uuid4())) == "Agent depth limit reached. Solve the task yourself."


def test_goal_output_persistence_and_completion(owner, monkeypatch):
    monkeypatch.setattr(native.time, "time", lambda: 1000)
    assert call(owner, "get_goal") == '{"completionBudgetReport":null,"goal":null,"remainingTokens":null}'
    created = value(owner, "create_goal", objective="  ship it  ", token_budget=100)
    assert created == {
        "goal": {"threadId": "11111111-1111-7111-8111-111111111111", "objective": "ship it", "status": "active",
                 "tokenBudget": 100, "tokensUsed": 0, "timeUsedSeconds": 0, "createdAt": 1000, "updatedAt": 1000},
        "remainingTokens": 100, "completionBudgetReport": None}
    assert value(owner, "get_goal") == created
    assert call(owner, "create_goal", objective="another") == (
        "cannot create a new goal because this thread already has a goal; use update_goal only when the existing goal is complete")
    owner._active_session_state.set_provider_metadata("usage_totals", {"input_tokens": 20, "output_tokens": 3})
    native.sync_codex_goal(owner, owner._active_session_state)
    completed = value(owner, "update_goal", status="complete")
    assert completed["goal"]["tokensUsed"] == 23
    assert completed["goal"]["status"] == "complete"
    assert completed["remainingTokens"] == 77
    assert completed["completionBudgetReport"] == native.COMPLETION_BUDGET_REPORT
    # Fresh conductor over the same admitted session reads durable state.
    cls = OpenAIConductor.__ray_metadata__.modified_class
    resumed = cls(workspace=owner.workspace, config=deepcopy(owner.config), local_mode=True,
                  admitted_workspace=Path(owner.workspace))
    resumed._active_session_state = owner._active_session_state
    assert value(resumed, "get_goal")["goal"] == completed["goal"]
    assert value(resumed, "get_goal")["completionBudgetReport"] is None


def test_goal_without_budget_and_blocked_budget_limit(owner):
    goal = value(owner, "create_goal", objective="x")
    assert "tokenBudget" not in goal["goal"]
    assert goal["remainingTokens"] is None
    blocked = value(owner, "update_goal", status="blocked")
    assert blocked["goal"]["status"] == "blocked"
    assert blocked["completionBudgetReport"] is None


def test_goal_budget_accounting_does_not_double_charge(owner):
    value(owner, "create_goal", objective="x", token_budget=10)
    state = owner._active_session_state
    state.set_provider_metadata("usage_totals", {"input_tokens": 9, "output_tokens": 3})
    native.sync_codex_goal(owner, state)
    native.sync_codex_goal(owner, state)
    result = value(owner, "update_goal", status="blocked")
    assert result["goal"]["status"] == "budgetLimited"
    assert result["goal"]["tokensUsed"] == 12
    assert result["remainingTokens"] == 0


def test_goal_clock_counts_whole_seconds(owner, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(native.time, "monotonic", lambda: clock[0])
    value(owner, "create_goal", objective="x")
    clock[0] = 102.75
    native.sync_codex_goal(owner, owner._active_session_state)
    clock[0] = 103.1
    native.sync_codex_goal(owner, owner._active_session_state)
    assert value(owner, "get_goal")["goal"]["timeUsedSeconds"] == 3


def replay(owner, tmp_path):
    path = tmp_path / "child-replay.json"
    path.write_text(json.dumps([
        {"role": "user", "message_id": "u0", "parts": [{"id": "p0", "type": "text", "text": "Run child"}]},
        {"role": "assistant", "message_id": "a1", "parts": [
            {"id": "p1", "type": "text", "text": "child finished"}]},
    ]))
    owner.config["replay"] = {"session_path": str(path)}


@pytest.fixture
def child_execution_provider(owner, tmp_path, monkeypatch):
    from scripts.compaction_lanes.codex_lane import EngineMockProvider

    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "exec_command", "call_id": "child-exec",
         "arguments": json.dumps({
             "cmd": "printf child-ran > child-evidence.txt",
             "workdir": owner.workspace, "yield_time_ms": 1000,
         }), "usage": usage},
        *({"type": "text", "text": "child finished", "usage": usage} for _ in range(3)),
    ]
    with EngineMockProvider(script=script, record_path=tmp_path / "child-requests.jsonl") as provider:
        monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
        yield provider


def test_real_child_runs_inherited_tools_and_reuses_history(owner, child_execution_provider):
    spawned = value(owner, "multi_agent_v1.spawn_agent", message="Do child work")
    id = spawned["agent_id"]
    assert uuid.UUID(id).version == 7
    assert spawned["nickname"] in native.AGENT_NAMES
    result = value(owner, "multi_agent_v1.wait_agent", targets=[id])
    assert result == {"status": {id: {"completed": "child finished"}}, "timed_out": False}
    assert (Path(owner.workspace) / "child-evidence.txt").read_text() == "child-ran"
    child = owner._codex_agent_registry.children[id]
    assert type(child.conductor) is type(owner)
    assert child.conductor._codex_agent_id == id
    assert child.route == owner._current_route_id
    assert child.config["tools"] == owner.config["tools"]
    assert child.config["providers"] == owner.config["providers"]
    original_count = len(child.messages)
    sent = value(owner, "multi_agent_v1.send_input", target=id, items=[{"type": "text", "text": "Again"}])
    assert list(sent) == ["submission_id"]
    value(owner, "multi_agent_v1.wait_agent", targets=[id])
    with owner._codex_agent_registry.condition:
        owner._codex_agent_registry.condition.wait_for(lambda: child.worker is None, timeout=10)
    assert len(child.messages) > original_count
    assert value(owner, "multi_agent_v1.close_agent", target=id) == {
        "previous_status": {"completed": "child finished"}}
    assert value(owner, "multi_agent_v1.wait_agent", targets=[id]) == {
        "status": {id: "not_found"}, "timed_out": False}
    assert value(owner, "multi_agent_v1.resume_agent", id=id) == {"status": "pending_init"}
    value(owner, "multi_agent_v1.send_input", target=id, message="after resume")
    assert value(owner, "multi_agent_v1.wait_agent", targets=[id])["status"][id] == {"completed": "child finished"}



@pytest.mark.parametrize("model", ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"])
def test_real_child_accepts_stock_model_and_effort_overrides(owner, child_execution_provider, model):
    owner.config["provider_tools"]["openai"]["request_params"] = {"service_tier": "priority"}
    id = value(owner, "multi_agent_v1.spawn_agent", message="Do child work",
               agent_type="explorer", model=model, reasoning_effort="low")["agent_id"]
    assert value(owner, "multi_agent_v1.wait_agent", targets=[id]) == {
        "status": {id: {"completed": "child finished"}}, "timed_out": False}
    assert (Path(owner.workspace) / "child-evidence.txt").read_text() == "child-ran"
    assert owner._codex_agent_registry.children[id].route == model
    selected = next(row for row in owner._codex_agent_registry.children[id].config["providers"]["models"]
                    if row["id"] == model)
    assert selected["supports_image_detail_original"] is (model != "gpt-5.2")
    assert selected["tool_output_truncation"] == {
        "mode": "bytes" if model == "gpt-5.2" else "tokens", "limit": 10000}
    assert selected["web_search_tool_type"] == (
        "text" if model in ("gpt-5.3-codex", "gpt-5.2") else "text_and_image")
    requests = [entry["json"] for entry in child_execution_provider.engine.recorded_requests]
    assert all(body["model"] == model and body["reasoning"]["effort"] == "low" for body in requests)
    assert all(body.get("service_tier") == ("priority" if model in ("gpt-5.5", "gpt-5.4") else None)
               for body in requests)


def test_full_history_fork_accepts_empty_role(owner, child_execution_provider):
    id = value(owner, "multi_agent_v1.spawn_agent", message="Do child work",
               fork_context=True, agent_type="   ")["agent_id"]
    assert value(owner, "multi_agent_v1.wait_agent", targets=[id])["status"][id] == {
        "completed": "child finished"}
    assert (Path(owner.workspace) / "child-evidence.txt").read_text() == "child-ran"

def test_completed_agents_count_until_closed(owner, tmp_path):
    replay(owner, tmp_path)
    owner.config["agents"] = {"max_threads": 1}
    id = value(owner, "multi_agent_v1.spawn_agent", message="x")["agent_id"]
    value(owner, "multi_agent_v1.wait_agent", targets=[id])
    assert call(owner, "multi_agent_v1.spawn_agent", message="x") == "collab spawn failed: agent thread limit reached"
    value(owner, "multi_agent_v1.close_agent", target=id)
    assert "agent_id" in value(owner, "multi_agent_v1.spawn_agent", message="x")


def test_wait_returns_first_final_and_clamps_timeout(owner, monkeypatch):
    registry = native._registry(owner)
    # Real wait over registry state, with a deterministic clock rather than a 10s sleep.
    id = str(uuid.uuid4())
    child = native.Child(id, "parent", 1, None, {}, "", owner, status="running")
    registry.children[id] = child
    times = iter([0.0, 11.0])
    monkeypatch.setattr(native.time, "monotonic", lambda: next(times))
    assert call(owner, "multi_agent_v1.wait_agent", targets=[id], timeout_ms=1) == '{"status":{},"timed_out":true}'
    child.closed = True


def test_non_codex_goal_hooks_are_inert(owner):
    owner.config["tools"].pop("native_semantics")
    native.begin_codex_goal(owner, owner._active_session_state)
    native.sync_codex_goal(owner, owner._active_session_state)
    assert not hasattr(owner, "_codex_goal_baseline")
    assert not (Path(owner.workspace) / ".breadboard/codex/goals").exists()


@pytest.mark.parametrize("kind,url,detail", [
    ("image", "https://example.invalid/image.png?x=1%202", "high"),
    ("image", "data:image/png;base64,abc", "high"),
    ("local_image", None, "low"),
    ("local_image", None, "auto"),
])
def test_child_image_request_matches_stock_url_bytes(owner, tmp_path, monkeypatch, kind, url, detail):
    from scripts.compaction_lanes.codex_lane import BINARY, EngineMockProvider, run_stock
    from scripts.compaction_lanes import codex_lane

    class WaitForChildHandler(codex_lane.EngineResponsesHandler):
        def _handle_openai_responses(self, item, request_json):
            outputs = {entry["call_id"]: entry["output"] for entry in request_json.get("input", [])
                       if entry.get("type") == "function_call_output"}
            if "wait-image" in outputs:
                item = {"type": "text", "text": "parent finished", "usage": usage}
            elif "spawn-image" in outputs:
                agent_id = json.loads(outputs["spawn-image"])["agent_id"]
                item = {
                    "type": "tool_call", "name": "wait_agent", "namespace": "multi_agent_v1",
                    "call_id": "wait-image", "arguments": json.dumps({"targets": [agent_id]}),
                    "usage": usage,
                }
            elif any(block.get("type") == "input_image" for entry in request_json.get("input", [])
                     for block in entry.get("content", []) if isinstance(block, dict)):
                item = {"type": "text", "text": "child finished", "usage": usage}
            super()._handle_openai_responses(item, request_json)

    if not BINARY.is_file():
        pytest.skip("pinned Codex 0.139.0 binary not installed")
    if kind == "local_image":
        from PIL import Image

        path = Path(owner.workspace) / "agent-input.png"
        Image.new("RGB", (8, 6), (23, 41, 67)).save(path)
        image = {"type": kind, "path": str(path), "detail": detail}
    else:
        image = {"type": kind, "image_url": url, "detail": detail}
    items = [{"type": "text", "text": "before"}, image, {"type": "text", "text": "after"}]
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "tool_call", "name": "spawn_agent", "namespace": "multi_agent_v1", "call_id": "spawn-image",
         "arguments": json.dumps({"items": items}), "usage": usage},
        *({"type": "text", "text": "child finished", "usage": usage} for _ in range(4)),
    ]
    stock_scratch = tmp_path / "stock"
    stock_scratch.mkdir()
    # Stock shuts children down when its root exits. Ask its real wait_agent
    # to join the spawn instead of racing a root final answer against image load.
    with monkeypatch.context() as stock_patch:
        stock_patch.setattr(codex_lane, "EngineResponsesHandler", WaitForChildHandler)
        with EngineMockProvider(script=script, record_path=stock_scratch / "requests.jsonl") as provider:
            stock = run_stock("mid_turn_compaction", Path(owner.workspace), stock_scratch,
                              provider.base_url, {"model": "gpt-5.5", "window": 272000, "limit": 244800})
            stock_requests = list(provider.engine.recorded_requests)
    assert stock["runs"][0]["returncode"] == 0, stock
    expected = next(message for request in stock_requests for message in request["json"].get("input", [])
                    if message.get("role") == "user" and any(
                        block.get("type") == "input_image" for block in message.get("content", [])
                        if isinstance(block, dict)))
    with EngineMockProvider(script=[{"type": "text", "text": "child finished", "usage": usage}],
                            record_path=tmp_path / "bb-requests.jsonl") as provider:
        monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
        id = value(owner, "multi_agent_v1.spawn_agent", items=items)["agent_id"]
        result = value(owner, "multi_agent_v1.wait_agent", targets=[id])
        assert result["status"][id] == {"completed": "child finished"}, result
        actual = next(message for request in provider.engine.recorded_requests
                      for message in request["json"].get("input", [])
                      if message.get("role") == "user" and any(
                          block.get("type") == "input_image" for block in message.get("content", [])
                          if isinstance(block, dict)))
    assert json.dumps(actual, sort_keys=True, separators=(",", ":")).encode() == (
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode())


def test_image_passthrough_requires_native_profile(owner):
    from breadboard_engine.provider.contracts import ProviderContractError, ProviderRuntimeContext
    from breadboard_engine.provider.input_media import resolve_input_media

    state = owner._active_session_state
    state.config["tools"].pop("native_semantics")
    context = ProviderRuntimeContext(agent_config=state.config, session_state=state)
    with pytest.raises(ProviderContractError, match="requires the native Codex profile"):
        resolve_input_media({"type": "media", "kind": "codex_image_url",
                             "uri": "https://example.invalid/no-fetch", "detail": "high"}, context)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("phase_enabled", [False, True])
def test_real_final_answer_phase_survives_fork(owner, tmp_path, monkeypatch, stream, phase_enabled):
    from scripts.compaction_lanes.codex_lane import EngineMockProvider

    if phase_enabled:
        owner.config["provider_tools"]["openai"]["responses_message_phase"] = True
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = [
        {"type": "text", "text": "parent answer", "phase": "final_answer", "usage": usage},
        {"type": "text", "text": "child answer", "phase": "final_answer", "usage": usage},
    ]
    captured = []
    original = owner._run_main_loop

    def capture_state(*args, **kwargs):
        captured.append(owner._active_session_state)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, "_run_main_loop", capture_state)
    with EngineMockProvider(script=script, record_path=tmp_path / "fork-requests.jsonl") as provider:
        monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
        result = owner.run_agentic_loop(
            "", "Answer", "gpt-5.5", max_steps=3, stream_responses=stream,
            tool_prompt_mode="system_once", completion_sentinel=None,
            completion_config=owner.config["completion"],
            context={
                "session_id": "11111111-1111-7111-8111-111111111111",
                "input_id": "22222222-2222-7222-8222-222222222222",
                "turn_id": "33333333-3333-7333-8333-333333333333",
            },
        )
        assert not result.get("error"), result
        # Preserve the actual admitted parent state after its invocation releases
        # the active pointer; do not synthesize provider history.
        owner._active_session_state = captured[0]
        parent_answers = [m for m in captured[0].provider_messages
                          if m.get("role") == "assistant" and m.get("content") == "parent answer"]
        assert len(parent_answers) == 1
        assert parent_answers[0].get("phase") == ("final_answer" if phase_enabled else None)
        id = value(owner, "multi_agent_v1.spawn_agent", message="Continue", fork_context=True)["agent_id"]
        assert value(owner, "multi_agent_v1.wait_agent", targets=[id])["status"][id] == {
            "completed": "child answer"}
        child_request = provider.engine.recorded_requests[-1]["json"]
        inherited = [m for m in child_request["input"] if m.get("role") == "assistant"]
        assert bool(inherited) is phase_enabled
        if phase_enabled:
            assert inherited[0]["phase"] == "final_answer"
            assert inherited[0]["content"] == [{"type": "output_text", "text": "parent answer"}]
            assert owner._codex_agent_registry.children[id].messages[-1]["phase"] == "final_answer"


def test_real_background_processes_are_thread_scoped_and_close_recursively(owner, tmp_path, monkeypatch):
    import re
    import time

    from breadboard_engine.execution.codex_exec import execute_codex_tool
    from scripts.compaction_lanes.codex_lane import EngineMockProvider

    owner.config["agents"] = {"max_depth": 2}
    owner.config["tools"]["registry"]["include"].append("write_stdin")
    root_id = owner._active_session_state.get_provider_metadata("session_id")
    root = execute_codex_tool(owner, "exec_command", {
        "cmd": "printf root-start; sleep 120", "workdir": owner.workspace, "yield_time_ms": 1000,
    })
    root_pid = int(re.search(r"Process running with session ID (\d+)", root["__mvi_text_output"])[1])
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script = []
    for call_id, filename in (("child-bg", "child-heartbeat.txt"), ("descendant-bg", "descendant-heartbeat.txt")):
        script.extend([
            {"type": "tool_call", "name": "exec_command", "call_id": call_id,
             "arguments": json.dumps({
                 "cmd": f"while :; do printf x >> {filename}; sleep 0.05; done",
                 "workdir": owner.workspace, "yield_time_ms": 1000,
             }), "usage": usage},
            {"type": "text", "text": "background started", "usage": usage},
        ])
    try:
        with EngineMockProvider(script=script, record_path=tmp_path / "background-requests.jsonl") as provider:
            monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url + "/v1")
            monkeypatch.setenv("OPENAI_API_KEY", "lane-key")
            id = value(owner, "multi_agent_v1.spawn_agent", message="Start background")["agent_id"]
            assert value(owner, "multi_agent_v1.wait_agent", targets=[id])["status"][id] == {
                "completed": "background started"}
            child = owner._codex_agent_registry.children[id]
            descendant_id = value(child.conductor, "multi_agent_v1.spawn_agent", message="Start descendant")["agent_id"]
            assert value(owner, "multi_agent_v1.wait_agent", targets=[descendant_id])["status"][descendant_id] == {
                "completed": "background started"}
            outputs = {item["call_id"]: item["output"]
                       for request in provider.engine.recorded_requests for item in request["json"]["input"]
                       if item.get("type") == "function_call_output"}
            child_pid = int(re.search(r"Process running with session ID (\d+)", outputs["child-bg"])[1])
            descendant_pid = int(re.search(r"Process running with session ID (\d+)", outputs["descendant-bg"])[1])
            assert execute_codex_tool(owner, "write_stdin", {
                "session_id": child_pid, "chars": "", "yield_time_ms": 0,
            })["__mvi_text_output"] == f"write_stdin failed: Unknown process id {child_pid}"
            assert execute_codex_tool(child.conductor, "write_stdin", {
                "session_id": root_pid, "chars": "", "yield_time_ms": 0,
            })["__mvi_text_output"] == f"write_stdin failed: Unknown process id {root_pid}"
            assert value(owner, "multi_agent_v1.close_agent", target=id) == {
                "previous_status": {"completed": "background started"}}
            descendant = owner._codex_agent_registry.children[descendant_id]
            assert child.status == descendant.status == "shutdown"
            for thread, pid in ((child, child_pid), (descendant, descendant_pid)):
                assert execute_codex_tool(thread.conductor, "write_stdin", {
                    "session_id": pid, "chars": "", "yield_time_ms": 0,
                })["__mvi_text_output"] == f"write_stdin failed: Unknown process id {pid}"
            paths = [Path(owner.workspace) / name for name in ("child-heartbeat.txt", "descendant-heartbeat.txt")]
            sizes = [path.stat().st_size for path in paths]
            assert all(size > 0 for size in sizes)
            time.sleep(0.15)
            assert [path.stat().st_size for path in paths] == sizes
            assert f"Process running with session ID {root_pid}" in execute_codex_tool(owner, "write_stdin", {
                "session_id": root_pid, "chars": "", "yield_time_ms": 0,
            })["__mvi_text_output"]
    finally:
        owner._ray_get(owner.sandbox.codex_close_exec_sessions.remote(root_id))