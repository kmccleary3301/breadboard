from __future__ import annotations

import copy
import hashlib
import locale
import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .compilation.enhanced_config_validator import EnhancedConfigValidator
from .compilation.tool_yaml_loader import load_yaml_tools
from .execution.enhanced_executor import EnhancedToolExecutor
from .logging_v2.workspace_manifest import build_workspace_manifest
from .provider_metrics import ProviderMetricsCollector
from .provider_ir import IRDeltaEvent
from .streaming_policy import StreamingPolicy
from .guardrails import build_guardrail_manager
from .core.core import ToolDefinition, ToolParameter


# ----------------------------
# Guardrail / config helpers
# ----------------------------

def initialize_guardrail_config(
    conductor: Any,
    *,
    default_warn_turns: int,
    default_abort_turns: int,
    default_warn_message: str,
    default_abort_message: str,
) -> None:
    loop_cfg = (conductor.config.get("loop", {}) or {})
    guardrails_cfg = (loop_cfg.get("guardrails", {}) or {})
    zero_tool_cfg = guardrails_cfg.get("zero_tool_watchdog", {}) or {}

    def _coerce_int(value: Any, default: int) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    conductor.zero_tool_warn_turns = _coerce_int(zero_tool_cfg.get("warn_after_turns"), default_warn_turns)
    conductor.zero_tool_abort_turns = _coerce_int(zero_tool_cfg.get("abort_after_turns"), default_abort_turns)
    conductor.zero_tool_emit_event = bool(zero_tool_cfg.get("emit_event", True))

    enhanced_cfg = (conductor.config.get("enhanced_tools", {}) or {})
    diff_policy_cfg = (enhanced_cfg.get("diff_policy", {}) or {})
    patch_cfg = (diff_policy_cfg.get("patch_splitting", {}) or {})
    policy = str(patch_cfg.get("policy", "auto_split") or "").strip().lower()
    max_files = patch_cfg.get("max_files_per_patch")
    conductor.patch_splitting_policy = {
        "mode": policy if policy in {"auto_split", "reject_multi_file"} else "auto_split",
        "max_files": max_files if isinstance(max_files, int) and max_files > 0 else 1,
        "message": patch_cfg.get("validation_message"),
    }

    try:
        conductor.guardrail_manager = build_guardrail_manager(conductor.config)
    except Exception:
        conductor.guardrail_manager = None
    if not conductor.guardrail_manager:
        return

    zero_tool_handler = (
        conductor.guardrail_manager.get_handler("zero_tool_watchdog")
        or conductor.guardrail_manager.get_first_handler_of_type("zero_tool_watchdog")
    )
    if zero_tool_handler:
        conductor.zero_tool_warn_turns = int(getattr(zero_tool_handler, "warn_after", conductor.zero_tool_warn_turns))
        conductor.zero_tool_abort_turns = int(getattr(zero_tool_handler, "abort_after", conductor.zero_tool_abort_turns))
        conductor.zero_tool_emit_event = bool(getattr(zero_tool_handler, "emit_event", conductor.zero_tool_emit_event))
        conductor.zero_tool_warn_message = zero_tool_handler.warning_message() or default_warn_message
        conductor.zero_tool_abort_message = zero_tool_handler.abort_message() or default_abort_message

    conductor.workspace_guard_handler = (
        conductor.guardrail_manager.get_handler("workspace_context")
        or conductor.guardrail_manager.get_first_handler_of_type("workspace_context")
    )
    conductor.todo_rate_guard_handler = (
        conductor.guardrail_manager.get_handler("todo_rate_limit")
        or conductor.guardrail_manager.get_first_handler_of_type("todo_rate_limit")
    )
    shell_write_handler = (
        conductor.guardrail_manager.get_handler("shell_write_block")
        or conductor.guardrail_manager.get_first_handler_of_type("shell_write_block")
    )
    if shell_write_handler:
        conductor.shell_write_guard_handler = shell_write_handler
        shell_cfg = shell_write_handler.executor_config()
        guardrails_section = conductor.config.setdefault("loop", {}).setdefault("guardrails", {})
        existing_shell = guardrails_section.get("shell_write") or {}
        merged = dict(shell_cfg)
        merged.update(existing_shell)
        guardrails_section["shell_write"] = merged
    completion_guard_handler = (
        conductor.guardrail_manager.get_handler("completion_guard")
        or conductor.guardrail_manager.get_first_handler_of_type("completion_guard")
    )
    if completion_guard_handler:
        threshold = int(getattr(completion_guard_handler, "threshold", 0) or 0)
        if threshold:
            conductor.completion_guard_abort_threshold = threshold
        conductor.completion_guard_handler = completion_guard_handler


