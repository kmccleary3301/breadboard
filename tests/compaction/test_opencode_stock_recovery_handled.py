"""Stock's authenticated PATCH dead end preserves errors and source wire edits."""
import asyncio
import base64
import copy
import json
import time

from pathlib import Path
import urllib.request

import yaml

from breadboard_engine.api.cli_bridge.events import EventType
from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionInputRequest
from breadboard_engine.api.cli_bridge.registry import SessionRegistry
from breadboard_engine.api.cli_bridge.service import SessionService

from breadboard_engine.provider.routing import provider_router
from scripts.compaction_lanes.opencode_lane import (
    ResponsesProvider, SOURCE_API_USERNAME, SOURCE_API_PASSWORD,
    production_settings, responses_sse, stock_server, tool_response,
)
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN
from tests.compaction.test_opencode_stock_tools import binary, plain




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
    config_document["tools"]["stock_config"] = {
        "permission": "allow", "lsp": False, "agent": {"title": {"disable": True}},
    }
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
        with stock_server(workspace, tmp_path / "oracle", config) as api:
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
