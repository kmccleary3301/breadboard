from __future__ import annotations
from tests.compaction.test_opencode_stock_tools import omo_dependencies
import asyncio
from pathlib import Path
import queue
from types import SimpleNamespace
from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry, TurnRecord
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.execution.opencode.driver import bridge_stock_interaction
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.state.session_state import SessionState
from tests.compaction.test_opencode_stock_tools import binary
from tests.compaction.test_opencode_stock_tools import hermetic_stock_config
from breadboard_engine.permissions.opencode_questions import request_stock_question
import json
import threading
from breadboard_engine.execution.opencode.session import OpenCodeToolSession
from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
from tests.compaction.test_opencode_stock_tools import connect_auxiliary
from tests.compaction.test_opencode_stock_tools import raw_descriptor
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN_DIGEST
from tests.compaction.test_opencode_stock_omo_groups import LSP_PIN
from tests.compaction.test_opencode_stock_tools import MODEL
from tests.compaction.test_opencode_stock_tools import isolated_environment
from tests.compaction.test_opencode_stock_tools import response
import pytest
import base64
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import yaml
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime import Session as ProductSession
from breadboard_engine.agent import AgenticCoder
from breadboard_engine.api.cli_bridge.events import EventType
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.compaction_lanes.opencode_stock import create_empty_stock_sessions
from breadboard_engine.compaction_lanes.opencode_scenarios import production_settings
from breadboard_engine.compaction_lanes.opencode_scenarios import responses_sse

def runner_at(root: Path):
    record = SessionRecord(session_id="stock-host", status=SessionStatus.RUNNING)
    turn = TurnRecord(input_id="input", turn_id="turn", client_message_id="message",
                      content="task", attachments=(), original_disposition="started", state="active")
    record.turns_by_id[turn.turn_id] = turn
    record.active_turn_id = turn.turn_id
    runner = SessionRunner(session=record, registry=SessionRegistry(state_root=root),
                           request=SessionCreateRequest(config_path="dummy.yml", task="task", stream=False))
    runner._base_config_cache = {}
    runner._permission_queue = queue.Queue()
    return runner


def test_question_answers_round_trip_through_session_control(tmp_path):
    runner = runner_at(tmp_path)
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    events = []

    def emit(kind, payload, turn=None):
        events.append((kind, payload))
        runner._control_controller.update_pending_permissions(kind, payload, source="session")
        if kind == "permission_request":
            delivered = asyncio.run(runner.handle_command("permission_response", {
                "request_id": payload["request_id"], "answers": [["Use stock"]],
            }))
            assert delivered["delivered"]["answers"] == [["Use stock"]]

    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", runner._permission_queue)
    replies = []
    session = SimpleNamespace(api=lambda method, path, body: replies.append((method, path, body)))
    bridge_stock_interaction(SimpleNamespace(permission_broker=broker), state, session, "question.asked", {
        "id": "que_stock", "sessionID": "ses_stock", "questions": [{
            "question": "Which executor?", "header": "Executor", "options": [{"label": "Use stock", "description": "Pinned executable"}],
        }],
    })
    assert replies == [("POST", "/question/que_stock/reply", {"answers": [["Use stock"]]})]
    assert [kind for kind, _ in events] == ["permission_request", "permission_response"]
    assert not runner.session.metadata.get("pending_permissions")


def test_permission_decision_round_trip_through_session_control(tmp_path):
    runner = runner_at(tmp_path)
    broker = PermissionBroker({"options": {"mode": "prompt"}})

    def emit(kind, payload, turn=None):
        runner._control_controller.update_pending_permissions(kind, payload, source="session")
        if kind == "permission_request":
            asyncio.run(runner.handle_command("permission_response", {
                "request_id": payload["request_id"], "response": "once",
            }))

    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", runner._permission_queue)
    replies = []
    bridge_stock_interaction(SimpleNamespace(permission_broker=broker), state,
                             SimpleNamespace(api=lambda method, path, body: replies.append((method, path, body))),
                             "permission.asked", {"id": "per_stock", "permission": "bash", "patterns": ["printf *"],
                                                  "always": ["printf *"], "metadata": {}, "sessionID": "ses_stock"})
    assert replies == [("POST", "/permission/per_stock/reply", {"reply": "once"})]
    assert not runner.session.metadata.get("pending_permissions")


