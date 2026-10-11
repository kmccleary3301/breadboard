"""An unanswered real child permission cannot stall the stock parent loop."""
import json
import queue
import threading
from types import SimpleNamespace

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, bridge_stock_interaction
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.state.session_state import SessionState
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from tests.compaction.test_opencode_stock_delegation import connect_auxiliary, raw_descriptor
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


def test_background_child_permission_leaves_parent_completion_and_root_abort_unblocked(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("child-readable content\n")
    (workspace / ".opencode").mkdir()
    (workspace / ".opencode/oh-my-opencode.json").write_text(json.dumps({
        "agents": {"explore": {"model": MODEL}},
    }))
    asked = threading.Event()
    events = []
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    def emit(kind, payload, turn=None):
        events.append((kind, payload))
        if kind == "permission_request":
            asked.set()
    state = SessionState(workspace=str(workspace), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", queue.Queue())
    owner = SimpleNamespace(permission_broker=broker)
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=30, lsp_root=LSP_PIN,
        stock_config={"permission": {"read": "ask"},
                      "agent": {"title": {"disable": True}, "explore": {"permission": {"read": "ask"}}}},
        read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
        environment=isolated_environment(tmp_path / "stock"),
        interaction=lambda native, kind, properties: bridge_stock_interaction(owner, state, native, kind, properties))
    session.current_state = state
    session.on_close = lambda native: broker.cancel_pending_requests(state)
    try:
        with ResponsesProvider() as provider:
            provider.engine.set_child_script(session.session_id, [
                raw_descriptor(response(session, [("call_child_read", "read", {"filePath": str(workspace / "sample.txt")})])),
                raw_descriptor(response(session, text="Child finished after permission rejection.")),
            ])
            connect_auxiliary(session, provider)
            session.user([{"type": "text", "text": "Launch the child and finish the parent independently."}])
            session.submit(response(session, [("call_delegated", "task", {
                "subagent_type": "explore", "load_skills": [], "description": "Child permission inspection",
                "prompt": "Read sample.txt before continuing.", "run_in_background": True,
            })]))
            assert asked.wait(10)
            request = next(payload for kind, payload in events if kind == "permission_request")
            assert request["metadata"]["stock_request"]["sessionID"] != session.session_id
            session.timeout = 5
            session.submit(response(session, text="Parent completed while the child waits."))
            assert session._root_finished()
            session.finish_run()
            assert session.interaction_threads
            assert not any(kind == "permission_response" for kind, _ in events)
            session.close()
    finally:
        session.close()
    assert not session.interaction_threads
    assert any(kind == "permission_response" for kind, _ in events)
