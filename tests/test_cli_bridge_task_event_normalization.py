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
