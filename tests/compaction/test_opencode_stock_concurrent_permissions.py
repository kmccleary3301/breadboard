"""Two real plugin child permissions must keep their own UI replies."""
import json
import queue
import threading
from types import SimpleNamespace

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, bridge_stock_interaction
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.state.session_state import SessionState
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from tests.compaction.test_opencode_stock_delegation import connect_auxiliary, raw_descriptor
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


class FirstReaderQueue(queue.Queue):
    """Force the first waiter to read a reply addressed to the second waiter."""
    def __init__(self):
        super().__init__()
        self.first_reader = None
        self.reader_lock = threading.Lock()
        self.reading = threading.Event()
        self.release = threading.Event()

    def get(self, *args, **kwargs):
        with self.reader_lock:
            if self.first_reader is None:
                self.first_reader = threading.current_thread()
                self.reading.set()
            first = self.first_reader is threading.current_thread()
        if not first:
            assert self.release.wait(20)
        return super().get(*args, **kwargs)


@pytest.mark.parametrize("cancel_second", [False, True])
def test_real_child_replies_route_in_reverse_order_and_cancel_only_the_named_child(tmp_path, binary, cancel_second):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("child-readable content\n")
    (workspace / ".opencode").mkdir()
    (workspace / ".opencode/oh-my-opencode.json").write_text(json.dumps({"agents": {"explore": {"model": MODEL}}}))
    condition = threading.Condition()
    requests, responses = [], []
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    def emit(kind, payload, turn=None):
        with condition:
            if kind == "permission_request":
                requests.append(payload)
            elif kind == "permission_response":
                responses.append(payload)
            condition.notify_all()
    state = SessionState(workspace=str(workspace), image="python-dev:latest", config={}, event_emitter=emit)
    replies = FirstReaderQueue()
    state.set_provider_metadata("permission_queue", replies)
    owner = SimpleNamespace(permission_broker=broker)
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=30, lsp_root=LSP_PIN,
        stock_config={"permission": {"read": "ask"}, "agent": {"title": {"disable": True},
                      "explore": {"permission": {"read": "ask"}}}},
        read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
        environment=isolated_environment(tmp_path / "stock"),
        interaction=lambda native, kind, properties: bridge_stock_interaction(owner, state, native, kind, properties))
    session.current_state = state
    session.on_close = lambda native: broker.cancel_pending_requests(state)
    try:
        with ResponsesProvider() as provider:
            read = raw_descriptor(response(session, [("call_child_read", "read", {"filePath": str(workspace / "sample.txt")})]))
            finished = raw_descriptor(response(session, text="Child permission handled."))
            provider.engine.set_child_script(session.session_id, [read, read, finished, finished])
            connect_auxiliary(session, provider)
            session.user([{"type": "text", "text": "Launch two independent children."}])
            for index in (1, 2):
                session.submit(response(session, [(f"call_child_{index}", "task", {
                    "subagent_type": "explore", "load_skills": [], "description": f"Read child {index}",
                    "prompt": "Read sample.txt before continuing.", "run_in_background": True,
                })]))
                with condition:
                    assert condition.wait_for(lambda: len(requests) == index, timeout=10)
                if index == 1:
                    assert replies.reading.wait(5)
            first, second = requests
            assert first["request_id"] != second["request_id"]
            assert first["metadata"]["stock_request"]["sessionID"] != second["metadata"]["stock_request"]["sessionID"]
            if cancel_second:
                broker.cancel_stock_requests(state, [second["metadata"]["stock_request"]["id"]])
            else:
                replies.put({"request_id": second["request_id"], "response": "once"})
            with condition:
                assert condition.wait_for(lambda: any(item["request_id"] == second["request_id"] for item in responses), timeout=5)
                assert not any(item["request_id"] == first["request_id"] for item in responses)
            replies.put({"request_id": first["request_id"], "response": "once"})
            with condition:
                assert condition.wait_for(lambda: len(responses) == 2, timeout=5)
            assert {item["request_id"]: item["response"] for item in responses} == {
                first["request_id"]: "once", second["request_id"]: "reject" if cancel_second else "once",
            }
    finally:
        replies.release.set()
        session.close()
