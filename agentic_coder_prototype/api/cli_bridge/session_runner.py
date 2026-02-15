"""Session execution helpers for the CLI bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, List

from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.checkpointing.checkpoint_manager import CheckpointManager
from agentic_coder_prototype.skills.registry import (
    load_skills,
    build_skill_catalog,
    normalize_skill_selection,
    apply_skill_selection,
)
from agentic_coder_prototype.plugins.loader import discover_plugin_manifests, plugin_snapshot
from agentic_coder_prototype.policy_pack import PolicyPack
from agentic_coder_prototype.guardrail_coordinator import GuardrailCoordinator
from agentic_coder_prototype.permission_rules_store import (
    build_permission_overrides,
    load_permission_rules,
    upsert_permission_rule,
)
from agentic_coder_prototype.todo import TodoStore
from agentic_coder_prototype.todo.projection import project_store_snapshot_to_tui_envelope

from .events import EventType, SessionEvent
from .models import SessionCreateRequest, SessionStatus
from .registry import SessionRecord, SessionRegistry

logger = logging.getLogger(__name__)


AgentFactory = Callable[[str, Optional[str], Optional[Dict[str, Any]]], Any]


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
        self._published_events = 0
        self._closed = False
        self._workspace_path: Optional[Path] = None
        self._attachment_store: Dict[str, Dict[str, Any]] = {}
        self._permission_queue: Any = None
        self._control_queue: Any = None
        self._checkpoint_manager: Optional[CheckpointManager] = None
        self._skills_catalog_cache: Optional[Dict[str, Any]] = None
        self._ctree_snapshot_cache: Optional[Dict[str, Any]] = None
        self._ctree_last_node: Optional[Dict[str, Any]] = None
        self._base_config_cache: Optional[Dict[str, Any]] = None
        self._todo_enabled: bool = False

        # Live overrides updated via commands
        initial_metadata = dict(request.metadata or {})
        self.session.metadata = initial_metadata
        self._model_override: Optional[str] = initial_metadata.get("model")
        self._mode: Optional[str] = initial_metadata.get("mode")

    def _default_factory(
        self,
        config_path: str,
        workspace_dir: Optional[str],
        overrides: Optional[Dict[str, Any]],
    ) -> Any:
        from agentic_coder_prototype.agent import create_agent

        return create_agent(config_path, workspace_dir=workspace_dir, overrides=overrides)

    async def start(self) -> None:
        if self._task:
            raise RuntimeError("runner already started")
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._task = loop.create_task(self._run(), name=f"kyle-session-{self.session.session_id}")

    async def stop(self) -> None:
        if self._closed:
            return
        self._stop_event.set()
        await self._input_queue.put(None)
        if self._task and not self._task.done():
            await self._task

    async def enqueue_input(self, content: str, attachments: Optional[list[str]] = None) -> None:
        if self._closed:
            raise RuntimeError("session is closed")
        if not content or not content.strip():
            raise ValueError("input content must not be empty")
        payload = {
            "content": content,
            "attachments": [
                item for item in (attachments or []) if isinstance(item, str) and item.strip()
            ],
        }
        await self._input_queue.put(payload)

    async def handle_command(self, command: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                if normalized in {"allow-once", "allow_once"}:
                    response_value = "once"
                elif normalized in {"allow-always", "allow_always"}:
                    response_value = "always"
                elif normalized in {"deny-once", "deny_once"}:
                    response_value = "reject"
                elif normalized in {"deny-always", "deny_always"}:
                    response_value = "reject"
                elif normalized in {"deny-stop", "deny_stop"}:
                    response_value = "reject"
                else:
                    response_value = normalized
                # Permission broker accepts a dict item with request_id + response/decision.
                # We use the simplest uniform response (applies to all items in a batch).
                permission_payload = {
                    "request_id": request_id.strip(),
                    "response": response_value,
                }
                if rule:
                    try:
                        rules = self.session.metadata.get("permission_rules")
                        if not isinstance(rules, list):
                            rules = []
                        rules.append(
                            {
                                "request_id": request_id.strip(),
                                "decision": normalized,
                                "rule": rule,
                                "scope": scope,
                                "note": note,
                            }
                        )
                        self.session.metadata["permission_rules"] = rules
                        self._persist_metadata_snapshot_threadsafe()
                    except Exception:
                        pass
                if (
                    isinstance(rule, str)
                    and rule.strip()
                    and normalized in {"allow-always", "allow_always", "deny-always", "deny_always"}
                ):
                    category = self._infer_permission_category(request_id.strip())
                    workspace_dir = self.get_workspace_dir()
                    if category and workspace_dir:
                        decision_value = "allow" if normalized.startswith("allow") else "deny"
                        try:
                            upsert_permission_rule(
                                workspace_dir,
                                category=category,
                                pattern=rule.strip(),
                                decision=decision_value,
                                scope=str(scope or "project"),
                            )
                        except Exception:
                            pass
                detail = await self.handle_command("respond_permission", permission_payload)
                if normalized in {"deny-stop", "deny_stop"} or bool(payload.get("stop")):
                    await self.handle_command("stop", {})
                return {"status": "ok", "delivered": detail}
            case "set_skills":
                selection_payload = dict(payload or {})
                if "selected" in selection_payload and "allowlist" not in selection_payload:
                    selection_payload["allowlist"] = selection_payload.get("selected")
                config = dict(getattr(self._agent, "config", {}) or {}) if self._agent else {}
                selection = normalize_skill_selection(config, selection_payload)
                self.session.metadata["skills_selection"] = selection
                if self._agent:
                    try:
                        overrides = {
                            "skills.allowlist": selection.get("allowlist") or [],
                            "skills.blocklist": selection.get("blocklist") or [],
                        }
                        self._agent.apply_runtime_overrides(overrides)
                    except Exception:
                        pass
                self._persist_metadata_snapshot_threadsafe()
                self._skills_catalog_cache = None
                catalog_payload = self.get_skill_catalog()
                await self.publish_event_async(EventType.SKILLS_SELECTION, {"selection": selection})
                await self.publish_event_async(EventType.SKILLS_CATALOG, catalog_payload)
                return {"status": "ok", "selection": selection, "catalog": catalog_payload.get("catalog")}
            case "stop":
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
                return {"status": "ok", "stopping": True}
            case "set_model":
                model_value = payload.get("model")
                if not isinstance(model_value, str) or not model_value.strip():
                    raise ValueError("set_model requires non-empty 'model'")
                try:
                    cfg = dict(getattr(self._agent, "config", {}) or {}) if self._agent else load_agent_config(self.request.config_path)
                except Exception:
                    cfg = {}
                policy = PolicyPack.from_config(cfg)
                if (policy.model_allowlist is not None or policy.model_denylist) and not policy.is_model_allowed(model_value.strip()):
                    raise ValueError(f"set_model denied by policy: {model_value.strip()}")
                self._model_override = model_value.strip()
                self.session.metadata["model"] = self._model_override
                self._apply_model_override()
                return {"status": "ok", "model": self._model_override}
            case "set_mode":
                mode_value = payload.get("mode")
                if not isinstance(mode_value, str) or not mode_value.strip():
                    raise ValueError("set_mode requires non-empty 'mode'")
                self._mode = mode_value.strip()
                self.session.metadata["mode"] = self._mode
                return {"status": "ok", "mode": self._mode}
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

                queue = getattr(self, "_permission_queue", None)
                if queue is None:
                    if self._debug_permissions_enabled():
                        response_payload: Dict[str, Any] = {"request_id": request_id.strip()}
                        if isinstance(responses, dict):
                            response_payload["responses"] = dict(responses)
                        elif isinstance(response, str) and response.strip():
                            response_payload["response"] = response.strip()
                            response_payload["decision"] = response.strip()
                        self._update_pending_permissions("permission_response", response_payload, source="session")
                        await self.publish_event_async(EventType.PERMISSION_RESPONSE, response_payload)
                        return {"status": "ok", "request_id": request_id.strip(), "delivered": response_payload, "debug": True}
                    raise RuntimeError("no permission request is active")

                if isinstance(items, dict) and not isinstance(responses, dict):
                    responses = {"items": dict(items)}

                if isinstance(responses, dict):
                    item: Dict[str, Any] = {"request_id": request_id.strip(), "responses": dict(responses)}
                else:
                    if not isinstance(response, str) or not response.strip():
                        raise ValueError("respond_permission requires non-empty 'response' when 'responses' is not provided")
                    item = {"permission_id": request_id.strip(), "response": response.strip()}
                try:
                    put_nowait = getattr(queue, "put_nowait", None)
                    if callable(put_nowait):
                        put_nowait(item)
                    else:
                        queue.put(item)
                except Exception as exc:
                    raise RuntimeError(f"failed to deliver permission response: {exc}") from exc
                return {"status": "ok", "request_id": request_id.strip(), "delivered": item}
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

    def _resolve_workspace_guess(self, base_cfg: Dict[str, Any]) -> Optional[Path]:
        candidate: Any = self.request.workspace
        if not candidate and isinstance(base_cfg, dict):
            ws_cfg = base_cfg.get("workspace")
            if isinstance(ws_cfg, dict):
                candidate = ws_cfg.get("root") or ws_cfg.get("path")
        if not candidate:
            return None
        try:
            return Path(str(candidate)).expanduser().resolve()
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
        self._agent = self.agent_factory(
            self.request.config_path,
            self.request.workspace,
            self.request.overrides,
        )
        await asyncio.to_thread(self._agent.initialize)
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

                if evt_type is EventType.TOOL_RESULT:
                    todo_update = payload.get("todo")
                    if isinstance(todo_update, dict):
                        try:
                            self.session.metadata["todo_last_update"] = dict(todo_update)
                            self._persist_metadata_snapshot_threadsafe()
                        except Exception:
                            pass

                await self.publish_event_async(evt_type, payload, turn=turn)
                published_events += 1

                if evt_type is EventType.COMPLETION:
                    published_completion = True
                elif evt_type is EventType.RUN_FINISHED:
                    published_run_finished = True

        completion_summary: Dict[str, Any] = {"completed": True, "reason": "replay"}
        if not published_completion:
            await self.publish_event_async(EventType.COMPLETION, {"summary": completion_summary, "mode": self._mode})
            published_events += 1
        if not published_run_finished:
            await self.publish_event_async(
                EventType.RUN_FINISHED,
                {"eventCount": published_events, "completed": True, "reason": "replay", "logging_dir": None},
            )

        return {
            "completion_summary": completion_summary,
            "reward_metrics": None,
            "logging_dir": None,
        }

    async def _run(self) -> None:
        await self.registry.update_status(self.session.session_id, SessionStatus.RUNNING)
        try:
            # Safety: never auto-wipe an existing workspace when running interactive sessions
            # via the CLI bridge. The engine historically treated workspaces as disposable
            # sandboxes; for a Claude Code-style experience we must preserve the user's
            # working directory unless explicitly overridden by the caller.
            os.environ.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
            overrides = dict(self.request.overrides or {})
            permission_mode = (self.request.permission_mode or self.session.metadata.get("permission_mode") or "").strip().lower()
            if permission_mode in {"prompt", "ask", "interactive"}:
                overrides.setdefault("permissions.options.mode", "prompt")
                overrides.setdefault("permissions.options.default_response", "reject")
                overrides.setdefault("permissions.edit.default", "ask")
                overrides.setdefault("permissions.shell.default", "ask")
                overrides.setdefault("permissions.webfetch.default", "ask")
                if not self.request.permission_mode:
                    self.request.permission_mode = permission_mode
                self.session.metadata["permission_mode"] = permission_mode

            # Merge persisted project-scoped permission rules (allow/deny) into overrides.
            base_cfg = self._load_base_config()
            workspace_guess_path = self._resolve_workspace_guess(base_cfg)
            if workspace_guess_path:
                self._workspace_path = workspace_guess_path

            if workspace_guess_path:
                try:
                    rules = load_permission_rules(workspace_guess_path)
                except Exception:
                    rules = []
                if rules:
                    merged = build_permission_overrides(base_cfg, rules)

                    def _merge_list(existing: Any, extra: Any) -> Any:
                        if not isinstance(existing, list) or not isinstance(extra, list):
                            return extra
                        out: list[Any] = []
                        for item in list(existing) + list(extra):
                            if item not in out:
                                out.append(item)
                        return out

                    for key, value in merged.items():
                        if key in overrides:
                            overrides[key] = _merge_list(overrides.get(key), value)
                        else:
                            overrides[key] = value

            self.request.overrides = overrides

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
                if self._parse_replay_path(task_text) is not None:
                    result = await self._execute_replay_task(task_text)
                    await self.registry.update_metadata(
                        self.session.session_id,
                        completion_summary=result.get("completion_summary"),
                        reward_summary=result.get("reward_metrics"),
                        logging_dir=result.get("logging_dir"),
                        metadata=self.session.metadata,
                    )
                    self._input_queue.task_done()
                    continue
                attachment_ids = task_payload.get("attachments") or []
                attachment_text = self._format_attachment_helper(attachment_ids)
                if attachment_text:
                    task_text = f"{task_text.rstrip()}\n\n{attachment_text}"

                await self._ensure_agent_initialized()
                result = await asyncio.to_thread(self._execute_task, task_text)
                await self.registry.update_metadata(
                    self.session.session_id,
                    completion_summary=result.get("completion_summary"),
                    reward_summary=result.get("reward_metrics"),
                    logging_dir=result.get("logging_dir"),
                    metadata=self.session.metadata,
                )
                self._input_queue.task_done()

            final_status = SessionStatus.STOPPED if self._stop_event.is_set() else SessionStatus.COMPLETED
            await self.registry.update_status(self.session.session_id, final_status)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Session %s failed", self.session.session_id)
            await self.registry.update_status(self.session.session_id, SessionStatus.FAILED)
            await self.publish_event_async(EventType.ERROR, {"message": str(exc)})
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

    def _apply_model_override(self) -> None:
        if not self._agent or not self._model_override:
            return
        try:
            providers = self._agent.config.setdefault("providers", {})  # type: ignore[attr-defined]
            providers["default_model"] = self._model_override
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to apply model override: %s", exc)

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
        self,
        kind: str,
        info: Dict[str, Any],
        *,
        source: str = "session",
        task_session_id: Optional[str] = None,
        subagent_type: Optional[str] = None,
    ) -> None:
        req_id = (
            info.get("request_id")
            or info.get("requestId")
            or info.get("permission_id")
            or info.get("permissionId")
            or info.get("id")
        )
        if not isinstance(req_id, str) or not req_id.strip():
            return
        request_id = req_id.strip()
        pending = self.session.metadata.get("pending_permissions")
        if not isinstance(pending, list):
            pending = []

        entry: Dict[str, Any] = {
            "source": str(source or "session"),
            "request_id": request_id,
        }
        if task_session_id:
            entry["task_session_id"] = task_session_id
        if subagent_type:
            entry["subagent_type"] = subagent_type

        if kind == "permission_request":
            entry["request"] = dict(info or {})
            normalized = [p for p in pending if self._pending_permission_key(p) != self._pending_permission_key(entry)]
            normalized.append(entry)
            self.session.metadata["pending_permissions"] = normalized
            self._persist_metadata_snapshot_threadsafe()
            return

        if kind == "permission_response":
            normalized = [p for p in pending if self._pending_permission_key(p) != self._pending_permission_key(entry)]
            if normalized:
                self.session.metadata["pending_permissions"] = normalized
            else:
                self.session.metadata.pop("pending_permissions", None)
            self._persist_metadata_snapshot_threadsafe()

    def _rehydrate_pending_permissions(self, event_type: str, payload: Dict[str, Any]) -> None:
        if event_type in {"permission_request", "permission_response"}:
            self._update_pending_permissions(event_type, dict(payload or {}), source="session")
            return
        if event_type != "task_event":
            return
        kind = str((payload or {}).get("kind") or "")
        if kind not in {"permission_request", "permission_response"}:
            return
        child_payload = (payload or {}).get("payload") or {}
        self._update_pending_permissions(
            kind,
            dict(child_payload) if isinstance(child_payload, dict) else {"payload": child_payload},
            source="task",
            task_session_id=str((payload or {}).get("sessionId") or ""),
            subagent_type=str((payload or {}).get("subagent_type") or ""),
        )

    def _execute_task(self, task_text: str) -> Dict[str, Any]:
        if not self._agent:
            raise RuntimeError("agent missing")

        emitted_flags = {"assistant": False}
        self._published_events = 0
        is_local_agent = bool(getattr(self._agent, "_local_mode", False))
        event_queue = None
        permission_queue = None
        control_queue = None
        queue_stop = None
        queue_thread = None

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
            try:
                self._rehydrate_pending_permissions(event_type, dict(payload or {}))
            except Exception:
                pass
            translated = self._translate_runtime_event(event_type, payload, turn)
            if not translated:
                return
            evt_type, evt_payload, evt_turn = translated
            if evt_type is EventType.ASSISTANT_MESSAGE:
                emitted_flags["assistant"] = True
            self.publish_event(evt_type, evt_payload, turn=evt_turn)

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
        try:
            task_context = {}
            try:
                if isinstance(self.session.metadata, dict):
                    task_context = dict(self.session.metadata.get("task_context") or {})
                    if "task_type" in self.session.metadata and "task_type" not in task_context:
                        task_context["task_type"] = self.session.metadata.get("task_type")
            except Exception:
                task_context = {}
            result = self._agent.run_task(  # type: ignore[call-arg]
                task_text,
                max_iterations=self.request.max_steps,
                stream=self.request.stream,
                event_emitter=handle_runtime_event if is_local_agent else None,
                event_queue=event_queue,
                permission_queue=permission_queue,
                control_queue=control_queue,
                context=task_context if task_context else None,
            )
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
                queue_thread.join(timeout=2)
            if event_queue is not None:
                self._drain_event_queue(event_queue, handle_runtime_event)

        elapsed_ms = int((time.time() - start_time) * 1000)
        completion = result.get("completion_summary") or {}
        reward = result.get("reward_metrics_payload") or {}
        messages = result.get("messages")
        if not emitted_flags["assistant"] and isinstance(messages, list):
            for entry in reversed(messages):
                if isinstance(entry, dict) and entry.get("role") == "assistant":
                    text = entry.get("content", "")
                    self.publish_event(
                        EventType.ASSISTANT_MESSAGE,
                        {"text": text, "message": entry, "source": "fallback"},
                    )
                    break
        logging_dir = result.get("logging_dir") or result.get("run_dir")
        usage_payload = self._extract_usage_metrics(result, logging_dir, elapsed_ms=elapsed_ms)
        completion_payload: Dict[str, Any] = {"summary": completion, "mode": self._mode}
        if usage_payload:
            completion_payload["usage"] = usage_payload
        self.publish_event(EventType.COMPLETION, completion_payload)
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
        self.publish_event(EventType.RUN_FINISHED, finish_payload)
        return {
            "completion_summary": completion,
            "reward_metrics": reward or None,
            "logging_dir": logging_dir,
        }

    async def publish_event_async(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        *,
        turn: Optional[int] = None,
    ) -> None:
        self._touch_last_activity()
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
        )
        await self._enqueue_event_async(event)

    def publish_event(self, event_type: EventType, payload: Dict[str, Any], *, turn: Optional[int] = None) -> None:
        self._touch_last_activity()
        event = SessionEvent(
            type=event_type,
            session_id=self.session.session_id,
            payload=payload,
            turn=turn,
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
        normalized = dict(payload or {})
        if "taskId" in normalized and "task_id" not in normalized:
            normalized["task_id"] = normalized.get("taskId")
        if "subagentType" in normalized and "subagent_type" not in normalized:
            normalized["subagent_type"] = normalized.get("subagentType")
        if "artifactPath" in normalized and "artifact_path" not in normalized:
            normalized["artifact_path"] = normalized.get("artifactPath")
        artifact = normalized.get("artifact")
        if "artifact_path" not in normalized and isinstance(artifact, dict):
            path = artifact.get("path") or artifact.get("artifact_path")
            if path:
                normalized["artifact_path"] = path
        if "description" not in normalized:
            for key in ("description", "title", "summary"):
                value = normalized.get(key)
                if value:
                    normalized["description"] = str(value)
                    break
        if "status" not in normalized:
            kind = str(normalized.get("kind") or "").lower()
            status_map = {
                "subagent_spawned": "running",
                "subagent_started": "running",
                "subagent_completed": "completed",
                "subagent_failed": "failed",
            }
            status = status_map.get(kind)
            if status:
                normalized["status"] = status
        if "timestamp" not in normalized:
            for key in ("timestamp", "created_at", "completed_at", "ts"):
                value = normalized.get(key)
                if isinstance(value, (int, float)):
                    normalized["timestamp"] = value
                    break
            else:
                normalized["timestamp"] = time.time()
        if "parentTaskId" in normalized and "parent_task_id" not in normalized:
            normalized["parent_task_id"] = normalized.get("parentTaskId")
        if "treePath" in normalized and "tree_path" not in normalized:
            normalized["tree_path"] = normalized.get("treePath")
        if "taskDepth" in normalized and "depth" not in normalized:
            normalized["depth"] = normalized.get("taskDepth")
        if "taskPriority" in normalized and "priority" not in normalized:
            normalized["priority"] = normalized.get("taskPriority")
        if "sessionId" in normalized and "task_session_id" not in normalized:
            normalized["task_session_id"] = normalized.get("sessionId")
        task_id = normalized.get("task_id") or normalized.get("id")
        if task_id and "tree_path" not in normalized:
            normalized["tree_path"] = f"task/{task_id}"
        if "depth" not in normalized:
            normalized["depth"] = 0
        return normalized

    def _translate_runtime_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        turn: Optional[int],
    ) -> Optional[tuple[EventType, Dict[str, Any], Optional[int]]]:
        mapping = {
            "turn_start": EventType.TURN_START,
            "assistant_message": EventType.ASSISTANT_MESSAGE,
            "assistant_delta": EventType.ASSISTANT_DELTA,
            "user_message": EventType.USER_MESSAGE,
            "tool_call": EventType.TOOL_CALL,
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
                text = str(message.get("content", ""))
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
                text = str(message.get("content", ""))
            normalized_payload = {"text": text, "message": message}
        elif evt is EventType.TOOL_CALL:
            normalized_payload = self._normalize_tool_call_payload(normalized_payload)
        elif evt is EventType.TOOL_RESULT:
            normalized_payload = self._normalize_tool_result_payload(normalized_payload)
        elif evt is EventType.PERMISSION_REQUEST:
            normalized_payload = self._normalize_permission_request(normalized_payload)
        elif evt is EventType.PERMISSION_RESPONSE:
            normalized_payload = self._normalize_permission_response(normalized_payload)
        elif evt is EventType.TASK_EVENT:
            normalized_payload = self._normalize_task_event(normalized_payload)
        return evt, normalized_payload, turn

    def _resolve_skill_catalog(self) -> Dict[str, Any]:
        config: Dict[str, Any]
        if self._agent is not None:
            config = dict(getattr(self._agent, "config", {}) or {})
        else:
            try:
                config = load_agent_config(self.request.config_path)
            except Exception:
                config = {}
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
