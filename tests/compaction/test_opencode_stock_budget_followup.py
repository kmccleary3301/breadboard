"""A real BB budget stop uses stock abort and preserves same-session history."""
import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from breadboard_engine.agent import AgenticCoder
from breadboard_engine.provider.routing import provider_router
from scripts.compaction_lanes.opencode_lane import (
    ResponsesProvider, SessionScriptEngine, production_settings, responses_sse, stock_server,
)


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
            "tools.stock_config": {"permission": "allow", "lsp": False, "agent": {"title": {"disable": True}}},
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
            with ThreadPoolExecutor(max_workers=1) as pool, stock_server(workspace, tmp_path / "oracle", config) as api:
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
