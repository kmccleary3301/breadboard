from __future__ import annotations

import asyncio
from pathlib import Path
import queue
from types import SimpleNamespace

from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry, TurnRecord
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner
from breadboard_engine.execution.opencode_tools import bridge_stock_interaction
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.state.session_state import SessionState
from tests.compaction.test_opencode_stock_tools import binary


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
    assert broker.request_stock_question(state, question) is None
    assert events == ["permission_request", "permission_response"]
    assert broker.request_stock_question(state, question) is None
    assert events == ["permission_request", "permission_response"]


def test_unanswered_stock_permission_does_not_block_root_abort_events(tmp_path, binary):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import pytest
    from breadboard_engine.execution.opencode_tools import OpenCodeToolSession, StockExecutionError
    from tests.compaction.test_opencode_stock_tools import MODEL, isolated_environment, response

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    asked = threading.Event()
    broker = PermissionBroker({"options": {"mode": "prompt"}})
    state = SessionState(workspace=str(workspace), image="python-dev:latest", config={},
                         event_emitter=lambda kind, payload, turn=None: asked.set() if kind == "permission_request" else None)
    state.set_provider_metadata("permission_queue", queue.Queue())
    owner = SimpleNamespace(permission_broker=broker)
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=5,
        stock_config={"lsp": False, "permission": {"bash": "ask"}, "agent": {"title": {"disable": True}}},
        environment=isolated_environment(tmp_path / "stock"),
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
    assert broker.request_stock_question(state, question("que_root")) is None
    assert broker.request_stock_question(state, question("que_root")) is None
    assert broker.request_stock_question(state, question("que_child")) == [["Answer child"]]
    assert broker.request_stock_question(state, question("que_later")) == [["Answer child"]]
    assert events == ["permission_request", "permission_response"] * 3
