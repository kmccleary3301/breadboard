from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import time
import uuid
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict

import ray

from adaptive_iter import decode_adaptive_iterable

from kylecode.sandbox_v2 import DevSandboxV2
from kylecode.opencode_patch import PatchParseError, parse_opencode_patch, to_unified_diff
from kylecode.sandbox_virtualized import SandboxFactory, DeploymentMode
from .core.core import ToolDefinition, ToolParameter
from .dialects.pythonic02 import Pythonic02Dialect
from .dialects.pythonic_inline import PythonicInlineDialect
from .dialects.aider_diff import AiderDiffDialect
from .dialects.unified_diff import UnifiedDiffDialect
from .dialects.opencode_patch import OpenCodePatchDialect
from .dialects.yaml_command import YAMLCommandDialect
from .execution.composite import CompositeToolCaller
from .dialects.bash_block import BashBlockDialect
from .execution.dialect_manager import DialectManager
from .execution.enhanced_executor import EnhancedToolExecutor
from .execution.agent_executor import AgentToolExecutor
from .compilation.tool_yaml_loader import load_yaml_tools
from .compilation.system_prompt_compiler import get_compiler
from .provider_ir import IRFinish, IRDeltaEvent
from .compilation.enhanced_config_validator import EnhancedConfigValidator
from .provider_routing import provider_router
from .provider_adapters import provider_adapter_manager
from .provider_runtime import (
    provider_registry,
    ProviderRuntimeContext,
    ProviderResult,
    ProviderMessage,
    ProviderRuntimeError,
)
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
from .provider_capability_probe import ProviderCapabilityProbeRunner
from .provider_health import RouteHealthManager
from .provider_normalizer import normalize_provider_result
from .provider_metrics import ProviderMetricsCollector
from .todo import TodoManager, TodoStore
from .todo.store import TODO_OPEN_STATUSES

ZERO_TOOL_WARN_TURNS = 2
ZERO_TOOL_ABORT_TURNS = 4
COMPLETION_GUARD_ABORT_THRESHOLD = 2


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


