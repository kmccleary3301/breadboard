from __future__ import annotations

from typing import Any, Dict


_STATE_KEY = "phase14_executor_state"


def _default_state(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "phase14_executor_state_v1",
        "family": str(contract.get("family") or ""),
        "variant": str(contract.get("variant") or ""),
        "support_strategy": str(contract.get("support_strategy") or ""),
        "turns_started": 0,
        "current_phase": "localize",
        "shell_successes": 0,
        "patch_applied": False,
        "patch_turn": None,
        "verify_evidence_turns": 0,
        "finish_attempts": 0,
        "finish_denials": 0,
        "last_turn_started": None,
    }


def get_executor_state(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = session_state.get_provider_metadata(_STATE_KEY)
    if not isinstance(state, dict):
        state = _default_state(contract)
        session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def _phase_for_state(state: Dict[str, Any], contract: Dict[str, Any]) -> str:
    budgets = dict(contract.get("budgets") or {})
    max_localize_turns = max(int(budgets.get("max_localize_turns") or 1), 1)
    max_commit_turns = max(int(budgets.get("max_commit_turns") or 1), 1)
    allow_no_edit_close = bool((contract.get("finish_rules") or {}).get("allow_no_edit_close", True))

    if bool(state.get("patch_applied")):
        return "close" if int(state.get("verify_evidence_turns") or 0) > 0 else "verify"
    if allow_no_edit_close and int(state.get("verify_evidence_turns") or 0) > 0:
        return "close"
    if int(state.get("shell_successes") or 0) <= 0:
        return "localize"
    if int(state.get("turns_started") or 0) <= (max_localize_turns + max_commit_turns):
        return "commit_edit"
    return "verify"


def begin_executor_turn(session_state: Any, contract: Dict[str, Any], *, turn_index: int) -> Dict[str, Any]:
    state = get_executor_state(session_state, contract)
    state["turns_started"] = int(state.get("turns_started") or 0) + 1
    state["last_turn_started"] = int(turn_index)
    state["current_phase"] = _phase_for_state(state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def current_tool_allowlist(contract: Dict[str, Any], state: Dict[str, Any]) -> list[str]:
    allowlists = dict(contract.get("tool_allowlists") or {})
    phase = str(state.get("current_phase") or "localize")
    return [str(name) for name in list(allowlists.get(phase) or []) if str(name)]


def record_executor_tool_result(
    session_state: Any,
    contract: Dict[str, Any],
    *,
    tool_name: str,
    tool_result: Dict[str, Any],
    turn_index: int | None,
) -> Dict[str, Any]:
    state = get_executor_state(session_state, contract)
    success = not bool((tool_result or {}).get("error"))
    phase = str(state.get("current_phase") or "localize")

    if success and tool_name == "shell_command":
        state["shell_successes"] = int(state.get("shell_successes") or 0) + 1
        if phase == "verify":
            state["verify_evidence_turns"] = int(state.get("verify_evidence_turns") or 0) + 1
    elif success and tool_name == "apply_patch":
        state["patch_applied"] = True
        if turn_index is not None:
            state["patch_turn"] = int(turn_index)
    elif tool_name == "mark_task_complete":
        state["finish_attempts"] = int(state.get("finish_attempts") or 0) + 1

    state["current_phase"] = _phase_for_state(state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_finish_attempt(state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    finish_rules = dict(contract.get("finish_rules") or {})
    phase = str(state.get("current_phase") or "localize")
    verify_evidence = int(state.get("verify_evidence_turns") or 0)
    patch_applied = bool(state.get("patch_applied"))
    allow_no_edit_close = bool(finish_rules.get("allow_no_edit_close", True))

    if phase != "close":
        return {
            "allowed": False,
            "reason": f"finish denied: executor is still in phase '{phase}'",
        }
    if patch_applied and verify_evidence <= 0:
        return {
            "allowed": False,
            "reason": "finish denied: patch path requires at least one verification command before completion",
        }
    if not patch_applied and not allow_no_edit_close:
        return {
            "allowed": False,
            "reason": "finish denied: no-edit closure is disabled for this executor family",
        }
    if not patch_applied and verify_evidence <= 0:
        return {
            "allowed": False,
            "reason": "finish denied: no-edit closure requires one grounding verification command before completion",
        }
    return {"allowed": True, "reason": ""}


def note_finish_denial(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = get_executor_state(session_state, contract)
    state["finish_denials"] = int(state.get("finish_denials") or 0) + 1
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state
