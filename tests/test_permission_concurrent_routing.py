"""Host-visible decisions must belong to an active permission request."""
from concurrent.futures import ThreadPoolExecutor
import queue
import threading
from types import SimpleNamespace

import pytest

from breadboard_engine.permissions.broker import PermissionBroker, PermissionRequestTimeoutError
from breadboard_engine.permissions.claude_permission import request_stock_tool_permission
from breadboard_engine.permissions.opencode_questions import request_stock_question
from breadboard_engine.permissions.session_ids import PermissionResponseQueue
from breadboard_engine.state.session_state import SessionState


def session(tmp_path, emit, replies=None):
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", replies if replies is not None else queue.Queue())
    return state


def native_request(name="first"):
    return {"tool_name": "Write", "input": {"file_path": name, "content": "owned"}}


def question():
    return {"id": "que_native", "questions": [{"question": "Choose?", "options": []}]}


@pytest.mark.parametrize("unowned", ["once", {"response": "once"},
    {"request_id": "permission_unknown", "response": "always"}])
def test_native_permission_ignores_unowned_approvals(tmp_path, unowned):
    broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 1}})
    events = []
    def emit(kind, payload, turn=None):
        events.append((kind, payload))
        if kind == "permission_request":
            replies.put(unowned)
            replies.put({"request_id": payload["request_id"], "response": "reject"})
    replies = queue.Queue()
    state = session(tmp_path, emit, replies)
    assert request_stock_tool_permission(broker, state, native_request())["behavior"] == "deny"
    assert events[-1][1]["response"] == "reject"
    assert not state.get_provider_metadata(broker.STATE_KEY)["pending"]


def test_legacy_single_request_keeps_unkeyed_once(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt"}, "shell": {"default": "ask"}})
    replies = queue.Queue()
    replies.put("once")
    state = session(tmp_path, lambda *args, **kwargs: None, replies)
    broker.ensure_allowed(state, [SimpleNamespace(function="bash", arguments={"command": "printf owned"})])
    assert any(item.get("permission_broker", {}).get("note") == "user_allow_once" for item in state.transcript)


def test_reverse_order_native_replies_reach_only_their_waiter(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 5}})
    condition = threading.Condition()
    requests, responses = [], []
    def emit(kind, payload, turn=None):
        with condition:
            if kind == "permission_request":
                requests.append(payload)
            elif kind == "permission_response":
                responses.append(payload)
            condition.notify_all()
    reading = threading.Event()
    class ObservedQueue(queue.Queue):
        def get(self, *args, **kwargs):
            reading.set()
            return super().get(*args, **kwargs)
    replies = ObservedQueue()
    state = session(tmp_path, emit, replies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request_stock_tool_permission, broker, state, native_request("first"))
        with condition:
            assert condition.wait_for(lambda: len(requests) == 1, timeout=2)
        assert reading.wait(2)
        second = pool.submit(request_stock_tool_permission, broker, state, native_request("second"))
        with condition:
            assert condition.wait_for(lambda: len(requests) == 2, timeout=2)
        try:
            replies.put({"permissionId": requests[1]["request_id"], "response": "reject"})
            assert second.result(timeout=2)["behavior"] == "deny"
            assert not first.done()
            replies.put({"requestId": requests[0]["request_id"], "response": "once"})
            assert first.result(timeout=2) == {"behavior": "allow", "updatedInput": native_request("first")["input"]}
        finally:
            broker.cancel_pending_requests(state)
    assert {item["request_id"]: item["response"] for item in responses} == {
        requests[0]["request_id"]: "once", requests[1]["request_id"]: "reject"}


def test_completed_request_reply_cannot_approve_later_request(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 1}})
    identifiers = []
    def emit(kind, payload, turn=None):
        if kind != "permission_request":
            return
        identifiers.append(payload["request_id"])
        if len(identifiers) == 2:
            replies.put({"request_id": identifiers[0], "response": "always"})
        replies.put({"request_id": payload["request_id"], "response": "once" if len(identifiers) == 1 else "reject"})
    replies = queue.Queue()
    state = session(tmp_path, emit, replies)
    assert request_stock_tool_permission(broker, state, native_request())["behavior"] == "allow"
    assert request_stock_tool_permission(broker, state, native_request("later"))["behavior"] == "deny"
    assert len(set(identifiers)) == 2


def test_unowned_reply_stream_reaches_timeout_without_approval(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 0.01}})
    replies = queue.Queue()
    replies.put("once")
    state = session(tmp_path, lambda *args, **kwargs: None, replies)
    with pytest.raises((queue.Empty, PermissionRequestTimeoutError)):
        request_stock_tool_permission(broker, state, native_request())
    assert not state.get_provider_metadata(broker.STATE_KEY)["pending"]


