from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.ctrees.executor_hooks import filter_tool_defs_by_allowlist
from agentic_coder_prototype.hooks.manager import build_hook_manager
from agentic_coder_prototype.state.session_state import SessionState


def test_phase14_hook_manager_emits_localize_allowlist_and_snapshot() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt54_e4_live_candidate_a_executor_v1.yaml")
    manager = build_hook_manager(config, "/tmp/workspace")
    snapshot = manager.snapshot()
    session = SessionState("/tmp/workspace", "img", config)

    result = manager.run("pre_turn", {"turn": 1, "tool_allowlist": ["shell_command", "apply_patch"]}, session_state=session, turn=1)

    assert snapshot["family"] == "candidate_a_executor_v1"
    assert result.action == "transform"
    assert result.payload["tool_allowlist"] == ["shell_command"]
    assert session.get_provider_metadata("phase14_executor_current_phase") == "localize"


def test_phase14_hook_manager_advances_patch_and_verify_paths() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt54_e4_live_candidate_a_executor_v1.yaml")
    manager = build_hook_manager(config, "/tmp/workspace")
    session = SessionState("/tmp/workspace", "img", config)

    manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "ok", "exit": 0}},
        session_state=session,
        turn=1,
    )
    second = manager.run("pre_turn", {"turn": 2, "tool_allowlist": []}, session_state=session, turn=2)
    assert second.payload["tool_allowlist"] == ["shell_command", "apply_patch"]

    manager.run(
        "post_tool",
        {"tool_call": {"function": "apply_patch"}, "tool_result": {"ok": True}},
        session_state=session,
        turn=2,
    )
    third = manager.run("pre_turn", {"turn": 3, "tool_allowlist": []}, session_state=session, turn=3)
    assert third.payload["tool_allowlist"] == ["shell_command"]

    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "verified", "exit": 0}},
        session_state=session,
        turn=3,
    )
    fourth = manager.run("pre_turn", {"turn": 4, "tool_allowlist": []}, session_state=session, turn=4)
    assert fourth.payload["tool_allowlist"] == ["mark_task_complete"]


def test_phase14_hook_manager_denies_early_finish() -> None:
    config = load_agent_config("agent_configs/misc/codex_cli_gpt54_e4_live_candidate_a_executor_v1.yaml")
    manager = build_hook_manager(config, "/tmp/workspace")
    session = SessionState("/tmp/workspace", "img", config)

    manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    result = manager.run("post_turn", {"turn": 1, "completed": True}, session_state=session, turn=1)

    assert result.action == "deny"
    assert "VALIDATION_ERROR" in result.payload["validation_message"]


def test_filter_tool_defs_by_allowlist_drops_unlisted_tools() -> None:
    tool_defs = [
        SimpleNamespace(name="shell_command"),
        SimpleNamespace(name="apply_patch"),
        SimpleNamespace(name="mark_task_complete"),
    ]
    filtered, names = filter_tool_defs_by_allowlist(tool_defs, ["shell_command", "mark_task_complete"])
    assert [item.name for item in filtered] == ["shell_command", "mark_task_complete"]
    assert names == ["shell_command", "mark_task_complete"]
