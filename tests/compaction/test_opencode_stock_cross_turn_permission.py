"""A genuine child keeps its approval channel through real BB runner turns."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import Session as ProductSession
from breadboard_engine.agent import AgenticCoder
from breadboard_engine.api.cli_bridge.events import EventType
from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry, TurnRecord
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.provider.routing import provider_router
from scripts.compaction_lanes.opencode_lane import ResponsesProvider, create_empty_stock_sessions, production_settings, responses_sse
from tests.compaction.test_opencode_stock_tools import MODEL, binary


def test_background_child_ui_approval_survives_parent_completion_and_next_runner_turn(tmp_path, monkeypatch, binary):
    settings = production_settings("oh-my-opencode")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("child-readable content\n")
    (workspace / ".opencode").mkdir()
    (workspace / ".opencode/oh-my-opencode.json").write_text(json.dumps({"agents": {"explore": {"model": MODEL}}}))
    exports = create_empty_stock_sessions(workspace, tmp_path / "seed", delegated=False)
    session_id = exports[0]["info"]["id"]
    usage = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
    def call(identifier, name, arguments, ordinal):
        return responses_sse({"type": "response", "tool_calls": [{"name": name, "call_id": identifier,
            "arguments": json.dumps(arguments)}], "usage": usage}, ordinal)
    def text(value, ordinal):
        return responses_sse({"type": "response", "text": value, "usage": usage}, ordinal)
    with ResponsesProvider() as provider:
        provider.engine.set_script([
            call("call_background", "task", {"subagent_type": "explore", "load_skills": [],
                "description": "Read child", "prompt": "Read sample.txt before continuing.", "run_in_background": True}, 0),
            text("Parent completed while the child waits.", 1),
            call("call_root_read", "read", {"filePath": str(workspace / "sample.txt")}, 2),
            text("Followup read completed.", 3),
        ])
        provider.engine.set_child_script(session_id, [
            call("call_child_read", "read", {"filePath": str(workspace / "sample.txt")}, 4),
            text("The approved child read completed.", 5),
        ])
        route = provider_router.providers["openai"]
        monkeypatch.setattr(route, "base_url", provider.base_url + "/v1")
        monkeypatch.setattr(route, "auth_owner", "none")
        monkeypatch.setattr(route, "credential_required", False)
        agent = AgenticCoder(settings["profile"], str(workspace), force_local_mode=True, overrides={
            "enhanced_tools.lsp_integration.enabled": False,
            "tools.stock_session_imports": exports,
            "tools.stock_config": {"permission": {"read": "ask"}, "lsp": False,
                                  "agent": {"title": {"disable": True}, "explore": {"permission": {"read": "ask"}}}},
            "tools.stock_environment": settings["overrides"]["stock_environment"],
        })
        record = SessionRecord(session_id=session_id, status=SessionStatus.RUNNING)
        record.product_session = ProductSession.start(
            EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64}),
            "Launch the child.", session_id=session_id, concurrent_approvals=True)
        runner = SessionRunner(session=record, registry=SessionRegistry(state_root=tmp_path / "registry"),
            request=SessionCreateRequest(config_path=settings["profile"], task="Launch the child.",
                                         stream=True, max_steps=4, permission_mode="prompt"))
        runner._base_config_cache = yaml.safe_load(Path(settings["profile"]).read_bytes())
        runner._agent = agent
        condition = threading.Condition()
        approvals = []
        publish = runner.publish_event
        def observe(kind, payload, *args, **kwargs):
            publish(kind, payload, *args, **kwargs)
            if kind == EventType.PERMISSION_REQUEST:
                with condition:
                    approvals.append(payload)
                    condition.notify_all()
        monkeypatch.setattr(runner, "publish_event", observe)
        def run_turn(index, content):
            turn = TurnRecord(input_id=f"input-{index}", turn_id=f"turn-{index}", client_message_id=f"message-{index}",
                content=content, attachments=(), original_disposition="started", state="active")
            record.turns_by_id[turn.turn_id] = turn
            record.active_turn_id = turn.turn_id
            result = runner._task_execution.execute_task(content, input_id=turn.input_id, turn_id=turn.turn_id)
            turn.state = "completed"
            record.active_turn_id = None
            return result
        try:
            first = run_turn(1, "Launch the child.")
            assert first["completion_summary"].get("reason") != "run_loop_exception", first
            with condition:
                assert condition.wait_for(lambda: len(approvals) == 1, timeout=10)
            child = approvals[0]
            child_session_id = child["metadata"]["stock_request"]["sessionID"]
            assert child_session_id != session_id
            retained_queue = runner._permission_queue
            assert retained_queue is not None
            native = list(agent.agent._opencode_tool_sessions.values())[0]
            native.timeout = 10
            with ThreadPoolExecutor(max_workers=1) as pool:
                next_turn = pool.submit(run_turn, 2, "Read sample.txt in the parent while the child waits.")
                with condition:
                    assert condition.wait_for(lambda: len(approvals) == 2, timeout=10)
                root = approvals[1]
                assert root["metadata"]["stock_request"]["sessionID"] == session_id
                assert root["request_id"] != child["request_id"]
                asyncio.run(runner.handle_command("permission_response", {"request_id": root["request_id"], "response": "once"}))
                second = next_turn.result(timeout=15)
                assert second["completion_summary"].get("reason") != "run_loop_exception", second
            assert runner._permission_queue is retained_queue
            assert record.active_turn_id is None
            delivered = asyncio.run(runner.handle_command("permission_response", {
                "request_id": child["request_id"], "response": "once",
            }))
            assert delivered["decision"] == "once"
            with native.condition:
                assert native.condition.wait_for(lambda: not native.interaction_threads, timeout=10)
            messages = native.api("GET", "/session/" + child_session_id + "/message")
            read = next(part for message in messages for part in message["parts"] if part.get("callID") == "call_child_read")
            assert read["state"]["status"] == "completed"
            assert "child-readable content" in read["state"]["output"]
        finally:
            agent.close()
