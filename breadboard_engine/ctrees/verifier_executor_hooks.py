from __future__ import annotations

from typing import Any, Dict

from ..hooks.model import HookResult
from .executor_hooks import build_finish_validation_message
from .verifier_executor_state import (
    begin_verifier_executor_turn,
    current_verifier_tool_allowlist,
    get_verifier_executor_state,
    note_verifier_finish_denial,
    record_verifier_tool_result,
    validate_verifier_finish_attempt,
    validate_verifier_turn_progress,
)


class Phase15VerifierExecutorHookManager:
    def __init__(self, contract: Dict[str, Any]) -> None:
        self.contract = dict(contract or {})

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": "phase15_verifier_executor_hook_snapshot_v1",
            "enabled": True,
            "family": str(self.contract.get("family") or ""),
            "variant": str(self.contract.get("variant") or ""),
            "support_strategy": str(self.contract.get("support_strategy") or ""),
            "phase_order": list(self.contract.get("phase_order") or []),
            "tool_allowlists": dict(self.contract.get("tool_allowlists") or {}),
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
            state = begin_verifier_executor_turn(session_state, self.contract, turn_index=int(turn or 0))
            allowlist = current_verifier_tool_allowlist(self.contract, state)
            session_state.set_provider_metadata("phase15_verifier_executor_contract", self.contract)
            session_state.set_provider_metadata("phase15_verifier_executor_current_phase", state.get("current_phase"))
            session_state.set_provider_metadata("phase15_verifier_executor_branch_mode", state.get("branch_mode"))
            return HookResult(
                action="transform",
                payload={
                    "tool_allowlist": allowlist,
                    "executor_phase": str(state.get("current_phase") or ""),
                },
            )

        if hook_name == "pre_tool":
            state = get_verifier_executor_state(session_state, self.contract)
            allowlist = current_verifier_tool_allowlist(self.contract, state)
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            if tool_name not in allowlist:
                return HookResult(
                    action="deny",
                    reason=f"tool '{tool_name}' is not available in verifier executor phase '{state.get('current_phase')}'",
                )
            return HookResult(action="allow")

        if hook_name == "post_tool":
            tool_call = dict(payload.get("tool_call") or {})
            tool_result = dict(payload.get("tool_result") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            state = record_verifier_tool_result(
                session_state,
                self.contract,
                tool_name=tool_name,
                tool_result=tool_result,
                turn_index=int(turn) if isinstance(turn, int) else None,
            )
            session_state.set_provider_metadata("phase15_verifier_executor_current_phase", state.get("current_phase"))
            session_state.set_provider_metadata("phase15_verifier_executor_branch_mode", state.get("branch_mode"))
            return HookResult(action="allow")

        if hook_name == "post_turn":
            completed = bool(payload.get("completed"))
            if not completed:
                state = get_verifier_executor_state(session_state, self.contract)
                verdict = validate_verifier_turn_progress(state, self.contract)
                if verdict.get("allowed"):
                    return HookResult(action="allow")
                return HookResult(
                    action="deny",
                    reason=str(verdict.get("reason") or "turn denied by verifier executor"),
                    payload={
                        "validation_message": build_finish_validation_message(
                            str(verdict.get("reason") or "turn denied by verifier executor")
                        )
                    },
                )
            state = get_verifier_executor_state(session_state, self.contract)
            verdict = validate_verifier_finish_attempt(state, self.contract)
            if verdict.get("allowed"):
                return HookResult(action="allow")
            note_verifier_finish_denial(session_state, self.contract)
            return HookResult(
                action="deny",
                reason=str(verdict.get("reason") or "finish denied by verifier executor"),
                payload={
                    "validation_message": build_finish_validation_message(
                        str(verdict.get("reason") or "finish denied by verifier executor")
                    )
                },
            )

        return HookResult(action="allow")
