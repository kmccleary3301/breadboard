from __future__ import annotations

from typing import Any, Dict

from ..hooks.model import HookResult
from .executor_hooks import build_finish_validation_message
from .branch_receipt_state import (
    begin_branch_receipt_turn,
    current_branch_receipt_allowlist,
    get_branch_receipt_state,
    note_branch_receipt_finish_denial,
    record_branch_receipt_tool_result,
    validate_branch_receipt_finish,
    validate_branch_receipt_turn_progress,
)


def _pseudo_tool_success(tool_name: str, tool_args: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "record_branch_decision":
        branch_mode = str((tool_args or {}).get("branch_mode") or "").strip()
        if branch_mode not in {"edit", "proof"}:
            return {"error": "branch_mode must be 'edit' or 'proof'"}
        return {"ok": True, "action": "record_branch_decision", "branch_mode": branch_mode, "receipt_visible": True}
    if tool_name == "record_proof_receipt":
        if int(state.get("shell_successes") or 0) < 1:
            return {"error": "proof receipt requires at least one shell evidence step"}
        return {"ok": True, "action": "record_proof_receipt", "receipt_visible": True}
    if tool_name == "record_verification_receipt":
        if not bool(state.get("post_patch_shell_seen")):
            return {"error": "verification receipt requires a shell verification step after the patch"}
        return {"ok": True, "action": "record_verification_receipt", "receipt_visible": True}
    return {"error": f"unsupported pseudo tool: {tool_name}"}


class Phase17BranchReceiptHookManager:
    def __init__(self, contract: Dict[str, Any]) -> None:
        self.contract = dict(contract or {})

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": "phase17_branch_receipt_hook_snapshot_v1",
            "enabled": True,
            "family": str(self.contract.get("family") or ""),
            "variant": str(self.contract.get("variant") or ""),
            "support_strategy": str(self.contract.get("support_strategy") or ""),
            "tool_choice": str(self.contract.get("tool_choice") or ""),
            "parallel_tool_calls": self.contract.get("parallel_tool_calls"),
        }

    def run(
        self,
        hook_name: str,
        payload: Dict[str, Any],
        *,
        session_state: Any = None,
        turn: int | None = None,
        hook_executor: Any = None,
    ) -> HookResult:
        del hook_executor
        if session_state is None:
            return HookResult(action="allow")

        if hook_name == "pre_turn":
            state = begin_branch_receipt_turn(session_state, self.contract, turn_index=int(turn or 0))
            allowlist = current_branch_receipt_allowlist(state, self.contract)
            session_state.set_provider_metadata("phase17_branch_receipt_family", self.contract.get("family"))
            return HookResult(
                action="transform",
                payload={
                    "tool_allowlist": allowlist,
                    "provider_tool_choice": str(self.contract.get("tool_choice") or "required"),
                    "parallel_tool_calls": self.contract.get("parallel_tool_calls"),
                    "phase_label": "phase17_branch_receipt_probe",
                },
            )

        if hook_name == "pre_tool":
            state = get_branch_receipt_state(session_state, self.contract)
            allowlist = current_branch_receipt_allowlist(state, self.contract)
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            if tool_name not in allowlist:
                return HookResult(action="deny", reason=f"tool '{tool_name}' is not available in branch-receipt phase")
            return HookResult(action="allow")

        if hook_name == "post_tool":
            state = get_branch_receipt_state(session_state, self.contract)
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            tool_args = dict(tool_call.get("arguments") or {})
            tool_result = dict(payload.get("tool_result") or {})
            if tool_name in {"record_branch_decision", "record_proof_receipt", "record_verification_receipt"}:
                tool_result = _pseudo_tool_success(tool_name, tool_args, state)
                record_branch_receipt_tool_result(
                    session_state,
                    self.contract,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result,
                    turn_index=turn,
                )
                return HookResult(action="transform", payload={"tool_result": tool_result})
            record_branch_receipt_tool_result(
                session_state,
                self.contract,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                turn_index=turn,
            )
            return HookResult(action="allow")

        if hook_name == "post_turn":
            state = get_branch_receipt_state(session_state, self.contract)
            progress = validate_branch_receipt_turn_progress(state, self.contract)
            if not progress.get("allowed"):
                message = str(progress.get("reason") or "branch-receipt progress denied")
                return HookResult(
                    action="deny",
                    reason=message,
                    payload={"validation_message": build_finish_validation_message(message)},
                )
            if bool(payload.get("completed")):
                verdict = validate_branch_receipt_finish(state, self.contract)
                if verdict.get("allowed"):
                    return HookResult(action="allow")
                note_branch_receipt_finish_denial(session_state, self.contract)
                message = str(verdict.get("reason") or "branch-receipt finish denied")
                return HookResult(
                    action="deny",
                    reason=message,
                    payload={"validation_message": build_finish_validation_message(message)},
                )
            return HookResult(action="allow")

        return HookResult(action="allow")
