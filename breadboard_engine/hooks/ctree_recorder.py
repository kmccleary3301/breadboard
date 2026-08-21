from __future__ import annotations

from typing import Any, Dict, Optional

from .model import HookResult


class CTreeRecorderHook:
    """Middleware hook that records session events into the C-Tree store."""

    hook_id = "ctree_recorder"
    phases = ("ctree_record",)
    owner = "engine"

    def handles(self, phase: str) -> bool:
        return phase in self.phases

    def run(
        self,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any] = None,
        turn: Optional[int] = None,
        hook_executor: Optional[Any] = None,
    ) -> HookResult:
        if phase != "ctree_record":
            return HookResult(action="allow")
        if session_state is None:
            return HookResult(action="allow", reason="missing_session_state")

        kind: Optional[str] = None
        data: Any = None
        record_turn: Optional[int] = turn
        if isinstance(payload, dict):
            kind = payload.get("kind")
            data = payload.get("payload")
            record_turn = payload.get("turn", record_turn)

        if not kind:
            return HookResult(action="allow", reason="missing_kind")

        try:
            node_id = session_state.ctree_store.record(str(kind), data, turn=record_turn)
            node = session_state.ctree_store.nodes[-1] if session_state.ctree_store.nodes else None
            snapshot = session_state.ctree_store.snapshot()
        except Exception:
            return HookResult(action="allow", reason="ctree_record_failed")

        payload_out: Dict[str, Any] = {"node_id": node_id, "snapshot": snapshot}
        if isinstance(node, dict):
            payload_out["node"] = dict(node)
        elif node is not None:
            payload_out["node"] = node
        return HookResult(action="allow", payload=payload_out)
