from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..hooks.model import HookResult
from .executor_state import (
    begin_executor_turn,
    current_tool_allowlist,
    get_executor_state,
    note_finish_denial,
    record_executor_tool_result,
    validate_finish_attempt,
)


def filter_tool_defs_by_allowlist(tool_defs: List[Any], allowlist: List[str]) -> Tuple[List[Any], List[str]]:
    allowed = [str(name) for name in list(allowlist or []) if str(name)]
    if not allowed:
        return [], []
    filtered = [
        definition
        for definition in list(tool_defs or [])
        if getattr(definition, "name", None) in allowed
    ]
    present = [str(getattr(definition, "name", "")) for definition in filtered if getattr(definition, "name", None)]
    return filtered, present


def build_finish_validation_message(reason: str) -> str:
    return f"<VALIDATION_ERROR>\n{reason}\n</VALIDATION_ERROR>"


class Phase14ExecutorHookManager:
    def __init__(self, contract: Dict[str, Any]) -> None:
        self.contract = dict(contract or {})

    def snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": "phase14_executor_hook_snapshot_v1",
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
            state = begin_executor_turn(session_state, self.contract, turn_index=int(turn or 0))
            allowlist = current_tool_allowlist(self.contract, state)
            session_state.set_provider_metadata("phase14_executor_contract", self.contract)
            session_state.set_provider_metadata("phase14_executor_current_phase", state.get("current_phase"))
            return HookResult(
                action="transform",
                payload={
                    "tool_allowlist": allowlist,
                    "executor_phase": str(state.get("current_phase") or ""),
                },
            )

        if hook_name == "pre_tool":
            state = get_executor_state(session_state, self.contract)
            allowlist = current_tool_allowlist(self.contract, state)
            tool_call = dict(payload.get("tool_call") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            if tool_name not in allowlist:
                return HookResult(
                    action="deny",
                    reason=f"tool '{tool_name}' is not available in executor phase '{state.get('current_phase')}'",
                )
            return HookResult(action="allow")

        if hook_name == "post_tool":
            tool_call = dict(payload.get("tool_call") or {})
            tool_result = dict(payload.get("tool_result") or {})
            tool_name = str(tool_call.get("function") or tool_call.get("name") or "")
            state = record_executor_tool_result(
                session_state,
                self.contract,
                tool_name=tool_name,
                tool_result=tool_result,
                turn_index=int(turn) if isinstance(turn, int) else None,
            )
            session_state.set_provider_metadata("phase14_executor_current_phase", state.get("current_phase"))
            return HookResult(action="allow")

        if hook_name == "post_turn":
            completed = bool(payload.get("completed"))
            if not completed:
                return HookResult(action="allow")
            state = get_executor_state(session_state, self.contract)
            verdict = validate_finish_attempt(state, self.contract)
            if verdict.get("allowed"):
                return HookResult(action="allow")
            note_finish_denial(session_state, self.contract)
            return HookResult(
                action="deny",
                reason=str(verdict.get("reason") or "finish denied by executor"),
                payload={
                    "validation_message": build_finish_validation_message(
                        str(verdict.get("reason") or "finish denied by executor")
                    )
                },
            )

        return HookResult(action="allow")
