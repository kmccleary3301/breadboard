from __future__ import annotations

from typing import Any, Dict, List


_STATE_KEY = "phase17_branch_receipt_state"


def _default_state(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "phase17_branch_receipt_state_v1",
        "family": str(contract.get("family") or ""),
        "variant": str(contract.get("variant") or ""),
        "support_strategy": str(contract.get("support_strategy") or ""),
        "turns_started": 0,
        "current_phase": "localize",
        "branch_mode": "undecided",
        "required_branch_mode": "",
        "closure_mode": "edit_or_proof",
        "required_receipts": [],
        "invalid_finish_trap": False,
        "allow_shell_branch_proxy": False,
        "shell_successes": 0,
        "patch_applied": False,
        "post_patch_shell_seen": False,
        "explicit_branch_receipt_observed": False,
        "proof_receipt_observed": False,
        "verification_receipt_observed": False,
        "finish_attempts": 0,
        "finish_denials": 0,
        "turn_tool_used": False,
        "last_turn_started": None,
        "last_tool_turn": None,
    }


def get_branch_receipt_state(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = session_state.get_provider_metadata(_STATE_KEY)
    if not isinstance(state, dict):
        state = _default_state(contract)
        session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def _task_metadata(session_state: Any) -> Dict[str, Any]:
    return {
        "task_id": str(session_state.get_provider_metadata("phase17_task_id") or ""),
        "closure_mode": str(session_state.get_provider_metadata("phase17_closure_mode") or "edit_or_proof"),
        "required_receipts": [str(item) for item in list(session_state.get_provider_metadata("phase17_required_receipts") or []) if str(item)],
        "invalid_finish_trap": bool(session_state.get_provider_metadata("phase17_invalid_finish_trap")),
        "required_branch_mode": str(session_state.get_provider_metadata("phase17_required_branch_mode") or ""),
        "allow_shell_branch_proxy": bool(session_state.get_provider_metadata("phase17_allow_shell_branch_proxy")),
    }


def _phase_for_state(state: Dict[str, Any], contract: Dict[str, Any]) -> str:
    budgets = dict(contract.get("budgets") or {})
    max_localize_turns = max(int(budgets.get("max_localize_turns") or 1), 1)
    strict_receipt_forcing = bool(((contract.get("finish_rules") or {}).get("strict_receipt_forcing")))

    if int(state.get("turns_started") or 0) <= max_localize_turns:
        return "localize"
    if not bool(state.get("explicit_branch_receipt_observed")):
        return "branch_lock"

    branch_mode = str(state.get("branch_mode") or "undecided")
    if branch_mode == "edit":
        if not bool(state.get("patch_applied")):
            return "commit_edit"
        if strict_receipt_forcing:
            if not bool(state.get("post_patch_shell_seen")):
                return "verify_evidence"
            if not bool(state.get("verification_receipt_observed")):
                return "verify_receipt"
            return "close"
        if not bool(state.get("verification_receipt_observed")):
            return "verify"
        return "close"
    if branch_mode == "proof":
        if not bool(state.get("proof_receipt_observed")):
            return "prove_no_edit"
        return "close"
    return "branch_lock"


def begin_branch_receipt_turn(session_state: Any, contract: Dict[str, Any], *, turn_index: int) -> Dict[str, Any]:
    state = get_branch_receipt_state(session_state, contract)
    state["turns_started"] = int(state.get("turns_started") or 0) + 1
    state["last_turn_started"] = int(turn_index)
    state["turn_tool_used"] = False
    task_meta = _task_metadata(session_state)
    state["closure_mode"] = task_meta["closure_mode"]
    state["required_receipts"] = task_meta["required_receipts"]
    state["invalid_finish_trap"] = task_meta["invalid_finish_trap"]
    state["required_branch_mode"] = task_meta["required_branch_mode"]
    state["allow_shell_branch_proxy"] = task_meta["allow_shell_branch_proxy"]
    state["current_phase"] = _phase_for_state(state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def current_branch_receipt_allowlist(state: Dict[str, Any], contract: Dict[str, Any]) -> List[str]:
    allowlists = dict(contract.get("tool_allowlists") or {})
    phase = str(state.get("current_phase") or "localize")
    return [str(item) for item in list(allowlists.get(phase) or []) if str(item)]


def record_branch_receipt_tool_result(
    session_state: Any,
    contract: Dict[str, Any],
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Dict[str, Any],
    turn_index: int | None,
) -> Dict[str, Any]:
    state = get_branch_receipt_state(session_state, contract)
    success = not bool((tool_result or {}).get("error"))
    if not success:
        session_state.set_provider_metadata(_STATE_KEY, state)
        return state

    state["turn_tool_used"] = True
    if tool_name == "shell_command":
        state["shell_successes"] = int(state.get("shell_successes") or 0) + 1
        if bool(state.get("patch_applied")) and not bool(state.get("verification_receipt_observed")):
            state["post_patch_shell_seen"] = True
    elif tool_name == "apply_patch":
        state["patch_applied"] = True
        state["branch_mode"] = "edit"
    elif tool_name == "record_branch_decision":
        branch_mode = str((tool_args or {}).get("branch_mode") or "").strip()
        if branch_mode in {"edit", "proof"}:
            state["branch_mode"] = branch_mode
            state["explicit_branch_receipt_observed"] = True
    elif tool_name == "record_proof_receipt":
        state["proof_receipt_observed"] = True
    elif tool_name == "record_verification_receipt":
        state["verification_receipt_observed"] = True
    elif tool_name == "mark_task_complete":
        state["finish_attempts"] = int(state.get("finish_attempts") or 0) + 1

    if turn_index is not None:
        state["last_tool_turn"] = int(turn_index)
    state["current_phase"] = _phase_for_state(state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_branch_receipt_finish(state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    del contract
    phase = str(state.get("current_phase") or "localize")
    closure_mode = str(state.get("closure_mode") or "edit_or_proof")
    branch_mode = str(state.get("branch_mode") or "undecided")
    required_branch_mode = str(state.get("required_branch_mode") or "")
    patch_applied = bool(state.get("patch_applied"))
    proof_receipt_observed = bool(state.get("proof_receipt_observed"))
    verification_receipt_observed = bool(state.get("verification_receipt_observed"))
    explicit_branch_receipt_observed = bool(state.get("explicit_branch_receipt_observed"))

    if phase != "close":
        return {"allowed": False, "reason": f"finish denied: branch-receipt executor is still in phase '{phase}'"}
    if not explicit_branch_receipt_observed:
        return {"allowed": False, "reason": "finish denied: explicit branch receipt is still missing"}
    if required_branch_mode and branch_mode != required_branch_mode:
        return {"allowed": False, "reason": f"finish denied: task requires branch_mode={required_branch_mode}"}

    if closure_mode == "edit_required":
        if branch_mode != "edit":
            return {"allowed": False, "reason": "finish denied: task requires the edit branch"}
        if not patch_applied:
            return {"allowed": False, "reason": "finish denied: edit branch requires a write receipt"}
        if not verification_receipt_observed:
            return {"allowed": False, "reason": "finish denied: edit branch requires a verification receipt"}
    elif closure_mode == "proof_required":
        if branch_mode != "proof":
            return {"allowed": False, "reason": "finish denied: task requires the proof branch"}
        if not proof_receipt_observed:
            return {"allowed": False, "reason": "finish denied: proof branch requires an explicit proof receipt"}
    else:
        if branch_mode == "edit":
            if not patch_applied:
                return {"allowed": False, "reason": "finish denied: edit branch requires a write receipt"}
            if not verification_receipt_observed:
                return {"allowed": False, "reason": "finish denied: edit branch requires a verification receipt"}
        elif branch_mode == "proof":
            if not proof_receipt_observed:
                return {"allowed": False, "reason": "finish denied: proof branch requires an explicit proof receipt"}
        else:
            return {"allowed": False, "reason": "finish denied: no valid branch was selected"}

    return {"allowed": True, "reason": ""}


def note_branch_receipt_finish_denial(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = get_branch_receipt_state(session_state, contract)
    state["finish_denials"] = int(state.get("finish_denials") or 0) + 1
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_branch_receipt_turn_progress(state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    turn_rules = dict(contract.get("turn_rules") or {})
    if not bool(turn_rules.get("deny_no_tool_turn_before_close", True)):
        return {"allowed": True, "reason": ""}
    phase = str(state.get("current_phase") or "localize")
    if phase == "close":
        return {"allowed": True, "reason": ""}
    if bool(state.get("turn_tool_used")):
        return {"allowed": True, "reason": ""}
    return {
        "allowed": False,
        "reason": f"turn denied: branch-receipt phase '{phase}' requires at least one allowed tool call before continuing",
    }