def test_question_alias_reply_uses_registered_request(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 1}})
    replies = queue.Queue()
    def emit(kind, payload, turn=None):
        if kind == "permission_request":
            replies.put({"answers": [["unowned"]]})
            replies.put({"permissionId": payload["request_id"], "answers": [["owned"]]})
    state = session(tmp_path, emit, replies)
    assert request_stock_question(broker, state, question()) == [["owned"]]


def test_legacy_and_native_requests_share_session_counter_across_brokers(tmp_path):
    legacy = PermissionBroker({
        "options": {"mode": "prompt", "timeout_s": 2}, "shell": {"default": "ask"}
    })
    native = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 2}})
    writing, release, native_started = threading.Event(), threading.Event(), threading.Event()
    legacy_done = threading.Event()
    native_thread = []
    replies = queue.Queue()
    events = []
    def emit(kind, payload, turn=None):
        if kind != "permission_request":
            return
        events.append(payload)
        if payload["category"] == "question":
            # Keep legacy's direct queue consumer from stealing the native answer.
            assert legacy_done.wait(2)
            replies.put({"request_id": payload["request_id"], "answers": [["owned"]]})
        else:
            replies.put({"request_id": payload["request_id"], "response": "once"})
    class PausedFirstWrite(SessionState):
        def get_provider_metadata(self, key, default=None):
            if key == "permission_queue" and native_thread == [threading.current_thread()]:
                native_started.set()
            return super().get_provider_metadata(key, default)
        def set_provider_metadata(self, key, value):
            if key == legacy.STATE_KEY and value.get("counter") == 1 and not writing.is_set():
                writing.set()
                assert release.wait(2)
            return super().set_provider_metadata(key, value)
    state = PausedFirstWrite(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", replies)
    def ask_legacy():
        try:
            legacy.ensure_allowed(state, [
                SimpleNamespace(function="bash", arguments={"command": "printf owned"})
            ])
        finally:
            legacy_done.set()
    def ask_native():
        native_thread.append(threading.current_thread())
        return request_stock_question(native, state, question())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ask_legacy)
        try:
            assert writing.wait(2)
            second = pool.submit(ask_native)
            assert native_started.wait(2)
        finally:
            release.set()
        assert first.result(timeout=2) is None
        assert second.result(timeout=2) == [["owned"]]
    assert {payload["request_id"] for payload in events} == {"permission_1", "permission_2"}
    assert len(events) == 2
    assert state.get_provider_metadata(legacy.STATE_KEY)["counter"] == 2
    assert any(
        item.get("permission_broker", {}).get("note") == "user_allow_once"
        for item in state.transcript
    )


def test_retained_response_channel_owns_ids_across_turn_states_and_brokers(tmp_path):
    replies = PermissionResponseQueue()
    child_asked = threading.Event()
    child_requests, root_requests = [], []
    def child_emit(kind, payload, turn=None):
        if kind == "permission_request":
            child_requests.append(payload)
            child_asked.set()
    def root_emit(kind, payload, turn=None):
        if kind == "permission_request":
            root_requests.append(payload)
            replies.put("once")
            replies.put({"request_id": "permission_stale", "response": "always"})
            replies.put({"request_id": payload["request_id"], "response": "reject"})
    child_state = session(tmp_path, child_emit, replies)
    root_state = session(tmp_path, root_emit, replies)
    child_broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 5}})
    root_broker = child_broker
    with ThreadPoolExecutor(max_workers=1) as pool:
        child = pool.submit(request_stock_question, child_broker, child_state, question())
        try:
            assert child_asked.wait(2)
            assert request_stock_tool_permission(root_broker, root_state, native_request())["behavior"] == "deny"
            assert not child.done()
            assert child_requests[0]["request_id"] != root_requests[0]["request_id"]
            replies.put({"request_id": child_requests[0]["request_id"], "answers": [["owned"]]})
            assert child.result(timeout=2) == [["owned"]]
        finally:
            child_broker.cancel_pending_requests(child_state)
    replies.put({"request_id": child_requests[0]["request_id"], "response": "always"})
    later_state = session(tmp_path, root_emit, replies)
    later_broker = PermissionBroker({"options": {"mode": "prompt", "timeout_s": 5}})
    assert request_stock_tool_permission(later_broker, later_state, native_request("later"))["behavior"] == "deny"
    assert [child_requests[0]["request_id"], *(item["request_id"] for item in root_requests)] == [
        "permission_1", "permission_2", "permission_3"
    ]
