from types import SimpleNamespace

from agentic_coder_prototype.guardrails import build_guardrail_manager


class DummySessionState:
    def __init__(self):
        self.metadata = {}

    def get_provider_metadata(self, key, default=None):
        return self.metadata.get(key, default)

    def set_provider_metadata(self, key, value):
        self.metadata[key] = value


def make_manager(overrides=None):
    config = {
        "guardrails": {
            "include": ["implementations/guardrails/base_workspace.yaml"],
        }
    }
    if overrides:
        config["guardrails"]["overrides"] = overrides
    return build_guardrail_manager(config)


def test_guardrail_manager_loads_workspace_guard():
    manager = make_manager()
    assert manager is not None
    workspace_handler = manager.get_handler("workspace_context")
    assert workspace_handler is not None
    snapshot = workspace_handler.as_config_snapshot()
    assert snapshot["require_exploration"] is True
    assert snapshot["require_read"] is True


def test_guardrail_override_updates_todo_limit():
    manager = make_manager(
        overrides={
            "todo_rate_limit": {
                "parameters": {"todo_updates_before_progress": 3},
            }
        }
    )
    todo_handler = manager.get_handler("todo_rate_limit")
    assert todo_handler is not None
    assert todo_handler.limit == 3


def test_workspace_guard_blocks_edits_without_read():
    manager = make_manager()
    handler = manager.get_handler("workspace_context")
    session = DummySessionState()
    session.set_provider_metadata("workspace_pending_read", True)
    session.set_provider_metadata("workspace_context_initialized", True)
    result = handler.preflight(session, SimpleNamespace(function="write"))
    assert result is not None
    assert "Read the relevant files" in result


def test_todo_rate_limit_requires_progress():
    manager = make_manager(
        overrides={
            "todo_rate_limit": {
                "parameters": {"todo_updates_before_progress": 0},
            }
        }
    )
    handler = manager.get_handler("todo_rate_limit")
    session = DummySessionState()
    session.set_provider_metadata("plan_mode_disabled", True)
    session.set_provider_metadata("workspace_context_initialized", True)
    session.set_provider_metadata("workspace_pending_todo_update_allowed", False)
    session.set_provider_metadata("todo_updates_since_progress", 0)
    result = handler.preflight(session, SimpleNamespace(function="todo.create"))
    assert result is not None
    assert "Inspect the codebase" in result
