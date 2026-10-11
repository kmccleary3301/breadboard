"""Native unfinished markers must receive BB summaries before normal responses."""
import base64
import json

from breadboard_engine.agent import AgenticCoder
from breadboard_engine.provider.routing import provider_router
from scripts.compaction_lanes.opencode_lane import ResponsesProvider, production_settings, responses_sse


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
            "tools.stock_config": {"permission": "allow", "lsp": False, "agent": {"title": {"disable": True}}},
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
