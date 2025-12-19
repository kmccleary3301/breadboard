from __future__ import annotations

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner


def test_session_runner_tracks_pending_permissions_for_rehydration() -> None:
    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_1", status=SessionStatus.RUNNING)
    request = SessionCreateRequest(config_path="dummy.yml", task="hi", stream=False)
    runner = SessionRunner(session=session, registry=registry, request=request)

    runner._rehydrate_pending_permissions(
        "permission_request",
        {
            "event_version": 1,
            "request_id": "permission_1",
            "items": [
                {"item_id": "permission_1_item_1", "category": "shell", "pattern": "echo hi", "metadata": {}},
            ],
        },
    )

    pending = session.metadata.get("pending_permissions")
    assert isinstance(pending, list) and pending
    assert pending[0].get("source") == "session"
    assert pending[0].get("request_id") == "permission_1"

    runner._rehydrate_pending_permissions("permission_response", {"request_id": "permission_1", "responses": {"default": "once"}})
    assert "pending_permissions" not in session.metadata


def test_session_runner_tracks_task_permission_requests_separately() -> None:
    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_2", status=SessionStatus.RUNNING)
    request = SessionCreateRequest(config_path="dummy.yml", task="hi", stream=False)
    runner = SessionRunner(session=session, registry=registry, request=request)

    runner._rehydrate_pending_permissions(
        "task_event",
        {
            "kind": "permission_request",
            "sessionId": "task_abc",
            "subagent_type": "general",
            "payload": {
                "event_version": 1,
                "request_id": "permission_2",
                "items": [
                    {"item_id": "permission_2_item_1", "category": "shell", "pattern": "echo hi", "metadata": {}},
                ],
            },
        },
    )

    pending = session.metadata.get("pending_permissions")
    assert isinstance(pending, list) and pending
    assert pending[0].get("source") == "task"
    assert pending[0].get("task_session_id") == "task_abc"
    assert pending[0].get("request_id") == "permission_2"

    runner._rehydrate_pending_permissions(
        "task_event",
        {
            "kind": "permission_response",
            "sessionId": "task_abc",
            "subagent_type": "general",
            "payload": {"request_id": "permission_2", "responses": {"default": "once"}},
        },
    )
    assert "pending_permissions" not in session.metadata