def prepare_concurrency_policy(config: Dict[str, Any]) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {"tool_to_group": {}, "barrier_functions": set()}
    policy = (config.get("concurrency", {}) or {})
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
        return defaults
    return {"tool_to_group": tool_to_group, "barrier_functions": barrier_functions}


# ----------------------------
# Streaming / routing helpers
# ----------------------------

def get_model_routing_preferences(config: Dict[str, Any], route_id: Optional[str]) -> Dict[str, Any]:
    defaults = {
        "disable_stream_on_probe_failure": True,
        "disable_native_tools_on_probe_failure": True,
        "fallback_models": [],
    }
    providers_cfg = (config.get("providers") or {})
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


def get_capability_probe_result(session_state: Any, route_id: Optional[str]) -> Optional[Dict[str, Any]]:
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


def log_routing_event(
    conductor: Any,
    session_state: Any,
    markdown_logger: Any,
    *,
    turn_index: Optional[Any],
    tag: str,
    message: str,
    payload: Dict[str, Any],
) -> None:
    try:
        session_state.add_transcript_entry({tag: payload})
    except Exception:
        pass
    try:
        markdown_logger.log_system_message(message)
    except Exception:
        pass
    try:
        if getattr(conductor.logger_v2, "run_dir", None):
            conductor.logger_v2.append_text(
                "conversation/conversation.md",
                conductor.md_writer.system(message),
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


def apply_streaming_policy_for_turn(
    conductor: Any,
    runtime: Any,
    model: str,
    tools_schema: Optional[List[Dict[str, Any]]],
    stream_requested: bool,
    session_state: Any,
    markdown_logger: Any,
    turn_index: int,
    route_id: Optional[str],
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    policy_manager = getattr(conductor, "streaming_policy", None)
    if policy_manager is None:
        metrics = getattr(conductor, "provider_metrics", None) or ProviderMetricsCollector()
        policy_manager = StreamingPolicy(metrics)
        conductor.streaming_policy = policy_manager

    routing_prefs = get_model_routing_preferences(conductor.config, route_id) or {}
    capability = get_capability_probe_result(session_state, route_id)
    def _emit_routing_event(**kwargs: Any) -> None:
        handler = getattr(conductor, "_log_routing_event", None)
        if callable(handler):
            try:
                handler(session_state, markdown_logger, **kwargs)
                return
            except Exception:
                pass
        log_routing_event(
            conductor,
            session_state,
            markdown_logger,
            **kwargs,
        )

    effective_stream_responses, stream_policy = policy_manager.apply(
        model=model,
        runtime_descriptor=getattr(runtime, "descriptor", None),
        tools_schema=tools_schema,
        stream_requested=stream_requested,
        session_state=session_state,
        markdown_logger=markdown_logger,
        turn_index=turn_index,
        route_id=route_id,
        routing_preferences=routing_prefs,
        capability=capability,
        logger_v2=conductor.logger_v2,
        md_writer=conductor.md_writer,
        log_routing_event=_emit_routing_event,
    )
    if stream_policy is not None:
        recorder = getattr(conductor, "_record_stream_policy_metadata", None)
        if callable(recorder):
            try:
                recorder(session_state, stream_policy)
            except Exception:
                pass
    return effective_stream_responses, stream_policy


def apply_capability_tool_overrides(
    conductor: Any,
    provider_tools_cfg: Dict[str, Any],
    session_state: Any,
    markdown_logger: Any,
    route_id: Optional[str],
) -> Dict[str, Any]:
    routing_prefs = get_model_routing_preferences(conductor.config, route_id)
    if not routing_prefs.get("disable_native_tools_on_probe_failure", True):
        return provider_tools_cfg
    capability = get_capability_probe_result(session_state, route_id)
    if not capability or not capability.get("attempted"):
        return provider_tools_cfg
    if capability.get("tool_stream_success") is not False:
        return provider_tools_cfg
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
    message = f"[tool-policy] Disabled native tool usage for route '{route_id}' after capability probe failure."
    conductor.provider_metrics.add_tool_override(
        route=route_id,
        reason="capability_probe_tool_failure",
    )
    log_routing_event(
        conductor,
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


# ----------------------------
# Tooling / modes / prompts
# ----------------------------

def initialize_yaml_tools(conductor: Any) -> None:
    try:
        tools_cfg = (conductor.config.get("tools", {}) or {})
        registry_cfg = (tools_cfg.get("registry") or {})
        include_list_raw = list(registry_cfg.get("include") or [])
        exclude_list_raw = list(registry_cfg.get("exclude") or [])
        registry_paths = list(registry_cfg.get("paths") or [])
        defs_dir = tools_cfg.get("defs_dir") or "implementations/tools/defs"
        overlays = (tools_cfg.get("overlays") or [])
        aliases = (tools_cfg.get("aliases") or {})
        if registry_paths:
            tools_by_name: Dict[str, Any] = {}
            ordered_names: List[str] = []
            merged_manipulations: Dict[str, List[str]] = {}
            for raw_path in registry_paths:
                path_str = str(raw_path)
                loaded = load_yaml_tools(path_str, overlays=overlays, aliases=aliases)
                for tool in loaded.tools:
                    name = getattr(tool, "name", None)
                    if not name:
                        continue
                    if name not in tools_by_name:
                        ordered_names.append(name)
                    tools_by_name[name] = tool
                try:
                    merged_manipulations.update(loaded.manipulations_by_id)
                except Exception:
                    pass
            conductor.yaml_tools = [tools_by_name[name] for name in ordered_names if name in tools_by_name]
            conductor.yaml_tool_manipulations = merged_manipulations
        else:
            loaded = load_yaml_tools(defs_dir, overlays=overlays, aliases=aliases)
            conductor.yaml_tools = loaded.tools
            conductor.yaml_tool_manipulations = loaded.manipulations_by_id

        include_list = []
        include_has_wildcard = False
        for entry in include_list_raw:
            if entry is None:
                continue
            name = str(entry).strip()
            if not name:
                continue
            if name in {"*", "*.*", "all"}:
                include_has_wildcard = True
            include_list.append(name)
        exclude_names = {str(item).strip() for item in exclude_list_raw if item is not None and str(item).strip()}
        legacy_enabled = (tools_cfg.get("enabled", {}) or {})

        def _is_included(name: str) -> bool:
            if not name:
                return False
            if name in exclude_names:
                return False
            if include_list:
                if include_has_wildcard:
                    return True
                return name in include_list
            if legacy_enabled:
                return bool(legacy_enabled.get(name, False))
            return True

        filtered_tools = []
        for tool in conductor.yaml_tools or []:
            tool_name = getattr(tool, "name", None)
            if tool_name and _is_included(str(tool_name)):
                filtered_tools.append(tool)
        # Gate Task tool unless multi-agent or task_tool config explicitly enabled.
        allow_task_tool = False
        try:
            multi_cfg = (conductor.config.get("multi_agent") or {}) if isinstance(getattr(conductor, "config", None), dict) else {}
            allow_task_tool = bool(multi_cfg.get("enabled"))
            if not allow_task_tool:
                allow_task_tool = bool((conductor.config.get("task_tool") or {})) if isinstance(getattr(conductor, "config", None), dict) else False
        except Exception:
            allow_task_tool = False

        if not allow_task_tool:
            filtered_tools = [
                tool for tool in filtered_tools
                if str(getattr(tool, "name", "")).strip().lower() != "task"
            ]
        # Preserve include-list ordering when provided (OpenCode/OmO parity).
        if include_list and not include_has_wildcard:
            include_set = {name for name in include_list if name}
            tools_by_name = {str(getattr(t, "name", "")): t for t in filtered_tools}
            ordered = [tools_by_name[name] for name in include_list if name in tools_by_name]
            remaining = [t for t in filtered_tools if str(getattr(t, "name", "")) not in include_set]
            filtered_tools = ordered + remaining
        conductor.yaml_tools = filtered_tools
    except Exception:
        conductor.yaml_tools = []
        conductor.yaml_tool_manipulations = {}

    # Dynamic tool descriptions (OpenCode parity surfaces).
    try:
        # Update bash tool to reflect the active workspace path.
        try:
            workspace = str(Path(getattr(conductor, "workspace", "")).resolve())
            for tool in conductor.yaml_tools or []:
                if getattr(tool, "name", None) != "bash":
                    continue
                desc = getattr(tool, "description", "")
                if not isinstance(desc, str) or not desc:
                    continue
                replacement = f"All commands run in {workspace} by default."
                desc = re.sub(r"All commands run in\s+.*?\s+by default\.", replacement, desc, flags=re.S)
                tool.description = desc
        except Exception:
            pass

        task_cfg = (conductor.config.get("task_tool") or {}) if isinstance(getattr(conductor, "config", None), dict) else {}
        subagents_cfg = task_cfg.get("subagents") if isinstance(task_cfg, dict) else None
        agents_lines: List[str] = []
        if isinstance(subagents_cfg, dict) and subagents_cfg:
            for name, sub_cfg in subagents_cfg.items():
                if not name:
                    continue
                desc = None
                if isinstance(sub_cfg, dict):
                    raw = sub_cfg.get("description")
                    if isinstance(raw, str) and raw.strip():
                        desc = raw.strip()
                if not desc:
                    desc = "This subagent should only be called manually by the user."
                agents_lines.append(f"- {name}: {desc}")
        else:
            # Multi-agent TeamConfig-based list (Phase 8)
            try:
                multi_cfg = (conductor.config.get("multi_agent") or {}) if isinstance(getattr(conductor, "config", None), dict) else {}
                if multi_cfg.get("enabled"):
                    team_payload = multi_cfg.get("team_config")
                    if isinstance(team_payload, str):
                        try:
                            import yaml  # type: ignore
                            team_payload = yaml.safe_load(Path(team_payload).read_text(encoding="utf-8"))
                        except Exception:
                            team_payload = None
                    if isinstance(team_payload, dict):
                        from .orchestration import TeamConfig

                        team_config = TeamConfig.from_dict(team_payload)
                        for agent_id, agent_ref in team_config.agents.items():
                            if agent_ref.entrypoint or agent_ref.role == "main" or agent_id == "main":
                                continue
                            desc = None
                            if isinstance(agent_ref.capabilities, dict):
                                raw = agent_ref.capabilities.get("description")
                                if isinstance(raw, str) and raw.strip():
                                    desc = raw.strip()
                            if not desc:
                                desc = "This subagent should only be called manually by the user."
                            agents_lines.append(f"- {agent_id}: {desc}")
            except Exception:
                pass

        if agents_lines:
            agents_blob = "\n".join(agents_lines)

            template_text: Optional[str] = None
            template_path = None
            if isinstance(task_cfg, dict):
                template_path = task_cfg.get("description_template_path")
            if template_path:
                path = Path(str(template_path))
                if not path.is_absolute():
                    path = (Path(__file__).resolve().parents[1] / path).resolve()
                if path.exists():
                    template_text = path.read_text(encoding="utf-8", errors="replace")
            if not template_text:
                # Best-effort default: mirror OpenCode's upstream copy if present.
                upstream = Path(__file__).resolve().parents[1] / "industry_coder_refs/opencode/packages/opencode/src/tool/task.txt"
                if upstream.exists():
                    template_text = upstream.read_text(encoding="utf-8", errors="replace")
            if not template_text:
                template_text = (
                    "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
                    "Available agent types and the tools they have access to:\n"
                    "{agents}\n"
                )

            rendered = template_text.replace("{agents}", agents_blob)
            for tool in conductor.yaml_tools or []:
                if getattr(tool, "name", None) == "task":
                    try:
                        tool.description = str(rendered)
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    try:
        conductor.config.setdefault("yaml_tool_manipulations", conductor.yaml_tool_manipulations)
    except Exception:
        pass


def initialize_config_validator(conductor: Any) -> None:
    validation_cfg = (conductor.config.get("enhanced_tools", {}).get("validation") or {})
    validation_enabled = bool(validation_cfg.get("enabled", True))
    if not validation_enabled:
        conductor.config_validator = None
        return
    enhanced_tools_config = conductor.config.get("tools", {}).get("registry")
    if not enhanced_tools_config or not os.path.exists("implementations/tools/enhanced_tools.yaml"):
        conductor.config_validator = None
        return
    try:
        conductor.config_validator = EnhancedConfigValidator("implementations/tools/enhanced_tools.yaml")
    except Exception:
        conductor.config_validator = None
    conductor.sequential_executor = None


def initialize_enhanced_executor(conductor: Any) -> None:
    enhanced_config = conductor.config.get("enhanced_tools", {})
    if enhanced_config.get("lsp_integration", {}).get("enabled", False):
        try:
            from breadboard.sandbox_lsp_integration import LSPEnhancedSandbox
            conductor.sandbox = LSPEnhancedSandbox.remote(conductor.sandbox, conductor.workspace)
        except ImportError:
            pass
    if enhanced_config.get("enabled", False) or enhanced_config.get("lsp_integration", {}).get("enabled", False):
        sandbox_for_executor = conductor.sandbox
        if enhanced_config.get("lsp_integration", {}).get("enabled", False):
            try:
                from breadboard.sandbox_lsp_integration import LSPEnhancedSandbox
                sandbox_for_executor = LSPEnhancedSandbox.remote(conductor.sandbox, conductor.workspace)
            except ImportError:
                pass
        conductor.enhanced_executor = EnhancedToolExecutor(
            sandbox=sandbox_for_executor,
            config=conductor.config,
        )
    else:
        conductor.enhanced_executor = None


def tool_defs_from_yaml(conductor: Any) -> Optional[List[ToolDefinition]]:
    try:
        if not getattr(conductor, "yaml_tools", None):
            return None

        tools_cfg = (conductor.config.get("tools", {}) or {})
        registry_cfg = (tools_cfg.get("registry", {}) or {})
        include_list_raw = list(registry_cfg.get("include") or [])
        include_list = []
        include_has_wildcard = False
        for entry in include_list_raw:
            if entry is None:
                continue
            name = str(entry).strip()
            if not name:
                continue
            if name in {"*", "*.*", "all"}:
                include_has_wildcard = True
            include_list.append(name)
        legacy_enabled = (tools_cfg.get("enabled", {}) or {})

        def _is_included(name: str) -> bool:
            if include_list:
                if include_has_wildcard:
                    return True
                return name in include_list
            if legacy_enabled:
                return bool(legacy_enabled.get(name, False))
            return True

        converted: List[ToolDefinition] = []
        for t in conductor.yaml_tools:
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


def completion_tool_config_enabled(config: Dict[str, Any]) -> bool:
    tools_cfg = (config.get("tools", {}) or {})
    direct_flag = tools_cfg.get("mark_task_complete")
    if isinstance(direct_flag, bool):
        if not direct_flag:
            return False
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


def ensure_completion_tool(tool_defs: List[ToolDefinition]) -> List[ToolDefinition]:
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


def is_read_only_tool(tool_name: str) -> bool:
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


def normalize_assistant_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.strip().split()).lower()


def get_prompt_cache_control(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    provider_cfg = (config.get("provider_tools") or {})
    anthropic_cfg = (provider_cfg.get("anthropic") or {})
    prompt_cache_cfg = (anthropic_cfg.get("prompt_cache") or {}) if isinstance(anthropic_cfg, dict) else {}
    cache_control = prompt_cache_cfg.get("cache_control")
    if isinstance(cache_control, dict):
        control = copy.deepcopy(cache_control)
        control.setdefault("type", "ephemeral")
        return control
    if cache_control:
        return {"type": str(cache_control)}
    return {"type": "ephemeral"}


def apply_cache_control_to_initial_user_prompt(messages: List[Dict[str, Any]], cache_control: Dict[str, Any]) -> None:
    for message in messages:
        if message.get("role") == "user":
            inject_cache_control_into_message(message, cache_control)
            return


def inject_cache_control_into_message(message: Dict[str, Any], cache_control: Dict[str, Any]) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = [{
            "type": "text",
            "text": content,
            "cache_control": copy.deepcopy(cache_control),
        }]
        return
    if isinstance(content, list):
        mutated = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                block.setdefault("cache_control", copy.deepcopy(cache_control))
                mutated = True
        if not mutated:
            message["content"].append({
                "type": "text",
                "text": "",
                "cache_control": copy.deepcopy(cache_control),
            })


def append_text_block(message: Dict[str, Any], text: str) -> None:
    content = message.get("content")
    if isinstance(content, list):
        message["content"].append({"type": "text", "text": text})
    else:
        message["content"] = (content or "") + text


def apply_cache_control_to_tool_messages(messages: List[Dict[str, Any]], cache_control: Dict[str, Any]) -> None:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"tool_use", "tool_result"}:
                block.setdefault("cache_control", copy.deepcopy(cache_control))


# ----------------------------
# Reward / logging helpers
# ----------------------------

def record_lsp_reward_metrics(conductor: Any, session_state: Any, turn_index: int) -> None:
    try:
        enhanced_executor = getattr(conductor.agent_executor, "enhanced_executor", None)
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


def record_test_reward_metric(session_state: Any, turn_index: int, success_value: Optional[float]) -> None:
    if success_value is None:
        return
    try:
        session_state.add_reward_metric(turn_index, "TPF_DELTA", float(success_value))
    except Exception:
        pass


def register_prompt_hash(conductor: Any, prompt_type: str, content: Optional[str], turn_index: Optional[int] = None) -> None:
    if not content:
        return
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if prompt_type == "system":
        conductor._prompt_hashes["system"] = digest
        return
    if prompt_type == "per_turn" and isinstance(turn_index, int):
        per_turn = conductor._prompt_hashes.setdefault("per_turn", {})
        per_turn[turn_index] = digest


def build_prompt_summary(conductor: Any) -> Optional[Dict[str, Any]]:
    prompt_section: Dict[str, Any] = {}
    system_hash = conductor._prompt_hashes.get("system")
    per_turn_hashes = conductor._prompt_hashes.get("per_turn") or {}
    compile_key = getattr(conductor, "_prompt_compile_key", None)
    compiler_version = getattr(conductor, "_prompt_compiler_version", None)
    blocks_hash = getattr(conductor, "_prompt_blocks_hash", None)
    if system_hash:
        prompt_section["system_hash"] = system_hash
    if per_turn_hashes:
        prompt_section["per_turn_hashes"] = [
            {"turn": turn, "hash": digest}
            for turn, digest in sorted(per_turn_hashes.items(), key=lambda item: item[0])
        ]
    if compile_key:
        prompt_section["compile_key"] = compile_key
    if compiler_version:
        prompt_section["compiler_version"] = compiler_version
    if blocks_hash:
        prompt_section["blocks_hash"] = blocks_hash
    return prompt_section or None


def write_workspace_manifest(conductor: Any) -> None:
    if not getattr(conductor.logger_v2, "run_dir", None):
        return
    try:
        manifest = build_workspace_manifest(conductor.workspace)
    except Exception:
        return
    try:
        conductor.logger_v2.write_json("meta/workspace.manifest.json", manifest)
    except Exception:
        pass


def write_env_fingerprint(conductor: Any) -> None:
    if not getattr(conductor.logger_v2, "run_dir", None):
        return
    fingerprint: Dict[str, Any] = {}
    try:
        fingerprint["platform"] = platform.system()
        fingerprint["platform_release"] = platform.release()
        fingerprint["platform_version"] = platform.version()
        fingerprint["machine"] = platform.machine()
        fingerprint["python_version"] = platform.python_version()
        fingerprint["processor"] = platform.processor()
    except Exception:
        pass
    try:
        fingerprint["timezone"] = list(time.tzname)
    except Exception:
        pass
    try:
        lang = locale.getdefaultlocale()
        if lang:
            fingerprint["locale"] = [entry for entry in lang if entry]
    except Exception:
        pass
    try:
        fingerprint["env"] = {
            "TZ": os.environ.get("TZ"),
            "LANG": os.environ.get("LANG"),
            "LC_ALL": os.environ.get("LC_ALL"),
        }
    except Exception:
        pass
    try:
        fingerprint["cpu_count"] = os.cpu_count()
    except Exception:
        pass
    try:
        usage = shutil.disk_usage(conductor.workspace)
        fingerprint["disk_usage"] = {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }
    except Exception:
        pass
    try:
        conductor.logger_v2.write_json("meta/env_fingerprint.json", fingerprint)
    except Exception:
        pass


def get_default_tool_definitions() -> List[ToolDefinition]:
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
            type_id="diff",
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


def maybe_run_plan_bootstrap(conductor: Any, session_state: Any, markdown_logger: Optional[Any] = None) -> None:
    try:
        exec_raw = conductor._build_exec_func(session_state)
        execute_calls = getattr(conductor, "_execute_agent_calls", None)
        if not callable(execute_calls):
            return

        def _exec_plan_calls(calls, *, transcript_callback=None, policy_bypass: bool = False):
            return execute_calls(
                calls,
                exec_raw,
                session_state,
                transcript_callback=transcript_callback,
                policy_bypass=policy_bypass,
            )

        conductor.guardrail_orchestrator.maybe_run_plan_bootstrap(
            session_state,
            markdown_logger,
            _exec_plan_calls,
        )
    except Exception:
        pass


def should_require_build_guard(conductor: Any, user_prompt: str) -> bool:
    guard_cfg = (conductor.config.get("completion", {}) or {}).get("build_guard", {})
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


def capture_turn_diagnostics(
    conductor: Any,
    session_state: Any,
    provider_result: Optional[Any],
    allowed_tools: Optional[List[str]],
    todo_seed_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    todo_seed_set = {n.lower() for n in todo_seed_names} if todo_seed_names else None
    turn_index = session_state.get_provider_metadata("current_turn_index")
    diag: Dict[str, Any] = {
        "turn": turn_index,
        "provider_tools": list(dict.fromkeys(allowed_tools or [])),
        "tool_calls": [],
        "has_todo_seed": False,
        "route_id": getattr(conductor, "_current_route_id", None),
        "provider_model": getattr(provider_result, "model", None) if provider_result else None,
    }
    latency = getattr(conductor, "_last_runtime_latency", None)
    if latency is not None:
        try:
            diag["latency_seconds"] = float(latency)
        except Exception:
            pass
    if provider_result and isinstance(getattr(provider_result, "usage", None), dict):
        usage_raw = provider_result.usage or {}
        diag["usage"] = {
            "prompt_tokens": usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens"),
            "completion_tokens": usage_raw.get("completion_tokens") or usage_raw.get("output_tokens"),
            "total_tokens": usage_raw.get("total_tokens"),
        }
    seed_detected = False
    tool_calls: List[Dict[str, Any]] = []
    if provider_result and provider_result.messages:
        for message in provider_result.messages:
            for call in getattr(message, "tool_calls", []) or []:
                call_name = (getattr(call, "name", None) or "").strip()
                entry = {
                    "name": call_name,
                    "id": getattr(call, "id", None),
                }
                if call_name in {"todo.write_board", "todo.create"}:
                    seed_detected = True
                elif todo_seed_set and call_name.lower() in todo_seed_set:
                    seed_detected = True
                try:
                    entry["type"] = getattr(call, "type", None)
                except Exception:
                    pass
                try:
                    entry["arguments"] = getattr(call, "arguments", None)
                except Exception:
                    pass
                tool_calls.append(entry)
    if tool_calls:
        diag["tool_calls"] = tool_calls
    if seed_detected:
        diag["has_todo_seed"] = True
    try:
        session_state.add_turn_diagnostic(diag)
    except Exception:
        pass
    return diag
