"""Session execution helpers for the CLI bridge."""

from __future__ import annotations


import asyncio
import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, List

from breadboard_engine.compilation.v2_loader import load_agent_config
from breadboard.product.runtime.artifacts import _validate_artifact_name
from breadboard.product.runtime import session_store
from breadboard_engine.model_roles import (
    ModelRoleProblem,
    ModelRoleResolutionError,
    select_role_target,
)
from breadboard_engine.auth.enforcer import apply_dotted_overrides
from breadboard_engine.checkpointing.checkpoint_manager import CheckpointManager
from breadboard_engine.security import redaction
from breadboard_engine.skills.registry import (
    load_skills,
    build_skill_catalog,
    normalize_skill_selection,
    apply_skill_selection,
)
from breadboard_engine.plugins.loader import discover_plugin_manifests, plugin_snapshot
from breadboard_engine.permissions import (
    build_permission_overrides,
    load_permission_rules,
    upsert_permission_rule,
)

from .events import EventType, SessionEvent
from .models import SessionCreateRequest, SessionStatus
from .registry import (
    SessionRecord,
    SessionRegistry,
    TurnRecord,
    submission_body_digest,
    identity_digest,
)
from .runtime_event_projector import (
    RuntimeEventProjector,
    TranslatedRuntimeEvent,
    _safe_runtime_error_code,
)
from .session_control import SessionControlController
from .task_execution import TaskExecutionOwner
from .session_lifecycle import SessionLifecycleOwner


