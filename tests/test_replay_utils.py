from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.replay import resolve_todo_placeholders


def test_resolve_todo_placeholders_with_snapshot(tmp_path: Path) -> None:
    payload = {"id": "{{todo[0].id}}", "note": {"id": "{{todo[1].id}}"}}
    snapshot = {
        "todos": [
            {"id": "todo-123"},
            {"id": "todo-456"},
        ]
    }
    resolved = resolve_todo_placeholders(payload, todo_snapshot=snapshot)
    assert resolved == {"id": "todo-123", "note": {"id": "todo-456"}}


def test_resolve_todo_placeholders_workspace_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    todo_dir = workspace / ".breadboard"
    todo_dir.mkdir(parents=True)
    (todo_dir / "todos.json").write_text(
        '{"todos":[{"id":"todo-aaa"},{"id":"todo-bbb"}]}',
        encoding="utf-8",
    )
    payload = {"id": "{{todo[1].id}}"}
    resolved = resolve_todo_placeholders(payload, workspace_path=workspace)
    assert resolved == {"id": "todo-bbb"}
