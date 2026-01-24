from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.guardrail_coordinator import GuardrailCoordinator


class DummyTodoManager:
    def __init__(self, *, has_todos: bool) -> None:
        self._has_todos = has_todos

    def snapshot(self):
        if self._has_todos:
            return {"todos": [{"title": "t1", "status": "todo"}]}
        return {"todos": []}


class DummySessionState:
    def __init__(self, *, has_todos: bool) -> None:
        self._manager = DummyTodoManager(has_todos=has_todos)

    def get_todo_manager(self):
        return self._manager


def test_todo_guard_blocks_side_effect_calls_without_todos() -> None:
    coordinator = GuardrailCoordinator({"features": {"todos": {"enabled": True, "strict": True}}})
    session = DummySessionState(has_todos=False)

    for fn in ("bash", "patch", "run_shell", "apply_unified_patch", "write"):
        reason = coordinator.todo_guard_preflight(session, SimpleNamespace(function=fn), current_mode="plan")
        assert reason and "todo.create" in reason

    assert coordinator.todo_guard_preflight(session, SimpleNamespace(function="read"), current_mode="plan") is None


def test_todo_guard_allows_side_effect_calls_with_todos() -> None:
    coordinator = GuardrailCoordinator({"features": {"todos": {"enabled": True, "strict": True}}})
    session = DummySessionState(has_todos=True)
    assert coordinator.todo_guard_preflight(session, SimpleNamespace(function="bash"), current_mode="plan") is None