def test_question_shutdown_cancels_registered_and_late_requests(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    events = []

    def emit(kind, payload, turn=None):
        events.append(kind)
        if kind == "permission_request":
            broker.cancel_pending_requests(state)

    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", queue.Queue())
    question = {"id": "que_shutdown", "sessionID": "ses_shutdown", "questions": [
        {"question": "Wait for host?", "header": "Host", "options": []},
    ]}
    assert request_stock_question(broker, state, question) is None
    assert events == ["permission_request", "permission_response"]
    assert request_stock_question(broker, state, question) is None
    assert events == ["permission_request", "permission_response"]


def test_unanswered_stock_permission_does_not_block_root_abort_events(tmp_path, binary):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import pytest
    from breadboard_engine.execution.opencode.session import OpenCodeToolSession
    from breadboard_engine.execution.opencode.wire import StockExecutionError
    from tests.compaction.test_opencode_stock_tools import MODEL
    from tests.compaction.test_opencode_stock_tools import isolated_environment
    from tests.compaction.test_opencode_stock_tools import response

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    asked = threading.Event()
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    state = SessionState(workspace=str(workspace), image="python-dev:latest", config={},
                         event_emitter=lambda kind, payload, turn=None: asked.set() if kind == "permission_request" else None)
    state.set_provider_metadata("permission_queue", queue.Queue())
    owner = SimpleNamespace(permission_broker=broker)
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=5, stock_config=hermetic_stock_config({"lsp": False, "permission": {"bash": "ask"}, "agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "stock"),
    interaction=lambda native, kind, properties: bridge_stock_interaction(owner, state, native, kind, properties))
    session.on_close = lambda native: broker.cancel_pending_requests(state)
    session.current_state = state
    def abort_prompts(native):
        with native.condition:
            request_ids = [frame["properties"]["id"] for frame in native.interaction_threads.values()
                           if frame["properties"]["sessionID"] == native.session_id]
        broker.cancel_stock_requests(state, request_ids)
    session.on_abort = abort_prompts
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            session.user([{"type": "text", "text": "Ask before changing this workspace."}], agent="build")
            submitted = pool.submit(session.submit, response(session, [("call_ask", "bash", {
                "command": "touch forbidden-permission-result", "description": "Requires an operator answer"})]))
            assert asked.wait(5)
            session.finish_run()
            assert session._root_finished()
            assert not (workspace / "forbidden-permission-result").exists()
            with pytest.raises(StockExecutionError):
                submitted.result(timeout=5)
    finally:
        session.close()
    assert not session.interaction_threads


def test_scoped_stock_prompt_cancellation_preserves_child_and_later_prompts(tmp_path):
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    events = []
    def emit(kind, payload, turn=None):
        events.append(kind)
        if kind == "permission_request":
            native_id = payload["metadata"]["stock_request"]["id"]
            if native_id == "que_root":
                broker.cancel_stock_requests(state, [native_id])
            else:
                responses.put({"request_id": payload["request_id"], "answers": [["Answer child"]]})
    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    responses = queue.Queue()
    state.set_provider_metadata("permission_queue", responses)
    def question(identifier):
        return {"id": identifier, "sessionID": "ses_root" if identifier == "que_root" else "ses_child",
                "questions": [{"question": "Answer?", "header": "Prompt", "options": []}]}
    assert request_stock_question(broker, state, question("que_root")) is None
    assert request_stock_question(broker, state, question("que_root")) is None
    assert request_stock_question(broker, state, question("que_child")) == [["Answer child"]]
    assert request_stock_question(broker, state, question("que_later")) == [["Answer child"]]
    assert events == ["permission_request", "permission_response"] * 3


"""An unanswered real child permission cannot stall the stock parent loop."""



@pytest.mark.usefixtures("omo_dependencies")
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
        timeout=30, lsp_root=LSP_PIN, stock_config=hermetic_stock_config({"permission": {"read": "ask"},
                  "agent": {"title": {"disable": True}, "explore": {"permission": {"read": "ask"}}}}), read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
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


"""Two real plugin child permissions must keep their own UI replies."""




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


@pytest.mark.usefixtures("omo_dependencies")
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
        timeout=30, lsp_root=LSP_PIN, stock_config=hermetic_stock_config({"permission": {"read": "ask"}, "agent": {"title": {"disable": True},
                  "explore": {"permission": {"read": "ask"}}}}), read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
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


"""A genuine child keeps its approval channel through real BB runner turns."""




@pytest.mark.usefixtures("omo_dependencies")
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
            "tools.stock_config": hermetic_stock_config({"permission": {"read": "ask"}, "lsp": False,
                                  "agent": {"title": {"disable": True}, "explore": {"permission": {"read": "ask"}}}}),
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
        native_parts = {}
        subscriptions = {}
        urlopen = urllib.request.urlopen
        def subscribe(request, *args, **kwargs):
            connection = urlopen(request, *args, **kwargs)
            if isinstance(request, urllib.request.Request) and request.full_url.endswith("/event"):
                stream = request.full_url
                read_line = connection.readline
                def observe_part(*line_args, **line_kwargs):
                    line = read_line(*line_args, **line_kwargs)
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        part = event.get("properties", {}).get("part", {})
                        if (event["type"] == "message.part.updated" and part.get("type") == "tool"
                                and part.get("callID") == "call_child_read"):
                            with condition:
                                native_parts[(stream, part["sessionID"], part["id"])] = part
                                condition.notify_all()
                    return line
                monkeypatch.setattr(connection, "readline", observe_part)
                subscriptions[stream] = request.get_header("Authorization")
            return connection
        # Install before the native bridge subscribes, so a completion already
        # delivered before wait_for is retained rather than a missed wakeup.
        monkeypatch.setattr(urllib.request, "urlopen", subscribe)
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
            stream = native.url + "/event"
            assert subscriptions[stream] == "Basic " + base64.b64encode(
                ("opencode:" + native.password).encode()).decode()
            before = native.api("GET", "/session/" + child_session_id + "/message")
            child_part = next(part for message in before for part in message["parts"]
                              if part.get("callID") == "call_child_read")
            assert child_part["sessionID"] == child_session_id and child_part["tool"] == "read"
            part_key = (stream, child_session_id, child_part["id"])
            delivered = asyncio.run(runner.handle_command("permission_response", {
                "request_id": child["request_id"], "response": "once",
            }))
            assert delivered["decision"] == "once"
            with native.condition:
                assert native.condition.wait_for(lambda: not native.interaction_threads, timeout=10)
            # permission.ts:38-43 acknowledges the reply before read.ts:45-50
            # resumes execution. Worker exit is not native tool completion.
            with condition:
                assert condition.wait_for(lambda: native_parts.get(part_key, {}).get("state", {}).get(
                    "status") == "completed", timeout=10), native_parts.get(part_key)
            messages = native.api("GET", "/session/" + child_session_id + "/message")
            read = next(part for message in messages for part in message["parts"] if part.get("callID") == "call_child_read")
            assert read["id"] == child_part["id"]
            assert read["messageID"] == native_parts[part_key]["messageID"]
            assert read["state"]["status"] == "completed"
            assert "child-readable content" in read["state"]["output"]
        finally:
            agent.close()


"""Pinned OMO owns background notification creation and parent scheduling."""




@pytest.mark.usefixtures("omo_dependencies")
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
        timeout=20, lsp_root=LSP_PIN, stock_config=hermetic_stock_config({"permission": {"read": "ask"}, "agent": {"title": {"disable": True},
                  "explore": {"model": MODEL, "permission": {"read": "ask"}}}}), read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
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
