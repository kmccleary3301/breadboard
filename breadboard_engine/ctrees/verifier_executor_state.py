from __future__ import annotations

from typing import Any, Dict, List


_STATE_KEY = "phase15_verifier_executor_state"


def _default_state(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "phase15_verifier_executor_state_v1",
        "family": str(contract.get("family") or ""),
        "variant": str(contract.get("variant") or ""),
        "support_strategy": str(contract.get("support_strategy") or ""),
        "turns_started": 0,
        "current_phase": "localize",
        "branch_mode": "undecided",
        "closure_mode": "edit_or_proof",
        "required_receipts": [],
        "invalid_finish_trap": False,
        "shell_successes": 0,
        "patch_applied": False,
        "patch_turn": None,
        "proof_receipt_observed": False,
        "verification_receipt_observed": False,
        "finish_attempts": 0,
        "finish_denials": 0,
        "reentry_required": False,
        "last_turn_started": None,
        "turn_tool_used": False,
        "last_tool_turn": None,
    }


def get_verifier_executor_state(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = session_state.get_provider_metadata(_STATE_KEY)
    if not isinstance(state, dict):
        state = _default_state(contract)
        session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def _task_metadata(session_state: Any) -> Dict[str, Any]:
    return {
        "task_id": str(session_state.get_provider_metadata("phase15_task_id") or ""),
        "closure_mode": str(session_state.get_provider_metadata("phase15_closure_mode") or "edit_or_proof"),
        "invalid_finish_trap": bool(session_state.get_provider_metadata("phase15_invalid_finish_trap")),
        "required_receipts": list(session_state.get_provider_metadata("phase15_required_receipts") or []),
    }


def _phase_for_state(state: Dict[str, Any], contract: Dict[str, Any]) -> str:
    budgets = dict(contract.get("budgets") or {})
    max_localize_turns = max(int(budgets.get("max_localize_turns") or 1), 1)
    max_branch_turns = max(int(budgets.get("max_branch_turns") or 1), 1)
    max_commit_turns = max(int(budgets.get("max_commit_turns") or 1), 1)

    if bool(state.get("reentry_required")):
        return "commit_edit" if str(state.get("branch_mode") or "") == "edit" else "prove_no_edit"
    if int(state.get("turns_started") or 0) <= max_localize_turns:
        return "localize"
    if str(state.get("branch_mode") or "undecided") == "undecided":
        if int(state.get("turns_started") or 0) <= (max_localize_turns + max_branch_turns):
            return "branch"
    elif str(state.get("branch_mode")) == "edit":
        if not bool(state.get("patch_applied")) and int(state.get("turns_started") or 0) <= (
            max_localize_turns + max_branch_turns + max_commit_turns
        ):
            return "commit_edit"
        if bool(state.get("patch_applied")) and not bool(state.get("verification_receipt_observed")):
            return "verify"
    elif str(state.get("branch_mode")) == "proof":
        if not bool(state.get("proof_receipt_observed")):
            return "prove_no_edit"
    if str(state.get("branch_mode") or "undecided") == "undecided":
        return "branch"
    if str(state.get("branch_mode")) == "edit" and not bool(state.get("verification_receipt_observed")):
        return "verify"
    return "close"


def begin_verifier_executor_turn(session_state: Any, contract: Dict[str, Any], *, turn_index: int) -> Dict[str, Any]:
    state = get_verifier_executor_state(session_state, contract)
    state["turns_started"] = int(state.get("turns_started") or 0) + 1
    state["last_turn_started"] = int(turn_index)
    task_meta = _task_metadata(session_state)
    state["closure_mode"] = task_meta["closure_mode"]
    state["required_receipts"] = task_meta["required_receipts"]
    state["invalid_finish_trap"] = task_meta["invalid_finish_trap"]
    state["current_phase"] = _phase_for_state(state, contract)
    state["turn_tool_used"] = False
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def current_verifier_tool_allowlist(contract: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
    allowlists = dict(contract.get("tool_allowlists") or {})
    phase = str(state.get("current_phase") or "localize")
    return [str(name) for name in list(allowlists.get(phase) or []) if str(name)]


def record_verifier_tool_result(
    session_state: Any,
    contract: Dict[str, Any],
    *,
    tool_name: str,
    tool_result: Dict[str, Any],
    turn_index: int | None,
) -> Dict[str, Any]:
    state = get_verifier_executor_state(session_state, contract)
    success = not bool((tool_result or {}).get("error"))
    phase = str(state.get("current_phase") or "localize")
    state["turn_tool_used"] = True

    if success and tool_name == "shell_command":
        state["shell_successes"] = int(state.get("shell_successes") or 0) + 1
        if phase == "branch":
            if str(state.get("closure_mode") or "edit_or_proof") == "proof_required":
                state["branch_mode"] = "proof"
            elif str(state.get("closure_mode") or "edit_or_proof") == "edit_required":
                state["branch_mode"] = "edit"
        elif phase == "prove_no_edit":
            state["proof_receipt_observed"] = True
        elif phase == "verify":
            state["verification_receipt_observed"] = True
            state["reentry_required"] = False
    elif success and tool_name == "apply_patch":
        state["branch_mode"] = "edit"
        state["patch_applied"] = True
        state["proof_receipt_observed"] = False
        state["reentry_required"] = False
        if turn_index is not None:
            state["patch_turn"] = int(turn_index)
    elif tool_name == "mark_task_complete":
        state["finish_attempts"] = int(state.get("finish_attempts") or 0) + 1

    state["current_phase"] = _phase_for_state(state, contract)
    if turn_index is not None:
        state["last_tool_turn"] = int(turn_index)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_verifier_finish_attempt(state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    phase = str(state.get("current_phase") or "localize")
    closure_mode = str(state.get("closure_mode") or "edit_or_proof")
    patch_applied = bool(state.get("patch_applied"))
    proof_receipt_observed = bool(state.get("proof_receipt_observed"))
    verification_receipt_observed = bool(state.get("verification_receipt_observed"))
    invalid_finish_trap = bool(state.get("invalid_finish_trap"))

    if phase != "close":
        return {"allowed": False, "reason": f"finish denied: verifier executor is still in phase '{phase}'"}

    if closure_mode == "edit_required":
        if not patch_applied:
            return {"allowed": False, "reason": "finish denied: task requires an edit receipt before completion"}
        if not verification_receipt_observed:
            return {"allowed": False, "reason": "finish denied: edit path requires a verification receipt"}
    elif closure_mode == "proof_required":
        if not proof_receipt_observed:
            return {"allowed": False, "reason": "finish denied: task requires an explicit proof receipt"}
    else:
        if not patch_applied and not proof_receipt_observed:
            return {
                "allowed": False,
                "reason": "finish denied: task requires either an edit receipt or an explicit proof receipt",
            }
        if patch_applied and not verification_receipt_observed:
            return {"allowed": False, "reason": "finish denied: edit path requires a verification receipt"}

    if invalid_finish_trap and not patch_applied and not proof_receipt_observed:
        return {"allowed": False, "reason": "finish denied: invalid finish trap requires a valid receipt path"}

    return {"allowed": True, "reason": ""}


def note_verifier_finish_denial(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = get_verifier_executor_state(session_state, contract)
    state["finish_denials"] = int(state.get("finish_denials") or 0) + 1
    if str(state.get("branch_mode") or "undecided") == "edit" and bool(state.get("patch_applied")) and not bool(
        state.get("verification_receipt_observed")
    ):
        state["reentry_required"] = True
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_verifier_turn_progress(state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
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
        "reason": f"turn denied: verifier executor phase '{phase}' requires at least one allowed tool call before continuing",
    }
