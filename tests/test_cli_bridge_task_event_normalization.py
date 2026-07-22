from __future__ import annotations

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner


def _make_runner(session_id: str = "sess-parent-1") -> SessionRunner:
    registry = SessionRegistry()
    session = SessionRecord(session_id=session_id, status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path="cfg.yaml", task="task")
    return SessionRunner(session=session, registry=registry, request=request)


def test_task_event_normalization_enriches_subagent_graph_fields() -> None:
    runner = _make_runner("sess-parent-42")
    payload = {
        "kind": "subagent_spawned",
        "task_id": "task-1",
        "sessionId": "sess-child-9",
        "subagent_type": "explore",
        "description": "Explore repository surface",
        "status": "running",
    }

    normalized = runner._normalize_task_event(payload)

    assert normalized["child_session_id"] == "sess-child-9"
    assert normalized["subagent_session_id"] == "sess-child-9"
    assert normalized["parent_session_id"] == "sess-parent-42"
    assert normalized["child_session_label"] == "explore"
    assert normalized["subagent_label"] == "explore"
    assert normalized["lane_id"] == "explore"
    assert normalized["lane_label"] == "explore"


def test_task_event_normalization_preserves_explicit_graph_fields() -> None:
    runner = _make_runner("sess-parent-42")
    payload = {
        "kind": "subagent_completed",
        "task_id": "task-2",
        "sessionId": "sess-child-ignored",
        "child_session_id": "sess-child-explicit",
        "child_session_label": "Subagent Explicit",
        "parent_session_id": "sess-parent-explicit",
        "lane_id": "lane-explicit",
        "lane_label": "Lane Explicit",
        "subagent_type": "librarian",
        "status": "completed",
    }

    normalized = runner._normalize_task_event(payload)

    assert normalized["child_session_id"] == "sess-child-explicit"
    assert normalized["parent_session_id"] == "sess-parent-explicit"
    assert normalized["child_session_label"] == "Subagent Explicit"
    assert normalized["lane_id"] == "lane-explicit"
    assert normalized["lane_label"] == "Lane Explicit"


def test_tool_call_and_result_receive_one_correlated_id_when_provider_omits_it() -> None:
    runner = _make_runner()

    translated_call = runner._translate_runtime_event(
        "tool_call",
        {
            "call": {
                "id": None,
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path":"."}'},
            }
        },
        1,
    )
    translated_result = runner._translate_runtime_event(
        "tool_result",
        {"tool": "list_dir_impl", "status": "ok", "error": False, "result": {"entries": []}},
        1,
    )
    translated_duplicate = runner._translate_runtime_event(
        "tool_result",
        {
            "message": {
                "role": "tool",
                "name": "list_dir_impl",
                "content": '{"entries":[]}',
            }
        },
        1,
    )

    assert translated_call is not None
    assert translated_result is not None
    call_payload = translated_call[1]
    result_payload = translated_result[1]
    assert call_payload["call_id"]
    assert result_payload["call_id"] == call_payload["call_id"]
    assert call_payload["tool"] == "list_dir"
    assert result_payload["tool"] == "list_dir_impl"
    assert translated_duplicate is None


def test_todo_event_does_not_consume_pending_tool_result_correlation() -> None:
    runner = _make_runner()

    translated_call = runner._translate_runtime_event(
        "tool_call",
        {
            "call": {
                "id": None,
                "type": "function",
                "function": {"name": "todo.write_board", "arguments": "{}"},
            }
        },
        1,
    )
    translated_todo = runner._translate_runtime_event(
        "todo_event",
        {"todo": {"op": "snapshot", "items": []}},
        1,
    )
    translated_result = runner._translate_runtime_event(
        "tool_result",
        {"tool": "todo.write_board", "status": "ok", "error": False, "result": {}},
        1,
    )

    assert translated_call is not None
    assert translated_todo is not None
    assert translated_result is not None
    assert translated_todo[0].value == "tool_result"
    assert "call_id" not in translated_todo[1]
    assert translated_result[1]["call_id"] == translated_call[1]["call_id"]


def test_permission_and_subagent_events_expose_required_typed_fields() -> None:
    runner = _make_runner("sess-parent-42")

    permission_request = runner._translate_runtime_event(
        "permission_request",
        {
            "request_id": "permission-1",
            "category": "exec",
            "metadata": {"function": "bash", "command": "echo safe"},
        },
        1,
    )
    permission_response = runner._translate_runtime_event(
        "permission_response",
        {"request_id": "permission-1", "decision": "allow"},
        1,
    )
    task_event = runner._translate_runtime_event(
        "task_event",
        {
            "kind": "subagent_spawned",
            "task_id": 7,
            "sessionId": "sess-child-7",
            "subagent_type": "scout",
        },
        1,
    )

    assert permission_request is not None
    assert permission_response is not None
    assert task_event is not None
    assert permission_request[1] == {
        **permission_request[1],
        "request_id": "permission-1",
        "tool": "bash",
        "kind": "Exec",
        "summary": "echo safe",
        "default_scope": "project",
        "rewindable": False,
    }
    assert permission_response[1]["decision"] == "allow"
    assert task_event[1]["task_id"] == "7"
    assert task_event[1]["parent_session_id"] == "sess-parent-42"
    assert task_event[1]["child_session_id"] == "sess-child-7"
