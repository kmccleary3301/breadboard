from __future__ import annotations

from agentic_coder_prototype.parity import compare_todo_journal


def test_compare_todo_journal_normalizes_reorder_ids() -> None:
    actual = [
        {"type": "todo.created", "todo_id": "todo_a", "payload": {"todo": {"id": "todo_a", "content": "A"}}},
        {"type": "todo.created", "todo_id": "todo_b", "payload": {"todo": {"id": "todo_b", "content": "B"}}},
        {"type": "todo.reordered", "todo_id": "_all_", "payload": {"order": ["todo_a", "todo_b"]}},
    ]
    expected = [
        {"type": "todo.created", "todo_id": "todo_x", "payload": {"todo": {"id": "todo_x", "content": "A"}}},
        {"type": "todo.created", "todo_id": "todo_y", "payload": {"todo": {"id": "todo_y", "content": "B"}}},
        {"type": "todo.reordered", "todo_id": "_all_", "payload": {"order": ["todo_x", "todo_y"]}},
    ]
    assert compare_todo_journal(actual, expected) == []


def test_compare_todo_journal_detects_reorder_differences() -> None:
    actual = [
        {"type": "todo.created", "todo_id": "todo_a", "payload": {"todo": {"id": "todo_a", "content": "A"}}},
        {"type": "todo.created", "todo_id": "todo_b", "payload": {"todo": {"id": "todo_b", "content": "B"}}},
        {"type": "todo.reordered", "todo_id": "_all_", "payload": {"order": ["todo_a", "todo_b"]}},
    ]
    expected = [
        {"type": "todo.created", "todo_id": "todo_x", "payload": {"todo": {"id": "todo_x", "content": "A"}}},
        {"type": "todo.created", "todo_id": "todo_y", "payload": {"todo": {"id": "todo_y", "content": "B"}}},
        {"type": "todo.reordered", "todo_id": "_all_", "payload": {"order": ["todo_y", "todo_x"]}},
    ]
    mismatches = compare_todo_journal(actual, expected)
    assert any("Todo event" in entry for entry in mismatches)

