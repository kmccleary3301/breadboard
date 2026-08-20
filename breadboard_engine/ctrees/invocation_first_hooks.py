from __future__ import annotations

from typing import Any, Dict

from ..hooks.model import HookResult
from .executor_hooks import build_finish_validation_message
from .invocation_first_state import (
    begin_invocation_first_turn,
    current_invocation_tool_allowlist,
    record_invocation_tool_result,
    validate_invocation_finish,
)


class Phase16InvocationFirstHookManager:
    def __init__(self, contract: Dict[str, Any]) -> None:
        self.contract = dict(contract or {})

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": "phase16_invocation_first_hook_snapshot_v1",
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
            state = begin_invocation_first_turn(session_state, self.contract, turn_index=int(turn or 0))
            allowlist = current_invocation_tool_allowlist(session_state, self.contract)
            session_state.set_provider_metadata("phase16_invocation_family", self.contract.get("family"))
            session_state.set_provider_metadata("phase16_invocation_probe_kind", state.get("probe_kind"))
            return HookResult(
                action="transform",
                payload={
                    "tool_allowlist": allowlist,
                    "provider_tool_choice": str(self.contract.get("tool_choice") or "required"),
                    "parallel_tool_calls": self.contract.get("parallel_tool_calls"),
                    "phase_label": "phase16_action_probe",
                },
            )

        if hook_name == "pre_tool":
            allowlist = current_invocation_tool_allowlist(session_state, self.contract)
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            if tool_name not in allowlist:
                return HookResult(
                    action="deny",
                    reason=f"tool '{tool_name}' is not available in invocation-first phase",
                )
            return HookResult(action="allow")

        if hook_name == "post_tool":
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            record_invocation_tool_result(session_state, self.contract, tool_name=tool_name)
            return HookResult(action="allow")

        if hook_name == "post_turn" and bool(payload.get("completed")):
            verdict = validate_invocation_finish(session_state, self.contract)
            if verdict.get("allowed"):
                return HookResult(action="allow")
            message = str(verdict.get("reason") or "finish denied by invocation-first contract")
            return HookResult(
                action="deny",
                reason=message,
                payload={"validation_message": build_finish_validation_message(message)},
            )

        return HookResult(action="allow")