logger = logging.getLogger(__name__)
AgentFactory = Callable[[str, Optional[str], Optional[Dict[str, Any]]], Any]
MAX_ATTACHMENT_BYTES = 16 * 1024


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
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._permission_decision_lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._start_authority = asyncio.Event()
        self._input_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._product_session_lock = threading.RLock()
        self._product_tool_completions: Dict[str, int] = {}
        self._published_events = 0
        self._session_failure_published = False
        self._workspace_path: Optional[Path] = None
        self._durable_product_workspace: Path | None = None
        self._checkpoint_manager: Optional[CheckpointManager] = None
        self._closed = False
        self._attachment_store: Dict[str, Dict[str, Any]] = {}
        self._active_attachment_capabilities: Dict[str, Dict[str, Any]] = {}
        self._active_input_media: List[Dict[str, str]] = []
        self._permission_queue: Any = None
        self._consumed_permission_responses: Dict[tuple[str, str, str], int] = {}
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
        task_context = dict(initial_metadata.get("task_context") or {})
        task_context.setdefault("session_id", self.session.session_id)
        if (
            not isinstance(task_context.get("input_id"), str)
            or not task_context["input_id"]
        ):
            task_context["input_id"] = f"input-{uuid.uuid4()}"
        if (
            not isinstance(task_context.get("turn_id"), str)
            or not task_context["turn_id"]
        ):
            task_context["turn_id"] = f"turn-{uuid.uuid4()}"
        initial_metadata["task_context"] = task_context
        self._model_role_lock: Any = initial_metadata.get("model_role_lock")
        self._active_model_role: str | None = (
            str(initial_metadata.get("active_model_role") or "").strip() or None
        )
        self._model_override: Optional[str] = initial_metadata.get("model")
        self._mode: Optional[str] = initial_metadata.get("mode")
        self._profile_timing_enabled: bool = bool(
            os.environ.get("BREADBOARD_PROFILE_TIMING", "").strip().lower()
            in {"1", "true", "yes", "on"}
            or initial_metadata.get("profile_timing")
        )
        self._runtime_event_projector = RuntimeEventProjector(
            self.session,
            self._persist_metadata_snapshot_threadsafe,
            observation_tool_name=self._observation_tool_name,
            product_session_lock=self._product_session_lock,
            product_tool_completions=self._product_tool_completions,
        )
        self._control_controller = SessionControlController(self)
        self._task_execution = TaskExecutionOwner(
            self,
            permission_projection=self._control_controller.rehydrate_pending_permissions,
        )
        self._lifecycle_owner = SessionLifecycleOwner(self, self._task_execution)

    def bind_durable_product_session(self, workspace: Path) -> None:
        with self._product_session_lock:
            self._durable_product_workspace = workspace.resolve()

    def _commit_terminal_product_session_locked(self) -> None:
        workspace = self._durable_product_workspace
        product_session = getattr(self.session, "product_session", None)
        if workspace is None or product_session is None:
            return
        session_store.create_session(workspace, product_session)
        manifest_ref = self.session.metadata.get("artifact_manifest_ref")
        if not isinstance(manifest_ref, Mapping):
            return
        digest = manifest_ref.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError("invalid attachment manifest reference")
        session_store.authorize_session_artifact_manifest(
            workspace,
            product_session.read_model.session_id,
            (
                f"{product_session.read_model.session_id}."
                f"{digest.removeprefix('sha256:')}.json"
            ),
        )

    def _default_factory(
        self,
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> Any:
        from breadboard_engine.agent import create_agent

        metadata = (
            self.session.metadata if isinstance(self.session.metadata, dict) else {}
        )
        force_local_mode = bool(metadata.get("cli_force_local_mode", True))
        return create_agent(
            config_path,
            workspace_dir=workspace_dir,
            overrides=overrides,
            force_local_mode=force_local_mode,
        )

    def schedule_start(self) -> None:
        if self._task:
            raise RuntimeError("runner already started")
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(
            self._run_after_start_authority(),
            name=f"kyle-session-{self.session.session_id}",
        )

    async def prepare_start(self, *, admission_serialized: bool = False) -> None:
        """Retain an initial turn before execution becomes runnable."""
        if self._task:
            raise RuntimeError("runner already started")
        self._loop = asyncio.get_running_loop()
        initial_task = (self.request.task or "").strip()
        if not initial_task:
            return
        client_message_id = f"session-create:{self.session.session_id}"
        input_id = f"input-{uuid.uuid4()}"
        turn_id = f"turn-{uuid.uuid4()}"
        attachments: tuple[str, ...] = ()
        turn = TurnRecord(
            input_id=input_id,
            turn_id=turn_id,
            client_message_id=client_message_id,
            content=initial_task,
            attachments=attachments,
            original_disposition="started",
            state="active",
            body_digest=submission_body_digest(initial_task, attachments),
        )
        self.session.turns_by_id[turn_id] = turn
        self.session.submissions_by_key[client_message_id] = turn
        self.session.submissions_by_key_digest[identity_digest(client_message_id)] = (
            turn
        )
        self.session.active_turn_id = turn_id

    def authorize_start(self) -> None:
        if not self._task:
            raise RuntimeError("runner is not scheduled")
        self._start_authority.set()

    async def _run_after_start_authority(self) -> None:
        await self._start_authority.wait()
        await self._run()

    async def start(self) -> None:
        if (self.request.task or "").strip() and self.session.active_turn_id is None:
            await self.prepare_start()
        self.schedule_start()
        self.authorize_start()

    def _signal_control(self, kind: str) -> bool:
        queue = getattr(self, "_control_queue", None)
        put = (
            getattr(queue, "put_nowait", None) or getattr(queue, "put", None)
            if queue is not None
            else None
        )
        if not callable(put):
            return False
        put({"kind": kind})
        return True

    def _install_control_queue(self, queue: Any) -> None:
        with self._product_session_lock:
            self._control_queue = queue
            if queue is None:
                return
            if self._stop_event.is_set():
                queue.put({"kind": "stop"})
            elif not self._resume_event.is_set():
                queue.put({"kind": "pause"})

    def _request_stop(self, reason: str) -> bool:
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            stopping = not product_session or product_session.read_model.status not in {
                "completed",
                "failed",
                "canceled",
            }
            try:
                if stopping:
                    self.transition_product_session("cancel", reason)
            finally:
                self._stop_event.set()
                self._resume_event.set()
                self._input_queue.put_nowait(None)
                try:
                    delivered = self._signal_control("stop")
                except Exception:
                    delivered = False
                request_stop = (
                    getattr(
                        getattr(self._agent, "agent", None),
                        "request_stop",
                        None,
                    )
                    if not delivered
                    else None
                )
                remote = getattr(request_stop, "remote", None)
                try:
                    if callable(remote):
                        remote()
                    elif callable(request_stop):
                        request_stop()
                except Exception:
                    pass
            return stopping

    async def stop(self, reason: str = "operator request") -> None:
        if self._closed:
            return
        cancelled_before_start = (
            self._task is None or not self._start_authority.is_set()
        )
        self._request_stop(reason)
        if self._task and not self._start_authority.is_set():
            self._task.cancel()
        if self._task and not self._task.done():
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if cancelled_before_start:
            product_state = getattr(
                getattr(
                    getattr(self.session, "product_session", None),
                    "read_model",
                    None,
                ),
                "status",
                None,
            )
            outcome = {
                "completed": "completed",
                "failed": "failed",
                "canceled": "cancelled",
            }.get(product_state, "cancelled")
            await self._terminalize_admitted_turns(
                outcome=outcome,
                reason=(
                    "stop_requested"
                    if outcome == "cancelled"
                    else "runtime_failure"
                    if outcome == "failed"
                    else "completed"
                ),
                error_code=("runtime_failure" if outcome == "failed" else None),
            )
            final_status = {
                "completed": SessionStatus.COMPLETED,
                "failed": SessionStatus.FAILED,
                "cancelled": SessionStatus.STOPPED,
            }[outcome]
            await self.registry.update_status(self.session.session_id, final_status)
            self._closed = True
            await self._enqueue_termination()

    async def enqueue_input(
        self,
        content: str,
        attachments: Optional[list[str]] = None,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> str:
        if self._closed:
            raise RuntimeError("session is closed")
        if not content or not content.strip():
            raise ValueError("input content must not be empty")
        if not isinstance(input_id, str) or not input_id.strip():
            raise ValueError("input_id is required at runner admission")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("turn_id is required at runner admission")
        admitted_turn = self.session.turns_by_id.get(turn_id)
        if admitted_turn is None:
            raise RuntimeError("turn_id was not admitted by the session registry")
        if admitted_turn.input_id != input_id:
            raise RuntimeError("input_id does not match the admitted turn")
        if admitted_turn.terminal_outcome is not None:
            raise RuntimeError("turn is already terminal")
        admitted_active = (
            admitted_turn.state == "active"
            and self.session.active_turn_id == admitted_turn.turn_id
        )
        admitted_queued = (
            admitted_turn.state == "queued"
            and admitted_turn.turn_id in self.session.queued_turn_ids
        )
        if not (admitted_active or admitted_queued):
            raise RuntimeError("turn is not active or queued for execution")

        attachment_ids = list(
            dict.fromkeys(
                item.strip()
                for item in (attachments or [])
                if isinstance(item, str) and item.strip()
            )
        )
        if admitted_turn.content != content:
            raise RuntimeError("input content does not match the admitted turn")
        if admitted_turn.attachments != tuple(attachment_ids):
            raise RuntimeError("attachments do not match the admitted turn")
        with self._product_session_lock:
            artifacts = getattr(self.session, "product_artifacts", {})
            unknown = [
                item
                for item in attachment_ids
                if not isinstance(artifacts, dict) or item not in artifacts
            ]
            if unknown:
                raise ValueError(f"unknown attachment IDs: {', '.join(unknown)}")
            total_bytes = sum(
                int(getattr(artifacts[item], "size_bytes", MAX_ATTACHMENT_BYTES + 1))
                for item in attachment_ids
            )
            if total_bytes > MAX_ATTACHMENT_BYTES:
                raise ValueError(
                    f"selected attachments exceed {MAX_ATTACHMENT_BYTES}-byte handoff limit"
                )
            content = self._sanitize_interactive_input_content(content)
            payload = {
                "content": content,
                "attachments": attachment_ids,
                "input_id": input_id,
                "turn_id": turn_id,
            }
            product_session = getattr(self.session, "product_session", None)
            if product_session is not None:
                product_session.input(
                    content, [artifacts[item] for item in attachment_ids]
                )
                self.session.metadata["session_contract"] = (
                    product_session.read_model.as_dict()
                )
            self._input_queue.put_nowait(payload)
        return content

    def transition_product_session(self, transition: str, *args: Any) -> None:
        with self._product_session_lock:
            product_session = getattr(self.session, "product_session", None)
            if product_session is None:
                return
            if transition in {
                "complete",
                "fail",
                "cancel",
            } and product_session.read_model.status in {
                "completed",
                "failed",
                "canceled",
            }:
                return
            getattr(product_session, transition)(*args)
            if transition in {"complete", "fail", "cancel"}:
                self._commit_terminal_product_session_locked()
            self.session.metadata["session_contract"] = (
                product_session.read_model.as_dict()
            )

    # Provider-supplied names are not public identities until they resolve into
    # the active, configured tool surface.
    def _observation_tool_name(self, payload: Dict[str, Any]) -> Optional[str]:
        raw = payload.get("tool")
        if not isinstance(raw, str) or not raw or len(raw) > 128 or not raw.isascii():
            return None
        if not raw[0].isalnum() or any(
            not (character.isalnum() or character in "_.-") for character in raw
        ):
            return None
        canonical = raw
        executor = getattr(self._agent, "agent_executor", None)
        canonicalize = getattr(executor, "canonical_tool_name", None)
        if callable(canonicalize):
            try:
                candidate = canonicalize(raw)
                if isinstance(candidate, str) and candidate:
                    canonical = candidate
            except Exception:
                return None
        if (
            len(canonical) > 128
            or not canonical.isascii()
            or not canonical[0].isalnum()
            or any(
                not (character.isalnum() or character in "_.-")
                for character in canonical
            )
        ):
            return None
        active_names = getattr(self._agent, "_active_tool_names", ())
        allowed = (
            {name for name in active_names if isinstance(name, str)}
            if isinstance(active_names, Sequence)
            and not isinstance(active_names, (str, bytes))
            else set()
        )
        config = self.current_runtime_config()
        modes = config.get("modes")
        for mode in modes if isinstance(modes, list) else ():
            if not isinstance(mode, dict):
                continue
            enabled_names = mode.get("tools_enabled")
            if isinstance(enabled_names, Sequence) and not isinstance(
                enabled_names, (str, bytes)
            ):
                allowed.update(name for name in enabled_names if isinstance(name, str))
        configured_tools = config.get("tools")
        if isinstance(configured_tools, dict):
            allowed.update(
                name
                for name, enabled in configured_tools.items()
                if isinstance(name, str) and bool(enabled)
            )
        return canonical if canonical in allowed else None

    def _tool_completion_fingerprint(
        self,
        tool: str,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        return self._runtime_event_projector._tool_completion_fingerprint(tool, payload)

    def _record_product_observation(
        self,
        family: Optional[str],
        payload: Dict[str, Any],
        *,
        message_projection: bool = False,
    ) -> None:
        return self._runtime_event_projector._record_product_observation(
            family, payload, message_projection=message_projection
        )

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
            meta = (
                self.session.metadata if isinstance(self.session.metadata, dict) else {}
            )
            repairs = (
                list(meta.get("input_boundary_repairs") or [])
                if isinstance(meta.get("input_boundary_repairs"), list)
                else []
            )
            repairs.append(
                {
                    "prior_len": len(prior),
                    "raw_len": len(raw),
                    "suffix_len": len(suffix),
                }
            )
            meta["input_boundary_repairs"] = repairs[-10:]
            self.session.metadata = meta
            self._persist_metadata_snapshot_threadsafe()
            return suffix.lstrip()
        return raw

    def _upsert_permission_rule(
        self,
        workspace_dir: Path | str,
        *,
        category: str,
        pattern: str,
        decision: str,
        scope: str,
    ) -> bool:
        return upsert_permission_rule(
            workspace_dir,
            category=category,
            pattern=pattern,
            decision=decision,
            scope=scope,
        )

    def _fail_control_transition(self, code: str, detail: str) -> None:
        try:
            self.transition_product_session("fail", code, detail)
        finally:
            self._request_stop(detail)

    async def handle_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        durable_reconfigure: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        return await self._control_controller.handle_command(
            command, payload, durable_reconfigure=durable_reconfigure
        )

    @staticmethod
    def _target_route(target: Mapping[str, Any]) -> str:
        route = str(target.get("route_id") or "").strip()
        if route:
            return route
        provider = str(target.get("provider_id") or "").strip()
        model = str(target.get("model_id") or "").strip()
        if not provider or not model:
            raise ValueError("locked model target is missing provider/model identity")
        return f"{provider}/{model}"

    def _locked_target(self, role: str | None = None) -> dict[str, Any]:
        lock = self._model_role_lock
        if not isinstance(lock, Mapping):
            raise ModelRoleResolutionError(
                ModelRoleProblem(
                    "known_role_unbound", "no model-role lock is active", "$.roles"
                )
            )
        chosen = str(
            role
            or self._active_model_role
            or (lock.get("defaults") or {}).get("role")
            or ""
        ).strip()
        if not chosen:
            raise ModelRoleResolutionError(
                ModelRoleProblem(
                    "known_role_unbound",
                    "no active model role is bound",
                    "$.defaults.role",
                )
            )
        return select_role_target(lock, chosen)

    def install_model_role_lock(self, lock: Mapping[str, Any]) -> Dict[str, Any]:
        self._model_role_lock = (
            lock.as_dict() if hasattr(lock, "as_dict") else dict(lock)
        )
        role = str(
            (self._model_role_lock.get("defaults") or {}).get("role") or ""
        ).strip()
        route = self._target_route(self._locked_target(role))
        self._active_model_role, self._model_override = role, route
        self.session.metadata.update(
            {
                "model_role_lock": dict(self._model_role_lock),
                "model_role_lock_hash": str(
                    self._model_role_lock.get("lock_hash") or ""
                ),
                "active_model_role": role,
                "model": route,
            }
        )
        prepared = self.prepare_runtime_config()
        prepared["model_role_lock"] = dict(self._model_role_lock)
        prepared["active_model_role"] = role
        prepared["providers"] = dict(prepared.get("providers") or {})
        prepared["providers"]["default_model"] = route
        self._prepared_runtime_config = prepared
        return dict(prepared)

    def _load_base_config(self) -> Dict[str, Any]:
        if isinstance(self._base_config_cache, dict):
            return dict(self._base_config_cache)
        cfg = load_agent_config(self.request.config_path)
        if not isinstance(cfg, dict):
            raise TypeError("agent config loader must return a mapping")
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
        requested_permission_mode = (
            (
                self.request.permission_mode
                or self.session.metadata.get("permission_mode")
                or ""
            )
            .strip()
            .lower()
        )
        base_cfg = self._load_base_config()
        if requested_permission_mode in {"prompt", "ask", "interactive"}:
            overrides.setdefault("permissions.options.mode", "prompt")
            overrides.setdefault("permissions.options.default_response", "reject")
            overrides.setdefault("permissions.edit.default", "ask")
            overrides.setdefault("permissions.shell.default", "ask")
            overrides.setdefault("permissions.webfetch.default", "ask")
            overrides.setdefault("permissions.read.default", "ask")
        if requested_permission_mode in {"prompt", "ask", "interactive", "configured"}:
            self.request.permission_mode = requested_permission_mode
            self.session.metadata["permission_mode"] = requested_permission_mode
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
            for key, value in (
                build_permission_overrides(base_cfg, rules).items() if rules else ()
            ):
                existing = overrides.get(key)
                if (
                    key in overrides
                    and isinstance(existing, list)
                    and isinstance(value, list)
                ):
                    value = existing + [item for item in value if item not in existing]
                overrides[key] = value
        self.request.overrides = overrides
        self._prepared_runtime_config = apply_dotted_overrides(base_cfg, overrides)
        permissions = self._prepared_runtime_config.get("permissions")
        options = permissions.get("options") if isinstance(permissions, dict) else None
        effective_permission_mode = (
            str(options.get("mode") or "").strip().lower()
            if isinstance(options, dict)
            else ""
        )
        resolved_permission_mode = (
            requested_permission_mode
            if requested_permission_mode
            and requested_permission_mode not in {"prompt", "ask", "interactive"}
            else effective_permission_mode
        )
        self.request.permission_mode = resolved_permission_mode or None
        if resolved_permission_mode:
            self.session.metadata["permission_mode"] = resolved_permission_mode
        else:
            self.session.metadata.pop("permission_mode", None)
        return dict(self._prepared_runtime_config)

    def current_runtime_config(self) -> Dict[str, Any]:
        return (
            dict(config)
            if isinstance((config := getattr(self._agent, "config", None)), dict)
            else self.prepare_runtime_config()
        )

    def _resolve_workspace_guess(self, base_cfg: Dict[str, Any]) -> Optional[Path]:
        candidate: Any = self.request.workspace
        if not candidate and isinstance(base_cfg, dict):
            workspace = base_cfg.get("workspace")
            candidate = (
                (workspace.get("root") or workspace.get("path"))
                if isinstance(workspace, dict)
                else None
            )
        candidate = (
            candidate
            or f"tmp/agent_ws_{os.path.basename(self.request.config_path).split('.')[0]}"
        )
        try:
            path = Path(str(candidate)).expanduser()
            if not path.is_absolute():
                root = Path(__file__).resolve().parents[3]
                path = (
                    root / path if path.parts[:1] == ("tmp",) else root / "tmp" / path
                )
            return path.resolve()
        except Exception:
            return None

    def _parse_replay_path(self, task_text: str) -> Optional[Path]:
        return self._task_execution.parse_replay_path(task_text)

    async def _maybe_publish_todo_snapshot(
        self, workspace_dir: Optional[Path], *, call_id: str
    ) -> None:
        return await self._task_execution.maybe_publish_todo_snapshot(
            workspace_dir, call_id=call_id
        )

    async def _ensure_agent_initialized(self) -> None:
        if self._agent is not None:
            return
        overrides = dict(self.request.overrides or {})
        frozen = self.current_runtime_config()
        if redaction.contains_provider_auth_runtime(
            frozen
        ) or redaction.contains_provider_auth_runtime(overrides):
            logger.warning(
                "Ignoring inline provider credentials; attach credentials through the provider broker."
            )
        frozen = redaction.strip_provider_auth_runtime(frozen)
        overrides = redaction.strip_provider_auth_runtime(overrides)
        descriptor, snapshot = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(frozen, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            self._agent = self.agent_factory(
                snapshot, self.request.workspace, overrides or None
            )
            if hasattr(self._agent, "config_path"):
                self._agent.config_path = self.request.config_path
            await asyncio.to_thread(self._agent.initialize)
        finally:
            Path(snapshot).unlink(missing_ok=True)
        workspace_dir = Path(self._agent.workspace_dir).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_path = workspace_dir
        if self._model_override:
            self._apply_model_override()
        if self._todo_enabled:
            meta = (
                self.session.metadata if isinstance(self.session.metadata, dict) else {}
            )
            if not isinstance(meta.get("todo_last_update"), dict):
                await self._maybe_publish_todo_snapshot(
                    workspace_dir, call_id="todo:snapshot:init"
                )
        try:
            if self._checkpoint_manager is None:
                self._checkpoint_manager = CheckpointManager(workspace_dir)
                self._checkpoint_manager.create_checkpoint("Session start")
        except Exception:
            self._checkpoint_manager = None

    def _require_execution_correlation(
        self, input_id: Optional[str], turn_id: Optional[str]
    ) -> Dict[str, str]:
        return self._task_execution.require_execution_correlation(input_id, turn_id)

    async def _execute_replay_task(
        self,
        task_text: str,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._task_execution.execute_replay_task(
            task_text, input_id=input_id, turn_id=turn_id
        )

    async def _run(self) -> None:
        await self._lifecycle_owner.run()

    def _load_todo_envelope_from_disk(
        self, workspace_dir: Path
    ) -> Optional[Dict[str, Any]]:
        return self._task_execution.load_todo_envelope_from_disk(workspace_dir)

    async def _terminalize_admitted_turns(
        self,
        *,
        outcome: str,
        reason: str,
        error_code: Optional[str] = None,
    ) -> None:
        await self._lifecycle_owner.terminalize_admitted_turns(
            outcome=outcome,
            reason=reason,
            error_code=error_code,
        )

    async def _publish_session_failure(self, error_code: str) -> None:
        if self._session_failure_published:
            return
        self._session_failure_published = True
        await self.publish_event_async(
            EventType.ERROR,
            {"code": _safe_runtime_error_code(error_code)},
            classification="bridge_host",
            family="session.failure",
            actor="service",
            visibility="host",
        )

    async def _enqueue_termination(self) -> None:
        queue = self.session.event_queue
        try:
            await queue.put(None)
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning(
                "Event queue full while terminating session %s", self.session.session_id
            )

    def _rollback_runtime_overrides(
        self, overrides: Dict[str, Any], restore: Optional[tuple[str, bool, Any]] = None
    ) -> bool:
        try:
            rolled_back = (
                not self._agent
                or self._agent.apply_runtime_overrides(overrides) is not False
            )
        except Exception:
            logger.exception("Failed to roll back runtime configuration")
            return False
        if self._agent and restore:
            if restore[1]:
                self._agent.config[restore[0]] = restore[2]
            else:
                self._agent.config.pop(restore[0], None)
        return rolled_back

    def _apply_model_override(self) -> bool:
        if not self._agent or not self._model_override:
            return True
        try:
            overrides: Dict[str, Any] = {
                "providers.default_model": self._model_override,
                "active_model_role": self._active_model_role,
            }
            if self._model_role_lock is not None:
                overrides["model_role_lock"] = dict(self._model_role_lock)
            return self._agent.apply_runtime_overrides(overrides) is not False
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to apply model override: %s", exc)
            return False

    def _persist_metadata_snapshot_threadsafe(self) -> None:
        loop = self._loop
        if not loop or not loop.is_running():
            return

        async def persist_latest_metadata() -> None:
            await self.registry.update_metadata(
                self.session.session_id,
                metadata=dict(self.session.metadata or {}),
            )

        try:
            asyncio.run_coroutine_threadsafe(
                persist_latest_metadata(),
                loop,
            )
        except Exception:
            pass

    def _debug_permissions_enabled(self) -> bool:
        return self._control_controller.debug_permissions_enabled()

    async def _emit_debug_permission_request(
        self, payload: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return await self._control_controller.emit_debug_permission_request(payload)

    def _pending_permission_key(self, entry: Dict[str, Any]) -> tuple[str, str, str]:
        return self._control_controller.pending_permission_key(entry)

    def _infer_permission_category(self, request_id: str) -> Optional[str]:
        return self._control_controller.infer_permission_category(request_id)

    def _update_pending_permissions(
        self,
        kind: str,
        info: Dict[str, Any],
        *,
        source: str = "session",
        task_session_id: Optional[str] = None,
        subagent_type: Optional[str] = None,
        consume_fifo: bool = False,
    ) -> Optional[List[Dict[str, Any]]]:
        return self._control_controller.update_pending_permissions(
            kind,
            info,
            source=source,
            task_session_id=task_session_id,
            subagent_type=subagent_type,
            consume_fifo=consume_fifo,
        )

    def _discard_undeliverable_permission(self, request_id: str) -> None:
        return self._control_controller.discard_undeliverable_permission(request_id)

    def _rehydrate_pending_permissions(
        self, event_type: str, payload: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        return self._control_controller.rehydrate_pending_permissions(
            event_type, payload
        )

    def _execute_task(
        self,
        task_text: str,
        *,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._task_execution.execute_task(
            task_text, input_id=input_id, turn_id=turn_id
        )


    async def _finish_turn(
        self,
        turn: TurnRecord,
        outcome: str,
        *,
        reason: Optional[str] = None,
        error_code: Optional[str] = None,
        completed_payload: Optional[Dict[str, Any]] = None,
        advance_queue: bool = True,
    ) -> bool:
        return await self._task_execution.finish_turn(
            turn,
            outcome,
            reason=reason,
            error_code=error_code,
            completed_payload=completed_payload,
            advance_queue=advance_queue,
        )

    async def publish_event_async(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
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
            input_id=input_id,
            turn_id=turn_id,
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
        input_id: Optional[str] = None,
        turn_id: Optional[str] = None,
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
            input_id=input_id,
            turn_id=turn_id,
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
            future = asyncio.run_coroutine_threadsafe(
                self._enqueue_event_async(event), loop
            )
            future.result()
            return
        try:
            self.session.event_queue.put_nowait(event)
            self._published_events += 1
        except asyncio.QueueFull:  # pragma: no cover - defensive
            logger.warning(
                "Event queue full for session %s, dropping event",
                self.session.session_id,
            )

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
        *,
        errors: Optional[List[BaseException]] = None,
    ) -> tuple[Any, Any]:
        return self._task_execution.start_queue_pump(
            event_queue, handle_event, errors=errors
        )

    def _drain_event_queue(
        self,
        event_queue: Any,
        handle_event: Callable[[str, Dict[str, Any], Optional[int]], None],
    ) -> None:
        return self._task_execution.drain_event_queue(event_queue, handle_event)

    def get_workspace_dir(self) -> Optional[Path]:
        if self._workspace_path:
            self._workspace_path.mkdir(parents=True, exist_ok=True)
            return self._workspace_path
        candidate = (
            getattr(self._agent, "workspace_dir", None) or self.request.workspace
        )
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
        helper_lines: list[str] = []
        self._active_attachment_capabilities = {}
        self._active_input_media = []
        for index, key in enumerate(
            dict.fromkeys(str(value) for value in attachment_ids), start=1
        ):
            info = self._attachment_store.get(key)
            if not info:
                continue
            artifact_ref = getattr(self.session, "product_artifacts", {}).get(key)
            if artifact_ref is None:
                raise RuntimeError(f"attachment artifact missing: {key}")
            filename = str(info.get("filename") or key)
            _validate_artifact_name(key)
            uri = f"attachment://{artifact_ref.digest}"
            self._active_attachment_capabilities[uri] = artifact_ref.as_dict()
            if str(artifact_ref.media_type).startswith("image/"):
                self._active_input_media.append(
                    {
                        "type": "media",
                        "kind": "image",
                        "uri": uri,
                        "mime": str(artifact_ref.media_type),
                    }
                )
            helper_lines.append(
                "[Attachment "
                f"{index}: name={json.dumps(filename, ensure_ascii=True)}; "
                f"uri={uri}; size_bytes={artifact_ref.size_bytes}; "
                "read with read_file after normal authorization]"
            )
        return "\n".join(helper_lines)

    def _load_run_summary(self, logging_dir: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._task_execution.load_run_summary(logging_dir)

    def _normalize_usage_payload(
        self, usage: Dict[str, Any], *, latency_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        return self._task_execution.normalize_usage_payload(
            usage, latency_ms=latency_ms
        )

    def _usage_from_run_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        return self._task_execution.usage_from_run_summary(summary)

    def _extract_usage_metrics(
        self,
        result: Dict[str, Any],
        logging_dir: Optional[str],
        *,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._task_execution.extract_usage_metrics(
            result, logging_dir, elapsed_ms=elapsed_ms
        )

    def _normalize_tool_call_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._runtime_event_projector._normalize_tool_call_payload(payload)

    def _normalize_tool_result_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._runtime_event_projector._normalize_tool_result_payload(payload)

    def _extract_artifact_ref(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return self._runtime_event_projector._extract_artifact_ref(payload)

    def _normalize_artifact_ref(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return self._runtime_event_projector._normalize_artifact_ref(payload)

    def _normalize_permission_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._runtime_event_projector._normalize_permission_request(payload)

    def _normalize_permission_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._runtime_event_projector._normalize_permission_response(payload)

    def _normalize_task_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._runtime_event_projector._normalize_task_event(payload)

    def _translate_runtime_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        turn: Optional[int],
    ) -> Optional[TranslatedRuntimeEvent]:
        return self._runtime_event_projector.translate(event_type, payload, turn)

    def _resolve_skill_catalog(self) -> Dict[str, Any]:
        config = self.current_runtime_config()
        workspace = self.get_workspace_dir()
        if not workspace:
            ws_cfg = (config.get("workspace") or {}) if isinstance(config, dict) else {}
            workspace = Path(
                str(ws_cfg.get("root") or self.request.workspace or ".")
            ).resolve()
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
        selection = normalize_skill_selection(
            config, self.session.metadata.get("skills_selection")
        )
        _, _, enabled_map = apply_skill_selection(
            prompt_skills, graph_skills, selection
        )
        catalog = build_skill_catalog(
            prompt_skills, graph_skills, selection=selection, enabled_map=enabled_map
        )
        snapshot = None
        try:
            snapshot = plugin_snapshot(plugin_manifests)
        except Exception:
            snapshot = None
        return {
            "catalog": catalog,
            "selection": selection,
            "sources": {
                "config_path": self.session.metadata.get(
                    "config_path", self.request.config_path
                ),
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
                self._skills_catalog_cache = {
                    "catalog": {"skills": []},
                    "selection": {},
                    "sources": {},
                }
        return dict(self._skills_catalog_cache or {})

    def get_ctree_snapshot(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(self._ctree_snapshot_cache, dict):
            payload.update(self._ctree_snapshot_cache)
        if self._ctree_last_node:
            payload.setdefault("last_node", dict(self._ctree_last_node))
        return payload
