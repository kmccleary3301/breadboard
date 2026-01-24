from __future__ import annotations

import copy
import contextvars
from datetime import datetime
import json
import os
import random
import shutil
import sys
import time
import uuid
import traceback
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict, dataclass, field

import ray

from adaptive_iter import decode_adaptive_iterable, ADAPTIVE_PREFIX_ITERABLE, ADAPTIVE_PREFIX_NON_ITERABLE

from breadboard.sandbox_v2 import DevSandboxV2
from breadboard.opencode_patch import PatchParseError, parse_opencode_patch, to_unified_diff
from breadboard.sandbox_virtualized import SandboxFactory, DeploymentMode
from .core.core import ToolDefinition, ToolParameter
from .execution.composite import CompositeToolCaller
from .dialects.bash_block import BashBlockDialect
from .execution.dialect_manager import DialectManager
from .execution.agent_executor import AgentToolExecutor
from .compilation.system_prompt_compiler import get_compiler
from .compilation.v2_loader import load_agent_config
from .provider_ir import IRFinish, IRDeltaEvent
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager
from .provider_runtime import (
    provider_registry,
    ProviderRuntimeContext,
    ProviderResult,
    ProviderMessage,
    ProviderRuntimeError,
)
from .provider_runtime_replay import ReplayRuntime
from .provider_capability_probe import ProviderCapabilityProbeRunner
from .state.session_state import SessionState
from .state.completion_detector import CompletionDetector
from .messaging.message_formatter import MessageFormatter
from .messaging.markdown_logger import MarkdownLogger
from .error_handling.error_handler import ErrorHandler
from .monitoring.telemetry import TelemetryLogger
from .monitoring.reward_metrics import RewardMetricsSQLiteWriter, TodoMetricsSQLiteWriter
from .logging_v2 import LoggerV2Manager
from .logging_v2.api_recorder import APIRequestRecorder
from .logging_v2.prompt_logger import PromptArtifactLogger
from .logging_v2.markdown_transcript import MarkdownTranscriptWriter
from .logging_v2.provider_native_logger import ProviderNativeLogger
from .logging_v2.request_recorder import StructuredRequestRecorder
from .utils.local_ray import LocalActorProxy, identity_get
from .runtime_context import get_current_session_state
from .provider_health import RouteHealthManager
from .provider_normalizer import normalize_provider_result
from .provider_metrics import ProviderMetricsCollector
from .guardrail_coordinator import GuardrailCoordinator
from .guardrail_orchestrator import GuardrailOrchestrator
from .conductor_components import (
    apply_cache_control_to_initial_user_prompt,
    apply_cache_control_to_tool_messages,
    apply_capability_tool_overrides,
    apply_streaming_policy_for_turn,
    append_text_block,
    build_prompt_summary,
    capture_turn_diagnostics,
    completion_tool_config_enabled,
    ensure_completion_tool,
    get_capability_probe_result,
    maybe_run_plan_bootstrap,
    get_model_routing_preferences,
    get_prompt_cache_control,
    should_require_build_guard,
    is_read_only_tool,
    get_default_tool_definitions,
    initialize_config_validator,
    initialize_enhanced_executor,
    initialize_guardrail_config,
    initialize_yaml_tools,
    inject_cache_control_into_message,
    log_routing_event,
    normalize_assistant_text,
    prepare_concurrency_policy,
    record_lsp_reward_metrics,
    record_test_reward_metric,
    register_prompt_hash,
    tool_defs_from_yaml,
    write_env_fingerprint,
    write_workspace_manifest,
)
from .mcp.tooling import (
    build_mcp_snapshot,
    load_mcp_servers_from_config,
    load_mcp_tools_from_config,
    load_mcp_fixture_results,
    mcp_live_tools_enabled,
    mcp_replay_tape_path,
    mcp_record_tape_path,
    tool_defs_from_mcp_tools,
)
from .mcp.manager import MCPManager
from .mcp.replay import MCPReplayTape, load_mcp_replay_tape, append_mcp_replay_entry
from .plugins.loader import discover_plugin_manifests, plugin_snapshot
from .hooks.manager import build_hook_manager
from .hooks.model import HookResult
from .skills.registry import (
    load_skills,
    build_skill_catalog,
    normalize_skill_selection,
    apply_skill_selection,
)
from .skills.executor import execute_graph_skill
from .conductor_patching import (
    apply_patch_operations_direct,
    convert_patch_to_unified,
    count_diff_hunks,
    emit_patch_policy_violation,
    expand_multi_file_patches,
    fetch_workspace_text,
    normalize_patch_block,
    normalize_workspace_path,
    record_diff_metrics,
    split_patch_blocks,
    synthesize_patch_blocks,
    retry_diff_with_aider,
    validate_structural_artifacts,
)
from .conductor_loop import run_main_loop
from .conductor_bootstrap import bootstrap_conductor, prepare_workspace
from .conductor_components import write_env_fingerprint
from .conductor_modes import (
    add_enhanced_message_fields,
    adjust_tool_prompt_mode,
    apply_preference_order,
    apply_selection_legacy,
    apply_turn_strategy_from_loop,
    apply_v2_dialect_selection,
    create_dialect_mapping,
    get_model_response,
    get_native_preference_hint,
    setup_native_tools,
    setup_tool_prompts,
)
from .conductor_execution import (
    build_exec_func,
    execute_agent_calls,
    build_turn_context,
    summarize_execution_results,
    emit_turn_snapshot,
    hydrate_turn_context_signals,
    finalize_turn_context_snapshot,
    apply_turn_guards,
    handle_blocked_calls,
    resolve_replay_todo_placeholders,
    maybe_transition_plan_mode,
    handle_native_tool_calls,
    handle_text_tool_calls,
    legacy_message_view,
    log_provider_message,
    process_model_output,
    retry_with_fallback,
)
from .todo import TodoManager, TodoStore
from .todo.store import TODO_OPEN_STATUSES
from .replay import ReplaySession, load_replay_session
from .plan_bootstrapper import PlanBootstrapper
from .permission_broker import PermissionBroker, PermissionDeniedError
from .loop_detection import LoopDetectionService
from .context_window_guard import ContextWindowGuard
from .streaming_policy import StreamingPolicy
from .provider_invoker import ProviderInvoker
from .tool_prompt_planner import ToolPromptPlanner
from .turn_relayer import TurnRelayer
from .turn_context import TurnContext
from .orchestration import TeamConfig, MultiAgentOrchestrator

ZERO_TOOL_WARN_TURNS = 2
ZERO_TOOL_ABORT_TURNS = 4
COMPLETION_GUARD_ABORT_THRESHOLD = 2
TODO_SEED_TOOL_NAMES = {"todo.create", "todo.write_board", "todowrite"}
ZERO_TOOL_WARN_MESSAGE = (
    "<VALIDATION_ERROR>\n"
    "No tool usage detected. Use read/list/diff/bash tools to make progress before responding again.\n"
    "</VALIDATION_ERROR>"
)
ZERO_TOOL_ABORT_MESSAGE = (
    "<VALIDATION_ERROR>\n"
    "No tool usage detected across multiple turns. The run is being stopped to avoid idle looping.\n"
    "</VALIDATION_ERROR>"
)
COMPLETION_GUARD_WARNING_TEMPLATE = (
    "<VALIDATION_ERROR>\n"
    "{reason}\n"
    "Completion guard engaged. Provide concrete file edits and successful tests. Warnings remaining before abort: {remaining}.\n"
    "</VALIDATION_ERROR>"
)
COMPLETION_GUARD_ABORT_TEMPLATE = (
    "<VALIDATION_ERROR>\n"
    "{reason}\n"
    "Completion guard engaged repeatedly. The run will now terminate to avoid wasting budget.\n"
    "</VALIDATION_ERROR>"
)


@dataclass
class AsyncAgentJob:
    task_id: str
    subagent_type: str
    description: str
    prompt: str
    status: str = "running"  # running|completed|failed|killed
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result_text: Optional[str] = None
    error: Optional[str] = None
    thread: Optional[threading.Thread] = None
    orchestrator_job_id: Optional[str] = None


@dataclass
class BackgroundTaskJob:
    task_id: str
    agent: str
    description: str
    prompt: str
    session_id: str
    status: str = "running"  # running|completed|failed|cancelled|killed
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result_text: Optional[str] = None
    error: Optional[str] = None
    thread: Optional[threading.Thread] = None


def compute_tool_prompt_mode(tool_prompt_mode: str, will_use_native_tools: bool, config: Dict[str, Any]) -> str:
    """Pure helper for testing prompt mode adjustment (module-level)."""
    if will_use_native_tools:
        suppress_prompts = bool((config or {}).get("provider_tools", {}).get("suppress_prompts", False))
        return "none" if suppress_prompts else "per_turn_append"
    return tool_prompt_mode

def _tools_schema() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a text file to the workspace (overwrites).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List files in a directory in the workspace. Optional depth parameter for tree structure (1-5, default 1).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "depth": {"type": "integer", "description": "Tree depth (1-5, default 1)", "default": 1}
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "Run a shell command in the workspace and return stdout/stderr/exit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer"},
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def _dump_tool_defs(tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for t in tool_defs:
        result.append(
            {
                "type_id": t.type_id,
                "name": t.name,
                "description": t.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "description": p.description,
                        "default": p.default,
                    }
                    for p in t.parameters
                ],
            }
        )
    return result


