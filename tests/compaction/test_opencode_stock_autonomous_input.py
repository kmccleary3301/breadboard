"""Pinned OMO owns background notification creation and parent scheduling."""
import queue
import json
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, bridge_stock_interaction
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.state.session_state import SessionState
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from tests.compaction.test_opencode_stock_delegation import connect_auxiliary, raw_descriptor
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


@pytest.mark.parametrize("boundary", ["idle", "busy_before_frame", "queued_before_observed_busy"])
def test_real_background_notification_is_claimed_without_duplicate_native_user(tmp_path, binary, boundary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("actual background result\n")
    asked = threading.Event()
    notified = threading.Event()
    requests = []
    notices = []
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    def emit(kind, payload, turn=None):
        if kind == "permission_request":
            requests.append(payload)
            asked.set()
    state = SessionState(workspace=str(workspace), image="python-dev:latest", config={}, event_emitter=emit)
    replies = queue.Queue()
    state.set_provider_metadata("permission_queue", replies)
    owner = SimpleNamespace(permission_broker=broker)
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=20, lsp_root=LSP_PIN,
        stock_config={"permission": {"read": "ask"}, "agent": {"title": {"disable": True},
                      "explore": {"model": MODEL, "permission": {"read": "ask"}}}},
        read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
        environment=isolated_environment(tmp_path / "stock"),
        interaction=lambda native, kind, properties: bridge_stock_interaction(owner, state, native, kind, properties))
    session.current_state = state
    session.on_close = lambda native: broker.cancel_pending_requests(state)
    def stock_input(source):
        notices.append(source)
        notified.set()
    session.stock_input_handler = stock_input
    block_frame = threading.Event()
    blocked = threading.Event()
    release_frame = threading.Event()
    busy_event_blocked = threading.Event()
    release_busy_event = threading.Event()
    lookup_seen = threading.Event()
    classify = session.classify
    def classify_with_boundary(headers):
        kind = classify(headers)
        if block_frame.is_set() and kind in {"main", "compaction"}:
            blocked.set()
            if not release_frame.wait(15):
                raise TimeoutError("Native source frame boundary was not released")
        return kind
    session.classify = classify_with_boundary
    try:
        with ResponsesProvider() as provider:
            provider.engine.set_child_script(session.session_id, [
                raw_descriptor(response(session, [("call_child_read", "read", {"filePath": str(workspace / "sample.txt")})])),
                raw_descriptor(response(session, text="The genuine child is complete.")),
            ])
            connect_auxiliary(session, provider)
            session.user([{"type": "text", "text": "Start a background reader and finish the parent."}])
            session.submit(response(session, [("call_background", "task", {
                "subagent_type": "explore", "load_skills": [], "description": "Read sample",
                "prompt": "Read sample.txt.", "run_in_background": True,
            })]))
            assert asked.wait(10)
            session.submit(response(session, text="The first parent turn is complete."))
            if boundary != "idle":
                block_frame.set()
            if boundary != "busy_before_frame":
                session.finish_run()
            if boundary == "queued_before_observed_busy":
                read_line = session.event_connection.readline
                def delayed_busy_event(*args, **kwargs):
                    line = read_line(*args, **kwargs)
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        properties = event.get("properties", {})
                        if (
                            event.get("type") == "session.status"
                            and properties.get("sessionID") == session.session_id
                            and properties["status"]["type"] == "busy"
                        ):
                            busy_event_blocked.set()
                            if not release_busy_event.wait(15):
                                raise TimeoutError("Authenticated native busy event was not released")
                    return line
                session.event_connection.readline = delayed_busy_event
                latest_user = session._latest_user_message
                def observed_lookup():
                    message = latest_user()
                    lookup_seen.set()
                    return message
                session._latest_user_message = observed_lookup
            replies.put({"request_id": requests[0]["request_id"], "response": "once"})
            if boundary == "busy_before_frame":
                assert blocked.wait(15)
                with session.condition:
                    assert session.condition.wait_for(
                        lambda: not session.native_prompt_done.is_set(), 10
                    ), "The real source root must be busy before its model frame"
                with ThreadPoolExecutor(max_workers=1) as executor:
                    finished = executor.submit(session.finish_run)
                    with session.condition:
                        assert session.condition.wait_for(lambda: not session.run_active, 10)
                    assert not finished.done(), "A genuine successor must not be aborted at this gap"
                    release_frame.set()
                    finished.result(timeout=15)
            if boundary == "queued_before_observed_busy":
                assert blocked.wait(15) and busy_event_blocked.wait(15)
                assert session.native_prompt_done.is_set(), "Only delivery is delayed; stock is genuinely busy"
                with ThreadPoolExecutor(max_workers=1) as executor:
                    fenced = executor.submit(session.wait_for_idle_source_input)
                    try:
                        assert lookup_seen.wait(1), "Admission must inspect the stock-queued user even before busy is observed"
                        assert not fenced.done(), "The user cannot pass the source notification's unfinished frame"
                    finally:
                        release_busy_event.set()
                        release_frame.set()
                    fenced.result(timeout=15)
            assert notified.wait(15), "An unsolicited stock root request must be delivered, not stranded"
            assert len(notices) == 1
            source = notices[0]
            assert source["kind"] == "stock"
            assert source["session_id"] == session.session_id
            assert "BACKGROUND" in source["content"]
            assert "<!-- OMO_INTERNAL_INITIATOR -->" in source["content"]
            before = session.api("GET", f"/session/{session.session_id}/message")
            request = session.claim_stock_input(source)
            assert request == session.records[-1]["request"]
            assert session.api("GET", f"/session/{session.session_id}/message") == before
            session.submit(response(session, text="The autonomous parent processed the result."))
            session.finish_run()
            session.user([{"type": "text", "text": "A real user follows up after background completion."}])
            session.submit(response(session, text="The followup works."))
            session.finish_run()
    finally:
        release_busy_event.set()
        release_frame.set()
        session.close()
