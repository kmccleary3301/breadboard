from tests.compaction.test_opencode_stock_tools import omo_dependencies
import json
from pathlib import Path
from breadboard_engine.execution.opencode.session import OpenCodeToolSession
from breadboard_engine.provider.contract_messages import ProviderResult
from tests.compaction.test_opencode_stock_tools import MODEL
from tests.compaction.test_opencode_stock_tools import binary
from tests.compaction.test_opencode_stock_tools import plain
from tests.compaction.test_opencode_stock_tools import isolated_environment
from tests.compaction.test_opencode_stock_tools import response
from tests.compaction.test_opencode_stock_tools import hermetic_stock_config
import pytest
from breadboard_engine.execution.opencode.wire import StockExecutionError
import copy
import os
import subprocess
import threading
from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
from tests.compaction.test_opencode_stock_tools import connect_auxiliary
from tests.compaction.test_opencode_stock_tools import raw_descriptor
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN_DIGEST
from tests.compaction.test_opencode_stock_omo_groups import LSP_PIN
import base64
import time
from concurrent.futures import ThreadPoolExecutor
from breadboard_engine.agent import AgenticCoder
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.compaction_lanes.opencode_recording import SessionScriptEngine
from breadboard_engine.compaction_lanes.opencode_scenarios import production_settings
from breadboard_engine.compaction_lanes.opencode_scenarios import responses_sse
from breadboard_engine.compaction_lanes.opencode_stock import stock_server
import asyncio
import urllib.request
import yaml
from breadboard_engine.api.cli_bridge.events import EventType
from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionInputRequest
from breadboard_engine.api.cli_bridge.registry import SessionRegistry
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.compaction_lanes.opencode_scenarios import SOURCE_API_USERNAME
from breadboard_engine.compaction_lanes.opencode_scenarios import SOURCE_API_PASSWORD
from breadboard_engine.compaction_lanes.opencode_scenarios import tool_response

"""Real stock primary transitions, summary metadata, and user-part projection."""



def test_stock_primary_agent_can_change_on_same_root(plain):
    first = plain.user([{"type": "text", "text": "Answer as build."}], agent="build")
    plain.submit(response(plain, text="Build answer."))
    second = plain.user([{"type": "text", "text": "Answer as plan."}], agent="plan")
    assert plain.root_agent == "plan"
    assert first["model"] == second["model"]
    plain.submit(response(plain, text="Plan answer."))


def test_genuine_bb_summary_response_retains_stock_usage_and_reasoning(plain):
    plain.user([{"type": "text", "text": "Inspect then compact."}], agent="build")
    payload = response(plain, [("call_summary", "bash", {
        "command": "printf genuine-summary", "description": "Inspect before summary",
    })])
    payload["usage"] = {"input_tokens": 390000, "output_tokens": 1}
    plain.submit(payload)
    summary = response(plain, text="Genuine BB summary.\n")
    summary["usage"] = {"input_tokens": 42, "output_tokens": 7,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 3}}
    summary["output"].insert(0, {"type": "reasoning", "id": "rs_summary",
                                 "summary": [{"type": "summary_text", "text": "Summary reasoning."}]})
    result = ProviderResult(messages=[], raw_response=summary, usage=summary["usage"], model=MODEL)
    assert plain.set_compaction_response(result) == "Genuine BB summary.\n"
    plain.compact("Genuine BB summary.\n")
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    native = next(message for message in messages
                  if message["info"]["role"] == "assistant" and message["info"].get("summary"))
    assert native["info"]["tokens"]["input"] == 42
    assert native["info"]["tokens"]["reasoning"] == 3
    assert any(part.get("text") == "Summary reasoning." for part in native["parts"])
    # Source processor.ts:321 trimEnd runs before the stock text-complete hook.
    assert any(part.get("text") == "Genuine BB summary." for part in native["parts"])
    plain.submit(response(plain))


