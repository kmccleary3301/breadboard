"""Session execution helpers for the CLI bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import os
import shutil
import time
import uuid, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, List, Tuple

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.auth.enforcer import apply_dotted_overrides
from agentic_coder_prototype.compilation.effective_operation_policy import policy_pack_for_config_authority
from agentic_coder_prototype.checkpointing.checkpoint_manager import CheckpointManager
from agentic_coder_prototype.skills.registry import (
    load_skills,
    build_skill_catalog,
    normalize_skill_selection,
    apply_skill_selection,
)
from agentic_coder_prototype.plugins.loader import discover_plugin_manifests, plugin_snapshot
from agentic_coder_prototype.guardrail import GuardrailCoordinator
from agentic_coder_prototype.permissions import (
    build_permission_overrides,
    load_permission_rules,
    upsert_permission_rule,
)
from agentic_coder_prototype.todo import TodoStore
from agentic_coder_prototype.todo.projection import project_store_snapshot_to_tui_envelope
from agentic_coder_prototype.state.session_state import (
    AUDIT_ONLY_RUNTIME_EVENT_TYPES,
    CANONICAL_KERNEL_EVENT_TYPES,
    PROJECTION_ONLY_RUNTIME_EVENT_TYPES,
)

from .events import EventType, SessionEvent
from .event_normalization import normalize_task_event_payload
from .models import SessionCreateRequest, SessionStatus
from .registry import SessionRecord, SessionRegistry

logger = logging.getLogger(__name__)

AgentFactory = Callable[[str, Optional[str], Optional[Dict[str, Any]]], Any]

_PERMISSION_ALIASES = {alias: decision for decision, aliases in {
    "once": "once allow approve approved ok okay yes y allow-once allow_once", "always": "always allow-always allow_always",
    "reject": "reject deny denied no n deny-once deny_once deny-always deny_always deny-stop deny_stop",
}.items() for alias in aliases.split()}
def _permission_response_tokens(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [token for nested in value.values() for token in _permission_response_tokens(nested)]
    return [value.strip().lower()] if isinstance(value, str) and value.strip() else []

def _canonical_permission_resolution(response: Any, responses: Any) -> str:
    tokens = _permission_response_tokens(responses if isinstance(responses, dict) else response)
    values = [_PERMISSION_ALIASES.get(token, token) for token in tokens]
    if not values or any(value not in {"once", "always", "reject"} for value in values): raise ValueError("permission response contains no valid decisions")
    return "reject" if "reject" in values else "always" if all(value == "always" for value in values) else "once"

def _canonical_permission_responses(responses: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _canonical_permission_responses(value) if isinstance(value, dict)
            else _canonical_permission_resolution(value, None) for key, value in responses.items()}

# These runtime event types are treated as kernel-owned event families and are
# bridged through mostly as-is, subject to payload normalization.
KERNEL_PASSTHROUGH_RUNTIME_EVENT_TYPES = {
    "assistant_message",
    "user_message",
    "tool_call",
    "tool.result",
    "tool_result",
    "todo_event",
    "permission_request",
    "permission_response",
    "ctree_node",
    "ctree_snapshot",
    "task_event",
}

# These runtime event types exist primarily for live client streaming and are
# not yet part of the shared kernel truth surface.
BRIDGE_STREAM_ONLY_RUNTIME_EVENT_TYPES = {
    "stream.gap",
    "assistant.message.start",
    "assistant.message.delta",
    "assistant.message.end",
    "assistant.reasoning.delta",
    "assistant.thought_summary.delta",
    "tool.exec.start",
    "tool.exec.stdout.delta",
    "tool.exec.stderr.delta",
    "tool.exec.end",
    "assistant_delta",
}

# These are host-facing lifecycle/control artifacts emitted by the bridge or
# session orchestration layer rather than the kernel contract itself.
BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES = {
    "conversation.compaction.start",
    "conversation.compaction.end",
    "checkpoint_list",
    "checkpoint_restored",
    "skills_catalog",
    "skills_selection",
    "warning",
    "reward_update",
    "limits_update",
    "completion",
    "log_link",
    "error",
    "run_finished",
}

RuntimeEventContract = Dict[str, Optional[str]]
TranslatedRuntimeEvent = Tuple[EventType, Dict[str, Any], Optional[int], RuntimeEventContract]

def _default_runtime_event_contract(event_type: str) -> RuntimeEventContract:
    event_name = str(event_type or "")
    for registry, classification in (
        (CANONICAL_KERNEL_EVENT_TYPES, "canonical"),
        (PROJECTION_ONLY_RUNTIME_EVENT_TYPES, "projection_only"),
        (AUDIT_ONLY_RUNTIME_EVENT_TYPES, "audit_only"),
    ):
        metadata = registry.get(event_name)
        if metadata is not None:
            return {
                "classification": classification,
                "family": metadata["family"],
                "actor": metadata["actor"],
                "visibility": metadata["visibility"],
            }
    if event_name in {"assistant_message", "assistant_delta", "assistant.message.start", "assistant.message.delta", "assistant.message.end"}:
        return {"classification": "bridge_stream", "family": "message.assistant", "actor": "engine", "visibility": "transcript"}
    if event_name == "user_message":
        return {"classification": "kernel", "family": "message.user", "actor": "human", "visibility": "transcript"}
    if event_name in {"assistant.reasoning.delta", "assistant.thought_summary.delta"}:
        return {"classification": "bridge_stream", "family": "reasoning.delta", "actor": "engine", "visibility": "diagnostic"}
    if event_name.startswith("tool.") or event_name in {"tool_call", "tool_result", "tool.result", "todo_event"}:
        return {"classification": "kernel", "family": "tool.event", "actor": "tool", "visibility": "tool"}
    if event_name in {"ctree_node", "turn_start", "lifecycle_event", "guardrail_event"}:
        return {"classification": "kernel", "family": f"audit.{event_name}", "actor": "service", "visibility": "audit"}
    if event_name in BRIDGE_HOST_ONLY_RUNTIME_EVENT_TYPES or event_name in {"permission_request", "permission_response", "task_event", "ctree_snapshot"}:
        return {"classification": "bridge_host", "family": f"host.{event_name}", "actor": "service", "visibility": "host"}
    return {"classification": "legacy_unclassified", "family": "legacy.unclassified", "actor": "engine", "visibility": "audit"}

class SessionRunner:
    """Coordinates agent execution, user inputs, and command handling for a session."""

    def __init__(
        self,
        *,
        session: SessionRecord,
        registry: SessionRegistry,
        request: SessionCreateRequest,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.request = request
        self.agent_factory = agent_factory or self._default_factory

        self._task: Optional[asyncio.Task[None]] = None
        self._agent: Optional[Any] = None
        self._stop_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._input_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._product_session_lock = threading.RLock()
        self._published_events = 0
        self._closed = False
        self._workspace_path: Optional[Path] = None
        self._attachment_store: Dict[str, Dict[str, Any]] = {}
        self._permission_queue: Any = None
        self._consumed_permission_responses: Dict[tuple[str, str, str], int] = {}
        self._control_queue: Any = None
        self._checkpoint_manager: Optional[CheckpointManager] = None
        self._skills_catalog_cache: Optional[Dict[str, Any]] = None
        self._ctree_snapshot_cache: Optional[Dict[str, Any]] = None
        self._ctree_last_node: Optional[Dict[str, Any]] = None
        self._base_config_cache: Optional[Dict[str, Any]] = None
        self._prepared_runtime_config: Optional[Dict[str, Any]] = None
        self._todo_enabled: bool = False
        self._active_bridge_timing_context: Optional[Dict[str, float]] = None
        self._accepted_task_texts: List[str] = []

        initial_metadata = self.session.metadata
        initial_metadata.update(dict(request.metadata or {}))
        self.session.metadata = initial_metadata
        self._model_override: Optional[str] = initial_metadata.get("model")
        self._mode: Optional[str] = initial_metadata.get("mode")
        self._profile_timing_enabled: bool = bool(
            os.environ.get("BREADBOARD_PROFILE_TIMING", "").strip().lower() in {"1", "true", "yes", "on"}
            or initial_metadata.get("profile_timing")
        )

    def _default_factory(
        self,
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> Any:
        from agentic_coder_prototype.agent import create_agent

        metadata = self.session.metadata if isinstance(self.session.metadata, dict) else {}
        force_local_mode = bool(metadata.get("cli_force_local_mode", True))
        return create_agent(
            config_path,
            workspace_dir=workspace_dir,
            overrides=overrides,
            force_local_mode=force_local_mode,
        )

    async def start(self) -> None:
        if self._task:
            raise RuntimeError("runner already started")
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(self._run(), name=f"kyle-session-{self.session.session_id}")

    def _request_stop(self, reason: str) -> bool:
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            stopping = not product_session or product_session.read_model.status not in {"completed", "failed", "canceled"}
            if stopping:
                self.transition_product_session("cancel", reason)
            self._stop_event.set()
            self._input_queue.put_nowait(None)
            return stopping

    async def stop(self) -> None:
        if self._closed:
            return
        self._request_stop("operator request")
        if self._task and not self._task.done():
            await self._task

    async def enqueue_input(self, content: str, attachments: Optional[list[str]] = None) -> str:
        if self._closed:
            raise RuntimeError("session is closed")
        if not content or not content.strip():
            raise ValueError("input content must not be empty")
        content = self._sanitize_interactive_input_content(content)
        attachment_ids = [item for item in (attachments or []) if isinstance(item, str) and item.strip()]
        payload = {"content": content, "attachments": attachment_ids}
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            if product_session is not None:
                artifacts = getattr(self.session, "product_artifacts", {})
                refs = [artifacts[item] for item in attachment_ids
                        if isinstance(artifacts, dict) and item in artifacts]
                product_session.input(content, refs)
                self.session.metadata["session_contract"] = product_session.read_model.as_dict()
            self._input_queue.put_nowait(payload)
        return content

    def transition_product_session(self, transition: str, *args: Any) -> None:
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            if product_session is None:
                return
            if transition in {"complete", "fail", "cancel"} and product_session.read_model.status in {
                "completed", "failed", "canceled"}:
                return
            getattr(product_session, transition)(*args)
            self.session.metadata["session_contract"] = product_session.read_model.as_dict()

    def _sanitize_interactive_input_content(self, content: str) -> str:
        """Remove an exact prior-prompt prefix accidentally repeated by the TUI.

        Independent prompt POSTs must remain separate turns; only the nonempty suffix
        of an exact prior accepted prompt is new input.
        """
        raw = str(content or "")
        if not raw:
            return raw
        for prior in sorted(self._accepted_task_texts, key=len, reverse=True):
            if not prior or not raw.startswith(prior) or len(raw) <= len(prior):
                continue
            suffix = raw[len(prior) :]
            if not suffix.strip():
                continue
            logger.warning(
                "session(%s) stripped stale prompt prefix from interactive input old_len=%s new_len=%s",
                self.session.session_id,
                len(prior),
                len(suffix),
            )
            meta = self.session.metadata if isinstance(self.session.metadata, dict) else {}
            repairs = list(meta.get("input_boundary_repairs") or []) if isinstance(meta.get("input_boundary_repairs"), list) else []
            repairs.append({"prior_len": len(prior), "raw_len": len(raw), "suffix_len": len(suffix)})
            meta["input_boundary_repairs"] = repairs[-10:]
            self.session.metadata = meta
            self._persist_metadata_snapshot_threadsafe()
            return suffix.lstrip()
        return raw

    async def handle_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        durable_reconfigure: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        if self._closed:
            raise RuntimeError("session is closed")

        payload = payload or {}
        match command:
            case "list_checkpoints":
                manager = self._checkpoint_manager
                if manager is None:
                    workspace_dir = self.get_workspace_dir()
                    if not workspace_dir:
                        raise RuntimeError("workspace not ready")
                    manager = CheckpointManager(workspace_dir)
                    self._checkpoint_manager = manager
                checkpoints = [cp.as_payload() for cp in manager.list_checkpoints()]
                await self.publish_event_async(EventType.CHECKPOINT_LIST, {"checkpoints": checkpoints})
                return {"status": "ok", "count": len(checkpoints)}
            case "restore_checkpoint":
                checkpoint_id = payload.get("checkpoint_id") or payload.get("checkpointId") or payload.get("id")
                if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
                    raise ValueError("restore_checkpoint requires non-empty 'checkpoint_id'")
                mode = str(payload.get("mode") or "code").strip().lower()
                if mode not in {"code", "conversation", "both"}:
                    raise ValueError("restore_checkpoint 'mode' must be one of: code, conversation, both")
                manager = self._checkpoint_manager
                if manager is None:
                    workspace_dir = self.get_workspace_dir()
                    if not workspace_dir:
                        raise RuntimeError("workspace not ready")
                    manager = CheckpointManager(workspace_dir)
                    self._checkpoint_manager = manager
                prune = True
                if mode == "conversation":
                    # Best-effort only for now; code restore is the P0 deterministic requirement.
                    prune = True
                if mode in {"code", "both"}:
                    manager.restore_checkpoint(checkpoint_id.strip(), prune=prune)
                if mode in {"conversation", "both"}:
                    try:
                        snapshot = manager.load_snapshot(checkpoint_id.strip())
                    except Exception:
                        snapshot = None
                    if snapshot:
                        workspace_dir = self.get_workspace_dir()
                        if workspace_dir:
                            active_path = workspace_dir / ".breadboard" / "checkpoints" / "active_snapshot.json"
                            try:
                                active_path.parent.mkdir(parents=True, exist_ok=True)
                                active_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
                                self.session.metadata["conversation_snapshot"] = {
                                    "checkpoint_id": checkpoint_id.strip(),
                                    "path": str(active_path),
                                }
                                self._persist_metadata_snapshot_threadsafe()
                            except Exception:
                                pass
                await self.publish_event_async(
                    EventType.CHECKPOINT_RESTORED,
                    {"checkpoint_id": checkpoint_id.strip(), "mode": mode, "prune": prune},
                )
                checkpoints = [cp.as_payload() for cp in manager.list_checkpoints()]
                await self.publish_event_async(EventType.CHECKPOINT_LIST, {"checkpoints": checkpoints})
                return {"status": "ok", "checkpoint_id": checkpoint_id.strip(), "mode": mode, "prune": prune}
            case "permission_decision":
                request_id = (
                    payload.get("request_id")
                    or payload.get("requestId")
                    or payload.get("permission_id")
                    or payload.get("permissionId")
                    or payload.get("id")
                )
                decision = payload.get("decision") or payload.get("response")
                rule = payload.get("rule")
                scope = payload.get("scope")
                note = payload.get("note")
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError("permission_decision requires non-empty 'request_id'")
                if not isinstance(decision, str) or not decision.strip():
                    raise ValueError("permission_decision requires non-empty 'decision'")
                normalized = decision.strip().lower()
                response_value = _canonical_permission_resolution(normalized, None)
                # Permission broker accepts a dict item with request_id + response/decision.
                # We use the simplest uniform response (applies to all items in a batch).
                permission_payload = {
                    "request_id": request_id.strip(),
                    "response": response_value,
                }
                category = self._infer_permission_category(request_id.strip()) if rule else None
                workspace_dir = self.get_workspace_dir() if rule else None
                detail = await self.handle_command("respond_permission", permission_payload)
                if rule:
                    before = dict(self.session.metadata or {}); metadata = dict(before)
                    rules = list(metadata.get("permission_rules") or []); rules.append(
                        {"request_id": request_id.strip(), "decision": normalized, "rule": rule, "scope": scope, "note": note})
                    metadata["permission_rules"] = rules
                    persist_rule = (response_value == "always" or normalized in {"deny-always", "deny_always"}) and category and workspace_dir
                    rule_path = workspace_dir / ".breadboard" / "permission_rules.json" if persist_rule else None
                    prior_rule_file = rule_path.read_bytes() if rule_path and rule_path.exists() else None
                    try:
                        if persist_rule and upsert_permission_rule(
                            workspace_dir, category=category, pattern=str(rule).strip(),
                            decision="deny" if normalized.startswith("deny") else "allow", scope=str(scope or "project"),
                        ) is False: raise RuntimeError("failed to persist permission rule")
                        await self.registry.update_metadata(self.session.session_id, metadata=metadata)
                    except Exception:
                        if rule_path: rule_path.write_bytes(prior_rule_file) if prior_rule_file is not None else rule_path.unlink(missing_ok=True)
                        self.session.metadata = before
                        self.transition_product_session("fail", "permission_commit_failed", "failed to commit permission decision")
                        raise
                if normalized in {"deny-stop", "deny_stop"} or bool(payload.get("stop")):
                    await self.handle_command("stop", {})
                return {"status": "ok", "request_id": request_id.strip(), "decision": response_value, "delivered": detail}
            case "set_skills":
                selection_payload = dict(payload or {})
                if "selected" in selection_payload and "allowlist" not in selection_payload:
                    selection_payload["allowlist"] = selection_payload.get("selected")
                with self._product_session_lock:
                    config = dict(getattr(self._agent, "config", {}) or {}) if self._agent else {}
                    selection = normalize_skill_selection(config, selection_payload)
                    overrides = {"skills.allowlist": selection.get("allowlist") or [],
                                 "skills.blocklist": selection.get("blocklist") or []}
                    prepared = apply_dotted_overrides(self.current_runtime_config(), overrides)
                    if durable_reconfigure is not None:
                        durable_reconfigure(prepared)
                    if self._agent:
                        try:
                            committed = self._agent.apply_runtime_overrides(overrides)
                        except Exception:
                            committed = False
                        if committed is False:
                            self.transition_product_session(
                                "fail", "runtime_reconfigure_failed", "failed to apply skills configuration")
                            raise RuntimeError("failed to apply skills configuration")
                    self.session.metadata["skills_selection"] = selection
                    self._prepared_runtime_config = prepared
                    self._persist_metadata_snapshot_threadsafe()
                    self._skills_catalog_cache = None
                    catalog_payload = self.get_skill_catalog()
                await self.publish_event_async(EventType.SKILLS_SELECTION, {"selection": selection})
                await self.publish_event_async(EventType.SKILLS_CATALOG, catalog_payload)
                return {"status": "ok", "selection": selection, "catalog": catalog_payload.get("catalog")}
            case "stop":
                stopping = self._request_stop("stop command")
                if not stopping:
                    return {"status": "ok", "stopping": False}
                queue = getattr(self, "_control_queue", None)
                if queue is not None:
                    try:
                        put_nowait = getattr(queue, "put_nowait", None)
                        if callable(put_nowait):
                            put_nowait({"kind": "stop"})
                        else:
                            queue.put({"kind": "stop"})
                    except Exception:
                        pass
                else:
                    # Fallback: request the active conductor loop stop if it is local/in-process.
                    agent = getattr(self._agent, "agent", None)
                    if agent is not None:
                        try:
                            req = getattr(agent, "request_stop", None)
                            remote = getattr(req, "remote", None) if req is not None else None
                            if callable(remote):
                                remote()
                            elif callable(req):
                                req()
                        except Exception:
                            pass
                await self.publish_event_async(EventType.TASK_EVENT, {"kind": "stop_requested"})
                return {"status": "ok", "stopping": stopping}
            case "set_model":
                model_value = payload.get("model")
                if not isinstance(model_value, str) or not model_value.strip():
                    raise ValueError("set_model requires non-empty 'model'")
                model_value = model_value.strip()
                cfg = self.current_runtime_config()
                policy = policy_pack_for_config_authority(
                    cfg, session_id=self.session.session_id, config_path=self.request.config_path,
                    logger=logger,
                )
                if (policy.model_allowlist is not None or policy.model_denylist) and not policy.is_model_allowed(model_value):
                    raise ValueError(f"set_model denied by policy: {model_value}")
                with self._product_session_lock:
                    prepared = apply_dotted_overrides(
                        self.current_runtime_config(), {"providers.default_model": model_value})
                    if durable_reconfigure is not None:
                        durable_reconfigure(prepared)
                    self._model_override = model_value
                    if not self._apply_model_override():
                        self.transition_product_session(
                            "fail", "runtime_reconfigure_failed", "failed to apply model configuration")
                        raise RuntimeError("failed to apply model configuration")
                    self.session.metadata["model"] = model_value
                    self._prepared_runtime_config = prepared
                return {"status": "ok", "model": model_value}
            case "set_mode":
                mode_value = payload.get("mode")
                if not isinstance(mode_value, str) or not mode_value.strip():
                    raise ValueError("set_mode requires non-empty 'mode'")
                mode_value = mode_value.strip()
                with self._product_session_lock:
                    overrides = {"mode": mode_value}
                    prepared = apply_dotted_overrides(self.current_runtime_config(), overrides)
                    if durable_reconfigure is not None:
                        durable_reconfigure(prepared)
                    if self._agent:
                        try: committed = self._agent.apply_runtime_overrides(overrides)
                        except Exception: committed = False
                        if committed is False:
                            self.transition_product_session("fail", "runtime_reconfigure_failed", "failed to apply mode configuration")
                            raise RuntimeError("failed to apply mode configuration")
                    self._mode = mode_value; self.session.metadata["mode"] = mode_value
                    self._prepared_runtime_config = prepared
                return {"status": "ok", "mode": mode_value}
            case "session_child_next" | "session_child_previous" | "session_parent":
                child_session_id = payload.get("child_session_id") or payload.get("childSessionId")
                parent_session_id = payload.get("parent_session_id") or payload.get("parentSessionId")
                target_session_id = payload.get("target_session_id") or payload.get("targetSessionId")

                def _norm(value: Any) -> Optional[str]:
                    if not isinstance(value, str):
                        return None
                    trimmed = value.strip()
                    return trimmed or None

                child_session_id = _norm(child_session_id)
                parent_session_id = _norm(parent_session_id)
                target_session_id = _norm(target_session_id)

                if command == "session_parent":
                    resolved_target = target_session_id or parent_session_id or child_session_id
                else:
                    resolved_target = target_session_id or child_session_id or parent_session_id

                if not resolved_target:
                    return {"status": "ok", "command": command, "switched": False, "reason": "target_missing"}

                target_record = await self.registry.get(resolved_target)
                if target_record is None:
                    return {
                        "status": "ok",
                        "command": command,
                        "switched": False,
                        "reason": "target_not_found",
                        "target_session_id": resolved_target,
                    }

                return {
                    "status": "ok",
                    "command": command,
                    "switched": True,
                    "target_session_id": resolved_target,
                    "active_session_id": resolved_target,
                    "child_session_id": child_session_id,
                    "parent_session_id": parent_session_id,
                }
            case "run_tests":
                if self._debug_permissions_enabled():
                    event_payload = await self._emit_debug_permission_request(payload)
                    return {"status": "ok", "debug": True, "request_id": event_payload.get("request_id")}
                raise NotImplementedError("run_tests not yet implemented")
            case "apply_diff":
                raise NotImplementedError("apply_diff not yet implemented")
            case "respond_permission" | "permission_response":
                request_id = (
                    payload.get("request_id")
                    or payload.get("requestId")
                    or payload.get("permission_id")
                    or payload.get("permissionId")
                    or payload.get("id")
                )
                response = payload.get("response") or payload.get("decision") or payload.get("default")
                responses = payload.get("responses")
                items = payload.get("items")

                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError("respond_permission requires non-empty 'request_id'/'permission_id'/'id'")

                if isinstance(items, dict) and not isinstance(responses, dict):
                    responses = {"items": dict(items)}
                canonical_responses = (
                    _canonical_permission_responses(responses) if isinstance(responses, dict) else None
                )
                resolution = _canonical_permission_resolution(response, canonical_responses)
                normalized_request_id = request_id.strip()
                queue = getattr(self, "_permission_queue", None)
                if queue is None:
                    if self._debug_permissions_enabled():
                        response_payload: Dict[str, Any] = {"request_id": normalized_request_id}
                        if canonical_responses is not None:
                            response_payload["responses"] = canonical_responses
                        else:
                            response_payload["response"] = resolution
                            response_payload["decision"] = resolution
                        with self._product_session_lock:
                            self.transition_product_session("resolve_approval", normalized_request_id, resolution)
                            self._update_pending_permissions("permission_response", response_payload, source="session")
                        await self.publish_event_async(EventType.PERMISSION_RESPONSE, response_payload)
                        return {"status": "ok", "request_id": normalized_request_id, "decision": resolution, "delivered": response_payload, "debug": True}
                    raise RuntimeError("no permission request is active")

                if canonical_responses is not None:
                    item: Dict[str, Any] = {"request_id": normalized_request_id, "responses": canonical_responses}
                else:
                    if not isinstance(response, str) or not response.strip():
                        raise ValueError("respond_permission requires non-empty 'response' when 'responses' is not provided")
                    item = {"permission_id": normalized_request_id, "response": resolution}
                with self._product_session_lock:
                    self.transition_product_session("resolve_approval", normalized_request_id, resolution)
                    try:
                        put_nowait = getattr(queue, "put_nowait", None)
                        if callable(put_nowait):
                            put_nowait(item)
                        else:
                            queue.put(item)
                    except Exception as exc:
                        self.transition_product_session(
                            "fail", "permission_delivery_failed", "failed to deliver permission response")
                        raise RuntimeError(f"failed to deliver permission response: {exc}") from exc
                    self._update_pending_permissions(
                        "permission_response", item, source="session", consume_fifo=True
                    )
                return {"status": "ok", "request_id": normalized_request_id, "decision": resolution, "delivered": item}
            case _:
                raise ValueError(f"Unsupported command: {command}")

    def _load_base_config(self) -> Dict[str, Any]:
        if isinstance(self._base_config_cache, dict):
            return dict(self._base_config_cache)
        try:
            cfg = load_agent_config(self.request.config_path)
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        self._base_config_cache = dict(cfg)
        return dict(self._base_config_cache)

    def prepare_runtime_config(self) -> Dict[str, Any]:
        """Freeze the exact base configuration and overrides passed to the agent."""
        if self._prepared_runtime_config is not None:
            return dict(self._prepared_runtime_config)
        overrides = dict(self.request.overrides or {})
        if isinstance(self._model_override, str) and self._model_override.strip():
            overrides["providers.default_model"] = self._model_override.strip()
        if isinstance(self._mode, str) and self._mode.strip():
            overrides["mode"] = self._mode.strip()
        permission_mode = (self.request.permission_mode or self.session.metadata.get("permission_mode") or "").strip().lower()
        if permission_mode in {"prompt", "ask", "interactive"}:
            overrides.setdefault("permissions.options.mode", "prompt")
            overrides.setdefault("permissions.options.default_response", "reject")
            overrides.setdefault("permissions.edit.default", "ask")
            overrides.setdefault("permissions.shell.default", "ask")
            overrides.setdefault("permissions.webfetch.default", "ask")
            self.request.permission_mode = permission_mode
            self.session.metadata["permission_mode"] = permission_mode

        base_cfg = self._load_base_config()
        workspace_guess_path = self._resolve_workspace_guess(base_cfg)
        if workspace_guess_path:
            self._workspace_path = workspace_guess_path
            workspace = str(workspace_guess_path)
            self.request.workspace = workspace
            overrides["workspace.root"] = workspace
            try:
                rules = load_permission_rules(workspace_guess_path)
            except Exception:
                rules = []
            for key, value in build_permission_overrides(base_cfg, rules).items() if rules else ():
                existing = overrides.get(key)
                if key in overrides and isinstance(existing, list) and isinstance(value, list):
                    value = existing + [item for item in value if item not in existing]
                overrides[key] = value

        self.request.overrides = overrides
        self._prepared_runtime_config = apply_dotted_overrides(base_cfg, overrides)
        return dict(self._prepared_runtime_config)

    def current_runtime_config(self) -> Dict[str, Any]:
        return dict(getattr(self._agent, "config", None) or self.prepare_runtime_config())

    def _resolve_workspace_guess(self, base_cfg: Dict[str, Any]) -> Optional[Path]:
        candidate: Any = self.request.workspace
        if not candidate and isinstance(base_cfg, dict):
            workspace = base_cfg.get("workspace")
            candidate = (workspace.get("root") or workspace.get("path")) if isinstance(workspace, dict) else None
        candidate = candidate or f"tmp/agent_ws_{os.path.basename(self.request.config_path).split('.')[0]}"
        try:
            path = Path(str(candidate)).expanduser()
            if not path.is_absolute():
                root = Path(__file__).resolve().parents[3]
                path = root / path if path.parts[:1] == ("tmp",) else root / "tmp" / path
            return path.resolve()
        except Exception:
            return None

    def _parse_replay_path(self, task_text: str) -> Optional[Path]:
        text = (task_text or "").strip()
        if not text:
            return None
        path_text: Optional[str] = None
        if text.startswith("replay:"):
            path_text = text[len("replay:") :].strip()
        elif text.startswith("@replay") or text.startswith("/replay"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                path_text = parts[1].strip()
        if not path_text:
            return None
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        return path

    async def _maybe_publish_todo_snapshot(self, workspace_dir: Optional[Path], *, call_id: str) -> None:
        if not self._todo_enabled or not workspace_dir:
            return
        envelope = self._load_todo_envelope_from_disk(workspace_dir)
        if envelope is None:
            return
        self.session.metadata["todo_last_update"] = envelope
        self._persist_metadata_snapshot_threadsafe()
        await self.publish_event_async(
            EventType.TOOL_RESULT,
            {"call_id": call_id, "todo": envelope},
        )

    async def _ensure_agent_initialized(self) -> None:
        if self._agent is not None:
            return
        overrides = dict(self.request.overrides or {})
        try:
            from agentic_coder_prototype.auth.store import DEFAULT_PROVIDER_AUTH_STORE

            openai_auth = DEFAULT_PROVIDER_AUTH_STORE.get("openai")
            if openai_auth is not None:
                if openai_auth.api_key:
                    overrides["provider_auth_runtime.openai.api_key"] = openai_auth.api_key
                if openai_auth.base_url:
                    overrides["provider_auth_runtime.openai.base_url"] = openai_auth.base_url
                for header_key, header_value in dict(openai_auth.headers or {}).items():
                    if not header_key or header_value is None:
                        continue
                    overrides[f"provider_auth_runtime.openai.headers.{header_key}"] = str(header_value)
        except Exception:
            pass
        frozen = self.current_runtime_config()
        descriptor, snapshot = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream: json.dump(frozen, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
            self._agent = self.agent_factory(snapshot, self.request.workspace, overrides or None)
            if hasattr(self._agent, "config_path"): self._agent.config_path = self.request.config_path
            await asyncio.to_thread(self._agent.initialize)
        finally: Path(snapshot).unlink(missing_ok=True)
        workspace_dir = Path(self._agent.workspace_dir).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_path = workspace_dir
        if self._model_override:
            self._apply_model_override()
        if self._todo_enabled:
            meta = self.session.metadata if isinstance(self.session.metadata, dict) else {}
            if not isinstance(meta.get("todo_last_update"), dict):
                await self._maybe_publish_todo_snapshot(workspace_dir, call_id="todo:snapshot:init")
        try:
            if self._checkpoint_manager is None:
                self._checkpoint_manager = CheckpointManager(workspace_dir)
                self._checkpoint_manager.create_checkpoint("Session start")
        except Exception:
            self._checkpoint_manager = None

    async def _execute_replay_task(self, task_text: str) -> Dict[str, Any]:
        replay_path = self._parse_replay_path(task_text)
        if replay_path is None:
            raise ValueError("replay task missing path (expected replay:<path>)")
        if not replay_path.exists():
            raise FileNotFoundError(f"replay fixture not found: {replay_path}")

        try:
            meta = self.session.metadata if isinstance(self.session.metadata, dict) else {}
            meta = dict(meta)
            meta["replay_fixture"] = {"path": str(replay_path)}
            self.session.metadata = meta
            self._persist_metadata_snapshot_threadsafe()
        except Exception:
            pass

        published_completion = False
        published_run_finished = False
        published_events = 0
        metadata = self.session.metadata if isinstance(self.session.metadata, dict) else {}
        one_shot = bool(metadata.get("non_interactive_cli_session") or metadata.get("cli_session_kind") == "oneshot")
        terminal_events: list[TranslatedRuntimeEvent] = []

        with replay_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                if self._stop_event.is_set():
                    break
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue

                delay_ms = entry.get("delay_ms", entry.get("delayMs", 0))
                try:
                    delay_ms_val = max(0, int(delay_ms))
                except Exception:
                    delay_ms_val = 0
                if delay_ms_val:
                    await asyncio.sleep(delay_ms_val / 1000.0)

                type_raw = entry.get("type") or entry.get("event_type") or entry.get("eventType")
                if not isinstance(type_raw, str) or not type_raw.strip():
                    continue
                evt_type = EventType(type_raw.strip())

                payload_raw = entry.get("payload")
                if payload_raw is None:
                    payload_raw = entry.get("data")
                if isinstance(payload_raw, dict):
                    payload: Dict[str, Any] = dict(payload_raw)
                else:
                    payload = {"value": payload_raw}

                turn_raw = entry.get("turn")
                try:
                    turn = int(turn_raw) if isinstance(turn_raw, (int, float, str)) and str(turn_raw).strip() else None
                except Exception:
                    turn = None

                if evt_type in {EventType.TOOL_RESULT, EventType.TOOL_RESULT_DOT}:
                    todo_update = payload.get("todo")
                    if isinstance(todo_update, dict):
                        try:
                            self.session.metadata["todo_last_update"] = dict(todo_update)
                            self._persist_metadata_snapshot_threadsafe()
                        except Exception:
                            pass

                if one_shot and evt_type in {EventType.COMPLETION, EventType.RUN_FINISHED}:
                    terminal_events.append((evt_type, payload, turn, {}))
                else:
                    await self.publish_event_async(evt_type, payload, turn=turn)
                published_events += 1

                if evt_type is EventType.COMPLETION:
                    published_completion = True
                elif evt_type is EventType.RUN_FINISHED:
                    published_run_finished = True

        completion_summary: Dict[str, Any] = {"completed": True, "reason": "replay"}
        if not published_completion:
            payload = {"summary": completion_summary, "mode": self._mode}
            terminal_events.append((EventType.COMPLETION, payload, None, {})) if one_shot else await self.publish_event_async(EventType.COMPLETION, payload)
            published_events += 1
        if not published_run_finished:
            payload = {"eventCount": published_events, "completed": True, "reason": "replay", "logging_dir": None}
            terminal_events.append((EventType.RUN_FINISHED, payload, None, {})) if one_shot else await self.publish_event_async(EventType.RUN_FINISHED, payload)

        return {
            "completion_summary": completion_summary,
            "reward_metrics": None,
            "logging_dir": None,
            "_terminal_events": terminal_events,
        }

    async def _run(self) -> None:
        session_started_at = time.monotonic()
        await self.registry.update_status(self.session.session_id, SessionStatus.RUNNING)
        try:
            # Safety: never auto-wipe an existing workspace when running interactive sessions
            # via the CLI bridge. The engine historically treated workspaces as disposable
            # sandboxes; for a Claude Code-style experience we must preserve the user's
            # working directory unless explicitly overridden by the caller.
            os.environ.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
            base_cfg = self.prepare_runtime_config()

            try:
                todo_cfg = GuardrailCoordinator(base_cfg).todo_config()
            except Exception:
                todo_cfg = {"enabled": False}
            self._todo_enabled = bool(todo_cfg.get("enabled"))
            await self._maybe_publish_todo_snapshot(self._workspace_path, call_id="todo:snapshot:init")
            try:
                if self._workspace_path and self._checkpoint_manager is None:
                    self._checkpoint_manager = CheckpointManager(self._workspace_path)
                    self._checkpoint_manager.create_checkpoint("Session start")
            except Exception:
                # Best-effort: checkpointing should not block starting a session.
                self._checkpoint_manager = None
            try:
                catalog_payload = self.get_skill_catalog()
                await self.publish_event_async(EventType.SKILLS_CATALOG, catalog_payload)
                selection = (catalog_payload.get("selection") or {}) if isinstance(catalog_payload, dict) else {}
                if selection:
                    await self.publish_event_async(EventType.SKILLS_SELECTION, {"selection": selection})
            except Exception:
                pass

            initial_task = (self.request.task or "").strip()
            if initial_task:
                self._accepted_task_texts.append(initial_task)
                self._input_queue.put_nowait({"content": initial_task, "attachments": []})

            while not self._stop_event.is_set():
                try:
                    next_input = await self._input_queue.get()
                except asyncio.CancelledError:  # pragma: no cover - defensive
                    break
                if next_input is None:
                    break

                task_payload = dict(next_input)
                task_text = str(task_payload.get("content", ""))
                task_received_at = time.monotonic()
                if self._parse_replay_path(task_text) is not None:
                    result = await self._execute_replay_task(task_text)
                    after_execute_task_at = time.monotonic()
                else:
                    attachment_ids = task_payload.get("attachments") or []
                    attachment_text = self._format_attachment_helper(attachment_ids)
                    if attachment_text:
                        task_text = f"{task_text.rstrip()}\n\n{attachment_text}"
                    if task_text.strip():
                        self._accepted_task_texts.append(task_text)
                        self._accepted_task_texts = self._accepted_task_texts[-20:]

                    await self._ensure_agent_initialized()
                    after_agent_init_at = time.monotonic()
                    if self._profile_timing_enabled:
                        self._active_bridge_timing_context = {
                            "session_to_task_received_seconds": round(task_received_at - session_started_at, 6),
                            "task_received_to_agent_initialized_seconds": round(after_agent_init_at - task_received_at, 6),
                        }
                    result = await asyncio.to_thread(self._execute_task, task_text)
                    after_execute_task_at = time.monotonic()
                    if self._profile_timing_enabled and isinstance(result, dict):
                        timing = result.setdefault("bridge_timing", {})
                        if isinstance(timing, dict):
                            timing.update(
                                {
                                    "session_to_task_received_seconds": round(task_received_at - session_started_at, 6),
                                    "task_received_to_agent_initialized_seconds": round(after_agent_init_at - task_received_at, 6),
                                    "execute_task_wall_seconds": round(after_execute_task_at - after_agent_init_at, 6),
                                }
                            )
                    self._active_bridge_timing_context = None
                metadata = self.session.metadata if isinstance(self.session.metadata, dict) else {}
                one_shot = bool(metadata.get("non_interactive_cli_session") or metadata.get("cli_session_kind") == "oneshot")
                with self._product_session_lock:
                    product_session = getattr(self.session, "product_session", None)
                    if product_session is None:
                        durable_success = True
                    else:
                        product_status = product_session.read_model
                        if one_shot and product_status.status == "running" and not self._stop_event.is_set():
                            self.transition_product_session("complete")
                        durable_success = product_session.read_model.status == "completed" if one_shot else product_status.status not in {"failed", "canceled"}
                if durable_success:
                    await self.registry.update_metadata(
                        self.session.session_id, completion_summary=result.get("completion_summary"),
                        reward_summary=result.get("reward_metrics"), logging_dir=result.get("logging_dir"), metadata=self.session.metadata,
                    )
                    for event_type, event_payload, event_turn, event_contract in result.pop("_terminal_events", ()):
                        await self.publish_event_async(event_type, event_payload, turn=event_turn, classification=event_contract.get("classification"), family=event_contract.get("family"), actor=event_contract.get("actor"), visibility=event_contract.get("visibility"))
                after_registry_update_at = time.monotonic()
                if self._profile_timing_enabled and isinstance(result, dict):
                    timing = result.setdefault("bridge_timing", {})
                    if isinstance(timing, dict):
                        timing["post_execute_registry_update_seconds"] = round(after_registry_update_at - after_execute_task_at, 6)
                self._input_queue.task_done()
                if one_shot:
                    break

            product_session = getattr(self.session, "product_session", None)
            if product_session is None:
                metadata = self.session.metadata if isinstance(self.session.metadata, dict) else {}
                legacy_one_shot = bool(metadata.get("non_interactive_cli_session") or metadata.get("cli_session_kind") == "oneshot")
                final_status = SessionStatus.STOPPED if self._stop_event.is_set() and not legacy_one_shot else SessionStatus.COMPLETED
            else:
                product_state = product_session.read_model.status
                if product_state == "running" and not self._stop_event.is_set(): self.transition_product_session("complete")
                elif product_state not in {"completed", "failed", "canceled"}: self.transition_product_session("cancel", "runtime stopped")
                product_state = product_session.read_model.status
                final_status = {"completed": SessionStatus.COMPLETED, "failed": SessionStatus.FAILED, "canceled": SessionStatus.STOPPED}[product_state]
            await self.registry.update_status(self.session.session_id, final_status)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Session %s failed", self.session.session_id)
            self.transition_product_session("fail", type(exc).__name__, "runtime failure")
            product_state = getattr(getattr(getattr(self.session, "product_session", None), "read_model", None), "status", "failed"); await self.registry.update_status(self.session.session_id, {"completed": SessionStatus.COMPLETED, "failed": SessionStatus.FAILED, "canceled": SessionStatus.STOPPED}[product_state])
            if product_state == "failed": await self.publish_event_async(EventType.ERROR, {"message": str(exc)})
        finally:
            self._closed = True
            await self._enqueue_termination()

    def _load_todo_envelope_from_disk(self, workspace_dir: Path) -> Optional[Dict[str, Any]]:
        try:
            store = TodoStore(str(workspace_dir), load_existing=True)
            snapshot = store.snapshot()
            return project_store_snapshot_to_tui_envelope(snapshot, scope_key="main", scope_label="main")
        except Exception:
            return None

    async def _enqueue_termination(self) -> None:
        queue = self.session.event_queue
        try:
            await queue.put(None)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning("Event queue full while terminating session %s", self.session.session_id)

    def _apply_model_override(self) -> bool:
        if not self._agent or not self._model_override:
            return True
        try:
            providers = self._agent.config.setdefault("providers", {})  # type: ignore[attr-defined]
            providers["default_model"] = self._model_override
            return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to apply model override: %s", exc)
            return False

    def _persist_metadata_snapshot_threadsafe(self) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.registry.update_metadata(self.session.session_id, metadata=dict(self.session.metadata or {})),
                loop,
            )
        except Exception:
            pass

    def _debug_permissions_enabled(self) -> bool:
        try:
            meta = self.session.metadata or {}
            if isinstance(meta, dict) and meta.get("debug_permissions"):
                return True
        except Exception:
            pass
        return bool(os.environ.get("BREADBOARD_DEBUG_PERMISSIONS"))

    async def _emit_debug_permission_request(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = dict(payload or {})
        request_id = data.get("request_id") or f"debug-perm-{uuid.uuid4().hex[:8]}"
        suite = data.get("suite") if isinstance(data.get("suite"), str) else None
        summary = f"Tool requests permission to run bash{f' ({suite})' if suite else ''}."
        event_payload = {
            "request_id": str(request_id),
            "tool": "bash",
            "kind": "run",
            "rewindable": True,
            "summary": summary,
            "default_scope": "project",
            "metadata": {
                "function": "run_shell",
                "command": "pwd",
                "kind": "run",
            },
        }
        self._update_pending_permissions("permission_request", event_payload, source="session")
        await self.publish_event_async(EventType.PERMISSION_REQUEST, event_payload)
        return event_payload

    def _pending_permission_key(self, entry: Dict[str, Any]) -> tuple[str, str, str]:
        source = str(entry.get("source") or "session")
        task_id = str(entry.get("task_session_id") or "")
        req_id = str(entry.get("request_id") or entry.get("id") or "")
        return source, task_id, req_id

    def _infer_permission_category(self, request_id: str) -> Optional[str]:
        pending = self.session.metadata.get("pending_permissions")
        if not isinstance(pending, list):
            return None
        for entry in pending:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("request_id") or "") != request_id:
                continue
            request = entry.get("request") or {}
            if isinstance(request, dict):
                category = request.get("category")
                if isinstance(category, str) and category.strip():
                    return category.strip().lower()
                items = request.get("items")
                if isinstance(items, list) and items:
                    first = items[0] if isinstance(items[0], dict) else {}
                    cat = first.get("category") if isinstance(first, dict) else None
                    if isinstance(cat, str) and cat.strip():
                        return cat.strip().lower()
            return None
        return None

    def _update_pending_permissions(
        self, kind: str, info: Dict[str, Any], *, source: str = "session",
        task_session_id: Optional[str] = None, subagent_type: Optional[str] = None,
        consume_fifo: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        req_id = next((info.get(key) for key in ("request_id", "requestId", "permission_id", "permissionId", "id")
                       if info.get(key)), None)
        if not isinstance(req_id, str) or not req_id.strip(): return None
        request_id = req_id.strip()
        with self._product_session_lock:
            pending = self.session.metadata.get("pending_permissions")
            pending = pending if isinstance(pending, list) else []
            entry: Dict[str, Any] = {"source": str(source or "session"), "request_id": request_id}
            entry.update({key: value for key, value in (("task_session_id", task_session_id), ("subagent_type", subagent_type)) if value})
            entry_key = self._pending_permission_key(entry); activate = None; ready = None
            project_before_activation = kind == "permission_response"
            if kind == "permission_request":
                entry["request"] = dict(info or {})
                match = next((i for i, item in enumerate(pending) if self._pending_permission_key(item) == entry_key), None)
                normalized = list(pending); project_before_activation = match is not None
                if match is None:
                    normalized.append(entry); activate = entry if not pending else None
                else:
                    normalized[match] = entry; activate = entry if match == 0 else None
            elif kind == "permission_response":
                if not consume_fifo:
                    suppressed = self._consumed_permission_responses.get(entry_key, 0)
                    if suppressed:
                        if suppressed == 1: self._consumed_permission_responses.pop(entry_key)
                        else: self._consumed_permission_responses[entry_key] = suppressed - 1
                        return None
                match = next((i for i, item in enumerate(pending) if
                              (str(item.get("request_id") or item.get("id") or "") == request_id if consume_fifo else self._pending_permission_key(item) == entry_key)), None)
                normalized = list(pending)
                if match is not None and not consume_fifo and match:
                    normalized[match] = {**normalized[match], "deferred_response": dict(info)}; ready = []
                elif match is not None:
                    product_session = getattr(self.session, "product_session", None)
                    if not consume_fifo and product_session:
                        responses = info.get("responses") or info.get("items")
                        self.transition_product_session("resolve_approval", request_id,
                            _canonical_permission_resolution(info.get("response") or info.get("decision"), responses))
                        ready = [dict(info)]
                    if consume_fifo:
                        consumed_key = self._pending_permission_key(normalized[match]); self._consumed_permission_responses[consumed_key] = self._consumed_permission_responses.get(consumed_key, 0) + 1
                    normalized.pop(match)
                    while ready is not None and normalized and isinstance(normalized[0].get("deferred_response"), dict):
                        self.session.metadata["pending_permissions"] = normalized
                        deferred = dict(normalized[0]["deferred_response"]); deferred_id = str(normalized[0].get("request_id") or "")
                        request = normalized[0].get("request") if isinstance(normalized[0].get("request"), dict) else {}
                        operation = str(request.get("operation") or request.get("tool") or request.get("category") or "runtime permission")
                        self.transition_product_session("request_approval", deferred_id, operation)
                        self.transition_product_session("resolve_approval", deferred_id, _canonical_permission_resolution(
                            deferred.get("response") or deferred.get("decision"), deferred.get("responses") or deferred.get("items")))
                        normalized.pop(0); ready.append(deferred)
                    activate = normalized[0] if match == 0 and normalized else None
            else: return None
            product_session = getattr(self.session, "product_session", None)
            if project_before_activation:
                if normalized: self.session.metadata["pending_permissions"] = normalized
                else: self.session.metadata.pop("pending_permissions", None)
            if product_session and activate and product_session.read_model.status == "running":
                request = activate.get("request") if isinstance(activate.get("request"), dict) else {}
                operation = str(request.get("operation") or request.get("tool") or request.get("category") or "runtime permission")
                product_session.request_approval(str(activate.get("request_id") or activate.get("id") or ""), operation)
                self.session.metadata["session_contract"] = product_session.read_model.as_dict()
            if not project_before_activation:
                if normalized: self.session.metadata["pending_permissions"] = normalized
                else: self.session.metadata.pop("pending_permissions", None)
            self._persist_metadata_snapshot_threadsafe()
            return ready

    def _rehydrate_pending_permissions(
        self, event_type: str, payload: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        if event_type in {"permission_request", "permission_response"}:
            info = {**dict(payload or {}), **({"_runtime_event": (event_type, dict(payload or {}))} if event_type == "permission_response" else {})}
            return self._update_pending_permissions(event_type, info, source="session")
        if event_type != "task_event": return None
        kind = str((payload or {}).get("kind") or "")
        if kind not in {"permission_request", "permission_response"}: return None
        child_payload = (payload or {}).get("payload") or {}
        child = dict(child_payload) if isinstance(child_payload, dict) else {"payload": child_payload}
        if kind == "permission_response": child["_runtime_event"] = (event_type, dict(payload or {}))
        return self._update_pending_permissions(kind, child,
            source="task", task_session_id=str((payload or {}).get("sessionId") or ""),
            subagent_type=str((payload or {}).get("subagent_type") or ""),
        )

    def _execute_task(self, task_text: str) -> Dict[str, Any]:
        if not self._agent:
            raise RuntimeError("agent missing")

        execute_started_at = time.monotonic()
        emitted_flags: Dict[Any, bool] = {"assistant": False, EventType.COMPLETION: False, EventType.RUN_FINISHED: False}
        self._published_events = 0
        metadata = self.session.metadata if isinstance(self.session.metadata, dict) else {}
        one_shot = bool(metadata.get("non_interactive_cli_session") or metadata.get("cli_session_kind") == "oneshot")
        terminal_events: list[TranslatedRuntimeEvent] = []; terminal_lock = threading.Lock()
        is_local_agent = bool(getattr(self._agent, "_local_mode", False))
        event_queue = permission_queue = control_queue = queue_stop = queue_thread = None
        def claim_terminal(evt_type: EventType, evt_payload: Dict[str, Any], evt_turn: Optional[int], evt_contract: RuntimeEventContract) -> None:
            with terminal_lock:
                if emitted_flags[evt_type]: return
                emitted_flags[evt_type] = True
                if one_shot: terminal_events.append((evt_type, evt_payload, evt_turn, evt_contract))
                else: self.publish_event(evt_type, evt_payload, turn=evt_turn, classification=evt_contract.get("classification"), family=evt_contract.get("family"), actor=evt_contract.get("actor"), visibility=evt_contract.get("visibility"))

        def handle_runtime_event(event_type: str, payload: Dict[str, Any], *, turn: Optional[int] = None) -> None:
            if event_type == "ctree_node":
                try:
                    node = (payload or {}).get("node")
                    if isinstance(node, dict):
                        self._ctree_last_node = dict(node)
                    snapshot = (payload or {}).get("snapshot")
                    if isinstance(snapshot, dict):
                        self._ctree_snapshot_cache = dict(snapshot)
                except Exception:
                    pass
            elif event_type == "ctree_snapshot":
                try:
                    if isinstance(payload, dict):
                        self._ctree_snapshot_cache = dict(payload)
                except Exception:
                    pass
            ready_responses = self._rehydrate_pending_permissions(event_type, dict(payload or {}))
            permission_response_event = event_type == "permission_response" or event_type == "task_event" and payload.get("kind") == "permission_response"
            if permission_response_event and ready_responses == []:
                return
            translated = self._translate_runtime_event(event_type, payload, turn)
            if not translated:
                return
            evt_type, evt_payload, evt_turn, evt_contract = translated
            if evt_type in {EventType.COMPLETION, EventType.RUN_FINISHED}:
                claim_terminal(evt_type, evt_payload, evt_turn, evt_contract); return
            if evt_type in {
                EventType.ASSISTANT_MESSAGE,
                EventType.ASSISTANT_MESSAGE_START,
                EventType.ASSISTANT_MESSAGE_DELTA,
                EventType.ASSISTANT_MESSAGE_END,
                EventType.ASSISTANT_DELTA,
            }:
                emitted_flags["assistant"] = True
            self.publish_event(
                evt_type,
                evt_payload,
                turn=evt_turn,
                classification=evt_contract.get("classification"),
                family=evt_contract.get("family"),
                actor=evt_contract.get("actor"),
                visibility=evt_contract.get("visibility"),
            )
            if permission_response_event and ready_responses:
                for deferred in ready_responses[1:]:
                    deferred_type, deferred_payload = deferred.get("_runtime_event", (event_type, deferred))
                    handle_runtime_event(str(deferred_type), dict(deferred_payload), turn=turn)

        remote_stream_enabled = bool(os.environ.get("BREADBOARD_ENABLE_REMOTE_STREAM", ""))
        if isinstance(self.request.metadata, dict) and "enable_remote_stream" in self.request.metadata:
            remote_stream_enabled = bool(self.request.metadata.get("enable_remote_stream"))

        permission_mode = (self.request.permission_mode or self.session.metadata.get("permission_mode") or "").strip().lower()
        interactive_permissions = permission_mode in {"prompt", "ask", "interactive"}

        logger.info(
            "session(%s) task=%s stream=%s local=%s remote_toggle=%s",
            self.session.session_id,
            task_text[:32].replace("\n", " ") if task_text else "<empty>",
            bool(self.request.stream),
            is_local_agent,
            remote_stream_enabled,
        )

        if self._model_override:
            self._apply_model_override()

        if interactive_permissions:
            try:
                perms = getattr(self._agent, "config", {}).setdefault("permissions", {})  # type: ignore[attr-defined]
                if not isinstance(perms, dict):
                    perms = {}
                    self._agent.config["permissions"] = perms  # type: ignore[attr-defined]
                opts = perms.get("options")
                if not isinstance(opts, dict):
                    opts = {}
                opts["mode"] = "prompt"
                opts.setdefault("default_response", "reject")
                perms["options"] = opts
            except Exception:
                pass

        if not is_local_agent and (interactive_permissions or (self.request.stream and remote_stream_enabled)):
            try:
                from ray.util.queue import Queue
            except ImportError:  # pragma: no cover
                Queue = None  # type: ignore[misc]
            if Queue is not None:
                event_queue = Queue()
                queue_stop, queue_thread = self._start_queue_pump(event_queue, handle_runtime_event)
                logger.info("session(%s) remote event queue initialized", self.session.session_id)

        if interactive_permissions:
            if is_local_agent:
                import queue as pyqueue

                permission_queue = pyqueue.Queue()
            else:
                try:
                    from ray.util.queue import Queue as RayQueue
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError("Ray Queue required for remote permission prompts") from exc
                permission_queue = RayQueue()
            self._permission_queue = permission_queue
        else:
            self._permission_queue = None

        if is_local_agent:
            import queue as pyqueue

            control_queue = pyqueue.Queue()
        else:
            try:
                from ray.util.queue import Queue as RayQueue
            except ImportError:  # pragma: no cover
                control_queue = None
            else:
                control_queue = RayQueue()
        self._control_queue = control_queue

        start_time = time.time()
        run_task_started_at = time.monotonic()
        try:
            task_context = {}
            try:
                if isinstance(self.session.metadata, dict):
                    task_context = dict(self.session.metadata.get("task_context") or {})
                    if "task_type" in self.session.metadata and "task_type" not in task_context:
                        task_context["task_type"] = self.session.metadata.get("task_type")
            except Exception:
                task_context = {}
            kernel_emitter_run_dir = None
            kernel_emitter_mode = None
            try:
                meta = self.session.metadata if isinstance(self.session.metadata, dict) else {}
                runtime_records = meta.get("runtime_records") if isinstance(meta, dict) else None
                runtime_dir = meta.get("runtime_record_dir") if isinstance(meta, dict) else None
                if runtime_dir:
                    kernel_emitter_run_dir = runtime_dir
                elif isinstance(runtime_records, dict):
                    config_plane_stream = runtime_records.get("config_plane_stream")
                    if config_plane_stream:
                        kernel_emitter_run_dir = str(Path(config_plane_stream).resolve().parents[1])
                if kernel_emitter_run_dir:
                    from agentic_coder_prototype.runtime.kernel_emitter import primitive_emission_mode

                    kernel_emitter_mode = primitive_emission_mode("strict")
            except Exception:
                kernel_emitter_run_dir = None
                kernel_emitter_mode = None
            result = self._agent.run_task(  # type: ignore[call-arg]
                task_text,
                max_iterations=self.request.max_steps,
                stream=self.request.stream,
                event_emitter=handle_runtime_event if is_local_agent else None,
                event_queue=event_queue,
                permission_queue=permission_queue,
                control_queue=control_queue,
                context=task_context if task_context else None,
                kernel_emitter_run_dir=str(kernel_emitter_run_dir) if kernel_emitter_run_dir else None,
                kernel_emitter_mode=str(kernel_emitter_mode) if kernel_emitter_mode else None,
            )
            run_task_completed_at = time.monotonic()
        finally:
            self._permission_queue = None
            self._control_queue = None
            if queue_stop:
                queue_stop.set()
                if event_queue is not None:
                    try:
                        event_queue.put((None, None, None))
                    except Exception:  # pragma: no cover
                        pass
            if queue_thread:
                queue_thread.join()
            if event_queue is not None:
                self._drain_event_queue(event_queue, handle_runtime_event)

        elapsed_ms = int((time.time() - start_time) * 1000)
        after_queue_drain_at = time.monotonic()
        completion = result.get("completion_summary") or {}
        reward = result.get("reward_metrics_payload") or {}
        messages = result.get("messages")
        fallback_assistant_emitted = False
        if not emitted_flags["assistant"] and isinstance(messages, list):
            for entry in reversed(messages):
                if isinstance(entry, dict) and entry.get("role") == "assistant":
                    text = entry.get("content", "")
                    self.publish_event(
                        EventType.ASSISTANT_MESSAGE,
                        {"text": text, "message": entry, "source": "fallback"},
                    )
                    fallback_assistant_emitted = True
                    break
        if not emitted_flags["assistant"] and not fallback_assistant_emitted:
            final_message = completion.get("final_message") if isinstance(completion, dict) else None
            if isinstance(final_message, str) and final_message.strip():
                self.publish_event(
                    EventType.ASSISTANT_MESSAGE,
                    {
                        "text": final_message,
                        "message": {"role": "assistant", "content": final_message, "source": "completion_summary"},
                        "source": "completion_summary",
                    },
                    visibility="transcript",
                )
        after_fallback_emit_at = time.monotonic()
        logging_dir = result.get("logging_dir") or result.get("run_dir")
        usage_payload = self._extract_usage_metrics(result, logging_dir, elapsed_ms=elapsed_ms)
        completion_payload: Dict[str, Any] = {"summary": completion, "mode": self._mode}
        if self._profile_timing_enabled:
            provider_timing = None
            candidate = result.get("provider_runtime_timing")
            if isinstance(candidate, dict):
                provider_timing = dict(candidate)
            elif isinstance(result.get("provider_finish_meta"), dict):
                nested = result["provider_finish_meta"].get("provider_runtime_timing")
                if isinstance(nested, dict):
                    provider_timing = dict(nested)
            completion_payload["bridge_timing"] = {
                "execute_task_total_seconds": round(time.monotonic() - execute_started_at, 6),
                "run_task_seconds": round(run_task_completed_at - run_task_started_at, 6),
                "post_run_task_queue_drain_seconds": round(after_queue_drain_at - run_task_completed_at, 6),
                "post_queue_drain_to_completion_payload_seconds": round(after_fallback_emit_at - after_queue_drain_at, 6),
                "published_event_count_before_completion": self._published_events,
                "provider_runtime_timing": provider_timing,
                **dict(self._active_bridge_timing_context or {}),
            }
        if usage_payload:
            completion_payload["usage"] = usage_payload
        claim_terminal(EventType.COMPLETION, completion_payload, None, {})
        after_completion_publish_at = time.monotonic()
        if reward:
            self.publish_event(EventType.REWARD_UPDATE, {"summary": reward})
        if logging_dir:
            self.publish_event(EventType.LOG_LINK, {"url": f"file://{logging_dir}"})
        logger.info(
            "session(%s) task complete events=%s logging_dir=%s",
            self.session.session_id,
            self._published_events,
            logging_dir,
        )
        finish_payload = {
            "eventCount": self._published_events + 1,
            "steps": completion.get("steps_taken") or result.get("steps_taken"),
            "completed": bool(completion.get("completed")),
            "reason": completion.get("reason") or completion.get("exit_kind"),
            "logging_dir": logging_dir,
        }
        if usage_payload:
            finish_payload["usage"] = usage_payload
        if self._profile_timing_enabled:
            finish_payload["bridge_timing"] = {
                "completion_event_publish_seconds": round(after_completion_publish_at - after_fallback_emit_at, 6),
                "post_completion_to_run_finished_seconds": round(time.monotonic() - after_completion_publish_at, 6),
            }
        claim_terminal(EventType.RUN_FINISHED, finish_payload, None, {})
        result_payload = {
            "completion_summary": completion,
            "reward_metrics": reward or None,
            "logging_dir": logging_dir,
            "_terminal_events": terminal_events,
        }
        if self._profile_timing_enabled:
            result_payload["bridge_timing"] = dict(completion_payload.get("bridge_timing") or {})
        return result_payload

    async def publish_event_async(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
        classification: Optional[str] = None,
        family: Optional[str] = None,
        actor: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> None:
        self._touch_last_activity()
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
            classification=classification,
            family=family,
            actor=actor,
            visibility=visibility,
        )
        await self._enqueue_event_async(event)

    def publish_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
        classification: Optional[str] = None,
        family: Optional[str] = None,
        actor: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> None:
        self._touch_last_activity()
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
            classification=classification,
            family=family,
            actor=actor,
            visibility=visibility,
        )
        loop = self._loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if loop and loop.is_running():
            if running_loop and running_loop is loop:
                loop.create_task(self._enqueue_event_async(event))
                return
            future = asyncio.run_coroutine_threadsafe(self._enqueue_event_async(event), loop)
            future.result()
            return

        try:
            self.session.event_queue.put_nowait(event)
            self._published_events += 1
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning("Event queue full for session %s, dropping event", self.session.session_id)

    async def _enqueue_event_async(self, event: SessionEvent) -> None:
        await self.session.event_queue.put(event)
        self._published_events += 1

    def _touch_last_activity(self) -> None:
        try:
            self.session.last_activity_at = datetime.now(timezone.utc)
        except Exception:
            pass

    def _start_queue_pump(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
    ) -> tuple[Any, Any]:
        import threading
        from queue import Empty

        stop_signal = threading.Event()

        def runner() -> None:
            while not stop_signal.is_set():
                try:
                    item = event_queue.get(timeout=0.1)
                except Empty:
                    continue
                if not item:
                    continue
                try:
                    event_type, payload, turn = item
                except ValueError:
                    continue
                if event_type is None:
                    break
                handle_event(event_type, payload, turn=turn)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return stop_signal, thread

    def _drain_event_queue(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
    ) -> None:
        from queue import Empty

        while True:
            try:
                item = event_queue.get_nowait()
            except Empty:
                break
            if not item:
                continue
            try:
                event_type, payload, turn = item
            except ValueError:
                continue
            if event_type is None:
                continue
            handle_event(event_type, payload, turn=turn)
        logger.info("session(%s) published %s events", self.session.session_id, self._published_events)

    def get_workspace_dir(self) -> Optional[Path]:
        if self._workspace_path:
            self._workspace_path.mkdir(parents=True, exist_ok=True)
            return self._workspace_path
        candidate = getattr(self._agent, "workspace_dir", None) or self.request.workspace
        if candidate:
            path = Path(candidate).resolve()
            path.mkdir(parents=True, exist_ok=True)
            self._workspace_path = path
            return path
        return None

    def register_attachments(self, entries: Sequence[Dict[str, Any]]) -> None:
        for entry in entries:
            attachment_id = entry.get("id")
            if not attachment_id:
                continue
            self._attachment_store[str(attachment_id)] = dict(entry)

    def _format_attachment_helper(self, attachment_ids: Sequence[str]) -> str:
        if not attachment_ids:
            return ""
        workspace_dir = self.get_workspace_dir()
        helper_lines: list[str] = []
        for index, attachment_id in enumerate(attachment_ids, start=1):
            key = str(attachment_id)
            info = self._attachment_store.get(key)
            if not info:
                continue
            rel_path = info.get("relative_path")
            abs_path_value = info.get("absolute_path")
            abs_path = Path(abs_path_value).resolve() if abs_path_value else None
            if workspace_dir and rel_path:
                destination = workspace_dir / rel_path
                if abs_path and abs_path.exists() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(abs_path, destination)
            helper_lines.append(f"[Attachment {index}: {info.get('filename')} -> {rel_path or 'unknown'}]")
        return "\n".join(helper_lines)

    def _load_run_summary(self, logging_dir: Optional[str]) -> Optional[Dict[str, Any]]:
        if not logging_dir:
            return None
        try:
            run_path = Path(logging_dir) / "meta" / "run_summary.json"
            if not run_path.exists():
                return None
            return json.loads(run_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _normalize_usage_payload(self, usage: Dict[str, Any], *, latency_ms: Optional[int] = None) -> Dict[str, Any]:
        if not isinstance(usage, dict):
            return {}

        def _to_int(value: Any) -> int:
            try:
                return int(value)
            except Exception:
                return 0

        def _to_float(value: Any) -> Optional[float]:
            try:
                return float(value)
            except Exception:
                return None

        prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = _to_int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = _to_int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        cache_read = _to_int(usage.get("cache_read_tokens") or usage.get("cache_read") or 0)
        cache_write = _to_int(usage.get("cache_write_tokens") or usage.get("cache_write") or 0)
        cost_usd = _to_float(usage.get("cost_usd") or usage.get("cost") or usage.get("total_cost"))
        latency_ms_val = _to_int(usage.get("latency_ms") or 0)
        if not latency_ms_val:
            latency_s = _to_float(usage.get("latency_s") or usage.get("latency_seconds"))
            if latency_s is not None:
                latency_ms_val = int(latency_s * 1000)
        if not latency_ms_val and latency_ms is not None:
            latency_ms_val = int(latency_ms)

        normalized: Dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        if cache_read:
            normalized["cache_read_tokens"] = cache_read
        if cache_write:
            normalized["cache_write_tokens"] = cache_write
        if cost_usd is not None:
            normalized["cost_usd"] = cost_usd
        if latency_ms_val:
            normalized["latency_ms"] = latency_ms_val
        return normalized

    def _usage_from_run_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        diagnostics = summary.get("turn_diagnostics")
        if not isinstance(diagnostics, list):
            return {}

        totals: Dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        latency_total = 0.0
        cost_total = 0.0
        saw_usage = False

        for entry in diagnostics:
            if not isinstance(entry, dict):
                continue
            usage = entry.get("usage")
            if isinstance(usage, dict):
                saw_usage = True
                totals["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                totals["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                total_tokens = usage.get("total_tokens")
                if total_tokens is None:
                    total_tokens = (usage.get("prompt_tokens") or usage.get("input_tokens") or 0) + (
                        usage.get("completion_tokens") or usage.get("output_tokens") or 0
                    )
                totals["total_tokens"] += int(total_tokens or 0)
                totals["cache_read_tokens"] += int(usage.get("cache_read_tokens") or usage.get("cache_read") or 0)
                totals["cache_write_tokens"] += int(usage.get("cache_write_tokens") or usage.get("cache_write") or 0)
                cost_value = usage.get("cost_usd") or usage.get("cost")
                if isinstance(cost_value, (int, float)):
                    cost_total += float(cost_value)
            latency_value = entry.get("latency_seconds") or entry.get("latency_s")
            if isinstance(latency_value, (int, float)):
                latency_total += float(latency_value)

        if not saw_usage:
            return {}
        totals["total_tokens"] = totals["total_tokens"] or (totals["prompt_tokens"] + totals["completion_tokens"])
        normalized = self._normalize_usage_payload(totals)
        if latency_total:
            normalized["latency_ms"] = int(latency_total * 1000)
        if cost_total:
            normalized["cost_usd"] = cost_total
        return normalized

    def _extract_usage_metrics(
        self,
        result: Dict[str, Any],
        logging_dir: Optional[str],
        *,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        for key in ("usage", "usage_summary", "usage_metrics"):
            usage = result.get(key)
            if isinstance(usage, dict):
                normalized = self._normalize_usage_payload(usage, latency_ms=elapsed_ms)
                if normalized:
                    return normalized
        summary = self._load_run_summary(logging_dir)
        if summary:
            normalized = self._usage_from_run_summary(summary)
            if normalized:
                if elapsed_ms and not normalized.get("latency_ms"):
                    normalized["latency_ms"] = int(elapsed_ms)
                return normalized
        if elapsed_ms:
            return {"latency_ms": int(elapsed_ms)}
        return {}

    def _normalize_tool_call_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        call = payload.get("call") or payload.get("tool_call") or payload.get("tool")
        if not isinstance(call, dict):
            return payload
        call_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
        function = call.get("function") if isinstance(call.get("function"), dict) else None
        tool_name = call.get("name") or (function or {}).get("name")
        arguments = call.get("arguments")
        if arguments is None and isinstance(function, dict):
            arguments = function.get("arguments")
        action = None
        if isinstance(arguments, dict):
            action = arguments.get("action") or arguments.get("command") or arguments.get("operation")
        diff_preview = call.get("diff_preview") if isinstance(call, dict) else None
        progress = call.get("progress") if isinstance(call, dict) else None
        normalized = dict(payload)
        normalized.update(
            {
                "call": call,
                "call_id": call_id,
                "tool": tool_name,
                "action": action,
            }
        )
        if diff_preview is not None and "diff_preview" not in normalized:
            normalized["diff_preview"] = diff_preview
        if progress is not None and "progress" not in normalized:
            normalized["progress"] = progress
        return normalized

    def _normalize_tool_result_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload)
        message = normalized.get("message")
        if isinstance(message, dict):
            call_id = (
                normalized.get("call_id")
                or message.get("tool_call_id")
                or message.get("tool_call_id")
                or message.get("call_id")
            )
            content = message.get("content")
            normalized.setdefault("call_id", call_id)
            normalized.setdefault("result", content)
            normalized.setdefault("status", message.get("status") or ("error" if message.get("error") else "ok"))
            normalized.setdefault("error", bool(message.get("error")))
        if "result" not in normalized and "content" in normalized:
            normalized["result"] = normalized.get("content")
        artifact_ref = self._extract_artifact_ref(normalized)
        if artifact_ref is not None:
            normalized["artifact_ref"] = artifact_ref
        return normalized

    def _extract_artifact_ref(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidate = payload.get("artifact_ref")
        if isinstance(candidate, dict):
            normalized = self._normalize_artifact_ref(candidate)
            if normalized:
                return normalized
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            normalized = self._normalize_artifact_ref(artifact)
            if normalized:
                return normalized
        display = payload.get("display")
        if isinstance(display, dict):
            detail_artifact = display.get("detail_artifact")
            if isinstance(detail_artifact, dict):
                normalized = self._normalize_artifact_ref(detail_artifact)
                if normalized:
                    return normalized
        return None

    def _normalize_artifact_ref(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = payload.get("path")
        sha256 = payload.get("sha256")
        schema_version = payload.get("schema_version") or "artifact_ref_v1"
        if not isinstance(path, str) or not path.strip():
            return None
        if not isinstance(sha256, str) or not sha256.strip():
            return None
        size_bytes = payload.get("size_bytes")
        size_int = int(size_bytes) if isinstance(size_bytes, (int, float)) else None
        if size_int is None or size_int < 0:
            return None
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            kind = "tool_result"
        mime = payload.get("mime")
        if not isinstance(mime, str) or not mime.strip():
            mime = "text/plain"
        storage = payload.get("storage")
        if not isinstance(storage, str) or not storage.strip():
            storage = "workspace_file"
        normalized: Dict[str, Any] = {
            "schema_version": str(schema_version),
            "id": str(payload.get("id") or f"artifact:{sha256[:16]}"),
            "kind": str(kind),
            "mime": str(mime),
            "size_bytes": int(size_int),
            "sha256": str(sha256),
            "storage": str(storage),
            "path": str(path).strip(),
        }
        preview = payload.get("preview")
        if isinstance(preview, dict):
            normalized["preview"] = preview
        return normalized

    def _normalize_permission_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload or {})
        request_id = normalized.get("request_id") or normalized.get("id")
        items = normalized.get("items")
        first_item = items[0] if isinstance(items, list) and items else {}
        category = normalized.get("category") or first_item.get("category")
        pattern = normalized.get("pattern") or first_item.get("pattern")
        metadata = normalized.get("metadata") or first_item.get("metadata") or {}
        tool = metadata.get("function") or category
        summary = metadata.get("summary") or metadata.get("command") or metadata.get("path") or pattern or category
        kind = metadata.get("kind") or (str(category).title() if category else "Permission")
        normalized.setdefault("request_id", request_id)
        normalized.setdefault("tool", tool)
        normalized.setdefault("kind", kind)
        normalized.setdefault("summary", summary)
        if "diff" in metadata and "diff" not in normalized:
            normalized["diff"] = metadata.get("diff")
        if "rule_suggestion" in metadata and "rule_suggestion" not in normalized:
            normalized["rule_suggestion"] = metadata.get("rule_suggestion")
        if "approval_pattern" in metadata and "rule_suggestion" not in normalized:
            normalized["rule_suggestion"] = metadata.get("approval_pattern")
        if "default_scope" not in normalized:
            normalized["default_scope"] = metadata.get("default_scope") or "project"
        if "rewindable" not in normalized:
            normalized["rewindable"] = bool(metadata.get("rewindable")) if isinstance(metadata, dict) else False
        return normalized

    def _normalize_permission_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload or {})
        request_id = normalized.get("request_id") or normalized.get("id")
        decision = normalized.get("decision") or normalized.get("response")
        responses = normalized.get("responses")
        if decision is None and isinstance(responses, dict):
            if "default" in responses:
                decision = responses.get("default")
            elif "items" in responses and isinstance(responses.get("items"), dict):
                items = responses.get("items") or {}
                if items:
                    unique = {str(v) for v in items.values() if v is not None}
                    if len(unique) == 1:
                        decision = next(iter(unique))
        normalized.setdefault("request_id", request_id)
        if decision is not None:
            normalized.setdefault("decision", decision)
        return normalized

    def _normalize_task_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return normalize_task_event_payload(
            payload,
            parent_session_id=getattr(self.session, "session_id", None),
        )

    def _translate_runtime_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        turn: Optional[int],
    ) -> Optional[TranslatedRuntimeEvent]:
        mapping = {
            "turn_start": EventType.TURN_START,
            "stream.gap": EventType.STREAM_GAP,
            "conversation.compaction.start": EventType.CONVERSATION_COMPACTION_START,
            "conversation.compaction.end": EventType.CONVERSATION_COMPACTION_END,
            "assistant.message.start": EventType.ASSISTANT_MESSAGE_START,
            "assistant.message.delta": EventType.ASSISTANT_MESSAGE_DELTA,
            "assistant.message.end": EventType.ASSISTANT_MESSAGE_END,
            "assistant.reasoning.delta": EventType.ASSISTANT_REASONING_DELTA,
            "assistant.thought_summary.delta": EventType.ASSISTANT_THOUGHT_SUMMARY_DELTA,
            "tool.exec.start": EventType.TOOL_EXEC_START,
            "tool.exec.stdout.delta": EventType.TOOL_EXEC_STDOUT_DELTA,
            "tool.exec.stderr.delta": EventType.TOOL_EXEC_STDERR_DELTA,
            "tool.exec.end": EventType.TOOL_EXEC_END,
            "assistant_message": EventType.ASSISTANT_MESSAGE,
            "assistant_delta": EventType.ASSISTANT_DELTA,
            "user_message": EventType.USER_MESSAGE,
            "tool_call": EventType.TOOL_CALL,
            "tool.result": EventType.TOOL_RESULT_DOT,
            "tool_result": EventType.TOOL_RESULT,
            "todo_event": EventType.TOOL_RESULT,
            "permission_request": EventType.PERMISSION_REQUEST,
            "permission_response": EventType.PERMISSION_RESPONSE,
            "checkpoint_list": EventType.CHECKPOINT_LIST,
            "checkpoint_restored": EventType.CHECKPOINT_RESTORED,
            "skills_catalog": EventType.SKILLS_CATALOG,
            "skills_selection": EventType.SKILLS_SELECTION,
            "ctree_node": EventType.CTREE_NODE,
            "ctree_snapshot": EventType.CTREE_SNAPSHOT,
            "task_event": EventType.TASK_EVENT,
            "warning": EventType.WARNING,
            "reward_update": EventType.REWARD_UPDATE,
            "limits_update": EventType.LIMITS_UPDATE,
            "completion": EventType.COMPLETION,
            "log_link": EventType.LOG_LINK,
            "error": EventType.ERROR,
            "run_finished": EventType.RUN_FINISHED,
        }
        evt = mapping.get(event_type)
        if not evt:
            return None

        normalized_payload: Dict[str, Any] = dict(payload or {})
        event_contract = _default_runtime_event_contract(event_type)
        if event_type == "todo_event":
            try:
                todo_update = normalized_payload.get("todo")
                if isinstance(todo_update, dict):
                    self.session.metadata["todo_last_update"] = dict(todo_update)
                    self._persist_metadata_snapshot_threadsafe()
            except Exception:
                pass
        if evt is EventType.TURN_START:
            if "mode" not in normalized_payload and self._mode:
                normalized_payload["mode"] = self._mode
        elif evt is EventType.ASSISTANT_MESSAGE:
            message = normalized_payload.get("message")
            text = normalized_payload.get("text")
            if not isinstance(text, str):
                text = ""
            if not text and isinstance(message, dict):
                content = message.get("content")
                text = content if isinstance(content, str) else ""
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.ASSISTANT_DELTA:
            text = normalized_payload.get("text")
            if not isinstance(text, str):
                text = ""
            message_id = normalized_payload.get("message_id") or normalized_payload.get("messageId") or normalized_payload.get("id")
            normalized_payload = {"text": text, "message_id": message_id}
        elif evt is EventType.USER_MESSAGE:
            message = normalized_payload.get("message")
            text = normalized_payload.get("text")
            if not isinstance(text, str):
                text = ""
            if not text and isinstance(message, dict):
                content = message.get("content")
                text = content if isinstance(content, str) else ""
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.TOOL_CALL:
            normalized_payload = self._normalize_tool_call_payload(normalized_payload)
        elif evt in {EventType.TOOL_RESULT, EventType.TOOL_RESULT_DOT}:
            normalized_payload = self._normalize_tool_result_payload(normalized_payload)
        elif evt is EventType.PERMISSION_REQUEST:
            normalized_payload = self._normalize_permission_request(normalized_payload)
        elif evt is EventType.PERMISSION_RESPONSE:
            normalized_payload = self._normalize_permission_response(normalized_payload)
        elif evt is EventType.TASK_EVENT:
            normalized_payload = self._normalize_task_event(normalized_payload)
        return evt, normalized_payload, turn, event_contract

    def _resolve_skill_catalog(self) -> Dict[str, Any]:
        config = self.current_runtime_config()
        workspace = self.get_workspace_dir()
        if not workspace:
            ws_cfg = (config.get("workspace") or {}) if isinstance(config, dict) else {}
            workspace = Path(str(ws_cfg.get("root") or self.request.workspace or ".")).resolve()
        plugin_manifests = discover_plugin_manifests(config, str(workspace))
        plugin_skill_paths: List[Path] = []
        for manifest in plugin_manifests:
            for rel in getattr(manifest, "skills_paths", []) or []:
                try:
                    base = getattr(manifest, "root", None) or str(workspace)
                    plugin_skill_paths.append(Path(str(base)) / rel)
                except Exception:
                    continue
        prompt_skills, graph_skills = load_skills(
            config,
            str(workspace),
            plugin_skill_paths=plugin_skill_paths,
        )
        selection = normalize_skill_selection(config, self.session.metadata.get("skills_selection"))
        _, _, enabled_map = apply_skill_selection(prompt_skills, graph_skills, selection)
        catalog = build_skill_catalog(prompt_skills, graph_skills, selection=selection, enabled_map=enabled_map)
        snapshot = None
        try:
            snapshot = plugin_snapshot(plugin_manifests)
        except Exception:
            snapshot = None
        return {
            "catalog": catalog,
            "selection": selection,
            "sources": {
                "config_path": self.request.config_path,
                "workspace": str(workspace),
                "plugin_count": len(plugin_manifests),
                "plugin_snapshot": snapshot,
                "skill_paths": [str(p) for p in plugin_skill_paths],
            },
        }

    def get_skill_catalog(self) -> Dict[str, Any]:
        try:
            self._skills_catalog_cache = self._resolve_skill_catalog()
        except Exception:
            if self._skills_catalog_cache is None:
                self._skills_catalog_cache = {"catalog": {"skills": []}, "selection": {}, "sources": {}}
        return dict(self._skills_catalog_cache or {})

    def get_ctree_snapshot(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(self._ctree_snapshot_cache, dict):
            payload.update(self._ctree_snapshot_cache)
        if self._ctree_last_node:
            payload.setdefault("last_node", dict(self._ctree_last_node))
        return payload
