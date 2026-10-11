"""Real stock execution across the credential and permission boundaries."""
import json
import os
import queue

import pytest

from breadboard_engine.execution.claude_tools import ClaudeToolSession
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.permissions.claude_permission import request_stock_tool_permission
from breadboard_engine.state.session_state import SessionState


MODEL = "claude-haiku-4-5-20251001"


def test_protected_credentials_cannot_enter_results_or_logs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    store = home / ".breadboard"
    store.mkdir(parents=True)
    secret = store / "credentials.txt"
    secret.write_text("never-relay-protected-secret")
    log = workspace / "exchanges.jsonl"
    stock = ClaudeToolSession(str(workspace), MODEL, timeout=45, permission_mode="bypassPermissions", log_path=log,
        environment={**os.environ, "HOME": str(home), "OPENAI_API_KEY": "never-relay-environment-secret"})
    try:
        denied = stock.execute("Read", {"file_path": str(secret)})
        assert "never-relay-protected-secret" not in json.dumps(denied)
        assert any(block.get("is_error") for block in denied["__claude_tool_result_blocks"])
        shell = stock.execute("Bash", {"command": f'cat "{secret}"; printf "%s" "$OPENAI_API_KEY"'})
        assert "never-relay-protected-secret" not in json.dumps(shell)
        assert "never-relay-environment-secret" not in json.dumps(shell)
        assert "never-relay-protected-secret" not in log.read_text()
        assert "never-relay-environment-secret" not in log.read_text()
    finally:
        stock.close()


def test_headless_default_preserves_stock_write_denial(tmp_path):
    target = tmp_path / "not-approved.txt"
    stock = ClaudeToolSession(str(tmp_path), MODEL, timeout=45)
    try:
        result = stock.execute("Write", {"file_path": str(target), "content": "not approved"})
        assert not target.exists()
        assert any(block.get("is_error") for block in result["__claude_tool_result_blocks"])
    finally:
        stock.close()


def test_stock_permissions_relay_operator_allow_and_reject(tmp_path):
    responses = queue.Queue()
    events = []
    decisions = iter(("once", "reject"))

    def emit(kind, payload, turn=None):
        events.append((kind, payload))
        if kind == "permission_request":
            responses.put({"request_id": payload["request_id"], "response": next(decisions)})

    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", responses)
    # The BB auto_allow option must never auto-answer a genuine stock prompt.
    broker = PermissionBroker({"options": {"mode": "auto_allow"}})
    stock = ClaudeToolSession(str(tmp_path), MODEL, timeout=45,
        permission=lambda request: request_stock_tool_permission(broker, state, request))
    try:
        accepted = tmp_path / "accepted.txt"
        stock.execute("Write", {"file_path": str(accepted), "content": "operator approved"})
        assert accepted.read_text() == "operator approved"
        rejected = tmp_path / "rejected.txt"
        result = stock.execute("Write", {"file_path": str(rejected), "content": "operator rejected"})
        assert not rejected.exists()
        assert any(block.get("is_error") for block in result["__claude_tool_result_blocks"])
        assert [kind for kind, _ in events].count("permission_request") == 2
        assert [kind for kind, _ in events].count("permission_response") == 2
        request = next(payload for kind, payload in events if kind == "permission_request")
        assert request["metadata"]["stock_request"]["tool_name"] == "Write"
    finally:
        stock.close()


@pytest.mark.parametrize("tool_name", ["Write", "Bash"])
def test_stock_always_permission_uses_stock_rule_for_matching_calls(tmp_path, tool_name):
    responses = queue.Queue()
    requests = []
    controls = []

    def emit(kind, payload, turn=None):
        if kind == "permission_request":
            requests.append(payload["metadata"]["stock_request"])
            responses.put({"request_id": payload["request_id"], "response": "always"})

    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", responses)
    broker = PermissionBroker({})

    def permit(request):
        response = request_stock_tool_permission(broker, state, request)
        controls.append(response)
        return response

    stock = ClaudeToolSession(str(tmp_path), MODEL, timeout=45, permission=permit)
    try:
        if tool_name == "Write":
            for name in ("first.txt", "second.txt"):
                target = tmp_path / name
                result = stock.execute("Write", {"file_path": str(target), "content": "always approved"})
                assert result["__mvi_text_output"] == f"File created successfully at: {target}"
                assert target.read_text() == "always approved"
        else:
            target = tmp_path / "shell-created.txt"
            command = f"touch -- '{target}'"
            for _ in range(2):
                result = stock.execute("Bash", {"command": command})
                assert result["__mvi_text_output"] == ""
                assert target.is_file()
        assert len(requests) == len(controls) == 1
        assert controls[0]["updatedPermissions"] == requests[0]["permission_suggestions"]
    finally:
        stock.close()


