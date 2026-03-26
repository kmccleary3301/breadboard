from __future__ import annotations

from typing import Any, Dict


_STATE_KEY = "phase16_invocation_first_state"


def _initial_state(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    probe_kind = str(session_state.get_provider_metadata("phase16_probe_kind") or "")
    return {
        "family": str(contract.get("family") or ""),
        "probe_kind": probe_kind,
        "shell_calls": 0,
        "write_calls": 0,
        "mark_calls": 0,
        "last_turn_index": None,
    }


def get_invocation_first_state(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = session_state.get_provider_metadata(_STATE_KEY)
    if isinstance(state, dict):
        return dict(state)
    state = _initial_state(session_state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return dict(state)


def begin_invocation_first_turn(session_state: Any, contract: Dict[str, Any], *, turn_index: int) -> Dict[str, Any]:
    state = get_invocation_first_state(session_state, contract)
    state["last_turn_index"] = int(turn_index)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def current_invocation_tool_allowlist(session_state: Any, contract: Dict[str, Any]) -> list[str]:
    state = get_invocation_first_state(session_state, contract)
    probe_kind = str(state.get("probe_kind") or "")
    shell_calls = int(state.get("shell_calls") or 0)
    write_calls = int(state.get("write_calls") or 0)

    if probe_kind == "single_required_shell":
        return ["shell_command"] if shell_calls < 1 else ["mark_task_complete"]
    if probe_kind == "single_required_patch":
        return ["apply_patch"] if write_calls < 1 else ["mark_task_complete"]
    if probe_kind == "shell_then_patch":
        if shell_calls < 1:
            return ["shell_command"]
        if write_calls < 1:
            return ["apply_patch"]
        return ["mark_task_complete"]
    if probe_kind == "invalid_finish_recovery":
        if shell_calls + write_calls < 1:
            return ["shell_command", "apply_patch"]
        return ["mark_task_complete"]
    return ["shell_command", "apply_patch", "mark_task_complete"]


def record_invocation_tool_result(
    session_state: Any,
    contract: Dict[str, Any],
    *,
    tool_name: str,
) -> Dict[str, Any]:
    state = get_invocation_first_state(session_state, contract)
    if tool_name == "shell_command":
        state["shell_calls"] = int(state.get("shell_calls") or 0) + 1
    elif tool_name == "apply_patch":
        state["write_calls"] = int(state.get("write_calls") or 0) + 1
    elif tool_name == "mark_task_complete":
        state["mark_calls"] = int(state.get("mark_calls") or 0) + 1
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_invocation_finish(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = get_invocation_first_state(session_state, contract)
    probe_kind = str(state.get("probe_kind") or "")
    shell_calls = int(state.get("shell_calls") or 0)
    write_calls = int(state.get("write_calls") or 0)

    if probe_kind == "single_required_shell" and shell_calls < 1:
        return {"allowed": False, "reason": "finish denied before required shell invocation"}
    if probe_kind == "single_required_patch" and write_calls < 1:
        return {"allowed": False, "reason": "finish denied before required patch invocation"}
    if probe_kind == "shell_then_patch" and (shell_calls < 1 or write_calls < 1):
        return {"allowed": False, "reason": "finish denied before shell and patch invocation"}
    if probe_kind == "invalid_finish_recovery" and (shell_calls + write_calls) < 1:
        return {"allowed": False, "reason": "finish denied before recovery tool invocation"}
    return {"allowed": True, "reason": ""}