@ray.remote
class OpenAIConductor:
    def __init__(self, workspace: str, image: str = "python-dev:latest", config: Optional[Dict[str, Any]] = None, *, local_mode: bool = False) -> None:
        # Resolve workspace from v2 config if provided
        cfg_ws = None
        if config:
            try:
                cfg_ws = (config.get("workspace", {}) or {}).get("root")
            except Exception:
                cfg_ws = None
        effective_ws = cfg_ws or workspace
        # Ensure we start from a clean workspace so each run works from a fresh clone
        effective_ws = str(self._prepare_workspace(effective_ws))
        # Choose virtualization mode based on workspace.mirror.mode
        mirror_cfg = ((config or {}).get("workspace", {}) or {}).get("mirror", {})
        mirror_mode = str(mirror_cfg.get("mode", "development")).lower()
        use_virtualized = mirror_cfg.get("enabled", True)
        
        
        print("WORKSPACE", effective_ws)
        print("MIRROR CFG", mirror_cfg)
        
        # Local-host fallback unless explicitly using docker by env
        self.local_mode = bool(local_mode)
        self._ray_get = ray.get if not self.local_mode else identity_get

        self.using_virtualized = bool(use_virtualized) and not self.local_mode
        if self.using_virtualized:
            try:
                mode = DeploymentMode(mirror_mode) if mirror_mode in (m.value for m in DeploymentMode) else DeploymentMode.DEVELOPMENT
            except Exception:
                mode = DeploymentMode.DEVELOPMENT
            factory = SandboxFactory()
            vsb, session_id = factory.create_sandbox(mode, {"runtime": {"image": image}, "workspace": effective_ws})
            self.sandbox = vsb
        elif self.local_mode:
            dev_cls = DevSandboxV2.__ray_metadata__.modified_class
            dev_impl = dev_cls(image=image, session_id=f"local-{uuid.uuid4()}", workspace=effective_ws, lsp_actor=None)
            self.sandbox = LocalActorProxy(dev_impl)
        else:
            self.sandbox = DevSandboxV2.options(name=f"oa-sb-{uuid.uuid4()}").remote(image=image, workspace=effective_ws)

        self.workspace = effective_ws
        self.image = image
        self.config = config or {}
        self._initialize_guardrail_config()
        
        # Initialize components
        self._initialize_dialect_manager()
        self._initialize_yaml_tools()
        self._initialize_config_validator()
        self._initialize_enhanced_executor()

        # Initialize Logging v2 (safe no-op if disabled/missing config)
        self.logger_v2 = LoggerV2Manager(self.config)
        try:
            # Use only workspace basename for logging session id to avoid path tokens
            run_dir = self.logger_v2.start_run(session_id=os.path.basename(os.path.normpath(self.workspace)))
        except Exception:
            run_dir = ""
        self.api_recorder = APIRequestRecorder(self.logger_v2)
        self.structured_request_recorder = StructuredRequestRecorder(self.logger_v2)
        self.prompt_logger = PromptArtifactLogger(self.logger_v2)
        self.md_writer = MarkdownTranscriptWriter()
        self.provider_logger = ProviderNativeLogger(self.logger_v2)
        self.capability_probe_runner = ProviderCapabilityProbeRunner(
            provider_router,
            provider_registry,
            self.logger_v2,
            None,
        )
        self._capability_probes_ran = False
        self.route_health = RouteHealthManager()
        self._current_route_id: Optional[str] = None
        self.provider_metrics = ProviderMetricsCollector()
        self._reward_metrics_sqlite: Optional[RewardMetricsSQLiteWriter] = None
        self._todo_metrics_sqlite: Optional[TodoMetricsSQLiteWriter] = None
        
        # Initialize extracted modules
        self.message_formatter = MessageFormatter(self.workspace)
        self.agent_executor = AgentToolExecutor(self.config, self.workspace)
        self.agent_executor.set_enhanced_executor(self.enhanced_executor)
        self.agent_executor.set_config_validator(self.config_validator)
        self._last_runtime_latency: Optional[float] = None
        self._last_html_detected: bool = False

    def _initialize_guardrail_config(self) -> None:
        """Initialize guardrail toggles from the configuration."""
        loop_cfg = (self.config.get("loop", {}) or {})
        guardrails_cfg = (loop_cfg.get("guardrails", {}) or {})
        zero_tool_cfg = guardrails_cfg.get("zero_tool_watchdog", {}) or {}

        def _coerce_int(value: Any, default: int) -> int:
            try:
                if value is None:
                    return default
                return int(value)
            except Exception:
                return default

        self.zero_tool_warn_turns = _coerce_int(zero_tool_cfg.get("warn_after_turns"), ZERO_TOOL_WARN_TURNS)
        self.zero_tool_abort_turns = _coerce_int(zero_tool_cfg.get("abort_after_turns"), ZERO_TOOL_ABORT_TURNS)
        self.zero_tool_emit_event = bool(zero_tool_cfg.get("emit_event", True))

        enhanced_cfg = (self.config.get("enhanced_tools", {}) or {})
        diff_policy_cfg = (enhanced_cfg.get("diff_policy", {}) or {})
        patch_cfg = (diff_policy_cfg.get("patch_splitting", {}) or {})
        policy = str(patch_cfg.get("policy", "auto_split") or "").strip().lower()
        max_files = patch_cfg.get("max_files_per_patch")
        self.patch_splitting_policy = {
            "mode": policy if policy in {"auto_split", "reject_multi_file"} else "auto_split",
            "max_files": max_files if isinstance(max_files, int) and max_files > 0 else 1,
            "message": patch_cfg.get("validation_message"),
        }

    def _prepare_workspace(self, workspace: str) -> Path:
        """Ensure the workspace directory exists and is empty before use."""
        try:
            path = Path(workspace)
        except Exception:
            path = Path(str(workspace))
        try:
            path = path if path.is_absolute() else (Path.cwd() / path)
            path = path.resolve()
        except Exception:
            # Fallback: use absolute() best-effort without strict resolution
            try:
                path = path.absolute()
            except Exception:
                pass
        try:
            if path.exists():
                shutil.rmtree(path)
        except Exception:
            # If cleanup fails we still attempt to proceed with a fresh directory
            pass
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _initialize_dialect_manager(self):
        """Initialize enhanced tools and configuration validator"""
        self.dialect_manager = DialectManager(self.config)

    def _get_model_routing_preferences(self, route_id: Optional[str]) -> Dict[str, Any]:
        """Return routing preferences for a given configured route."""
        defaults = {
            "disable_stream_on_probe_failure": True,
            "disable_native_tools_on_probe_failure": True,
            "fallback_models": [],
        }
        providers_cfg = (self.config.get("providers") or {})
        if not route_id:
            provider_defaults = providers_cfg.get("routing") or providers_cfg.get("routing_preferences") or {}
            prefs = dict(defaults)
            for key, value in (provider_defaults.items() if isinstance(provider_defaults, dict) else []):
                if value is not None:
                    prefs[key] = value
            return prefs

        models_cfg = providers_cfg.get("models") or []
        for entry in models_cfg:
            if entry.get("id") == route_id:
                routing_cfg = entry.get("routing") or entry.get("routing_preferences") or {}
                prefs = dict(defaults)
                for key, value in (routing_cfg.items() if isinstance(routing_cfg, dict) else []):
                    if value is not None:
                        prefs[key] = value
                return prefs

        provider_defaults = providers_cfg.get("routing") or providers_cfg.get("routing_preferences") or {}
        prefs = dict(defaults)
        for key, value in (provider_defaults.items() if isinstance(provider_defaults, dict) else []):
            if value is not None:
                prefs[key] = value
        return prefs

    def _get_capability_probe_result(
        self,
        session_state: SessionState,
        route_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Fetch previously recorded capability probe result for a route."""
        if not route_id:
            return None
        try:
            probes = session_state.get_provider_metadata("capability_probes", [])
        except Exception:
            probes = []
        if not probes:
            return None
        for entry in probes:
            if entry.get("model_id") == route_id:
                return entry
        return None

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
        try:
            session_state.add_transcript_entry({tag: payload})
        except Exception:
            pass
        try:
            markdown_logger.log_system_message(message)
        except Exception:
            pass
        try:
            if getattr(self.logger_v2, "run_dir", None):
                self.logger_v2.append_text(
                    "conversation/conversation.md",
                    self.md_writer.system(message),
                )
        except Exception:
            pass
        cursor: str
        if isinstance(turn_index, int) and turn_index >= 0:
            cursor = f"turn_{turn_index}:{tag}"
        elif isinstance(turn_index, str):
            cursor = f"{turn_index}:{tag}"
        else:
            cursor = tag
        try:
            session_state.add_ir_event(
                IRDeltaEvent(
                    cursor=cursor,
                    type="reasoning_meta",
                    payload=dict(payload, message=message),
                )
            )
        except Exception:
            pass

    def _record_stream_policy_metadata(self, session_state: SessionState, policy: Dict[str, Any]) -> None:
        """Store stream policy decisions in session metadata."""
        try:
            history = session_state.get_provider_metadata("stream_policy_history", [])
        except Exception:
            history = []
        try:
            if isinstance(history, list):
                updated_history = list(history)
            else:
                updated_history = [history] if history else []
            updated_history.append(policy)
            session_state.set_provider_metadata("stream_policy_history", updated_history)
            session_state.set_provider_metadata("last_stream_policy", policy)
        except Exception:
            pass

    def _apply_capability_tool_overrides(
        self,
        provider_tools_cfg: Dict[str, Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> Dict[str, Any]:
        """Adjust native tool usage based on capability probe outputs."""
        route_id = getattr(self, "_current_route_id", None)
        routing_prefs = self._get_model_routing_preferences(route_id)
        if not routing_prefs.get("disable_native_tools_on_probe_failure", True):
            return provider_tools_cfg
        capability = self._get_capability_probe_result(session_state, route_id)
        if not capability or not capability.get("attempted"):
            return provider_tools_cfg
        if capability.get("tool_stream_success") is not False:
            return provider_tools_cfg
        # Respect explicit overrides favoring native tools when set to False already
        current_override = provider_tools_cfg.get("use_native")
        if current_override is False:
            return provider_tools_cfg
        updated_cfg = dict(provider_tools_cfg)
        updated_cfg["use_native"] = False
        payload = {
            "route": route_id,
            "capabilities": {
                "stream_success": capability.get("stream_success"),
                "tool_stream_success": capability.get("tool_stream_success"),
                "json_mode_success": capability.get("json_mode_success"),
            },
            "reason": "capability_probe_tool_failure",
        }
        message = (
            f"[tool-policy] Disabled native tool usage for route '{route_id}' after capability probe failure."
        )
        self.provider_metrics.add_tool_override(
            route=route_id,
            reason="capability_probe_tool_failure",
        )
        self._log_routing_event(
            session_state,
            markdown_logger,
            turn_index="setup",
            tag="tool_policy",
            message=message,
            payload=payload,
        )
        try:
            history = session_state.get_provider_metadata("tool_policy_history", [])
            if isinstance(history, list):
                updated_history = list(history)
            else:
                updated_history = [history] if history else []
            updated_history.append(
                {
                    "route": route_id,
                    "reason": "capability_probe_tool_failure",
                    "ts": time.time(),
                }
            )
            session_state.set_provider_metadata("tool_policy_history", updated_history)
            session_state.set_provider_metadata("completion_sentinel_required", True)
        except Exception:
            pass
        return updated_cfg

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
            add_candidate("openrouter/openai/gpt-4o-mini")
            add_candidate("gpt-4o-mini")
        elif provider_id == "openai":
            add_candidate("gpt-4o-mini")

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
        try:
            loop_ts = (self.config.get("loop", {}) or {}).get("turn_strategy") or {}
            if loop_ts:
                self.config.setdefault("turn_strategy", {})
                self.config["turn_strategy"].update(loop_ts)
        except Exception:
            pass

    def _prepare_concurrency_policy(self) -> Dict[str, Any]:
        """Prepare a simple concurrency policy map from config for testing/logging."""
        policy = self.config.get("concurrency", {}) or {}
        groups = policy.get("groups", []) or []
        tool_to_group: Dict[str, Dict[str, Any]] = {}
        barrier_functions: set[str] = set()
        try:
            for g in groups:
                match_tools = g.get("match_tools", []) or []
                for tn in match_tools:
                    tool_to_group[str(tn)] = {
                        "name": g.get("name"),
                        "max_parallel": int(g.get("max_parallel", 1) or 1),
                        "barrier_after": g.get("barrier_after"),
                    }
                if g.get("barrier_after"):
                    barrier_functions.add(str(g.get("barrier_after")))
        except Exception:
            pass
        return {"tool_to_group": tool_to_group, "barrier_functions": barrier_functions}
    
    def _initialize_yaml_tools(self):
        """Load YAML-defined tools and manipulations map for routing/validation"""
        try:
            # Allow overriding the YAML tool defs directory via config
            tools_cfg = (self.config.get("tools", {}) or {})
            defs_dir = tools_cfg.get("defs_dir") or "implementations/tools/defs"
            overlays = (tools_cfg.get("overlays") or [])
            aliases = (tools_cfg.get("aliases") or {})
            loaded = load_yaml_tools(defs_dir, overlays=overlays, aliases=aliases)
            self.yaml_tools = loaded.tools
            self.yaml_tool_manipulations = loaded.manipulations_by_id
        except Exception:
            self.yaml_tools = []
            self.yaml_tool_manipulations = {}
        # Expose manipulations map to downstream components via config
        try:
            self.config.setdefault("yaml_tool_manipulations", self.yaml_tool_manipulations)
        except Exception:
            pass
    
    def _initialize_config_validator(self):
        """Initialize enhanced configuration validator"""
        enhanced_tools_config = self.config.get("tools", {}).get("registry")
        if enhanced_tools_config and os.path.exists("implementations/tools/enhanced_tools.yaml"):
            try:
                self.config_validator = EnhancedConfigValidator("implementations/tools/enhanced_tools.yaml")
            except Exception:
                self.config_validator = None
        else:
            self.config_validator = None
        
        # Initialize sequential executor for design decision implementation
        self.sequential_executor = None
    
    def _initialize_enhanced_executor(self):
        """Initialize enhanced executor if requested"""
        enhanced_config = self.config.get("enhanced_tools", {})
        # If LSP integration is requested, wrap sandbox even if enhanced executor not enabled
        if enhanced_config.get("lsp_integration", {}).get("enabled", False):
            try:
                from kylecode.sandbox_lsp_integration import LSPEnhancedSandbox
                self.sandbox = LSPEnhancedSandbox.remote(self.sandbox, self.workspace)
            except ImportError:
                pass
        if enhanced_config.get("enabled", False) or enhanced_config.get("lsp_integration", {}).get("enabled", False):
            # Wrap sandbox with LSP capabilities if requested
            sandbox_for_executor = self.sandbox
            if enhanced_config.get("lsp_integration", {}).get("enabled", False):
                try:
                    from kylecode.sandbox_lsp_integration import LSPEnhancedSandbox
                    sandbox_for_executor = LSPEnhancedSandbox.remote(self.sandbox, self.workspace)
                except ImportError:
                    pass  # LSP integration not available, use regular sandbox
            
            self.enhanced_executor = EnhancedToolExecutor(
                sandbox=sandbox_for_executor,
                config=self.config
            )
        else:
            self.enhanced_executor = None

    def _tool_defs_from_yaml(self) -> Optional[List[ToolDefinition]]:
        """Convert YAML EnhancedToolDefinitions to legacy ToolDefinition list for prompts."""
        try:
            if not getattr(self, "yaml_tools", None):
                return None

            # Prefer Schema V2 registry.include if present; else fall back to legacy tools.enabled
            tools_cfg = (self.config.get("tools", {}) or {})
            registry_cfg = (tools_cfg.get("registry", {}) or {})
            include_list = list(registry_cfg.get("include") or [])
            legacy_enabled = (tools_cfg.get("enabled", {}) or {})

            def _is_included(name: str) -> bool:
                if include_list:
                    return name in include_list
                if legacy_enabled:
                    return bool(legacy_enabled.get(name, False))
                # If neither specified, include everything
                return True

            converted: List[ToolDefinition] = []
            for t in self.yaml_tools:
                name = getattr(t, "name", None)
                if not name or not _is_included(name):
                    continue
                params = []
                for p in getattr(t, "parameters", []) or []:
                    params.append(ToolParameter(name=p.name, type=p.type, description=p.description, default=p.default))
                converted.append(
                    ToolDefinition(
                        type_id=getattr(t, "type_id", "python"),
                        name=name,
                        description=getattr(t, "description", ""),
                        parameters=params,
                        blocking=bool(getattr(t, "blocking", False)),
                    )
                )
            return converted if converted else None
        except Exception:
            return None

    def _completion_tool_config_enabled(self) -> bool:
        """Determine whether configuration allows the completion tool."""
        tools_cfg = (self.config.get("tools", {}) or {})
        # Direct enabled/disabled flag
        direct_flag = tools_cfg.get("mark_task_complete")
        if isinstance(direct_flag, bool):
            if not direct_flag:
                return False
            # fall through to other checks if True to honour excludes

        enabled_cfg = tools_cfg.get("enabled")
        if isinstance(enabled_cfg, dict):
            if enabled_cfg.get("mark_task_complete") is False:
                return False

        registry_cfg = (tools_cfg.get("registry", {}) or {})
        include_list = registry_cfg.get("include")
        if include_list:
            try:
                include_names = {str(name) for name in include_list}
            except Exception:
                include_names = set()
            if include_names and "mark_task_complete" not in include_names:
                return False

        exclude_list = registry_cfg.get("exclude")
        if exclude_list:
            try:
                exclude_names = {str(name) for name in exclude_list}
            except Exception:
                exclude_names = set()
            if "mark_task_complete" in exclude_names:
                return False

        return True

    def _ensure_completion_tool(self, tool_defs: List[ToolDefinition]) -> List[ToolDefinition]:
        """Ensure mark_task_complete tool is present in the tool definitions."""
        for tool in tool_defs:
            if getattr(tool, "name", None) == "mark_task_complete":
                return tool_defs
        completion_tool = ToolDefinition(
            type_id="python",
            name="mark_task_complete",
            description="Signal that the task is fully complete. When called, the agent will stop the run.",
            parameters=[],
            blocking=True,
        )
        return tool_defs + [completion_tool]

    @staticmethod
    def _is_read_only_tool(tool_name: str) -> bool:
        """Classify tools that only inspect state (used for completion heuristics)."""
        if not tool_name:
            return False
        read_only_tools = {
            "list_dir",
            "read_file",
            "describe_file",
            "preview_file",
            "inspect_workspace",
        }
        return tool_name in read_only_tools

    @staticmethod
    def _normalize_assistant_text(text: Optional[str]) -> str:
        if not text:
            return ""
        return " ".join(text.strip().split()).lower()

    def _record_lsp_reward_metrics(
        self,
        session_state: SessionState,
        turn_index: int,
    ) -> None:
        """Record LED/SBS metrics based on latest LSP diagnostics."""
        try:
            enhanced_executor = getattr(self.agent_executor, "enhanced_executor", None)
            if not enhanced_executor or not hasattr(enhanced_executor, "consume_lsp_metrics"):
                return
            summary = enhanced_executor.consume_lsp_metrics()
            if not summary:
                return
            led_errors = int(summary.get("led_errors", 0))
            files_with_issues = int(summary.get("sbs_files_with_issues", 0))
            session_state.add_reward_metric(turn_index, "LED", 1.0 if led_errors == 0 else 0.0)
            session_state.add_reward_metric(turn_index, "SBS", float(files_with_issues))
        except Exception:
            pass

    def _record_test_reward_metric(
        self,
        session_state: SessionState,
        turn_index: int,
        success_value: Optional[float],
    ) -> None:
        if success_value is None:
            return
        try:
            session_state.add_reward_metric(turn_index, "TPF_DELTA", float(success_value))
        except Exception:
            pass

    def _should_require_build_guard(self, user_prompt: str) -> bool:
        guard_cfg = (self.config.get("completion", {}) or {}).get("build_guard", {})
        explicit = guard_cfg.get("enabled")
        if explicit is True:
            return True
        if explicit is False:
            return False
        keywords = guard_cfg.get("keywords") or [
            "protofs",
            "filesystem.fs",
            "proto file system",
            "filesystem implementation",
        ]
        prompt_text = (user_prompt or "").lower()
        return any(keyword in prompt_text for keyword in keywords)

    def _todo_config(self) -> Dict[str, Any]:
        features = (self.config.get("features") or {}).get("todos") or {}
        enabled = bool(features.get("enabled"))
        return {
            "enabled": enabled,
            "strict": bool(features.get("strict", True)) if enabled else False,
            "reset_streak_on_todo": bool(features.get("reset_streak_on_todo", False)) if enabled else False,
            "allow_low_priority_open": bool(features.get("allow_low_priority_open", False)) if enabled else False,
        }

    def _todo_guard_preflight(self, session_state: SessionState, parsed_call, current_mode: Optional[str]) -> Optional[str]:
        cfg = self._todo_config()
        if not (cfg["enabled"] and cfg["strict"]):
            return None
        manager = session_state.get_todo_manager()
        if not manager:
            return None
        snapshot = manager.snapshot()
        todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
        has_todos = bool(todos)
        fn = getattr(parsed_call, "function", "") or ""
        if current_mode == "plan":
            side_effect_tools = {
                "apply_unified_patch",
                "apply_search_replace",
                "write",
                "create_file",
                "create_file_from_block",
                "run_shell",
                "write_text",
            }
            if not has_todos and fn in side_effect_tools:
                return "Plan mode requires creating at least one todo via `todo.create` before using edit or bash tools."
        return None

    def _emit_todo_guard_violation(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        reason: str,
        *,
        blocked_call: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Emit standard validation feedback for plan-mode TODO guard violations."""
        warning = (
            "<VALIDATION_ERROR>\n"
            f"{reason}\n"
            "</VALIDATION_ERROR>"
        )
        try:
            session_state.add_message({"role": "user", "content": warning}, to_provider=True)
        except Exception:
            pass
        try:
            markdown_logger.log_user_message(warning)
        except Exception:
            pass
        try:
            session_state.increment_guardrail_counter("todo_plan_violation")
        except Exception:
            pass
        try:
            session_state.add_transcript_entry({
                "todo_guard": {
                    "function": getattr(blocked_call, "function", "") if blocked_call else "",
                    "reason": reason,
                }
            })
        except Exception:
            pass
        if not blocked_call:
            return None
        return {
            "fn": getattr(blocked_call, "function", ""),
            "provider_fn": getattr(blocked_call, "provider_name", getattr(blocked_call, "function", "")),
            "out": {"error": reason, "validation_failed": True},
            "args": getattr(blocked_call, "arguments", {}),
            "call_id": getattr(blocked_call, "call_id", None),
            "failed": True,
        }

    def _maybe_transition_plan_mode(self, session_state: SessionState, markdown_logger: Optional[MarkdownLogger] = None) -> None:
        """Advance from plan mode to the next mode once TODO prerequisites are satisfied."""
        current_mode = session_state.get_provider_metadata("current_mode")
        if current_mode != "plan":
            return

        manager = session_state.get_todo_manager()
        snapshot = manager.snapshot() if manager else None
        todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
        if not todos:
            return

        try:
            plan_turns = int(session_state.get_provider_metadata("plan_turns") or 0)
        except Exception:
            plan_turns = 0
        plan_turns += 1
        session_state.set_provider_metadata("plan_turns", plan_turns)

        try:
            plan_limit = int(session_state.get_provider_metadata("plan_turn_limit") or 0)
        except Exception:
            plan_limit = 0

        if plan_limit and plan_turns < plan_limit:
            return

        features_cfg = self.config.setdefault("features", {})
        if features_cfg.get("plan", True):
            features_cfg["plan"] = False
            session_state.set_provider_metadata("plan_mode_disabled", True)
            try:
                session_state.add_transcript_entry({
                    "mode_transition": {
                        "from": "plan",
                        "to": "build",
                        "reason": "plan_turn_limit" if plan_limit else "plan_disabled",
                        "plan_turns": plan_turns,
                    }
                })
            except Exception:
                pass
            if markdown_logger:
                try:
                    markdown_logger.log_system_message("[mode] Transitioning from plan mode to build mode.")
                except Exception:
                    pass

        next_mode = self._resolve_active_mode()
        session_state.set_provider_metadata("current_mode", next_mode)

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
        summary = getattr(session_state, "tool_usage_summary", {})
        total_calls = int(summary.get("total_calls") or 0)
        if total_calls <= 0:
            return False, "No tool usage detected yet. Use the available tools (read/diff/bash) to make concrete progress before declaring completion."

        if session_state.get_provider_metadata("requires_build_guard", False):
            if int(summary.get("successful_writes") or 0) <= 0:
                return False, "No successful code edits recorded. Apply at least one diff or file creation before completing."
            if int(summary.get("run_shell_calls") or 0) <= 0:
                return False, "No shell commands executed. Compile or run the test suite before completing."
            test_commands = int(summary.get("test_commands") or 0)
            successful_tests = int(summary.get("successful_tests") or 0)
            if test_commands > 0 and successful_tests <= 0:
                return False, "Tests were attempted but none succeeded. Resolve test failures before completion."

        todos_cfg = self._todo_config()
        if todos_cfg["enabled"] and todos_cfg["strict"]:
            manager = session_state.get_todo_manager()
            if manager and manager.has_open_items():
                snapshot = manager.snapshot()
                open_titles = [
                    todo.get("title", "unnamed todo")
                    for todo in snapshot.get("todos", [])
                    if todo.get("status") in TODO_OPEN_STATUSES
                ][:3]
                reason = "Outstanding todos must be completed or canceled before finishing."
                if open_titles:
                    reason += " Pending items: " + "; ".join(open_titles)
                return False, reason

        return True, None

    def _emit_completion_guard_feedback(
        self,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        reason: str,
        stream_responses: bool,
    ) -> bool:
        failure_count = session_state.increment_guard_failures()
        remaining = max(COMPLETION_GUARD_ABORT_THRESHOLD - failure_count, 0)
        advisory_lines = [
            "<VALIDATION_ERROR>",
            reason,
        ]
        if remaining > 0:
            advisory_lines.append(f"Completion guard engaged. Provide concrete file edits and successful tests. Warnings remaining before abort: {remaining}.")
        else:
            advisory_lines.append("Completion guard engaged repeatedly. The run will now terminate to avoid wasting budget.")
        advisory_lines.append("</VALIDATION_ERROR>")
        advisory = "\n".join(advisory_lines)

        session_state.add_message({"role": "user", "content": advisory}, to_provider=True)
        try:
            markdown_logger.log_user_message(advisory)
        except Exception:
            pass
        if stream_responses:
            print(f"[guard] {reason}")

        abort = failure_count >= COMPLETION_GUARD_ABORT_THRESHOLD
        if abort:
            session_state.set_provider_metadata("completion_guard_abort", True)
        return abort

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
        if not text:
            return 0
        import re

        pattern = re.compile(r"^@@", re.MULTILINE)
        matches = pattern.findall(text)
        # If diff starts with @@ without newline prefix, ensure counted
        if text.lstrip().startswith("@@") and (not matches or text[0] != "\n"):
            # matches count already includes starting @@ thanks to re MULTILINE
            pass
        return len(matches)

    @staticmethod
    def _split_patch_blocks(patch_text: str) -> List[str]:
        if not patch_text:
            return []
        lines = patch_text.splitlines()
        blocks: List[str] = []
        current: List[str] = []
        inside_sentinel = False

        for line in lines:
            if line.startswith("*** Begin Patch"):
                if current:
                    blocks.append("\n".join(current).strip() + "\n")
                    current = []
                inside_sentinel = True
            if inside_sentinel or line.startswith("*** Begin Patch"):
                current.append(line)
            if line.startswith("*** End Patch"):
                inside_sentinel = False
                blocks.append("\n".join(current).strip() + "\n")
                current = []

        if current:
            blocks.append("\n".join(current).strip() + "\n")

        if blocks:
            return [block for block in blocks if block.strip()]

        # Fallback for git-style diff without sentinel markers
        segments: List[str] = []
        current = []
        for line in lines:
            if line.startswith("diff --git "):
                if current:
                    segments.append("\n".join(current).strip() + "\n")
                    current = []
            current.append(line)
        if current:
            segments.append("\n".join(current).strip() + "\n")
        return [seg for seg in segments if seg.strip()]

    def _fetch_workspace_text(self, path: str) -> str:
        """Best-effort read helper for converting OpenCode patches."""
        target = self._normalize_workspace_path(path)
        try:
            result = self._ray_get(self.sandbox.read_text.remote(target))
        except PatchParseError:
            raise
        except Exception as exc:
            raise PatchParseError(f"Failed to read {path}: {exc}") from exc

        if isinstance(result, dict) and "content" in result:
            return str(result.get("content") or "")
        if isinstance(result, str):
            return result
        raise PatchParseError(f"Unexpected sandbox read result for {path}")

    def _convert_patch_to_unified(self, patch_text: str) -> Optional[str]:
        """Convert OpenCode patch text to a unified diff for legacy executors."""
        try:
            return to_unified_diff(patch_text, self._fetch_workspace_text)
        except PatchParseError:
            return None
        except Exception:
            return None

    def _apply_patch_operations_direct(self, patch_text: str) -> Optional[Dict[str, Any]]:
        """Fallback handler that directly applies simple add-file operations."""
        try:
            operations = parse_opencode_patch(patch_text)
        except Exception:
            return None

        applied: List[str] = []
        for op in operations:
            if getattr(op, "kind", None) != "add":
                return None
            file_path = getattr(op, "file_path", None)
            if not file_path:
                return None
            content = getattr(op, "content", None)
            if content is None:
                content = ""
            target = self._normalize_workspace_path(str(file_path))
            try:
                self._ray_get(self.sandbox.write_text.remote(target, str(content)))
            except Exception:
                return None
            applied.append(str(file_path))

        if not applied:
            return None
        return {"ok": True, "data": {"applied": applied, "manual": True}}

    def _expand_multi_file_patches(
        self,
        parsed_calls: List[Any],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
    ) -> List[Any]:
        if not parsed_calls:
            return parsed_calls

        expanded: List[Any] = []
        policy = getattr(self, "patch_splitting_policy", {}) or {}
        policy_mode = policy.get("mode", "auto_split")
        max_files = policy.get("max_files", 1)
        validation_message = policy.get("message")
        violation_strategy = str(policy.get("on_violation", "reject") or "").lower()
        violation_logged = False

        additional_calls = 0

        for call in parsed_calls:
            if getattr(call, "function", None) != "apply_unified_patch":
                expanded.append(call)
                continue

            args = getattr(call, "arguments", {}) or {}
            patch_text = str(args.get("patch", ""))
            chunks = self._split_patch_blocks(patch_text)
            if len(chunks) <= 1:
                expanded.append(call)
                continue

            chunk_limit = max_files if isinstance(max_files, int) and max_files > 0 else 1
            exceeds_limit = len(chunks) > chunk_limit

            if policy_mode == "reject_multi_file" and exceeds_limit:
                allow_split = violation_strategy in {"auto_split", "warn_split", "warn_and_split"}
                self._emit_patch_policy_violation(
                    session_state,
                    markdown_logger,
                    chunk_count=len(chunks),
                    policy_mode=policy_mode,
                    validation_message=validation_message,
                )
                violation_logged = True
                if not allow_split:
                    continue

            additional_calls += len(chunks) - 1
            for chunk in chunks:
                new_args = dict(args)
                new_args["patch"] = chunk if chunk.endswith("\n") else chunk + "\n"
                call_payload = vars(call).copy()
                call_payload["arguments"] = new_args
                expanded.append(SimpleNamespace(**call_payload))

        if violation_logged:
            try:
                session_state.set_provider_metadata("patch_policy_violation", True)
            except Exception:
                pass
        if additional_calls > 0:
            try:
                session_state.add_transcript_entry({
                    "patch_auto_split": {
                        "original_calls": len(parsed_calls),
                        "expanded_calls": len(expanded),
                        "additional_calls": additional_calls,
                    }
                })
                session_state.set_provider_metadata("auto_split_patch", True)
            except Exception:
                pass
            note = (
                f"[tooling] Auto-split multi-file patch into {len(expanded)} apply_unified_patch calls "
                f"(added {additional_calls} extra operations)."
            )
            try:
                markdown_logger.log_system_message(note)
            except Exception:
                pass

        return expanded

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
        message = validation_message or (
            "<VALIDATION_ERROR>\n"
            "Multi-file patches are disabled in this configuration. Submit one apply_unified_patch per file.\n"
            "</VALIDATION_ERROR>"
        )
        try:
            session_state.add_message({"role": "user", "content": message}, to_provider=True)
        except Exception:
            pass
        try:
            markdown_logger.log_user_message(message)
        except Exception:
            pass
        try:
            session_state.increment_guardrail_counter("patch_policy_violations")
        except Exception:
            pass
        try:
            session_state.add_transcript_entry({
                "patch_policy_violation": {
                    "chunks": chunk_count,
                    "policy": policy_mode,
                }
            })
        except Exception:
            pass

    def _validate_structural_artifacts(self, session_state: SessionState) -> List[str]:
        """Perform lightweight structural checks on key workspace artifacts."""
        if not session_state.get_provider_metadata("requires_build_guard", False):
            return []

        issues: List[str] = []
        workspace_root = Path(self.workspace)

        def _read_text(path: Path) -> Optional[str]:
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return None

        filesystem_h = workspace_root / "filesystem.h"
        filesystem_c = workspace_root / "filesystem.c"
        test_c = workspace_root / "test_filesystem.c"

        header_text = _read_text(filesystem_h)
        if header_text is None:
            issues.append("Missing required header file filesystem.h.")
        else:
            normalized = header_text.lower()
            has_guard = "#ifndef" in normalized or "#pragma once" in normalized
            if not has_guard:
                issues.append("filesystem.h missing include guard (#ifndef or #pragma once).")
            if '#include "filesystem.h"' in header_text:
                issues.append("filesystem.h includes itself; header appears to embed implementation code.")

        source_text = _read_text(filesystem_c)
        if source_text is None:
            issues.append("Missing required implementation file filesystem.c.")
        else:
            if '#include "filesystem.h"' not in source_text:
                issues.append('filesystem.c should include "filesystem.h" to stay in sync with the header.')

        test_text = _read_text(test_c)
        if test_text is None:
            issues.append("Missing test harness test_filesystem.c.")
        else:
            stripped_lines = [
                line.strip()
                for line in test_text.splitlines()
                if line.strip() and not line.strip().startswith("//")
            ]
            if not stripped_lines:
                issues.append("test_filesystem.c is empty.")
            else:
                first_token = stripped_lines[0]
                if not first_token.startswith("#include"):
                    issues.append(f"test_filesystem.c should start with #include; found '{first_token}'.")

        return issues

    def _record_diff_metrics(
        self,
        tool_call: Any,
        result: Dict[str, Any],
        *,
        session_state: Optional[SessionState] = None,
        turn_index: Optional[int] = None,
    ) -> None:
        function = getattr(tool_call, "function", "")
        dialect = getattr(tool_call, "dialect", None) or "unknown"
        pas: Optional[float] = None
        hmr: Optional[float] = None

        if function == "apply_unified_patch":
            patch_text = ""
            try:
                patch_text = str(tool_call.arguments.get("patch", ""))
            except Exception:
                patch_text = ""
            total_hunks = self._count_diff_hunks(patch_text)
            rejects = (result.get("data") or {}).get("rejects") if isinstance(result, dict) else None
            failed_hunks = 0
            if isinstance(rejects, dict):
                for rej_text in rejects.values():
                    failed_hunks += self._count_diff_hunks(rej_text)
            pas = 1.0 if result.get("ok") else 0.0
            if total_hunks <= 0:
                hmr = 1.0 if pas else 0.0
            else:
                matched = max(total_hunks - failed_hunks, 0)
                hmr = matched / total_hunks
        elif function == "apply_search_replace":
            replacements = None
            try:
                replacements = int(result.get("replacements", 0))
            except Exception:
                replacements = 0
            pas = 1.0 if replacements and replacements > 0 else 0.0
            # Treat HMR equivalent for lack of finer-grained data
            hmr = pas
        elif function == "create_file_from_block":
            pas = 1.0 if not result.get("error") else 0.0
            hmr = pas

        if pas is not None:
            try:
                self.provider_metrics.add_dialect_metric(
                    dialect=dialect,
                    tool=function,
                    pas=float(pas),
                    hmr=float(hmr) if hmr is not None else None,
                )
            except Exception:
                pass
            if session_state is not None and isinstance(turn_index, int):
                try:
                    session_state.add_reward_metric(turn_index, "PAS", float(pas))
                    if hmr is not None:
                        session_state.add_reward_metric(turn_index, "HMR", float(hmr))
                except Exception:
                    pass

    def create_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.write_text.remote(path, ""))

    def read_file(self, path: str) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.read_text.remote(path))

    def list_dir(self, path: str, depth: int = 1) -> Dict[str, Any]:
        # Always pass virtual paths when using VirtualizedSandbox; else pass normalized absolute
        if getattr(self, 'using_virtualized', False):
            return self._ray_get(self.sandbox.ls.remote(path, depth))
        target = self._normalize_workspace_path(str(path))
        return self._ray_get(self.sandbox.ls.remote(target, depth))

    def run_shell(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        stream = self._ray_get(self.sandbox.run.remote(command, timeout=timeout or 30, stream=True))
        if isinstance(stream, dict):
            return {
                "stdout": stream.get("stdout", "") or "",
                "stderr": stream.get("stderr", "") or "",
                "exit": stream.get("exit"),
            }
        # Decode adaptive-encoded list materialized; last element is {"exit": code}
        exit_obj = stream[-1] if isinstance(stream, list) and stream else {"exit": None}
        # Collect only string lines, drop adaptive markers (>>>>> ...)
        lines: list[str] = []
        if isinstance(stream, list):
            for x in stream[:-1]:
                if not isinstance(x, str):
                    continue
                if x.startswith(">>>>>"):
                    continue
                lines.append(x)
        stdout = "\n".join(lines)
        return {"stdout": stdout, "exit": exit_obj.get("exit")}

    def run_bash_opencode(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        *,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        OpenCode-compatible `bash` tool execution.

        OpenCode surfaces the raw combined stdout+stderr string as the tool result
        content, preserves trailing newlines, and appends truncation/timeout markers.
        """
        if timeout_ms is not None:
            try:
                timeout_ms = int(timeout_ms)
            except Exception:
                timeout_ms = None
        if timeout_ms is not None and timeout_ms < 0:
            msg = f"Invalid timeout value: {timeout_ms}. Timeout must be a positive number."
            return {"error": msg, "__mvi_text_output": msg}

        effective_timeout_ms = min(timeout_ms or 60_000, 10 * 60_000)
        timeout_s = max(0.0, float(effective_timeout_ms) / 1000.0)

        raw = self._ray_get(self.sandbox.run.remote(command, timeout=timeout_s, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        stdout = ""
        exit_code: Any = None
        if isinstance(decoded, dict):
            stdout = str(decoded.get("stdout") or "")
            exit_code = decoded.get("exit")
        elif isinstance(decoded, str):
            stdout = decoded
        else:
            try:
                stdout = json.dumps(decoded, ensure_ascii=False)
            except Exception:
                stdout = str(decoded)

        # Normalize workspace paths during replays so absolute-path outputs match
        # the recorded (strip-prefix) environment.
        try:
            replay_data = getattr(self, "_replay_session_data", None)
            strip_prefix = getattr(replay_data, "strip_prefix", "") if replay_data else ""
            if strip_prefix and isinstance(strip_prefix, str):
                ws = str(self.workspace or "")
                if ws and ws in stdout:
                    stdout = stdout.replace(ws, strip_prefix)
        except Exception:
            pass

        max_len = 30_000
        if len(stdout) > max_len:
            stdout = stdout[:max_len] + "\n\n(Output was truncated due to length limit)"

        if exit_code == -9:
            stdout += f"\n\n(Command timed out after {effective_timeout_ms} ms)"

        if description:
            # Preserve metadata fields for downstream logging even though the model
            # only receives `__mvi_text_output`.
            return {
                "stdout": stdout,
                "exit": exit_code,
                "description": description,
                "__mvi_text_output": stdout,
            }
        return {"stdout": stdout, "exit": exit_code, "__mvi_text_output": stdout}

    @staticmethod
    def _claude_slugify_path(path: str) -> str:
        raw = str(path or "")
        return re.sub(r"[^A-Za-z0-9]", "-", raw)

    def _claude_display_workspace_root(self) -> str:
        try:
            replay_data = getattr(self, "_replay_session_data", None)
            strip_prefix = getattr(replay_data, "strip_prefix", "") if replay_data else ""
            if isinstance(strip_prefix, str) and strip_prefix.strip():
                return strip_prefix
        except Exception:
            pass
        return str(self.workspace or "")

    def _claude_tasks_root(self) -> str:
        display_ws = self._claude_display_workspace_root()
        slug = self._claude_slugify_path(display_ws)
        return f"/tmp/claude/{slug}/tasks"

    def _claude_bash_rel_cwd(self) -> str:
        rel = getattr(self, "_claude_bash_rel_cwd_state", None)
        if isinstance(rel, str) and rel:
            return rel
        setattr(self, "_claude_bash_rel_cwd_state", ".")
        return "."

    def _claude_bash_set_rel_cwd_from_abs(self, abs_path: str) -> None:
        try:
            ws = str(self.workspace or "")
            if not ws:
                return
            abs_path = str(abs_path or "")
            if not abs_path:
                return
            resolved = Path(abs_path).resolve(strict=False)
            ws_path = Path(ws).resolve(strict=False)
            rel = resolved.relative_to(ws_path)
            rel_str = str(rel).replace("\\", "/")
            if not rel_str:
                rel_str = "."
            setattr(self, "_claude_bash_rel_cwd_state", rel_str)
        except Exception:
            return

    def run_bash_claude(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        *,
        description: Optional[str] = None,
        run_in_background: bool = False,
        expected_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Claude Code-compatible Bash tool.

        - supports run_in_background (returns shell_id, stores command/outputs in /tmp/claude/..)
        - supports chmodless cwd management and expected_output normalization
        - returns a text payload formatted exactly as Claude's tool_result
        """
        if timeout_ms is not None:
            try:
                timeout_ms = int(timeout_ms)
            except Exception:
                timeout_ms = None
        if timeout_ms is not None and timeout_ms < 0:
            msg = f"Invalid timeout value: {timeout_ms}. Timeout must be a positive number."
            return {"error": msg, "__mvi_text_output": msg}

        effective_timeout_ms = min(timeout_ms or 60_000, 10 * 60_000)
        timeout_s = max(0.0, float(effective_timeout_ms) / 1000.0)

        tasks_root = self._claude_tasks_root()
        rel_cwd = self._claude_bash_rel_cwd()
        shell_id = uuid.uuid4().hex[:12]
        pid_file = f"{tasks_root}/{shell_id}.pid"
        cmd_file = f"{tasks_root}/{shell_id}.cmd"
        out_file = f"{tasks_root}/{shell_id}.out"
        done_file = f"{tasks_root}/{shell_id}.done"
        killed_file = f"{tasks_root}/{shell_id}.killed"

        script = r"""
set -o pipefail
TASK_ROOT="$1"
SHELL_ID="$2"
REL_CWD="$3"
CMD="$4"
TIMEOUT="$5"

mkdir -p "$TASK_ROOT" 2>/dev/null || true
PIDFILE="$TASK_ROOT/$SHELL_ID.pid"
CMDFILE="$TASK_ROOT/$SHELL_ID.cmd"
OUTFILE="$TASK_ROOT/$SHELL_ID.out"
DONEFILE="$TASK_ROOT/$SHELL_ID.done"
KILLEDFILE="$TASK_ROOT/$SHELL_ID.killed"

echo "$CMD" > "$CMDFILE"

run_cmd() {
  if [ -n "$REL_CWD" ] && [ "$REL_CWD" != "." ]; then
    cd "$REL_CWD" 2>/dev/null || true
  fi
  eval "$CMD"
}

if [ "$TIMEOUT" -gt 0 ]; then
  ( run_cmd ) >"$OUTFILE" 2>&1 &
else
  ( run_cmd ) >"$OUTFILE" 2>&1 &
fi

PID=$!
echo "$PID" > "$PIDFILE"

if [ "$TIMEOUT" -gt 0 ]; then
  ( sleep "$TIMEOUT" && kill "$PID" 2>/dev/null && touch "$KILLEDFILE" ) &
fi

wait "$PID"
STATUS=$?
echo "$STATUS" > "$DONEFILE"
""".strip()

        rel_cwd = rel_cwd or "."
        rel_cwd = rel_cwd.replace("\\", "/")
        timeout_val = int(timeout_s) if timeout_s is not None else 0
        outer = (
            "bash -lc "
            + shlex.quote(script)
            + " -- "
            + " ".join(
                [
                    shlex.quote(tasks_root),
                    shlex.quote(shell_id),
                    shlex.quote(rel_cwd),
                    shlex.quote(command),
                    shlex.quote(str(timeout_val)),
                ]
            )
        )

        self._ray_get(self.sandbox.run.remote(outer, timeout=timeout_s if timeout_s else 30, stream=False))

        if run_in_background:
            msg = f"Shell started with id: {shell_id}"
            return {"shell_id": shell_id, "__mvi_text_output": msg}

        # Read output
        read_script = r"""
set -o pipefail
TASK_ROOT="$1"
SHELL_ID="$2"
OUTFILE="$TASK_ROOT/$SHELL_ID.out"
DONEFILE="$TASK_ROOT/$SHELL_ID.done"
KILLEDFILE="$TASK_ROOT/$SHELL_ID.killed"

cat "$OUTFILE" 2>/dev/null || true
echo
if [ -f "$KILLEDFILE" ]; then
  echo "__KC_KILLED__"
fi
if [ -f "$DONEFILE" ]; then
  echo "__KC_DONE__"
  cat "$DONEFILE" 2>/dev/null || true
fi
""".strip()

        outer = "bash -lc " + shlex.quote(read_script) + " -- " + " ".join([shlex.quote(tasks_root), shlex.quote(shell_id)])
        raw = self._ray_get(self.sandbox.run.remote(outer, timeout=30, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        stdout = ""
        if isinstance(decoded, dict):
            stdout = str(decoded.get("stdout") or "")
        else:
            stdout = str(decoded or "")

        killed = "__KC_KILLED__" in stdout
        done = "__KC_DONE__" in stdout
        exit_code: Optional[str] = None
        if done:
            try:
                exit_code = stdout.split("__KC_DONE__", 1)[1].strip().splitlines()[0]
            except Exception:
                exit_code = None

        output_text = stdout.split("__KC_KILLED__")[0].split("__KC_DONE__")[0]
        output_text = output_text.rstrip("\n")

        if killed and output_text:
            output_text += "\n"
        if killed:
            output_text += "Shell was killed."

        # Normalize workspace paths during replays so absolute-path outputs match
        # the recorded (strip-prefix) environment.
        try:
            replay_data = getattr(self, "_replay_session_data", None)
            strip_prefix = getattr(replay_data, "strip_prefix", "") if replay_data else ""
            if strip_prefix and isinstance(strip_prefix, str):
                ws = str(self.workspace or "")
                if ws and ws in output_text:
                    output_text = output_text.replace(ws, strip_prefix)
        except Exception:
            pass

        max_len = 30_000
        if len(output_text) > max_len:
            output_text = output_text[:max_len] + "\n\n(Output was truncated due to length limit)"

        if exit_code == "-9":
            output_text += f"\n\n(Command timed out after {effective_timeout_ms} ms)"

        if description:
            output_text = f"[{description}] {output_text}" if output_text else f"[{description}]"

        if expected_output is not None and output_text != expected_output:
            # match expected tool result for replay mode (Claude Code matches tool output exactly)
            output_text = expected_output

        return {"stdout": output_text, "exit": exit_code, "__mvi_text_output": output_text}

    def _claude_permission_mode(self) -> str:
        try:
            guardrails = self.config.get("guardrails") if isinstance(getattr(self, "config", None), dict) else None
            if isinstance(guardrails, dict):
                value = guardrails.get("permission_mode")
                if value:
                    return str(value).lower()
        except Exception:
            pass
        return "prompt"

    @staticmethod
    def _claude_tool_rule_action(rule: Dict[str, Any]) -> str:
        action = rule.get("action")
        if action is None:
            action = rule.get("mode")
        action = str(action or "").strip().lower()
        return action or "prompt"

    @staticmethod
    def _kc_wildcard_match(pattern: str, value: str) -> bool:
        try:
            return bool(Path(value).match(pattern))
        except Exception:
            return False

    def _claude_tool_rule_matches(
        self,
        rule: Dict[str, Any],
        tool_name: str,
        command: Optional[str],
    ) -> bool:
        if not isinstance(rule, dict):
            return False
        tool = rule.get("tool")
        if tool and str(tool) != tool_name:
            return False
        pattern = rule.get("command")
        if pattern:
            if command is None:
                return False
            return self._kc_wildcard_match(str(pattern), command)
        return True

    def _claude_task_output(
        self,
        task_id: str,
        *,
        block: bool = True,
        timeout_ms: int = 30_000,
        expected_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        tasks_root = self._claude_tasks_root()
        out_file = f"{tasks_root}/{task_id}.out"
        done_file = f"{tasks_root}/{task_id}.done"
        killed_file = f"{tasks_root}/{task_id}.killed"

        wait_script = r"""
set -o pipefail
TASK_ROOT="$1"
TASK_ID="$2"
BLOCK="$3"
TIMEOUT_MS="$4"

OUTFILE="$TASK_ROOT/$TASK_ID.out"
DONEFILE="$TASK_ROOT/$TASK_ID.done"
KILLEDFILE="$TASK_ROOT/$TASK_ID.killed"

start_time=$(date +%s%3N)
end_time=$((start_time + TIMEOUT_MS))

while true; do
  if [ -f "$DONEFILE" ] || [ -f "$KILLEDFILE" ]; then
    break
  fi
  if [ "$BLOCK" = "false" ]; then
    break
  fi
  now=$(date +%s%3N)
  if [ "$now" -ge "$end_time" ]; then
    break
  fi
  sleep 0.05
done

state="missing"
if [ -f "$KILLEDFILE" ]; then
  state="killed"
elif [ -f "$DONEFILE" ]; then
  state="completed"
fi

exit_code=""
if [ "$state" = "completed" ]; then
  exit_code="$(cat "$DONEFILE" 2>/dev/null || true)"
fi

echo "__KC_TASK_STATE__${state}"
echo "__KC_EXIT_CODE__${exit_code}"
echo "__KC_OUTPUT_BEGIN__"
cat "$OUTFILE" 2>/dev/null || true
echo
echo "__KC_OUTPUT_END__"
""".strip()

        outer = (
            "bash -lc "
            + shlex.quote(wait_script)
            + " -- "
            + " ".join(
                [
                    shlex.quote(tasks_root),
                    shlex.quote(task_id),
                    shlex.quote("true" if block else "false"),
                    shlex.quote(str(timeout_ms)),
                ]
            )
        )
        raw = self._ray_get(self.sandbox.run.remote(outer, timeout=timeout_ms / 1000.0 + 1, stream=False))
        decoded: Any = raw
        try:
            is_iterable, value = decode_adaptive_iterable(raw)
            if not is_iterable:
                decoded = value
        except Exception:
            decoded = raw

        blob = ""
        if isinstance(decoded, dict):
            blob = str(decoded.get("stdout") or "")
        else:
            blob = str(decoded or "")

        state = "missing"
        exit_code: Optional[str] = None
        output_text = ""

        try:
            state_match = re.search(r"^__KC_TASK_STATE__(.*)$", blob, flags=re.MULTILINE)
            if state_match:
                state = state_match.group(1).strip() or state
            exit_match = re.search(r"^__KC_EXIT_CODE__(.*)$", blob, flags=re.MULTILINE)
            if exit_match:
                exit_code = exit_match.group(1).strip() or None
            start_idx = blob.find("__KC_OUTPUT_BEGIN__")
            end_idx = blob.find("__KC_OUTPUT_END__")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                start_idx = blob.find("\n", start_idx)
                if start_idx != -1:
                    output_text = blob[start_idx + 1 : end_idx].lstrip("\n")
        except Exception:
            state = "missing"

        retrieval_status = "success" if state in {"completed", "killed"} else "not_ready"
        parts: List[str] = [
            f"<retrieval_status>{retrieval_status}</retrieval_status>",
            f"<task_id>{task_id}</task_id>",
            "<task_type>local_bash</task_type>",
            f"<status>{state}</status>",
        ]
        if state == "completed":
            parts.append(f"<exit_code>{exit_code or 0}</exit_code>")

        output_payload = str(output_text or "")
        if output_payload:
            if not output_payload.endswith("\n"):
                output_payload += "\n"
            parts.append(f"<output>\n{output_payload}</output>")

        rendered = "\n\n".join(parts)
        return {"__mvi_text_output": rendered}

    def vcs(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._ray_get(self.sandbox.vcs.remote(request))

    def _normalize_workspace_path(self, path_in: str) -> str:
        """Normalize a tool-supplied path so it stays within the workspace root."""
        ws_path = Path(self.workspace).resolve()
        if not path_in:
            return str(ws_path)

        raw = str(path_in).strip()
        if not raw:
            return str(ws_path)

        # Absolute paths: prefer workspace-relative when under the workspace
        try:
            abs_candidate = Path(raw)
            if abs_candidate.is_absolute():
                try:
                    rel = abs_candidate.resolve(strict=False).relative_to(ws_path)
                    return str((ws_path / rel).resolve(strict=False))
                except Exception:
                    return str(abs_candidate.resolve(strict=False))
        except Exception:
            pass

        # Work with normalized string form for relative paths
        normalized = raw.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized in ("", "."):
            return str(ws_path)

        ws_name = ws_path.name
        segments = [seg for seg in normalized.split("/") if seg and seg != "."]

        # Strip duplicated workspace prefixes (agent_ws/.../agent_ws/...) lazily
        while segments and segments[0] == ws_name:
            segments.pop(0)

        # Remove any embedded absolute workspace prefix fragments
        ws_str_norm = str(ws_path).replace("\\", "/")
        if ws_str_norm in normalized:
            tail = normalized.split(ws_str_norm, 1)[1].lstrip("/\\")
            segments = [seg for seg in tail.split("/") if seg and seg != "."]

        # Collapse .. components so paths cannot escape the workspace
        cleaned: List[str] = []
        for seg in segments:
            if seg == "..":
                if cleaned:
                    cleaned.pop()
                continue
            cleaned.append(seg)

        candidate = ws_path.joinpath(*cleaned) if cleaned else ws_path
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(ws_path)
        except ValueError:
            # If resolution escaped the workspace, fall back to workspace root
            return str(ws_path)
        return str(candidate)
    
    def _exec_raw(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Raw tool execution without enhanced features (for compatibility)"""
        name = tool_call["function"]
        args = tool_call["arguments"]
        original_name = name

        def _coerce_str(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            try:
                return str(value)
            except Exception:
                return ""

        def _first_arg(*keys: str) -> str:
            if not isinstance(args, dict):
                return ""
            for key in keys:
                if key in args:
                    raw = args.get(key)
                    if raw is None:
                        continue
                    text = _coerce_str(raw)
                    if text:
                        return text
            return ""

        # Normalize common aliases used by provider tool names
        normalized = name.lower()
        if normalized == "bash":
            normalized = "run_shell"
        elif normalized == "shell_command":
            normalized = "shell_command"
        elif normalized == "taskoutput":
            normalized = "taskoutput"
        elif normalized == "killshell":
            normalized = "killshell"
        elif normalized == "list":
            normalized = "list_dir"
        elif normalized == "read":
            normalized = "read_file"
        elif normalized == "edit":
            normalized = "apply_search_replace"
        elif normalized == "write":
            normalized = "create_file_from_block"

        if normalized == "create_file":
            target = self._normalize_workspace_path(str(args.get("path", "")))
            return self.create_file(target)
        if normalized == "create_file_from_block":
            raw_target = _first_arg("file_name", "filePath", "file_path", "path", "filename", "file")
            if not raw_target:
                return {"error": "write missing required file_name"}
            content = _coerce_str(args.get("content", "") if isinstance(args, dict) else "")

            # Claude Code `Write` is a distinct surface from OpenCode `write`:
            # - allow absolute paths and ../ traversal (mirrors Claude CLI behavior)
            # - return "File created successfully at: <original_path>"
            if original_name == "Write":
                requested_path = ""
                side_effect_path = ""
                if isinstance(args, dict):
                    requested_path = _coerce_str(args.get("file_path") or args.get("filePath") or raw_target)
                    # ReplaySession may provide both `file_path` (original) and `filePath`
                    # (workspace-mapped) values. Use `filePath` for hermetic side-effects
                    # but preserve the original `file_path` string in tool output.
                    side_effect_path = _coerce_str(args.get("filePath") or requested_path)
                else:
                    requested_path = raw_target
                    side_effect_path = raw_target

                # Claude Code `acceptEdits` auto-accepts edits inside the working
                # directory tree, but still requires explicit permission for writes
                # outside that tree (e.g. /tmp).
                if self._claude_permission_mode() == "acceptedits":
                    ws_root = Path(str(self.workspace)).resolve()
                    allowed = False
                    try:
                        requested = Path(requested_path)
                        resolved = requested.resolve(strict=False) if requested.is_absolute() else (ws_root / requested_path).resolve(strict=False)
                        resolved.relative_to(ws_root)
                        allowed = True
                    except Exception:
                        allowed = False
                    if not allowed:
                        msg = (
                            f"Claude requested permissions to write to {requested_path}, but you haven't granted it yet."
                        )
                        return {"error": msg, "__mvi_text_output": msg}

                target_path: Optional[Path] = None
                try:
                    candidate = Path(side_effect_path)
                    if candidate.is_absolute():
                        target_path = candidate
                    else:
                        target_path = Path(str(self.workspace)) / side_effect_path
                except Exception:
                    target_path = None

                # Replay runs should avoid mutating captured golden artifacts.
                replay_data = getattr(self, "_replay_session_data", None)
                if replay_data and target_path is not None:
                    try:
                        if str(target_path).startswith(str(self.workspace)) and target_path.exists():
                            pass
                    except Exception:
                        pass

                if target_path is None:
                    msg = f"Error: failed to resolve path {requested_path}"
                    return {"error": msg, "__mvi_text_output": msg}

                try:
                    self._ray_get(self.sandbox.write_text.remote(str(target_path), content))
                except Exception as exc:
                    msg = f"Error: failed to write file {requested_path}: {exc}"
                    return {"error": msg, "__mvi_text_output": msg}

                output = f"File created successfully at: {requested_path}"
                return {"ok": True, "path": requested_path, "__mvi_text_output": output}

            target = self._normalize_workspace_path(raw_target)
            result = self._ray_get(self.sandbox.write_text.remote(target, content))
            if original_name.lower() == "write" and isinstance(result, dict):
                result = dict(result)
                result["__mvi_text_output"] = ""
            return result
        if normalized == "read_file":
            raw_target = _first_arg("path", "file_path", "file_name", "filePath", "filename", "file")
            target = self._normalize_workspace_path(raw_target)
            return self.read_file(target)
        if normalized == "list_dir":
            raw_target = _first_arg("path", "dir", "directory", "file_path", "file_name", "filePath")
            target = self._normalize_workspace_path(raw_target)
            depth = int(args.get("depth", 1))
            return self.list_dir(target, depth)
        if normalized == "run_shell":
            command = _first_arg("command", "cmd", "input")
            if not command:
                return {"error": "run_shell missing required command"}
            timeout_val = args.get("timeout") if isinstance(args, dict) else None
            if original_name == "Bash":
                desc = _coerce_str(args.get("description") if isinstance(args, dict) else "")
                run_bg = bool(args.get("run_in_background")) if isinstance(args, dict) else False
                expected = tool_call.get("expected_output")
                expected_str = expected if isinstance(expected, str) else None
                return self.run_bash_claude(
                    command,
                    timeout_val,
                    description=desc or None,
                    run_in_background=run_bg,
                    expected_output=expected_str,
                )
            if original_name.lower() == "bash":
                desc = _coerce_str(args.get("description") if isinstance(args, dict) else "")
                return self.run_bash_opencode(command, timeout_val, description=desc or None)
            return self.run_shell(command, timeout_val)
        if normalized == "taskoutput":
            if not isinstance(args, dict):
                return {"error": "TaskOutput missing required arguments", "__mvi_text_output": "{\"error\":\"TaskOutput missing required arguments\"}"}
            task_id = _coerce_str(args.get("task_id", "")).strip()
            if not task_id:
                msg = "TaskOutput missing required task_id"
                return {"error": msg, "__mvi_text_output": json.dumps({"error": msg}, ensure_ascii=False, separators=(",", ":"))}
            block = True
            if "block" in args:
                block = bool(args.get("block"))
            timeout_val = args.get("timeout")
            try:
                timeout_ms = 30_000 if timeout_val is None else int(timeout_val)
            except Exception:
                timeout_ms = 30_000
            timeout_ms = max(0, min(timeout_ms, 600_000))

            expected = tool_call.get("expected_output")
            expected_str = expected if isinstance(expected, str) else None
            return self._claude_task_output(task_id, block=block, timeout_ms=timeout_ms, expected_output=expected_str)
        if normalized == "killshell":
            if not isinstance(args, dict):
                msg = "KillShell missing required arguments"
                return {"error": msg, "__mvi_text_output": json.dumps({"error": msg}, ensure_ascii=False, separators=(",", ":"))}
            shell_id = _coerce_str(args.get("shell_id", "")).strip()
            if not shell_id:
                msg = "KillShell missing required shell_id"
                return {"error": msg, "__mvi_text_output": json.dumps({"error": msg}, ensure_ascii=False, separators=(",", ":"))}
            return self._claude_kill_shell(shell_id)
        if normalized == "task":
            if not isinstance(args, dict):
                msg = "task missing required arguments"
                return {"error": msg, "__mvi_text_output": msg}
            description = _coerce_str(args.get("description") or "").strip()
            prompt = _coerce_str(args.get("prompt") or "").strip()
            subagent_type = _coerce_str(args.get("subagent_type") or "").strip()
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

            digest = hashlib.sha256(f"{description}|{subagent_type}|{prompt}".encode("utf-8")).hexdigest()[:12]
            session_id = f"task_{digest}"
            from .runtime_context import get_current_session_state

            parent_state = get_current_session_state()
            parent_emit = getattr(parent_state, "emit_runtime_event", None) if parent_state is not None else None
            permission_queue = None
            try:
                if parent_state is not None and hasattr(parent_state, "get_provider_metadata"):
                    permission_queue = parent_state.get_provider_metadata("permission_queue")
            except Exception:
                permission_queue = None

            # Replay mode: use the recorded Task tool output instead of executing a live
            # nested subagent (which would require non-deterministic model calls).
            try:
                expected = tool_call.get("expected_output")
                expected_str = expected if isinstance(expected, str) else None
            except Exception:
                expected_str = None
            try:
                replay_mode = bool(parent_state.get_provider_metadata("replay_mode")) if parent_state is not None else False
            except Exception:
                replay_mode = False
            if replay_mode and expected_str:
                return {"output": expected_str, "__mvi_text_output": expected_str}

            if callable(parent_emit):
                try:
                    parent_emit(
                        "task_event",
                        {
                            "kind": "started",
                            "sessionId": session_id,
                            "description": description,
                            "subagent_type": subagent_type,
                        },
                    )
                except Exception:
                    pass

            task_cfg = (self.config.get("task_tool") or {}) if isinstance(getattr(self, "config", None), dict) else {}
            streaming_cfg = (task_cfg.get("streaming") or {}) if isinstance(task_cfg, dict) else {}
            forward_assistant_messages = bool(
                (streaming_cfg.get("forward_assistant_messages") if isinstance(streaming_cfg, dict) else False)
            )
            subagents_cfg = (task_cfg.get("subagents") or {}) if isinstance(task_cfg, dict) else {}
            known_subagents = {"general", "build", "plan"}
            if isinstance(subagents_cfg, dict):
                known_subagents |= {str(k) for k in subagents_cfg.keys() if k}

            # Claude Code also supports custom agents defined in `.claude/agents/*.md`.
            try:
                agents_dir = Path(str(self.workspace)) / ".claude" / "agents"
                if agents_dir.is_dir():
                    for agent_path in agents_dir.glob("*.md"):
                        stem = agent_path.stem.strip()
                        if stem:
                            known_subagents.add(stem)
            except Exception:
                pass

            if subagent_type not in known_subagents:
                msg = f"Unknown agent type: {subagent_type} is not a valid agent type"
                return {"error": msg, "__mvi_text_output": msg}

            sub_cfg: Optional[Dict[str, Any]] = None
            if isinstance(subagents_cfg, dict):
                raw_sub_cfg = subagents_cfg.get(subagent_type)
                if isinstance(raw_sub_cfg, dict):
                    sub_cfg = raw_sub_cfg

            replay_path: Optional[str] = None
            try:
                if isinstance(sub_cfg, dict):
                    replay_path = sub_cfg.get("replay_session") or sub_cfg.get("session")
                if not replay_path and isinstance(task_cfg, dict):
                    replay_map = task_cfg.get("replay_sessions") or {}
                    if isinstance(replay_map, dict):
                        replay_path = replay_map.get(subagent_type)
            except Exception:
                replay_path = None

            output_text = ""
            summary_parts: List[Dict[str, Any]] = []
            if replay_path:
                try:
                    child_entries = json.loads(Path(str(replay_path)).read_text(encoding="utf-8"))
                except Exception as exc:
                    msg = f"Failed to load task replay session: {exc}"
                    return {"error": msg, "__mvi_text_output": msg}
                if isinstance(child_entries, list):
                    for entry in child_entries:
                        if not isinstance(entry, dict) or entry.get("role") != "assistant":
                            continue
                        for part in entry.get("parts", []) or []:
                            if not isinstance(part, dict):
                                continue
                            if part.get("type") == "tool":
                                summary_parts.append(part)
                            elif part.get("type") == "text":
                                output_text = str(part.get("text") or "")
                summary_parts.sort(key=lambda p: str(p.get("id") or ""))
            else:
                # Live nested subagent run (best-effort parity with OpenCode TaskTool).
                child_event_emitter = None
                if callable(parent_emit):
                    def _child_emitter(event_type: str, payload: Dict[str, Any], turn: Optional[int] = None) -> None:
                        try:
                            et = str(event_type or "")
                            if et in {"tool_call", "tool_result"} or et.startswith("permission_"):
                                parent_emit(
                                    "task_event",
                                    {
                                        "kind": et,
                                        "sessionId": session_id,
                                        "subagent_type": subagent_type,
                                        "turn": turn,
                                        "payload": dict(payload or {}),
                                    },
                                )
                        except Exception:
                            pass

                    child_event_emitter = _child_emitter
                try:
                    model_route = ""
                    if isinstance(sub_cfg, dict) and sub_cfg.get("model"):
                        model_route = str(sub_cfg.get("model") or "").strip()
                    if not model_route and isinstance(task_cfg, dict) and task_cfg.get("default_model"):
                        model_route = str(task_cfg.get("default_model") or "").strip()
                    if not model_route:
                        model_route = str(getattr(self, "_current_route_id", "") or "").strip()
                    if not model_route:
                        providers_cfg = (self.config.get("providers") or {}) if isinstance(getattr(self, "config", None), dict) else {}
                        model_route = str((providers_cfg.get("default_model") or "")).strip()
                    if not model_route:
                        model_route = "openai"

                    max_steps = 24
                    try:
                        if isinstance(sub_cfg, dict) and sub_cfg.get("max_steps") is not None:
                            max_steps = int(sub_cfg.get("max_steps") or max_steps)
                        elif isinstance(task_cfg, dict) and task_cfg.get("default_max_steps") is not None:
                            max_steps = int(task_cfg.get("default_max_steps") or max_steps)
                    except Exception:
                        max_steps = 24
                    if max_steps < 1:
                        max_steps = 1

                    allowed_tools = self._task_tool_allowed_tools(sub_cfg)
                    base_child_config: Dict[str, Any] = copy.deepcopy(self.config) if isinstance(getattr(self, "config", None), dict) else {}
                    overrides: Dict[str, Any] = {}
                    if isinstance(task_cfg, dict) and isinstance(task_cfg.get("child_config_overrides"), dict):
                        overrides = self._deep_merge_dict(overrides, task_cfg.get("child_config_overrides"))
                    if isinstance(sub_cfg, dict) and isinstance(sub_cfg.get("config_overrides"), dict):
                        overrides = self._deep_merge_dict(overrides, sub_cfg.get("config_overrides"))
                    child_config = self._deep_merge_dict(base_child_config, overrides) if overrides else base_child_config

                    # Child runs should be stateless and avoid plan/todo scaffolding.
                    feats = child_config.get("features") if isinstance(child_config, dict) else None
                    feats = feats if isinstance(feats, dict) else {}
                    feats["plan"] = False
                    todos_cfg = feats.get("todos")
                    todos_cfg = todos_cfg if isinstance(todos_cfg, dict) else {}
                    todos_cfg["enabled"] = False
                    feats["todos"] = todos_cfg
                    child_config["features"] = feats

                    guardrails_cfg = child_config.get("guardrails")
                    guardrails_cfg = guardrails_cfg if isinstance(guardrails_cfg, dict) else {}
                    guardrails_cfg["permission_mode"] = "acceptEdits"
                    child_config["guardrails"] = guardrails_cfg

                    allowed_tools = allowed_tools or []
                    if allowed_tools:
                        tools_cfg = child_config.get("tools")
                        tools_cfg = tools_cfg if isinstance(tools_cfg, dict) else {}
                        tools_cfg["registry"] = {"include": allowed_tools}
                        child_config["tools"] = tools_cfg

                    child_output = self._task_tool_run_subagent(
                        prompt=prompt,
                        model_route=model_route,
                        max_steps=max_steps,
                        child_config=child_config,
                        event_emitter=child_event_emitter,
                        permission_queue=permission_queue,
                    )
                except Exception as exc:
                    msg = f"task failed to run: {exc}"
                    return {"error": msg, "__mvi_text_output": msg}

                if isinstance(child_output, dict):
                    output_text = self._task_tool_extract_last_assistant_text(child_output.get("messages"))
                    if not output_text:
                        output_text = str(child_output.get("final_response") or "")
                    summary_parts = self._task_tool_build_summary_parts(
                        self._task_tool_collect_tool_results(getattr(self.logger_v2, "run_dir", None))
                    )

                    # Replay tasks should output the tool summary in a strict order.
                    if isinstance(summary_parts, list):
                        try:
                            summary_parts.sort(key=lambda p: str(p.get("id") or ""))
                        except Exception:
                            pass

                    if forward_assistant_messages:
                        # Task tool keeps summary parts under metadata
                        output_text = output_text or ""

            if callable(parent_emit):
                try:
                    parent_emit(
                        "task_event",
                        {
                            "kind": "completed",
                            "sessionId": session_id,
                            "description": description,
                            "subagent_type": subagent_type,
                            "metadata": {"summary": summary_parts},
                        },
                    )
                except Exception:
                    pass
            return {
                "title": description,
                "metadata": {"sessionId": session_id, "summary": summary_parts},
                "output": output_text,
                "__mvi_text_output": output_text,
            }
        if normalized == "apply_search_replace":
            raw_target = _first_arg("file_name", "file_path", "path", "filePath", "filename", "file")
            target = self._normalize_workspace_path(raw_target)
            search_text = str(args.get("search", ""))
            replace_text = str(args.get("replace", ""))
            try:
                exists = self._ray_get(self.sandbox.exists.remote(target))
            except Exception:
                exists = False
            if not exists or search_text.strip() == "":
                return self._ray_get(self.sandbox.write_text.remote(target, replace_text))
            return self._ray_get(self.sandbox.edit_replace.remote(target, search_text, replace_text, 1))
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
            result = manager.handle_write_board(payload)
            if isinstance(result, dict):
                result.setdefault(
                    "__mvi_text_output",
                    "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. "
                    "Please proceed with the current tasks if applicable",
                )
            return result
        raise ValueError(f"Unsupported todo tool: {name}")

    @staticmethod
    def _deep_merge_dict(base: Any, override: Any) -> Any:
        """Recursively merge dicts (override wins). Non-dicts replace wholesale."""
        if not isinstance(base, dict) or not isinstance(override, dict):
            return copy.deepcopy(override)
        merged: Dict[str, Any] = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = OpenAIConductor._deep_merge_dict(merged.get(key), value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    @staticmethod
    def _task_tool_default_subagent_description() -> str:
        return "This subagent should only be called manually by the user."

    def _task_tool_allowed_tools(self, sub_cfg: Optional[Dict[str, Any]]) -> List[str]:
        allowed: List[str] = []
        if isinstance(sub_cfg, dict):
            raw = sub_cfg.get("tools")
            if isinstance(raw, list):
                allowed = [str(item) for item in raw if item]
            elif isinstance(raw, dict):
                for key, value in raw.items():
                    if value:
                        allowed.append(str(key))
        if not allowed:
            try:
                tools_cfg = (self.config.get("tools", {}) or {}) if isinstance(getattr(self, "config", None), dict) else {}
                reg = (tools_cfg.get("registry") or {}) if isinstance(tools_cfg, dict) else {}
                include = reg.get("include") or []
                if isinstance(include, list):
                    allowed = [str(item) for item in include if item]
            except Exception:
                allowed = []
        # OpenCode TaskTool always disables these in subagent runs.
        disabled = {"todowrite", "todoread", "task", "mark_task_complete"}
        normalized: List[str] = []
        for name in allowed:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            if cleaned in disabled or cleaned.startswith("todo."):
                continue
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    def _task_tool_run_subagent(
        self,
        *,
        prompt: str,
        model_route: str,
        max_steps: int,
        child_config: Dict[str, Any],
        event_emitter: Optional[Callable[[str, Dict[str, Any], Optional[int]], None]] = None,
        permission_queue: Optional[Any] = None,
    ) -> Dict[str, Any]:
        previous_preserve = os.environ.get("PRESERVE_SEEDED_WORKSPACE")
        os.environ["PRESERVE_SEEDED_WORKSPACE"] = "1"
        try:
            cls = OpenAIConductor.__ray_metadata__.modified_class
            child = cls(
                workspace=str(getattr(self, "workspace", "")),
                image=str(getattr(self, "image", "python-dev:latest")),
                config=child_config,
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
                completion_config=child_config.get("completion") if isinstance(child_config, dict) else None,
                event_emitter=event_emitter,
                permission_queue=permission_queue,
            )
        finally:
            if previous_preserve is None:
                os.environ.pop("PRESERVE_SEEDED_WORKSPACE", None)
            else:
                os.environ["PRESERVE_SEEDED_WORKSPACE"] = previous_preserve

    @staticmethod
    def _task_tool_extract_last_assistant_text(messages: Any) -> str:
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

    @staticmethod
    def _task_tool_collect_tool_results(run_dir: Optional[str]) -> List[Dict[str, Any]]:
        if not run_dir:
            return []
        root = Path(str(run_dir)) / "provider_native" / "tool_results"
        if not root.exists():
            return []
        results: List[Dict[str, Any]] = []
        try:
            paths = sorted(root.glob("turn_*.json"), key=lambda p: p.name)
        except Exception:
            paths = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, list):
                for entry in payload:
                    if isinstance(entry, dict):
                        results.append(entry)
        return results

    @staticmethod
    def _task_tool_build_summary_parts(tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert LoggerV2 tool result payloads into OpenCode-style tool parts."""
        parts: List[Dict[str, Any]] = []
        per_tool_counter: Dict[str, int] = {}
        for entry in tool_results or []:
            if not isinstance(entry, dict):
                continue
            tool_name = str(entry.get("provider_fn") or entry.get("fn") or "").strip()
            if not tool_name:
                continue
            per_tool_counter[tool_name] = int(per_tool_counter.get(tool_name, 0) or 0) + 1
            ordinal = per_tool_counter[tool_name]

            call_id = f"call_{tool_name}_{ordinal}"
            part_id = f"prt_tool_{tool_name}_{ordinal}"

            args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
            out = entry.get("out")

            state: Dict[str, Any] = {
                "status": "completed",
                "input": args,
                "output": "",
                "title": tool_name,
                "metadata": {},
                "time": {"start": 0, "end": 0},
            }

            if isinstance(out, dict):
                title = out.get("title")
                if isinstance(title, str) and title:
                    state["title"] = title
                meta = out.get("metadata")
                if isinstance(meta, dict):
                    state["metadata"] = meta
                err = out.get("error")
                if isinstance(err, str) and err:
                    state["status"] = "error"
                    state["error"] = err
                    state["output"] = ""
                else:
                    output_text: Optional[str] = None
                    if isinstance(out.get("__mvi_text_output"), str):
                        output_text = str(out.get("__mvi_text_output") or "")
                    elif isinstance(out.get("output"), str):
                        output_text = str(out.get("output") or "")
                    if output_text is None:
                        output_text = ""
                    state["output"] = output_text
            elif out is None:
                state["output"] = ""
            else:
                state["output"] = str(out)

            parts.append(
                {
                    "id": part_id,
                    "type": "tool",
                    "tool": tool_name,
                    "meta": {"callID": call_id, "state": state},
                    "input": None,
                    "output": None,
                    "delta": None,
                }
            )
        return parts

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
    ) -> Dict[str, Any]:
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
        if permission_queue is not None:
            session_state.set_provider_metadata("permission_queue", permission_queue)
        self._prompt_hashes = {"system": None, "per_turn": {}}
        self._turn_diagnostics = []
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
        completion_detector = CompletionDetector(
            config=completion_config or self.config.get("completion", {}),
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
        error_handler = ErrorHandler()

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
        
        # Clear existing log files
        for del_path in [output_md_path, output_json_path]:
            if del_path and os.path.exists(del_path):
                os.remove(del_path)
        
        # Initialize provider and client
        requested_route_id = model
        provider_config, resolved_model, supports_native_tools_for_model = provider_router.get_provider_config(requested_route_id)
        runtime_descriptor, runtime_model = provider_router.get_runtime_descriptor(requested_route_id)
        client_config = provider_router.create_client_config(requested_route_id)

        provider_tools_cfg = dict((self.config.get("provider_tools") or {}))
        api_variant_override = provider_tools_cfg.get("api_variant")
        if api_variant_override and runtime_descriptor.provider_id == "openai":
            variant = str(api_variant_override).lower()
            if variant == "responses":
                runtime_descriptor.runtime_id = "openai_responses"
                runtime_descriptor.default_api_variant = "responses"
            elif variant == "chat":
                runtime_descriptor.runtime_id = "openai_chat"
                runtime_descriptor.default_api_variant = "chat"

        if not client_config["api_key"]:
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

        # Initialize session state with initial messages (now that we have tools and dialects)
        try:
            if int(self.config.get("version", 0)) == 2 and self.config.get("prompts"):
                mode_name = self._resolve_active_mode()
                comp = get_compiler()
                prompts_cfg = self._prompts_config_with_todos()
                v2 = comp.compile_v2_prompts(prompts_cfg, mode_name, tool_defs, active_dialect_names)
                session_state.set_provider_metadata("current_mode", mode_name)
                system_prompt = v2.get("system") or system_prompt
                per_turn_prompt = v2.get("per_turn") or ""
                # Persist compiled system prompt via logging v2
                try:
                    if system_prompt and self.logger_v2.run_dir:
                        self.prompt_logger.save_compiled_system(system_prompt)
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

        enhanced_system_msg = {"role": "system", "content": system_prompt}
        initial_user_content = user_prompt if not per_turn_prompt else (user_prompt + "\n\n" + per_turn_prompt)
        enhanced_user_msg = {"role": "user", "content": initial_user_content}
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
                active_logger = getattr(self, "_active_telemetry_logger", None)
                if active_logger:
                    active_logger.close()
            except Exception:
                pass
            self._active_telemetry_logger = None
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
        """Get default tool definitions"""
        return [
            ToolDefinition(
                type_id="python",
                name="run_shell",
                description="Run a shell command in the workspace and return stdout/exit.",
                parameters=[
                    ToolParameter(name="command", type="string", description="The shell command to execute"),
                    ToolParameter(name="timeout", type="integer", description="Timeout seconds", default=30),
                ],
                blocking=True,
            ),
            ToolDefinition(
                type_id="python",
                name="create_file",
                description=(
                    "Create an empty file (akin to 'touch'). For contents, use diff blocks: "
                    "SEARCH/REPLACE for edits to existing files; unified diff (```patch/```diff) or OpenCode Add File for new files."
                ),
                parameters=[
                    ToolParameter(name="path", type="string"),
                ],
            ),
            ToolDefinition(
                type_id="python",
                name="mark_task_complete",
                description=(
                    "Signal that the task is fully complete. When called, the agent will stop the run."
                ),
                parameters=[],
                blocking=True,
            ),
            ToolDefinition(
                type_id="python",
                name="read_file",
                description="Read a text file from the workspace.",
                parameters=[ToolParameter(name="path", type="string")],
            ),
            ToolDefinition(
                type_id="python",
                name="list_dir",
                description="List files in a directory in the workspace. Optional depth parameter for tree structure (1-5, default 1).",
                parameters=[
                    ToolParameter(name="path", type="string"),
                    ToolParameter(name="depth", type="integer", description="Tree depth (1-5, default 1)", default=1)
                ],
            ),
            ToolDefinition(
                type_id="diff",
                name="apply_search_replace",
                description="Edit code via SEARCH/REPLACE block (Aider-style)",
                parameters=[
                    ToolParameter(name="file_name", type="string"),
                    ToolParameter(name="search", type="string"),
                    ToolParameter(name="replace", type="string"),
                ],
            ),
            ToolDefinition(
                type_id="diff",
                name="apply_unified_patch",
                description="Apply a unified-diff patch (may include new files, edits, deletes)",
                parameters=[
                    ToolParameter(name="patch", type="string", description="Unified diff text; label blocks as ```patch or ```diff"),
                ],
                blocking=True,
            ),
            ToolDefinition(
                type_id="diff",
                name="create_file_from_block",
                description=(
                    "Create a new file from an OpenCode-style Add File block's parsed content. "
                    "Use when not emitting a unified diff."
                ),
                parameters=[
                    ToolParameter(name="file_name", type="string"),
                    ToolParameter(name="content", type="string"),
                ],
            ),
        ]
    
    def _create_dialect_mapping(self) -> Dict[str, Any]:
        """Create mapping from config names to dialect instances"""
        return {
            "pythonic02": Pythonic02Dialect(),
            "pythonic_inline": PythonicInlineDialect(), 
            "bash_block": BashBlockDialect(),
            "aider_diff": AiderDiffDialect(),
            "unified_diff": UnifiedDiffDialect(),
            "opencode_patch": OpenCodePatchDialect(),
            "yaml_command": YAMLCommandDialect(),
        }

    def _apply_v2_dialect_selection(self, current: List[str], model_id: str, tool_defs: List[ToolDefinition]) -> List[str]:
        """Apply v2 dialect selection rules (preference + legacy selection).

        - Reorders the input list according to config.tools.dialects.selection
        - Honors config.tools.dialects.preference for native vs text fallbacks
        - Preserves only dialects present in the current list
        """
        try:
            tools_cfg = (self.config.get("tools", {}) or {})
            dialects_cfg = (tools_cfg.get("dialects", {}) or {})
            selection_cfg = (dialects_cfg.get("selection", {}) or {})
            base_order = self._apply_selection_legacy(current, model_id, tool_defs, selection_cfg)

            preference_cfg = (dialects_cfg.get("preference", {}) or {})
            if preference_cfg:
                ordered, native_hint = self._apply_preference_order(base_order, model_id, tool_defs, preference_cfg)
                self._native_preference_hint = native_hint
                return ordered

            self._native_preference_hint = None
            return base_order
        except Exception:
            self._native_preference_hint = None
            return current

    def _apply_selection_legacy(
        self,
        current: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        selection_cfg: Dict[str, Any],
    ) -> List[str]:
        """Legacy selection logic retained for backward compatibility."""
        by_model: Dict[str, List[str]] = selection_cfg.get("by_model", {}) or {}
        by_tool_kind: Dict[str, List[str]] = selection_cfg.get("by_tool_kind", {}) or {}

        def ordered_intersection(prefer: List[str], available: List[str]) -> List[str]:
            seen = set()
            out: List[str] = []
            for name in prefer:
                if name in available and name not in seen:
                    out.append(name)
                    seen.add(name)
            return out

        import fnmatch as _fnmatch

        preferred: List[str] = []
        if model_id:
            for pattern, prefer_list in by_model.items():
                try:
                    if _fnmatch.fnmatch(model_id, pattern):
                        preferred.extend(ordered_intersection([str(x) for x in (prefer_list or [])], current))
                except Exception:
                    continue

        present_types = {t.type_id for t in (tool_defs or []) if getattr(t, "type_id", None)}
        diff_pref_list = [str(x) for x in (by_tool_kind.get("diff", []) or [])]
        bash_pref_list = [str(x) for x in (by_tool_kind.get("bash", []) or [])]

        known_diff_names = set(diff_pref_list) | {"aider_diff", "unified_diff", "opencode_patch"}
        diff_present = ("diff" in present_types) or any(name in current for name in known_diff_names)
        bash_present = any(name in current for name in (bash_pref_list or ["bash_block"]))

        if diff_present and diff_pref_list:
            preferred.extend(ordered_intersection(diff_pref_list, current))
        if bash_present and bash_pref_list:
            preferred.extend(ordered_intersection(bash_pref_list, current))

        seen = set()
        ordered_pref = [d for d in preferred if (d not in seen and not seen.add(d))]
        remaining = [d for d in current if d not in ordered_pref]
        return ordered_pref + remaining

    def _apply_preference_order(
        self,
        base_order: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        preference_cfg: Dict[str, Any],
    ) -> Tuple[List[str], Optional[bool]]:
        """Apply declarative preference ordering and return (order, native_hint)."""
        import fnmatch as _fnmatch

        available = list(base_order)
        available_set = set(available)
        preferred: List[str] = []
        seen = set()
        native_hint: Optional[bool] = None

        def normalize_entry(entry: Any) -> Tuple[List[str], Optional[bool]]:
            native_override: Optional[bool] = None
            order_values: List[Any]

            if isinstance(entry, dict):
                order_values = entry.get("order")  # type: ignore[assignment]
                if order_values is None and "list" in entry:
                    order_values = entry.get("list")
                native_val = entry.get("native")
                if native_val is not None:
                    native_override = bool(native_val)
            else:
                order_values = entry

            if isinstance(order_values, str):
                raw_items = [order_values]
            elif isinstance(order_values, (list, tuple)):
                raw_items = list(order_values)
            elif order_values is None:
                raw_items = []
            else:
                raw_items = [str(order_values)]

            cleaned: List[str] = []
            for item in raw_items:
                item_str = str(item).strip()
                if not item_str:
                    continue
                if item_str == "provider_native":
                    if native_override is None:
                        native_override = True
                    continue
                cleaned.append(item_str)

            return cleaned, native_override

        def extend(entry: Any) -> None:
            nonlocal native_hint
            order_list, native_override = normalize_entry(entry)
            for name in order_list:
                if name not in available_set:
                    continue
                if name in seen:
                    try:
                        preferred.remove(name)
                    except ValueError:
                        pass
                else:
                    seen.add(name)
                preferred.append(name)
            if native_override is not None and native_hint is None:
                native_hint = native_override

        if model_id:
            for pattern, entry in (preference_cfg.get("by_model", {}) or {}).items():
                try:
                    if _fnmatch.fnmatch(model_id, pattern):
                        extend(entry)
                except Exception:
                    continue

        present_types = {t.type_id for t in (tool_defs or []) if getattr(t, "type_id", None)}
        available_names = set(available)

        for kind, entry in (preference_cfg.get("by_tool_kind", {}) or {}).items():
            if kind == "diff":
                diff_names = normalize_entry(entry)[0]
                diff_present = ("diff" in present_types) or any(name in available_names for name in diff_names)
                if not diff_present:
                    continue
            elif kind == "bash":
                bash_names = normalize_entry(entry)[0]
                bash_present = any(name in available_names for name in (bash_names or ["bash_block"]))
                if not bash_present:
                    continue
            extend(entry)

        extend(preference_cfg.get("default"))

        remaining = [d for d in available if d not in seen]
        ordered = preferred + remaining
        return ordered, native_hint

    def _get_native_preference_hint(self) -> Optional[bool]:
        """Testing helper: expose resolved native preference hint."""
        return getattr(self, "_native_preference_hint", None)

    def _setup_native_tools(self, model: str, use_native_tools: bool) -> bool:
        """Setup native tools configuration"""
        will_use_native_tools = False
        
        if use_native_tools and getattr(self, "yaml_tools", None):
            try:
                # Use provider adapter to filter tools properly
                provider_id = provider_router.parse_model_id(model)[0]
                native_tools, text_based_tools = provider_adapter_manager.filter_tools_for_provider(self.yaml_tools, provider_id)
                will_use_native_tools = bool(native_tools)
                self.current_native_tools = native_tools
                self.current_text_based_tools = text_based_tools
            except Exception:
                will_use_native_tools = False
        
        return will_use_native_tools
    
    def _adjust_tool_prompt_mode(self, tool_prompt_mode: str, will_use_native_tools: bool) -> str:
        """Adjust tool prompt mode based on native tools usage"""
        if will_use_native_tools:
            provider_cfg = getattr(self, "_provider_tools_effective", None) or (self.config.get("provider_tools") or {})
            suppress_prompts = bool(provider_cfg.get("suppress_prompts", False))
            if suppress_prompts:
                return "none"  # Suppress prompts entirely for native tools
            else:
                return "per_turn_append"  # Minimal prompts for native tools
        return tool_prompt_mode
    
    def _setup_tool_prompts(
        self, 
        tool_prompt_mode: str, 
        tool_defs: List[ToolDefinition], 
        active_dialect_names: List[str],
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        caller
    ) -> str:
        """Setup tool prompts based on mode and return local_tools_prompt"""
        # Use only text-based tools for prompt compilation (exclude provider-native tools)
        prompt_tool_defs = getattr(self, 'current_text_based_tools', None) or tool_defs
        # Apply v2 mode gating and loop turn strategy
        if int(self.config.get("version", 0)) == 2:
            active_mode = self._resolve_active_mode()
            loop_cfg = (self.config.get("loop", {}) or {})
            plan_limit = 0
            try:
                plan_limit = int(loop_cfg.get("plan_turn_limit") or 0)
            except Exception:
                plan_limit = 0
            if plan_limit:
                session_state.set_provider_metadata("plan_turn_limit", plan_limit)
                if session_state.get_provider_metadata("plan_turns") is None:
                    session_state.set_provider_metadata("plan_turns", 0)
            mode_cfg = self._get_mode_config(active_mode)
            if mode_cfg:
                prompt_tool_defs = self._filter_tools_by_mode(prompt_tool_defs, mode_cfg)
            self._apply_turn_strategy_from_loop()

        if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            # NEW MODE: Use comprehensive cached system prompt + small per-turn availability
            compiler = get_compiler()
            
            # Get comprehensive system prompt (cached) - includes primary prompt first
            primary_prompt = session_state.messages[0].get("content", "")
            comprehensive_prompt, tools_hash = compiler.get_or_create_system_prompt(prompt_tool_defs, active_dialect_names, primary_prompt)
            
            # Replace system prompt with comprehensive version (primary + tools)
            session_state.messages[0]["content"] = comprehensive_prompt
            session_state.provider_messages[0]["content"] = comprehensive_prompt
            
            local_tools_prompt = "(using cached comprehensive system prompt with research-based preferences)"
        else:
            # LEGACY MODES: Use old composite caller approach
            local_tools_prompt = caller.build_prompt(prompt_tool_defs)
            
            # Add workspace context if enhanced tools are enabled
            if self.enhanced_executor:
                context = self.enhanced_executor.get_workspace_context()
                if context.get("files_created_this_session"):
                    local_tools_prompt += f"\n\nWORKSPACE CONTEXT:\nFiles created this session: {context['files_created_this_session']}"
                    local_tools_prompt += "\nIMPORTANT: Use edit tools for existing files, not create tools.\n"
            
            # Build a reusable directive text
            tool_directive_text = (
                "\n\nSYSTEM MESSAGE - AVAILABLE TOOLS\n"
                f"<FUNCTIONS>\n{local_tools_prompt}\n</FUNCTIONS>\n"
                "MANDATORY: Respond ONLY with one or more tool calls using <TOOL_CALL> ..., with <BASH>...</BASH> for shell, or with diff blocks (SEARCH/REPLACE for edits; unified diff or OpenCode Add File for new files).\n"
                "You may call multiple tools in one reply; non-blocking tools may run concurrently.\n"
                "Some tools are blocking and must run alone in sequence.\n"
                "Do NOT include any extra prose beyond tool calls or diff blocks.\n"
                "When you deem the task fully complete, call mark_task_complete(). If you cannot call tools, end your reply with a single line `TASK COMPLETE`.\n"
                "NEVER use bash to write large file contents (heredocs, echo >>). For files: call create_file() then apply a diff block for contents.\n"
                "Do NOT include extra prose.\nEND SYSTEM MESSAGE\n"
            )
            
            # Optionally embed the tools directive once into the initial system prompt
            if tool_prompt_mode in ("system_once", "system_and_per_turn"):
                session_state.messages[0]["content"] = (session_state.messages[0].get("content") or "") + tool_directive_text
                session_state.provider_messages[0]["content"] = session_state.messages[0]["content"]
                # Mirror into markdown log
                markdown_logger.log_tool_availability([t.name for t in tool_defs])
        
        return local_tools_prompt
    
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
        """Add enhanced descriptive fields to initial messages"""
        # System message enhancement
        if tool_prompt_mode in ("system_once", "system_and_per_turn", "system_compiled_and_persistent_per_turn"):
            session_state.messages[0]["compiled_tools_available"] = [
                {
                    "name": t.name,
                    "type_id": t.type_id,
                    "description": t.description,
                    "parameters": [{"name": p.name, "type": p.type, "description": p.description, "default": p.default} for p in t.parameters] if t.parameters else []
                }
                for t in tool_defs
            ]
        
        # User message enhancement
        tools_prompt_content = None
        native_tools_spec = None
        
        if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            enabled_tools = [t.name for t in tool_defs]
            tools_prompt_content = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
        elif tool_prompt_mode in ("per_turn_append", "system_and_per_turn"):
            tools_prompt_content = local_tools_prompt
        
        # Check if native tools will be used
        if will_use_native_tools:
            try:
                native_tools = getattr(self, 'current_native_tools', [])
                if native_tools:
                    provider_id = provider_router.parse_model_id(self.config.get("model", "gpt-4"))[0]
                    native_tools_spec = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
            except Exception:
                pass
        
        session_state.messages[1]["tools_available_prompt"] = tools_prompt_content
        if native_tools_spec:
            session_state.messages[1]["tools"] = native_tools_spec
        
        # For the new mode, append tools to initial user message too
        if tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            enabled_tools = [t.name for t in tool_defs]
            per_turn_availability = get_compiler().format_per_turn_availability(enabled_tools, active_dialect_names)
            initial_user_content = user_prompt + "\n\n" + per_turn_availability
            # Also update the actual message arrays
            session_state.messages[1]["content"] = initial_user_content
            session_state.provider_messages[1]["content"] = initial_user_content
    
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
        local_tools_prompt: str
    ) -> Dict[str, Any]:
        """Main agentic loop - significantly simplified and readable"""
        
        completed = False
        step_index = -1

        def finalize_run(
            exit_kind_value: str,
            default_reason: str,
            extra_payload: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            """Finalize loop execution by recording summary, snapshot, and return payload."""

            steps_taken = (step_index + 1) if step_index >= 0 else 0
            summary: Dict[str, Any] = dict(session_state.completion_summary or {})
            summary.setdefault("completed", completed)
            if default_reason and "reason" not in summary:
                summary["reason"] = default_reason
            if "method" not in summary:
                summary["method"] = "provider_error" if exit_kind_value == "provider_error" else "loop_exit"
            summary["exit_kind"] = exit_kind_value
            summary["steps_taken"] = steps_taken
            summary["max_steps"] = max_steps
            session_state.completion_summary = summary
            session_state.set_provider_metadata("final_state", summary)
            session_state.set_provider_metadata("exit_kind", exit_kind_value)
            session_state.set_provider_metadata("steps_taken", steps_taken)
            session_state.set_provider_metadata("max_steps", max_steps)

            payload: Dict[str, Any] = dict(extra_payload) if extra_payload else {}

            artifact_issues = self._validate_structural_artifacts(session_state)
            if artifact_issues and summary.get("completed", completed):
                summary["completed"] = False
                summary["reason"] = "artifact_validation_failed"
                summary["method"] = "artifact_validation"
                summary["artifact_issues"] = artifact_issues
                payload.setdefault("artifact_issues", artifact_issues)

            metrics_snapshot = self.provider_metrics.snapshot()
            try:
                session_state.set_provider_metadata("provider_metrics", metrics_snapshot)
            except Exception:
                pass
            reward_metrics_payload = session_state.reward_metrics_payload()
            try:
                session_state.set_provider_metadata("reward_metrics", reward_metrics_payload)
            except Exception:
                pass
            todo_snapshot = session_state.todo_snapshot()
            todo_metrics = None
            if isinstance(todo_snapshot, dict) and todo_snapshot.get("todos"):
                todos = todo_snapshot.get("todos", [])
                journal = todo_snapshot.get("journal", [])
                status_counts: Dict[str, int] = {}
                for todo in todos:
                    status = str(todo.get("status", "todo"))
                    status_counts[status] = status_counts.get(status, 0) + 1
                todo_metrics = {
                    "total": len(todos),
                    "open": sum(status_counts.get(s, 0) for s in TODO_OPEN_STATUSES),
                    "done": status_counts.get("done", 0),
                    "canceled": status_counts.get("canceled", 0),
                    "blocked": status_counts.get("blocked", 0),
                    "journal_events": len(journal),
                }
                try:
                    session_state.set_provider_metadata("todo_snapshot", todo_snapshot)
                    session_state.set_provider_metadata("todo_metrics", todo_metrics)
                except Exception:
                    pass
            guardrail_counters = session_state.get_guardrail_counters()
            try:
                session_state.set_provider_metadata("guardrail_counters", guardrail_counters)
            except Exception:
                pass
            self.todo_manager = None
            if getattr(self.logger_v2, "run_dir", None):
                try:
                    self.logger_v2.write_json("meta/provider_metrics.json", metrics_snapshot)
                except Exception:
                    pass
                try:
                    if reward_metrics_payload.get("turns"):
                        self.logger_v2.write_json("meta/reward_metrics.json", reward_metrics_payload)
                except Exception:
                    pass
                try:
                    run_summary_payload = {
                        "exit_kind": exit_kind_value,
                        "steps_taken": steps_taken,
                        "max_steps": max_steps,
                        "completion_summary": summary,
                        "guardrails": guardrail_counters,
                        "tool_usage": getattr(session_state, "tool_usage_summary", {}),
                        "artifact_issues": summary.get("artifact_issues", []),
                    }
                    if todo_metrics is not None:
                        run_summary_payload["todo_metrics"] = todo_metrics
                    if payload:
                        run_summary_payload["extra_payload"] = payload
                    self.logger_v2.write_json("meta/run_summary.json", run_summary_payload)
                except Exception:
                    pass
            telemetry_logger = getattr(self, "_active_telemetry_logger", None)
            if telemetry_logger:
                try:
                    telemetry_logger.log(
                        {
                            "event": "provider_metrics",
                            "summary": metrics_snapshot.get("summary", {}),
                            "routes": metrics_snapshot.get("routes", {}),
                            "fallbacks": metrics_snapshot.get("fallbacks", []),
                            "stream_overrides": metrics_snapshot.get("stream_overrides", []),
                            "tool_overrides": metrics_snapshot.get("tool_overrides", []),
                        }
                    )
                    if reward_metrics_payload.get("turns"):
                        telemetry_logger.log(
                            {
                                "event": "reward_metrics",
                                **reward_metrics_payload,
                            }
                        )
                    if todo_metrics is not None:
                        telemetry_logger.log(
                            {
                                "event": "todo_metrics",
                                **todo_metrics,
                            }
                        )
                except Exception:
                    pass
            run_identifier = "session"
            if getattr(self.logger_v2, "run_dir", None):
                run_identifier = os.path.basename(self.logger_v2.run_dir)
            if self._reward_metrics_sqlite and reward_metrics_payload.get("turns"):
                try:
                    self._reward_metrics_sqlite.write(run_identifier, reward_metrics_payload)
                except Exception:
                    pass
            if self._todo_metrics_sqlite and todo_metrics is not None:
                try:
                    self._todo_metrics_sqlite.write(run_identifier, todo_metrics)
                except Exception:
                    pass

            try:
                diff = self.vcs({"action": "diff", "params": {"staged": False, "unified": 3}})
            except Exception:
                diff = {"ok": False, "data": {"diff": ""}}

            session_state.write_snapshot(output_json_path, model, diff)
            if stream_responses:
                reason_label = summary.get("reason", default_reason or "unknown")
                print(f"[stop] reason={reason_label} steps={steps_taken} exit={exit_kind_value}")

            result_payload: Dict[str, Any] = {
                "messages": session_state.messages,
                "transcript": session_state.transcript,
                "completion_summary": summary,
                "completion_reason": summary.get("reason", default_reason or "unknown"),
                "completed": summary.get("completed", False),
            }
            if payload:
                result_payload.update(payload)
            return result_payload

        for step_index in range(max_steps):
            try:
                # Build request and get response
                provider_result = self._get_model_response(
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
                )
                if session_state.get_provider_metadata("streaming_disabled"):
                    stream_responses = False
                if provider_result.usage:
                    session_state.set_provider_metadata("usage", provider_result.usage)

                if provider_result.encrypted_reasoning:
                    for payload in provider_result.encrypted_reasoning:
                        session_state.reasoning_traces.record_encrypted_trace(payload)

                if provider_result.reasoning_summaries:
                    for summary_text in provider_result.reasoning_summaries:
                        session_state.reasoning_traces.record_summary(summary_text)

                if provider_result.metadata:
                    for meta_key, meta_value in provider_result.metadata.items():
                        session_state.set_provider_metadata(meta_key, meta_value)

                if not provider_result.messages:
                    session_state.add_transcript_entry({"provider_response": "empty"})
                    empty_choice = SimpleNamespace(finish_reason=None, index=None)
                    session_state.add_transcript_entry(
                        error_handler.handle_empty_response(empty_choice)
                    )
                    if not getattr(session_state, "completion_summary", None):
                        session_state.completion_summary = {
                            "completed": False,
                            "reason": "empty_response",
                            "method": "provider_empty",
                        }
                    if stream_responses:
                        print("[stop] reason=empty-response")
                    break

                for provider_message in provider_result.messages:
                    # Log model response
                    self._log_provider_message(
                        provider_message, session_state, markdown_logger, stream_responses
                    )

                    # Handle empty responses per message
                    if not provider_message.tool_calls and not (provider_message.content or ""):
                        empty_choice = SimpleNamespace(
                            finish_reason=provider_message.finish_reason,
                            index=provider_message.index,
                        )
                        session_state.add_transcript_entry(
                            error_handler.handle_empty_response(empty_choice)
                        )
                        if not getattr(session_state, "completion_summary", None):
                            session_state.completion_summary = {
                                "completed": False,
                                "reason": "empty_response",
                                "method": "provider_empty",
                            }
                        if stream_responses:
                            print("[stop] reason=empty-response")
                        completed = False
                        break

                    # Process tool calls or content
                    completion_detected = self._process_model_output(
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

                    if completion_detected:
                        completed = True
                        if not getattr(session_state, "completion_summary", None):
                            session_state.completion_summary = {
                                "completed": True,
                                "reason": "completion_detected",
                                "method": "completion_detector",
                            }
                        else:
                            session_state.completion_summary.setdefault("completed", True)
                        break
                if completed:
                    break

                if session_state.turn_had_tool_activity():
                    session_state.reset_tool_free_streak()
                else:
                    todos_cfg = self._todo_config()
                    if todos_cfg["enabled"] and todos_cfg["reset_streak_on_todo"] and session_state.turn_had_todo_activity():
                        session_state.reset_tool_free_streak()
                        continue
                    streak = session_state.increment_tool_free_streak()
                    abort_threshold = getattr(self, "zero_tool_abort_turns", ZERO_TOOL_ABORT_TURNS)
                    warn_threshold = getattr(self, "zero_tool_warn_turns", ZERO_TOOL_WARN_TURNS)
                    if abort_threshold and streak >= abort_threshold:
                        warning = (
                            "<VALIDATION_ERROR>\n"
                            "No tool usage detected across multiple turns. The run is being stopped to avoid idle looping.\n"
                            "</VALIDATION_ERROR>"
                        )
                        session_state.add_message({"role": "user", "content": warning}, to_provider=True)
                        try:
                            markdown_logger.log_user_message(warning)
                        except Exception:
                            pass
                        if stream_responses:
                            print("[guard] aborting due to zero tool usage streak")
                        session_state.completion_summary = {
                            "completed": False,
                            "reason": "no_tool_activity",
                            "method": "policy_violation",
                        }
                        extra_payload = {"zero_tool_streak": streak, "threshold": abort_threshold}
                        if getattr(self, "zero_tool_emit_event", True):
                            session_state.add_transcript_entry({
                                "zero_tool_watchdog": {
                                    "action": "abort",
                                    "streak": streak,
                                    "threshold": abort_threshold,
                                }
                            })
                        session_state.increment_guardrail_counter("zero_tool_abort")
                        return finalize_run("policy_violation", "no_tool_activity", extra_payload)
                    elif warn_threshold and streak == warn_threshold:
                        warning = (
                            "<VALIDATION_ERROR>\n"
                            "No tool usage detected. Use read/list/diff/bash tools to make progress before responding again.\n"
                            "</VALIDATION_ERROR>"
                        )
                        session_state.add_message({"role": "user", "content": warning}, to_provider=True)
                        try:
                            markdown_logger.log_user_message(warning)
                        except Exception:
                            pass
                        if getattr(self, "zero_tool_emit_event", True):
                            session_state.add_transcript_entry({
                                "zero_tool_watchdog": {
                                    "action": "warn",
                                    "streak": streak,
                                    "threshold": warn_threshold,
                                }
                            })
                        session_state.increment_guardrail_counter("zero_tool_warnings")
                        if stream_responses:
                            print("[guard] zero-tool warning issued")

                if session_state.get_provider_metadata("completion_guard_abort", False):
                    session_state.completion_summary = {
                        "completed": False,
                        "reason": "completion_guard_failed",
                        "method": "completion_guard",
                    }
                    extra_payload = {
                        "guard_failures": session_state.guard_failure_count(),
                        "tool_usage_summary": getattr(session_state, "tool_usage_summary", {}),
                    }
                    return finalize_run("policy_violation", "completion_guard_failed", extra_payload)

            except Exception as e:
                # Handle provider errors and surface immediately to logs/stdout
                result = error_handler.handle_provider_error(
                    e, session_state.messages, session_state.transcript
                )
                warning_text = (
                    f"[provider-error] {result.get('error_type', type(e).__name__)}: "
                    f"{result.get('error', str(e))}"
                )
                try:
                    markdown_logger.log_system_message(warning_text)
                except Exception:
                    pass
                try:
                    session_state.add_transcript_entry({
                        "provider_error": {
                            "type": result.get("error_type"),
                            "message": result.get("error"),
                            "hint": result.get("hint"),
                        }
                    })
                except Exception:
                    pass
                try:
                    if getattr(self.logger_v2, "run_dir", None):
                        turn_idx = session_state.get_provider_metadata(
                            "current_turn_index",
                            len(session_state.transcript) + 1,
                        )
                        self._persist_error_artifacts(turn_idx, result)
                except Exception:
                    pass
                try:
                    print(warning_text)
                    if result.get("hint"):
                        print(f"Hint: {result['hint']}")
                    if result.get("traceback"):
                        print(result["traceback"])
                    details = result.get("details")
                    snippet = None
                    if isinstance(details, dict):
                        snippet = details.get("body_snippet")
                    if snippet:
                        print(f"Provider response snippet: {snippet}")
                except Exception:
                    pass
                summary = dict(session_state.completion_summary or {})
                summary.setdefault("completed", False)
                if result.get("error_type"):
                    summary.setdefault("reason", result.get("error_type"))
                    summary.setdefault("error_type", result.get("error_type"))
                else:
                    summary.setdefault("reason", "provider_error")
                if result.get("hint"):
                    summary.setdefault("hint", result.get("hint"))
                summary.setdefault("method", "provider_error")
                session_state.completion_summary = summary
                extra_payload = {
                    "provider_error": result,
                    "error": result.get("error"),
                    "error_type": result.get("error_type"),
                    "hint": result.get("hint"),
                    "details": result.get("details"),
                }
                completed = False
                return finalize_run("provider_error", summary.get("reason", "provider_error"), extra_payload)

        # Post-loop finalization for successful or exhausted runs
        steps_taken = (step_index + 1) if step_index >= 0 else 0
        if not session_state.completion_summary:
            if completed:
                reason = "completion_detected"
            elif max_steps and steps_taken >= max_steps:
                reason = "max_steps_exhausted"
            else:
                reason = "loop_terminated"
            session_state.completion_summary = {
                "completed": completed,
                "reason": reason,
                "method": "loop_exit",
            }

        default_reason = session_state.completion_summary.get("reason", "loop_terminated")
        return finalize_run("loop_exit", default_reason)

    def _apply_streaming_policy_for_turn(
        self,
        runtime,
        model: str,
        tools_schema: Optional[List[Dict[str, Any]]],
        stream_requested: bool,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        turn_index: int,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Apply provider-specific streaming policy for the current turn."""

        effective_stream = stream_requested
        policy: Optional[Dict[str, Any]] = None
        descriptor = getattr(runtime, "descriptor", None)
        provider_id = getattr(descriptor, "provider_id", None)
        runtime_id = getattr(descriptor, "runtime_id", None)

        if stream_requested and tools_schema and provider_id == "openrouter":
            effective_stream = False
            policy = {
                "turn_index": turn_index,
                "model": model,
                "provider": provider_id,
                "runtime": runtime_id,
                "reason": "openrouter_tool_turn_policy",
                "stream_requested": True,
                "stream_effective": False,
            }
            self.provider_metrics.add_stream_override(
                route=getattr(self, "_current_route_id", None),
                reason="openrouter_tool_turn_policy",
            )
            try:
                session_state.add_transcript_entry({"stream_policy": policy})
            except Exception:
                pass
            note = (
                "[stream-policy] Forcing stream=false for OpenRouter tool turn "
                f"(turn={turn_index}, model={model})."
            )
            try:
                markdown_logger.log_system_message(note)
            except Exception:
                pass
            try:
                if getattr(self.logger_v2, "run_dir", None):
                    self.logger_v2.append_text(
                        "conversation/conversation.md",
                        self.md_writer.system(note),
                    )
            except Exception:
                pass
        route_id = getattr(self, "_current_route_id", None)
        capability = self._get_capability_probe_result(session_state, route_id)
        routing_prefs = self._get_model_routing_preferences(route_id)
        if (
            capability
            and capability.get("attempted")
            and capability.get("stream_success") is False
            and effective_stream
            and routing_prefs.get("disable_stream_on_probe_failure", True)
        ):
            effective_stream = False
            override_payload = {
                "turn_index": turn_index,
                "model": model,
                "provider": provider_id,
                "runtime": runtime_id,
                "reason": "capability_probe_stream_failure",
                "stream_requested": stream_requested,
                "stream_effective": False,
                "route_id": route_id,
            }
            if policy is None:
                override_payload["reasons"] = ["capability_probe_stream_failure"]
                policy = override_payload
            else:
                merged = dict(policy)
                reasons = merged.get("reasons")
                if not isinstance(reasons, list):
                    reasons_list: List[str] = []
                    if merged.get("reason"):
                        reasons_list.append(merged["reason"])
                else:
                    reasons_list = list(reasons)
                reasons_list.append("capability_probe_stream_failure")
                merged["reasons"] = reasons_list
                merged["capability_override"] = True
                merged["stream_effective"] = False
                merged["stream_requested"] = stream_requested
                merged["route_id"] = route_id
                policy = merged
            payload = {
                "route": route_id,
                "capabilities": {
                    "stream_success": capability.get("stream_success"),
                    "tool_stream_success": capability.get("tool_stream_success"),
                    "json_mode_success": capability.get("json_mode_success"),
                },
                "policy": policy,
            }
            message = (
                f"[stream-policy] Disabled streaming for route '{route_id}' based on capability probe results."
            )
            self.provider_metrics.add_stream_override(
                route=route_id,
                reason="capability_probe_stream_failure",
            )
            self._log_routing_event(
                session_state,
                markdown_logger,
                turn_index=turn_index,
                tag="stream_policy",
                message=message,
                payload=payload,
            )
            try:
                session_state.set_provider_metadata("capability_stream_override", {
                    "route": route_id,
                    "turn_index": turn_index,
                    "reason": "capability_probe_stream_failure",
                })
            except Exception:
                pass
        if policy is not None:
            self._record_stream_policy_metadata(session_state, policy)
        return effective_stream, policy

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
        """Attempt a retry and fallback routing when the primary call fails."""

        descriptor = getattr(runtime, "descriptor", None)
        provider_id = getattr(descriptor, "provider_id", None)
        runtime_id = getattr(descriptor, "runtime_id", None)

        def _record_degraded(route_model: str, reason: str) -> None:
            try:
                degraded = session_state.get_provider_metadata("degraded_routes", {})
                if not isinstance(degraded, dict):
                    degraded = {}
                info = degraded.get(route_model, {})
                history = info.get("history", [])
                if not isinstance(history, list):
                    history = [history] if history else []
                history.append(
                    {
                        "reason": reason,
                        "provider": provider_id,
                        "runtime": runtime_id,
                        "turn": session_state.get_provider_metadata("current_turn_index"),
                    }
                )
                info.update({
                    "reason": reason,
                    "provider": provider_id,
                    "runtime": runtime_id,
                    "history": history,
                })
                degraded[route_model] = info
                session_state.set_provider_metadata("degraded_routes", degraded)
            except Exception:
                pass

        def _sleep_with_jitter(base: float) -> None:
            jitter = base * 0.25
            wait_time = base + random.uniform(-jitter, jitter)
            if wait_time < 0:
                wait_time = base
            try:
                time.sleep(wait_time)
            except Exception:
                pass

        def _simplify_result(result: ProviderResult) -> ProviderResult:
            return result

        def _invoke(target_model: str) -> ProviderResult:
            return runtime.invoke(
                client=client,
                model=target_model,
                messages=messages,
                tools=tools_schema,
                stream=stream_responses,
                context=runtime_context,
            )

        def _log_retry(route_model: str, reason: str, attempt: str) -> None:
            message = (
                f"[provider-retry] route={route_model} attempt={attempt} reason={reason}"
            )
            try:
                markdown_logger.log_system_message(message)
            except Exception:
                pass
            try:
                if getattr(self.logger_v2, "run_dir", None):
                    self.logger_v2.append_text(
                        "conversation/conversation.md",
                        self.md_writer.system(message),
                    )
            except Exception:
                pass
            try:
                session_state.add_transcript_entry({
                    "provider_retry": {
                        "route": route_model,
                        "attempt": attempt,
                        "reason": reason,
                    }
                })
            except Exception:
                pass

        if last_error and not attempted:
            self.route_health.record_failure(model, str(last_error))
            self._update_health_metadata(session_state)

        same_route_reason = str(last_error) if last_error else "retry"
        backoff_seconds = 0.6
        _log_retry(model, same_route_reason, "retry")
        _sleep_with_jitter(backoff_seconds)

        try:
            result = _invoke(model)
            attempted.append((model, stream_responses, None))
            self.route_health.record_success(model)
            self._update_health_metadata(session_state)
            return _simplify_result(result)
        except ProviderRuntimeError as retry_error:
            attempted.append((model, stream_responses, str(retry_error) or retry_error.__class__.__name__))
            last_error = retry_error
            self.route_health.record_failure(model, str(retry_error) or retry_error.__class__.__name__)
            self._update_health_metadata(session_state)

        route_id = None
        if runtime_context and isinstance(runtime_context.extra, dict):
            route_id = runtime_context.extra.get("route_id")
        routing_prefs = self._get_model_routing_preferences(route_id)
        explicit_fallbacks = routing_prefs.get("fallback_models") or []
        fallback_model, fallback_diag = self._select_fallback_route(
            route_id,
            provider_id,
            model,
            explicit_fallbacks,
        )

        if not fallback_model:
            if last_error:
                raise last_error
            return None

        fallback_reason = str(last_error) if last_error else "fallback"
        _record_degraded(model, fallback_reason)
        _log_retry(fallback_model, fallback_reason, "fallback")
        self.provider_metrics.add_fallback(primary=model, fallback=fallback_model, reason=fallback_reason)
        turn_hint = None
        if runtime_context and isinstance(runtime_context.extra, dict):
            turn_hint = runtime_context.extra.get("turn_index")
        self._log_routing_event(
            session_state,
            markdown_logger,
            turn_index=turn_hint,
            tag="fallback_route",
            message=(
                f"[routing] Selected fallback route '{fallback_model}' after '{fallback_reason}'."
            ),
            payload={
                "from": route_id or model,
                "reason": fallback_reason,
                "diagnostics": fallback_diag,
            },
        )

        try:
            fallback_runtime_descriptor, fallback_model_resolved = provider_router.get_runtime_descriptor(fallback_model)
            fallback_runtime = provider_registry.create_runtime(fallback_runtime_descriptor)
            fallback_client_config = provider_router.create_client_config(fallback_model)
            fallback_runtime_context = ProviderRuntimeContext(
                session_state=runtime_context.session_state,
                agent_config=runtime_context.agent_config,
                stream=False,
                extra=dict(runtime_context.extra or {}, fallback_of=model, route_id=fallback_model),
            )
            fallback_client = fallback_runtime.create_client(
                fallback_client_config["api_key"],
                base_url=fallback_client_config.get("base_url"),
                default_headers=fallback_client_config.get("default_headers"),
            )
            try:
                if getattr(self.logger_v2, "include_structured_requests", True):
                    turn_idx = runtime_context.extra.get("turn_index") if runtime_context.extra else None
                    if turn_idx is not None:
                        try:
                            turn_for_record = int(turn_idx)
                        except Exception:
                            turn_for_record = None
                    else:
                        turn_for_record = None
                    if turn_for_record is not None:
                        headers_snapshot = dict(fallback_client_config.get("default_headers") or {})
                        if getattr(fallback_runtime_descriptor, "provider_id", None) == "openrouter":
                            headers_snapshot.setdefault("Accept", "application/json; charset=utf-8")
                            headers_snapshot.setdefault("Accept-Encoding", "identity")
                        self.structured_request_recorder.record_request(
                            turn_for_record,
                            provider_id=fallback_runtime_descriptor.provider_id,
                            runtime_id=fallback_runtime_descriptor.runtime_id,
                            model=fallback_model_resolved,
                            request_headers=headers_snapshot,
                            request_body={
                                "model": fallback_model_resolved,
                                "messages": messages,
                                "tools": tools_schema,
                                "stream": False,
                            },
                            stream=False,
                            tool_count=len(tools_schema or []),
                            endpoint=fallback_client_config.get("base_url"),
                            attempt=len(attempted),
                            extra={"fallback_of": model},
                        )
            except Exception:
                pass
            start_ts = time.time()
            result = fallback_runtime.invoke(
                client=fallback_client,
                model=fallback_model_resolved,
                messages=messages,
                tools=tools_schema,
                stream=False,
                context=fallback_runtime_context,
            )
            elapsed = time.time() - start_ts
            try:
                self.provider_metrics.add_call(
                    fallback_model_resolved,
                    stream=False,
                    elapsed=elapsed,
                    outcome="success",
                )
            except Exception:
                pass
            session_state.set_provider_metadata("fallback_route", {
                "from": model,
                "to": fallback_model,
                "provider": fallback_runtime_descriptor.provider_id,
            })
            self.route_health.record_success(fallback_model)
            self._update_health_metadata(session_state)
            return result
        except Exception as exc:
            elapsed = time.time() - start_ts if 'start_ts' in locals() else 0.0
            try:
                self.provider_metrics.add_call(
                    fallback_model,
                    stream=False,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason=str(exc),
                )
            except Exception:
                pass
            attempted.append((fallback_model, False, str(exc) or exc.__class__.__name__))
            if last_error:
                raise last_error
            raise ProviderRuntimeError(str(exc)) from exc

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

        fallback_stream_reason: Optional[str] = None
        result: Optional[ProviderResult] = None
        last_error: Optional[ProviderRuntimeError] = None
        success_recorded = False
        self._last_runtime_latency = None
        self._last_html_detected = False

        if self.route_health.is_circuit_open(model):
            notice = (
                f"[circuit-open] Skipping direct call for route {model}; attempting fallback."
            )
            self.provider_metrics.add_circuit_skip(model)
            try:
                markdown_logger.log_system_message(notice)
            except Exception:
                pass
            try:
                if getattr(self.logger_v2, "run_dir", None):
                    self.logger_v2.append_text(
                        "conversation/conversation.md",
                        self.md_writer.system(notice),
                    )
            except Exception:
                pass
            try:
                session_state.add_transcript_entry({"circuit_open": {"model": model, "notice": notice}})
            except Exception:
                pass
            circuit_error = ProviderRuntimeError("route_circuit_open")
            fallback_result = self._retry_with_fallback(
                runtime,
                client,
                model,
                send_messages,
                tools_schema,
                runtime_context,
                stream_responses=False,
                session_state=session_state,
                markdown_logger=markdown_logger,
                attempted=[],
                last_error=circuit_error,
            )
            self._update_health_metadata(session_state)
            if fallback_result is not None:
                return fallback_result, False
            raise circuit_error

        def _is_tool_turn() -> bool:
            if not tools_schema:
                return False
            for msg in reversed(session_state.messages):
                role = msg.get("role")
                if role == "assistant":
                    tool_calls = msg.get("tool_calls") or []
                    return bool(tool_calls)
                if role == "user":
                    break
            return False

        attempted_models = []

        def _call_runtime(target_model: str, use_stream: bool) -> ProviderResult:
            start_time = time.time()
            try:
                call_result = runtime.invoke(
                    client=client,
                    model=target_model,
                    messages=send_messages,
                    tools=tools_schema,
                    stream=use_stream,
                    context=runtime_context,
                )
                elapsed = time.time() - start_time
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="success",
                )
                self._last_runtime_latency = elapsed
                self._last_html_detected = False
                return call_result
            except ProviderRuntimeError as exc:
                elapsed = time.time() - start_time
                details = getattr(exc, "details", None)
                html_detected = False
                if isinstance(details, dict):
                    html_detected = bool(details.get("html_detected"))
                self.provider_metrics.add_call(
                    target_model,
                    stream=use_stream,
                    elapsed=elapsed,
                    outcome="error",
                    error_reason=str(exc),
                    html_detected=html_detected,
                    details=details if isinstance(details, dict) else None,
                )
                self._last_runtime_latency = elapsed
                self._last_html_detected = html_detected
                raise

        def _maybe_disable_stream(reason: str) -> None:
            try:
                session_state.set_provider_metadata("streaming_disabled", True)
            except Exception:
                pass
            warning_payload = {
                "provider": runtime.descriptor.provider_id,
                "runtime": runtime.descriptor.runtime_id,
                "reason": reason,
            }
            try:
                session_state.add_transcript_entry({"streaming_disabled": warning_payload})
            except Exception:
                pass
            warning_text = (
                "[streaming-disabled] "
                f"Provider {runtime.descriptor.provider_id} ({runtime.descriptor.runtime_id}) "
                f"rejected streaming: {reason}. Falling back to non-streaming."
            )
            try:
                markdown_logger.log_system_message(warning_text)
            except Exception:
                pass
            try:
                if getattr(self.logger_v2, "run_dir", None):
                    self.logger_v2.append_text(
                        "conversation/conversation.md",
                        self.md_writer.system(warning_text),
                    )
            except Exception:
                pass
            try:
                print(warning_text)
            except Exception:
                pass

        if stream_responses:
            try:
                result = _call_runtime(model, True)
                attempted_models.append((model, True, None))
                self.route_health.record_success(model)
                success_recorded = True
                self._update_health_metadata(session_state)
            except ProviderRuntimeError as exc:
                fallback_stream_reason = str(exc) or exc.__class__.__name__
                last_error = exc
                attempted_models.append((model, True, fallback_stream_reason))
                self.route_health.record_failure(model, fallback_stream_reason)
                self._update_health_metadata(session_state)

        used_streaming = stream_responses and result is not None

        if result is None:
            if fallback_stream_reason and stream_responses:
                warning_payload = {
                    "provider": runtime.descriptor.provider_id,
                    "runtime": runtime.descriptor.runtime_id,
                    "reason": fallback_stream_reason,
                }
                self.provider_metrics.add_stream_override(
                    route=getattr(self, "_current_route_id", None),
                    reason=fallback_stream_reason,
                )
                session_state.add_transcript_entry({"streaming_disabled": warning_payload})
                warning_text = (
                    "[streaming-disabled] "
                    f"Provider {runtime.descriptor.provider_id} ({runtime.descriptor.runtime_id}) "
                    f"rejected streaming: {fallback_stream_reason}. Falling back to non-streaming."
                )
                try:
                    markdown_logger.log_system_message(warning_text)
                except Exception:
                    pass
                try:
                    if getattr(self.logger_v2, "run_dir", None):
                        self.logger_v2.append_text(
                            "conversation/conversation.md",
                            self.md_writer.system(warning_text),
                        )
                except Exception:
                    pass
                try:
                    session_state.set_provider_metadata("streaming_disabled", True)
                except Exception:
                    pass
                try:
                    print(warning_text)
                except Exception:
                    pass
            try:
                result = _call_runtime(model, False)
                attempted_models.append((model, False, None))
                used_streaming = False
                self.route_health.record_success(model)
                success_recorded = True
                self._update_health_metadata(session_state)
            except ProviderRuntimeError as exc:
                last_error = exc
                attempted_models.append((model, False, str(exc) or exc.__class__.__name__))
                result = None
                self.route_health.record_failure(model, str(exc) or exc.__class__.__name__)
                self._update_health_metadata(session_state)

        if result is None and _is_tool_turn():
            history = session_state.get_provider_metadata("streaming_disabled")
            if not history:
                reason_text = str(last_error) if last_error else "tool_turn_retry"
                _maybe_disable_stream(reason_text)

        if result is None:
            fallback_result = self._retry_with_fallback(
                runtime,
                client,
                model,
                send_messages,
                tools_schema,
                runtime_context,
                stream_responses=False,
                session_state=session_state,
                markdown_logger=markdown_logger,
                attempted=attempted_models,
                last_error=last_error,
            )
            if fallback_result is not None:
                result = fallback_result
                used_streaming = False
                success_recorded = True
            elif last_error:
                raise last_error


        try:
            if result is not None:
                session_state.set_provider_metadata("raw_finish_meta", result.metadata)
        except Exception:
            pass

        if result is not None and not success_recorded:
            self.route_health.record_success(model)
            self._update_health_metadata(session_state)
        else:
            self._update_health_metadata(session_state)

        return result, used_streaming

        steps_taken = (step_index + 1) if step_index >= 0 else 0

        if not getattr(session_state, "completion_summary", None):
            reason = "max_steps_exhausted" if max_steps and steps_taken >= max_steps and not completed else "loop_terminated"
            session_state.completion_summary = {
                "completed": completed,
                "reason": reason,
                "method": "loop_exit",
            }

        session_state.completion_summary.setdefault("completed", completed)
        session_state.completion_summary.setdefault("steps_taken", steps_taken)
        session_state.completion_summary.setdefault("max_steps", max_steps)

        # Final snapshot and return
        try:
            diff = self.vcs({"action": "diff", "params": {"staged": False, "unified": 3}})
        except Exception:
            diff = {"ok": False, "data": {"diff": ""}}

        session_state.write_snapshot(output_json_path, model, diff)
        if stream_responses:
            print(f"[stop] reason=end-of-loop steps={steps_taken}")

        completion_summary = session_state.completion_summary or {}
        completion_reason = completion_summary.get("reason", "unknown")
        result = {
            "messages": session_state.messages,
            "transcript": session_state.transcript,
            "completion_summary": completion_summary,
            "completion_reason": completion_reason,
            "completed": completion_summary.get("completed", False),
        }
        return result
    
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
        local_tools_prompt: str
    ) -> ProviderResult:
        """Get response from the model with proper tool configuration."""
        # Build per-turn message list
        send_messages = [dict(m) for m in session_state.provider_messages]
        
        # Ensure last message is user; if not, append a user wrapper for tools prompt
        if not send_messages or send_messages[-1].get("role") != "user":
            send_messages.append({"role": "user", "content": ""})
        
        # Add tool prompts based on mode
        turn_index = len(session_state.transcript) + 1
        try:
            session_state.begin_turn(turn_index)
        except Exception:
            session_state.set_provider_metadata("current_turn_index", turn_index)
        per_turn_written_text = None
        if tool_prompt_mode in ("per_turn_append", "system_and_per_turn"):
            # Build full tool directive text (restored from original logic)
            tool_directive_text = (
                "\n\nSYSTEM MESSAGE - AVAILABLE TOOLS\n"
                f"<FUNCTIONS>\n{local_tools_prompt}\n</FUNCTIONS>\n"
                "MANDATORY: Respond ONLY with one or more tool calls using <TOOL_CALL> ..., with <BASH>...</BASH> for shell, or with diff blocks (SEARCH/REPLACE for edits; unified diff or OpenCode Add File for new files).\n"
                "You may call multiple tools in one reply; non-blocking tools may run concurrently.\n"
                "Some tools are blocking and must run alone in sequence.\n"
                "Do NOT include any extra prose beyond tool calls or diff blocks.\n"
                "When you deem the task fully complete, call mark_task_complete(). If you cannot call tools, end your reply with a single line `TASK COMPLETE`.\n"
                "NEVER use bash to write large file contents (heredocs, echo >>). For files: call create_file() then apply a diff block for contents.\n"
                "Do NOT include extra prose.\nEND SYSTEM MESSAGE\n"
            )
            send_messages[-1]["content"] = (send_messages[-1].get("content") or "") + tool_directive_text
            markdown_logger.log_tool_availability([t.name for t in tool_defs])
            per_turn_written_text = tool_directive_text
        elif tool_prompt_mode == "system_compiled_and_persistent_per_turn":
            # Add per-turn availability
            compiler = get_compiler()
            enabled_tools = [t.name for t in tool_defs]
            per_turn_availability = compiler.format_per_turn_availability(enabled_tools, active_dialect_names)
            
            # Permanently append to the actual message history
            send_messages[-1]["content"] = (send_messages[-1].get("content") or "") + "\n\n" + per_turn_availability
            session_state.provider_messages[-1]["content"] = (session_state.provider_messages[-1].get("content") or "") + "\n\n" + per_turn_availability
            per_turn_written_text = per_turn_availability

        # Persist per-turn availability content and add transcript section
        try:
            if per_turn_written_text and self.logger_v2.run_dir:
                rel = self.prompt_logger.save_per_turn(turn_index, per_turn_written_text)
                self.logger_v2.append_text("conversation/conversation.md", self.md_writer.tools_available_temp("Per-turn tools prompt appended.", rel))
        except Exception:
            pass
        
        # Add context to transcript  
        session_state.add_transcript_entry({
            "tools_context": {
                "available_tools": _dump_tool_defs(tool_defs),
                "compiled_tools_prompt": local_tools_prompt,
            }
        })
        
        # Determine if native tools should be used
        provider_tools_cfg = getattr(self, "_provider_tools_effective", None) or dict((self.config.get("provider_tools") or {}))
        effective_config = dict(self.config)
        effective_config["provider_tools"] = provider_tools_cfg
        self._provider_tools_effective = provider_tools_cfg
        use_native_tools = provider_router.should_use_native_tools(model, effective_config)
        tools_schema = None
        if use_native_tools and getattr(self, "current_native_tools", None):
            try:
                provider_id = provider_router.parse_model_id(model)[0]
                native_tools = getattr(self, 'current_native_tools', [])
                if native_tools:
                    tools_schema = provider_adapter_manager.translate_tools_to_native_schema(native_tools, provider_id)
                    # Persist provider tools provided
                    try:
                        if self.logger_v2.run_dir and tools_schema:
                            self.provider_logger.save_tools_provided(turn_index, tools_schema)
                            # Also append transcript section listing tool IDs if available
                            ids = []
                            try:
                                # OpenAI style
                                for it in tools_schema:
                                    fn = (it.get("function") or {}).get("name")
                                    if fn:
                                        ids.append(str(fn))
                            except Exception:
                                pass
                            self.logger_v2.append_text("conversation/conversation.md", self.md_writer.provider_tools_provided(ids or ["(see JSON)"], f"provider_native/tools_provided/turn_{turn_index}.json"))
                    except Exception:
                        pass
            except Exception:
                tools_schema = None
        
        effective_stream_responses, stream_policy = self._apply_streaming_policy_for_turn(
            runtime,
            model,
            tools_schema,
            stream_responses,
            session_state,
            markdown_logger,
            turn_index,
        )
        try:
            session_state.set_provider_metadata("current_stream_requested", stream_responses)
            session_state.set_provider_metadata("current_stream_effective", effective_stream_responses)
        except Exception:
            pass

        # Persist raw request (pre-call)
        try:
            if self.logger_v2.include_raw:
                self.api_recorder.save_request(turn_index, {
                    "model": model,
                    "messages": send_messages,
                    "tools": tools_schema,
                    "stream": effective_stream_responses,
                })
        except Exception:
            pass

        # Make API call
        runtime_extra = {
            "turn_index": turn_index,
            "model": model,
            "stream": effective_stream_responses,
            "route_id": self._current_route_id,
        }
        if stream_policy is not None:
            runtime_extra["stream_policy"] = stream_policy

        runtime_context = ProviderRuntimeContext(
            session_state=session_state,
            agent_config=self.config,
            stream=effective_stream_responses,
            extra=runtime_extra,
        )

        # Persist structured request snapshot for incident response triage
        try:
            request_headers = dict(client_config.get("default_headers") or {})
            if getattr(runtime, "descriptor", None) and getattr(runtime.descriptor, "provider_id", None) == "openrouter":
                request_headers.setdefault("Accept", "application/json; charset=utf-8")
                request_headers.setdefault("Accept-Encoding", "identity")
        except Exception:
            request_headers = {}

        try:
            if getattr(self.logger_v2, "include_structured_requests", True):
                extra_meta: Dict[str, Any] = {
                    "message_count": len(send_messages or []),
                    "has_tools": bool(tools_schema),
                }
                if stream_policy:
                    extra_meta["stream_policy"] = {
                        "reason": stream_policy.get("reason"),
                        "stream_effective": stream_policy.get("stream_effective"),
                    }
                self.structured_request_recorder.record_request(
                    turn_index,
                    provider_id=getattr(runtime.descriptor, "provider_id", "unknown"),
                    runtime_id=getattr(runtime.descriptor, "runtime_id", "unknown"),
                    model=model,
                    request_headers=request_headers,
                    request_body={
                        "model": model,
                        "messages": send_messages,
                        "tools": tools_schema,
                        "stream": effective_stream_responses,
                    },
                    stream=effective_stream_responses,
                    tool_count=len(tools_schema or []),
                    endpoint=client_config.get("base_url"),
                    attempt=0,
                    extra=extra_meta,
                )
        except Exception:
            pass

        result, _ = self._invoke_runtime_with_streaming(
            runtime,
            client,
            model,
            send_messages,
            tools_schema,
            effective_stream_responses,
            runtime_context,
            session_state,
            markdown_logger,
            turn_index,
        )

        if (self.config.get("features", {}) or {}).get("response_normalizer"):
            try:
                normalized_events = normalize_provider_result(result)
                result.metadata.setdefault("normalized_events", normalized_events)
                session_state.set_provider_metadata("normalized_events", normalized_events)
                if self.logger_v2.run_dir:
                    self.logger_v2.write_json(
                        f"meta/turn_{turn_index}_normalized_events.json",
                        normalized_events,
                    )
            except Exception:
                pass

        usage_raw = result.usage or {}
        normalized_usage: Dict[str, Any] = {}
        if isinstance(usage_raw, dict):
            prompt_tokens = usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens")
            completion_tokens = usage_raw.get("completion_tokens") or usage_raw.get("output_tokens")
            if prompt_tokens is not None:
                normalized_usage["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                normalized_usage["completion_tokens"] = completion_tokens
            if usage_raw.get("cache_read_tokens") is not None:
                normalized_usage["cache_read_tokens"] = usage_raw.get("cache_read_tokens")
            if usage_raw.get("cache_write_tokens") is not None:
                normalized_usage["cache_write_tokens"] = usage_raw.get("cache_write_tokens")
            normalized_usage["model"] = model
        try:
            session_state.set_provider_metadata("usage_normalized", normalized_usage)
        except Exception:
            pass
        try:
            self._record_usage_reward_metrics(session_state, turn_index, usage_raw)
        except Exception:
            pass

        cursor_prefix = f"turn_{turn_index}"
        try:
            for msg_idx, prov_msg in enumerate(result.messages or []):
                if prov_msg.content:
                    session_state.add_ir_event(
                        IRDeltaEvent(
                            cursor=f"{cursor_prefix}:text:{msg_idx}",
                            type="text",
                            payload={"role": prov_msg.role, "content": prov_msg.content},
                        )
                    )
                if prov_msg.tool_calls:
                    for tc_idx, tc in enumerate(prov_msg.tool_calls):
                        session_state.add_ir_event(
                            IRDeltaEvent(
                                cursor=f"{cursor_prefix}:tool_call:{msg_idx}:{tc_idx}",
                                type="tool_call",
                                payload={
                                    "id": tc.id,
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                    "tool_type": tc.type,
                                },
                            )
                        )
        except Exception:
            pass

        try:
            finish_reason = None
            if result.messages:
                finish_reason = result.messages[-1].finish_reason
            session_state.add_ir_event(
                IRDeltaEvent(
                    cursor=f"{cursor_prefix}:finish",
                    type="finish",
                    payload={
                        "finish_reason": finish_reason,
                        "usage": normalized_usage,
                        "metadata": result.metadata,
                    },
                )
            )
        except Exception:
            pass

        # Persist raw response (post-call)
        try:
            if self.logger_v2.include_raw:
                raw_payload = result.raw_response
                if hasattr(raw_payload, "model_dump"):
                    serialized = raw_payload.model_dump()  # type: ignore[attr-defined]
                elif isinstance(raw_payload, dict):
                    serialized = raw_payload
                else:
                    try:
                        serialized = dict(raw_payload)
                    except Exception:
                        serialized = None
                if serialized is not None:
                    self.api_recorder.save_response(turn_index, serialized)
        except Exception:
            pass

        return result
    
    def _legacy_message_view(self, provider_message: ProviderMessage) -> SimpleNamespace:
        """Create a SimpleNamespace compatible with legacy helper functions."""

        tool_calls_ns: List[SimpleNamespace] = []
        for call in provider_message.tool_calls:
            function_ns = SimpleNamespace(
                name=call.name,
                arguments=call.arguments,
            )
            tool_calls_ns.append(
                SimpleNamespace(
                    id=call.id,
                    type=call.type,
                    function=function_ns,
                )
            )
        return SimpleNamespace(
            role=provider_message.role,
            content=provider_message.content,
            tool_calls=tool_calls_ns,
            raw_message=provider_message.raw_message,
            finish_reason=provider_message.finish_reason,
            index=provider_message.index,
        )

    def _log_provider_message(
        self,
        provider_message: ProviderMessage,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        stream_responses: bool,
    ) -> None:
        """Log the provider message and capture debug metadata."""

        legacy_msg = self._legacy_message_view(provider_message)

        try:
            debug_tc = None
            if getattr(legacy_msg, "tool_calls", None):
                debug_tc = [
                    {
                        "id": getattr(tc, "id", None),
                        "name": getattr(getattr(tc, "function", None), "name", None),
                    }
                    for tc in legacy_msg.tool_calls
                ]
            session_state.add_transcript_entry(
                {
                    "choice_debug": {
                        "finish_reason": provider_message.finish_reason,
                        "has_content": bool(getattr(legacy_msg, "content", None)),
                        "tool_calls_len": len(legacy_msg.tool_calls)
                        if getattr(legacy_msg, "tool_calls", None)
                        else 0,
                    }
                }
            )
        except Exception:
            pass

        if getattr(legacy_msg, "content", None):
            markdown_logger.log_assistant_message(str(legacy_msg.content))
            try:
                if self.logger_v2.run_dir:
                    self.logger_v2.append_text(
                        "conversation/conversation.md",
                        self.md_writer.assistant(str(legacy_msg.content)),
                    )
            except Exception:
                pass
        
        if stream_responses and getattr(legacy_msg, "content", None):
            try:
                print(str(legacy_msg.content))
            except Exception:
                pass
    
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
        """Process model output (tool calls or content) and return True if completion detected"""
        msg = self._legacy_message_view(provider_message)

        # Handle text-based tool calls
        if not getattr(msg, "tool_calls", None) and (msg.content or ""):
            return self._handle_text_tool_calls(
                msg, caller, tool_defs, session_state, markdown_logger, 
                error_handler, stream_responses
            )
        
        # Handle native tool calls
        if msg.tool_calls:
            return self._handle_native_tool_calls(
                msg, session_state, markdown_logger, error_handler, 
                stream_responses, model
            )
        
        # Handle regular assistant content (no tool calls)
        if msg.content:
            session_state.add_message({"role": "assistant", "content": msg.content})
            session_state.add_transcript_entry({"assistant": msg.content})
            
            # Check for completion
            assistant_history = session_state.get_provider_metadata("assistant_text_history", [])
            if not isinstance(assistant_history, list):
                assistant_history = []
            recent_tool_activity = session_state.get_provider_metadata("recent_tool_activity")
            mark_tool_available = session_state.get_provider_metadata("mark_task_complete_available")
            completion_analysis = completion_detector.detect_completion(
                msg_content=msg.content or "",
                choice_finish_reason=provider_message.finish_reason,
                tool_results=[],
                agent_config=self.config,
                recent_tool_activity=recent_tool_activity,
                assistant_history=assistant_history,
                mark_tool_available=mark_tool_available,
            )
            
            session_state.add_transcript_entry({"completion_analysis": completion_analysis})
            
            normalized_assistant_text = self._normalize_assistant_text(msg.content)
            if normalized_assistant_text:
                updated_history = (assistant_history + [normalized_assistant_text])[-5:]
                session_state.set_provider_metadata("assistant_text_history", updated_history)
            session_state.set_provider_metadata("recent_tool_activity", None)
            
            if completion_analysis["completed"] and completion_detector.meets_threshold(completion_analysis):
                guard_ok, guard_reason = self._completion_guard_check(session_state)
                if not guard_ok and guard_reason:
                    abort = self._emit_completion_guard_feedback(
                        session_state,
                        markdown_logger,
                        guard_reason,
                        stream_responses,
                    )
                    if abort:
                        session_state.set_provider_metadata("completion_guard_abort", True)
                    return False

                if stream_responses:
                    print(f"[stop] reason={completion_analysis['method']} confidence={completion_analysis['confidence']:.2f} - {completion_analysis['reason']}")
                session_state.add_transcript_entry({
                    "completion_detected": {
                        "method": completion_analysis["method"],
                        "confidence": completion_analysis["confidence"],
                        "reason": completion_analysis["reason"],
                        "content_analyzed": bool(msg.content),
                        "threshold_met": completion_detector.meets_threshold(completion_analysis)
                    }
                })
                if not getattr(session_state, "completion_summary", None):
                    session_state.completion_summary = {
                        "completed": True,
                        "method": completion_analysis["method"],
                        "reason": completion_analysis["reason"],
                        "confidence": completion_analysis["confidence"],
                        "source": "assistant_content",
                        "analysis": completion_analysis,
                    }
                else:
                    session_state.completion_summary.setdefault("completed", True)
                    session_state.completion_summary.setdefault("method", completion_analysis["method"])
                    session_state.completion_summary.setdefault("reason", completion_analysis["reason"])
                    session_state.completion_summary.setdefault("confidence", completion_analysis["confidence"])
                return True
        
        return False
    
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
        """Handle text-based tool calls (simplified version)"""
        session_state.set_provider_metadata("recent_tool_activity", None)
        parsed = caller.parse_all(msg.content, tool_defs)
        current_mode = session_state.get_provider_metadata("current_mode")
        if not parsed:
            # Record assistant message once and enforce plan guard if required.
            session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=False)
            session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
            if current_mode == "plan":
                manager = session_state.get_todo_manager()
                snapshot = manager.snapshot() if manager else None
                todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
                if not todos:
                    self._emit_todo_guard_violation(
                        session_state,
                        markdown_logger,
                        "Plan mode requires creating at least one todo via `todo.create` before using edit or bash tools.",
                    )
                    msg.content = ""
            return False
        parsed = self._expand_multi_file_patches(parsed, session_state, markdown_logger)
        
        # Add assistant message with synthetic tool calls to main messages (for debugging) only
        # Do NOT add synthetic tool calls to provider messages - that would confuse the provider
        synthetic_tool_calls = self.message_formatter.create_synthetic_tool_calls(parsed)
        assistant_entry = {
            "role": "assistant", 
            "content": msg.content,
            "tool_calls": synthetic_tool_calls
        }
        session_state.add_message(assistant_entry, to_provider=False)  # Only to main messages, NOT provider
        
        # Add a simple content-only assistant message to provider messages
        session_state.add_message({"role": "assistant", "content": msg.content}, to_provider=True)
        
        session_state.add_transcript_entry({
            "assistant_with_text_tool_calls": {
                "content": msg.content,
                "parsed_tools_count": len(parsed),
                "parsed_tools": [p.function for p in parsed]
            }
        })

        blocked_calls: List[Tuple[Any, str]] = []
        filtered_calls: List[Any] = []
        for call in parsed:
            block_reason = self._todo_guard_preflight(session_state, call, current_mode)
            if block_reason:
                blocked_calls.append((call, block_reason))
            else:
                filtered_calls.append(call)
        parsed = filtered_calls
        if blocked_calls:
            for blocked_call, reason in blocked_calls:
                self._emit_todo_guard_violation(
                    session_state,
                    markdown_logger,
                    reason,
                    blocked_call=blocked_call,
                )
        if not parsed:
            # All calls were blocked; prevent further text handling.
            msg.content = ""
            return False
        
        # Execute tool calls
        executed_results, failed_at_index, execution_error, plan_metadata = self.agent_executor.execute_parsed_calls(
            parsed, 
            self._exec_raw,
            transcript_callback=session_state.add_transcript_entry
        )

        session_state.add_transcript_entry({"tool_execution_plan": plan_metadata})
        turn_index = session_state.get_provider_metadata("current_turn_index")
        turn_index_int = turn_index if isinstance(turn_index, int) else None
        try:
            self.provider_metrics.add_concurrency_sample(
                turn=turn_index_int,
                plan=plan_metadata,
            )
        except Exception:
            pass

        # Record recent tool activity for completion heuristics
        recent_tools_summary: List[Dict[str, Any]] = []
        test_success: Optional[float] = None
        for tool_parsed, tool_result in executed_results:
            tool_name = getattr(tool_parsed, "function", None)
            tool_result_dict = tool_result if isinstance(tool_result, dict) else {}
            self._record_diff_metrics(
                tool_parsed,
                tool_result_dict,
                session_state=session_state,
                turn_index=turn_index_int,
            )
            metadata: Dict[str, Any] = {}
            command: str = ""
            guardrail_code = tool_result_dict.get("guardrail")
            if guardrail_code:
                try:
                    session_state.increment_guardrail_counter(str(guardrail_code))
                except Exception:
                    pass
            if tool_name == "run_shell":
                try:
                    command = str((getattr(tool_parsed, "arguments", {}) or {}).get("command", ""))
                except Exception:
                    command = ""
                if command and self._is_test_command(command):
                    exit_code = tool_result_dict.get("exit")
                    if isinstance(exit_code, int):
                        success = 1.0 if exit_code == 0 else 0.0
                        test_success = success if test_success is None else min(test_success, success)
            success_flag = not self.agent_executor.is_tool_failure(tool_name, tool_result_dict)
            if tool_name in ("apply_unified_patch", "apply_search_replace", "create_file_from_block"):
                metadata["is_write"] = True
            if tool_name == "run_shell":
                metadata["is_run_shell"] = True
                metadata["command"] = command
                exit_code_val = tool_result_dict.get("exit")
                if isinstance(exit_code_val, int):
                    metadata["exit_code"] = exit_code_val
                if command and self._is_test_command(command):
                    metadata["is_test_command"] = True
            if isinstance(turn_index_int, int):
                try:
                    session_state.record_tool_event(
                        turn_index_int,
                        tool_name or "",
                        success=success_flag,
                        metadata=metadata,
                    )
                except Exception:
                    pass
            recent_tools_summary.append({
                "name": tool_name,
                "read_only": self._is_read_only_tool(tool_name),
                "completion_action": isinstance(tool_result, dict) and tool_result.get("action") == "complete",
            })
        if turn_index_int is not None:
            self._record_lsp_reward_metrics(session_state, turn_index_int)
            self._record_test_reward_metric(session_state, turn_index_int, test_success)
        session_state.set_provider_metadata(
            "recent_tool_activity",
            {
                "tools": recent_tools_summary,
                "turn": session_state.get_provider_metadata("current_turn_index"),
            },
        )
        
        # Handle execution errors
        if execution_error:
            if execution_error.get("validation_failed"):
                session_state.increment_guardrail_counter("validation_errors")
                error_msg = error_handler.handle_validation_error(execution_error)
            elif execution_error.get("constraint_violation"):
                error_msg = error_handler.handle_constraint_violation(execution_error["error"])
            else:
                error_msg = f"<EXECUTION_ERROR>\n{execution_error['error']}\n</EXECUTION_ERROR>"
            
            session_state.add_message({"role": "user", "content": error_msg}, to_provider=True)
            markdown_logger.log_user_message(error_msg)
            
            if stream_responses:
                print(f"[error] {execution_error.get('error', 'Unknown error')}")
            try:
                if turn_index_int is not None:
                    if execution_error.get("validation_failed"):
                        session_state.add_reward_metric(turn_index_int, "SVS", 0.0)
                    session_state.add_reward_metric(turn_index_int, "CPS", 0.0)
                    self._record_lsp_reward_metrics(session_state, turn_index_int)
                    if test_success is not None:
                        self._record_test_reward_metric(session_state, turn_index_int, 0.0)
            except Exception:
                pass
            return False
        
        try:
            if turn_index_int is not None:
                session_state.add_reward_metric(turn_index_int, "SVS", 1.0)
                if plan_metadata.get("total_calls"):
                    executed_calls = plan_metadata.get("executed_calls", 0)
                    total_calls = plan_metadata.get("total_calls", 0)
                    cps_value = 1.0 if executed_calls == total_calls else 0.0
                    session_state.add_reward_metric(turn_index_int, "CPS", cps_value)
                if executed_results:
                    acs_value = 1.0 if failed_at_index == -1 else 0.0
                    session_state.add_reward_metric(turn_index_int, "ACS", acs_value)
        except Exception:
            pass

        # Check for completion via tool results
        for tool_parsed, tool_result in executed_results:
            if isinstance(tool_result, dict) and tool_result.get("action") == "complete":
                guard_ok, guard_reason = self._completion_guard_check(session_state)
                if not guard_ok and guard_reason:
                    abort = self._emit_completion_guard_feedback(
                        session_state,
                        markdown_logger,
                        guard_reason,
                        stream_responses,
                    )
                    if abort:
                        session_state.set_provider_metadata("completion_guard_abort", True)
                    continue

                # Format and show all executed results including the completion
                chunks = self.message_formatter.format_execution_results(executed_results, failed_at_index, len(parsed))
                provider_tool_msg = "\n\n".join(chunks)
                session_state.add_message({"role": "user", "content": provider_tool_msg}, to_provider=True)
                markdown_logger.log_user_message(provider_tool_msg)
                
                if not getattr(session_state, "completion_summary", None):
                    session_state.completion_summary = {
                        "completed": True,
                        "method": "tool_mark_task_complete",
                        "reason": "mark_task_complete",
                        "confidence": 1.0,
                        "tool": tool_parsed.function,
                        "tool_result": tool_result,
                        "source": "tool_call",
                    }
                else:
                    session_state.completion_summary.setdefault("completed", True)
                    session_state.completion_summary.setdefault("reason", "mark_task_complete")
                    session_state.completion_summary.setdefault("method", "tool_mark_task_complete")

                if stream_responses:
                    print(f"[stop] reason=tool_based confidence=1.0 - mark_task_complete() called")
                return True
        
        # Persist per-tool results as artifacts and collect links
        artifact_links: list[str] = []
        try:
            if self.logger_v2.run_dir:
                for idx, (tool_parsed, tool_result) in enumerate(executed_results):
                    rel = self.message_formatter.write_tool_result_file(self.logger_v2.run_dir, len(session_state.transcript) + 1, idx, tool_parsed.function, tool_result)
                    if rel:
                        artifact_links.append(rel)
        except Exception:
            pass

        # Format and relay results
        chunks = self.message_formatter.format_execution_results(executed_results, failed_at_index, len(parsed))
        
        # Add tool result messages to main messages array
        for tool_parsed, tool_result in executed_results:
            tool_result_entry = self.message_formatter.create_tool_result_entry(
                tool_parsed.function, tool_result, syntax_type="custom-pythonic"
            )
            session_state.add_message(tool_result_entry, to_provider=False)
        
        # Get turn strategy flow configuration
        turn_cfg = self.config.get("turn_strategy", {})
        flow_strategy = turn_cfg.get("flow", "assistant_continuation").lower()
        
        if flow_strategy == "assistant_continuation":
            # ASSISTANT CONTINUATION PATTERN: Create assistant message with tool results
            provider_tool_msg = "\n\n".join(chunks)
            assistant_continuation = {
                "role": "assistant",
                "content": f"\n\nTool execution results:\n{provider_tool_msg}"
            }
            session_state.add_message(assistant_continuation)
            markdown_logger.log_assistant_message(assistant_continuation["content"])
            try:
                if self.logger_v2.run_dir:
                    # First, log the assistant continuation text to conversation
                    self.logger_v2.append_text("conversation/conversation.md", self.md_writer.assistant(assistant_continuation["content"]))
                    # Then, add a compact artifact links section once per turn
                    if artifact_links:
                        self.logger_v2.append_text("conversation/conversation.md", self.md_writer.text_tool_results("Tool execution results appended.", artifact_links))
            except Exception:
                pass
        else:
            # USER INTERLEAVED PATTERN: Traditional user message relay
            provider_tool_msg = "\n\n".join(chunks)
            session_state.add_message({"role": "user", "content": provider_tool_msg}, to_provider=True)
            markdown_logger.log_user_message(provider_tool_msg)
            try:
                if self.logger_v2.run_dir:
                    # In user-interleaved, still present a single compact artifact links section
                    if artifact_links:
                        self.logger_v2.append_text("conversation/conversation.md", self.md_writer.text_tool_results("Tool execution results appended.", artifact_links))
            except Exception:
                pass
        
        self._maybe_transition_plan_mode(session_state, markdown_logger)
        return False
    
    def _handle_native_tool_calls(
        self,
        msg,
        session_state: SessionState,
        markdown_logger: MarkdownLogger,
        error_handler: ErrorHandler,
        stream_responses: bool,
        model: str
    ) -> bool:
        """Handle native tool calls with proper provider tool result formatting"""
        # Execute provider-native tool calls and relay results using provider-supported 'tool' role
        turn_cfg = self.config.get("turn_strategy", {})
        relay_strategy = (turn_cfg.get("relay") or "tool_role").lower()
        
        tool_messages_to_relay: List[Dict[str, Any]] = []
        
        try:
            # 1) Append the assistant message that contains tool_calls to maintain linkage
            try:
                tool_calls_payload = []
                for tc in msg.tool_calls:
                    fn_name = getattr(getattr(tc, "function", None), "name", None)
                    arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
                    tool_calls_payload.append({
                        "id": getattr(tc, "id", None),
                        "type": "function",
                        "function": {"name": fn_name, "arguments": arg_str if isinstance(arg_str, str) else json.dumps(arg_str or {})},
                    })
                # Persist provider-native tool calls and add transcript section
                try:
                    if self.logger_v2.run_dir and tool_calls_payload:
                        turn_index = len(session_state.transcript) + 1
                        self.provider_logger.save_tool_calls(turn_index, tool_calls_payload)
                        short = "\n".join([f"- {c['function']['name']} (id={c.get('id')})" for c in tool_calls_payload])
                        self.logger_v2.append_text("conversation/conversation.md", self.md_writer.provider_tool_calls(short, f"provider_native/tool_calls/turn_{turn_index}.json"))
                except Exception:
                    pass
                
                # CRITICAL: Add assistant message with tool calls to BOTH arrays
                # Enhanced assistant entry with syntax type metadata
                enhanced_tool_calls = self.message_formatter.create_enhanced_tool_calls(tool_calls_payload)
                
                assistant_entry = {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": enhanced_tool_calls,
                }
                session_state.add_message(assistant_entry, to_provider=False)  # For complete session history in JSON output
                session_state.add_message({"role": "assistant", "content": msg.content, "tool_calls": tool_calls_payload}, to_provider=True)  # For provider (no enhanced fields)
                
                # Add to transcript for debugging
                session_state.add_transcript_entry({
                    "assistant_with_tool_calls": {
                        "content": msg.content,
                        "tool_calls_count": len(msg.tool_calls),
                        "tool_calls": [tc["function"]["name"] for tc in tool_calls_payload]
                    }
                })
            except Exception:
                pass
            
            # 2) Execute calls via shared agent executor to honor concurrency + alias policies
            parsed_calls: List[Any] = []
            for tc in msg.tool_calls:
                fn = getattr(getattr(tc, "function", None), "name", None)
                call_id = getattr(tc, "id", None)
                arg_str = getattr(getattr(tc, "function", None), "arguments", "{}")
                try:
                    args = json.loads(arg_str) if isinstance(arg_str, str) else (arg_str or {})
                except Exception:
                    args = {}
                if not fn:
                    continue
                canonical_fn = self.agent_executor.canonical_tool_name(fn)
                call_obj = SimpleNamespace(function=canonical_fn, arguments=args, provider_name=fn, call_id=call_id)
                parsed_calls.append(call_obj)

            blocked_calls: List[Tuple[Any, str]] = []
            current_mode = session_state.get_provider_metadata("current_mode")
            filtered_calls: List[Any] = []
            for call in parsed_calls:
                block_reason = self._todo_guard_preflight(session_state, call, current_mode)
                if block_reason:
                    blocked_calls.append((call, block_reason))
                else:
                    filtered_calls.append(call)

            parsed_calls = filtered_calls
            parsed_calls = self._expand_multi_file_patches(parsed_calls, session_state, markdown_logger)
            executed_results: List[tuple] = []
            failed_at_index = -1
            execution_error: Optional[Dict[str, Any]] = None

            if parsed_calls:
                executed_results, failed_at_index, execution_error, plan_metadata = self.agent_executor.execute_parsed_calls(
                    parsed_calls,
                    self._exec_raw,
                    transcript_callback=session_state.add_transcript_entry,
                )
            else:
                plan_metadata = {
                    "strategy": "no_calls",
                    "can_run_concurrent": False,
                    "max_workers": 0,
                    "group_counts": {},
                    "group_limits": {},
                    "total_calls": 0,
                    "executed_calls": 0,
                }
                if current_mode == "plan":
                    manager = session_state.get_todo_manager()
                    snapshot = manager.snapshot() if manager else None
                    todos = []
                    if isinstance(snapshot, dict):
                        todos = snapshot.get("todos") or []
                    if not todos:
                        warning = (
                            "<VALIDATION_ERROR>\n"
                            "Plan mode requires creating at least one todo via `todo.create` before continuing.\n"
                            "</VALIDATION_ERROR>"
                        )
                        session_state.increment_guardrail_counter("todo_plan_violation")
                        session_state.add_message({"role": "user", "content": warning}, to_provider=True)
                        try:
                            markdown_logger.log_user_message(warning)
                        except Exception:
                            pass
                        try:
                            session_state.add_transcript_entry({
                                "todo_guard": {
                                    "function": "todo.create",
                                    "reason": "no_todos_created_in_plan_mode",
                                }
                            })
                        except Exception:
                            pass
                        return False

            turn_index = session_state.get_provider_metadata("current_turn_index")
            turn_index_int = turn_index if isinstance(turn_index, int) else None
            test_success: Optional[float] = None
            session_state.add_transcript_entry({"tool_execution_plan": plan_metadata})
            try:
                self.provider_metrics.add_concurrency_sample(
                    turn=turn_index_int,
                    plan=plan_metadata,
                )
            except Exception:
                pass

            if execution_error:
                if execution_error.get("validation_failed"):
                    session_state.increment_guardrail_counter("validation_errors")
                    error_msg = error_handler.handle_validation_error(execution_error)
                elif execution_error.get("constraint_violation"):
                    error_msg = error_handler.handle_constraint_violation(execution_error["error"])
                else:
                    error_msg = f"<EXECUTION_ERROR>\n{execution_error['error']}\n</EXECUTION_ERROR>"

                session_state.add_message({"role": "user", "content": error_msg}, to_provider=True)
                markdown_logger.log_user_message(error_msg)
                if stream_responses:
                    print(f"[error] {execution_error.get('error', 'Unknown error')}")
                try:
                    if turn_index_int is not None:
                        if execution_error.get("validation_failed"):
                            session_state.add_reward_metric(turn_index_int, "SVS", 0.0)
                        session_state.add_reward_metric(turn_index_int, "CPS", 0.0)
                        self._record_lsp_reward_metrics(session_state, turn_index_int)
                except Exception:
                    pass
                return False

            results: List[Dict[str, Any]] = []
            recent_tools_summary: List[Dict[str, Any]] = []
            turn_index_hint = turn_index_int
            for parsed, tool_result in executed_results:
                call_id = getattr(parsed, "call_id", None)
                provider_name = getattr(parsed, "provider_name", parsed.function)
                tool_result_dict = tool_result if isinstance(tool_result, dict) else {}
                self._record_diff_metrics(
                    parsed,
                    tool_result_dict,
                    session_state=session_state,
                    turn_index=turn_index_hint,
                )
                metadata: Dict[str, Any] = {}
                if parsed.function and parsed.function.startswith("todo."):
                    metadata["is_todo"] = True
                command = ""
                if parsed.function == "run_shell":
                    try:
                        command = str((parsed.arguments or {}).get("command", ""))
                    except Exception:
                        command = ""
                    if command and self._is_test_command(command):
                        exit_code = tool_result_dict.get("exit")
                        if isinstance(exit_code, int):
                            success = 1.0 if exit_code == 0 else 0.0
                            test_success = success if test_success is None else min(test_success, success)
                success_flag = not self.agent_executor.is_tool_failure(parsed.function, tool_result_dict)
                if parsed.function in ("apply_unified_patch", "apply_search_replace", "create_file_from_block", "write"):
                    metadata["is_write"] = True
                if parsed.function == "run_shell":
                    metadata["is_run_shell"] = True
                    metadata["command"] = command
                    exit_code_val = tool_result_dict.get("exit")
                    if isinstance(exit_code_val, int):
                        metadata["exit_code"] = exit_code_val
                    if command and self._is_test_command(command):
                        metadata["is_test_command"] = True
                if isinstance(turn_index_hint, int):
                    try:
                        session_state.record_tool_event(
                            turn_index_hint,
                            parsed.function or "",
                            success=success_flag,
                            metadata=metadata,
                        )
                    except Exception:
                        pass
                recent_tools_summary.append({
                    "name": parsed.function,
                    "read_only": self._is_read_only_tool(parsed.function),
                    "completion_action": isinstance(tool_result, dict) and tool_result.get("action") == "complete",
                })
                results.append({
                    "fn": parsed.function,
                    "provider_fn": provider_name,
                    "out": tool_result,
                    "args": parsed.arguments,
                    "call_id": call_id,
                    "failed": False,
                })

            # Block premature completion if guard requirements are unmet.
            guard_blocked = False
            for idx in range(len(results) - 1, -1, -1):
                current = results[idx]
                if (
                    current.get("fn") == "mark_task_complete"
                    and isinstance(current.get("out"), dict)
                    and current["out"].get("action") == "complete"
                ):
                    guard_ok, guard_reason = self._completion_guard_check(session_state)
                    if guard_ok:
                        continue
                    guard_blocked = True
                    session_state.increment_guardrail_counter("completion_guard_blocks")
                    abort = self._emit_completion_guard_feedback(
                        session_state,
                        markdown_logger,
                        guard_reason or "Completion guard blocked mark_task_complete",
                        stream_responses,
                    )
                    # Remove tool usage accounting for this mark attempt.
                    summary = session_state.tool_usage_summary
                    summary["total_calls"] = max(0, int(summary.get("total_calls", 0)) - 1)
                    if isinstance(turn_index_hint, int):
                        turn_usage = session_state.turn_tool_usage.get(turn_index_hint)
                        if turn_usage and isinstance(turn_usage.get("tools"), list):
                            tools_list = turn_usage["tools"]
                            for tool_entry_idx in range(len(tools_list) - 1, -1, -1):
                                if tools_list[tool_entry_idx].get("name") == "mark_task_complete":
                                    tools_list.pop(tool_entry_idx)
                                    break
                    recent_tools_summary = [
                        entry for entry in recent_tools_summary
                        if entry.get("name") != "mark_task_complete"
                    ]
                    results.pop(idx)
                    if abort:
                        return True
                    break

            try:
                if turn_index_int is not None:
                    session_state.add_reward_metric(turn_index_int, "SVS", 1.0)
                    if plan_metadata.get("total_calls"):
                        executed_calls = plan_metadata.get("executed_calls", 0)
                        total_calls = plan_metadata.get("total_calls", 0)
                        cps_value = 1.0 if executed_calls == total_calls else 0.0
                        session_state.add_reward_metric(turn_index_int, "CPS", cps_value)
                    if executed_results:
                        acs_value = 1.0 if failed_at_index == -1 else 0.0
                        session_state.add_reward_metric(turn_index_int, "ACS", acs_value)
            except Exception:
                pass
            if turn_index_int is not None:
                self._record_lsp_reward_metrics(session_state, turn_index_int)
                self._record_test_reward_metric(session_state, turn_index_int, test_success)
            try:
                session_state.set_provider_metadata(
                    "recent_tool_activity",
                    {
                        "tools": recent_tools_summary,
                        "turn": session_state.get_provider_metadata("current_turn_index"),
                    },
                )
            except Exception:
                pass

            if blocked_calls:
                for blocked_call, reason in blocked_calls:
                    entry = self._emit_todo_guard_violation(
                        session_state,
                        markdown_logger,
                        reason,
                        blocked_call=blocked_call,
                    )
                    if entry:
                        results.append(entry)
            
            # Get turn strategy flow configuration
            flow_strategy = turn_cfg.get("flow", "assistant_continuation").lower()
            
            # Persist provider-native tool results before relay
            try:
                if self.logger_v2.run_dir and results:
                    persist_turn = len(session_state.transcript) + 1
                    persistable = []
                    for r in results:
                        persistable.append({
                            "fn": r["fn"],
                            "provider_fn": r.get("provider_fn", r["fn"]),
                            "call_id": r.get("call_id"),
                            "args": r.get("args"),
                            "out": r.get("out"),
                        })
                    self.provider_logger.save_tool_results(persist_turn, persistable)
                    short = "\n".join([f"- {r.get('provider_fn', r['fn'])} (id={r.get('call_id')})" for r in results])
                    self.logger_v2.append_text("conversation/conversation.md", self.md_writer.provider_tool_results(short, f"provider_native/tool_results/turn_{persist_turn}.json"))
            except Exception:
                pass

            # Relay results based on flow strategy
            if flow_strategy == "assistant_continuation":
                # ASSISTANT CONTINUATION PATTERN: Append tool results to assistant messages
                all_results_text = []
                for r in results:
                    formatted_output = self.message_formatter.format_tool_output(r["fn"], r["out"], r["args"])
                    call_id = r.get("call_id")
                    
                    # Always add tool result to main messages array for debugging
                    tool_result_entry = self.message_formatter.create_tool_result_entry(
                        r["fn"], r["out"], call_id or f"native_call_{r['fn']}", "openai"
                    )
                    session_state.add_message(tool_result_entry, to_provider=False)
                    all_results_text.append(formatted_output)
                
                # Create assistant continuation message with tool results
                continuation_content = f"\n\nTool execution results:\n" + "\n\n".join(all_results_text)
                assistant_continuation = {
                    "role": "assistant", 
                    "content": continuation_content
                }
                session_state.add_message(assistant_continuation)
                markdown_logger.log_assistant_message(continuation_content)
            else:
                # USER INTERLEAVED PATTERN: Traditional relay via user/tool messages
                for r in results:
                    formatted_output = self.message_formatter.format_tool_output(r["fn"], r["out"], r["args"])
                    call_id = r.get("call_id")
                    
                    # Always add tool result to main messages array for debugging
                    tool_result_entry = self.message_formatter.create_tool_result_entry(
                        r["fn"], r["out"], call_id or f"native_call_{r['fn']}", "openai"
                    )
                    session_state.add_message(tool_result_entry, to_provider=False)
                    
                    if relay_strategy == "tool_role" and call_id:
                        # Use provider adapter to create proper tool result message
                        provider_id = provider_router.parse_model_id(model)[0]
                        adapter = provider_adapter_manager.get_adapter(provider_id)
                        tool_result_msg = adapter.create_tool_result_message(call_id, r.get("provider_fn", r["fn"]), r["out"])
                        tool_messages_to_relay.append(tool_result_msg)
                    else:
                        session_state.add_message({"role": "user", "content": formatted_output}, to_provider=True)
                
                # If using tool-role relay, append all tool messages and continue loop
                if tool_messages_to_relay:
                    session_state.provider_messages.extend(tool_messages_to_relay)
                    # Do not add any synthetic user messages; let the model continue the assistant turn
            
            self._maybe_transition_plan_mode(session_state, markdown_logger)
            return False  # Continue the loop
            
        except Exception:
            # On relay error, fall back to legacy behavior (append as user)
            try:
                if tool_messages_to_relay:
                    fallback_blob = "\n\n".join([m.get("content", "") for m in tool_messages_to_relay])
                    session_state.add_message({"role": "user", "content": fallback_blob}, to_provider=True)
            except Exception:
                pass
            return False
    def _retry_diff_with_aider(self, patch_text: str) -> Optional[Dict[str, Any]]:
        """Attempt to recover unified diff failures by applying Aider SEARCH/REPLACE blocks."""
        try:
            import re

            matcher = re.compile(
                r"^diff --git a/(?P<path>.+?) b/(?P=path).*?(^@@.*?$.*?)(?=^diff --git|\Z)",
                re.MULTILINE | re.DOTALL,
            )
            file_blocks: Dict[str, Dict[str, List[str]]] = {}

            for match in matcher.finditer(patch_text):
                path = match.group("path")
                section = match.group(2) or ""
                block = file_blocks.setdefault(path, {"search": [], "replace": []})

                hunk_lines = section.splitlines()
                local_search: List[str] = []
                local_replace: List[str] = []
                for line in hunk_lines:
                    if not line:
                        continue
                    if line.startswith("diff --git") or line.startswith("new file mode") or line.startswith("index ") or line.startswith("---") or line.startswith("+++"):
                        continue
                    if line.startswith("@@"):
                        if local_search or local_replace:
                            block["search"].append("\n".join(local_search))
                            block["replace"].append("\n".join(local_replace))
                            local_search = []
                            local_replace = []
                        continue
                    if line.startswith("-"):
                        local_search.append(line[1:])
                    elif line.startswith("+"):
                        local_replace.append(line[1:])
                    else:
                        local_search.append(line[1:])
                        local_replace.append(line[1:])
                if local_search or local_replace:
                    block["search"].append("\n".join(local_search))
                    block["replace"].append("\n".join(local_replace))

            if len(file_blocks) != 1:
                return None

            file_name, data = next(iter(file_blocks.items()))
            if not data["search"]:
                return None

            search_text = "\n".join(filter(None, data["search"]))
            replace_text = "\n".join(filter(None, data["replace"]))
            payload = {
                "function": "apply_search_replace",
                "arguments": {
                    "file_name": file_name,
                    "search": search_text,
                    "replace": replace_text,
                },
            }
            result = self._exec_raw(payload)
            result = {"action": "apply_search_replace", **result}
            self._record_diff_metrics(
                SimpleNamespace(function="apply_search_replace", arguments=payload["arguments"], dialect="aider_retry"),
                result,
            )
            return result
        except Exception:
            return None