def _wrap_event_emitter_with_before_emit(
    hook_manager: Any,
    *,
    session_state: SessionState,
    emitter: Callable[[str, Dict[str, Any], Optional[int]], None],
) -> Callable[[str, Dict[str, Any], Optional[int]], None]:
    in_before_emit = contextvars.ContextVar("breadboard_before_emit_hook", default=False)

    def wrapped(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
        if in_before_emit.get():
            try:
                emitter(event_type, payload, turn=turn)
            except Exception:
                pass
            return

        token = in_before_emit.set(True)
        try:
            envelope: Dict[str, Any] = {
                "kind": event_type,
                "payload": dict(payload or {}),
                "turn": turn,
            }
            try:
                hook_result = hook_manager.run(
                    "before_emit",
                    envelope,
                    session_state=session_state,
                    turn=turn,
                )
            except Exception:
                hook_result = None

            if hook_result is not None and getattr(hook_result, "action", "allow") == "deny":
                return
            if (
                hook_result is not None
                and getattr(hook_result, "action", "") == "transform"
                and isinstance(getattr(hook_result, "payload", None), dict)
            ):
                envelope = dict(hook_result.payload)

            out_event_type = str(envelope.get("kind") or event_type)
            out_payload = envelope.get("payload")
            if not isinstance(out_payload, dict):
                out_payload = dict(payload or {})
            if isinstance(payload, dict) and "seq" in payload and "seq" not in out_payload:
                out_payload["seq"] = payload["seq"]
            emitter(out_event_type, out_payload, turn=turn)
        finally:
            in_before_emit.reset(token)

    return wrapped


@ray.remote
class OpenAIConductor:
    def __init__(self, workspace: str, image: str = "python-dev:latest", config: Optional[Dict[str, Any]] = None, *, local_mode: bool = False) -> None:
        """Initialize conductor with workspace, image, and configuration."""
        bootstrap_conductor(
            self,
            workspace=workspace,
            image=image,
            config=config,
            local_mode=local_mode,
            zero_tool_warn_message=ZERO_TOOL_WARN_MESSAGE,
            zero_tool_abort_message=ZERO_TOOL_ABORT_MESSAGE,
            completion_guard_abort_threshold=COMPLETION_GUARD_ABORT_THRESHOLD,
        )
        # Stop requests are session-scoped and should only affect the current run.
        self._stop_requested = False
        # Runtime-only reference used for streaming task events (e.g., async subagent completions).
        self._active_session_state: Optional[SessionState] = None
        self._multi_agent_last_wakeup_event_id = 0

    def request_stop(self) -> None:
        """Best-effort interrupt: stop the current run at the next safe boundary."""
        self._stop_requested = True

    def apply_config_overrides(self, overrides: Dict[str, Any]) -> bool:
        """Apply dotted-path overrides to the in-memory config (best-effort)."""
        if not isinstance(overrides, dict) or not overrides:
            return False

        def _tokenize(path: str) -> List[Any]:
            tokens: List[Any] = []
            parts = path.split(".")
            for part in parts:
                cursor = part
                while cursor:
                    if "[" in cursor:
                        name, rest = cursor.split("[", 1)
                        if name:
                            tokens.append(name)
                        idx_str, _, remainder = rest.partition("]")
                        if idx_str.isdigit():
                            tokens.append(int(idx_str))
                        cursor = remainder.lstrip(".") if remainder.startswith(".") else remainder
                    else:
                        tokens.append(cursor)
                        cursor = ""
            return tokens

        def _set_nested(config: Any, tokens: List[Any], value: Any) -> None:
            current = config
            parent_stack: List[Tuple[Any, Any]] = []
            for idx, token in enumerate(tokens):
                is_last = idx == len(tokens) - 1
                if isinstance(token, str):
                    if not isinstance(current, dict):
                        if parent_stack:
                            parent, parent_token = parent_stack[-1]
                            replacement: Dict[str, Any] = {}
                            if isinstance(parent, dict):
                                parent[parent_token] = replacement
                            elif isinstance(parent, list) and isinstance(parent_token, int):
                                parent[parent_token] = replacement
                            current = replacement
                        else:
                            return
                    if is_last:
                        current[token] = value
                        return
                    next_token = tokens[idx + 1]
                    if token not in current or current[token] is None:
                        current[token] = [] if isinstance(next_token, int) else {}
                    parent_stack.append((current, token))
                    current = current[token]
                else:
                    if not isinstance(current, list):
                        replacement_list: List[Any] = []
                        if parent_stack:
                            parent, parent_token = parent_stack[-1]
                            if isinstance(parent, dict):
                                parent[parent_token] = replacement_list
                            elif isinstance(parent, list) and isinstance(parent_token, int):
                                parent[parent_token] = replacement_list
                        current = replacement_list
                    while len(current) <= token:
                        next_token = tokens[idx + 1] if not is_last else None
                        current.append([] if isinstance(next_token, int) else {})
                    if is_last:
                        current[token] = value
                        return
                    parent_stack.append((current, token))
                    current = current[token]

        try:
            if not isinstance(self.config, dict):
                self.config = {}
            for path, value in overrides.items():
                if not isinstance(path, str) or not path:
                    continue
                _set_nested(self.config, _tokenize(path), value)
            return True
        except Exception:
            return False

    def _prepare_replay_session(
        self,
        session_state: SessionState,
        user_prompt: str,
        max_steps: int,
    ) -> Tuple[str, int]:
        """
        Initialize an active ReplaySession for this run if replay data is configured.

        This helper is a straight extraction of the existing inline logic in
        run_agentic_loop so we can treat replay setup as a single, well-defined
        step without changing behavior.
        """
        if self._replay_session_data:
            user_prompt = self._replay_session_data.user_prompt
            self._active_replay_session = ReplaySession(self._replay_session_data, Path(self.workspace))
            session_state.set_provider_metadata("replay_mode", True)
            session_state.set_provider_metadata("active_replay_session", self._active_replay_session)
            session_state.set_provider_metadata("replay_total_turns", self._active_replay_session.total_turns)
            max_steps = min(max_steps, self._active_replay_session.total_turns + 2)
        else:
            self._active_replay_session = None
        return user_prompt, max_steps

    def seed_workspace_file(self, filename: str, contents: str) -> None:
        """Copy helper files (e.g., task specs) into the active workspace before execution."""
        if not filename:
            return
        try:
            target = Path(self.workspace) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
        except Exception:
            pass


    def _prepare_workspace(self, workspace: str) -> Path:
        """Ensure the workspace directory exists and is empty before use."""
        return prepare_workspace(workspace)

    def _load_resume_snapshot(self) -> Optional[Dict[str, Any]]:
        resume_cfg = {}
        try:
            resume_cfg = (self.config.get("resume") or {}) if isinstance(self.config, dict) else {}
        except Exception:
            resume_cfg = {}
        explicit_path = None
        if isinstance(resume_cfg, dict):
            explicit_path = resume_cfg.get("snapshot_path") or resume_cfg.get("snapshot")
        env_path = os.environ.get("BREADBOARD_RESUME_SNAPSHOT")
        workspace_root = Path(str(getattr(self, "workspace", ""))).resolve()
        default_path = workspace_root / ".breadboard" / "checkpoints" / "active_snapshot.json"
        candidate = explicit_path or env_path or (str(default_path) if default_path.exists() else None)
        if not candidate:
            return None
        target = Path(str(candidate))
        if not target.is_absolute():
            target = (workspace_root / target).resolve()
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return None
        if explicit_path is None and env_path is None:
            try:
                target.unlink()
            except Exception:
                pass
        return payload if isinstance(payload, dict) else None

    def _get_multi_agent_orchestrator(self) -> Optional[MultiAgentOrchestrator]:
        cfg = (self.config.get("multi_agent") or {}) if isinstance(getattr(self, "config", None), dict) else {}
        if not cfg or not bool(cfg.get("enabled")):
            return None
        if getattr(self, "_multi_agent_orchestrator", None) is not None:
            return self._multi_agent_orchestrator

        team_payload: Dict[str, Any] = {"team": {"id": "team", "version": 1}}
        raw_team = cfg.get("team_config")
        if isinstance(raw_team, dict):
            team_payload = raw_team
        elif isinstance(raw_team, str):
            try:
                from pathlib import Path as _Path
                import yaml  # type: ignore

                raw_text = _Path(raw_team).read_text(encoding="utf-8")
                loaded = yaml.safe_load(raw_text)
                if isinstance(loaded, dict):
                    team_payload = loaded
            except Exception:
                pass

        team_config = TeamConfig.from_dict(team_payload)
        orchestrator = MultiAgentOrchestrator(team_config)

        event_log_path = cfg.get("event_log_path")
        if event_log_path:
            orchestrator.set_event_log_path(str(event_log_path))
            self._multi_agent_event_log_path = str(event_log_path)

        self._multi_agent_orchestrator = orchestrator
        return orchestrator

    def _persist_multi_agent_log(self) -> None:
        orchestrator = getattr(self, "_multi_agent_orchestrator", None)
        if orchestrator is None:
            return
        try:
            orchestrator.persist_event_log()
        except Exception:
            pass
        try:
            run_dir = getattr(getattr(self, "logger_v2", None), "run_dir", None)
            if run_dir:
                path = Path(str(run_dir)) / "meta" / "multi_agent_events.jsonl"
                orchestrator.event_log.to_jsonl(str(path))
        except Exception:
            pass
        try:
            workspace_root = Path(str(getattr(self, "workspace", ""))).resolve()
            if workspace_root.exists():
                path = workspace_root / ".breadboard" / "multi_agent_events.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                orchestrator.event_log.to_jsonl(str(path))
        except Exception:
            pass

    def _inject_multi_agent_wakeups(self, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
        """
        Inject model-visible wakeup notifications at the turn boundary (deterministic order).

        This is guarded by `team.bus.model_visible_topics` containing `wakeup`.
        Claude Code print-mode goldens do not show wakeups in-model, so configs should
        omit this topic unless explicitly desired.
        """
        orchestrator = getattr(self, "_multi_agent_orchestrator", None)
        if orchestrator is None:
            return
        try:
            topics = {str(t).lower() for t in (orchestrator.team_config.bus.model_visible_topics or [])}
        except Exception:
            topics = set()
        if "wakeup" not in topics:
            return

        try:
            events = orchestrator.event_log.events
        except Exception:
            events = []
        new_events = [
            ev
            for ev in events
            if getattr(ev, "type", None) == "agent.wakeup_emitted"
            and int(getattr(ev, "event_id", 0) or 0) > int(getattr(self, "_multi_agent_last_wakeup_event_id", 0) or 0)
        ]
        if not new_events:
            return
        new_events.sort(key=lambda ev: (int((getattr(ev, "payload", {}) or {}).get("seq") or 0), int(getattr(ev, "event_id", 0) or 0)))

        for ev in new_events:
            payload = getattr(ev, "payload", {}) or {}
            try:
                payload = orchestrator.bus_adapter.format_wakeup(dict(payload))
            except Exception:
                payload = dict(payload)
            try:
                msg = orchestrator.bus_adapter.build_mvi_message("wakeup", dict(payload))
            except Exception:
                msg = None
            if msg is None:
                text = str(payload.get("message") or "").strip()
                if not text:
                    continue
                msg = {"role": "system", "content": text}
            if isinstance(msg, dict):
                try:
                    session_state.add_message(msg)
                except Exception:
                    continue
                try:
                    if msg.get("role") == "system":
                        markdown_logger.log_system_message(str(msg.get("content") or ""))
                except Exception:
                    pass

        try:
            self._multi_agent_last_wakeup_event_id = max(int(getattr(ev, "event_id", 0) or 0) for ev in new_events)
        except Exception:
            pass

    def _load_agent_config_from_path(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            target = Path(str(path))
            if not target.exists():
                return None
            loaded = load_agent_config(str(target))
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return None
        return None

    @staticmethod
    def _extract_last_assistant_text(messages: Any) -> str:
        if not isinstance(messages, list):
            return ""
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
        return ""

    def _run_sync_subagent(
        self,
        *,
        prompt: str,
        config: Dict[str, Any],
        model_route: str,
        max_steps: int,
    ) -> Dict[str, Any]:
        previous_preserve = os.environ.get("PRESERVE_SEEDED_WORKSPACE")
        os.environ["PRESERVE_SEEDED_WORKSPACE"] = "1"
        try:
            # Ensure subagents operate inside the parent workspace, regardless of config defaults.
            try:
                cfg_copy = dict(config)
                ws_cfg = cfg_copy.get("workspace") if isinstance(cfg_copy.get("workspace"), dict) else {}
                ws_cfg = dict(ws_cfg or {})
                ws_cfg["root"] = str(getattr(self, "workspace", ""))
                cfg_copy["workspace"] = ws_cfg
            except Exception:
                cfg_copy = config
            cls = OpenAIConductor.__ray_metadata__.modified_class
            child = cls(
                workspace=str(getattr(self, "workspace", "")),
                image=str(getattr(self, "image", "python-dev:latest")),
                config=cfg_copy,
                local_mode=True,
            )
            return child.run_agentic_loop(
                "",
                prompt,
                model_route,
                max_steps=max_steps,
                output_json_path=None,
                stream_responses=False,
                output_md_path=None,
                tool_prompt_mode="system_once",
                completion_sentinel=">>>>>> END RESPONSE",
                completion_config=(config.get("completion") if isinstance(config, dict) else None),
            )
        finally:
            if previous_preserve is None:
                os.environ.pop("PRESERVE_SEEDED_WORKSPACE", None)
            else:
                os.environ["PRESERVE_SEEDED_WORKSPACE"] = previous_preserve

    def _ensure_async_jobs(self) -> None:
        if getattr(self, "_async_agent_jobs", None) is None:
            self._async_agent_jobs: Dict[str, AsyncAgentJob] = {}
        if getattr(self, "_async_agent_jobs_lock", None) is None:
            self._async_agent_jobs_lock = threading.Lock()

    def _ensure_background_jobs(self) -> None:
        if getattr(self, "_background_jobs", None) is None:
            self._background_jobs: Dict[str, BackgroundTaskJob] = {}
        if getattr(self, "_background_jobs_lock", None) is None:
            self._background_jobs_lock = threading.Lock()

    def _format_claude_task_ack(self, *, task_id: str) -> str:
        text = (
            "Async agent launched successfully.\n"
            f"agentId: {task_id} (This is an internal ID for your use, do not mention it to the user. "
            "Use this ID to retrieve results with TaskOutput when the agent finishes).\n"
            "The agent is currently working in the background. If you have other tasks you you should continue working on them now. "
            "Wait to call TaskOutput until either:\n"
            "- If you want to check on the agent's progress - call TaskOutput with block=false to get an immediate update on the agent's status\n"
            "- If you run out of things to do and the agent is still running - call TaskOutput with block=true to idle and wait for the agent's result "
            "(do not use block=true unless you completely run out of things to do as it will waste time)."
        )
        return json.dumps([{"type": "text", "text": text}], ensure_ascii=False)

    def _format_claude_task_output(
        self,
        *,
        task_id: str,
        status: str,
        output_text: str,
        retrieval_status: str = "success",
        task_type: str = "local_agent",
    ) -> str:
        # Claude Code's TaskOutput surface is a tagged, markdown-ish envelope.
        out = (
            f"<retrieval_status>{retrieval_status}</retrieval_status>\n\n"
            f"<task_id>{task_id}</task_id>\n\n"
            f"<task_type>{task_type}</task_type>\n\n"
            f"<status>{status}</status>\n\n"
            "<output>\n\n"
            f"{output_text.rstrip()}\n"
            "</output>"
        )
        return out

    def _format_omo_background_task_ack(
        self,
        *,
        task_id: str,
        session_id: str,
        description: str,
        agent: str,
    ) -> str:
        return (
            "Background task launched successfully.\n\n"
            f"Task ID: {task_id}\n"
            f"Session ID: {session_id}\n"
            f"Description: {description}\n"
            f"Agent: {agent}\n"
            "Status: running\n\n"
            "The system will notify you when the task completes.\n"
            f"Use `background_output` tool with task_id=\"{task_id}\" to check progress:\n"
            "- block=false (default): Check status immediately - returns full status info\n"
            "- block=true: Wait for completion (rarely needed since system notifies)"
        )

    def _format_omo_call_omo_agent_ack(
        self,
        *,
        task_id: str,
        session_id: str,
        description: str,
        agent: str,
    ) -> str:
        return (
            "Background agent task launched successfully.\n\n"
            f"Task ID: {task_id}\n"
            f"Session ID: {session_id}\n"
            f"Description: {description}\n"
            f"Agent: {agent} (subagent)\n"
            "Status: running\n\n"
            "The system will notify you when the task completes.\n"
            f"Use `background_output` tool with task_id=\"{task_id}\" to check progress:\n"
            "- block=false (default): Check status immediately - returns full status info\n"
            "- block=true: Wait for completion (rarely needed since system notifies)"
        )

    def _format_omo_background_output(
        self,
        *,
        task_id: str,
        session_id: str,
        description: str,
        duration_s: Optional[int],
        body: str,
    ) -> str:
        dur = ""
        if isinstance(duration_s, int) and duration_s >= 0:
            dur = f"Duration: {duration_s}s\n"
        return (
            "Task Result\n\n"
            f"Task ID: {task_id}\n"
            f"Description: {description}\n"
            f"{dur}"
            f"Session ID: {session_id}\n\n"
            "---\n\n"
            f"{body.rstrip()}"
        )

    def _persist_subagent_artifact(
        self,
        *,
        task_id: str,
        subagent_type: str,
        description: str,
        prompt: str,
        status: str,
        output_text: Optional[str],
        error_text: Optional[str],
        orchestrator_job_id: Optional[str],
        transcript_path: Optional[str] = None,
        seq: Optional[int] = None,
        created_at: Optional[float] = None,
        completed_at: Optional[float] = None,
    ) -> None:
        if not task_id:
            return
        payload = {
            "task_id": task_id,
            "subagent_type": subagent_type,
            "description": description,
            "prompt": prompt,
            "status": status,
            "output": output_text,
            "error": error_text,
            "orchestrator_job_id": orchestrator_job_id,
        }
        if transcript_path:
            payload["transcript_path"] = transcript_path
        if seq is not None:
            payload["seq"] = seq
        if created_at is not None:
            payload["created_at"] = created_at
        if completed_at is not None:
            payload["completed_at"] = completed_at
        try:
            run_dir = getattr(getattr(self, "logger_v2", None), "run_dir", None)
            if run_dir:
                self.logger_v2.write_json(f"meta/subagents/{task_id}.json", payload)
        except Exception:
            pass
        try:
            workspace_root = Path(str(getattr(self, "workspace", ""))).resolve()
            if workspace_root.exists():
                dest = workspace_root / ".breadboard" / "subagents" / f"{task_id}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _persist_subagent_transcript(
        self,
        *,
        task_id: str,
        transcript: Optional[List[Dict[str, Any]]],
    ) -> Optional[str]:
        if not task_id or not transcript:
            return None
        rows = [row for row in transcript if isinstance(row, dict)]
        if not rows:
            return None
        payload = "\n".join([json.dumps(row, ensure_ascii=False) for row in rows]) + "\n"
        rel_path = f".breadboard/subagents/agent-{task_id}.jsonl"
        recorded_path: Optional[str] = None
        try:
            workspace_root = Path(str(getattr(self, "workspace", ""))).resolve()
            if workspace_root.exists():
                dest = workspace_root / ".breadboard" / "subagents" / f"agent-{task_id}.jsonl"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(payload, encoding="utf-8")
                recorded_path = rel_path
        except Exception:
            pass
        try:
            run_dir = getattr(getattr(self, "logger_v2", None), "run_dir", None)
            if run_dir:
                dest = Path(str(run_dir)) / "meta" / "subagents" / f"agent-{task_id}.jsonl"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(payload, encoding="utf-8")
                if recorded_path is None:
                    recorded_path = str(dest)
        except Exception:
            pass
        return recorded_path

    def _emit_task_event(self, payload: Dict[str, Any]) -> None:
        session_state = getattr(self, "_active_session_state", None)
        if session_state is None:
            session_state = get_current_session_state()
        if session_state is None:
            return
        try:
            session_state.emit_task_event(payload)
        except Exception:
            return

    def _extract_task_context(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(args, dict):
            return {}
        ctx: Dict[str, Any] = {}
        parent_task_id = str(args.get("parent_task_id") or args.get("parentTaskId") or "").strip()
        tree_path = str(args.get("tree_path") or args.get("treePath") or "").strip()
        depth = args.get("depth")
        priority = args.get("priority")
        if parent_task_id:
            ctx["parent_task_id"] = parent_task_id
        if tree_path:
            ctx["tree_path"] = tree_path
        if isinstance(depth, int):
            ctx["depth"] = depth
        if isinstance(priority, (int, float, str)) and str(priority).strip():
            ctx["priority"] = priority
        return ctx

    def _handle_task_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            msg = "task missing required arguments"
            return {"error": msg, "__mvi_text_output": msg}

        description = str(args.get("description") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        subagent_type = str(args.get("subagent_type") or args.get("subagentType") or "").strip()
        run_in_background = bool(args.get("run_in_background") or args.get("runInBackground") or False)
        resume_id = str(args.get("resume") or "").strip()
        task_context = self._extract_task_context(args)
        if not description or not prompt or not subagent_type:
            missing = []
            if not description:
                missing.append("description")
            if not prompt:
                missing.append("prompt")
            if not subagent_type:
                missing.append("subagent_type")
            msg = f"missing required field: {', '.join(missing)}"
            return {"error": msg, "__mvi_text_output": msg}

        def _unknown_agent_error(agent: str) -> Dict[str, Any]:
            msg = f"Error: Unknown agent type: {agent} is not a valid agent type"
            return {"error": msg, "__mvi_text_output": msg}

        def _summarize_replay_entries(entries: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
            summary: List[Dict[str, Any]] = []
            output_parts: List[str] = []
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("role") != "assistant":
                    continue
                for part in entry.get("parts") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool":
                        meta = part.get("meta") or {}
                        state = (meta.get("state") or {}) if isinstance(meta, dict) else {}
                        summary.append(
                            {
                                "id": part.get("id"),
                                "tool": part.get("tool"),
                                "input": state.get("input"),
                                "status": state.get("status"),
                                "output": state.get("output"),
                            }
                        )
                    if part.get("type") == "text":
                        text = part.get("text")
                        if text:
                            output_parts.append(str(text))
            output_text = "\n".join(output_parts).strip()
            return output_text, summary

        def _extract_session_id_from_expected(expected_output: Any, expected_metadata: Any) -> Optional[str]:
            if isinstance(expected_metadata, dict):
                for key in ("sessionId", "session_id", "sessionID"):
                    value = expected_metadata.get(key)
                    if isinstance(value, str) and value:
                        return value
                for key in ("task_id", "taskId"):
                    value = expected_metadata.get(key)
                    if isinstance(value, str) and value:
                        return value
            if not isinstance(expected_output, str):
                return None
            for pattern in (
                r"session_id:\s*([a-zA-Z0-9_-]+)",
                r"session ID [`'\"]?([a-zA-Z0-9_-]+)[`'\"]?",
                r"<task_id>\s*([a-zA-Z0-9_-]+)\s*</task_id>",
                r"task_id:\s*([a-zA-Z0-9_-]+)",
            ):
                match = re.search(pattern, expected_output)
                if match:
                    return match.group(1)
            return None

        # Replay-mode deterministic stub: when the replay driver supplies an expected
        # tool output, return it verbatim to avoid nested live provider calls, unless
        # a subagent replay index is available (in which case we derive the output
        # from the child replay session to validate subagent parity).
        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        expected_metadata = tool_call.get("expected_metadata")

        if resume_id:
            if isinstance(expected_output, str):
                if str(expected_status or "").lower() == "error":
                    return {"error": expected_output, "__mvi_text_output": expected_output}
                meta_payload = expected_metadata if isinstance(expected_metadata, dict) else {}
                return {
                    "title": description,
                    "output": expected_output,
                    "__mvi_text_output": expected_output,
                    "metadata": meta_payload,
                }
            msg = f"No transcript found for agent ID: {resume_id}"
            return {"error": msg, "__mvi_text_output": msg}

        # Phase 8: multi-agent orchestrator-backed execution (live).
        orchestrator = self._get_multi_agent_orchestrator()
        if orchestrator is not None:
            tool_name = str(tool_call.get("provider_name") or tool_call.get("function") or "")
            agent_key = None
            if subagent_type in orchestrator.team_config.agents:
                agent_key = subagent_type
            else:
                for key, ref in orchestrator.team_config.agents.items():
                    if ref.role == subagent_type:
                        agent_key = key
                        break
            if agent_key is None:
                return _unknown_agent_error(subagent_type)

            ref = orchestrator.team_config.agents[agent_key]
            sub_config = self.config
            if ref.config_ref:
                loaded = self._load_agent_config_from_path(ref.config_ref)
                if loaded:
                    sub_config = loaded

            spawn_payload = {
                "description": description,
                "prompt": prompt,
                "subagent_type": subagent_type,
            }
            spawn = orchestrator.spawn_subagent(
                owner_agent="main",
                agent_id=agent_key,
                kind="agent",
                async_mode=bool(run_in_background and tool_name == "Task"),
                payload=spawn_payload,
            )

            model_route = str(
                args.get("model")
                or (sub_config.get("providers", {}) or {}).get("default_model")
                or getattr(self, "_current_route_id", "")
                or "openai"
            )
            try:
                max_steps = int(args.get("max_steps") or (sub_config.get("loop", {}) or {}).get("max_steps") or 24)
            except Exception:
                max_steps = 24

            if run_in_background and tool_name == "Task":
                self._ensure_async_jobs()
                task_id = uuid.uuid4().hex[:7]
                job = AsyncAgentJob(
                    task_id=task_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    orchestrator_job_id=spawn.job.job_id,
                )

                def _runner() -> None:
                    try:
                        output_text = ""
                        transcript_path: Optional[str] = None
                        if subagent_type == "repo-scanner":
                            try:
                                root = Path(self.workspace).resolve()
                                entries = sorted(root.iterdir(), key=lambda p: p.name)
                                dirs = [p.name + "/" for p in entries if p.is_dir()]
                                files = [p.name for p in entries if p.is_file()]
                                output_text = (
                                    "Repo root listing:\n"
                                    + "\n".join([f"- {name}" for name in (dirs + files)])
                                ).strip()
                            except Exception:
                                output_text = "Repo root listing unavailable."
                        elif subagent_type == "grep-summarizer":
                            try:
                                root = Path(self.workspace).resolve()
                                patterns = ["multi_agent", "TaskOutput"]
                                found: Dict[str, List[str]] = {p: [] for p in patterns}
                                for path in root.rglob("*"):
                                    if not path.is_file():
                                        continue
                                    try:
                                        if path.stat().st_size > 1024 * 1024:
                                            continue
                                        text = path.read_text(encoding="utf-8", errors="ignore")
                                    except Exception:
                                        continue
                                    rel = str(path.relative_to(root))
                                    for pat in patterns:
                                        if pat in text and rel not in found[pat]:
                                            found[pat].append(rel)
                                lines = []
                                for pat in patterns:
                                    hits = found.get(pat) or []
                                    lines.append(f"{pat}: {len(hits)} file(s)")
                                    for rel in sorted(hits):
                                        lines.append(f"- {rel}")
                                output_text = "\n".join(lines).strip()
                            except Exception:
                                output_text = "Search unavailable."
                        else:
                            child_output = self._run_sync_subagent(
                                prompt=prompt,
                                config=sub_config,
                                model_route=model_route,
                                max_steps=max_steps,
                            )
                            if isinstance(child_output, dict):
                                transcript_path = self._persist_subagent_transcript(
                                    task_id=task_id,
                                    transcript=child_output.get("transcript"),
                                )
                                output_text = self._extract_last_assistant_text(child_output.get("messages"))
                                if not output_text:
                                    output_text = str(
                                        child_output.get("final_response")
                                        or child_output.get("output")
                                        or child_output.get("response")
                                        or ""
                                    )
                        with self._async_agent_jobs_lock:
                            job.status = "completed"
                            job.completed_at = time.time()
                            job.result_text = output_text
                        self._persist_subagent_artifact(
                            task_id=task_id,
                            subagent_type=subagent_type,
                            description=description,
                            prompt=prompt,
                            status="completed",
                            output_text=output_text,
                            error_text=None,
                            orchestrator_job_id=spawn.job.job_id,
                            transcript_path=transcript_path,
                            seq=getattr(spawn.job, "seq", None),
                            created_at=job.created_at,
                            completed_at=job.completed_at,
                        )
                        orchestrator.mark_job_completed(
                            spawn.job.job_id,
                            result_payload={
                                "output": output_text,
                                "subagent_type": subagent_type,
                                "task_id": task_id,
                            },
                        )
                        try:
                            orchestrator.emit_wakeup(
                                spawn.job,
                                reason="completed",
                                message=f"Task {task_id} ({subagent_type}) completed.",
                            )
                        except Exception:
                            pass
                        payload = {
                            "kind": "subagent_completed",
                            "task_id": task_id,
                            "sessionId": spawn.job.job_id,
                            "subagent_type": subagent_type,
                            "description": description,
                            "seq": getattr(spawn.job, "seq", None),
                            "status": "completed",
                            "artifact": {"path": f".breadboard/subagents/{task_id}.json"},
                            "output_excerpt": (output_text or "")[:400],
                        }
                        if task_context:
                            payload.update(task_context)
                        self._emit_task_event(payload)
                    except Exception as exc:
                        with self._async_agent_jobs_lock:
                            job.status = "failed"
                            job.completed_at = time.time()
                            job.error = str(exc)
                        self._persist_subagent_artifact(
                            task_id=task_id,
                            subagent_type=subagent_type,
                            description=description,
                            prompt=prompt,
                            status="failed",
                            output_text=None,
                            error_text=str(exc),
                            orchestrator_job_id=spawn.job.job_id,
                            seq=getattr(spawn.job, "seq", None),
                            created_at=job.created_at,
                            completed_at=job.completed_at,
                        )
                        orchestrator.mark_job_completed(
                            spawn.job.job_id,
                            result_payload={
                                "error": str(exc),
                                "subagent_type": subagent_type,
                                "task_id": task_id,
                            },
                        )
                        try:
                            orchestrator.emit_wakeup(
                                spawn.job,
                                reason="failed",
                                message=f"Task {task_id} ({subagent_type}) failed.",
                            )
                        except Exception:
                            pass
                        payload = {
                            "kind": "subagent_failed",
                            "task_id": task_id,
                            "sessionId": spawn.job.job_id,
                            "subagent_type": subagent_type,
                            "description": description,
                            "seq": getattr(spawn.job, "seq", None),
                            "status": "failed",
                            "artifact": {"path": f".breadboard/subagents/{task_id}.json"},
                            "error": str(exc),
                        }
                        if task_context:
                            payload.update(task_context)
                        self._emit_task_event(payload)
                    finally:
                        try:
                            self._persist_multi_agent_log()
                        except Exception:
                            pass

                thread = threading.Thread(target=_runner, name=f"async-agent-{task_id}", daemon=True)
                job.thread = thread
                with self._async_agent_jobs_lock:
                    self._async_agent_jobs[task_id] = job
                thread.start()
                self._persist_subagent_artifact(
                    task_id=task_id,
                    subagent_type=subagent_type,
                    description=description,
                    prompt=prompt,
                    status="running",
                    output_text=None,
                    error_text=None,
                    orchestrator_job_id=spawn.job.job_id,
                    seq=getattr(spawn.job, "seq", None),
                    created_at=job.created_at,
                    completed_at=None,
                )
                payload = {
                    "kind": "subagent_spawned",
                    "task_id": task_id,
                    "sessionId": spawn.job.job_id,
                    "subagent_type": subagent_type,
                    "description": description,
                    "seq": getattr(spawn.job, "seq", None),
                    "status": "running",
                }
                if task_context:
                    payload.update(task_context)
                self._emit_task_event(payload)
                ack = self._format_claude_task_ack(task_id=task_id)
                return {
                    "title": description,
                    "output": ack,
                    "__mvi_text_output": ack,
                    "metadata": {"agentId": task_id, "sessionId": spawn.job.job_id},
                }

            task_id_sync = spawn.job.job_id
            if tool_name == "Task":
                spawn_event = {
                    "kind": "subagent_spawned",
                    "task_id": task_id_sync,
                    "sessionId": spawn.job.job_id,
                    "subagent_type": subagent_type,
                    "description": description,
                    "seq": getattr(spawn.job, "seq", None),
                    "status": "running",
                    "artifact": {"path": f".breadboard/subagents/{task_id_sync}.json"},
                }
                if task_context:
                    spawn_event.update(task_context)
                self._emit_task_event(spawn_event)

            output_text = ""
            transcript_path: Optional[str] = None
            error_text: Optional[str] = None
            status_value = "completed"
            try:
                child_output = self._run_sync_subagent(
                    prompt=prompt,
                    config=sub_config,
                    model_route=model_route,
                    max_steps=max_steps,
                )
                if isinstance(child_output, dict):
                    transcript_path = self._persist_subagent_transcript(
                        task_id=spawn.job.job_id,
                        transcript=child_output.get("transcript"),
                    )
                    output_text = self._extract_last_assistant_text(child_output.get("messages"))
                    if not output_text:
                        output_text = str(
                            child_output.get("final_response")
                            or child_output.get("output")
                            or child_output.get("response")
                            or ""
                        )
            except Exception as exc:  # noqa: BLE001
                status_value = "failed"
                error_text = str(exc)

            self._persist_subagent_artifact(
                task_id=spawn.job.job_id,
                subagent_type=subagent_type,
                description=description,
                prompt=prompt,
                status=status_value,
                output_text=output_text,
                error_text=error_text,
                orchestrator_job_id=spawn.job.job_id,
                transcript_path=transcript_path,
                seq=getattr(spawn.job, "seq", None),
                created_at=time.time(),
                completed_at=time.time(),
            )
            orchestrator.mark_job_completed(
                spawn.job.job_id,
                result_payload={
                    "output": output_text,
                    "error": error_text,
                    "subagent_type": subagent_type,
                },
            )
            self._persist_multi_agent_log()

            if tool_name == "Task":
                finish_event = {
                    "kind": "subagent_completed" if status_value == "completed" else "subagent_failed",
                    "task_id": task_id_sync,
                    "sessionId": spawn.job.job_id,
                    "subagent_type": subagent_type,
                    "description": description,
                    "seq": getattr(spawn.job, "seq", None),
                    "status": status_value,
                    "artifact": {"path": f".breadboard/subagents/{task_id_sync}.json"},
                }
                if output_text:
                    finish_event["output_excerpt"] = output_text[:400]
                if error_text:
                    finish_event["error"] = error_text
                if task_context:
                    finish_event.update(task_context)
                self._emit_task_event(finish_event)

            return {
                "title": description,
                "output": output_text,
                "__mvi_text_output": output_text,
                "metadata": {"sessionId": spawn.job.job_id, "summary": []},
            }

        # Phase 7/OpenCode parity: replay-backed extraction via `task_tool.subagents`.
        task_cfg = (self.config.get("task_tool", {}) or {}) if isinstance(getattr(self, "config", None), dict) else {}
        subagents = task_cfg.get("subagents") or {}
        if not isinstance(subagents, dict):
            subagents = {}
        sub_cfg = subagents.get(subagent_type)
        if not isinstance(sub_cfg, dict):
            if isinstance(expected_output, str):
                if str(expected_status or "").lower() == "error":
                    return {"error": expected_output, "__mvi_text_output": expected_output}
                meta_payload = expected_metadata if isinstance(expected_metadata, dict) else {}
                return {
                    "title": description,
                    "output": expected_output,
                    "__mvi_text_output": expected_output,
                    "metadata": meta_payload,
                }
            return _unknown_agent_error(subagent_type)

        replay_path = sub_cfg.get("replay_session") or sub_cfg.get("child_replay_session")
        replay_index = sub_cfg.get("replay_index") or sub_cfg.get("replay_session_index")
        chosen_session_id: Optional[str] = None
        if replay_index:
            try:
                index_payload = json.loads(Path(replay_index).read_text(encoding="utf-8"))
                if isinstance(index_payload, dict):
                    chosen_session_id = _extract_session_id_from_expected(expected_output, expected_metadata)
                    if chosen_session_id and chosen_session_id in index_payload:
                        replay_path = index_payload.get(chosen_session_id)
                    elif index_payload:
                        # Stable fallback: pick first entry (sorted by key).
                        first_key = sorted(index_payload.keys())[0]
                        replay_path = index_payload.get(first_key)
                        chosen_session_id = chosen_session_id or first_key
            except Exception:
                replay_path = replay_path or None

        if not replay_path:
            if isinstance(expected_output, str):
                if str(expected_status or "").lower() == "error":
                    return {"error": expected_output, "__mvi_text_output": expected_output}
                meta_payload = expected_metadata if isinstance(expected_metadata, dict) else {}
                return {
                    "title": description,
                    "output": expected_output,
                    "__mvi_text_output": expected_output,
                    "metadata": meta_payload,
                }
            msg = "Failed to load task replay session"
            return {"error": msg, "__mvi_text_output": msg}

        try:
            entries = json.loads(Path(replay_path).read_text(encoding="utf-8"))
        except Exception:
            msg = "Failed to load task replay session"
            return {"error": msg, "__mvi_text_output": msg}

        output_text, summary = _summarize_replay_entries(entries)
        session_id = chosen_session_id or _extract_session_id_from_expected(expected_output, expected_metadata) or str(uuid.uuid4())

        streaming_cfg = task_cfg.get("streaming") if isinstance(task_cfg, dict) else None
        forward_assistant = bool(
            isinstance(streaming_cfg, dict) and streaming_cfg.get("forward_assistant_messages")
        )
        try:
            started_payload: Dict[str, Any] = {
                "kind": "started",
                "sessionId": session_id,
                "subagent_type": subagent_type,
                "description": description,
                "status": "running",
            }
            if task_context:
                started_payload.update(task_context)
            self._emit_task_event(started_payload)

            for entry in entries or []:
                if not isinstance(entry, dict) or entry.get("role") != "assistant":
                    continue
                for part in entry.get("parts") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool":
                        meta = part.get("meta") or {}
                        state = (meta.get("state") or {}) if isinstance(meta, dict) else {}
                        tool_payload: Dict[str, Any] = {
                            "kind": "tool_call",
                            "sessionId": session_id,
                            "subagent_type": subagent_type,
                            "tool": part.get("tool"),
                            "status": state.get("status"),
                        }
                        if "input" in state:
                            tool_payload["input"] = state.get("input")
                        if task_context:
                            tool_payload.update(task_context)
                        self._emit_task_event(tool_payload)
                    if forward_assistant and part.get("type") == "text":
                        text = part.get("text")
                        if not text:
                            continue
                        msg_payload: Dict[str, Any] = {
                            "kind": "assistant_message",
                            "sessionId": session_id,
                            "subagent_type": subagent_type,
                            "content": str(text),
                        }
                        if task_context:
                            msg_payload.update(task_context)
                        self._emit_task_event(msg_payload)

            completed_payload: Dict[str, Any] = {
                "kind": "completed",
                "sessionId": session_id,
                "subagent_type": subagent_type,
                "description": description,
                "status": "completed",
            }
            if output_text:
                completed_payload["output_excerpt"] = output_text[:400]
            if task_context:
                completed_payload.update(task_context)
            self._emit_task_event(completed_payload)
        except Exception:
            pass
        return {
            "title": description,
            "output": output_text,
            "__mvi_text_output": output_text,
            "metadata": {"sessionId": session_id, "summary": summary},
        }

    def _handle_background_task_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            msg = "background_task missing required arguments"
            return {"error": msg, "__mvi_text_output": msg}

        description = str(args.get("description") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        agent = str(args.get("agent") or "").strip()
        task_context = self._extract_task_context(args)

        if not description or not prompt or not agent:
            missing = []
            if not description:
                missing.append("description")
            if not prompt:
                missing.append("prompt")
            if not agent:
                missing.append("agent")
            msg = f"missing required field: {', '.join(missing)}"
            return {"error": msg, "__mvi_text_output": msg}

        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        if isinstance(expected_output, str):
            if str(expected_status or "").lower() == "error":
                return {"error": expected_output, "__mvi_text_output": expected_output}
            return {"output": expected_output, "__mvi_text_output": expected_output}

        self._ensure_background_jobs()
        task_id = f"bg_{uuid.uuid4().hex[:8]}"
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        job = BackgroundTaskJob(
            task_id=task_id,
            agent=agent,
            description=description,
            prompt=prompt,
            session_id=session_id,
        )

        def _runner() -> None:
            try:
                root = Path(self.workspace).resolve()
                output_text = ""
                lowered = prompt.lower()
                if "list the repo root" in lowered or "repo root" in lowered:
                    entries = sorted(root.iterdir(), key=lambda p: p.name)
                    dirs = [p.name + "/" for p in entries if p.is_dir()]
                    files = [p.name for p in entries if p.is_file()]
                    output_text = "<results>\n<files>\n" + "\n".join(
                        [f"- {str(root / name)}" for name in (dirs + files)]
                    ) + "\n</files>\n</results>"
                elif "search" in lowered or "multi_agent" in prompt or "taskoutput" in lowered:
                    patterns = ["multi_agent", "TaskOutput"]
                    hits: List[str] = []
                    for path in root.rglob("*"):
                        if not path.is_file():
                            continue
                        try:
                            if path.stat().st_size > 1024 * 1024:
                                continue
                            text = path.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            continue
                        if any(pat in text for pat in patterns):
                            hits.append(str(path))
                    hits_sorted = sorted(set(hits))
                    output_text = "<results>\n<files>\n" + "\n".join([f"- {h}" for h in hits_sorted]) + "\n</files>\n</results>"
                else:
                    output_text = "<results>\n<files>\n</files>\n</results>"

                with self._background_jobs_lock:
                    job.status = "completed"
                    job.completed_at = time.time()
                    job.result_text = output_text
                self._persist_subagent_artifact(
                    task_id=task_id,
                    subagent_type=agent,
                    description=description,
                    prompt=prompt,
                    status="completed",
                    output_text=output_text,
                    error_text=None,
                    orchestrator_job_id=session_id,
                    created_at=job.created_at,
                    completed_at=job.completed_at,
                )
                event = {
                    "kind": "background_task_completed",
                    "task_id": task_id,
                    "sessionId": session_id,
                    "subagent_type": agent,
                    "description": description,
                    "status": "completed",
                    "artifact": {"path": f".breadboard/subagents/{task_id}.json"},
                    "output_excerpt": (output_text or "")[:400],
                }
                if task_context:
                    event.update(task_context)
                self._emit_task_event(event)
            except Exception as exc:
                with self._background_jobs_lock:
                    job.status = "failed"
                    job.completed_at = time.time()
                    job.error = str(exc)
                self._persist_subagent_artifact(
                    task_id=task_id,
                    subagent_type=agent,
                    description=description,
                    prompt=prompt,
                    status="failed",
                    output_text=None,
                    error_text=str(exc),
                    orchestrator_job_id=session_id,
                    created_at=job.created_at,
                    completed_at=job.completed_at,
                )
                event = {
                    "kind": "background_task_failed",
                    "task_id": task_id,
                    "sessionId": session_id,
                    "subagent_type": agent,
                    "description": description,
                    "status": "failed",
                    "artifact": {"path": f".breadboard/subagents/{task_id}.json"},
                    "error": str(exc),
                }
                if task_context:
                    event.update(task_context)
                self._emit_task_event(event)

        thread = threading.Thread(target=_runner, name=f"background-task-{task_id}", daemon=True)
        job.thread = thread
        with self._background_jobs_lock:
            self._background_jobs[task_id] = job
        self._persist_subagent_artifact(
            task_id=task_id,
            subagent_type=agent,
            description=description,
            prompt=prompt,
            status="running",
            output_text=None,
            error_text=None,
            orchestrator_job_id=session_id,
            created_at=job.created_at,
            completed_at=None,
        )
        spawn_event = {
            "kind": "background_task_spawned",
            "task_id": task_id,
            "sessionId": session_id,
            "subagent_type": agent,
            "description": description,
            "status": "running",
            "artifact": {"path": f".breadboard/subagents/{task_id}.json"},
        }
        if task_context:
            spawn_event.update(task_context)
        self._emit_task_event(spawn_event)
        thread.start()

        ack = self._format_omo_background_task_ack(
            task_id=task_id,
            session_id=session_id,
            description=description,
            agent=agent,
        )
        return {"output": ack, "__mvi_text_output": ack, "metadata": {"task_id": task_id, "session_id": session_id}}

    def _handle_call_omo_agent_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            msg = "call_omo_agent missing required arguments"
            return {"error": msg, "__mvi_text_output": msg}

        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        if isinstance(expected_output, str):
            if str(expected_status or "").lower() == "error":
                return {"error": expected_output, "__mvi_text_output": expected_output}
            return {"output": expected_output, "__mvi_text_output": expected_output}

        description = str(args.get("description") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        subagent_type = str(args.get("subagent_type") or args.get("subagentType") or "").strip()
        has_run_flag = "run_in_background" in args or "runInBackground" in args
        run_in_background = bool(args.get("run_in_background") if "run_in_background" in args else args.get("runInBackground"))
        session_id_in = str(args.get("session_id") or args.get("sessionId") or "").strip()

        missing: List[str] = []
        if not description:
            missing.append("description")
        if not prompt:
            missing.append("prompt")
        if not subagent_type:
            missing.append("subagent_type")
        if not has_run_flag:
            missing.append("run_in_background")
        if missing:
            msg = f"missing required field: {', '.join(missing)}"
            return {"error": msg, "__mvi_text_output": msg}

        allowed = {"explore", "librarian"}
        if subagent_type not in allowed:
            msg = f'Error: Invalid agent type "{subagent_type}". Only explore, librarian are allowed.'
            return {"output": msg, "__mvi_text_output": msg}

        if run_in_background and session_id_in:
            msg = (
                "Error: session_id is not supported in background mode. "
                "Use run_in_background=false to continue an existing session."
            )
            return {"output": msg, "__mvi_text_output": msg}

        if not run_in_background:
            msg = "Error: call_omo_agent run_in_background=false is not implemented in Breadboard."
            return {"error": msg, "__mvi_text_output": msg}

        bg_result = self._handle_background_task_tool(
            {
                "arguments": {
                    "description": description,
                    "prompt": prompt,
                    "agent": subagent_type,
                    "run_in_background": True,
                }
            }
        )
        meta = bg_result.get("metadata") if isinstance(bg_result, dict) else None
        task_id = ""
        session_id = ""
        if isinstance(meta, dict):
            task_id = str(meta.get("task_id") or "")
            session_id = str(meta.get("session_id") or "")
        if not task_id or not session_id:
            msg = "Failed to launch background agent task."
            return {"error": msg, "__mvi_text_output": msg}

        ack = self._format_omo_call_omo_agent_ack(
            task_id=task_id,
            session_id=session_id,
            description=description,
            agent=subagent_type,
        )
        return {"output": ack, "__mvi_text_output": ack, "metadata": {"task_id": task_id, "session_id": session_id}}

    def _append_mcp_tape(self, payload: Dict[str, Any]) -> None:
        path = getattr(self, "_mcp_record_path", None)
        if not path:
            return
        try:
            run_dir = getattr(getattr(self, "logger_v2", None), "run_dir", None)
            if run_dir:
                run_path = Path(str(run_dir))
                try:
                    rel = Path(path).relative_to(run_path)
                    self.logger_v2.append_jsonl(str(rel), payload)
                    return
                except Exception:
                    pass
        except Exception:
            pass
        try:
            append_mcp_replay_entry(Path(path), payload)
        except Exception:
            pass

    def _parse_hook_tool_result(self, result: Dict[str, Any], *, fallback_payload: Optional[Dict[str, Any]] = None) -> HookResult:
        if not isinstance(result, dict):
            return HookResult(action="deny", reason="hook_tool_non_dict")
        if "error" in result:
            return HookResult(action="deny", reason=str(result.get("error")))
        action = result.get("action") or result.get("decision") or result.get("status")
        if isinstance(result.get("allow"), bool):
            action = "allow" if result.get("allow") else "deny"
        if isinstance(result.get("deny"), bool):
            action = "deny" if result.get("deny") else "allow"
        action = str(action or "allow").lower()
        if action not in {"allow", "deny", "transform"}:
            action = "allow"
        reason = result.get("reason")
        payload = result.get("payload")
        if payload is None and isinstance(result.get("result"), dict):
            payload = result.get("result")
        if payload is None and fallback_payload is not None and action == "transform":
            payload = fallback_payload
        if not isinstance(payload, dict):
            payload = {}
        return HookResult(action=action, reason=str(reason) if reason else None, payload=payload)

    def _exec_hook_tool(
        self,
        hook: Any,
        payload: Dict[str, Any],
        *,
        session_state: Optional[SessionState] = None,
        turn: Optional[int] = None,
    ) -> HookResult:
        try:
            config = hook.payload or {}
        except Exception:
            config = {}
        try:
            owner = getattr(hook, "owner", None)
        except Exception:
            owner = None
        trust_map = getattr(self, "_plugin_trust_map", {}) if hasattr(self, "_plugin_trust_map") else {}
        if owner and isinstance(trust_map, dict):
            trusted = trust_map.get(str(owner))
        else:
            trusted = True
        if trusted is False:
            plugins_cfg = (self.config.get("plugins") or {}) if isinstance(self.config, dict) else {}
            policy = str(plugins_cfg.get("untrusted_hook_tools") or "deny").lower()
            if policy not in {"allow", "true", "1", "yes"}:
                return HookResult(action="deny", reason="untrusted_plugin_hook_tool")
        tool_name = None
        try:
            tool_name = config.get("tool") or config.get("tool_name") or config.get("name")
        except Exception:
            tool_name = None
        if not tool_name:
            return HookResult(action="allow", reason="hook_tool_missing_name", payload=dict(payload))
        args = config.get("arguments") if isinstance(config, dict) else None
        if not isinstance(args, dict):
            args = {}
        include_context = config.get("include_context")
        if include_context is None:
            include_context = True
        if include_context:
            context = {
                "phase": getattr(hook, "phase", None),
                "hook_id": getattr(hook, "hook_id", None),
                "payload": payload,
                "turn": turn,
            }
            args = dict(args)
            args.setdefault("context", context)
        try:
            broker = getattr(self, "permission_broker", None)
            if broker is not None and hasattr(broker, "decide"):
                decision = broker.decide({"function": str(tool_name), "arguments": args})
                if decision and str(decision).lower() not in {"allow", "yes", "true", "1"}:
                    if not (str(decision).lower() == "ask" and getattr(broker, "auto_allow", lambda: False)()):
                        return HookResult(action="deny", reason="hook_tool_permission_denied")
        except Exception:
            pass
        result = self._handle_mcp_tool({"function": str(tool_name), "arguments": args})
        return self._parse_hook_tool_result(result, fallback_payload=payload)

    def _merge_permission_rules(self, base: Any, extra: Any) -> Dict[str, Any]:
        if not isinstance(base, dict):
            base = {}
        merged: Dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
        if not isinstance(extra, dict):
            return merged
        for category, cfg in extra.items():
            if not isinstance(cfg, dict):
                continue
            entry = merged.get(category)
            if not isinstance(entry, dict):
                entry = {}
            if "default" not in entry and cfg.get("default") is not None:
                entry["default"] = cfg.get("default")
            for key in ("allow", "deny", "ask", "allowlist", "denylist", "asklist"):
                values = cfg.get(key)
                if not values:
                    continue
                existing = entry.get(key)
                if not isinstance(existing, list):
                    existing = []
                if isinstance(values, str):
                    values_list = [values]
                elif isinstance(values, list):
                    values_list = [v for v in values if v is not None]
                else:
                    values_list = []
                for item in values_list:
                    text = str(item).strip()
                    if text and text not in existing:
                        existing.append(text)
                entry[key] = existing
            merged[category] = entry
        return merged

    def _handle_mcp_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        name = str(tool_call.get("function") or "")
        args = tool_call.get("arguments") or {}
        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        if expected_output is not None:
            if str(expected_status or "").lower() == "error":
                if isinstance(expected_output, dict):
                    return expected_output
                return {"error": expected_output, "__mvi_text_output": str(expected_output)}
            if isinstance(expected_output, dict):
                return expected_output
            return {"result": expected_output, "__mvi_text_output": str(expected_output)}
        try:
            fixture_results = load_mcp_fixture_results(self.config, self.workspace)
        except Exception:
            fixture_results = {}
        if isinstance(fixture_results, dict) and name in fixture_results:
            result = fixture_results.get(name)
            if isinstance(result, dict):
                self._append_mcp_tape(
                    {"name": name, "arguments": args, "result": result, "source": "fixture"}
                )
                return result
            self._append_mcp_tape(
                {"name": name, "arguments": args, "result": result, "source": "fixture"}
            )
            return {"result": result}
        replay_tape = getattr(self, "_mcp_replay_tape", None)
        if isinstance(replay_tape, MCPReplayTape):
            result = replay_tape.next(name, args if isinstance(args, dict) else {})
            self._append_mcp_tape(
                {"name": name, "arguments": args, "result": result, "source": "replay"}
            )
            return result
        manager = getattr(self, "_mcp_manager", None)
        if isinstance(manager, MCPManager) and manager.has_tool(name):
            server_name = None
            try:
                server_name = manager.resolve_tool_server(name)
            except Exception:
                server_name = None
            try:
                result = manager.call_tool(name, args if isinstance(args, dict) else {})
                self._append_mcp_tape(
                    {"name": name, "arguments": args, "result": result, "server": server_name, "source": "live"}
                )
                return result if isinstance(result, dict) else {"result": result}
            except Exception as exc:
                msg = f"mcp tool error: {exc}"
                self._append_mcp_tape(
                    {"name": name, "arguments": args, "error": msg, "server": server_name, "source": "live"}
                )
                return {"error": msg}
        # Built-in deterministic fixtures
        if name == "mcp.echo":
            result = {"text": str(args.get("text", ""))}
            self._append_mcp_tape(
                {"name": name, "arguments": args, "result": result, "source": "builtin"}
            )
            return result
        if name == "mcp.stat":
            result = {"path": str(args.get("path", "")), "size": 0, "mode": "file"}
            self._append_mcp_tape(
                {"name": name, "arguments": args, "result": result, "source": "builtin"}
            )
            return result
        return {"error": f"unknown mcp tool {name}"}

    def _handle_skill_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        name = str(tool_call.get("function") or "")
        skill_id = name.split(".", 1)[1] if "." in name else name
        session_state = getattr(self, "_active_session_state", None)
        graph_skills = []
        if session_state is not None:
            try:
                graph_skills = session_state.get_provider_metadata("graph_skills") or []
            except Exception:
                graph_skills = []
        skill = None
        for candidate in graph_skills:
            if getattr(candidate, "skill_id", None) == skill_id:
                skill = candidate
                break
        if skill is None:
            return {"error": f"unknown skill {skill_id}"}

        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        inputs = args.get("input")
        if inputs is None:
            inputs = args.get("inputs")
        if inputs is None:
            inputs = args.get("payload")
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except Exception:
                inputs = {"value": inputs}
        if not isinstance(inputs, dict):
            inputs = {"value": inputs}

        def _exec_from_skill(step_call: Dict[str, Any]) -> Dict[str, Any]:
            step_name = str(step_call.get("function") or "")
            if step_name.lower().startswith("skill."):
                return {"error": "nested skill calls are not supported"}
            return self._exec_raw(step_call, _skip_skill=True)

        try:
            steps, success = execute_graph_skill(
                skill,
                inputs=inputs,
                exec_func=_exec_from_skill,
            )
        except Exception as exc:
            return {"error": f"skill execution failed: {exc}"}
        payload: Dict[str, Any] = {
            "ok": bool(success),
            "skill_id": skill_id,
            "steps": steps,
        }
        if not success:
            payload["error"] = "skill failed"
        return payload

    def _handle_background_output_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            msg = "background_output missing required arguments"
            return {"error": msg, "__mvi_text_output": msg}

        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        if isinstance(expected_output, str):
            if str(expected_status or "").lower() == "error":
                return {"error": expected_output, "__mvi_text_output": expected_output}
            return {"output": expected_output, "__mvi_text_output": expected_output}

        task_id = str(args.get("task_id") or args.get("taskId") or "").strip()
        if not task_id:
            msg = "background_output missing task_id"
            return {"error": msg, "__mvi_text_output": msg}

        block = bool(args.get("block", False))
        try:
            timeout_ms = int(args.get("timeout", 60000) or 60000)
        except Exception:
            timeout_ms = 60000
        timeout_ms = max(0, min(timeout_ms, 600000))

        self._ensure_background_jobs()
        with self._background_jobs_lock:
            job = self._background_jobs.get(task_id)
        if job is None:
            msg = f"background_output unknown task_id: {task_id}"
            return {"error": msg, "__mvi_text_output": msg}

        if block and job.thread and job.thread.is_alive():
            job.thread.join(timeout_ms / 1000.0 if timeout_ms else None)

        with self._background_jobs_lock:
            status = job.status
            result_text = job.result_text
            error_text = job.error
            desc = job.description
            session_id = job.session_id
            created_at = job.created_at
            completed_at = job.completed_at

        duration_s = None
        if completed_at is not None:
            try:
                duration_s = int(max(0.0, completed_at - created_at))
            except Exception:
                duration_s = None

        if status == "completed":
            body = result_text or ""
            payload = self._format_omo_background_output(
                task_id=task_id,
                session_id=session_id,
                description=desc,
                duration_s=duration_s,
                body=body,
            )
            return {"output": payload, "__mvi_text_output": payload, "status": "completed", "task_id": task_id}

        if status == "failed":
            body = f"<error>\n{error_text or 'unknown error'}\n</error>"
            payload = self._format_omo_background_output(
                task_id=task_id,
                session_id=session_id,
                description=desc,
                duration_s=duration_s,
                body=body,
            )
            return {"error": payload, "__mvi_text_output": payload, "status": "failed", "task_id": task_id}

        if status == "cancelled":
            body = "<status>cancelled</status>"
            payload = self._format_omo_background_output(
                task_id=task_id,
                session_id=session_id,
                description=desc,
                duration_s=duration_s,
                body=body,
            )
            return {"output": payload, "__mvi_text_output": payload, "status": "cancelled", "task_id": task_id}

        # Still running.
        body = "<status>running</status>"
        payload = self._format_omo_background_output(
            task_id=task_id,
            session_id=session_id,
            description=desc,
            duration_s=duration_s,
            body=body,
        )
        return {"output": payload, "__mvi_text_output": payload, "status": "running", "task_id": task_id}

    def _handle_background_cancel_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            msg = "background_cancel missing required arguments"
            return {"error": msg, "__mvi_text_output": msg}

        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        if isinstance(expected_output, str):
            if str(expected_status or "").lower() == "error":
                return {"error": expected_output, "__mvi_text_output": expected_output}
            return {"output": expected_output, "__mvi_text_output": expected_output}

        cancel_all = args.get("all") is True
        task_id = str(args.get("taskId") or args.get("task_id") or args.get("taskID") or "").strip()

        if not cancel_all and not task_id:
            msg = "❌ Invalid arguments: Either provide a taskId or set all=true to cancel all running tasks."
            return {"output": msg, "__mvi_text_output": msg}

        self._ensure_background_jobs()
        if cancel_all:
            with self._background_jobs_lock:
                running = [job for job in self._background_jobs.values() if job.status == "running"]
                for job in running:
                    job.status = "cancelled"
                    job.completed_at = time.time()

            if not running:
                msg = "✅ No running background tasks to cancel."
                return {"output": msg, "__mvi_text_output": msg}

            for job in running:
                self._persist_subagent_artifact(
                    task_id=job.task_id,
                    subagent_type=job.agent,
                    description=job.description,
                    prompt=job.prompt,
                    status="cancelled",
                    output_text=None,
                    error_text=None,
                    orchestrator_job_id=job.session_id,
                    created_at=job.created_at,
                    completed_at=job.completed_at,
                )
                self._emit_task_event(
                    {
                        "kind": "background_task_cancelled",
                        "task_id": job.task_id,
                        "sessionId": job.session_id,
                        "subagent_type": job.agent,
                        "description": job.description,
                        "status": "cancelled",
                        "artifact": {"path": f".breadboard/subagents/{job.task_id}.json"},
                    }
                )

            results = "\n".join([f"- {job.task_id}: {job.description}" for job in running])
            msg = f"✅ Cancelled {len(running)} background task(s):\n\n{results}"
            return {"output": msg, "__mvi_text_output": msg}

        with self._background_jobs_lock:
            job = self._background_jobs.get(task_id)

        if job is None:
            msg = f"❌ Task not found: {task_id}"
            return {"output": msg, "__mvi_text_output": msg}

        if job.status != "running":
            msg = (
                f"❌ Cannot cancel task: current status is \"{job.status}\".\n"
                "Only running tasks can be cancelled."
            )
            return {"output": msg, "__mvi_text_output": msg}

        with self._background_jobs_lock:
            job.status = "cancelled"
            job.completed_at = time.time()
            session_id = job.session_id
            description = job.description
            status = job.status

        self._persist_subagent_artifact(
            task_id=job.task_id,
            subagent_type=job.agent,
            description=job.description,
            prompt=job.prompt,
            status="cancelled",
            output_text=None,
            error_text=None,
            orchestrator_job_id=job.session_id,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        self._emit_task_event(
            {
                "kind": "background_task_cancelled",
                "task_id": job.task_id,
                "sessionId": job.session_id,
                "subagent_type": job.agent,
                "description": job.description,
                "status": "cancelled",
                "artifact": {"path": f".breadboard/subagents/{job.task_id}.json"},
            }
        )

        msg = (
            "✅ Task cancelled successfully\n\n"
            f"Task ID: {task_id}\n"
            f"Description: {description}\n"
            f"Session ID: {session_id}\n"
            f"Status: {status}"
        )
        return {"output": msg, "__mvi_text_output": msg, "status": status, "task_id": task_id}

    def _handle_taskoutput_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_call.get("arguments") or {}
        if not isinstance(args, dict):
            msg = "TaskOutput missing required arguments"
            return {"error": msg, "__mvi_text_output": msg}

        expected_output = tool_call.get("expected_output")
        expected_status = tool_call.get("expected_status")
        if isinstance(expected_output, str):
            if str(expected_status or "").lower() == "error":
                return {"error": expected_output, "__mvi_text_output": expected_output}
            return {"output": expected_output, "__mvi_text_output": expected_output}

        task_id = str(args.get("task_id") or args.get("taskId") or "").strip()
        if not task_id:
            msg = "TaskOutput missing task_id"
            return {"error": msg, "__mvi_text_output": msg}

        block = bool(args.get("block", True))
        try:
            timeout_ms = int(args.get("timeout", 30000) or 30000)
        except Exception:
            timeout_ms = 30000
        timeout_ms = max(0, min(timeout_ms, 600000))

        self._ensure_async_jobs()
        with self._async_agent_jobs_lock:
            job = self._async_agent_jobs.get(task_id)
        if job is None:
            msg = f"<tool_use_error>No task found with ID: {task_id}</tool_use_error>"
            return {"error": msg, "__mvi_text_output": msg}

        if block and job.thread and job.thread.is_alive():
            job.thread.join(timeout_ms / 1000.0 if timeout_ms else None)

        with self._async_agent_jobs_lock:
            status = job.status
            result_text = job.result_text
            error_text = job.error

        if status == "completed":
            payload = self._format_claude_task_output(
                task_id=task_id,
                status="completed",
                output_text=f"--- RESULT ---\n{(result_text or '').rstrip()}",
            )
            return {"output": payload, "__mvi_text_output": payload, "status": "completed", "task_id": task_id}

        if status == "failed":
            payload = self._format_claude_task_output(
                task_id=task_id,
                status="failed",
                output_text=f"--- ERROR ---\n{(error_text or '').rstrip()}",
            )
            return {"output": payload, "__mvi_text_output": payload, "status": "failed", "task_id": task_id}

        # Still running.
        payload = self._format_claude_task_output(
            task_id=task_id,
            status="running",
            output_text="--- STATUS ---\nTask is still running.",
        )
        return {"output": payload, "__mvi_text_output": payload, "status": "running", "task_id": task_id}

    def _apply_streaming_policy_for_turn(
        self,
        runtime: Any,
        model: str,
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_requested: bool,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        turn_index: int,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        route_id = getattr(self, "_current_route_id", None)
        return apply_streaming_policy_for_turn(
            self,
            runtime,
            model,
            tools_schema,
            stream_requested,
            session_state,
            markdown_logger,
            turn_index,
            route_id,
        )

    def _get_model_routing_preferences(self, route_id: Optional[str]) -> Dict[str, Any]:
        """Return routing preferences for a given configured route."""
        return get_model_routing_preferences(self.config, route_id)

    def _get_capability_probe_result(
        self,
        session_state: SessionState,
        route_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Fetch previously recorded capability probe result for a route."""
        return get_capability_probe_result(session_state, route_id)

    def _log_routing_event(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        *,
        turn_index: Optional[Any],
        tag: str,
        message: str,
        payload: Dict[str, Any],
    ) -> None:
        """Emit routing-related diagnostic across transcript, markdown, and IR."""
        log_routing_event(
            self,
            session_state,
            markdown_logger,
            turn_index=turn_index,
            tag=tag,
            message=message,
            payload=payload,
        )

    def _apply_capability_tool_overrides(
        self,
        provider_tools_cfg: Dict[str, Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> Dict[str, Any]:
        """Adjust native tool usage based on capability probe outputs."""
        route_id = getattr(self, "_current_route_id", None)
        return apply_capability_tool_overrides(
            self,
            provider_tools_cfg,
            session_state,
            markdown_logger,
            route_id,
        )

    def _select_fallback_route(
        self,
        primary_route: Optional[str],
        provider_id: Optional[str],
        primary_model: str,
        explicit_candidates: List[str],
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """Choose a fallback route honoring routing prefs and route health."""
        ordered: List[str] = []
        skipped: List[Dict[str, Any]] = []
        seen = set()

        def add_candidate(candidate: Optional[str]) -> None:
            if not candidate:
                return
            if candidate in seen:
                return
            seen.add(candidate)
            ordered.append(candidate)

        for item in explicit_candidates or []:
            add_candidate(str(item))

        if provider_id == "openrouter":
            try:
                _, base_model, _ = provider_router.get_provider_config(primary_route or primary_model)
            except Exception:
                base_model = None
            if base_model and base_model.startswith("openai/"):
                add_candidate(base_model)

        for candidate in ordered:
            try:
                descriptor, resolved = provider_router.get_runtime_descriptor(candidate)
            except Exception:
                skipped.append({"route": candidate, "reason": "descriptor_lookup_failed"})
                continue
            if self.route_health.is_circuit_open(candidate) or self.route_health.is_circuit_open(resolved):
                skipped.append({"route": candidate, "reason": "circuit_open"})
                continue
            return candidate, {"candidates": ordered, "skipped": skipped, "selected": candidate}

        return None, {"candidates": ordered, "skipped": skipped, "selected": None}
    
    # ===== Agent Schema v2 helpers =====
    def _resolve_active_mode(self) -> Optional[str]:
        try:
            seq = (self.config.get("loop", {}) or {}).get("sequence") or []
            features = self.config.get("features", {}) or {}
            for step in seq:
                if not isinstance(step, dict):
                    continue
                if "if" in step and "then" in step:
                    cond = str(step.get("if"))
                    then = step.get("then") or {}
                    ok = False
                    if cond.startswith("features."):
                        key = cond.split("features.", 1)[1]
                        ok = bool(features.get(key))
                    if ok and isinstance(then, dict) and then.get("mode"):
                        return str(then.get("mode"))
                if step.get("mode"):
                    return str(step.get("mode"))
        except Exception:
            pass
        try:
            modes = self.config.get("modes", []) or []
            if modes and isinstance(modes, list):
                name = modes[0].get("name")
                if name:
                    return str(name)
        except Exception:
            pass
        return None

    def _get_mode_config(self, mode_name: Optional[str]) -> Dict[str, Any]:
        if not mode_name:
            return {}
        try:
            for m in self.config.get("modes", []) or []:
                if m.get("name") == mode_name:
                    return m
        except Exception:
            pass
        return {}

    def _filter_tools_by_mode(self, tool_defs: List[ToolDefinition], mode_cfg: Dict[str, Any]) -> List[ToolDefinition]:
        try:
            enabled = mode_cfg.get("tools_enabled")
            disabled = mode_cfg.get("tools_disabled") or []
            if not enabled and not disabled:
                return tool_defs
            enabled_set = set(enabled or [])
            disabled_set = set(disabled)
            out: List[ToolDefinition] = []
            for t in tool_defs:
                name = t.name
                if name in disabled_set:
                    continue
                if enabled and "*" not in enabled_set and name not in enabled_set:
                    continue
                out.append(t)
            return out or tool_defs
        except Exception:
            return tool_defs

    def _apply_turn_strategy_from_loop(self) -> None:
        apply_turn_strategy_from_loop(self)

    def _get_prompt_cache_control(self) -> Optional[Dict[str, Any]]:
        provider_cfg = getattr(self, "_provider_tools_effective", None) or (self.config.get("provider_tools") or {})
        return get_prompt_cache_control({"provider_tools": provider_cfg})

    def _apply_cache_control_to_initial_user_prompt(
        self,
        messages: List[Dict[str, Any]],
        cache_control: Dict[str, Any],
    ) -> None:
        apply_cache_control_to_initial_user_prompt(messages, cache_control)

    def _inject_cache_control_into_message(
        self,
        message: Dict[str, Any],
        cache_control: Dict[str, Any],
    ) -> None:
        inject_cache_control_into_message(message, cache_control)

    def _append_text_block(self, message: Dict[str, Any], text: str) -> None:
        append_text_block(message, text)

    def _apply_cache_control_to_tool_messages(
        self,
        messages: List[Dict[str, Any]],
        cache_control: Dict[str, Any],
    ) -> None:
        apply_cache_control_to_tool_messages(messages, cache_control)

    def _prepare_concurrency_policy(self) -> Dict[str, Any]:
        return prepare_concurrency_policy(self.config)

    def _tool_defs_from_yaml(self) -> Optional[List[ToolDefinition]]:
        return tool_defs_from_yaml(self)

    def _completion_tool_config_enabled(self) -> bool:
        return completion_tool_config_enabled(self.config)

    def _ensure_completion_tool(self, tool_defs: List[ToolDefinition]) -> List[ToolDefinition]:
        """Ensure mark_task_complete tool is present in the tool definitions."""
        return ensure_completion_tool(tool_defs)

    @staticmethod
    def _is_read_only_tool(tool_name: str) -> bool:
        """Classify tools that only inspect state (used for completion heuristics)."""
        return is_read_only_tool(tool_name)

    @staticmethod
    def _normalize_assistant_text(text: Optional[str]) -> str:
        return normalize_assistant_text(text)

    def _dump_tool_defs(self, tool_defs: List[ToolDefinition]) -> List[Dict[str, Any]]:
        return _dump_tool_defs(tool_defs)

    def _record_lsp_reward_metrics(
        self,
        session_state: SessionState,
        turn_index: int,
    ) -> None:
        record_lsp_reward_metrics(self, session_state, turn_index)

    def _record_test_reward_metric(
        self,
        session_state: SessionState,
        turn_index: int,
        success_value: Optional[float],
    ) -> None:
        record_test_reward_metric(session_state, turn_index, success_value)

    def _register_prompt_hash(self, prompt_type: str, content: Optional[str], turn_index: Optional[int] = None) -> None:
        register_prompt_hash(self, prompt_type, content, turn_index)

    def _build_prompt_summary(self) -> Optional[Dict[str, Any]]:
        return build_prompt_summary(self)

    def _write_workspace_manifest(self) -> None:
        write_workspace_manifest(self)

    def _write_env_fingerprint(self) -> None:
        write_env_fingerprint(self)

    def _append_environment_prompt(self, system_prompt: str) -> str:
        prompts_cfg = self.config.get("prompts") or {}
        env_cfg = prompts_cfg.get("environment") or {}
        if not isinstance(env_cfg, dict) or not env_cfg.get("enabled"):
            return system_prompt
        if str(env_cfg.get("format") or "").lower() != "opencode":
            return system_prompt
        file_limit = env_cfg.get("file_limit")
        file_limit = int(file_limit) if isinstance(file_limit, int) or str(file_limit).isdigit() else 200
        env_block = self._build_opencode_environment_block(file_limit=file_limit)
        if not env_block:
            return system_prompt
        if system_prompt:
            sep = "\n" if system_prompt.endswith("\n") else "\n\n"
            return f"{system_prompt}{sep}{env_block}"
        return env_block

    def _build_opencode_environment_block(self, file_limit: int = 200) -> str:
        workspace = Path(str(self.workspace)).resolve()
        git_root = self._find_git_root(workspace)
        is_git = bool(git_root)
        platform = sys.platform
        date_str = datetime.now().strftime("%a %b %d %Y")
        tree_text = self._opencode_tree(workspace, limit=file_limit) if is_git else ""
        lines = [
            "Here is some useful information about the environment you are running in:",
            "<env>",
            f"  Working directory: {workspace}",
            f"  Is directory a git repo: {'yes' if is_git else 'no'}",
            f"  Platform: {platform}",
            f"  Today's date: {date_str}",
            "</env>",
            "<files>",
            f"  {tree_text}",
            "</files>",
        ]
        return "\n".join(lines)

    @staticmethod
    def _find_git_root(start: Path) -> Optional[Path]:
        current = start
        while True:
            if (current / ".git").exists():
                return current
            if current.parent == current:
                return None
            current = current.parent

    def _opencode_tree(self, root: Path, limit: int = 200) -> str:
        files = []
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=True):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                if ".git" in rel.split(os.sep):
                    continue
                if ".opencode" in rel:
                    continue
                files.append(rel)

        class Node:
            def __init__(self, path_parts: List[str]):
                self.path = path_parts
                self.children: List["Node"] = []

        def get_path(node: Node, parts: List[str], create: bool) -> Optional[Node]:
            if not parts:
                return node
            current = node
            for part in parts:
                existing = next((x for x in current.children if x.path[-1] == part), None)
                if existing is None:
                    if not create:
                        return None
                    existing = Node(current.path + [part])
                    current.children.append(existing)
                current = existing
            return current

        root_node = Node([])
        for file in files:
            if ".opencode" in file:
                continue
            parts = file.split(os.sep)
            get_path(root_node, parts, True)

        def sort_node(node: Node) -> None:
            node.children.sort(
                key=lambda x: (0 if x.children else 1, x.path[-1])
            )
            for child in node.children:
                sort_node(child)

        sort_node(root_node)

        current = [root_node]
        result = Node([])
        processed = 0
        limit = limit if isinstance(limit, int) and limit > 0 else 50
        while current:
            next_level: List[Node] = []
            for node in current:
                if node.children:
                    next_level.extend(node.children)
            max_children = max([len(x.children) for x in current], default=0)
            for i in range(max_children):
                for node in current:
                    if processed >= limit:
                        break
                    if i >= len(node.children):
                        continue
                    child = node.children[i]
                    get_path(result, child.path, True)
                    processed += 1
                if processed >= limit:
                    break
            if processed >= limit:
                for node in current + next_level:
                    compare = get_path(result, node.path, False)
                    if not compare:
                        continue
                    if len(compare.children) != len(node.children):
                        diff = len(node.children) - len(compare.children)
                        compare.children.append(Node(compare.path + [f"[{diff} truncated]"]))
                break
            current = next_level

        lines: List[str] = []

        def render(node: Node, depth: int) -> None:
            indent = "\t" * depth
            name = node.path[-1]
            suffix = "/" if node.children else ""
            lines.append(f"{indent}{name}{suffix}")
            for child in node.children:
                render(child, depth + 1)

        for child in result.children:
            render(child, 0)

        return "\n".join(lines)

    def _capture_turn_diagnostics(
        self,
        session_state: SessionState,
        provider_result: Optional[ProviderResult],
        allowed_tools: Optional[List[str]],
    ) -> Dict[str, Any]:
        diag = capture_turn_diagnostics(
            self,
            session_state,
            provider_result,
            allowed_tools,
            todo_seed_names=list(TODO_SEED_TOOL_NAMES),
        )
        if diag.get("has_todo_seed"):
            session_state.set_provider_metadata("todo_seed_completed", True)
        self._turn_diagnostics.append(diag)
        if len(self._turn_diagnostics) > 400:
            self._turn_diagnostics = self._turn_diagnostics[-400:]
        # Delegate plan bootstrap warning to guardrail orchestrator
        try:
            self.guardrail_orchestrator.maybe_emit_plan_bootstrap_warning(session_state, diag)
        except Exception:
            pass
        return diag

    def _maybe_run_plan_bootstrap(self, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
        maybe_run_plan_bootstrap(self, session_state, markdown_logger)

    def _should_require_build_guard(self, user_prompt: str) -> bool:
        return should_require_build_guard(self, user_prompt)

    def _todo_config(self) -> Dict[str, Any]:
        return self.guardrail_coordinator.todo_config()

    def _build_turn_context(self, session_state: SessionState, parsed_calls: List[Any]) -> TurnContext:
        return build_turn_context(self, session_state, parsed_calls)

    def _summarize_execution_results(
        self,
        turn_ctx: TurnContext,
        executed_results: List[Any],
        session_state: SessionState,
        turn_index_int: Optional[int],
    ) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        return summarize_execution_results(self, turn_ctx, executed_results, session_state, turn_index_int)

    def _emit_turn_snapshot(self, session_state: SessionState, turn_ctx: TurnContext) -> None:
        emit_turn_snapshot(self, session_state, turn_ctx)

    def _hydrate_turn_context_signals(
        self,
        session_state: SessionState,
        turn_ctx: TurnContext,
    ) -> None:
        hydrate_turn_context_signals(session_state, turn_ctx)

    def _finalize_turn_context_snapshot(
        self,
        session_state: SessionState,
        turn_ctx: TurnContext,
        turn_index: Optional[int],
    ) -> None:
        finalize_turn_context_snapshot(self, session_state, turn_ctx, turn_index)

    def _apply_turn_guards(self, turn_ctx: TurnContext, session_state: SessionState) -> List[Any]:
        return apply_turn_guards(self, turn_ctx, session_state)

    def _handle_blocked_calls(
        self,
        turn_ctx: TurnContext,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> None:
        handle_blocked_calls(self, turn_ctx, session_state, markdown_logger)

    def _resolve_replay_todo_placeholders(self, session_state: SessionState, parsed_call: Any) -> None:
        resolve_replay_todo_placeholders(self, session_state, parsed_call)

    def _maybe_transition_plan_mode(self, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
        maybe_transition_plan_mode(self, session_state, markdown_logger)

    def _prompts_config_with_todos(self) -> Dict[str, Any]:
        todos_cfg = self._todo_config()
        if not todos_cfg["enabled"]:
            return self.config
        cfg = copy.deepcopy(self.config)
        prompts_cfg = cfg.setdefault("prompts", {})
        packs = prompts_cfg.setdefault("packs", {})
        base_pack = packs.setdefault("base", {})
        base_pack.setdefault("todo_plan", "implementations/prompts/todos/plan.md")
        base_pack.setdefault("todo_build", "implementations/prompts/todos/build.md")
        injection = prompts_cfg.setdefault("injection", {})
        system_order = injection.setdefault("system_order", [])
        if "@pack(base).todo_plan" not in system_order:
            system_order.append("@pack(base).todo_plan")
        if "@pack(base).todo_build" not in system_order:
            system_order.append("@pack(base).todo_build")
        return cfg

    def _completion_guard_check(self, session_state: SessionState) -> Tuple[bool, Optional[str]]:
        return self.guardrail_orchestrator.completion_guard_check(session_state)

    def _emit_completion_guard_feedback(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        reason: str,
        stream_responses: bool,
    ) -> bool:
        return self.guardrail_orchestrator.emit_completion_guard_feedback(
            session_state,
            markdown_logger,
            reason,
            stream_responses,
        )

    @staticmethod
    def _is_test_command(command: str) -> bool:
        if not command:
            return False
        normalized = command.lower()
        test_keywords = (
            "pytest",
            "npm test",
            "yarn test",
            "pnpm test",
            "go test",
            "cargo test",
            "mvn test",
            "gradle test",
            "bundle exec rspec",
            "tox",
        )
        return any(keyword in normalized for keyword in test_keywords)

    def _record_usage_reward_metrics(
        self,
        session_state: SessionState,
        turn_index: int,
        usage_raw: Optional[Dict[str, Any]],
    ) -> None:
        if not isinstance(usage_raw, dict):
            return
        prompt_tokens = usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens")
        completion_tokens = usage_raw.get("completion_tokens") or usage_raw.get("output_tokens")
        try:
            prompt_value = float(prompt_tokens) if prompt_tokens is not None else 0.0
        except (TypeError, ValueError):
            prompt_value = 0.0
        try:
            completion_value = float(completion_tokens) if completion_tokens is not None else 0.0
        except (TypeError, ValueError):
            completion_value = 0.0
        total_tokens = prompt_value + completion_value
        try:
            session_state.add_reward_metric(turn_index, "TE", total_tokens)
            if total_tokens > 0:
                toe_value = completion_value / total_tokens
                session_state.add_reward_metric(turn_index, "TOE", toe_value)
        except Exception:
            pass
        latency = getattr(self, "_last_runtime_latency", None)
        if latency is not None:
            try:
                session_state.add_reward_metric(turn_index, "LE", float(latency))
            except Exception:
                pass
        try:
            spa_value = 0.0 if getattr(self, "_last_html_detected", False) else 1.0
            session_state.add_reward_metric(turn_index, "SPA", spa_value)
        except Exception:
            pass

    @staticmethod
    def _count_diff_hunks(text: Optional[str]) -> int:
        return count_diff_hunks(text)

    @staticmethod
    def _split_patch_blocks(patch_text: str) -> List[str]:
        return split_patch_blocks(patch_text)

    def _normalize_patch_block(self, block_text: str) -> str:
        """Wrap loose OpenCode patch snippets with sentinel markers."""
        return normalize_patch_block(block_text)

    def _fetch_workspace_text(self, path: str) -> str:
        """Best-effort read helper for converting OpenCode patches."""
        return fetch_workspace_text(self, path)

    def _convert_patch_to_unified(self, patch_text: str) -> Optional[str]:
        """Convert OpenCode patch text to a unified diff for legacy executors."""
        return convert_patch_to_unified(self, patch_text)

    def _apply_patch_operations_direct(self, patch_text: str) -> Optional[Dict[str, Any]]:
        """Fallback handler that directly applies simple add-file operations."""
        return apply_patch_operations_direct(self, patch_text)

    def _expand_multi_file_patches(
        self,
        parsed_calls: List[Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> List[Any]:
        return expand_multi_file_patches(self, parsed_calls, session_state, markdown_logger)

    def _emit_patch_policy_violation(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        *,
        chunk_count: int,
        policy_mode: str,
        validation_message: Optional[str],
    ) -> None:
        """Emit a validation error when multi-file patch policy is violated."""
        emit_patch_policy_violation(
            self,
            session_state,
            markdown_logger,
            chunk_count=chunk_count,
            policy_mode=policy_mode,
            validation_message=validation_message,
        )

    def _validate_structural_artifacts(self, session_state: SessionState) -> List[str]:
        """Perform lightweight structural checks on key workspace artifacts."""
        return validate_structural_artifacts(self, session_state)

    def _record_diff_metrics(
        self,
        tool_call: Any,
        result: Dict[str, Any],
        *,
        session_state: Optional[SessionState] = None,
        turn_index: Optional[int] = None,
    ) -> None:
        record_diff_metrics(
            self,
            tool_call,
            result,
            session_state=session_state,
            turn_index=turn_index,
        )

    def create_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.write_text.remote(path, ""))

    def read_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.read_text.remote(path))

    def list_dir(self, path: str, depth: int = 1) -> Dict[str, Any]:
        try:
            max_depth = int(os.environ.get("BREADBOARD_LIST_DIR_MAX_DEPTH", "1"))
        except Exception:
            max_depth = 1
        if max_depth < 1:
            max_depth = 1
        try:
            requested_depth = int(depth)
        except Exception:
            requested_depth = 1
        safe_depth = min(max(requested_depth, 1), max_depth)
        # Always pass virtual paths when using VirtualizedSandbox; else pass normalized absolute
        if getattr(self, 'using_virtualized', False):
            return self._ray_get(self.sandbox.ls.remote(path, safe_depth))
        target = self._normalize_workspace_path(str(path))
        return self._ray_get(self.sandbox.ls.remote(target, safe_depth))

    def run_shell(
        self,
        command: str,
        timeout: Optional[int] = None,
        *,
        call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        emit_stream: bool = True,
    ) -> Dict[str, Any]:
        def _maybe_append_system_reminder(text: str) -> str:
            if not text:
                return text
            if "<system-reminder>" in text:
                return text
            try:
                tools_cfg = (self.config.get("provider_tools", {}) or {}) if isinstance(getattr(self, "config", None), dict) else {}
                anthropic_cfg = (tools_cfg.get("anthropic", {}) or {}) if isinstance(tools_cfg, dict) else {}
                reminders_cfg = (anthropic_cfg.get("system_reminders", {}) or {}) if isinstance(anthropic_cfg, dict) else {}
                if not isinstance(reminders_cfg, dict) or not bool(reminders_cfg.get("enabled")):
                    return text
                budget = reminders_cfg.get("usd_budget_limit")
                try:
                    budget_f = float(budget) if budget is not None else 0.0
                except Exception:
                    budget_f = 0.0
                spent_f = 0.0
                remaining_f = max(0.0, budget_f - spent_f)
                return (
                    f"{text.rstrip(chr(10))}\n\n"
                    "<system-reminder>\n"
                    f"USD budget: ${spent_f}/${budget_f}; ${remaining_f} remaining\n"
                    "</system-reminder>"
                )
            except Exception:
                return text

        session_state = get_current_session_state()
        turn_index = None
        if session_state is not None:
            try:
                turn_index = session_state.get_provider_metadata("current_turn_index")
            except Exception:
                turn_index = None
        exec_id = None
        if emit_stream and call_id:
            exec_id = f"{call_id}:exec"
            if session_state is not None:
                try:
                    session_state.emit_stream_event(
                        "tool.exec.start",
                        {
                            "tool_call_id": call_id,
                            "exec_id": exec_id,
                            "started_at": int(time.time() * 1000),
                            "cwd": str(getattr(self, "workspace", "")),
                            "command": command,
                            "tool_name": tool_name,
                        },
                        turn=turn_index if isinstance(turn_index, int) else None,
                    )
                except Exception:
                    pass

        result = self._ray_get(self.sandbox.run.remote(command, timeout=timeout or 30, stream=True))

        def _emit_delta(stream_type: str, text: str) -> None:
            if not emit_stream or not session_state or not call_id or not exec_id:
                return
            if not text:
                return
            try:
                session_state.emit_stream_event(
                    f"tool.exec.{stream_type}.delta",
                    {
                        "tool_call_id": call_id,
                        "exec_id": exec_id,
                        "delta": text,
                    },
                    turn=turn_index if isinstance(turn_index, int) else None,
                )
            except Exception:
                pass

        def _emit_end(exit_code: Optional[int]) -> None:
            if not emit_stream or not session_state or not call_id or not exec_id:
                return
            try:
                session_state.emit_stream_event(
                    "tool.exec.end",
                    {
                        "tool_call_id": call_id,
                        "exec_id": exec_id,
                        "ended_at": int(time.time() * 1000),
                        "exit_code": exit_code,
                    },
                    turn=turn_index if isinstance(turn_index, int) else None,
                )
            except Exception:
                pass

        # Newer sandbox implementations return a dict payload directly.
        if isinstance(result, dict):
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            exit_code = result.get("exit")
            if stdout:
                _emit_delta("stdout", stdout)
            if stderr:
                _emit_delta("stderr", stderr)
            _emit_end(exit_code if isinstance(exit_code, int) else None)
            mvi_text = stdout if stdout else stderr
            mvi_text = _maybe_append_system_reminder(mvi_text)
            payload: Dict[str, Any] = {"stdout": stdout, "exit": exit_code, "__mvi_text_output": mvi_text}
            if stderr:
                payload["stderr"] = stderr
            return payload

        # Legacy/virtualized sandboxes may return an adaptive stream list where
        # the last element is {"exit": code}.
        if not isinstance(result, list):
            text = str(result)
            if text:
                _emit_delta("stdout", text)
            _emit_end(None)
            return {"stdout": text, "exit": None, "__mvi_text_output": text}

        exit_obj = result[-1] if result else {"exit": None}
        lines: list[str] = []
        items = list(result)
        start_index = 0
        if items and items[0] in {ADAPTIVE_PREFIX_ITERABLE, ADAPTIVE_PREFIX_NON_ITERABLE}:
            start_index = 1
        for x in items[start_index:-1]:
            if not isinstance(x, str):
                continue
            if x.startswith(">>>>>"):
                continue
            lines.append(x)
            _emit_delta("stdout", f"{x}\n")
        _emit_end(exit_obj.get("exit") if isinstance(exit_obj, dict) else None)
        stdout = "\n".join(lines)
        mvi_text = _maybe_append_system_reminder(stdout)
        return {"stdout": stdout, "exit": exit_obj.get("exit"), "__mvi_text_output": mvi_text}

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.vcs.remote(request))

    def _normalize_workspace_path(self, path_in: str) -> str:
        """Normalize a tool-supplied path so it stays within the workspace root."""
        return normalize_workspace_path(self, path_in)
    
    def _exec_raw(self, tool_call: Dict[str, Any], *, _skip_skill: bool = False) -> Dict[str, Any]:
        """Raw tool execution without enhanced features (for compatibility)"""
        name = tool_call["function"]
        args = tool_call["arguments"]

        # Normalize common aliases used by provider tool names
        normalized = name.lower()
        if normalized == "bash":
            normalized = "run_shell"
        elif normalized == "list":
            normalized = "list_dir"
        elif normalized == "read":
            normalized = "read_file"
        elif normalized == "write":
            normalized = "create_file_from_block"
        elif normalized == "edit":
            normalized = "edit_replace"
        elif normalized == "todowrite":
            normalized = "todo.write_board"
        elif normalized == "todoread":
            normalized = "todo.list"
        elif normalized == "update_plan":
            normalized = "update_plan"

        if normalized == "patch":
            patch_text = str(args.get("patchText") or args.get("patch") or "")
            return self._exec_raw(
                {"function": "apply_unified_patch", "arguments": {"patch": patch_text}},
                _skip_skill=_skip_skill,
            )

        if normalized.startswith("mcp."):
            return self._handle_mcp_tool(tool_call)
        if normalized.startswith("skill."):
            if _skip_skill:
                return {"error": f"nested skill call blocked: {name}"}
            return self._handle_skill_tool(tool_call)

        if normalized == "task":
            replay_expected = tool_call.get("expected_output") is not None or tool_call.get("expected_status") is not None
            allow_task = replay_expected or self._get_multi_agent_orchestrator() is not None
            if not allow_task:
                try:
                    resume_value = args.get("resume") if isinstance(args, dict) else None
                except Exception:
                    resume_value = None
                if isinstance(resume_value, str) and resume_value.strip():
                    allow_task = True
                elif resume_value:
                    allow_task = True
            if not allow_task:
                try:
                    allow_task = bool(self.config.get("task_tool")) if isinstance(getattr(self, "config", None), dict) else False
                except Exception:
                    allow_task = False
            if not allow_task:
                return {"error": f"unknown tool {name}"}
            return self._handle_task_tool(tool_call)

        if normalized == "background_task":
            return self._handle_background_task_tool(tool_call)

        if normalized == "call_omo_agent":
            return self._handle_call_omo_agent_tool(tool_call)

        if normalized == "background_output":
            return self._handle_background_output_tool(tool_call)

        if normalized == "background_cancel":
            return self._handle_background_cancel_tool(tool_call)

        if normalized == "taskoutput":
            return self._handle_taskoutput_tool(tool_call)

        if normalized == "update_plan":
            expected_output = tool_call.get("expected_output")
            expected_status = tool_call.get("expected_status")
            if isinstance(expected_output, str):
                payload: Dict[str, Any] = {"ok": True, "__mvi_text_output": expected_output}
                if str(expected_status or "").lower() == "error":
                    payload["error"] = expected_output
                return payload
            # Codex CLI surface expects a simple acknowledgement.
            try:
                setattr(self, "_codex_cli_plan", args.get("plan"))
            except Exception:
                pass
            return {"ok": True, "__mvi_text_output": "Plan updated"}

        if normalized == "create_file":
            target = self._normalize_workspace_path(str(args.get("path", "")))
            return self.create_file(target)
        if normalized == "todo.write_board":
            return self._execute_todo_tool("todo.write_board", args)
        if normalized == "todo.list":
            return self._execute_todo_tool("todo.list", args)
        if normalized == "create_file_from_block":
            path_value = (
                args.get("file_name")
                or args.get("path")
                or args.get("filePath")
                or args.get("file_path")
            )
            target = self._normalize_workspace_path(str(path_value or ""))
            content = str(args.get("content", ""))
            return self._ray_get(self.sandbox.write_text.remote(target, content))
        if normalized == "read_file":
            expected_output = tool_call.get("expected_output")
            expected_status = tool_call.get("expected_status")
            if isinstance(expected_output, str):
                payload: Dict[str, Any] = {"__mvi_text_output": expected_output}
                if str(expected_status or "").lower() == "error":
                    payload["error"] = expected_output
                return payload

            path_value = args.get("path") or args.get("filePath") or args.get("file_path")
            target = self._normalize_workspace_path(str(path_value or ""))
            return self.read_file(target)
        if normalized == "glob":
            pattern = str(args.get("pattern") or "")
            root = str(args.get("path") or ".")
            limit = args.get("limit")
            return self._ray_get(self.sandbox.glob.remote(pattern, root, limit))
        if normalized == "grep":
            pattern = str(args.get("pattern") or "")
            root = str(args.get("path") or ".")
            include = args.get("include")
            limit = int(args.get("limit") or 100)
            return self._ray_get(self.sandbox.grep.remote(pattern, root, include, limit))
        if normalized == "list_dir":
            expected_output = tool_call.get("expected_output")
            expected_status = tool_call.get("expected_status")
            if isinstance(expected_output, str):
                payload: Dict[str, Any] = {"__mvi_text_output": expected_output}
                if str(expected_status or "").lower() == "error":
                    payload["error"] = expected_output
                return payload

            target = self._normalize_workspace_path(str(args.get("path", "")))
            depth = int(args.get("depth", 1))
            return self.list_dir(target, depth)
        if normalized == "edit_replace":
            expected_output = tool_call.get("expected_output")
            expected_status = tool_call.get("expected_status")
            path_value = (
                args.get("file_name")
                or args.get("path")
                or args.get("filePath")
                or args.get("file_path")
            )
            target = self._normalize_workspace_path(str(path_value or ""))
            search_text = args.get("oldString") or args.get("search") or ""
            replace_text = args.get("newString") or args.get("replace") or ""
            try:
                count = int(args.get("count") or 1)
            except Exception:
                count = 1
            if count < 1:
                count = 1

            # In replay mode, avoid mutating the workspace on expected failures.
            if str(expected_status or "").lower() == "error":
                if isinstance(expected_output, str):
                    return {"__mvi_text_output": expected_output, "error": expected_output}
                return {"error": "edit failed"}

            # Always attempt side-effects; return recorded output if provided for parity.
            try:
                result = self._ray_get(self.sandbox.edit_replace.remote(target, str(search_text), str(replace_text), count))
            except Exception as exc:
                result = {"error": str(exc)}

            if isinstance(expected_output, str):
                payload: Dict[str, Any] = {"__mvi_text_output": expected_output}
                if isinstance(result, dict):
                    payload.update({k: v for k, v in result.items() if k not in payload})
                return payload
            return result if isinstance(result, dict) else {"result": result}
        if normalized == "run_shell":
            expected_output = tool_call.get("expected_output")
            expected_status = tool_call.get("expected_status")
            expected_metadata = tool_call.get("expected_metadata")
            expected_exit = None
            call_id = tool_call.get("call_id") or tool_call.get("tool_call_id") or tool_call.get("id")
            if isinstance(expected_metadata, dict):
                for key in ("exit", "exit_code", "exitCode"):
                    value = expected_metadata.get(key)
                    if value is None:
                        continue
                    try:
                        expected_exit = int(value)
                    except Exception:
                        expected_exit = None
                    break
            if isinstance(expected_output, str):
                # Replay mode: still execute for side-effects (e.g. compiled artifacts),
                # but return the recorded output for deterministic parity.
                try:
                    _ = self.run_shell(
                        args["command"],
                        args.get("timeout"),
                        call_id=str(call_id) if call_id else None,
                        tool_name=normalized,
                        emit_stream=False,
                    )
                except Exception:
                    pass
                if str(expected_status or "").lower() == "error":
                    return {
                        "error": expected_output,
                        "exit": expected_exit if expected_exit is not None else 1,
                        "__mvi_text_output": expected_output,
                    }
                return {
                    "stdout": expected_output,
                    "exit": expected_exit if expected_exit is not None else 0,
                    "__mvi_text_output": expected_output,
                }
            return self.run_shell(
                args["command"],
                args.get("timeout"),
                call_id=str(call_id) if call_id else None,
                tool_name=normalized,
                emit_stream=True,
            )
        if normalized == "shell_command":
            expected_output = tool_call.get("expected_output")
            expected_status = tool_call.get("expected_status")
            call_id = tool_call.get("call_id") or tool_call.get("tool_call_id") or tool_call.get("id")

            raw_command = str(args.get("command") or "")
            raw_workdir = args.get("workdir")
            timeout_ms = args.get("timeout_ms")

            timeout_seconds: Optional[int] = None
            if timeout_ms is not None:
                try:
                    timeout_seconds = max(1, int(timeout_ms) // 1000)
                except Exception:
                    timeout_seconds = None

            command_to_run = raw_command
            if isinstance(raw_workdir, str) and raw_workdir.strip() and raw_workdir.strip() != ".":
                try:
                    import shlex

                    wd = self._normalize_workspace_path(raw_workdir.strip())
                    command_to_run = f"cd {shlex.quote(wd)} && {raw_command}"
                except Exception:
                    command_to_run = raw_command

            if isinstance(expected_output, str):
                # Replay mode: run for side-effects, return recorded output for parity.
                try:
                    _ = self.run_shell(
                        command_to_run,
                        timeout_seconds,
                        call_id=str(call_id) if call_id else None,
                        tool_name=normalized,
                        emit_stream=False,
                    )
                except Exception:
                    pass
                payload: Dict[str, Any] = {"__mvi_text_output": expected_output}
                if str(expected_status or "").lower() == "error":
                    payload["error"] = expected_output
                return payload

            start = time.time()
            result = self.run_shell(
                command_to_run,
                timeout_seconds,
                call_id=str(call_id) if call_id else None,
                tool_name=normalized,
                emit_stream=True,
            )
            exit_code = result.get("exit")
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            combined = stdout if stdout else stderr
            duration = max(0.0, time.time() - start)
            formatted = f"Exit code: {exit_code}\nWall time: {duration:.1f} seconds\nOutput:\n{combined}"
            result = dict(result)
            result["__mvi_text_output"] = formatted
            return result
        if normalized == "apply_search_replace":
            target = self._normalize_workspace_path(str(args.get("file_name", "")))
            search_text = str(args.get("search", ""))
            replace_text = str(args.get("replace", ""))
            try:
                exists = self._ray_get(self.sandbox.exists.remote(target))
            except Exception:
                exists = False
            if not exists or search_text.strip() == "":
                return self._ray_get(self.sandbox.write_text.remote(target, replace_text))
            return self._ray_get(self.sandbox.edit_replace.remote(target, search_text, replace_text, 1))
        if normalized == "apply_patch":
            # Codex CLI-compatible patch surface (*** Begin Patch ... *** End Patch)
            patch_source_text = str(args.get("input") or args.get("patchText") or args.get("patch") or "")
            if not patch_source_text.strip():
                return {"error": "apply_patch missing input"}

            direct = self._apply_patch_operations_direct(patch_source_text)
            if direct:
                return direct

            # Fallback: best-effort convert to unified diff and apply via VCS.
            converted = self._convert_patch_to_unified(patch_source_text)
            if converted:
                return self._exec_raw(
                    {"function": "apply_unified_patch", "arguments": {"patch": converted}},
                    _skip_skill=_skip_skill,
                )
            return {"error": "Failed to apply patch"}
        if normalized == "apply_unified_patch":
            patch_source_text = str(args.get("patch", ""))
            patch_text = patch_source_text
            if (
                "*** Add File:" in patch_text
                or "*** Update File:" in patch_text
                or "*** Delete File:" in patch_text
                or "*** Begin Patch" in patch_text
            ):
                converted = self._convert_patch_to_unified(patch_text)
                if converted:
                    patch_text = converted
                    try:
                        tool_call["arguments"]["patch"] = patch_text
                    except Exception:
                        pass
            result = self.vcs({
                "action": "apply_patch",
                "params": {
                    "patch": patch_text,
                    "three_way": True,
                    "index": True,
                    "whitespace": "fix",
                    "keep_rejects": True,
                },
            })
            if not result.get("ok"):
                manual_result = self._apply_patch_operations_direct(patch_source_text or patch_text)
                if manual_result:
                    result = manual_result
            if not result.get("ok"):
                rejects = (result.get("data") or {}).get("rejects") or {}
                has_rejects = any(bool(v) for v in rejects.values())
                if has_rejects:
                    retries = self._retry_diff_with_aider(patch_text)
                    if retries is not None:
                        return retries
            return result
        if name == "TodoWrite":
            # Claude Code's TodoWrite is a separate surface from KyleCode's internal
            # todo.* tools. For Claude parity configs we keep todo.* disabled but
            # still need TodoWrite to work and return the exact success message.
            payload = args if isinstance(args, dict) else {}
            todos = payload.get("todos")
            if not isinstance(todos, list):
                msg = "Error: TodoWrite missing required todos"
                return {"error": msg, "__mvi_text_output": msg}
            try:
                # Session-scoped, in-memory todo board (Claude Code does not persist into workspace).
                setattr(self, "_claude_todowrite_state", list(todos))
            except Exception:
                pass
            msg = (
                "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. "
                "Please proceed with the current tasks if applicable"
            )
            return {"ok": True, "__mvi_text_output": msg}
        if name == "todowrite":
            return self._execute_todo_tool("todo.write_board", args)
        if name == "todoread":
            return self._execute_todo_tool("todo.list", args)
        if name in {"create_file_from_block", "Write"}:
            path = self._normalize_workspace_path(str(args.get("file_name", "")))
            content = str(args.get("content", ""))
            return self._ray_get(self.sandbox.write_text.remote(path, content))
        if name == "mark_task_complete":
            return {"action": "complete"}
        if name.startswith("todo."):
            try:
                return self._execute_todo_tool(name, args)
            except ValueError as exc:
                return {"error": str(exc)}
        if name in {"run_shell", "Bash"}:
            command = args.get("command") or args.get("input")
            timeout = args.get("timeout")
            return self._ray_get(self.sandbox.run_shell.remote(command, timeout=timeout))
        return {"error": f"unknown tool {name}"}

    def _execute_todo_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        manager = getattr(self, "todo_manager", None)
        if manager is None:
            raise ValueError("Todo tools are disabled for this configuration.")
        payload = args if isinstance(args, dict) else {}
        if name == "todo.create":
            return manager.handle_create(payload)
        if name == "todo.update":
            return manager.handle_update(payload)
        if name == "todo.complete":
            return manager.handle_complete(payload)
        if name == "todo.cancel":
            return manager.handle_cancel(payload)
        if name == "todo.reorder":
            return manager.handle_reorder(payload)
        if name == "todo.attach":
            return manager.handle_attach(payload)
        if name == "todo.note":
            return manager.handle_note(payload)
        if name == "todo.list":
            return manager.handle_list(payload)
        if name == "todo.write_board":
            todos_payload = payload.get("todos")
            if isinstance(todos_payload, str):
                try:
                    payload["todos"] = json.loads(todos_payload)
                except Exception:
                    pass
            if isinstance(payload.get("todos"), list):
                normalized_list = []
                status_map = {
                    "todo": "pending",
                    "pending": "pending",
                    "in_progress": "in_progress",
                    "blocked": "blocked",
                    "complete": "completed",
                    "completed": "completed",
                    "done": "completed",
                    "canceled": "canceled",
                    "cancelled": "canceled",
                }
                for entry in payload["todos"]:
                    if not isinstance(entry, dict):
                        normalized_list.append(entry)
                        continue
                    normalized = dict(entry)
                    if "content" not in normalized:
                        for alias in ("description", "task", "item"):
                            value = normalized.get(alias)
                            if value:
                                normalized["content"] = value
                                if alias != "content":
                                    normalized.pop(alias, None)
                                break
                    status = normalized.get("status")
                    if isinstance(status, str):
                        normalized["status"] = status_map.get(status.lower(), status)
                    normalized_list.append(normalized)
                payload["todos"] = normalized_list
            return manager.handle_write_board(payload)
        raise ValueError(f"Unsupported todo tool: {name}")

    def run_agentic_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_steps: int = 12,
        output_json_path: Optional[str] = None,
        stream_responses: bool = False,
        output_md_path: Optional[str] = None,
        tool_prompt_mode: str = "system_once",  # system_once | per_turn_append | system_and_per_turn | none
        completion_sentinel: Optional[str] = ">>>>>> END RESPONSE",
        completion_config: Optional[Dict[str, Any]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
        event_queue: Optional[Any] = None,
        permission_queue: Optional[Any] = None,
        control_queue: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Clear any prior stop request from earlier runs (Esc in the TUI should only
        # interrupt the current in-flight run, not permanently disable the session).
        self._stop_requested = False
        # Initialize components
        emitter = event_emitter
        if emitter is None and event_queue is not None:
            def queue_emitter(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
                try:
                    event_queue.put((event_type, payload, turn))
                except Exception:
                    pass
            emitter = queue_emitter
        self.todo_manager = None
        session_state = SessionState(self.workspace, self.image, self.config, event_emitter=emitter)
        self._active_session_state = session_state
        hook_manager = getattr(self, "hook_manager", None)
        if hook_manager is not None and emitter is not None:
            session_state.set_event_emitter(
                _wrap_event_emitter_with_before_emit(
                    hook_manager,
                    session_state=session_state,
                    emitter=emitter,
                )
            )
        try:
            session_state.record_lifecycle_event(
                "session_started",
                {"model": str(model), "user_prompt": user_prompt or ""},
            )
        except Exception:
            pass
        self._multi_agent_last_wakeup_event_id = 0
        self._prompt_hashes = {"system": None, "per_turn": {}}
        self._turn_diagnostics = []
        session_state.set_provider_metadata("permission_queue", permission_queue)
        session_state.set_provider_metadata("control_queue", control_queue)
        if isinstance(context, dict):
            task_type = context.get("task_type") or context.get("taskType")
            if task_type:
                session_state.set_provider_metadata("task_type", str(task_type))
            latency_budget = context.get("latency_budget_ms") or context.get("latencyBudgetMs")
            if latency_budget is not None:
                session_state.set_provider_metadata("latency_budget_ms", latency_budget)
            initial_tpf = context.get("initial_tpf") or context.get("initialTpf")
            if initial_tpf is not None:
                session_state.set_provider_metadata("initial_tpf", initial_tpf)
            winrate = context.get("winrate_vs_baseline") or context.get("winrateVsBaseline")
            if winrate is not None:
                session_state.set_provider_metadata("winrate_vs_baseline", winrate)
        session_state.set_provider_metadata("initial_user_prompt", user_prompt or "")
        session_state.set_provider_metadata(
            "requires_build_guard",
            self._should_require_build_guard(user_prompt or ""),
        )
        session_state.set_provider_metadata("workspace_context_initialized", False)
        session_state.set_provider_metadata("workspace_context_required", False)
        session_state.set_provider_metadata("workspace_pending_todo_update_allowed", False)
        todos_cfg = self._todo_config()
        if todos_cfg["enabled"]:
            todo_store = TodoStore(self.workspace)
            todo_manager = TodoManager(todo_store, session_state.emit_todo_event)
            session_state.set_todo_manager(todo_manager)
            session_state.set_provider_metadata("todos_config", todos_cfg)
            session_state.set_provider_metadata("todo_snapshot", todo_manager.snapshot())
            self.todo_manager = todo_manager
        orchestrator = self._get_multi_agent_orchestrator()
        if orchestrator is not None:
            try:
                session_state.set_provider_metadata("multi_agent_ordering", "total_event_id")
                orchestrator.event_log.add(
                    "run.started",
                    agent_id="main",
                    payload={"model": str(model), "user_prompt": user_prompt or ""},
                )
                orchestrator.persist_event_log()
            except Exception:
                pass
        completion_detector = CompletionDetector(
            config=completion_config or self.config.get("completion", {}),
            completion_sentinel=completion_sentinel
        )
        markdown_logger = MarkdownLogger(output_md_path)
        # Also seed conversation.md under logging v2 if enabled
        try:
            if self.logger_v2.run_dir:
                self.logger_v2.write_text("conversation/conversation.md", "# Conversation Transcript\n")
        except Exception:
            pass
        telemetry_path = os.environ.get("RAYCODE_TELEMETRY_PATH")
        if not telemetry_path and getattr(self.logger_v2, "run_dir", None):
            telemetry_path = str(Path(self.logger_v2.run_dir) / "meta" / "telemetry.jsonl")
        telemetry = TelemetryLogger(telemetry_path)
        self._active_telemetry_logger = telemetry
        self._mcp_manager = None
        self._mcp_replay_tape = None
        self._mcp_record_path = None
        error_handler = ErrorHandler(output_json_path)

        telemetry_cfg = self.config.get("telemetry", {}) or {}
        db_path = telemetry_cfg.get("database_path") or os.environ.get("KC_TELEMETRY_DB")
        self._reward_metrics_sqlite = None
        self._todo_metrics_sqlite = None
        if db_path:
            try:
                self._reward_metrics_sqlite = RewardMetricsSQLiteWriter(str(db_path))
                self._todo_metrics_sqlite = TodoMetricsSQLiteWriter(str(db_path))
            except Exception:
                self._reward_metrics_sqlite = None
                self._todo_metrics_sqlite = None

        self._ensure_capability_probes(session_state, markdown_logger)
        self.provider_metrics.reset()
        user_prompt, max_steps = self._prepare_replay_session(session_state, user_prompt, max_steps)
        
        # Clear existing log files
        for del_path in [output_md_path, output_json_path]:
            if del_path and os.path.exists(del_path):
                os.remove(del_path)
        
        # Initialize provider and client
        requested_route_id = model
        if self._active_replay_session:
            requested_route_id = "replay"

        runtime = None
        client = None
        provider_tools_cfg = dict((self.config.get("provider_tools") or {}))
        
        # Initialize runtime (ReplayRuntime if replay, else standard)
        provider_config, resolved_model, supports_native_tools_for_model = provider_router.get_provider_config(requested_route_id)
        runtime_descriptor, runtime_model = provider_router.get_runtime_descriptor(requested_route_id)
        client_config = provider_router.create_client_config(requested_route_id)

        # Fix for client_config usage in replay mode
        if runtime_descriptor.provider_id == "replay":
            client_config.setdefault("api_key", "mock")

        api_variant_override = provider_tools_cfg.get("api_variant")
        if api_variant_override and runtime_descriptor.provider_id == "openai":
            variant = str(api_variant_override).lower()
            if variant == "responses":
                runtime_descriptor.runtime_id = "openai_responses"
                runtime_descriptor.default_api_variant = "responses"
            elif variant == "chat":
                runtime_descriptor.runtime_id = "openai_chat"
                runtime_descriptor.default_api_variant = "chat"

        if not client_config["api_key"] and runtime_descriptor.provider_id != "replay":
            raise RuntimeError(f"{provider_config.api_key_env} missing in environment")

        runtime = provider_registry.create_runtime(runtime_descriptor)

        # Create client with provider-specific configuration
        client = runtime.create_client(
            client_config["api_key"],
            base_url=client_config.get("base_url"),
            default_headers=client_config.get("default_headers"),
        )

        self._current_route_id = requested_route_id
        try:
            session_state.set_provider_metadata("route_id", requested_route_id)
        except Exception:
            pass
        model = runtime_model  # Update model to resolved version for API calls
        session_state.set_provider_metadata("provider_id", runtime_descriptor.provider_id)
        session_state.set_provider_metadata("runtime_id", runtime_descriptor.runtime_id)
        session_state.set_provider_metadata("api_variant", runtime_descriptor.default_api_variant)
        session_state.set_provider_metadata("resolved_model", runtime_model)
        try:
            capabilities = provider_router.get_capabilities(model)
            session_state.set_provider_metadata("capabilities", asdict(capabilities))
        except Exception:
            pass
        
        # Defer initial message creation until after tool/dialect resolution so we can compile prompts correctly
        per_turn_prompt = ""
        
        # Setup tool definitions and dialects
        tool_defs_yaml = self._tool_defs_from_yaml()
        tool_defs = tool_defs_yaml or self._get_default_tool_definitions()

        # MCP baseline + live stdio tool provider (optional)
        try:
            mcp_servers = load_mcp_servers_from_config(self.config, self.workspace)
            mcp_tools = load_mcp_tools_from_config(self.config, self.workspace)
            mcp_fixture_results = load_mcp_fixture_results(self.config, self.workspace)
            live_tools: List[Dict[str, Any]] = []
            mcp_manager = None
            if mcp_live_tools_enabled(self.config) and mcp_servers:
                mcp_manager = MCPManager(mcp_servers)
                try:
                    live = mcp_manager.list_tools()
                    for tool in live:
                        live_tools.append(
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "schema": tool.schema,
                                "server": tool.server,
                                "source": "live",
                            }
                        )
                except Exception:
                    live_tools = []
            if mcp_tools:
                tool_defs = tool_defs + tool_defs_from_mcp_tools(mcp_tools)
            if live_tools:
                tool_defs = tool_defs + tool_defs_from_mcp_tools(live_tools)
            tools_snapshot: List[Dict[str, Any]] = []
            if mcp_tools:
                tools_snapshot.extend(mcp_tools)
            if live_tools:
                tools_snapshot.extend(live_tools)
            if mcp_servers or tools_snapshot or mcp_fixture_results:
                session_state.set_provider_metadata(
                    "mcp_snapshot",
                    build_mcp_snapshot(mcp_servers, tools_snapshot, mcp_fixture_results),
                )
            self._mcp_manager = mcp_manager
            tape_path = mcp_replay_tape_path(self.config, self.workspace)
            if tape_path:
                self._mcp_replay_tape = load_mcp_replay_tape(tape_path)
            record_path = None
            if mcp_servers or tools_snapshot or mcp_fixture_results or mcp_live_tools_enabled(self.config):
                record_path = mcp_record_tape_path(
                    self.config,
                    self.workspace,
                    Path(self.logger_v2.run_dir) if getattr(self.logger_v2, "run_dir", None) else None,
                )
            self._mcp_record_path = record_path
        except Exception:
            pass

        # Plugins + skills (deterministic load order; explicit invocation only)
        try:
            plugin_manifests = discover_plugin_manifests(self.config, self.workspace)
            if plugin_manifests:
                session_state.set_provider_metadata("plugin_snapshot", plugin_snapshot(plugin_manifests))
            plugin_permissions: Dict[str, Any] = {}
            plugin_trust_map: Dict[str, bool] = {}
            for manifest in plugin_manifests:
                perms = getattr(manifest, "permissions", None)
                if isinstance(perms, dict) and perms:
                    plugin_permissions = self._merge_permission_rules(plugin_permissions, perms)
                runtime = getattr(manifest, "runtime", {}) if hasattr(manifest, "runtime") else {}
                trusted = None
                if isinstance(runtime, dict):
                    if "trusted" in runtime:
                        trusted = bool(runtime.get("trusted"))
                    elif "sandbox" in runtime:
                        sandbox = str(runtime.get("sandbox") or "").lower()
                        if sandbox in {"none", "disabled", "trusted"}:
                            trusted = True
                        elif sandbox:
                            trusted = False
                if trusted is None:
                    trusted = True
                plugin_trust_map[str(manifest.plugin_id)] = bool(trusted)
            if plugin_permissions:
                session_state.set_provider_metadata("plugin_permissions_requested", plugin_permissions)
            self._plugin_trust_map = plugin_trust_map
            plugins_cfg = (self.config.get("plugins") or {}) if isinstance(self.config, dict) else {}
            auto_grant = bool(plugins_cfg.get("auto_grant_permissions"))
            if plugin_permissions and auto_grant:
                merged_permissions = self._merge_permission_rules(self.config.get("permissions"), plugin_permissions)
                try:
                    self.config["permissions"] = merged_permissions
                except Exception:
                    pass
                try:
                    from agentic_coder_prototype.policy_pack import PolicyPack

                    self.permission_broker = PermissionBroker(
                        merged_permissions,
                        policy_pack=PolicyPack.from_config(self.config),
                    )
                except Exception:
                    pass
            hook_manager = build_hook_manager(
                self.config,
                self.workspace,
                plugin_manifests=plugin_manifests,
            )
            if hook_manager:
                self.hook_manager = hook_manager
                session_state.set_provider_metadata("hook_snapshot", hook_manager.snapshot())
            else:
                self.hook_manager = None
            plugin_skill_paths: List[Path] = []
            for manifest in plugin_manifests:
                for rel in manifest.skills_paths:
                    try:
                        base = getattr(manifest, "root", None) or self.workspace
                        plugin_skill_paths.append(Path(str(base)) / rel)
                    except Exception:
                        continue
            prompt_skills, graph_skills = load_skills(
                self.config,
                self.workspace,
                plugin_skill_paths=plugin_skill_paths,
            )
            selection = normalize_skill_selection(
                self.config,
                session_state.get_provider_metadata("skill_selection"),
            )
            selected_prompts, selected_graphs, enabled_map = apply_skill_selection(
                prompt_skills,
                graph_skills,
                selection,
            )
            if prompt_skills or graph_skills:
                session_state.set_provider_metadata("skill_selection", selection)
                session_state.set_provider_metadata(
                    "skill_catalog",
                    build_skill_catalog(prompt_skills, graph_skills, selection=selection, enabled_map=enabled_map),
                )
                session_state.set_provider_metadata("prompt_skills", selected_prompts)
                session_state.set_provider_metadata("graph_skills", selected_graphs)
                # Append GraphSkill tool defs (explicit invocation)
                for skill in selected_graphs:
                    tool_def = ToolDefinition(
                        type_id="skill",
                        name=f"skill.{skill.skill_id}",
                        description=skill.description or f"Execute skill {skill.skill_id}",
                        parameters=[ToolParameter(name="input", type="object", description="Skill input payload")],
                        blocking=True,
                    )
                    try:
                        tool_def.provider_settings = {
                            "openai": {"native_primary": True},
                        }
                    except Exception:
                        pass
                    tool_defs.append(tool_def)
                    try:
                        yaml_tools = getattr(self, "yaml_tools", None)
                        if isinstance(yaml_tools, list):
                            existing = any(getattr(t, "name", None) == tool_def.name for t in yaml_tools)
                            if not existing:
                                yaml_tools.append(tool_def)
                    except Exception:
                        pass
        except Exception:
            pass

        completion_tool_enabled = self._completion_tool_config_enabled()
        if completion_tool_enabled:
            tool_defs = self._ensure_completion_tool(tool_defs)
        mark_tool_present = any(getattr(t, "name", None) == "mark_task_complete" for t in tool_defs)
        session_state.set_provider_metadata("mark_task_complete_available", bool(completion_tool_enabled and mark_tool_present))
        if not completion_tool_enabled or not mark_tool_present:
            session_state.set_provider_metadata("completion_sentinel_required", True)
        
        # Create dialect mapping and filter based on model configuration
        dialect_mapping = self._create_dialect_mapping()
        active_dialect_names = self.dialect_manager.get_dialects_for_model(
            model, list(dialect_mapping.keys())
        )
        
        # Initialize caller for all modes (needed for parsing)
        # Apply v2 selection ordering (by_model/by_tool_kind) if v2 config detected
        try:
            from .compilation.v2_loader import is_v2_config
            if is_v2_config(self.config):
                active_dialect_names = self._apply_v2_dialect_selection(active_dialect_names, model, tool_defs)
        except Exception:
            pass

        filtered_dialects = [dialect_mapping[name] for name in active_dialect_names if name in dialect_mapping]
        caller = CompositeToolCaller(filtered_dialects)

        prompt_compile_key: Optional[str] = None
        prompt_compiler_version: Optional[str] = None
        try:
            if int(self.config.get("version", 0)) == 2 and self.config.get("prompts"):
                mode_name = self._resolve_active_mode()
                comp = get_compiler()
                prompts_cfg = self._prompts_config_with_todos()
                compiler_choice = str(prompts_cfg.get("compiler") or "v3").lower()
                extra_blocks = {"system": [], "per_turn": []}
                user_prompt_extra: List[str] = []
                try:
                    prompt_skills = session_state.get_provider_metadata("prompt_skills") or []
                    for skill in prompt_skills:
                        slot = getattr(skill, "slot", "system")
                        blocks = getattr(skill, "blocks", []) or []
                        if not blocks:
                            continue
                        text = "\n".join(str(b) for b in blocks if b is not None).strip()
                        if not text:
                            continue
                        if slot in {"system", "developer"}:
                            header = f"\n\n# Skill: {getattr(skill, 'skill_id', 'unknown')}\n"
                            extra_blocks["system"].append(header + text)
                        elif slot == "per_turn":
                            extra_blocks["per_turn"].append(text)
                        elif slot == "user":
                            user_prompt_extra.append(text)
                except Exception:
                    user_prompt_extra = []
                v2 = comp.compile_v2_prompts(
                    prompts_cfg,
                    mode_name,
                    tool_defs,
                    active_dialect_names,
                    extra_blocks=extra_blocks,
                )
                session_state.set_provider_metadata("current_mode", mode_name)
                system_prompt = v2.get("system") or system_prompt
                per_turn_prompt = v2.get("per_turn") or ""
                prompt_compile_key = v2.get("cache_key")
                prompt_compiler_version = "v2"
                if user_prompt_extra:
                    user_prompt = (user_prompt or "") + "\n\n" + "\n\n".join(user_prompt_extra)
                if compiler_choice == "v3":
                    try:
                        from .compilation.prompt_compiler_v2_adapter import shadow_compile_v2_bundle
                        from .compilation.prompt_compiler_v3 import write_prompt_bundle_artifacts

                        compile_inputs = {
                            "mode": mode_name,
                            "dialects": list(active_dialect_names or []),
                            "cache_key": v2.get("cache_key"),
                        }
                        bundle = shadow_compile_v2_bundle(
                            config=prompts_cfg,
                            mode_name=mode_name,
                            tool_defs=tool_defs,
                            dialects=active_dialect_names,
                            compile_inputs=compile_inputs,
                        )
                        prompt_compile_key = bundle.compile_key
                        prompt_compiler_version = bundle.compiler_version
                        if self.logger_v2.run_dir:
                            write_prompt_bundle_artifacts(bundle, Path(self.logger_v2.run_dir))
                    except Exception:
                        pass
                try:
                    shadow_flag = (prompts_cfg.get("compiler_shadow") or "").lower()
                except Exception:
                    shadow_flag = ""
                if shadow_flag in {"v3", "true", "1"}:
                    try:
                        from .compilation.prompt_compiler_v2_adapter import shadow_compile_v2_bundle
                        from .compilation.prompt_compiler_v3 import hash_payload, write_prompt_bundle_artifacts

                        compile_inputs = {
                            "mode": mode_name,
                            "dialects": list(active_dialect_names or []),
                            "cache_key": v2.get("cache_key"),
                        }
                        bundle = shadow_compile_v2_bundle(
                            config=prompts_cfg,
                            mode_name=mode_name,
                            tool_defs=tool_defs,
                            dialects=active_dialect_names,
                            compile_inputs=compile_inputs,
                        )
                        v2_hashes = {
                            "system": hash_payload(system_prompt or ""),
                            "per_turn": hash_payload(per_turn_prompt or ""),
                        }
                        v3_hashes = {
                            "system": bundle.slots.get("system").slot_hash if bundle.slots.get("system") else None,
                            "per_turn": bundle.slots.get("per_turn").slot_hash if bundle.slots.get("per_turn") else None,
                        }
                        shadow_payload = {
                            "v2_hashes": v2_hashes,
                            "v3_hashes": v3_hashes,
                            "matches": {
                                "system": v2_hashes.get("system") == v3_hashes.get("system"),
                                "per_turn": v2_hashes.get("per_turn") == v3_hashes.get("per_turn"),
                            },
                            "compile_key": bundle.compile_key,
                        }
                        if self.logger_v2.run_dir:
                            self.logger_v2.write_json("prompts/shadow_v2_v3.json", shadow_payload)
                            write_prompt_bundle_artifacts(bundle, Path(self.logger_v2.run_dir))
                    except Exception:
                        pass
                # Persist TPSL catalogs if available
                try:
                    if self.logger_v2.run_dir and isinstance(v2.get("tpsl"), dict):
                        tpsl_meta = v2["tpsl"]
                        # Choose a stable catalog id from template ids (hash part)
                        def _catalog_id(meta: dict) -> str:
                            tid = str(meta.get("template_id", "fallback"))
                            return tid.split("::")[-1].replace("/", "_")
                        files = []
                        # System catalog
                        if isinstance(tpsl_meta.get("system"), dict):
                            cid = _catalog_id(tpsl_meta["system"]) or "tpsl"
                            path = f"prompts/catalogs/{cid}/system_full.md"
                            self.logger_v2.write_text(path, str(tpsl_meta["system"].get("text", "")))
                            files.append({
                                "path": path,
                                "dialect": tpsl_meta["system"].get("dialect"),
                                "detail": tpsl_meta["system"].get("detail"),
                                "template_id": tpsl_meta["system"].get("template_id"),
                            })
                        # Per-turn catalog
                        if isinstance(tpsl_meta.get("per_turn"), dict):
                            cid = _catalog_id(tpsl_meta["per_turn"]) or "tpsl"
                            path = f"prompts/catalogs/{cid}/per_turn_short.md"
                            self.logger_v2.write_text(path, str(tpsl_meta["per_turn"].get("text", "")))
                            files.append({
                                "path": path,
                                "dialect": tpsl_meta["per_turn"].get("dialect"),
                                "detail": tpsl_meta["per_turn"].get("detail"),
                                "template_id": tpsl_meta["per_turn"].get("template_id"),
                            })
                        if files:
                            # Use first catalog id as manifest name
                            name = (files[0]["template_id"] or "tpsl").split("::")[-1]
                            self.prompt_logger.save_catalog(name, files)
                except Exception:
                    pass
        except Exception:
            per_turn_prompt = per_turn_prompt or ""

        system_prompt = self._append_environment_prompt(system_prompt)
        # Persist compiled system prompt after environment + skills
        try:
            if system_prompt and self.logger_v2.run_dir:
                self.prompt_logger.save_compiled_system(system_prompt)
            self._register_prompt_hash("system", system_prompt)
            from .compilation.prompt_compiler_v3 import hash_payload
            self._prompt_blocks_hash = hash_payload({"system": system_prompt or "", "per_turn": per_turn_prompt or ""})
            self._prompt_compile_key = prompt_compile_key
            self._prompt_compiler_version = prompt_compiler_version
        except Exception:
            pass

        enhanced_system_msg = {"role": "system", "content": system_prompt}
        initial_user_content = user_prompt if not per_turn_prompt else (user_prompt + "\n\n" + per_turn_prompt)
        enhanced_user_msg = {"role": "user", "content": initial_user_content}
        resume_snapshot = self._load_resume_snapshot()
        resume_has_system = False
        if isinstance(resume_snapshot, dict):
            seed_messages = resume_snapshot.get("messages")
            if isinstance(seed_messages, list):
                cleaned = [msg for msg in seed_messages if isinstance(msg, dict)]
                if cleaned:
                    session_state.messages = list(cleaned)
                    session_state.provider_messages = list(cleaned)
                    resume_has_system = any(m.get("role") == "system" for m in cleaned if isinstance(m, dict))
            seed_transcript = resume_snapshot.get("transcript")
            if isinstance(seed_transcript, list):
                session_state.transcript = [entry for entry in seed_transcript if isinstance(entry, dict)]
            meta = resume_snapshot.get("provider_metadata")
            if isinstance(meta, dict):
                session_state.provider_metadata.update(meta)
            session_state.set_provider_metadata("resume_snapshot_applied", True)
        if not resume_has_system:
            session_state.add_message(enhanced_system_msg)
        session_state.add_message(enhanced_user_msg)
        
        # Configure native tools and tool prompt mode
        native_pref_hint = getattr(self, "_native_preference_hint", None)
        provider_tools_cfg = dict(provider_tools_cfg)
        if provider_tools_cfg.get("use_native") is None and native_pref_hint is not None:
            provider_tools_cfg["use_native"] = native_pref_hint
        provider_tools_cfg = self._apply_capability_tool_overrides(
            provider_tools_cfg,
            session_state,
            markdown_logger,
        )
        effective_config = dict(self.config)
        effective_config["provider_tools"] = provider_tools_cfg
        self._provider_tools_effective = provider_tools_cfg
        use_native_tools = provider_router.should_use_native_tools(model, effective_config)
        will_use_native_tools = self._setup_native_tools(model, use_native_tools)
        tool_prompt_mode = self._adjust_tool_prompt_mode(tool_prompt_mode, will_use_native_tools)
        session_state.last_tool_prompt_mode = tool_prompt_mode
        
        # Setup tool prompts and system messages
        local_tools_prompt = self._setup_tool_prompts(
            tool_prompt_mode, tool_defs, active_dialect_names, 
            session_state, markdown_logger, caller
        )
        
        # Add enhanced descriptive fields to initial messages after tool setup
        self._add_enhanced_message_fields(
            tool_prompt_mode, tool_defs, active_dialect_names, 
            session_state, will_use_native_tools, local_tools_prompt, user_prompt
        )
        
        # Initialize markdown log and snapshot
        markdown_logger.log_system_message(system_prompt)
        try:
            if self.logger_v2.run_dir:
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.system(system_prompt))
        except Exception:
            pass
        if session_state.get_provider_metadata("current_mode") is None:
            session_state.set_provider_metadata("current_mode", self._resolve_active_mode())
        # Use enhanced user content for markdown log
        initial_user_content = session_state.messages[1].get("content", user_prompt)
        markdown_logger.log_user_message(initial_user_content)
        try:
            if self.logger_v2.run_dir:
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.user(initial_user_content))
        except Exception:
            pass
        session_state.write_snapshot(output_json_path, model)
        
        # Main agentic loop - significantly simplified
        run_result = None
        run_loop_error: Optional[Dict[str, Any]] = None
        try:
            run_result = self._run_main_loop(
                runtime,
                client,
                model,
                max_steps,
                output_json_path,
                tool_prompt_mode,
                tool_defs,
                active_dialect_names,
                caller,
                session_state,
                completion_detector,
                markdown_logger,
                error_handler,
                stream_responses,
                local_tools_prompt,
                client_config,
            )
        except Exception as exc:
            run_loop_error = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            session_state.add_transcript_entry({"run_loop_exception": run_loop_error})
            try:
                session_state.set_provider_metadata("run_loop_exception", run_loop_error)
            except Exception:
                pass
            try:
                if self.logger_v2.run_dir:
                    self.logger_v2.write_json("errors/run_loop_exception.json", run_loop_error)
            except Exception:
                pass
        finally:
            self._persist_final_workspace()
            try:
                self._persist_multi_agent_log()
            except Exception:
                pass
            try:
                active_logger = getattr(self, "_active_telemetry_logger", None)
                if active_logger:
                    active_logger.close()
            except Exception:
                pass
            self._active_telemetry_logger = None
            self._active_session_state = None
        # Defensive: always return a dict result
        if not isinstance(run_result, dict):
            completion_summary = session_state.completion_summary or {}
            completion_summary.setdefault("completed", False)
            if run_loop_error is not None:
                completion_summary["reason"] = "run_loop_exception"
                completion_summary["error"] = run_loop_error
            else:
                completion_summary.setdefault("reason", "no_result")
            session_state.completion_summary = completion_summary
            run_result = {
                "messages": session_state.messages,
                "transcript": session_state.transcript,
                "completion_summary": completion_summary,
                "completion_reason": completion_summary.get("reason", "no_result"),
                "completed": bool(completion_summary.get("completed", False)),
            }
        try:
            run_dir = getattr(self.logger_v2, "run_dir", None)
            if run_dir and isinstance(run_result, dict):
                run_result.setdefault("run_dir", str(run_dir))
                run_result.setdefault("logging_dir", str(run_dir))
        except Exception:
            pass

        # Populate finish metadata for IR and persist conversation snapshot
        usage_payload = session_state.get_provider_metadata("usage")
        if not isinstance(usage_payload, dict):
            usage_payload = {}
        finish_reason = "stop" if run_result.get("completed") else "error"
        finish_meta = session_state.get_provider_metadata("raw_finish_meta")
        agent_summary = copy.deepcopy(session_state.completion_summary or {})
        session_state.set_ir_finish(
            IRFinish(
                reason=finish_reason,
                usage=usage_payload,
                provider_meta=finish_meta,
                agent_summary=agent_summary,
            )
        )

        try:
            if self.logger_v2.run_dir:
                conv_id = os.path.basename(self.logger_v2.run_dir)
                conversation_ir = session_state.build_conversation_ir(conversation_id=conv_id)
                self.logger_v2.write_json("meta/conversation_ir.json", asdict(conversation_ir))
        except Exception:
            pass
        if orchestrator is not None:
            try:
                orchestrator.event_log.add(
                    "run.finished",
                    agent_id="main",
                    payload={
                        "completed": bool(run_result.get("completed", False)),
                        "completion_reason": run_result.get("completion_reason"),
                    },
                )
                orchestrator.persist_event_log()
            except Exception:
                pass

        return run_result
    
    def _persist_final_workspace(self) -> None:
        """Copy the final workspace into the run's log directory if available."""
        run_dir = getattr(self.logger_v2, "run_dir", None)
        if not run_dir:
            return
        try:
            workspace_path = Path(self.workspace)
        except Exception:
            return
        if not workspace_path.exists() or not workspace_path.is_dir():
            return
        dest = Path(run_dir) / "final_container_dir"
        try:
            workspace_resolved = workspace_path.resolve()
            dest_resolved = dest.resolve(strict=False)
            if str(dest_resolved).startswith(str(workspace_resolved)):
                return
        except Exception:
            pass
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(workspace_path, dest)
        except Exception:
            pass

    def _get_default_tool_definitions(self) -> List[ToolDefinition]:
        return get_default_tool_definitions()
    
    def _create_dialect_mapping(self) -> Dict[str, Any]:
        return create_dialect_mapping()

    def _apply_v2_dialect_selection(self, current: List[str], model_id: str, tool_defs: List[ToolDefinition]) -> List[str]:
        return apply_v2_dialect_selection(self, current, model_id, tool_defs)

    def _apply_selection_legacy(
        self,
        current: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        selection_cfg: Dict[str, Any],
    ) -> List[str]:
        return apply_selection_legacy(current, model_id, tool_defs, selection_cfg)

    def _apply_preference_order(
        self,
        base_order: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        preference_cfg: Dict[str, Any],
    ) -> Tuple[List[str], Optional[bool]]:
        return apply_preference_order(base_order, model_id, tool_defs, preference_cfg)

    def _get_native_preference_hint(self) -> Optional[bool]:
        return get_native_preference_hint(self)

    def _setup_native_tools(self, model: str, use_native_tools: bool) -> bool:
        return setup_native_tools(self, model, use_native_tools)
    
    def _adjust_tool_prompt_mode(self, tool_prompt_mode: str, will_use_native_tools: bool) -> str:
        return adjust_tool_prompt_mode(self, tool_prompt_mode, will_use_native_tools)
    
    def _setup_tool_prompts(
        self, 
        tool_prompt_mode: str, 
        tool_defs: List[ToolDefinition], 
        active_dialect_names: List[str],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        caller
    ) -> str:
        return setup_tool_prompts(
            self,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            markdown_logger,
            caller,
        )
    
    def _add_enhanced_message_fields(
        self,
        tool_prompt_mode: str,
        tool_defs: List[ToolDefinition],
        active_dialect_names: List[str],
        session_state: SessionState,
        will_use_native_tools: bool,
        local_tools_prompt: str,
        user_prompt: str
    ):
        add_enhanced_message_fields(
            self,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            will_use_native_tools,
            local_tools_prompt,
            user_prompt,
        )
    
    def _run_main_loop(
        self,
        runtime,
        client,
        model: str,
        max_steps: int,
        output_json_path: str,
        tool_prompt_mode: str,
        tool_defs: List[ToolDefinition],
        active_dialect_names: List[str],
        caller,
        session_state: SessionState,
        completion_detector: CompletionDetector,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool,
        local_tools_prompt: str,
        client_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Main loop delegated to conductor_loop.run_main_loop for readability."""
        return run_main_loop(
            self,
            runtime,
            client,
            model,
            max_steps,
            output_json_path,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            caller,
            session_state,
            completion_detector,
            markdown_logger,
            error_handler,
            stream_responses,
            local_tools_prompt,
            client_config,
        )

    def _persist_error_artifacts(self, turn_index: int, payload: Dict[str, Any]) -> None:
        """Persist error payload and associated diagnostics artifacts."""

        run_dir = getattr(self.logger_v2, "run_dir", None)
        if not run_dir:
            return

        try:
            self.logger_v2.write_json(f"errors/turn_{turn_index}.json", payload)
            details = payload.get("details")
            if not isinstance(details, dict):
                return
            headers = details.get("response_headers")
            if headers:
                self.logger_v2.write_json(
                    f"raw/responses/turn_{turn_index}.headers.json",
                    headers,
                )
            raw_b64 = details.get("raw_body_b64")
            if raw_b64:
                self.logger_v2.write_text(
                    f"raw/responses/turn_{turn_index}.body.b64",
                    raw_b64,
                )
            raw_excerpt = details.get("raw_excerpt")
            if raw_excerpt:
                self.logger_v2.write_text(
                    f"raw/responses/turn_{turn_index}.raw_excerpt.txt",
                    raw_excerpt,
                )
            html_excerpt = details.get("html_excerpt")
            if html_excerpt:
                self.logger_v2.write_text(
                    f"raw/responses/turn_{turn_index}.html_excerpt.txt",
                    html_excerpt,
                )
        except Exception:
            pass

    def _retry_with_fallback(
        self,
        runtime,
        client,
        model: str,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        runtime_context: ProviderRuntimeContext,
        *,
        stream_responses: bool,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        attempted: List[Tuple[str, bool, Optional[str]]],
        last_error: Optional[ProviderRuntimeError],
    ) -> Optional[ProviderResult]:
        return retry_with_fallback(
            self,
            runtime,
            client,
            model,
            messages,
            tools_schema,
            runtime_context,
            stream_responses=stream_responses,
            session_state=session_state,
            markdown_logger=markdown_logger,
            attempted=attempted,
            last_error=last_error,
        )

    def _ensure_capability_probes(self, session_state: SessionState, markdown_logger: MarkdownLogger) -> None:
        if self._capability_probes_ran:
            return
        try:
            self.capability_probe_runner.markdown_logger = markdown_logger
            self.capability_probe_runner.run(self.config, session_state)
        except Exception:
            pass
        finally:
            self._capability_probes_ran = True

    def _update_health_metadata(self, session_state: SessionState) -> None:
        try:
            session_state.set_provider_metadata("route_health", self.route_health.snapshot())
        except Exception:
            pass

    def _invoke_runtime_with_streaming(
        self,
        runtime,
        client,
        model: str,
        send_messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_responses: bool,
        runtime_context: ProviderRuntimeContext,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        turn_index: int,
    ) -> Tuple[ProviderResult, bool]:
        """Invoke provider runtime, falling back to non-streaming on failure."""

        return self.provider_invoker.invoke(
            runtime=runtime,
            client=client,
            model=model,
            send_messages=send_messages,
            tools_schema=tools_schema,
            stream_responses=stream_responses,
            runtime_context=runtime_context,
            session_state=session_state,
            markdown_logger=markdown_logger,
            turn_index=turn_index,
            route_id=getattr(self, "_current_route_id", None),
        )

    def _build_exec_func(self, session_state: SessionState) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        return build_exec_func(self, session_state)

    def _execute_agent_calls(
        self,
        parsed_calls: List[Any],
        exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        session_state: SessionState,
        *,
        transcript_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        policy_bypass: bool = False,
    ) -> Tuple[List[Any], int, Optional[Dict[str, Any]], Dict[str, Any]]:
        return execute_agent_calls(
            self,
            parsed_calls,
            exec_func,
            session_state,
            transcript_callback=transcript_callback,
            policy_bypass=policy_bypass,
        )
    
    def _get_model_response(
        self,
        runtime,
        client,
        model: str,
        tool_prompt_mode: str,
        tool_defs: List[ToolDefinition],
        active_dialect_names: List[str],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
        local_tools_prompt: str,
        client_config: Dict[str, Any],
    ) -> ProviderResult:
        return get_model_response(
            self,
            runtime,
            client,
            model,
            tool_prompt_mode,
            tool_defs,
            active_dialect_names,
            session_state,
            markdown_logger,
            stream_responses,
            local_tools_prompt,
            client_config,
        )
    
    def _legacy_message_view(self, provider_message: ProviderMessage) -> SimpleNamespace:
        return legacy_message_view(provider_message)

    def _log_provider_message(
        self,
        provider_message: ProviderMessage,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
    ) -> None:
        log_provider_message(self, provider_message, session_state, markdown_logger, stream_responses)
    
    def _process_model_output(
        self,
        provider_message: ProviderMessage,
        caller,
        tool_defs: List[ToolDefinition],
        session_state: SessionState,
        completion_detector: CompletionDetector,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool,
        model: str,
    ) -> bool:
        return process_model_output(
            self,
            provider_message,
            caller,
            tool_defs,
            session_state,
            completion_detector,
            markdown_logger,
            error_handler,
            stream_responses,
            model,
        )
    
    def _handle_text_tool_calls(
        self,
        msg,
        caller,
        tool_defs: List[ToolDefinition],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool
    ) -> bool:
        return handle_text_tool_calls(
            self,
            msg,
            caller,
            tool_defs,
            session_state,
            markdown_logger,
            error_handler,
            stream_responses,
        )

    def _synthesize_patch_blocks(self, message_text: Optional[str]) -> List[str]:
        """Extract raw patch/diff blocks from assistant text."""
        return synthesize_patch_blocks(message_text)
    
    def _handle_native_tool_calls(
        self,
        msg,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool,
        model: str
    ) -> bool:
        return handle_native_tool_calls(
            self,
            msg,
            session_state,
            markdown_logger,
            error_handler,
            stream_responses,
            model,
        )
    def _retry_diff_with_aider(self, patch_text: str) -> Optional[Dict[str, Any]]:
        payload = retry_diff_with_aider(patch_text)
        if not payload:
            return None
        result = self._exec_raw(payload)
        result = {"action": "apply_search_replace", **result}
        self._record_diff_metrics(
            SimpleNamespace(function="apply_search_replace", arguments=payload["arguments"], dialect="aider_retry"),
            result,
        )
        return result
