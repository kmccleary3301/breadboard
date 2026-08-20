from __future__ import annotations

from typing import Any, Dict

from .branch_receipt_state import get_branch_receipt_state, validate_branch_receipt_finish


_STATE_KEY = "phase18_finish_closure_state"


def _default_state(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "phase18_finish_closure_state_v1",
        "family": str(contract.get("family") or ""),
        "variant": str(contract.get("variant") or ""),
        "support_strategy": str(contract.get("support_strategy") or ""),
        "turns_started": 0,
        "turn_tool_used": False,
        "closure_ready": False,
        "closure_ready_step": None,
        "closure_ready_linger_turns": 0,
        "closure_blocked": True,
        "closure_block_reason": "",
        "closure_ready_evidence": [],
        "accepted_finish": False,
        "accepted_finish_reason": "",
        "finish_request_count": 0,
        "finish_denial_count": 0,
        "finish_denial_reason": "",
        "missing_prerequisite_reason": "",
        "finish_request_observed": False,
        "finish_request_step": None,
        "last_turn_started": None,
    }


def get_finish_closure_state(session_state: Any, contract: Dict[str, Any]) -> Dict[str, Any]:
    state = session_state.get_provider_metadata(_STATE_KEY)
    if not isinstance(state, dict):
        state = _default_state(contract)
        session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def _build_closure_ready_evidence(branch_state: Dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    if bool(branch_state.get("explicit_branch_receipt_observed")):
        evidence.append("branch_receipt")
    if bool(branch_state.get("patch_applied")):
        evidence.append("edit_receipt")
    if bool(branch_state.get("proof_receipt_observed")):
        evidence.append("proof_receipt")
    if bool(branch_state.get("verification_receipt_observed")):
        evidence.append("verification_receipt")
    return evidence


def _update_closure_view(state: Dict[str, Any], branch_state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    verdict = validate_branch_receipt_finish(branch_state, dict(contract.get("branch_contract") or {}))
    closure_ready = bool(verdict.get("allowed"))
    prior_ready = bool(state.get("closure_ready"))
    state["closure_ready"] = closure_ready
    state["closure_blocked"] = not closure_ready
    state["closure_block_reason"] = "" if closure_ready else str(verdict.get("reason") or "")
    state["missing_prerequisite_reason"] = "" if closure_ready else str(verdict.get("reason") or "")
    state["closure_ready_evidence"] = _build_closure_ready_evidence(branch_state)
    if closure_ready and not prior_ready:
        state["closure_ready_step"] = branch_state.get("last_turn_started")
    return state


def begin_finish_closure_turn(session_state: Any, contract: Dict[str, Any], *, turn_index: int) -> Dict[str, Any]:
    state = get_finish_closure_state(session_state, contract)
    if bool(state.get("closure_ready")) and not bool(state.get("accepted_finish")):
        state["closure_ready_linger_turns"] = int(state.get("closure_ready_linger_turns") or 0) + 1
    state["turns_started"] = int(state.get("turns_started") or 0) + 1
    state["turn_tool_used"] = False
    state["last_turn_started"] = int(turn_index)
    branch_state = get_branch_receipt_state(session_state, dict(contract.get("branch_contract") or {}))
    _update_closure_view(state, branch_state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def current_finish_closure_allowlist(session_state: Any, contract: Dict[str, Any]) -> list[str]:
    state = get_finish_closure_state(session_state, contract)
    if bool(state.get("closure_ready")):
        return ["request_finish_receipt"]
    return list((dict(contract.get("tool_allowlists") or {})).get(str(get_branch_receipt_state(session_state, dict(contract.get("branch_contract") or {})).get("current_phase") or "localize")) or [])


def record_finish_closure_tool_result(
    session_state: Any,
    contract: Dict[str, Any],
    *,
    tool_name: str,
    tool_result: Dict[str, Any],
    turn_index: int | None,
) -> Dict[str, Any]:
    state = get_finish_closure_state(session_state, contract)
    state["turn_tool_used"] = True
    success = not bool((tool_result or {}).get("error"))
    if tool_name == "request_finish_receipt":
        state["finish_request_count"] = int(state.get("finish_request_count") or 0) + 1
        state["finish_request_observed"] = True
        if turn_index is not None:
            state["finish_request_step"] = int(turn_index)
        if success and bool((tool_result or {}).get("accepted_finish")):
            state["accepted_finish"] = True
            state["accepted_finish_reason"] = str((tool_result or {}).get("accepted_finish_reason") or "")
        elif not success:
            state["finish_denial_count"] = int(state.get("finish_denial_count") or 0) + 1
            state["finish_denial_reason"] = str((tool_result or {}).get("error") or "")
            state["missing_prerequisite_reason"] = str((tool_result or {}).get("missing_prerequisite_reason") or "")
    branch_state = get_branch_receipt_state(session_state, dict(contract.get("branch_contract") or {}))
    _update_closure_view(state, branch_state, contract)
    session_state.set_provider_metadata(_STATE_KEY, state)
    return state


def validate_finish_closure_request(
    session_state: Any,
    contract: Dict[str, Any],
    *,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    del tool_args
    branch_contract = dict(contract.get("branch_contract") or {})
    branch_state = get_branch_receipt_state(session_state, branch_contract)
    verdict = validate_branch_receipt_finish(branch_state, branch_contract)
    if not bool(verdict.get("allowed")):
        reason = str(verdict.get("reason") or "finish denied")
        return {
            "allowed": False,
            "reason": reason,
            "missing_prerequisite_reason": reason,
        }
    return {
        "allowed": True,
        "accepted_finish_reason": "closure_ready_request_accepted",
    }


def validate_finish_closure_turn_progress(state: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    rules = dict(contract.get("turn_rules") or {})
    if bool(rules.get("deny_no_tool_turn_when_closure_ready", True)) and bool(state.get("closure_ready")):
        if not bool(state.get("turn_tool_used")) and not bool(state.get("accepted_finish")):
            return {
                "allowed": False,
                "reason": "turn denied: closure-ready state requires an immediate finish request",
            }
    return {"allowed": True, "reason": ""}
