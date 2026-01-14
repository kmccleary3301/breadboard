from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .model import HookResult


class HookManager:
    """Lightweight hook/middleware dispatcher."""

    def __init__(self, hooks: Iterable[Any]) -> None:
        self._hooks = [hook for hook in hooks if hook is not None]

    def snapshot(self) -> Dict[str, Any]:
        hooks: List[Dict[str, Any]] = []
        for hook in self._hooks:
            hook_id = getattr(hook, "hook_id", None) or hook.__class__.__name__
            phases = list(getattr(hook, "phases", []) or [])
            hooks.append(
                {
                    "id": str(hook_id),
                    "phases": phases,
                    "owner": getattr(hook, "owner", None),
                }
            )
        return {"hooks": hooks}

    def _handles(self, hook: Any, phase: str) -> bool:
        if hasattr(hook, "handles"):
            try:
                return bool(hook.handles(phase))
            except Exception:
                return False
        phases = getattr(hook, "phases", None)
        if isinstance(phases, (list, tuple, set)):
            return phase in phases or "*" in phases
        return False

    def _run_hook(
        self,
        hook: Any,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any],
        turn: Optional[int],
        hook_executor: Optional[Any],
    ) -> Optional[HookResult]:
        if hasattr(hook, "run"):
            return hook.run(
                phase,
                payload,
                session_state=session_state,
                turn=turn,
                hook_executor=hook_executor,
            )
        if hasattr(hook, "handle"):
            return hook.handle(
                phase,
                payload,
                session_state=session_state,
                turn=turn,
                hook_executor=hook_executor,
            )
        if callable(hook):
            return hook(
                phase,
                payload,
                session_state=session_state,
                turn=turn,
                hook_executor=hook_executor,
            )
        return None

    def run(
        self,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any] = None,
        turn: Optional[int] = None,
        hook_executor: Optional[Any] = None,
    ) -> HookResult:
        action = "allow"
        current_payload = payload
        merged_payload: Dict[str, Any] = {}

        for hook in self._hooks:
            if not self._handles(hook, phase):
                continue
            try:
                result = self._run_hook(
                    hook,
                    phase,
                    current_payload,
                    session_state=session_state,
                    turn=turn,
                    hook_executor=hook_executor,
                )
            except Exception:
                continue
            if not isinstance(result, HookResult):
                continue
            if result.action == "deny":
                return result
            if result.action == "transform":
                action = "transform"
                if isinstance(result.payload, dict):
                    current_payload = dict(result.payload)
                    merged_payload = dict(result.payload)
                continue
            if isinstance(result.payload, dict) and result.payload:
                for key, value in result.payload.items():
                    merged_payload.setdefault(key, value)

        return HookResult(action=action, payload=merged_payload)


def build_hook_manager(config: Dict[str, Any], workspace: str, *, plugin_manifests: Optional[List[Any]] = None) -> Any:
    hooks_cfg = (config or {}).get("hooks") if isinstance(config, dict) else None
    if isinstance(hooks_cfg, dict) and hooks_cfg.get("enabled") is False:
        return None

    hooks: List[Any] = []
    ctree_cfg = (config or {}).get("ctrees") if isinstance(config, dict) else None
    ctree_enabled = True
    if isinstance(ctree_cfg, dict) and ctree_cfg.get("enabled") is False:
        ctree_enabled = False
    if ctree_enabled:
        try:
            from .ctree_recorder import CTreeRecorderHook

            hooks.append(CTreeRecorderHook())
        except Exception:
            pass

    if not hooks:
        return None
    return HookManager(hooks)
