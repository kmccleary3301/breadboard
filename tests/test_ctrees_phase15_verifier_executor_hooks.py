from __future__ import annotations

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.hooks.manager import build_hook_manager
from agentic_coder_prototype.state.session_state import SessionState


def _session(config_path: str) -> tuple[object, SessionState]:
    config = load_agent_config(config_path)
    manager = build_hook_manager(config, "/tmp/workspace")
    session = SessionState("/tmp/workspace", "img", config)
    return manager, session


def test_phase15_hook_manager_emits_localize_allowlist() -> None:
    manager, session = _session(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_verifier_owned_executor_v2_native.yaml"
    )
    session.set_provider_metadata("phase15_closure_mode", "edit_required")
    session.set_provider_metadata("phase15_required_receipts", ["edit", "verification", "finish"])
    result = manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    assert result.action == "transform"
    assert result.payload["tool_allowlist"] == ["shell_command"]
    assert session.get_provider_metadata("phase15_verifier_executor_current_phase") == "localize"


def test_phase15_hook_manager_requires_patch_and_verification_for_edit_tasks() -> None:
    manager, session = _session(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_verifier_owned_executor_v2_native.yaml"
    )
    session.set_provider_metadata("phase15_closure_mode", "edit_required")
    session.set_provider_metadata("phase15_required_receipts", ["edit", "verification", "finish"])

    manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "localized", "exit": 0}},
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
    denied = manager.run("post_turn", {"turn": 3, "completed": True}, session_state=session, turn=3)
    assert denied.action == "deny"
    assert "phase 'verify'" in denied.reason
    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "verified", "exit": 0}},
        session_state=session,
        turn=3,
    )
    fourth = manager.run("pre_turn", {"turn": 4, "tool_allowlist": []}, session_state=session, turn=4)
    assert fourth.payload["tool_allowlist"] == ["mark_task_complete"]


def test_phase15_hook_manager_requires_explicit_proof_receipt_for_proof_tasks() -> None:
    manager, session = _session(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_deterministic_verifier_owned_executor_v2_native.yaml"
    )
    session.set_provider_metadata("phase15_closure_mode", "proof_required")
    session.set_provider_metadata("phase15_required_receipts", ["proof", "finish"])

    manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "localized", "exit": 0}},
        session_state=session,
        turn=1,
    )
    second = manager.run("pre_turn", {"turn": 2, "tool_allowlist": []}, session_state=session, turn=2)
    assert second.payload["tool_allowlist"] == ["shell_command", "apply_patch"]
    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "proof", "exit": 0}},
        session_state=session,
        turn=2,
    )
    third = manager.run("pre_turn", {"turn": 3, "tool_allowlist": []}, session_state=session, turn=3)
    assert third.payload["tool_allowlist"] == ["shell_command"]
    manager.run(
        "post_tool",
        {"tool_call": {"function": "shell_command"}, "tool_result": {"stdout": "proof accepted", "exit": 0}},
        session_state=session,
        turn=3,
    )
    fourth = manager.run("pre_turn", {"turn": 4, "tool_allowlist": []}, session_state=session, turn=4)
    assert fourth.payload["tool_allowlist"] == ["mark_task_complete"]


def test_phase15_hook_manager_rejects_invalid_finish_trap_without_receipt() -> None:
    manager, session = _session(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_verifier_owned_executor_v2_native.yaml"
    )
    session.set_provider_metadata("phase15_closure_mode", "edit_or_proof")
    session.set_provider_metadata("phase15_invalid_finish_trap", True)
    session.set_provider_metadata("phase15_required_receipts", ["finish"])
    manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    denied = manager.run("post_turn", {"turn": 1, "completed": True}, session_state=session, turn=1)
    assert denied.action == "deny"
    assert "VALIDATION_ERROR" in denied.payload["validation_message"]


def test_phase15_hook_manager_denies_no_tool_turn_before_close() -> None:
    manager, session = _session(
        "agent_configs/misc/codex_cli_gpt54mini_e4_live_execution_first_verifier_owned_executor_v2_native.yaml"
    )
    session.set_provider_metadata("phase15_closure_mode", "edit_required")
    session.set_provider_metadata("phase15_required_receipts", ["edit", "verification", "finish"])
    manager.run("pre_turn", {"turn": 1, "tool_allowlist": []}, session_state=session, turn=1)
    denied = manager.run("post_turn", {"turn": 1, "completed": False}, session_state=session, turn=1)
    assert denied.action == "deny"
    assert "requires at least one allowed tool call" in denied.reason
