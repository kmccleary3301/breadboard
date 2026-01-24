from __future__ import annotations

from agentic_coder_prototype.state.session_state import SessionState


def test_task_event_not_recorded_by_default() -> None:
    session_state = SessionState(workspace=".", image="img", config={})
    session_state.emit_task_event({"kind": "subagent_spawned", "task_id": "t1"})
    assert session_state.ctree_store.nodes == []


def test_task_event_records_minimal_payload_when_enabled() -> None:
    config = {"ctrees": {"record_task_events": {"enabled": True}}}
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state.emit_task_event(
        {
            "kind": "subagent_completed",
            "task_id": "t1",
            "parent_task_id": "root",
            "tree_path": "root/t1",
            "depth": 1,
            "priority": "high",
            "subagent_type": "codex",
            "status": "completed",
            "sessionId": "job-123",
            "artifact": {"path": ".breadboard/subagents/t1.json"},
            # Should never be persisted into the C-Tree node payload.
            "ctree_snapshot": {"big": "payload"},
            "output_excerpt": "sk-abcdef0123456789abcdef0123456789",
        }
    )

    assert session_state.ctree_store.nodes
    node = session_state.ctree_store.nodes[-1]
    assert node.get("kind") == "task_event"
    payload = node.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("kind") == "subagent_completed"
    assert payload.get("task_id") == "task_0001"
    assert payload.get("parent_task_id") == "root"
    assert payload.get("tree_path") == "root/task_0001"
    assert payload.get("depth") == 1
    assert payload.get("priority") == "high"
    assert payload.get("subagent_type") == "codex"
    assert payload.get("status") == "completed"
    assert "session_id" not in payload
    assert payload.get("artifact_path") == ".breadboard/subagents/task_0001.json"
    assert "ctree_snapshot" not in payload
    assert "output_excerpt" not in payload


def test_task_event_aliasing_is_replay_stable_across_raw_ids() -> None:
    config = {"ctrees": {"record_task_events": {"enabled": True}}}

    a = SessionState(workspace=".", image="img", config=config)
    a.emit_task_event(
        {
            "kind": "subagent_spawned",
            "task_id": "raw-a",
            "parent_task_id": "root",
            "tree_path": "root/raw-a",
            "artifact": {"path": ".breadboard/subagents/raw-a.json"},
        }
    )

    b = SessionState(workspace=".", image="img", config=config)
    b.emit_task_event(
        {
            "kind": "subagent_spawned",
            "task_id": "raw-b",
            "parent_task_id": "root",
            "tree_path": "root/raw-b",
            "artifact": {"path": ".breadboard/subagents/raw-b.json"},
        }
    )

    assert a.ctree_store.hashes() == b.ctree_store.hashes()
    assert [node.get("payload") for node in a.ctree_store.nodes] == [node.get("payload") for node in b.ctree_store.nodes]


def test_task_event_can_emit_subagent_node_once_per_task() -> None:
    config = {"ctrees": {"record_task_events": {"enabled": True, "include_subagent_nodes": True}}}
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state.emit_task_event(
        {
            "kind": "subagent_spawned",
            "task_id": "raw-task",
            "parent_task_id": "root",
            "tree_path": "root/raw-task",
            "subagent_type": "codex",
            "status": "running",
        }
    )
    session_state.emit_task_event(
        {
            "kind": "subagent_completed",
            "task_id": "raw-task",
            "parent_task_id": "root",
            "tree_path": "root/raw-task",
            "subagent_type": "codex",
            "status": "completed",
        }
    )

    kinds = [node.get("kind") for node in session_state.ctree_store.nodes]
    assert "subagent" in kinds
    subagents = [node for node in session_state.ctree_store.nodes if node.get("kind") == "subagent"]
    assert len(subagents) == 1
    payload = subagents[0].get("payload") or {}
    assert payload.get("task_id") == "task_0001"
    assert payload.get("parent_task_id") == "root"


def test_task_event_respects_explicit_turn_override() -> None:
    config = {"ctrees": {"record_task_events": {"enabled": True}}}
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state._active_turn_index = 7
    session_state.emit_task_event({"kind": "subagent_spawned", "task_id": "t1", "turn": 2})

    node = session_state.ctree_store.nodes[-1]
    assert node.get("kind") == "task_event"
    assert node.get("turn") == 2


def test_task_event_async_completion_injects_at_turn_boundary() -> None:
    config = {"ctrees": {"record_task_events": {"enabled": True}}}
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state._active_turn_index = 5
    session_state.emit_task_event({"kind": "subagent_completed", "task_id": "t1", "turn": 1})

    node = session_state.ctree_store.nodes[-1]
    assert node.get("kind") == "task_event"
    assert node.get("turn") == 1
