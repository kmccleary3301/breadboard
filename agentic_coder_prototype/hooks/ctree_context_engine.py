from __future__ import annotations

from typing import Any, Dict, Optional

from ..ctrees.context_engine import normalize_context_engine_config
from ..ctrees.context_engine import render_context_system_message, select_ctree_context
from .model import HookResult


class CTreeContextEngineHook:
    """Before-model hook that injects a deterministic C-Trees context note.

    This is feature-gated and safe-by-default: no behavior changes unless explicitly enabled.
    """

    hook_id = "ctree_context_engine"
    phases = ("before_model",)
    owner = "engine"
    priority = 60

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

        config = getattr(session_state, "config", None)
        ctrees_cfg = (config.get("ctrees") or {}) if isinstance(config, dict) else {}
        normalized = normalize_context_engine_config(ctrees_cfg if isinstance(ctrees_cfg, dict) else None)
        if not normalized.get("enabled"):
            return HookResult(action="allow")

        mode = str(normalized.get("mode") or "off")
        if mode == "off":
            return HookResult(action="allow")

        if session_state.get_provider_metadata("replay_mode") and not normalized.get("allow_in_replay"):
            return HookResult(action="allow", reason="disabled_in_replay")

        store = getattr(session_state, "ctree_store", None)
        if store is None:
            return HookResult(action="allow", reason="missing_ctree_store")

        original_messages = None
        if isinstance(payload, dict):
            original_messages = payload.get("messages")
        if not isinstance(original_messages, list):
            original_messages = []

        extra_pins = []
        if mode == "replace_messages":
            # Replacement without preserving the initial system prompt is almost always wrong.
            # Pinning is deterministic (append-order) and only affects message inclusion.
            try:
                for node in getattr(store, "nodes", []) or []:
                    if not isinstance(node, dict):
                        continue
                    if str(node.get("kind") or "") != "message":
                        continue
                    payload_node = node.get("payload")
                    if isinstance(payload_node, dict) and payload_node.get("role") == "system":
                        node_id = node.get("id")
                        if isinstance(node_id, str) and node_id:
                            extra_pins.append(node_id)
                        break
            except Exception:
                extra_pins = []

        try:
            selection, compiled = select_ctree_context(
                store,
                selection_config=normalized.get("selection") or {},
                header_config=normalized.get("header") or {},
                collapse_target=(normalized.get("collapse") or {}).get("target"),
                collapse_mode=str((normalized.get("collapse") or {}).get("mode") or "all_but_last"),
                stage=str(normalized.get("stage") or "SPEC"),
                pin_latest=True,
                extra_pin_ids=extra_pins,
            )
        except Exception:
            return HookResult(action="allow", reason="ctree_context_engine_failed")

        meta_summary = {
            "mode": mode,
            "stage": selection.get("stage"),
            "selection_sha256": selection.get("selection_sha256"),
            "selected_count": selection.get("candidate_count"),
            "kept_count": selection.get("kept_count"),
            "dropped_count": selection.get("dropped_count"),
            "collapsed_count": selection.get("collapsed_count"),
        }
        try:
            session_state.set_provider_metadata("ctrees_context_engine", dict(meta_summary))
        except Exception:
            pass

        system_message = render_context_system_message(selection=selection, compiled=compiled, mode=mode)
        if not isinstance(system_message, dict):
            return HookResult(action="allow", reason="ctree_context_engine_no_message")

        if mode == "prepend_system":
            return HookResult(action="transform", payload={"messages": [system_message, *original_messages]})

        if mode == "replace_messages":
            if not normalized.get("dangerously_allow_replace"):
                try:
                    session_state.set_provider_metadata("ctrees_context_engine_warning", "replace_messages_disabled")
                except Exception:
                    pass
                return HookResult(action="transform", payload={"messages": [system_message, *original_messages]})

            mapped = []
            try:
                mapping = getattr(session_state, "_ctree_message_map", None)
                if isinstance(mapping, list):
                    mapped = [entry for entry in mapping if isinstance(entry, dict)]
            except Exception:
                mapped = []

            keep_ids = set(selection.get("kept_ids") or [])
            collapse_ids = set(selection.get("collapsed_ids") or [])
            keep_ids = keep_ids.difference(collapse_ids)
            indices = []
            for entry in mapped:
                node_id = entry.get("node_id")
                provider_index = entry.get("provider_index")
                if not isinstance(node_id, str) or node_id not in keep_ids:
                    continue
                if not isinstance(provider_index, int) or provider_index < 0:
                    continue
                indices.append(provider_index)
            indices = sorted({idx for idx in indices})
            if not indices:
                try:
                    session_state.set_provider_metadata("ctrees_context_engine_warning", "replace_messages_no_mapping")
                except Exception:
                    pass
                return HookResult(action="transform", payload={"messages": [system_message, *original_messages]})

            replacement_messages = []
            for idx in indices:
                if idx < len(original_messages) and isinstance(original_messages[idx], dict):
                    replacement_messages.append(dict(original_messages[idx]))

            if not replacement_messages:
                try:
                    session_state.set_provider_metadata("ctrees_context_engine_warning", "replace_messages_empty")
                except Exception:
                    pass
                return HookResult(action="transform", payload={"messages": [system_message, *original_messages]})

            return HookResult(action="transform", payload={"messages": [system_message, *replacement_messages]})

        return HookResult(action="allow")
