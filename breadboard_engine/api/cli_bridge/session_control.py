"""Command, permission, and pause/resume control for a CLI bridge session."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from breadboard_engine.auth.enforcer import apply_dotted_overrides
from breadboard_engine.checkpointing.checkpoint_manager import CheckpointManager
from breadboard_engine.compilation.effective_operation_policy import (
    policy_pack_for_config_authority,
)
from breadboard_engine.model_roles import (
    ModelRoleProblem,
    ModelRoleResolutionError,
    resolve_role_name,
)
from breadboard_engine.permissions import (
    PermissionAuthority,
    normalize_permission_responses,
    resolve_permission_responses,
)
from breadboard_engine.skills.registry import normalize_skill_selection

from .events import EventType

logger = logging.getLogger(__name__)


class SessionControlHost(Protocol):
    """Explicit host port retained by command and permission control."""

    permission_authority: PermissionAuthority
    session: Any
    registry: Any
    request: Any
    _closed: bool
    _checkpoint_manager: Any
    _permission_queue: Any
    _permission_decision_lock: asyncio.Lock
    _product_session_lock: threading.RLock
    _resume_event: asyncio.Event
    _agent: Any
    _model_role_lock: Any
    _active_model_role: Optional[str]
    _model_override: Optional[str]
    _mode: Optional[str]
    _prepared_runtime_config: Optional[Dict[str, Any]]
    _skills_catalog_cache: Optional[Dict[str, Any]]
    _consumed_permission_responses: Dict[tuple[str, str, str], int]

    def get_workspace_dir(self) -> Optional[Path]: ...
    async def publish_event_async(
        self, event_type: EventType, payload: Dict[str, Any], **kwargs: Any
    ) -> Any: ...
    def _persist_metadata_snapshot_threadsafe(self) -> None: ...
    def transition_product_session(self, transition: str, *args: Any) -> None: ...
    def current_runtime_config(self) -> Dict[str, Any]: ...
    def _rollback_runtime_overrides(
        self, overrides: Dict[str, Any], restore: Optional[tuple[str, bool, Any]] = None
    ) -> bool: ...
    def get_skill_catalog(self) -> Dict[str, Any]: ...
    def _signal_control(self, kind: str) -> bool: ...
    def _fail_control_transition(self, code: str, detail: str) -> None: ...
    def _request_stop(self, reason: str) -> bool: ...
    def _locked_target(self, role: Optional[str] = None) -> Dict[str, Any]: ...
    @staticmethod
    def _target_route(target: Mapping[str, Any]) -> str: ...
    def _apply_model_override(self) -> bool: ...




def _canonical_permission_resolution(
    response: Any,
    responses: Any,
    requested_ids: Sequence[str] = (),
    missing_response: str = "reject",
) -> str:
    return resolve_permission_responses(
        response, responses, requested_ids, missing_response
    )


def _canonical_permission_responses(responses: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_permission_responses(responses)

def _control_kind(item: Any) -> str:
    return (
        "stop"
        if item is None
        else item.strip().lower()
        if isinstance(item, str)
        else str(
            item.get("kind") or item.get("type") or ("stop" if item.get("stop") else "")
        )
        .strip()
        .lower()
        if isinstance(item, dict)
        else ""
    )


class _PauseAwareControlQueue:
    def __init__(self, queue: Any) -> None:
        self._queue = queue

    def __getattr__(self, name: str) -> Any:
        queue = object.__getattribute__(self, "_queue")
        return getattr(queue, name)

    def get_nowait(self) -> Any:
        item = self._queue.get_nowait()
        while _control_kind(item) == "pause":
            item = self._queue.get()
        return item




class SessionControlController:
    """Owns command dispatch, permission decisions, and control transitions."""

    def __init__(self, runner: SessionControlHost) -> None:
        self._runner = runner

    def _upsert_permission_rule(
        self,
        workspace_dir: Path | str,
        *,
        category: str,
        pattern: str,
        decision: str,
        scope: str,
    ) -> bool:
        return self._runner.permission_authority.update_rule(
            workspace_dir,
            category=category,
            pattern=pattern,
            decision=decision,
            scope=scope,
        )

    async def handle_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        durable_reconfigure: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        runner = self._runner
        if runner._closed:
            raise RuntimeError("session is closed")
        if command == "set_model" and runner._model_role_lock is not None:
            raise ModelRoleResolutionError(
                ModelRoleProblem(
                    "lock_immutable",
                    "model overrides are rejected after session.start",
                    "$.role_overrides",
                    {"lock_hash": runner.session.metadata.get("model_role_lock_hash")},
                )
            )
        payload = payload or {}
        match command:
            case "list_checkpoints":
                manager = runner._checkpoint_manager
                if manager is None:
                    workspace_dir = runner.get_workspace_dir()
                    if not workspace_dir:
                        raise RuntimeError("workspace not ready")
                    manager = CheckpointManager(workspace_dir)
                    runner._checkpoint_manager = manager
                checkpoints = [cp.as_payload() for cp in manager.list_checkpoints()]
                await runner.publish_event_async(
                    EventType.CHECKPOINT_LIST, {"checkpoints": checkpoints}
                )
                return {"status": "ok", "count": len(checkpoints)}
            case "restore_checkpoint":
                checkpoint_id = (
                    payload.get("checkpoint_id")
                    or payload.get("checkpointId")
                    or payload.get("id")
                )
                if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
                    raise ValueError(
                        "restore_checkpoint requires non-empty 'checkpoint_id'"
                    )
                mode = str(payload.get("mode") or "code").strip().lower()
                if mode not in {"code", "conversation", "both"}:
                    raise ValueError(
                        "restore_checkpoint 'mode' must be one of: code, conversation, both"
                    )
                workspace_dir = runner.get_workspace_dir()
                manager = runner._checkpoint_manager
                if manager is None:
                    workspace_dir = runner.get_workspace_dir()
                    if not workspace_dir:
                        raise RuntimeError("workspace not ready")
                    manager = CheckpointManager(workspace_dir)
                    runner._checkpoint_manager = manager
                prune = True
                if mode in {"code", "both"}:
                    manager.restore_checkpoint(checkpoint_id.strip(), prune=prune)
                if mode in {"conversation", "both"}:
                    try:
                        snapshot = manager.load_snapshot(checkpoint_id.strip())
                    except Exception:
                        snapshot = None
                    if snapshot:
                        if workspace_dir:
                            active_logical = (
                                ".breadboard/checkpoints/active_snapshot.json"
                            )
                            try:
                                with WorkspaceFilesystem.open_anchored_root(
                                    workspace_dir,
                                    create=False,
                                ) as filesystem:
                                    filesystem.create_directory(
                                        ".breadboard/checkpoints"
                                    )
                                    filesystem.write_text(
                                        active_logical,
                                        json.dumps(
                                            snapshot, indent=2, ensure_ascii=False
                                        ),
                                        encoding="utf-8",
                                    )
                                    active_path = Path(
                                        filesystem.display_path(active_logical)
                                    )
                                runner.session.metadata["conversation_snapshot"] = {
                                    "checkpoint_id": checkpoint_id.strip(),
                                    "path": str(active_path),
                                }
                                runner._persist_metadata_snapshot_threadsafe()
                            except Exception:
                                pass
                await runner.publish_event_async(
                    EventType.CHECKPOINT_RESTORED,
                    {
                        "checkpoint_id": checkpoint_id.strip(),
                        "mode": mode,
                        "prune": prune,
                    },
                )
                checkpoints = [cp.as_payload() for cp in manager.list_checkpoints()]
                await runner.publish_event_async(
                    EventType.CHECKPOINT_LIST, {"checkpoints": checkpoints}
                )
                return {
                    "status": "ok",
                    "checkpoint_id": checkpoint_id.strip(),
                    "mode": mode,
                    "prune": prune,
                }
            case "permission_decision":
                async with runner._permission_decision_lock:
                    request_id = next(
                        (
                            payload.get(key)
                            for key in (
                                "request_id",
                                "requestId",
                                "permission_id",
                                "permissionId",
                                "id",
                            )
                            if payload.get(key)
                        ),
                        None,
                    )
                    decision = payload.get("decision") or payload.get("response")
                    if not isinstance(request_id, str) or not request_id.strip():
                        raise ValueError(
                            "permission_decision requires non-empty 'request_id'"
                        )
                    if not isinstance(decision, str) or not decision.strip():
                        raise ValueError(
                            "permission_decision requires non-empty 'decision'"
                        )
                    request_id = request_id.strip()
                    resolved = runner.permission_authority.resolve_decision(decision)
                    normalized = resolved.token
                    pending = runner.session.metadata.get("pending_permissions")
                    active = (
                        pending[0]
                        if isinstance(pending, list)
                        and pending
                        and isinstance(pending[0], dict)
                        else {}
                    )
                    if (
                        str(active.get("request_id") or active.get("id") or "")
                        != request_id
                    ):
                        raise ValueError("permission request is not active")
                    response_value = resolved.value
                    rule, scope, note = (
                        payload.get("rule"),
                        payload.get("scope"),
                        payload.get("note"),
                    )
                    category = (
                        self.infer_permission_category(request_id) if rule else None
                    )
                    workspace_dir = runner.get_workspace_dir() if rule else None
                    if rule:
                        metadata = dict(runner.session.metadata or {})
                        rules = list(metadata.get("permission_rules") or [])
                        rules.append(
                            {
                                "request_id": request_id,
                                "decision": normalized,
                                "rule": rule,
                                "scope": scope,
                                "note": note,
                            }
                        )
                        metadata["permission_rules"] = rules
                        persist_rule = bool(
                            resolved.persistent and category and workspace_dir
                        )
                        if persist_rule:
                            persisted = self._upsert_permission_rule(
                                workspace_dir,
                                category=category,
                                pattern=str(rule).strip(),
                                decision=resolved.rule_decision or "allow",
                                scope=str(scope or "project"),
                            )
                        else:
                            persisted = True
                        if not persisted:
                            runner.transition_product_session(
                                "fail",
                                "permission_commit_failed",
                                "failed to commit permission decision",
                            )
                            raise RuntimeError(
                                "failed to commit permission decision"
                            )
                        try:
                            await runner.registry.update_metadata(
                                runner.session.session_id, metadata=metadata
                            )
                        except Exception:
                            if not persist_rule:
                                runner.transition_product_session(
                                    "fail",
                                    "permission_commit_failed",
                                    "failed to commit permission decision",
                                )
                                raise
                            runner.session.metadata = metadata
                            logger.warning(
                                "Permission metadata projection failed after durable rule commit",
                                exc_info=True,
                            )
                    detail = await self.handle_command(
                        "respond_permission",
                        {"request_id": request_id, "response": response_value},
                    )
                    if normalized in {"deny-stop", "deny_stop"} or bool(
                        payload.get("stop")
                    ):
                        await self.handle_command("stop", {})
                    return {
                        "status": "ok",
                        "request_id": request_id,
                        "decision": response_value,
                        "delivered": detail,
                    }
            case "set_skills":
                selection_payload = dict(payload or {})
                if (
                    "selected" in selection_payload
                    and "allowlist" not in selection_payload
                ):
                    selection_payload["allowlist"] = selection_payload.get("selected")
                with runner._product_session_lock:
                    config = (
                        dict(getattr(runner._agent, "config", {}) or {})
                        if runner._agent
                        else {}
                    )
                    selection = normalize_skill_selection(config, selection_payload)
                    overrides = {
                        "skills.allowlist": selection.get("allowlist") or [],
                        "skills.blocklist": selection.get("blocklist") or [],
                    }
                    previous = runner.current_runtime_config()
                    had_skills = "skills" in config
                    previous_skills = (
                        json.loads(json.dumps(config.get("skills")))
                        if had_skills
                        else None
                    )
                    rollback = {
                        "skills.allowlist": (previous_skills or {}).get("allowlist")
                        or [],
                        "skills.blocklist": (previous_skills or {}).get("blocklist")
                        or [],
                    }
                    try:
                        if (
                            runner._agent
                            and runner._agent.apply_runtime_overrides(overrides)
                            is False
                        ):
                            raise RuntimeError("failed to apply skills configuration")
                        prepared = apply_dotted_overrides(previous, overrides)
                        if durable_reconfigure is not None:
                            durable_reconfigure(prepared)
                    except Exception as error:
                        rolled_back = runner._rollback_runtime_overrides(
                            rollback, ("skills", had_skills, previous_skills)
                        )
                        if not isinstance(error, OSError) or not rolled_back:
                            runner.transition_product_session(
                                "fail",
                                "runtime_reconfigure_failed",
                                "failed to apply skills configuration",
                            )
                        raise
                    runner.session.metadata["skills_selection"] = selection
                    runner._prepared_runtime_config = prepared
                    runner._persist_metadata_snapshot_threadsafe()
                    runner._skills_catalog_cache = None
                    catalog_payload = runner.get_skill_catalog()
                await runner.publish_event_async(
                    EventType.SKILLS_SELECTION, {"selection": selection}
                )
                await runner.publish_event_async(
                    EventType.SKILLS_CATALOG, catalog_payload
                )
                return {
                    "status": "ok",
                    "selection": selection,
                    "catalog": catalog_payload.get("catalog"),
                }
            case "pause":
                with runner._product_session_lock:
                    transitioned = False
                    was_resumed = runner._resume_event.is_set()
                    try:
                        runner._resume_event.clear()
                        runner.transition_product_session(
                            "pause", str(payload.get("reason") or "operator request")
                        )
                        transitioned = True
                        runner._signal_control("pause")
                    except Exception:
                        runner._resume_event.set() if was_resumed else None
                        (
                            runner._fail_control_transition(
                                "pause_control_failed",
                                "failed to deliver pause control",
                            )
                            if transitioned
                            else None
                        )
                        raise
                return {"status": "ok", "paused": True}
            case "resume":
                with runner._product_session_lock:
                    runner.transition_product_session("resume")
                    try:
                        pending = runner.session.metadata.get("pending_permissions")
                        head = (
                            pending[0]
                            if isinstance(pending, list)
                            and pending
                            and isinstance(pending[0], dict)
                            else None
                        )
                        request = (
                            head.get("request")
                            if head and isinstance(head.get("request"), dict)
                            else {}
                        )
                        (
                            self.update_pending_permissions(
                                "permission_request",
                                request,
                                source=str(head.get("source") or "session"),
                                task_session_id=head.get("task_session_id"),
                                subagent_type=head.get("subagent_type"),
                            )
                            if head
                            else None
                        )
                        runner._signal_control("resume")
                        runner._resume_event.set()
                    except Exception:
                        runner._fail_control_transition(
                            "resume_control_failed", "failed to deliver resume control"
                        )
                        raise
                return {"status": "ok", "paused": False}
            case "stop":
                stopping = runner._request_stop("stop command")
                return {"status": "ok", "stopping": stopping}
            case "set_role" | "set_model_role":
                if runner._model_role_lock is None:
                    raise ModelRoleResolutionError(
                        ModelRoleProblem(
                            "known_role_unbound",
                            "no model-role lock is active",
                            "$.roles",
                        )
                    )
                if (
                    getattr(runner.session, "product_session", None) is not None
                    and durable_reconfigure is None
                ):
                    raise RuntimeError(
                        "model-role transitions require durable reconfiguration"
                    )
                async with runner.session.admission_lock:
                    if runner.session.active_turn_id is not None:
                        raise ModelRoleResolutionError(
                            ModelRoleProblem(
                                "model_role_transition_active_turn",
                                "model roles may change only between turns",
                                "$.role",
                            )
                        )
                    requested = str(
                        payload.get("role") or payload.get("model_role") or ""
                    ).strip()
                    role = resolve_role_name(runner._model_role_lock, requested or None)
                    target = runner._locked_target(role)
                    route = runner._target_route(target)
                    with runner._product_session_lock:
                        previous_config = runner.current_runtime_config()
                        previous_role = runner._active_model_role
                        previous_model = runner._model_override
                        previous_metadata = dict(runner.session.metadata)
                        prepared = dict(previous_config)
                        prepared["active_model_role"] = role
                        prepared["model_role_lock"] = dict(runner._model_role_lock)
                        prepared["providers"] = dict(prepared.get("providers") or {})
                        prepared["providers"]["default_model"] = route
                        runner._active_model_role = role
                        runner._model_override = route
                        try:
                            if not runner._apply_model_override():
                                raise RuntimeError("failed to apply locked model role")
                            runner.session.metadata.update(
                                {"active_model_role": role, "model": route}
                            )
                            runner._prepared_runtime_config = prepared
                            if durable_reconfigure is not None:
                                durable_reconfigure(prepared)
                        except Exception:
                            runner._active_model_role = previous_role
                            runner._model_override = previous_model
                            runner.session.metadata.clear()
                            runner.session.metadata.update(previous_metadata)
                            runner._prepared_runtime_config = previous_config
                            runner._apply_model_override()
                            raise
                    try:
                        await runner.registry.update_metadata(
                            runner.session.session_id,
                            metadata=dict(runner.session.metadata or {}),
                        )
                    except Exception:
                        with runner._product_session_lock:
                            runner._active_model_role = previous_role
                            runner._model_override = previous_model
                            runner.session.metadata.clear()
                            runner.session.metadata.update(previous_metadata)
                            runner._prepared_runtime_config = previous_config
                            runner._apply_model_override()
                            if durable_reconfigure is not None:
                                durable_reconfigure(previous_config)
                        raise
                    return {
                        "status": "ok",
                        "role": role,
                        "model": route,
                        "target": dict(target),
                    }
            case "set_model":
                if runner._model_role_lock is not None:
                    raise ModelRoleResolutionError(
                        ModelRoleProblem(
                            "model_role_lock_immutable",
                            "direct model mutation is forbidden while a model-role lock is active",
                            "$.model",
                        )
                    )
                model_value = payload.get("model")
                if not isinstance(model_value, str) or not model_value.strip():
                    raise ValueError("set_model requires non-empty 'model'")
                model_value = model_value.strip()
                cfg = runner.current_runtime_config()
                policy = policy_pack_for_config_authority(
                    cfg,
                    session_id=runner.session.session_id,
                    config_path=runner.request.config_path,
                    logger=logger,
                )
                if (
                    policy.model_allowlist is not None or policy.model_denylist
                ) and not policy.is_model_allowed(model_value):
                    raise ValueError(f"set_model denied by policy: {model_value}")
                with runner._product_session_lock:
                    previous, previous_model = (
                        runner.current_runtime_config(),
                        runner._model_override,
                    )
                    agent_config = (
                        getattr(runner._agent, "config", {}) if runner._agent else {}
                    )
                    agent_providers = (
                        agent_config.get("providers")
                        if isinstance(agent_config, dict)
                        and isinstance(agent_config.get("providers"), dict)
                        else {}
                    )
                    rollback_model = (
                        "default_model" in agent_providers,
                        agent_providers.get("default_model"),
                    )
                    prepared = apply_dotted_overrides(
                        previous, {"providers.default_model": model_value}
                    )
                    try:
                        runner._model_override = model_value
                        if not runner._apply_model_override():
                            raise RuntimeError("failed to apply model configuration")
                        if durable_reconfigure is not None:
                            durable_reconfigure(prepared)
                    except Exception as error:
                        runner._model_override = previous_model
                        rolled_back = True
                        try:
                            providers = (
                                runner._agent.config.setdefault("providers", {})
                                if runner._agent
                                else {}
                            )
                            if rollback_model[0]:
                                providers["default_model"] = rollback_model[1]
                            else:
                                providers.pop("default_model", None)
                        except Exception:
                            rolled_back = False
                            logger.exception("Failed to roll back model configuration")
                        if not isinstance(error, OSError) or not rolled_back:
                            runner.transition_product_session(
                                "fail",
                                "runtime_reconfigure_failed",
                                "failed to apply model configuration",
                            )
                        raise
                    runner.session.metadata["model"] = model_value
                    runner._prepared_runtime_config = prepared
                return {"status": "ok", "model": model_value}
            case "set_mode":
                mode_value = payload.get("mode")
                if not isinstance(mode_value, str) or not mode_value.strip():
                    raise ValueError("set_mode requires non-empty 'mode'")
                mode_value = mode_value.strip()
                with runner._product_session_lock:
                    overrides = {"mode": mode_value}
                    previous, previous_mode = (
                        runner.current_runtime_config(),
                        runner._mode,
                    )
                    agent_config = (
                        getattr(runner._agent, "config", {}) if runner._agent else {}
                    )
                    mode_restore = (
                        "mode",
                        "mode" in agent_config,
                        agent_config.get("mode"),
                    )
                    prepared = apply_dotted_overrides(previous, overrides)
                    try:
                        if (
                            runner._agent
                            and runner._agent.apply_runtime_overrides(overrides)
                            is False
                        ):
                            raise RuntimeError("failed to apply mode configuration")
                        if durable_reconfigure is not None:
                            durable_reconfigure(prepared)
                    except Exception as error:
                        rolled_back = runner._rollback_runtime_overrides(
                            {"mode": previous.get("mode")}, mode_restore
                        )
                        runner._mode = previous_mode
                        if not isinstance(error, OSError) or not rolled_back:
                            runner.transition_product_session(
                                "fail",
                                "runtime_reconfigure_failed",
                                "failed to apply mode configuration",
                            )
                        raise
                    runner._mode = mode_value
                    runner.session.metadata["mode"] = mode_value
                    runner._prepared_runtime_config = prepared
                return {"status": "ok", "mode": mode_value}
            case "session_child_next" | "session_child_previous" | "session_parent":
                child_session_id = payload.get("child_session_id") or payload.get(
                    "childSessionId"
                )
                parent_session_id = payload.get("parent_session_id") or payload.get(
                    "parentSessionId"
                )
                target_session_id = payload.get("target_session_id") or payload.get(
                    "targetSessionId"
                )

                def _norm(value: Any) -> Optional[str]:
                    if not isinstance(value, str):
                        return None
                    trimmed = value.strip()
                    return trimmed or None

                child_session_id = _norm(child_session_id)
                parent_session_id = _norm(parent_session_id)
                target_session_id = _norm(target_session_id)
                if command == "session_parent":
                    resolved_target = (
                        target_session_id or parent_session_id or child_session_id
                    )
                else:
                    resolved_target = (
                        target_session_id or child_session_id or parent_session_id
                    )
                if not resolved_target:
                    return {
                        "status": "ok",
                        "command": command,
                        "switched": False,
                        "reason": "target_missing",
                    }
                target_record = await runner.registry.get(resolved_target)
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
                if self.debug_permissions_enabled():
                    event_payload = await self.emit_debug_permission_request(payload)
                    return {
                        "status": "ok",
                        "debug": True,
                        "request_id": event_payload.get("request_id"),
                    }
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
                response = (
                    payload.get("response")
                    or payload.get("decision")
                    or payload.get("default")
                )
                responses = payload.get("responses")
                items = payload.get("items")
                if not isinstance(request_id, str) or not request_id.strip():
                    raise ValueError(
                        "respond_permission requires non-empty 'request_id'/'permission_id'/'id'"
                    )
                if isinstance(items, dict) and not isinstance(responses, dict):
                    responses = {"items": dict(items)}
                canonical_responses = (
                    runner.permission_authority.normalize_responses(responses)
                    if isinstance(responses, dict)
                    else None
                )
                normalized_request_id = request_id.strip()
                pending = runner.session.metadata.get("pending_permissions")
                pending_request = (
                    next(
                        (
                            entry.get("request")
                            for entry in pending
                            if isinstance(entry, dict)
                            and entry.get("request_id") == normalized_request_id
                        ),
                        {},
                    )
                    if isinstance(pending, list)
                    else {}
                )
                requested_ids = runner.permission_authority.request_item_ids(
                    pending_request
                )
                resolution = runner.permission_authority.resolve_responses(
                    response,
                    canonical_responses,
                    requested_ids,
                    runner.permission_authority.default_response(
                        runner.current_runtime_config()
                    ),
                )
                queue = getattr(runner, "_permission_queue", None)
                if queue is None:
                    if self.debug_permissions_enabled():
                        response_payload: Dict[str, Any] = {
                            "request_id": normalized_request_id
                        }
                        if canonical_responses is not None:
                            response_payload["responses"] = canonical_responses
                        else:
                            response_payload["response"] = resolution
                            response_payload["decision"] = resolution
                        with runner._product_session_lock:
                            self.update_pending_permissions(
                                "permission_response",
                                response_payload,
                                source="session",
                            )
                        await runner.publish_event_async(
                            EventType.PERMISSION_RESPONSE, response_payload
                        )
                        return {
                            "status": "ok",
                            "request_id": normalized_request_id,
                            "decision": resolution,
                            "delivered": response_payload,
                            "debug": True,
                        }
                    self.discard_undeliverable_permission(normalized_request_id)
                    raise ValueError("no permission request is active")
                if canonical_responses is not None:
                    item: Dict[str, Any] = {
                        "request_id": normalized_request_id,
                        "responses": canonical_responses,
                    }
                else:
                    if not isinstance(response, str) or not response.strip():
                        raise ValueError(
                            "respond_permission requires non-empty 'response' when 'responses' is not provided"
                        )
                    item = {
                        "permission_id": normalized_request_id,
                        "response": resolution,
                    }
                with runner._product_session_lock:
                    runner.transition_product_session(
                        "resolve_approval", normalized_request_id, resolution
                    )
                    try:
                        put_nowait = getattr(queue, "put_nowait", None)
                        if callable(put_nowait):
                            put_nowait(item)
                        else:
                            queue.put(item)
                    except Exception as exc:
                        runner.transition_product_session(
                            "fail",
                            "permission_delivery_failed",
                            "failed to deliver permission response",
                        )
                        raise RuntimeError(
                            f"failed to deliver permission response: {exc}"
                        ) from exc
                    self.update_pending_permissions(
                        "permission_response", item, source="session", consume_fifo=True
                    )
                return {
                    "status": "ok",
                    "request_id": normalized_request_id,
                    "decision": resolution,
                    "delivered": item,
                }
            case _:
                raise ValueError(f"Unsupported command: {command}")

    def debug_permissions_enabled(self) -> bool:
        runner = self._runner
        try:
            meta = runner.session.metadata or {}
            if isinstance(meta, dict) and meta.get("debug_permissions"):
                return True
        except Exception:
            pass
        return bool(os.environ.get("BREADBOARD_DEBUG_PERMISSIONS"))

    async def emit_debug_permission_request(
        self, payload: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        runner = self._runner
        data = dict(payload or {})
        request_id = data.get("request_id") or f"debug-perm-{uuid.uuid4().hex[:8]}"
        suite = data.get("suite") if isinstance(data.get("suite"), str) else None
        summary = (
            f"Tool requests permission to run bash{f' ({suite})' if suite else ''}."
        )
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
        self.update_pending_permissions(
            "permission_request", event_payload, source="session"
        )
        await runner.publish_event_async(EventType.PERMISSION_REQUEST, event_payload)
        return event_payload

    def pending_permission_key(self, entry: Dict[str, Any]) -> tuple[str, str, str]:
        source = str(entry.get("source") or "session")
        task_id = str(entry.get("task_session_id") or "")
        req_id = str(entry.get("request_id") or entry.get("id") or "")
        return source, task_id, req_id

    def infer_permission_category(self, request_id: str) -> Optional[str]:
        runner = self._runner
        pending = runner.session.metadata.get("pending_permissions")
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

    def update_pending_permissions(
        self,
        kind: str,
        info: Dict[str, Any],
        *,
        source: str = "session",
        task_session_id: Optional[str] = None,
        subagent_type: Optional[str] = None,
        consume_fifo: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        runner = self._runner
        req_id = next(
            (
                info.get(key)
                for key in (
                    "request_id",
                    "requestId",
                    "permission_id",
                    "permissionId",
                    "id",
                )
                if info.get(key)
            ),
            None,
        )
        if not isinstance(req_id, str) or not req_id.strip():
            return None
        request_id = req_id.strip()
        with runner._product_session_lock:
            pending = runner.session.metadata.get("pending_permissions")
            pending = pending if isinstance(pending, list) else []
            entry: Dict[str, Any] = {
                "source": str(source or "session"),
                "request_id": request_id,
            }
            entry.update(
                {
                    key: value
                    for key, value in (
                        ("task_session_id", task_session_id),
                        ("subagent_type", subagent_type),
                    )
                    if value
                }
            )
            entry_key = self.pending_permission_key(entry)
            activate = None
            ready = None
            project_before_activation = kind == "permission_response"
            if kind == "permission_request":
                entry["request"] = dict(info or {})
                match = next(
                    (
                        i
                        for i, item in enumerate(pending)
                        if self.pending_permission_key(item) == entry_key
                    ),
                    None,
                )
                normalized = list(pending)
                project_before_activation = match is not None
                if match is None:
                    normalized.append(entry)
                    activate = entry if not pending else None
                else:
                    normalized[match] = entry
                    activate = entry if match == 0 else None
            elif kind == "permission_response":
                if not consume_fifo:
                    suppressed = runner._consumed_permission_responses.get(entry_key, 0)
                    if suppressed:
                        if suppressed == 1:
                            runner._consumed_permission_responses.pop(entry_key)
                        else:
                            runner._consumed_permission_responses[entry_key] = (
                                suppressed - 1
                            )
                        return None
                match = next(
                    (
                        i
                        for i, item in enumerate(pending)
                        if (
                            str(item.get("request_id") or item.get("id") or "")
                            == request_id
                            if consume_fifo
                            else self.pending_permission_key(item) == entry_key
                        )
                    ),
                    None,
                )
                normalized = list(pending)
                if match is not None and not consume_fifo and match:
                    normalized[match] = {
                        **normalized[match],
                        "deferred_response": dict(info),
                    }
                    ready = []
                elif match is not None:
                    product_session = getattr(runner.session, "product_session", None)
                    if not consume_fifo:
                        if product_session:
                            request = normalized[match].get("request")
                            responses = info.get("responses") or info.get("items")
                            runner.transition_product_session(
                                "resolve_approval",
                                request_id,
                                runner.permission_authority.resolve_responses(
                                    info.get("response") or info.get("decision"),
                                    responses,
                                    runner.permission_authority.request_item_ids(
                                        request
                                    ),
                                    runner.permission_authority.default_response(
                                        runner.current_runtime_config()
                                    ),
                                ),
                            )
                        ready = [dict(info)]
                    if consume_fifo:
                        consumed_key = self.pending_permission_key(normalized[match])
                        runner._consumed_permission_responses[consumed_key] = (
                            runner._consumed_permission_responses.get(consumed_key, 0)
                            + 1
                        )
                    normalized.pop(match)
                    while (
                        ready is not None
                        and normalized
                        and isinstance(normalized[0].get("deferred_response"), dict)
                    ):
                        runner.session.metadata["pending_permissions"] = normalized
                        deferred = dict(normalized[0]["deferred_response"])
                        deferred_id = str(normalized[0].get("request_id") or "")
                        if product_session:
                            request = (
                                normalized[0].get("request")
                                if isinstance(normalized[0].get("request"), dict)
                                else {}
                            )
                            operation = str(
                                request.get("operation")
                                or request.get("tool")
                                or request.get("category")
                                or "runtime permission"
                            )
                            runner.transition_product_session(
                                "request_approval", deferred_id, operation
                            )
                            runner.transition_product_session(
                                "resolve_approval",
                                deferred_id,
                                runner.permission_authority.resolve_responses(
                                    deferred.get("response")
                                    or deferred.get("decision"),
                                    deferred.get("responses") or deferred.get("items"),
                                    runner.permission_authority.request_item_ids(
                                        request
                                    ),
                                    runner.permission_authority.default_response(
                                        runner.current_runtime_config()
                                    ),
                                ),
                            )
                        normalized.pop(0)
                        ready.append(deferred)
                    activate = normalized[0] if match == 0 and normalized else None
            else:
                return None
            product_session = getattr(runner.session, "product_session", None)
            if project_before_activation:
                if normalized:
                    runner.session.metadata["pending_permissions"] = normalized
                else:
                    runner.session.metadata.pop("pending_permissions", None)
            if (
                product_session
                and activate
                and product_session.read_model.status == "running"
            ):
                request = (
                    activate.get("request")
                    if isinstance(activate.get("request"), dict)
                    else {}
                )
                operation = str(
                    request.get("operation")
                    or request.get("tool")
                    or request.get("category")
                    or "runtime permission"
                )
                product_session.request_approval(
                    str(activate.get("request_id") or activate.get("id") or ""),
                    operation,
                )
                runner.session.metadata["session_contract"] = (
                    product_session.read_model.as_dict()
                )
            if not project_before_activation:
                if normalized:
                    runner.session.metadata["pending_permissions"] = normalized
                else:
                    runner.session.metadata.pop("pending_permissions", None)
            runner._persist_metadata_snapshot_threadsafe()
            return ready

    def discard_undeliverable_permission(self, request_id: str) -> None:
        runner = self._runner
        with runner._product_session_lock:
            pending = runner.session.metadata.get("pending_permissions")
            if not isinstance(pending, list):
                return

            def _usable(entry: Any) -> bool:
                return isinstance(entry, dict) and bool(
                    str(entry.get("request_id") or "")
                )

            match = next(
                (
                    index
                    for index, entry in enumerate(pending)
                    if _usable(entry) and str(entry.get("request_id")) == request_id
                ),
                None,
            )
            if match is None:
                return
            remaining = [
                entry
                for index, entry in enumerate(pending)
                if index != match and _usable(entry)
            ]
            first_valid = next(
                (index for index, entry in enumerate(pending) if _usable(entry)), None
            )
            is_head = first_valid is not None and match == first_valid
            product_session = getattr(runner.session, "product_session", None)
            if (
                is_head
                and product_session is not None
                and product_session.read_model.status == "awaiting_approval"
            ):
                runner.transition_product_session(
                    "resolve_approval", request_id, "reject"
                )
                head = remaining[0] if remaining else None
                if head is not None:
                    request = (
                        head.get("request")
                        if isinstance(head.get("request"), dict)
                        else {}
                    )
                    operation = str(
                        request.get("operation")
                        or request.get("tool")
                        or request.get("category")
                        or "runtime permission"
                    )
                    try:
                        runner.transition_product_session(
                            "request_approval",
                            str(head.get("request_id") or head.get("id") or ""),
                            operation,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to re-expose pending approval after discarding undeliverable request",
                            exc_info=True,
                        )
                runner.session.metadata["session_contract"] = (
                    product_session.read_model.as_dict()
                )
            if remaining:
                runner.session.metadata["pending_permissions"] = remaining
            else:
                runner.session.metadata.pop("pending_permissions", None)
        runner._persist_metadata_snapshot_threadsafe()

    def rehydrate_pending_permissions(
        self,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        if event_type in {"permission_request", "permission_response"}:
            info = {
                **dict(payload or {}),
                **(
                    {"_runtime_event": (event_type, dict(payload or {}))}
                    if event_type == "permission_response"
                    else {}
                ),
            }
            return self.update_pending_permissions(event_type, info, source="session")
        if event_type != "task_event":
            return None
        kind = str((payload or {}).get("kind") or "")
        if kind not in {"permission_request", "permission_response"}:
            return None
        child_payload = (payload or {}).get("payload") or {}
        child = (
            dict(child_payload)
            if isinstance(child_payload, dict)
            else {"payload": child_payload}
        )
        if kind == "permission_response":
            child["_runtime_event"] = (event_type, dict(payload or {}))
        return self.update_pending_permissions(
            kind,
            child,
            source="task",
            task_session_id=str((payload or {}).get("sessionId") or ""),
            subagent_type=str((payload or {}).get("subagent_type") or ""),
        )
