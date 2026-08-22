from __future__ import annotations

from typing import Any, Dict

from ..hooks.model import HookResult
from .branch_receipt_hooks import _pseudo_tool_success
from .branch_receipt_state import (
    begin_branch_receipt_turn,
    get_branch_receipt_state,
    record_branch_receipt_tool_result,
    validate_branch_receipt_turn_progress,
)
from .executor_hooks import build_finish_validation_message
from .finish_closure_state import (
    begin_finish_closure_turn,
    current_finish_closure_allowlist,
    get_finish_closure_state,
    record_finish_closure_tool_result,
    validate_finish_closure_request,
    validate_finish_closure_turn_progress,
)


class Phase18FinishClosureHookManager:
    def __init__(self, contract: Dict[str, Any]) -> None:
        self.contract = dict(contract or {})
        self.branch_contract = dict(self.contract.get("branch_contract") or {})

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": "phase18_finish_closure_hook_snapshot_v1",
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
            begin_branch_receipt_turn(session_state, self.branch_contract, turn_index=int(turn or 0))
            state = begin_finish_closure_turn(session_state, self.contract, turn_index=int(turn or 0))
            allowlist = current_finish_closure_allowlist(session_state, self.contract)
            session_state.set_provider_metadata("phase18_finish_closure_family", self.contract.get("family"))
            return HookResult(
                action="transform",
                payload={
                    "tool_allowlist": allowlist,
                    "provider_tool_choice": str(self.contract.get("tool_choice") or "required"),
                    "parallel_tool_calls": self.contract.get("parallel_tool_calls"),
                    "phase_label": "phase18_finish_closure_probe",
                    "closure_ready": bool(state.get("closure_ready")),
                },
            )

        if hook_name == "pre_tool":
            allowlist = current_finish_closure_allowlist(session_state, self.contract)
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            if tool_name not in allowlist:
                return HookResult(action="deny", reason=f"tool '{tool_name}' is not available in finish-closure phase")
            return HookResult(action="allow")

        if hook_name == "post_tool":
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            tool_args = dict(tool_call.get("arguments") or {})
            tool_result = dict(payload.get("tool_result") or {})

            if tool_name in {"record_branch_decision", "record_proof_receipt", "record_verification_receipt"}:
                tool_result = _pseudo_tool_success(tool_name, tool_args, get_branch_receipt_state(session_state, self.branch_contract))
                record_branch_receipt_tool_result(
                    session_state,
                    self.branch_contract,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result,
                    turn_index=turn,
                )
                record_finish_closure_tool_result(
                    session_state,
                    self.contract,
                    tool_name=tool_name,
                    tool_result=tool_result,
                    turn_index=turn,
                )
                return HookResult(action="transform", payload={"tool_result": tool_result})

            if tool_name == "request_finish_receipt":
                verdict = validate_finish_closure_request(session_state, self.contract, tool_args=tool_args)
                if bool(verdict.get("allowed")):
                    tool_result = {
                        "ok": True,
                        "action": "complete",
                        "accepted_finish": True,
                        "accepted_finish_reason": str(verdict.get("accepted_finish_reason") or ""),
                        "receipt_visible": True,
                    }
                else:
                    tool_result = {
                        "error": str(verdict.get("reason") or "finish denied"),
                        "missing_prerequisite_reason": str(verdict.get("missing_prerequisite_reason") or ""),
                    }
                record_finish_closure_tool_result(
                    session_state,
                    self.contract,
                    tool_name=tool_name,
                    tool_result=tool_result,
                    turn_index=turn,
                )
                return HookResult(action="transform", payload={"tool_result": tool_result})

            record_branch_receipt_tool_result(
                session_state,
                self.branch_contract,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                turn_index=turn,
            )
            record_finish_closure_tool_result(
                session_state,
                self.contract,
                tool_name=tool_name,
                tool_result=tool_result,
                turn_index=turn,
            )
            return HookResult(action="allow")

        if hook_name == "post_turn":
            branch_state = get_branch_receipt_state(session_state, self.branch_contract)
            progress = validate_branch_receipt_turn_progress(branch_state, self.branch_contract)
            if not progress.get("allowed"):
                message = str(progress.get("reason") or "finish-closure progress denied")
                return HookResult(
                    action="deny",
                    reason=message,
                    payload={"validation_message": build_finish_validation_message(message)},
                )
            finish_state = get_finish_closure_state(session_state, self.contract)
            finish_progress = validate_finish_closure_turn_progress(finish_state, self.contract)
            if not finish_progress.get("allowed"):
                message = str(finish_progress.get("reason") or "finish-closure progress denied")
                return HookResult(
                    action="deny",
                    reason=message,
                    payload={"validation_message": build_finish_validation_message(message)},
                )
            if bool(payload.get("completed")) and not bool(finish_state.get("accepted_finish")):
                message = "finish denied: use request_finish_receipt after the runner declares the task closure-ready"
                return HookResult(
                    action="deny",
                    reason=message,
                    payload={"validation_message": build_finish_validation_message(message)},
                )
            return HookResult(action="allow")

        return HookResult(action="allow")
