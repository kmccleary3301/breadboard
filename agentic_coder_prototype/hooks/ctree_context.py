from __future__ import annotations

from typing import Any, Dict, Optional

from ..ctrees.collapse import collapse_ctree
from ..ctrees.compiler import compile_ctree
from ..ctrees.policy import collapse_policy
from ..ctrees.summary import build_ctree_hash_summary
from .model import HookResult


class CTreeContextHook:
    """Before-model hook that refreshes C-Tree metadata for the current session.

    This is safe-by-default and does not mutate provider messages. It updates provider metadata so
    downstream components (TUI, surfaces, future context selection) can read stable hashes.
    """

    hook_id = "ctree_context"
    phases = ("before_model",)
    owner = "engine"
    priority = 50

    def run(
        self,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any] = None,
        turn: Optional[int] = None,
        hook_executor: Optional[Any] = None,
    ) -> HookResult:
        if phase != "before_model":
            return HookResult(action="allow")
        if session_state is None:
            return HookResult(action="allow", reason="missing_session_state")

        store = getattr(session_state, "ctree_store", None)
        if store is None:
            return HookResult(action="allow", reason="missing_ctree_store")

        config = getattr(session_state, "config", None)
        ctree_cfg = (config.get("ctrees") or {}) if isinstance(config, dict) else {}
        compiler_cfg = (ctree_cfg.get("compiler") or {}) if isinstance(ctree_cfg, dict) else {}
        collapse_cfg = (ctree_cfg.get("collapse") or {}) if isinstance(ctree_cfg, dict) else {}
        collapse_target = collapse_cfg.get("target") if isinstance(collapse_cfg, dict) else None
        try:
            collapse_target = int(collapse_target) if collapse_target is not None else None
        except Exception:
            collapse_target = None

        try:
            snapshot = store.snapshot()
            compiler = compile_ctree(store, config=compiler_cfg if isinstance(compiler_cfg, dict) else None)
            policy = collapse_policy(store, target=collapse_target)
            collapse = collapse_ctree(store, policy=policy)
            hash_summary = build_ctree_hash_summary(
                snapshot=snapshot if isinstance(snapshot, dict) else None,
                compiler=compiler if isinstance(compiler, dict) else None,
                collapse=collapse if isinstance(collapse, dict) else None,
                runner=None,
            )
        except Exception:
            return HookResult(action="allow", reason="ctree_context_failed")

        try:
            session_state.set_provider_metadata("ctrees_snapshot", snapshot)
            session_state.set_provider_metadata("ctrees_compiler", compiler)
            session_state.set_provider_metadata("ctrees_collapse", collapse)
            session_state.set_provider_metadata("ctrees_hash_summary", hash_summary)
        except Exception:
            pass

        return HookResult(action="allow")