def test_stock_question_receives_real_updated_input(tmp_path):
    responses = queue.Queue()

    def emit(kind, payload, turn=None):
        if kind == "permission_request":
            original = payload["metadata"]["stock_request"]["input"]
            responses.put({"request_id": payload["request_id"], "response": "once",
                "updated_input": {**original, "answers": {"Which mode?": "Safe"}}})

    state = SessionState(workspace=str(tmp_path), image="python-dev:latest", config={}, event_emitter=emit)
    state.set_provider_metadata("permission_queue", responses)
    broker = PermissionBroker({})
    stock = ClaudeToolSession(str(tmp_path), MODEL, timeout=45,
        permission=lambda request: request_stock_tool_permission(broker, state, request))
    try:
        result = stock.execute("AskUserQuestion", {"questions": [{"question": "Which mode?", "header": "Mode",
            "options": [{"label": "Safe", "description": "Safe mode"}, {"label": "Other", "description": "Other mode"}], "multiSelect": False}]})
        assert '"Which mode?"="Safe"' in result["__mvi_text_output"]
    finally:
        stock.close()


def test_context_forked_agent_is_not_the_inherited_main_loop(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "forked.md").write_text("---\nname: forked\ndescription: Real context-forked stock agent\nforkContext: 'true'\nmodel: inherit\n---\nReturn the provider answer.\n")
    requests = []

    def exchange(body, headers, path):
        requests.append(body)
        return 200, {"Content-Type": "text/event-stream" if body.get("stream") else "application/json"}, ClaudeToolSession.encode_response(
            [{"type": "text", "text": "Real forked Agent provider answer."}], body)

    stock = ClaudeToolSession(str(tmp_path), MODEL, timeout=45, permission_mode="bypassPermissions", auxiliary=exchange)
    try:
        result = stock.execute("Agent", {"description": "test inherited marker", "prompt": "Return the provider answer.", "subagent_type": "forked"})
        assert "Real forked Agent provider answer." in result["__mvi_text_output"]
        assert len(requests) == 1
        assert stock.marker in json.dumps(requests[0]["messages"])
        assert any(record["kind"] == "agent" for record in stock.records)
    finally:
        stock.close()


def test_host_question_answers_reach_native_permission_queue(tmp_path):
    import asyncio
    from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
    from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry, TurnRecord
    from breadboard_engine.api.cli_bridge.session_runner import SessionRunner

    async def route():
        record = SessionRecord(session_id="native-question", status=SessionStatus.RUNNING)
        turn = TurnRecord(input_id="input-test", turn_id="turn-test", client_message_id="message-test",
            content="task", attachments=(), original_disposition="started", state="active")
        record.turns_by_id[turn.turn_id] = turn
        record.active_turn_id = turn.turn_id
        runner = SessionRunner(session=record, registry=SessionRegistry(state_root=tmp_path / "registry"),
            request=SessionCreateRequest(config_path="dummy.yml", task="task", stream=False))
        runner._base_config_cache = {}
        runner._permission_queue = asyncio.Queue()
        original = {"questions": [{"question": "Which mode?"}]}
        runner._control_controller.update_pending_permissions("permission_request", {
            "request_id": "native-question", "items": [], "metadata": {
                "stock_kind": "claude_permission",
                "stock_request": {"tool_name": "AskUserQuestion", "input": original},
            }}, source="session")
        response = await runner.handle_command("respond_permission", {
            "request_id": "native-question", "answers": {"Which mode?": "Safe"},
        })
        delivered = await runner._permission_queue.get()
        assert response["decision"] == "once"
        assert delivered["updated_input"] == {**original, "answers": {"Which mode?": "Safe"}}
        assert delivered["response"] == "once"

    asyncio.run(route())