def test_source_hook_added_text_preserves_authorized_media_slots(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin = workspace / "user-hook.mjs"
    plugin.write_text('''export const UserHook = async () => ({
      "chat.message": async (_input, output) => {
        const first = output.parts[0];
        output.parts.unshift({...first, id: first.id + "prefix", text: "Native added prefix."});
        output.parts.push({...first, id: first.id + "suffix", text: "Native added suffix."});
      }
    });''')
    image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII="
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60, stock_config=hermetic_stock_config({"plugin": [plugin.as_uri()], "agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "stock"),)
    try:
        session.user([{"type": "text", "text": "Original authorized text."},
                      {"type": "file", "mime": "image/png", "url": image}], agent="build")
        sequence = session.latest_user_sequence()
        # Stock persists/sorts by part ID, not the plugin's temporary array order.
        assert sequence == [{"type": "text", "text": "Original authorized text."},
                            {"type": "text", "text": "Native added prefix."},
                            {"type": "text", "text": "Native added suffix."},
                            {"type": "stock_media", "index": 0}]
        assert image not in json.dumps(sequence)
        session.submit(response(session))
    finally:
        session.close()


def test_root_prompt_lifetime_does_not_use_operation_deadline(plain):
    import time
    plain.timeout = 1
    plain.user([{"type": "text", "text": "Wait for BB's model response."}])
    time.sleep(1.2)
    plain.check_alive()
    plain.submit(response(plain, text="The long-lived root request survived."))


def test_headless_finish_and_operator_stop_wait_for_one_genuine_native_close(plain, tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from types import SimpleNamespace

    from breadboard_engine.execution.stock_tools import register_stock_session, close_stock_sessions
    from tests.compaction.test_opencode_stock_interaction import runner_at

    plain.headless = True
    plain.user([{"type": "text", "text": "Complete before operator shutdown."}])
    plain.submit(response(plain, text="The real native root is idle."))
    assert plain.headless_idle_seen
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    aborts = []
    original_api = plain.api
    def paused_native_abort(method, path, *args, **kwargs):
        if method == "POST" and path == f"/session/{plain.session_id}/abort":
            aborts.append(path)
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Genuine native shutdown barrier was not released")
        return original_api(method, path, *args, **kwargs)
    plain.api = paused_native_abort
    owner = SimpleNamespace()
    register_stock_session(owner, plain)
    runner = runner_at(tmp_path / "operator")
    asyncio.run(runner.registry.create(runner.session))
    def close_agent():
        stopped.set()
        close_stock_sessions(owner)
    runner._agent = SimpleNamespace(close=close_agent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(plain.finish_run)
        assert entered.wait(10)
        operator = executor.submit(lambda: asyncio.run(runner.stop()))
        try:
            assert stopped.wait(10)
            assert not operator.done(), "Operator stop must await the worker-owned whole teardown"
        finally:
            release.set()
        worker.result(timeout=15)
        operator.result(timeout=15)
    assert len(aborts) == 1
    assert plain.closed and plain.process.poll() is not None
    assert not Path(plain.scratch.name).exists()
    assert owner._stock_tool_sessions == []


def test_native_process_lookup_race_still_disposes_proxy_and_scratch(plain):
    original_terminate = plain.process.terminate
    def exited_during_terminate():
        original_terminate()
        raise ProcessLookupError("Native process exited at the termination boundary")
    plain.process.terminate = exited_during_terminate
    plain.close()
    assert plain.closed and plain.process.poll() is not None
    assert not Path(plain.scratch.name).exists()
    assert plain.proxy.socket.fileno() == -1


"""Lane-only pre-run fixtures use the unmodified stock import command."""



def test_native_empty_root_and_child_import_under_same_isolation(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    options = dict(binary=binary, timeout=60, environment=isolated_environment(tmp_path / "home"))
    seed = OpenCodeToolSession(str(workspace), MODEL, **options)
    try:
        root = seed.api("GET", "/session/" + seed.session_id)
        child = seed.api("POST", "/session", {"parentID": seed.session_id})
        exports = [{"info": info, "messages": seed.api("GET", "/session/" + info["id"] + "/message")}
                   for info in (root, child)]
        assert all(item["messages"] == [] for item in exports)
    finally:
        seed.close()
    resumed = OpenCodeToolSession(str(workspace), MODEL, session_imports=exports,
                                 imported_session_id=root["id"], **options)
    try:
        assert resumed.session_id == root["id"]
        children = resumed.api("GET", "/session/" + root["id"] + "/children")
        assert [info["id"] for info in children] == [child["id"]]
        assert resumed.api("GET", "/session/" + root["id"] + "/message") == []
    finally:
        resumed.close()
    exports[0]["messages"] = [{"info": {"role": "assistant"}}]
    with pytest.raises(StockExecutionError, match="pre-run empty"):
        OpenCodeToolSession(str(workspace), MODEL, session_imports=exports,
                            imported_session_id=root["id"], **options)




def test_reopened_sidecar_retains_native_session_and_tool_history(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = workspace / ".breadboard/sessions/bb-resume/stock-opencode"
    options = dict(binary=binary, timeout=60, data_root=data,
                   environment=isolated_environment(tmp_path / "environment"),
                   stock_config={"agent": {"title": {"disable": True}}})
    with_first = OpenCodeToolSession(str(workspace), MODEL, **options)
    try:
        with_first.user([{"type": "text", "text": "Remember native history."}])
        with_first.submit(response(with_first, [("call_remember", "bash", {
            "command": "printf retained-result", "description": "Retain a tool result",
        })]))
        assert with_first.result("call_remember")["__mvi_text_output"] == "retained-result"
        with_first.submit(response(with_first))
        identity = with_first.session_id
    finally:
        with_first.close()
    reopened = OpenCodeToolSession(str(workspace), MODEL, **options)
    try:
        assert reopened.session_id == identity
        history = reopened.api("GET", "/session/" + identity + "/message")
        assert any(part.get("state", {}).get("output") == "retained-result"
                   for message in history for part in message["parts"])
        held = reopened.user([{"type": "text", "text": "Continue the same session."}])
        assert any(item.get("call_id") == "call_remember" and item.get("output") == "retained-result"
                   for item in held["input"])
        reopened.submit(response(reopened))
    finally:
        reopened.close()


"""The dated headless sidecar exits at the same root idle as stock `run`."""



@pytest.mark.usefixtures("omo_dependencies")
def test_headless_first_root_idle_disposes_pending_child_like_stock_run(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("real child input\n")
    config = {"permission": {"read": "ask"}, "lsp": False,
              "agent": {"title": {"disable": True},
                        "explore": {"model": MODEL, "permission": {"read": "ask"}}}}
    # Keep remote discovery out of this lifecycle proof on both real stock
    # transports (OMO mcp-config-handler.ts:8-25,48-51). No hook is disabled.
    config["mcp"] = {name: {"enabled": False} for name in ("websearch", "context7", "grep_app")}
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=30, lsp_root=LSP_PIN, headless=True, stock_config=hermetic_stock_config(config), read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
    environment=isolated_environment(tmp_path / "sidecar"))
    empty = {"info": session.api("GET", f"/session/{session.session_id}"), "messages": []}
    prompt = "Start a background reader and finish the parent."
    task = ("call_headless_bg", "task", {"subagent_type": "explore", "load_skills": [],
        "description": "Read sample", "prompt": "Read sample.txt.", "run_in_background": True})
    child = raw_descriptor(response(session, [("call_headless_child", "read", {
        "filePath": str(workspace / "sample.txt")})]))
    notices = []
    child_held = threading.Event()
    original_interaction = session._interaction
    def observe_interaction(kind, properties, state):
        original_interaction(kind, properties, state)
        if properties["sessionID"] != session.session_id:
            child_held.set()
    session._interaction = observe_interaction
    session.stock_input_handler = notices.append
    try:
        with ResponsesProvider() as provider:
            provider.engine.set_child_script(session.session_id, [child])
            connect_auxiliary(session, provider)
            initial = session.user([{"type": "text", "text": prompt + "\n"}])
            session.submit(response(session, [task]))
            assert child_held.wait(15)
            assert session.api("GET", "/permission"), "Stock child must really be waiting, not mocked"
            finish = response(session, text="The headless parent is complete.")
            session.submit(finish)
            assert session.headless_idle_seen
            session.finish_run()
            assert session.closed and session.process.poll() is not None
            assert notices == []
            assert len([row for row in session.records if row["kind"] == "main"]) == 2

        # Stock run.ts:531-537 stops its event loop at root idle, while 640-646
        # awaits that root prompt. bootstrap disposes the instance, not a daemon
        # that waits for the background child. Exercise the actual pinned CLI.
        oracle = tmp_path / "oracle"
        oracle.mkdir()
        fixture = oracle / "empty.json"
        fixture.write_text(json.dumps(empty))
        with ResponsesProvider() as provider:
            provider.engine.set_script([raw_descriptor(response(session, [task])), raw_descriptor(finish)])
            provider.engine.set_child_script(session.session_id, [child])
            stock_config = copy.deepcopy(config)
            stock_config.update(model=MODEL, plugin=[(PLUGIN / "dist/index.js").as_uri()])
            stock_config["provider"] = {"opencode": {}, "openai": {"options": {
                "baseURL": provider.base_url + "/v1", "apiKey": "stock-test"}}}
            env = {**os.environ, **isolated_environment(oracle),
                   "OPENCODE_CONFIG_CONTENT": json.dumps(stock_config),
                   "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                   "OPENCODE_DISABLE_LSP_DOWNLOAD": "1"}
            imported = subprocess.run([str(binary), "import", str(fixture)], cwd=workspace,
                env=env, capture_output=True, text=True, timeout=30)
            assert imported.returncode == 0, imported.stderr
            completed = subprocess.run([str(binary), "run", "--format", "json", "--model", MODEL,
                "--session", session.session_id, *prompt.split()], cwd=workspace, env=env, input="",
                capture_output=True, text=True, timeout=30)
            assert completed.returncode == 0, completed.stderr
            root = [row["json"] for row in provider.engine.recorded_requests
                    if row["json"].get("prompt_cache_key") == session.session_id]
            assert len(root) == 2
            assert root[0] == initial, "Headless root request must match actual stock CLI bytes"
            assert len([row for row in provider.engine.recorded_requests
                        if row["json"].get("prompt_cache_key") != session.session_id]) == 1
    finally:
        session.close()


"""A real BB budget stop uses stock abort and preserves same-session history."""



class InterruptEngine(SessionScriptEngine):
    def __init__(self, script):
        super().__init__(script=script)
        self.delegated_arrived = threading.Event()
        self.delegated_release = threading.Event()
        self.delegated_finished = threading.Event()

    def next_response(self):
        response = super().next_response()
        if response["type"] == "delegated_pending":
            self.delegated_arrived.set()
        return response


def test_real_bb_max_steps_abort_keeps_history_and_allows_prompt_followup(tmp_path, monkeypatch):
    settings = production_settings("opencode")
    model = settings["model"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    arguments = {"command": "printf budget-history", "description": "Record budget history"}
    tool_response = responses_sse({"type": "response", "tool_calls": [{
        "name": "bash", "call_id": "call_budget", "arguments": json.dumps(arguments)}],
        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}}, 0)
    final_response = responses_sse({"type": "response", "text": "Followup complete.",
        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}}, 1)
    with ResponsesProvider() as provider:
        provider.engine.set_script([tool_response, final_response])
        route = provider_router.providers["openai"]
        monkeypatch.setattr(route, "base_url", provider.base_url + "/v1")
        monkeypatch.setattr(route, "auth_owner", "none")
        monkeypatch.setattr(route, "credential_required", False)
        agent = AgenticCoder(settings["profile"], str(workspace), force_local_mode=True, overrides={
            "enhanced_tools.lsp_integration.enabled": False,
            "tools.stock_config": hermetic_stock_config({"permission": "allow", "lsp": False, "agent": {"title": {"disable": True}}}),
            "tools.stock_environment": settings["overrides"]["stock_environment"],
        })
        events = []
        def receive(kind, payload, turn=None):
            events.append({"type": kind, "payload": payload})
        try:
            context = {"session_id": "budget-session", "input_id": "input-1", "turn_id": "turn-1",
                       "retained_effective_messages": [], "retained_raw_fact_ids": [], "_product_compaction_owner": True}
            first = agent.run_task("Execute the approved operation.", max_iterations=1, stream=True,
                                   event_emitter=receive, context=context)
            assert first["completion_reason"] == "max_steps_exhausted", first
            sessions = list(agent.agent._opencode_tool_sessions.values())
            assert len(sessions) == 1
            native = sessions[0]
            assert native.pending is None and native.request_done.is_set()
            native.timeout = 5
            snapshot = next(event["payload"] for event in reversed(events)
                            if event["type"] == "conversation.compaction.end")
            context.update(input_id="input-2", turn_id="turn-2",
                           retained_effective_messages=json.loads(base64.b64decode(snapshot["effective_context"])),
                           retained_raw_fact_ids=snapshot["raw_fact_ids"])
            started = time.monotonic()
            second = agent.run_task("Follow up on the completed tool.", max_iterations=2, stream=True,
                                    event_emitter=receive, context=context)
            assert time.monotonic() - started < 15
            assert second["completion_reason"] != "run_loop_exception", second
            assert list(agent.agent._opencode_tool_sessions.values()) == [native]
            bb_history = [item for item in provider.engine.recorded_requests[-1]["json"]["input"]
                          if item.get("call_id") == "call_budget"]
            assert {item["type"] for item in bb_history} == {"function_call", "function_call_output"}
            assert next(item["output"] for item in bb_history if item["type"] == "function_call_output") == "budget-history"
            messages = native.api("GET", "/session/" + native.session_id + "/message")
            bb_tool = next(part for message in messages for part in message["parts"]
                           if part.get("callID") == "call_budget")
        finally:
            agent.close()
    with ResponsesProvider() as provider:
        provider.engine = InterruptEngine([tool_response, {"type": "delegated_pending"}, final_response])
        provider.server.engine = provider.engine
        config = {"model": "openai/" + model, "permission": "allow", "lsp": False,
                  "agent": {"title": {"disable": True}},
                  "provider": {"openai": {"options": {"baseURL": provider.base_url + "/v1", "apiKey": "test"}}}}
        try:
            with ThreadPoolExecutor(max_workers=1) as pool, stock_server(workspace, tmp_path / "oracle", hermetic_stock_config(config)) as api:
                session_id = api("POST", "/session", {"title": "Budget interrupt oracle"})["id"]
                interrupted = pool.submit(api, "POST", "/session/" + session_id + "/message", {
                    "parts": [{"type": "text", "text": "Execute the approved operation."}], "agent": "build",
                    "model": {"providerID": "openai", "modelID": model},
                })
                assert provider.engine.delegated_arrived.wait(15)
                api("POST", "/session/" + session_id + "/abort", {})
                provider.engine.delegated_release.set()
                assert provider.engine.delegated_finished.wait(10)
                interrupted.result(timeout=10)
                api("POST", "/session/" + session_id + "/message", {
                    "parts": [{"type": "text", "text": "Follow up on the completed tool."}], "agent": "build",
                    "model": {"providerID": "openai", "modelID": model},
                })
                direct_history = [item for item in provider.engine.recorded_requests[-1]["json"]["input"]
                                  if item.get("call_id") == "call_budget"]
                assert bb_history == direct_history
                messages = api("GET", "/session/" + session_id + "/message")
                direct_tool = next(part for message in messages for part in message["parts"]
                                   if part.get("callID") == "call_budget")
                assert {key: bb_tool["state"][key] for key in ("input", "output", "status")} == {
                    key: direct_tool["state"][key] for key in ("input", "output", "status")}
        finally:
            provider.engine.delegated_release.set()


"""Native unfinished markers must receive BB summaries before normal responses."""



@pytest.mark.usefixtures("omo_dependencies")
def test_real_bb_followup_serves_stock_summary_left_by_actual_abort(tmp_path, monkeypatch):
    settings = production_settings("oh-my-opencode")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    usage = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
    with ResponsesProvider() as provider:
        provider.engine.set_script([
            responses_sse({"type": "response", "text": "Older BB context recorded.", "usage": usage}, 0),
            responses_sse({"type": "response", "text": "Genuine BB recovery summary.", "usage": usage}, 1),
            responses_sse({"type": "response", "text": "Followup completed through BB.", "usage": usage}, 2),
        ])
        route = provider_router.providers["openai"]
        monkeypatch.setattr(route, "base_url", provider.base_url + "/v1")
        monkeypatch.setattr(route, "auth_owner", "none")
        monkeypatch.setattr(route, "credential_required", False)
        agent = AgenticCoder(settings["profile"], str(workspace), force_local_mode=True, overrides={
            "enhanced_tools.lsp_integration.enabled": False,
            "tools.stock_config": hermetic_stock_config({"permission": "allow", "lsp": False, "agent": {"title": {"disable": True}}}),
            "tools.stock_environment": settings["overrides"]["stock_environment"],
        })
        events = []
        context = {"session_id": "queued-summary", "input_id": "input-1", "turn_id": "turn-1",
                   "retained_effective_messages": [], "retained_raw_fact_ids": [], "_product_compaction_owner": True}
        def receive(kind, payload, turn=None):
            events.append({"type": kind, "payload": payload})
        try:
            first = agent.run_task("Keep this older BB context.", max_iterations=3, stream=True,
                                   event_emitter=receive, context=context)
            assert first["completion_reason"] != "run_loop_exception", first
            native = next(iter(agent.agent._opencode_tool_sessions.values()))
            native._start_request("/session/" + native.session_id + "/summarize", {
                "providerID": "openai", "modelID": settings["model"], "auto": True,
            })
            native.wait_pending(allow_finished_request=False)
            assert native.compaction_pending is not None
            native.finish_run()
            messages = native.api("GET", "/session/" + native.session_id + "/message")
            aborted = [item for item in messages if item["info"].get("summary") is True
                       and item["info"].get("error", {}).get("name") == "MessageAbortedError"]
            assert len(aborted) == 1
            snapshot = next(event["payload"] for event in reversed(events)
                            if event["type"] == "conversation.compaction.end")
            context.update(input_id="input-2", turn_id="turn-2",
                           retained_effective_messages=json.loads(base64.b64decode(snapshot["effective_context"])),
                           retained_raw_fact_ids=snapshot["raw_fact_ids"])
            second = agent.run_task("Follow up after the genuine stock abort.", max_iterations=3, stream=True,
                                    event_emitter=receive, context=context)
            assert second["completion_reason"] != "run_loop_exception", second
            assert len(provider.engine.recorded_requests) == 3
            messages = native.api("GET", "/session/" + native.session_id + "/message")
            assert any(item["info"].get("summary") is True and any(
                part.get("text") == "Genuine BB recovery summary." for part in item["parts"])
                for item in messages)
            assert any(part.get("text") == "Followup completed through BB."
                       for item in messages for part in item["parts"])
            assert native._root_finished() and native.pending is None and native.compaction_pending is None
            assert any(record.is_boundary and record.summary == "Genuine BB recovery summary."
                       for record in native.current_state.compaction_state.records)
        finally:
            agent.close()


"""Stock's authenticated PATCH dead end preserves errors and source wire edits."""








@pytest.mark.usefixtures("omo_dependencies")
def test_authenticated_native_patch_dead_end_preserves_error_and_user_followup(tmp_path, monkeypatch):
    settings = production_settings("oh-my-opencode")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    usage = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
    original_error = "too many tokens: 74000 tokens > 64000 maximum"
    descriptors = [
        tool_response("handled_seed", 12, 24000),
        {"type": "response", "text": "First inspection complete.", "usage": usage},
        {"type": "error", "status_code": 400, "error": "invalid_request_error", "message": original_error},
        {"type": "response", "text": "User followup completed.", "usage": usage},
    ]
    script = [responses_sse(value, index) for index, value in enumerate(descriptors)]
    config_document = yaml.safe_load(Path(settings["profile"]).read_bytes())
    config_document.setdefault("enhanced_tools", {}).setdefault("lsp_integration", {})["enabled"] = False
    config_document["tools"]["stock_config"] = hermetic_stock_config({
        "permission": "allow", "lsp": False, "agent": {"title": {"disable": True}},
    })
    config_document["tools"]["stock_environment"] = settings["overrides"]["stock_environment"]
    profile = tmp_path / "native-profile.yaml"
    profile.write_text(yaml.safe_dump(config_document))
    with ResponsesProvider() as provider:
        provider.engine.set_script(copy.deepcopy(script))
        route = provider_router.providers["openai"]
        monkeypatch.setattr(route, "base_url", provider.base_url + "/v1")
        monkeypatch.setattr(route, "auth_owner", "none")
        monkeypatch.setattr(route, "credential_required", False)
        async def scenario():
            registry = SessionRegistry(state_root=tmp_path / "registry")
            service = SessionService(registry=registry, state_root=tmp_path / "registry")
            terminal = asyncio.Queue()
            async def next_terminal():
                while True:
                    event = await terminal.get()
                    assert event is not None, "Session dispatcher ended before its turn terminal"
                    if event.type in {EventType.TURN_COMPLETED, EventType.TURN_FAILED, EventType.TURN_CANCELLED}:
                        return event.type, event.payload
            try:
                created = await service.create_session(SessionCreateRequest(
                    config_path=str(profile), workspace=str(workspace),
                    task="", stream=True, max_steps=4, permission_mode="allow",
                ), event_root=tmp_path / "events", runtime_root=tmp_path / "runtime")
                record = await registry.get(created.session_id)
                await service._register_subscriber(record, terminal)
                await service.send_input(created.session_id, SessionInputRequest(
                    content="Inspect the numbered records.", client_message_id="user-first"))
                first_kind, _ = await asyncio.wait_for(next_terminal(), 60)
                assert first_kind == EventType.TURN_COMPLETED
                runner = record.runner
                native = runner._agent.agent._opencode_tool_sessions[created.session_id]
                native.timeout = 5
                await service.send_input(created.session_id, SessionInputRequest(
                    content="Finish the inspection.", client_message_id="user-recovery"))
                failed_kind, failed = await asyncio.wait_for(next_terminal(), 15)
                assert failed_kind == EventType.TURN_FAILED, failed
                assert runner._stock_input_controller.origins == {}
                assert native.recovery_outcome == "error"
                assert native.pending is None and native.compaction_pending is None
                assert all(trigger.pending is None for trigger in runner._agent.agent.compaction_controller.recipe.triggers
                           if getattr(trigger, "kind", None) == "pending_error")
                messages = native.api("GET", "/session/" + native.session_id + "/message")
                assert any(original_error in json.dumps(item["info"].get("error")) for item in messages)
                patched = [part for item in messages for part in item["parts"]
                           if part["type"] == "tool" and part["state"].get("output", "").startswith("[TOOL RESULT TRUNCATED")]
                assert patched and all(part["state"]["time"].get("compacted") for part in patched)
                assert len([item for item in messages if item["info"]["role"] == "user"]) == 2
                await service.send_input(created.session_id, SessionInputRequest(
                    content="Confirm the inspection.", client_message_id="user-followup"))
                followup_kind, followup_result = await asyncio.wait_for(next_terminal(), 60)
                assert followup_kind == EventType.TURN_COMPLETED, followup_result
                assert len(provider.engine.recorded_requests) == 4
                actual = [item for item in provider.engine.recorded_requests[3]["json"]["input"]
                          if item.get("type") == "function_call_output"]
                # message-v2.ts:637 projects time.compacted, not the stored marker.
                assert any(item["output"] == "[Old tool result content cleared]" for item in actual)
                return actual
            finally:
                await service.shutdown_runtime_owners()
        actual = asyncio.run(scenario())
    with ResponsesProvider() as provider:
        provider.engine.set_script(copy.deepcopy(script))
        config = {"plugin": [(PLUGIN / "dist/index.js").as_uri()], "model": "openai/" + settings["model"], "permission": "allow", "lsp": False,
                  "agent": {"title": {"disable": True}},
                  "provider": {"openai": {"options": {"baseURL": provider.base_url + "/v1", "apiKey": "test"}}}}
        with stock_server(workspace, tmp_path / "oracle", hermetic_stock_config(config)) as api:
            session_id = api("POST", "/session", {"title": "Authenticated native recovery dead end"})["id"]
            def prompt(text):
                return api("POST", "/session/" + session_id + "/message", {
                    "parts": [{"type": "text", "text": text}],
                    "model": {"providerID": "openai", "modelID": settings["model"]},
                })
            prompt("Inspect the numbered records.")
            authorization = "Basic " + base64.b64encode(
                f"{SOURCE_API_USERNAME}:{SOURCE_API_PASSWORD}".encode()).decode()
            request = urllib.request.Request(api.url + "/event", headers={"Authorization": authorization})
            with urllib.request.urlopen(request, timeout=30) as events:
                failed = prompt("Finish the inspection.")
                assert original_error in json.dumps(failed["info"]["error"])
                deadline = time.monotonic() + 30
                for line in events:
                    assert time.monotonic() < deadline, "Genuine source truncation completion timed out"
                    if not line.startswith(b"data: "):
                        continue
                    event = json.loads(line[6:])
                    if (event["type"] == "tui.toast.show"
                            and event["properties"].get("title") == "Truncation Complete"):
                        assert event["properties"]["variant"] == "success"
                        break
                else:
                    raise AssertionError("Genuine source truncation completion was not observed")
            messages = api("GET", "/session/" + session_id + "/message")
            assert len([item for item in messages if item["info"]["role"] == "user"]) == 2
            prompt("Confirm the inspection.")
            expected = [item for item in provider.engine.recorded_requests[3]["json"]["input"]
                        if item.get("type") == "function_call_output"]
            assert len(provider.engine.recorded_requests) == 4
            assert json.dumps(actual, ensure_ascii=False, separators=(",", ":")).encode() == json.dumps(
                expected, ensure_ascii=False, separators=(",", ":")).encode()


def test_no_native_recovery_continuation_keeps_original_error_bounded(plain):
    plain.user([{"type": "text", "text": "Observe an actual non-recoverable provider failure."}])
    plain.timeout = 0.5
    payload = json.dumps({"error": {"type": "invalid_request_error", "message": "Original invalid request"}}).encode()
    assert plain.submit_overflow(400, {"content-type": "application/json"}, payload) == "error"
    assert plain.pending is None and plain.compaction_pending is None
    assert plain.failure is None
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    assert messages[-1]["info"]["error"]["name"] == "APIError"
    assert "Original invalid request" in json.dumps(messages[-1]["info"]["error"])


@pytest.mark.parametrize("origin,claimed,idle,closed,expected", [
    ({"request_id": "source"}, False, False, False, True),
    ({"request_id": "source"}, True, False, False, False),
    (None, False, False, False, False),
    ({"request_id": "source"}, False, True, False, False),
    ({"request_id": "source"}, False, False, True, False),
])
def test_source_continuation_port_exposes_only_unclaimed_live_origin(origin, claimed, idle, closed, expected):
    import threading
    session = OpenCodeToolSession.__new__(OpenCodeToolSession)
    session.condition = threading.Condition()
    session.closed, session.headless_idle_seen = closed, idle
    session.pending = {"stock_origin": origin, "stock_claimed": claimed}
    session.compaction_pending = None
    assert session.source_should_continue() is expected


@pytest.mark.parametrize("latest", [
    {"info": {"role": "user"}, "parts": [{"type": "compaction", "auto": True}]},
    {"info": {"role": "assistant", "summary": True, "time": {"created": 1}}, "parts": []},
])
def test_native_compaction_tail_fences_delayed_provider_admission(latest):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    waiting = threading.Event()
    class AdmissionCondition(threading.Condition):
        def wait_for(self, predicate, timeout=None):
            waiting.set()
            return super().wait_for(predicate, timeout)
    session = OpenCodeToolSession.__new__(OpenCodeToolSession)
    session.condition = AdmissionCondition()
    session.closed, session.failure = False, None
    session.headless, session.headless_idle_seen = False, False
    session.session_id, session.timeout = "root", 5
    session.compaction_pending = None
    calls = []
    def native_api(method, path):
        calls.append((method, path))
        return [latest]
    session.api = native_api
    pending = {"body": {"input": []}}
    with ThreadPoolExecutor() as pool:
        result = pool.submit(session.queued_compaction)
        assert waiting.wait(5)
        assert not result.done()
        with session.condition:
            session.compaction_pending = pending
            session.condition.notify_all()
        assert result.result(timeout=5) is pending
    assert calls == [("GET", "/session/root/message")]


@pytest.mark.parametrize("latest", [
    {"info": {"role": "assistant", "summary": True, "time": {"completed": 2}}, "parts": []},
    {"info": {"role": "assistant", "summary": True, "error": {"name": "MessageAbortedError"}}, "parts": []},
    {"info": {"role": "assistant", "time": {"completed": 2}}, "parts": [{"type": "text", "text": "Done."}]},
])
def test_finished_or_aborted_native_summary_is_not_a_queued_compaction(latest):
    import threading
    session = OpenCodeToolSession.__new__(OpenCodeToolSession)
    session.condition = threading.Condition()
    session.closed, session.failure = False, None
    session.headless, session.headless_idle_seen = False, False
    session.session_id, session.timeout = "root", 5
    session.compaction_pending = None
    session.api = lambda method, path: [latest]
    assert session.queued_compaction() is None
